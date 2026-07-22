#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Aelvoxim Desktop Gateway — Universal desktop software adapter.

Usage:
    python main.py                        # Normal mode (HTTP + WebSocket)
    python main.py --setup                # First-time setup (browser login)
    python main.py --service              # Windows service mode (silent)
    python main.py --port 9705 --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# ── Token 存储路径 ──
_TOKEN_FILE = os.path.join(_THIS_DIR, "gateway_token.json")
_SETUP_PORT = 19705


def main():
    parser = argparse.ArgumentParser(
        description="Aelvoxim Desktop Gateway — universal desktop software adapter"
    )
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP server port (default: from config, 9705)")
    parser.add_argument("--host", type=str, default="",
                        help="Bind address (default: from config, 127.0.0.1)")
    parser.add_argument("--config", type=str, default="",
                        help="Path to config.yaml (default: ./config.yaml)")
    parser.add_argument("--setup", action="store_true",
                        help="Open first-time setup page in browser")
    parser.add_argument("--service", action="store_true",
                        help="Windows service mode (no console)")
    args = parser.parse_args()

    # ── 首次配置 ──
    if args.setup:
        _open_setup_page()
        _run_setup_server()
        return

    # ── 加载 token ──
    token = _load_token()
    if not token:
        print("[Warning] No login token. Opening setup page...")
        # 在浏览器中打开配置页面
        _open_setup_page()
        # 启动 HTTP 服务等待用户配置
        _run_setup_server()
        # 配置完成后重新加载 token
        token = _load_token()
        if not token:
            print("[Error] Setup failed. Please try again.")
            sys.exit(1)

    # ── 加载配置 ──
    import gateway.config as cfg
    _cfg_path = args.config or ""
    if not _cfg_path:
        _exe_dir = os.path.dirname(os.path.abspath(__file__))
        _candidate = os.path.join(_exe_dir, "config.yaml")
        if os.path.exists(_candidate):
            _cfg_path = _candidate
    if _cfg_path:
        cfg.load(_cfg_path)
    else:
        cfg.load()

    port = args.port or cfg.gateway_port()
    host = args.host or cfg.gateway_host()
    temp_dir = cfg.temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)

    # ── 大脑连接地址（从配置文件或默认） ──
    brain_ws_url = os.environ.get(
        "AELVOXIM_BRAIN_WS_URL",
        os.environ.get("GATEWAY_BRAIN_WS_URL", ""),
    ) or cfg.get("gateway.brain_ws_url", "ws://127.0.0.1:9701/v1/gateway/ws")

    print("╔══════════════════════════════════════════╗")
    print("║     Aelvoxim Desktop Gateway             ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  HTTP:     http://{host}:{port}")
    print(f"  Brain WS: {brain_ws_url}")
    print(f"  User:     {token.get('email', '?')}")
    print()

    # ── 启动 HTTP 服务（线程）──
    from gateway.server import start_server
    http_thread = threading.Thread(
        target=start_server,
        args=(host, port),
        daemon=True,
    )
    http_thread.start()

    # ── 启动 WebSocket 客户端（独立线程+事件循环）──
    from gateway.client import GatewayClient
    import asyncio

    if args.service:
        _set_log_silent()

    client = GatewayClient(
        token=token.get("api_key", ""),
        brain_url=brain_ws_url,
    )

    def _run_ws():
        asyncio.set_event_loop(asyncio.new_event_loop())
        loop = asyncio.get_event_loop()
        try:
            print("[Main] WS thread started", flush=True)
            loop.run_until_complete(client.connect())
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"[Main] WS thread error: {e}", flush=True)
        finally:
            loop.close()

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()

    # 主线程保持运行
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nGateway stopped.")


# ═══════════════════════════════════════════
# Token 持久化
# ═══════════════════════════════════════════


def _save_token(token_data: dict):
    """保存认证 token 到本地文件。"""
    with open(_TOKEN_FILE, "w") as f:
        json.dump(token_data, f, ensure_ascii=False)
    print(f"[OK] Credentials saved")


