# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.server — FastAPI HTTP + WebSocket server for Desktop Gateway.

Provides:
    GET  /api/context    — Latest software context (JSON)
    GET  /api/status     — Gateway health status
    POST /api/execute    — Execute a single operation
    POST /api/execute-plan — Execute multi-step plan
    POST /api/run-script — Run a script file
    POST /api/screenshot — Take screenshot of a window
    POST /api/history    — Get execution history
    WS   /ws             — Real-time context push
    POST /api/control/pause   — Pause execution
    POST /api/control/resume  — Resume execution
    POST /api/control/abort   — Abort execution
    POST /api/control/confirm — Confirm next step
    GET  /api/control/state   — Get controller state
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List

from pydantic import BaseModel

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

__version__ = "0.2.0"

from .context import scan_snapshots, normalize, get_latest, cleanup_old
from .executor import execute as _exec_op
from . import controller as _controller
from gateway import config as _cfg

log = logging.getLogger("aelvoxim.gateway")

# ── Lifespan (startup / shutdown) ──


@asynccontextmanager
async def _lifespan(_app):
    # Startup: ensure temp dir exists and start context push task
    temp_dir = _cfg.temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    interval = _cfg.refresh_interval()
    push_task = asyncio.create_task(_context_push_loop(interval))
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    print("  Gateway HTTP: FastAPI")
    print(f"  Context refresh: every {interval}s")
    print(f"  Temp dir: {temp_dir}")
    yield
    # Shutdown: cancel background tasks
    push_task.cancel()
    heartbeat_task.cancel()


# ── FastAPI app ──

