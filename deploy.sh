#!/usr/bin/env bash
# ── Aelvoxim 一键部署脚本（Ubuntu 24.04 / 阿里云） ──
# 用法: bash deploy.sh
# 前置条件:
#   1. 设置 AELVOXIM_DEPLOY_HOST、AELVOXIM_DEPLOY_USER（默认 root）
#   2. SSH 密钥已配置到服务器
set -euo pipefail

DEPLOY_USER="${AELVOXIM_DEPLOY_USER:-root}"
DEPLOY_HOST="${AELVOXIM_DEPLOY_HOST:?必须设置 AELVOXIM_DEPLOY_HOST}"
DEPLOY_DIR="/opt/aelvoxim"
DATA_DIR="/var/aelvoxim/data"

echo "╔════════════════════════════════════════════╗"
echo "║     Aelvoxim 一键部署                      ║"
echo "║     Target: $DEPLOY_USER@$DEPLOY_HOST       ║"
echo "╚════════════════════════════════════════════╝"

# ── Step 1: SSH 连通性检查 ──
echo ""
echo "=== Step 1: SSH 连通性 ==="
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$DEPLOY_USER@$DEPLOY_HOST" "echo OK" || {
    echo "❌ SSH 连接失败，请检查:"
    echo "   ssh $DEPLOY_USER@$DEPLOY_HOST"
    exit 1
}
echo "✅ SSH OK"

# ── Step 2: 系统准备 ──
echo ""
echo "=== Step 2: 系统准备 ==="
ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s << 'SYSEOF'
set -e
export DEBIAN_FRONTEND=noninteractive

# Python 3.12（Ubuntu 24.04 自带）
echo "  Python: $(python3 --version)"

# 安装系统依赖
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv postgresql-16 nginx certbot curl

# 创建目录
mkdir -p /opt/aelvoxim /var/aelvoxim/data /var/aelvoxim/logs

# PG 初始化
if ! pg_isready -q 2>/dev/null; then
    systemctl start postgresql
    systemctl enable postgresql
fi

# 创建 PG 用户和数据库（幂等）
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='aelvoxim'\" | grep -q 1 || createuser aelvoxim" 2>/dev/null
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='aelvoxim'\" | grep -q 1 || createdb -O aelvoxim aelvoxim"
su - postgres -c "psql -c \"ALTER USER aelvoxim WITH PASSWORD 'aelvoxim_pg_pass'\""

# 允许密码认证
PG_HBA=$(find /etc/postgresql -name pg_hba.conf 2>/dev/null | head -1)
if [ -n "$PG_HBA" ]; then
    sed -i 's/local   all             all                                     peer/local   all             all                                     md5/' "$PG_HBA"
    echo "host    all             all             127.0.0.1/32            md5" >> "$PG_HBA"
    systemctl restart postgresql
fi
echo "  PostgreSQL: $(psql -h 127.0.0.1 -U aelvoxim -d aelvoxim -c 'SELECT version()' 2>/dev/null | head -3)"
SYSEOF
echo "✅ 系统准备完成"

# ── Step 3: 上传源码 ──
echo ""
echo "=== Step 3: 上传源码 ==="
cd "$(dirname "$0")"
# 打包源码（排除无用文件）
tar czf /tmp/aelvoxim-src.tar.gz \
    --exclude='__pycache__' --exclude='*.egg-info' \
    --exclude='build' --exclude='dist' --exclude='.git' \
    --exclude='node_modules' --exclude='.pytest_cache' \
    --exclude='aelvoxim-gateway' --exclude='.hermes' \
    -C /mnt/c/Aelvoxim .

scp /tmp/aelvoxim-src.tar.gz "$DEPLOY_USER@$DEPLOY_HOST:/opt/aelvoxim/"
ssh "$DEPLOY_USER@$DEPLOY_HOST" "cd /opt/aelvoxim && tar xzf aelvoxim-src.tar.gz && rm -f aelvoxim-src.tar.gz"
echo "✅ 源码上传完成（$(du -sh /mnt/c/Aelvoxim/src | cut -f1)）"

# ── Step 4: Python 依赖 ──
echo ""
echo "=== Step 4: Python 依赖 ==="
ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s << 'DEPEOF'
set -e
cd /opt/aelvoxim
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
# 核心依赖
pip install bcrypt fastapi uvicorn[standard] psycopg2-binary pydantic Pillow numpy -q
# 安装项目（editable）
pip install -e . -q 2>/dev/null || pip install . -q
echo "  Done: $(pip list 2>/dev/null | grep -c ^[a-z]) packages"
DEPEOF
echo "✅ Python 依赖完成"

# ── Step 5: systemd 服务 ──
echo ""
echo "=== Step 5: systemd 服务 ==="
ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s << 'SYSEOF'
set -e

# API 服务
cat > /etc/systemd/system/aelvoxim-api.service << 'UNIT'
[Unit]
Description=Aelvoxim API Server
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/aelvoxim
Environment=PYTHONPATH=/opt/aelvoxim/src
Environment=AELVOXIM_EDITION=enterprise
Environment=AELVOXIM_HOST=0.0.0.0
Environment=AELVOXIM_DATABASE_URL=host=127.0.0.1 port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_778af6539f11998d
ExecStart=/opt/aelvoxim/venv/bin/python3 -B /opt/aelvoxim/src/run_server.py 9701
Restart=always
RestartSec=5
StandardOutput=append:/var/aelvoxim/logs/api_stdout.log
StandardError=append:/var/aelvoxim/logs/api_stderr.log

