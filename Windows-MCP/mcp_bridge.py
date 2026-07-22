"""
mcp_bridge.py — 阿里云大脑 ↔ Windows-MCP 桥接脚本

作用：通过 WS 连阿里云大脑，把大脑的 execute 指令转成 MCP JSON-RPC 调 Windows-MCP
替代：aelvoxim-gateway（不需要 3,150 行代码）

用法：
    python mcp_bridge.py --token <api_key>

依赖：
    pip install websockets requests
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error

# ── 配置 ──
BRAIN_WS_URL = os.environ.get(
    "AELVOXIM_BRAIN_WS_URL",
    "ws://8.134.185.33:9701/v1/gateway/ws",
)
MCP_URL = os.environ.get("WINDOWS_MCP_URL", "http://127.0.0.1:8000/mcp")
RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30, 60, 120]
HEARTBEAT_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format="[MCP-Bridge] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("mcp_bridge")

# MCP 模式（stateless-http 无需 session）
_mcp_session_id: str = ""


def _mcp_initialize() -> bool:
    """初始化 MCP（stateless-http 模式不需要 session）"""
    global _mcp_session_id
    _mcp_session_id = "stateless"
    log.info("MCP stateless mode")
    return True


def _mcp_call(tool: str, args: dict) -> dict:
    """调用 MCP 工具（stateless-http 模式，每次独立请求）"""
    body = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
        "id": int(time.time() * 1000),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            sid = resp.headers.get("Mcp-Session-Id", "")
            if sid:
                _mcp_session_id = sid
            # SSE format: data: {...}
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "error" in data:
                        return {
                            "success": False,
                            "error": data["error"].get("message", str(data["error"])),
                        }
                    result = data.get("result", {})
                    content = result.get("content", [])
                    text_parts = []
                    for c in content:
                        if c.get("type") == "text":
                            text_parts.append(c.get("text", ""))
                        elif c.get("type") == "image":
                            text_parts.append("[screenshot]")
                    return {
                        "success": True,
                        "output": "\n".join(text_parts),
                        "raw": result,
                    }
            return {"success": False, "error": "No data in MCP response"}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"MCP HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"success": False, "error": f"MCP call failed: {e}"}


# ── Gateway 操作 → MCP 工具 映射 ──

# 大脑发来的 action → MCP tool name
_ACTION_MAP = {
    "open": ("App", {"mode": "launch", "name": "{target}"}),
    "open_app": ("App", {"mode": "launch", "name": "{target}"}),
    "launch": ("App", {"mode": "launch", "name": "{target}"}),
    "activate_window": ("App", {"mode": "switch", "name": "{target}"}),
    "switch": ("App", {"mode": "switch", "name": "{target}"}),
    "click": ("Click", {"mode": "click", "coords": "{params.coords}"}),
    "click_button": ("Click", {"mode": "click", "coords": "{params.coords}"}),
    "mouse_click": ("Click", {"mode": "click", "coords": "{params.coords}"}),
    "type_text": ("Type", {"text": "{target}"}),
    "send_keys": ("Shortcut", {"keys": "{target}"}),
    "press_key": ("Shortcut", {"keys": "{target}"}),
    "screenshot": ("Snapshot", {"use_vision": False}),
    "ocr_screenshot": ("Snapshot", {"use_vision": False}),
    "scroll": ("Scroll", {"clicks": "{params.clicks}"}),
    "wait": ("Wait", {"ms": "{params.ms}"}),
}

# 大脑 action → MCP tool 的精确参数表（用于参数复杂的操作）
_PARAM_MAP = {
    "open": lambda op: ("PowerShell", {"command": f"Start-Process '{op.get('target', 'notepad')}'", "timeout": 15}),
    "open_app": lambda op: ("PowerShell", {"command": f"Start-Process '{op.get('target', 'notepad')}'", "timeout": 15}),
    "screenshot": lambda op: ("Snapshot", {"use_vision": False}),
    "click": lambda op: _build_click(op),
    "type_text": lambda op: ("Type", {"text": op.get("target", "")}),
}


def _build_click(op: dict) -> tuple:
    params = op.get("params", {})
    target = op.get("target", "")
    if isinstance(target, str) and target.replace(".", "").isdigit():
        coords = [int(x.strip()) for x in target.split(",")]
    elif "x" in params and "y" in params:
        coords = [params["x"], params["y"]]
    else:
        coords = [500, 500]
    return ("Click", {"mode": "click", "coords": coords})


def _execute_operation(operation: dict) -> dict:
    """执行桌面操作（通过 Windows-MCP）"""
    action = operation.get("action", "")
    target = operation.get("target", "")
    params = operation.get("params", {})

    # 先用精确参数表
    if action in _PARAM_MAP:
        tool, args = _PARAM_MAP[action](operation)
        return _mcp_call(tool, args)

    # 再用简单映射表
    if action in _ACTION_MAP:
        tool, template = _ACTION_MAP[action]
        args = {}
        for k, v in template.items():
            if isinstance(v, str) and "{" in v:
                # 替换占位符
                v = v.replace("{target}", target or "")
                v = v.replace("{params.coords}", str(params.get("coords", "")))
                v = v.replace("{params.clicks}", str(params.get("clicks", "1")))
                v = v.replace("{params.ms}", str(params.get("ms", "1000")))
            args[k] = v
        return _mcp_call(tool, args)

    # 未知操作：尝试用 PowerShell 执行
    if target:
        return _mcp_call("PowerShell", {"command": target, "timeout": 30})
    return {"success": False, "error": f"Unknown action: {action}"}


# ── WebSocket 客户端（连阿里云大脑）──


async def run_bridge(token: str):
    """启动桥接，连阿里云大脑"""
    import websockets

    # 先初始化 MCP
    log.info("Initializing MCP connection to %s ...", MCP_URL)
    if not _mcp_initialize():
        log.warning("MCP init failed, will retry after WS connect")

    log.info("Connecting to brain %s ...", BRAIN_WS_URL)
    reconnect_idx = 0
    while True:
        try:
            async with websockets.connect(BRAIN_WS_URL) as ws:
                log.info("WS connected to brain")
                reconnect_idx = 0

                # 认证
                await ws.send(json.dumps({"type": "auth", "token": token}))
                auth_resp = await asyncio.wait_for(ws.recv(), timeout=15)
                auth_data = json.loads(auth_resp)
                if auth_data.get("type") == "error":
                    log.error("Auth failed: %s", auth_data.get("detail"))
                    return
                if auth_data.get("type") == "auth_ok":
                    log.info("Authenticated as %s", auth_data.get("user", ""))

                # 定期通知 MCP 状态
                asyncio.create_task(_heartbeat_loop(ws))

                # 消息循环
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "execute":
                        cmd_id = msg.get("id", "")
                        operation = msg.get("operation", {})
                        log.info(
                            "Execute: action=%s target=%s",
                            operation.get("action", ""),
                            operation.get("target", "")[:30],
                        )
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, _execute_operation, operation
                        )
                        await ws.send_json({
                            "type": "result",
                            "id": cmd_id,
                            **result,
                        })

                    elif msg.get("type") == "ping":
                        await ws.send_json({"type": "pong"})

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Connection error: %s", e)

        delay = RECONNECT_DELAYS[min(reconnect_idx, len(RECONNECT_DELAYS) - 1)]
        reconnect_idx += 1
        log.info("Reconnecting in %ds ...", delay)
        await asyncio.sleep(delay)


async def _heartbeat_loop(ws):
    """定期发送心跳保持连接"""
    import websockets

    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                # 顺便确保 MCP session 有效
                global _mcp_session_id
                if not _mcp_session_id:
                    _mcp_initialize()
                await ws.send(json.dumps({"type": "ping"}))
            except websockets.ConnectionClosed:
                break
    except asyncio.CancelledError:
        pass


# ── 入口 ──


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Aelvoxim ↔ Windows-MCP Bridge")
    parser.add_argument("--token", required=True, help="API key from Aelvoxim setup")
    parser.add_argument("--mcp-url", default=None, help="Windows-MCP URL")
    parser.add_argument("--brain-url", default=None, help="Brain WS URL")
    args = parser.parse_args()

    global MCP_URL, BRAIN_WS_URL
    MCP_URL = args.mcp_url or MCP_URL
    BRAIN_WS_URL = args.brain_url or BRAIN_WS_URL

    print("╔══════════════════════════════════════════╗")
    print("║  Aelvoxim Bridge — Windows-MCP           ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  Brain WS: {BRAIN_WS_URL}")
    print(f"  MCP URL:  {MCP_URL}")
    print()

    try:
        asyncio.run(run_bridge(args.token))
    except KeyboardInterrupt:
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()