app = FastAPI(
    title="Aelvoxim Desktop Gateway",
    version="0.2.0",
    description="Universal desktop software adapter — controls Windows desktop apps via UIA/VLM.",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Key auth ──
_GATEWAY_API_KEY = os.environ.get("AELVOXIM_GATEWAY_KEY", "")


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    # No auth for health check and root
    if request.url.path in ("/", "/api/status"):
        return await call_next(request)
    # If a key is configured, validate it
    if _GATEWAY_API_KEY:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != _GATEWAY_API_KEY:
            return JSONResponse(status_code=403, content={"error": "Forbidden"})
    return await call_next(request)

# ── Globals ──
_task_ctrl = _controller.TaskController()
_ws_clients: List[WebSocket] = []


# ═══════════════════════════════════════════
# WebSocket manager
# ═══════════════════════════════════════════


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = ConnectionManager()


# ═══════════════════════════════════════════
# Context push loop (periodic)
# ═══════════════════════════════════════════


_last_context_payload: str = ""


async def _context_push_loop(interval: float = 3.0):
    """Periodically scan and push context changes to WebSocket clients."""
    global _last_context_payload
    while True:
        await asyncio.sleep(interval)
        try:
            temp_dir = _cfg.temp_dir()
            snapshots = scan_snapshots(str(temp_dir))
            if not snapshots:
                continue
            contexts = {sw: normalize(snap) for sw, snap in snapshots.items()}
            payload = json.dumps({
                "type": "context",
                "contexts": contexts,
                "ts": time.time(),
            }, ensure_ascii=False)
            if payload != _last_context_payload:
                _last_context_payload = payload
                await _manager.broadcast({"type": "context", "contexts": contexts, "ts": time.time()})
        except Exception:
            log.exception("context push error")


async def _heartbeat_loop(interval: float = 30.0):
    """Periodically report Gateway health to Aelvoxim brain."""
    import urllib.request as _ur
    import json as _js
    _brain_url = _cfg.get("gateway.brain_ws_url", "http://127.0.0.1:9701/v1/gateway/ws")
    # 从 ws:// 协议提取 HTTP base URL
    _base = _brain_url.replace("ws://", "http://").replace("/v1/gateway/ws", "")
    _url = f"{_base}/v1/heartbeat"
    _payload = _js.dumps({
        "service": "gateway",
        "port": _cfg.gateway_port(),
        "version": "0.2.0",
    }).encode()
    while True:
        await asyncio.sleep(interval)
        try:
            req = _ur.Request(
                _url, data=_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _ur.urlopen(req, timeout=5):
                pass
        except Exception as e:
            log.debug("Heartbeat failed: %s", e)


# ═══════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════


@app.get("/")
async def root():
    return {"message": "Aelvoxim Desktop Gateway"}


@app.get("/api/context")
async def get_context():
    temp_dir = _cfg.temp_dir()
    snapshots = scan_snapshots(str(temp_dir))
    contexts: Dict[str, str] = {}
    for sw, snap in snapshots.items():
        contexts[sw] = normalize(snap)
    result = {
        "contexts": contexts,
        "timestamp": time.time(),
        "software_count": len(snapshots),
    }
    if snapshots:
        result["raw"] = snapshots
    return result


@app.get("/api/status")
async def get_status():
    temp_dir = _cfg.temp_dir()
    snapshots = scan_snapshots(str(temp_dir))
    return {
        "running": True,
        "port": _cfg.gateway_port(),
        "host": _cfg.gateway_host(),
        "software_count": len(snapshots),
        "software_list": list(snapshots.keys()),
        "temp_dir": str(temp_dir),
        "cleanup": cleanup_old(str(temp_dir)),
    }


class ExecuteRequest(BaseModel):
    operation: Dict[str, Any]


class ExecutePlanRequest(BaseModel):
    plan: List[Dict[str, Any]]


class RunScriptRequest(BaseModel):
    script_path: str
    app: str = "photoshop"


class ScreenshotRequest(BaseModel):
    window_title: str = ""


class ConfirmRequest(BaseModel):
    confirm: bool = True


@app.post("/api/execute")
async def post_execute(body: ExecuteRequest):
    try:
        result = _exec_op(body.operation)
        return result
    except Exception:
        log.exception("execute failed")
        raise HTTPException(500, detail="Execution failed")


@app.post("/api/execute-plan")
async def post_execute_plan(body: ExecutePlanRequest):
    try:
        if not body.plan:
            raise HTTPException(400, detail="plan required")
        result = _task_ctrl.execute_plan(body.plan)
        return result
    except Exception:
        log.exception("plan execution failed")
        raise HTTPException(500, detail="Plan execution failed")


@app.post("/api/run-script")
async def post_run_script(body: RunScriptRequest):
    try:
        if not body.script_path:
            raise HTTPException(400, detail="script_path required")
        scripts_dir = _cfg.get("gateway.scripts_dir", "./scripts")
        full_path = os.path.join(scripts_dir, body.script_path) if not os.path.isabs(body.script_path) else body.script_path
        from .executor import execute
        result = execute({"action": "run_script", "params": {"path": full_path}})
        return result
    except Exception:
        log.exception("run_script failed")
        raise HTTPException(500, detail="Script execution failed")


@app.post("/api/screenshot")
async def post_screenshot(body: ScreenshotRequest):
    try:
        from .executor._uia import screenshot
        result = screenshot(body.window_title)
        return result
    except Exception:
        log.exception("screenshot failed")
        raise HTTPException(500, detail="Screenshot failed")


@app.post("/api/history")
async def post_history():
    return {"history": _task_ctrl.get_history()}


@app.post("/api/control/pause")
async def control_pause():
    _controller.pause()
    return {"state": "paused"}


@app.post("/api/control/resume")
async def control_resume():
    _controller.resume()
    return {"state": "running"}


@app.post("/api/control/abort")
async def control_abort():
    _controller.abort()
    return {"state": "aborted"}


@app.post("/api/control/confirm")
async def control_confirm(body: ConfirmRequest):
    _controller.confirm_next(body.confirm)
    _controller.resume()
    return {"confirm": body.confirm}


@app.get("/api/control/state")
async def control_state():
    return _controller.get_state()


# ═══════════════════════════════════════════
# WebSocket — real-time context push
# ═══════════════════════════════════════════


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await _manager.connect(ws)
    try:
        while True:
            # Read any incoming messages (ping/keepalive)
            data = await ws.receive_text()
            # Echo back as ack
            await ws.send_json({"type": "ack", "data": data})
    except WebSocketDisconnect:
        _manager.disconnect(ws)
    except Exception:
        _manager.disconnect(ws)


# ═══════════════════════════════════════════
# Convenience: run via uvicorn
# ═══════════════════════════════════════════


def start_server(host: str = "127.0.0.1", port: int = 9705):
    """Start Gateway FastAPI server via uvicorn."""
    import uvicorn
    from gateway.server import app as _app
    uvicorn.run(
        _app,
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )
