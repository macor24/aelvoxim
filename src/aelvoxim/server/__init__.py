"""
metacore.server — FastAPI SaaS server for MetaCore

Aggregates all route modules and creates the FastAPI application.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import base routes first (defines _verify_key, router, public_router)
from .routes import router, public_router

# Import sub-routers and merge into the main router
from .routes_chat import router as _chat_router
from .routes_memory import router as _memory_router
from .routes_config import router as _config_router
from .routes_task import router as _task_router
from .routes_system import router as _system_router
from .routes_brain import router as _brain_router

router.include_router(_chat_router)
router.include_router(_memory_router)
router.include_router(_config_router)
router.include_router(_task_router)
router.include_router(_system_router)
router.include_router(_brain_router)

# Cortex routes (formerly 9703 orchestrator — planner management)
from ..cortex import router as _cortex_router
router.include_router(_cortex_router)

# Chimera and brain routers (external)
from ..chimera.routes import router as chimera_router
from ..orchestrator import router as brain_router


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Aelvoxim API",
        version="1.0.0",
        description="Self-evolving AI Agent — Standard Public API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(public_router)
    app.include_router(chimera_router)
    app.include_router(brain_router)

    # Forward-compat: /orchestrate without /v1 prefix (for ChatAEL-v2 frontend)
    @app.post("/orchestrate")
    async def orchestrate_root(request: dict):
        from .routes_chat import _handle_orchestrate
        return await _handle_orchestrate(request)

    # Auto-start Learner + .pyc cleanup on startup
    @app.on_event("startup")
    async def _startup_init():
        # Clean stale .pyc files first
        try:
            import subprocess, os as _os2
            _root = _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__)))
            subprocess.run(
                ["find", _root, "-name", "__pycache__", "-type", "d",
                 "-exec", "rm", "-rf", "{}", "+"],
                capture_output=True, timeout=10,
            )
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: pyc cleanup failed")
        # License verification + edition injection
        try:
            from aelvoxim.server.edition import current as _ed_current
            from aelvoxim.server.license import current_edition
            # Priority: 1) env var AELVOXIM_EDITION  2) AELVOXIM_LICENSE_KEY  3) community
            _lic_key = os.environ.get("AELVOXIM_LICENSE_KEY", "")
            if _lic_key:
                from aelvoxim.server.license import apply_license
                apply_license(_lic_key)
            _ed = current_edition()
            from aelvoxim.learn.knowledge import KnowledgeBase
            KnowledgeBase._current_plan = _ed
            import logging
            logging.getLogger("aelvoxim.server").info(
                "Edition: %s (license: %s)", _ed, "set" if _lic_key else "none"
            )
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: edition injection failed")

        # Start Learner
        try:
            from aelvoxim.learn.learner import get_learner, LEARNER_DIR
            LEARNER_DIR.mkdir(parents=True, exist_ok=True)
            (LEARNER_DIR / "enabled.flag").touch()
            _learner = get_learner()
            if _learner and not _learner.is_running():
                _learner.start()
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: learner start failed")

        # Start ProactiveEngine
        try:
            from aelvoxim.proactive.engine import ProactiveEngine
            _proactive = ProactiveEngine(tick_interval=300)
            _proactive.start()
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: proactive engine failed")

        # Start Watchdog (service health monitor + auto-heal)
        try:
            from aelvoxim.core.health import start_watchdog
            start_watchdog()
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: watchdog failed")

        # Start Cortex Scheduler (formerly 9703 orchestrator's background tick)
        try:
            from aelvoxim.cortex.scheduler import Scheduler
            from aelvoxim.planner import LongTermPlanner
            _cortex_scheduler = Scheduler(planner=LongTermPlanner())
            _cortex_scheduler.start()
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: cortex scheduler failed")

        # Start daily backup scheduler
        try:
            from aelvoxim.utils.backup import start_scheduler as _start_backup
            _start_backup()
        except Exception:
            import logging
            logging.getLogger("aelvoxim.server").exception("startup: backup scheduler failed")

    @app.get("/")
    async def root():
        from fastapi.responses import HTMLResponse
        from pathlib import Path
        _html = Path(__file__).parent / "portal.html"
        if _html.exists():
            return HTMLResponse(content=_html.read_text(encoding="utf-8"), status_code=200)
        return {
            "name": "Aelvoxim API",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/v1/health",
        }

    @app.get("/v1")
    async def v1_root():
        return {
            "endpoints": {
                "health": "GET /v1/health",
                "register": "POST /v1/auth/register?plan=free",
                "task_create": "POST /v1/task?goal=...&task_type=learn",
                "task_status": "GET /v1/task/{task_id}",
                "memory_read": "GET /v1/memory/{key}",
                "memory_write": "POST /v1/memory?key=...&value=...",
                "memory_search": "GET /v1/memory/search?q=...",
                "config_list": "GET /v1/config",
                "config_get": "GET /v1/config/{key}",
                "config_set": "POST /v1/config?key=...&value=...",
                "user_info": "GET /v1/user/me",
            },
            "auth": "Authorization: Bearer <your_api_key>",
        }

    @app.get("/api")
    async def console_page():
        from pathlib import Path
        html = Path(__file__).parent.joinpath("console.html").read_text(encoding="utf-8")
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html)

    @app.get("/v1/admin/panel")
    async def admin_panel():
        from pathlib import Path
        html = Path(__file__).parent.joinpath("admin_panel.html").read_text(encoding="utf-8")
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html)

    @app.get("/v1/admin/dash-full")
    async def admin_dash_full(token: str = ""):
        from pathlib import Path
        from fastapi.responses import HTMLResponse
        _dash = Path(__file__).parent.parent / "ui" / "dashboard.html"
        if _dash.exists():
            html = _dash.read_text(encoding="utf-8")
            if token:
                safe = token.replace('"', '&quot;').replace('<', '&lt;')
                auth = '<script>var _t="' + safe + '";var _dashToken=_t;var _f=window.fetch;window.fetch=function(u,o){if(typeof u=="string"&&u.indexOf("/v1/admin/")===0){o=o||{};o.headers=o.headers||{};o.headers["Authorization"]="Bearer "+_t;}return _f(u,o);};</script>'
                html = html.replace("</head>", auth + "</head>")
            return HTMLResponse(content=html)
        return {"error": "not found"}

    @app.get("/v1/status/planner")
    async def planner_status():
        """Report learner status for the Orchestrator's LongTermPlanner."""
        result = {
            "total_cycles": 0,
            "active_directions": 0,
            "total_entries": 0,
            "last_heartbeat": 0.0,
        }
        try:
            from aelvoxim.learn.loop import get_learner
            learner = get_learner()
            result["total_cycles"] = sum(
                d.cycles_completed for d in learner._directions.values()
            ) if hasattr(learner, '_directions') else 0
            result["active_directions"] = sum(
                1 for d in learner._directions.values()
                if d.status == "active"
            ) if hasattr(learner, '_directions') else 0
            result["total_entries"] = sum(
                d.entries_created for d in learner._directions.values()
            ) if hasattr(learner, '_directions') else 0
            result["last_heartbeat"] = getattr(learner, '_last_heartbeat', 0.0)
        except Exception:
            pass
        return result


        return result

    # Serve ChatAEL frontend (built SPA) at /chatael
    from pathlib import Path as _Path
    _chatael_dist = _Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "chatael-v2" / "dist"
    if _chatael_dist.exists():
        @app.get("/chatael")
        async def chatael_index():
            from fastapi.responses import HTMLResponse
            return HTMLResponse(content=(_chatael_dist / "index.html").read_text(encoding="utf-8"))

        @app.get("/chatael/{path:path}")
        async def chatael_spa(path: str):
            from fastapi.responses import HTMLResponse
            _file = _chatael_dist / path
            if _file.exists() and _file.is_file():
                _content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}.get(_file.suffix, "application/octet-stream")
                return HTMLResponse(content=_file.read_bytes(), status_code=200, media_type=_content_type)
            return HTMLResponse(content=(_chatael_dist / "index.html").read_text(encoding="utf-8"))

    return app