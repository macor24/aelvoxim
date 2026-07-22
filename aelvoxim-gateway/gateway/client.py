"""client — Aelvoxim Gateway WebSocket 客户端

连接到云大脑，保持长连，处理指令，上报状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

# 确保包路径
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

log = logging.getLogger("aelvoxim.gateway.client")

# ── 配置 ──
# 从环境变量或 config.yaml 读取
_BRAIN_WS_URL = os.environ.get(
    "AELVOXIM_BRAIN_WS_URL",
    os.environ.get("GATEWAY_BRAIN_WS_URL", ""),
) or "ws://127.0.0.1:9701/v1/gateway/ws"
_RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30, 60, 120]  # 指数退避
_HEARTBEAT_INTERVAL = 30  # 秒


class GatewayClient:
    """WebSocket 客户端，维护与云大脑的长连接。"""

    def __init__(
        self,
        token: str,
        brain_url: str = "",
        executor: Optional[Any] = None,
    ):
        self.token = token
        self.brain_url = brain_url or _BRAIN_WS_URL
        self.executor = executor  # 桌面执行器实例
        self._ws = None
        self._running = False
        self._reconnect_idx = 0
        self._user_email = ""

    async def connect(self):
        """连接到大脑 WebSocket（自动重连）。"""
        import websockets

        self._running = True
        print("[WS] Starting WebSocket client...", flush=True)
        print(f"[WS] Connecting to {self.brain_url} ...", flush=True)
        while self._running:
            try:
                log.info("Connecting to %s ...", self.brain_url)
                print(f"[WS] Attempting connection...")
                async with websockets.connect(self.brain_url) as ws:
                    self._ws = ws
                    self._reconnect_idx = 0
                    print(f"[WS] Connected!")
                    # 发送认证
                    await ws.send(json.dumps({
                        "type": "auth",
                        "token": self.token,
                        "version": self._get_version(),
                    }))

                    # 等待认证结果
                    resp = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(resp)

                    if data.get("type") == "error":
                        log.error("Auth failed: %s", data.get("detail"))
                        # Token 无效，不再重试
                        self._running = False
                        return

                    if data.get("type") == "auth_ok":
                        self._user_email = data.get("user", "")
                    print(f"[WS] Authenticated as {data.get('user','')}")

                    # 启动心跳任务
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

                    # 发送初始状态
                    await self._send_status(ws)

                    # 消息循环
                    msg_count = 0
                    async for raw in ws:
                        msg_count += 1
                        print(f"[WS] Recv #{msg_count}: {raw[:80]}")
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_message(ws, msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Connection error: %s", e)

            if not self._running:
                break

            # 重连
            delay = _RECONNECT_DELAYS[min(
                self._reconnect_idx,
                len(_RECONNECT_DELAYS) - 1,
            )]
            self._reconnect_idx += 1
            log.info("Reconnecting in %ds ...", delay)
            await asyncio.sleep(delay)

    async def disconnect(self):
        """断开连接。"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _heartbeat_loop(self, ws):
        """定期发送心跳保持连接。"""
        while self._running:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    async def _send_status(self, ws):
        """上报 Gateway 状态。"""
        try:
            await ws.send(json.dumps({
                "type": "status",
                "desktop": "ready",
                "version": self._get_version(),
                "os": sys.platform,
            }))
        except Exception as e:
            log.warning("Failed to send status: %s", e)

    async def _handle_message(self, ws, msg: dict):
        """处理来自大脑的消息。"""
        msg_type = msg.get("type", "")

        if msg_type == "execute":
            # 大脑发来的桌面操作指令
            cmd_id = msg.get("id", "")
            operation = msg.get("operation", {})

            log.info("Execute command: %s", cmd_id)

            # 执行操作
            result = await self._execute_operation(operation)

            # 返回结果
            try:
                await ws.send(json.dumps({
                    "type": "result",
                    "id": cmd_id,
                    **result,
                }))
            except Exception as e:
                log.warning("Failed to send result: %s", e)

        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

        elif msg_type == "auth_ok":
            # 已经认证过了，忽略
            pass

    async def _execute_operation(self, operation: dict) -> dict:
        """执行桌面操作并返回结果。

        如果 executor 已设置，委托给它执行；
        否则通过本地 HTTP API 调用 executor。
        """
        if self.executor:
            # 直接调用
            try:
                action = operation.get("action", "")
                target = operation.get("target", "")
                params = operation.get("params", {})

                # 导入执行器
                from .executor import ACTIONS

                handler = ACTIONS.get(action)
                if handler:
                    result = handler(operation)
                    return result if isinstance(result, dict) else {"success": True, "data": result}
                return {"success": False, "error": f"Unknown action: {action}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            # 通过本地 HTTP API 调用
            import urllib.request as _ur

            try:
                body = json.dumps({"operation": operation}).encode()
                req = _ur.Request(
                    f"http://127.0.0.1:9705/api/execute",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with _ur.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode())
            except Exception as e:
                return {"success": False, "error": f"Local Gateway error: {e}"}

    @staticmethod
    def _get_version() -> str:
        """获取 Gateway 版本号。"""
        try:
            from .server import __version__
            return __version__
        except (ImportError, AttributeError):
            return "1.0.0"


# ── 便捷启动 ──


async def run_client(token: str, brain_url: str = ""):
    """简便方法：创建客户端并运行。"""
    client = GatewayClient(token=token, brain_url=brain_url)
    try:
        await client.connect()
    except KeyboardInterrupt:
        pass
    finally:
        await client.disconnect()


def start(token: str, brain_url: str = ""):
    """同步入口。"""
    asyncio.run(run_client(token, brain_url))
