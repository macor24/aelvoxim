"""gateway_ws — WebSocket 服务端
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("aelvoxim.gateway_ws")

# 活跃连接和待处理指令（模块级别，通过函数访问）
_GATEWAY_CONNECTIONS: Dict[str, Dict[str, Any]] = {}
_PENDING_COMMANDS: Dict[str, list] = {}


def get_pool() -> Dict:
    return _GATEWAY_CONNECTIONS


def get_user_gateway(email: str) -> Optional[Dict[str, Any]]:
    info = _GATEWAY_CONNECTIONS.get(email)
    if info and info.get("ws"):
        return {"online": True, "connected_at": info.get("connected_at", 0), "version": info.get("version", "")}
    return None


async def send_to_gateway(email: str, message: dict) -> bool:
    info = _GATEWAY_CONNECTIONS.get(email)
    if not info or not info.get("ws"):
        return False
    try:
        await info["ws"].send_json(message)
        return True
    except Exception:
        _disconnect(email)
        return False


async def execute_on_gateway(email: str, operation: dict, timeout: float = 60.0) -> Optional[Dict]:
    cmd_id = f"cmd_{int(time.time() * 1000)}_{hash(str(operation)) % 10000}"
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    if email not in _PENDING_COMMANDS:
        _PENDING_COMMANDS[email] = []
    _PENDING_COMMANDS[email].append({"id": cmd_id, "future": future})
    sent = await send_to_gateway(email, {"type": "execute", "id": cmd_id, "operation": operation})
    if not sent:
        if email in _PENDING_COMMANDS:
            _PENDING_COMMANDS[email] = [c for c in _PENDING_COMMANDS[email] if c["id"] != cmd_id]
        return None
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        if email in _PENDING_COMMANDS:
            _PENDING_COMMANDS[email] = [c for c in _PENDING_COMMANDS[email] if c["id"] != cmd_id]
        return {"success": False, "error": "Gateway operation timed out"}
    finally:
        if email in _PENDING_COMMANDS:
            _PENDING_COMMANDS[email] = [c for c in _PENDING_COMMANDS[email] if c["id"] != cmd_id]


def _disconnect(email: str):
    if email in _GATEWAY_CONNECTIONS:
        del _GATEWAY_CONNECTIONS[email]
    if email in _PENDING_COMMANDS:
        for cmd in _PENDING_COMMANDS[email]:
            if not cmd["future"].done():
                cmd["future"].set_result({"success": False, "error": "Gateway disconnected"})
        del _PENDING_COMMANDS[email]
    log.info("Gateway disconnected: %s", email)


def _handle_result(email: str, msg: dict):
    cmd_id = msg.get("id", "")
    if not cmd_id or email not in _PENDING_COMMANDS:
        return
    for cmd in _PENDING_COMMANDS[email]:
        if cmd["id"] == cmd_id and not cmd["future"].done():
            cmd["future"].set_result(msg)
            break


async def handle_gateway_ws(ws: WebSocket):
    """Handle a Gateway WebSocket connection. Called from create_app()."""
    from .auth import find_user

    await ws.accept()
    email = ""
    try:
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "error", "detail": "Authentication timeout"})
            await ws.close()
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "detail": "Invalid JSON"})
            await ws.close()
            return
        if msg.get("type") != "auth" or not msg.get("token"):
            await ws.send_json({"type": "error", "detail": "Auth message required"})
            await ws.close()
            return
        user = find_user(msg["token"])
        if not user:
            await ws.send_json({"type": "error", "detail": "Invalid token"})
            await ws.close()
            return
        email = user.get("email", "")
        if not email:
            await ws.send_json({"type": "error", "detail": "User has no email"})
            await ws.close()
            return
        old_info = _GATEWAY_CONNECTIONS.get(email)
        if old_info:
            try:
                await old_info["ws"].close()
            except Exception:
                log.exception("gateway_ws error")
        _GATEWAY_CONNECTIONS[email] = {"ws": ws, "connected_at": time.time(), "version": msg.get("version", "")}
        await ws.send_json({"type": "auth_ok", "user": email})
        log.info("Gateway connected: %s", email)
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type", "")
            if t == "ping":
                await ws.send_json({"type": "pong"})
            elif t == "result":
                _handle_result(email, msg)
            elif t == "status":
                info = _GATEWAY_CONNECTIONS.get(email)
                if info:
                    info["version"] = msg.get("version", info.get("version", ""))
    except WebSocketDisconnect:
        log.exception("gateway_ws error")
    except Exception as e:
        log.exception("Gateway WS error for %s: %s", email, e)
    finally:
        if email:
            _disconnect(email)