def _load_token() -> dict | None:
    """从本地文件加载 token。"""
    if os.path.exists(_TOKEN_FILE):
        try:
            with open(_TOKEN_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Failed to load token file: {e}")
    return None


# ═══════════════════════════════════════════
# 首次配置页面 (内嵌 HTTP 服务)
# ═══════════════════════════════════════════


def _open_setup_page():
    """在浏览器中打开配置页面。"""
    import webbrowser
    url = f"http://127.0.0.1:{_SETUP_PORT}/setup"
    print(f"[Setup] Opening browser: {url}")
    print("  If browser does not open, visit the URL manually.")
    webbrowser.open(url)


def _run_setup_server():
    """启动简易 HTTP 服务，提供配置页面。"""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class SetupHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/setup":
                self._serve_setup_page()
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")

        def do_POST(self):
            if self.path == "/setup/login":
                self._handle_login()
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")

        def _serve_setup_page(self):
            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<title>Aelvoxim Gateway — 配置</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0b0d10;color:#e8eaed;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#1a1d23;border:1px solid #2a2d33;border-radius:16px;padding:32px;width:360px}}
  h2{{font-size:18px;margin-bottom:20px;color:#fff}}
  label{{display:block;font-size:13px;color:#9aa0a6;margin-bottom:4px}}
  input{{width:100%;padding:10px 12px;background:#0b0d10;border:1px solid #2a2d33;border-radius:8px;color:#e8eaed;font-size:14px;margin-bottom:16px}}
  input:focus{{outline:none;border-color:#3b82f6}}
  button{{width:100%;padding:10px;background:#3b82f6;border:none;border-radius:8px;color:#fff;font-size:14px;cursor:pointer}}
  button:hover{{background:#2563eb}}
  .error{{color:#ef4444;font-size:13px;margin-top:8px}}
  .success{{color:#10b981;font-size:13px;margin-top:8px}}
</style></head>
<body>
<div class="card">
  <h2>🔗 连接 Aelvoxim 大脑</h2>
  <p style="font-size:13px;color:#9aa0a6;margin-bottom:20px">输入您的 ChatAEL 账号信息以建立连接</p>
  <label>邮箱</label>
  <input type="email" id="email" placeholder="your@email.com" autocomplete="email">
  <label>密码</label>
  <input type="password" id="password" placeholder="密码" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()">
  <button onclick="doLogin()">连接</button>
  <div id="msg" class="error"></div>
</div>
<script>
async function doLogin() {{
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const msg = document.getElementById('msg');
  if (!email || !password) {{ msg.textContent = '请填写邮箱和密码'; return; }}
  msg.textContent = '连接中...';
  msg.className = '';
  try {{
    const res = await fetch('/setup/login', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, password}})
    }});
    const data = await res.json();
    if (data.success) {{
      msg.className = 'success';
      msg.textContent = '✅ 连接成功！即将启动 Gateway...';
      setTimeout(() => window.close(), 1500);
    }} else {{
      msg.className = 'error';
      msg.textContent = data.error || '登录失败';
    }}
  }} catch(e) {{
    msg.textContent = '网络错误: ' + e.message;
  }}
}}
</script>
</body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def _handle_login(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            email = body.get("email", "").strip()
            password = body.get("password", "")

            # 通过 API 验证登录（地址从环境变量或配置读取）
            import urllib.request
            _login_url = os.environ.get("AELVOXIM_LOGIN_URL", "http://127.0.0.1:9701")
            # 尝试读取 config.yaml
            _cfg_path = os.path.join(_THIS_DIR, "config.yaml")
            if os.path.exists(_cfg_path):
                try:
                    with open(_cfg_path) as _f:
                        import yaml
                        _yaml = yaml.safe_load(_f)
                        _lu = _yaml.get("gateway", {}).get("login_url", "")
                        if _lu:
                            _login_url = _lu
                except Exception:
                    pass
            try:
                req_data = json.dumps({
                    "email": email, "password": password
                }).encode()
                req = urllib.request.Request(
                    _login_url + "/v1/auth/login",
                    data=req_data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())

                api_key = data.get("api_key", "")
                if api_key:
                    _save_token({
                        "api_key": api_key,
                        "email": data.get("email", email),
                        "plan": data.get("plan", ""),
                    })
                    self._json({"success": True})
                else:
                    self._json({"success": False, "error": "登录失败"})
            except urllib.error.HTTPError as e:
                detail = "邮箱或密码错误" if e.code == 401 else f"服务器错误 ({e.code})"
                self._json({"success": False, "error": detail})
            except Exception as e:
                self._json({"success": False, "error": f"连接服务器失败: {e}"})

        def _json(self, data, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        def log_message(self, format, *args):
            pass  # 静默

    server = HTTPServer(("127.0.0.1", _SETUP_PORT), SetupHandler)
    print(f"  配置服务器: http://127.0.0.1:{_SETUP_PORT}/setup")
    print("  完成配置后请关闭此窗口。")
    server.serve_forever()


# ═══════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════


def _set_log_silent():
    """服务模式下减少日志输出。"""
    import logging
    logging.getLogger("aelvoxim.gateway").setLevel(logging.WARNING)

if __name__ == "__main__":
    main()
