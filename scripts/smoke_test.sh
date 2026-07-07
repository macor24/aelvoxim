#!/usr/bin/env bash
# Aelvoxim 核心 API 冒烟测试
# 改完代码后跑这个，确认后端没断
# 使用方法: bash scripts/smoke_test.sh
set -euo pipefail

BASE="${1:-http://127.0.0.1:9701}"
PASS=0
FAIL=0
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "========================================"
echo " Aelvoxim Smoke Test — ${BASE}"
echo "========================================"

# ── 1. 健康检查 ──
CODE=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/v1/health" 2>/dev/null || echo "000")
if [ "$CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} GET /v1/health → ${CODE}"
  PASS=$((PASS+1))
else
  echo -e "  ${RED}✗${NC} GET /v1/health → ${CODE}  （后端没启动?）"
  FAIL=$((FAIL+1))
fi

# ── 2. 登录（gmxchz@126.com / admin123）──
LOGIN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"gmxchz@126.com","password":"admin123"}' \
  "${BASE}/v1/auth/login" 2>/dev/null || echo "")
KEY=$(echo "$LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('api_key',''))" 2>/dev/null || echo "")
if [ -n "$KEY" ]; then
  echo -e "  ${GREEN}✓${NC} POST /v1/auth/login → 登录成功, API Key 存在"
  PASS=$((PASS+1))
else
  echo -e "  ${RED}✗${NC} POST /v1/auth/login → 登录失败"
  FAIL=$((FAIL+1))
fi

# ── 3. 用户列表（admin）──
if [ -n "$KEY" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    "${BASE}/v1/admin/users" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} GET /v1/admin/users → ${CODE}"
    PASS=$((PASS+1))
  else
    echo -e "  ${RED}✗${NC} GET /v1/admin/users → ${CODE}"
    FAIL=$((FAIL+1))
  fi
fi

# ── 4. 统计数据 ──
if [ -n "$KEY" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    "${BASE}/v1/admin/stats" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} GET /v1/admin/stats → ${CODE}"
    PASS=$((PASS+1))
  else
    echo -e "  ${RED}✗${NC} GET /v1/admin/stats → ${CODE}"
    FAIL=$((FAIL+1))
  fi
fi

# ── 5. 配置列表 ──
if [ -n "$KEY" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    "${BASE}/v1/config" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} GET /v1/config → ${CODE}"
    PASS=$((PASS+1))
  else
    echo -e "  ${RED}✗${NC} GET /v1/config → ${CODE}"
    FAIL=$((FAIL+1))
  fi
fi

# ── 6. Panel 页面（无 auth）──
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  "${BASE}/v1/admin/panel" 2>/dev/null || echo "000")
if [ "$CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} GET /v1/admin/panel → ${CODE}"
  PASS=$((PASS+1))
else
  echo -e "  ${RED}✗${NC} GET /v1/admin/panel → ${CODE}"
  FAIL=$((FAIL+1))
fi

# ── 7. 当前用户信息 ──
if [ -n "$KEY" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $KEY" \
    "${BASE}/v1/user/me" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} GET /v1/user/me → ${CODE}"
    PASS=$((PASS+1))
  else
    echo -e "  ${RED}✗${NC} GET /v1/user/me → ${CODE}"
    FAIL=$((FAIL+1))
  fi
fi

# ── 结果 ──
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
  echo -e " ${GREEN}全部通过 (${PASS}/${PASS})${NC} — 可以关掉去浏览器看了"
else
  echo -e " ${RED}通过 ${PASS}, 失败 ${FAIL}${NC} — 先修上面的 ✗"
fi
echo "========================================"