[Install]
WantedBy=multi-user.target
UNIT

# ChatAEL 前端服务
cat > /etc/systemd/system/aelvoxim-chatael.service << 'UNIT'
[Unit]
Description=Aelvoxim ChatAEL Frontend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/aelvoxim
ExecStart=/opt/aelvoxim/venv/bin/python3 -B /opt/aelvoxim/serve_chatael.py
Restart=always
RestartSec=5
StandardOutput=append:/var/aelvoxim/logs/chatael_stdout.log
StandardError=append:/var/aelvoxim/logs/chatael_stderr.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable aelvoxim-api aelvoxim-chatael
systemctl start aelvoxim-api aelvoxim-chatael
sleep 4
echo "  API: $(systemctl is-active aelvoxim-api)"
echo "  ChatAEL: $(systemctl is-active aelvoxim-chatael)"
SYSEOF
echo "✅ systemd 服务完成"

# ── Step 6: 数据迁移（知识库 + 用户） ──
echo ""
echo "=== Step 6: 数据迁移 ==="
# 本地导出 PG 数据
PGPASSWORD=aelvoxim_pg_pass pg_dump -h localhost -U aelvoxim -d aelvoxim \
    --data-only --inserts \
    -t knowledge_entries -t users -t learning_directions \
    -f /tmp/aelvoxim-data.sql 2>/dev/null || {
    echo "  ⚠️ 本地 PG 导出失败（可能无数据），跳过"
    touch /tmp/aelvoxim-data.sql
}

if [ -s /tmp/aelvoxim-data.sql ]; then
    scp /tmp/aelvoxim-data.sql "$DEPLOY_USER@$DEPLOY_HOST:/tmp/"
    ssh "$DEPLOY_USER@$DEPLOY_HOST" "PGPASSWORD=aelvoxim_pg_pass psql -h 127.0.0.1 -U aelvoxim -d aelvoxim -f /tmp/aelvoxim-data.sql 2>&1 | tail -3"
    echo "  ✅ PG 数据迁移完成"
else
    echo "  ⏭️ 无数据需要迁移"
fi

# 迁移 JSON 数据文件（~/.aelvoxim）
scp -r /home/gmxchz/.aelvoxim "$DEPLOY_USER@$DEPLOY_HOST:/var/aelvoxim/data/"
ssh "$DEPLOY_USER@$DEPLOY_HOST" "ln -sf /var/aelvoxim/data/.aelvoxim /root/.aelvoxim"
echo "  ✅ JSON 数据迁移完成"

# ── Step 7: Nginx 反代（可选） ──
echo ""
echo "=== Step 7: Nginx 配置 ==="
ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s << 'NGXEOF'
set -e
DOMAIN="${AELVOXIM_DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
    # 用 IP 地址
    IP=$(curl -s http://checkip.amazonaws.com 2>/dev/null || curl -s https://api.ipify.org 2>/dev/null || echo "未知")
    cat > /etc/nginx/sites-available/aelvoxim << 'NGINX'
server {
    listen 80;
    server_name _;

    # API
    location / {
        proxy_pass http://127.0.0.1:9701;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # ChatAEL
    location /chatael {
        proxy_pass http://127.0.0.1:9702;
        proxy_set_header Host $host;
    }

    # SSE streaming needs buffering off
    location /v1/llm/chat/stream {
        proxy_pass http://127.0.0.1:9701;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }
}
NGINX
    echo "  Server IP: $IP"
else
    # 有域名，配 SSL
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" 2>/dev/null || true
    echo "  Domain: $DOMAIN"
fi

ln -sf /etc/nginx/sites-available/aelvoxim /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
NGXEOF
echo "✅ Nginx 配置完成"

# ── 完成 ──
echo ""
echo "╔════════════════════════════════════════════╗"
echo "║  ✅ Aelvoxim 部署完成！                    ║"
echo "╠════════════════════════════════════════════╣"
IP=$(ssh "$DEPLOY_USER@$DEPLOY_HOST" "curl -s http://checkip.amazonaws.com 2>/dev/null || hostname -I | awk '{print \$1}'")
echo "║  API:     http://$IP:9701                    ║"
echo "║  面板:    http://$IP/v1/admin/panel         ║"
echo "║  文档:    http://$IP:9701/docs              ║"
echo "║  ChatAEL: http://$IP:9702                   ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "📋 后续操作:"
echo "  1. 设置环境变量: ssh $DEPLOY_USER@$DEPLOY_HOST"
echo "     'export DEEPSEEK_API_KEY=sk-xxx' 或写入 /etc/systemd/system/aelvoxim-api.service.d/env.conf"
echo "  2. 验证: curl http://127.0.0.1:9701/v1/health"
echo "  3. 注册管理员: curl -X POST http://127.0.0.1:9701/v1/auth/register -d 'email=admin@xxx' -d 'password=xxx'"
