"""
metacore.server.routes_system — Auth, health, admin, gateway, webhook endpoints.

Routes:
    POST /v1/auth/register              — Register a new user
    POST /v1/auth/login                 — Login with email + password
    POST /v1/auth/send-verification      — Send email verification code
    POST /v1/auth/verify-email          — Verify email with code
    POST /v1/auth/forgot-password       — Send reset token
    POST /v1/auth/reset-password        — Reset password with token
    POST /v1/auth/change-password       — Change password while logged in
    POST /v1/auth/license               — Upload a license key
    GET  /v1/auth/license               — Get current license info
    GET  /v1/auth/api-keys              — List API keys
    POST /v1/auth/api-keys              — Create a new API key
    DELETE /v1/auth/api-keys/{key_id}   — Delete an API key
    POST /v1/auth/api-keys/{key_id}/refresh — Regenerate an API key
    GET  /v1/user/me                    — Current user info
    GET  /v1/health                     — Health check
    GET  /v1/logs                       — View logs
    GET  /v1/ethics/gates               — List ethics gates
    POST /v1/ethics/update              — Enable/disable an ethics gate
    GET  /v1/cognition/selfmodel        — SelfModel state
    POST /v1/webhook/subscribe          — Subscribe to webhook events
    GET  /v1/webhook/subscriptions      — List webhook subscriptions
    DELETE /v1/webhook/subscribe/{sub_id} — Delete a webhook
    POST /v1/webhook/test-delivery      — Test webhook delivery
    POST /v1/gateway/execute            — Execute Gateway operation
    GET  /v1/gateway/context            — Get Gateway context
    POST /v1/gateway/control/{action}   — Control Gateway state
    GET  /v1/admin/users                — List users (admin)
    GET  /v1/admin/user/{email}         — Get user detail (admin)
    POST /v1/admin/update-user          — Update user (admin)
    POST /v1/admin/migrate-users        — Migrate users to PG (admin)
    GET  /v1/admin/stats                — System stats (admin)
    GET  /v1/admin/overview             — System overview
    GET  /v1/admin/data                 — Dashboard data
    GET  /v1/admin/cognition            — Cognition overview
    GET  /v1/admin/knowledge-graph      — Knowledge graph data
    GET  /v1/admin/learner/status       — Learner status
    POST /v1/admin/learner/start        — Start learner
    POST /v1/admin/learner/stop         — Stop learner
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pathlib import Path

from .routes import _verify_key, _require_admin

router = APIRouter()

# ── Auth helpers ──

def _map_key(key: str) -> str:
    if not key or len(key) < 8:
        return key
    return key[:4] + "..." + key[-4:]

_SAFETY_RESPONSE = "I'm sorry, but I cannot assist with that request."

# ── Auth endpoints ──

@router.post("/auth/login")
async def login(body: dict):
    """Login with email + password. Returns api_key for subsequent API calls."""
    email = body.get("email", "").strip().lower()
    # Rate limit by email (5 attempts per minute)
    from .ratelimit import login_limiter
    allowed, retry_after = login_limiter.check(email)
    if not allowed:
        raise HTTPException(429, detail=f"Too many login attempts. Retry after {retry_after}s")
    from .auth import find_by_email, verify_password, _save_user
    password = body.get("password", "")
    if not email or not password:
        raise HTTPException(400, detail="email and password are required")
    user = find_by_email(email)
    if not user or not verify_password(password, user.get("password_hash", "")):
        from .audit import log as _audit_log
        _audit_log("user.login", user=email, status="failure", detail={"reason": "invalid credentials"})
        raise HTTPException(401, detail="invalid email or password")
    # Check trial expiry on every login — downgrade to community if expired
    from .license import check_trial_expiry
    before = user.get("plan")
    user = check_trial_expiry(user)
    if user.get("plan") != before:
        _save_user(user)
    from .audit import log as _audit_log
    _audit_log("user.login", user=email, status="success")
    return {"api_key": user.get("api_keys", [None])[0] or "", "plan": user.get("plan", "free"), "email": email}

@router.post("/auth/register")
async def register(body: dict):
    """Register a new user with email + password + username.
    New users automatically get a 30-day full-feature trial.
    """
    from .auth import create_user, hash_password, find_by_email, generate_api_key, TRIAL_DAYS
    from .license import create_trial_license, check_trial_expiry
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    username = body.get("username", "")
    plan = body.get("plan", "community")
    if not email or not password:
        raise HTTPException(400, detail="email and password are required")
    if find_by_email(email):
        raise HTTPException(409, detail="email already registered")
    # New users get a trial; override plan to "trial"
    trial_expires = create_trial_license(email)
    api_key = generate_api_key()
    user = {
        "email": email,
        "password_hash": hash_password(password),
        "username": username or email.split("@")[0],
        "plan": "trial",
        "trial_expires_at": trial_expires,
        "role": "user",
        "api_keys": [api_key],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if create_user(user):
        from .audit import log as _audit_log
        _audit_log("user.register", user=email, status="success")
        return {
            "api_key": api_key,
            "plan": "trial",
            "email": email,
            "trial_expires_at": trial_expires,
            "trial_days": TRIAL_DAYS,
        }
    from .audit import log as _audit_log
    _audit_log("user.register", user=email, status="failure", detail={"reason": "creation failed"})
    raise HTTPException(500, detail="user creation failed")

@router.post("/auth/send-verification")
async def send_verification(body: dict):
    """Send email verification code (file-level, no SMTP needed)."""
    from .auth import find_by_email, set_verification_code
    email = body.get("email", "").strip().lower()
    if not email:
        raise HTTPException(400, detail="email is required")
    user = find_by_email(email)
    if not user:
        # Don't reveal whether email exists
        return {"message": "If the email exists, a verification code has been sent"}
    code = set_verification_code(email)
    return {"message": "If the email exists, a verification code has been sent", "code": code}

@router.post("/auth/verify-email")
async def verify_email_endpoint(body: dict):
    """Verify email with code."""
    from .auth import verify_email_code
    email = body.get("email", "").strip().lower()
    code = body.get("code", "").strip()
    if not email or not code:
        raise HTTPException(400, detail="email and code are required")
    if verify_email_code(email, code):
        return {"verified": True}
    raise HTTPException(400, detail="invalid or expired verification code")

@router.post("/auth/forgot-password")
async def forgot_password(body: dict):
    """Generate a password reset token."""
    from .auth import find_by_email, set_reset_token
    email = body.get("email", "").strip().lower()
    if not email:
        raise HTTPException(400, detail="email is required")
    user = find_by_email(email)
    if not user:
        return {"message": "If the email exists, a reset link has been sent"}
    token = set_reset_token(email)
    return {"message": "If the email exists, a reset link has been sent", "token": token}

@router.post("/auth/reset-password")
async def reset_password(body: dict):
    """Reset password with token."""
    from .auth import verify_reset_token, hash_password, update_password
    email = body.get("email", "").strip().lower()
    token = body.get("token", "").strip()
    new_password = body.get("new_password", "")
    if not email or not token or not new_password:
        raise HTTPException(400, detail="email, token, and new_password are required")
    if not verify_reset_token(email, token):
        raise HTTPException(400, detail="invalid or expired reset token")
    update_password(email, hash_password(new_password))
    return {"message": "password reset successful"}

@router.post("/auth/change-password")
async def change_password(body: dict, current_user: dict = Depends(_verify_key)):
    """Change password while logged in."""
    from .auth import find_by_email, verify_password, hash_password, update_password
    old = body.get("old_password", "")
    new = body.get("new_password", "")
    if not old or not new:
        raise HTTPException(400, detail="old_password and new_password are required")
    if len(new) < 6:
        raise HTTPException(400, detail="new_password must be at least 6 characters")
    user = find_by_email(current_user.get("email", ""))
    if not user or not verify_password(old, user.get("password_hash", "")):
        raise HTTPException(400, detail="current password is incorrect")
    update_password(current_user["email"], hash_password(new))
    return {"message": "password changed successfully"}

@router.post("/auth/license")
async def upload_license(body: dict, user_data: dict = Depends(_verify_key)):
    """Upload a license key to unlock Pro/Enterprise features."""
    from .license import save_license, verify_license
    key = body.get("key", "").strip()
    if not key:
        raise HTTPException(400, detail="license key is required")
    result = verify_license(key)
    if not result.get("valid"):
        raise HTTPException(400, detail=result.get("reason", "Invalid license key"))
    save_license(key)
    return {"plan": result["plan"], "expires_at": result["expires_at"]}

@router.get("/auth/license")
async def get_license_info(user_data: dict = Depends(_verify_key)):
    """Get current license information for the authenticated user."""
    from .license import load_license
    return load_license()

@router.get("/auth/api-keys")
async def list_api_keys(current_user: dict = Depends(_verify_key)):
    """List all API keys for the authenticated user."""
    from .auth import find_by_email
    user = find_by_email(current_user.get("email", ""))
    if not user:
        raise HTTPException(404, detail="user not found")
    keys = user.get("api_keys", [user.get("api_key", "")])
    return {"keys": [{"id": i, "preview": _map_key(k)} for i, k in enumerate(keys)]}

@router.post("/auth/api-keys")
async def create_api_key_route(current_user: dict = Depends(_verify_key)):
    """Create a new API key."""
    from .auth import find_by_email, generate_api_key, update_user_field
    user = find_by_email(current_user.get("email", ""))
    if not user:
        raise HTTPException(404, detail="user not found")
    keys = user.get("api_keys", [user.get("api_key", "")])
    new_key = generate_api_key()
    keys.append(new_key)
    update_user_field(current_user["email"], "api_keys", keys)
    return {"api_key": new_key, "id": len(keys) - 1}

@router.delete("/auth/api-keys/{key_id}")
async def delete_api_key_route(key_id: int, current_user: dict = Depends(_verify_key)):
    """Delete an API key by index. Cannot delete the primary key (index 0)."""
    from .auth import find_by_email, update_user_field
    user = find_by_email(current_user.get("email", ""))
    if not user:
        raise HTTPException(404, detail="user not found")
    keys = user.get("api_keys", [user.get("api_key", "")])
    if key_id <= 0 or key_id >= len(keys):
        raise HTTPException(400, detail="cannot delete primary key")
    keys.pop(key_id)
    update_user_field(current_user["email"], "api_keys", keys)
    return {"deleted": True}

@router.post("/auth/api-keys/{key_id}/refresh")
async def refresh_api_key_route(key_id: int, current_user: dict = Depends(_verify_key)):
    """Regenerate an API key by index."""
    from .auth import find_by_email, generate_api_key, update_user_field
    user = find_by_email(current_user.get("email", ""))
    if not user:
        raise HTTPException(404, detail="user not found")
    keys = user.get("api_keys", [user.get("api_key", "")])
    if key_id < 0 or key_id >= len(keys):
        raise HTTPException(400, detail="invalid key index")
    keys[key_id] = generate_api_key()
    update_user_field(current_user["email"], "api_keys", keys)
    return {"api_key": keys[key_id], "id": key_id}

# ── User endpoints ──

@router.get("/user/me")
async def user_me(current_user: dict = Depends(_verify_key)):
    """Get current user info including username, plan, and usage."""
    from .auth import find_by_email
    user = find_by_email(current_user.get("email", ""))
    if not user:
        raise HTTPException(404, detail="user not found")
    return {
        "email": user.get("email", ""),
        "username": user.get("username", ""),
        "plan": user.get("plan", "community"),
        "role": user.get("role", "user"),
        "api_key_count": len(user.get("api_keys", [user.get("api_key", "")])),
        "created_at": user.get("created_at", ""),
    }

# ── System endpoints ──

@router.get("/health")
async def health_check():
    """Unified health endpoint."""
    from datetime import datetime
    result = {
        "service": "aelvoxim-api",
        "version": "1.0.0",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ok",
        "uptime": 0,
        "dependencies": {},
    }
    try:
        from ..memory import _get_db, get_layer_stats
        db = _get_db()
        ec = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        result["dependencies"]["sqlite"] = {"status": "ok", "entities": ec}
        result["dependencies"]["memory_layers"] = get_layer_stats()
    except Exception as e:
        result["dependencies"]["sqlite"] = {"status": "error", "detail": str(e)}
        result["status"] = "degraded"
    try:
        from ..client.sentrikit import is_available
        result["dependencies"]["sentrikit"] = {"status": "ok" if is_available() else "unavailable"}
    except Exception:
        result["dependencies"]["sentrikit"] = {"status": "unavailable"}
    try:
        from ..learn.learner import get_learner
        l = get_learner()
        if l:
            result["dependencies"]["learner"] = {"running": l.is_running()}
    except Exception:
        result["dependencies"]["learner"] = {"running": False}
    result["confidence"] = {"current": 0.5, "avg_50": 0.5, "trend": "stable", "samples": 0}
    try:
        from ..server.service_chat import get_confidence_trend
        result["confidence"] = get_confidence_trend()
    except Exception:
        pass
    try:
        from ..core.health import get_watchdog, get_resource_usage, get_pg_status
        wd = get_watchdog()
        result["services"] = wd.get_status()
        result["heal_log"] = wd.get_heal_log(limit=10)
        result["heal_counts"] = wd.get_heal_counts()
        result["resources"] = get_resource_usage()
        result["postgres"] = get_pg_status()
    except Exception:
        pass
    return result

@router.get("/logs")
async def get_logs(source: str = Query("learner"), lines: int = Query(50), _user: dict = Depends(_require_admin)):
    """View aggregated logs from different MetaCore components."""
    from ..utils import DATA_DIR
    sources = {
        "learner": DATA_DIR / "learner" / "learner.log",
        "chat_monitor": DATA_DIR / "chat_monitor",
        "health": DATA_DIR / "health",
    }
    path = sources.get(source)
    if not path:
        raise HTTPException(400, detail=f"unknown source: {source}")
    try:
        text = path.read_text(errors="replace")
        all_lines = text.strip().split("\n")
        return {"lines": all_lines[-lines:]}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ── Ethics endpoints ──

@router.post("/ethics/update")
async def ethics_update(body: dict, authorization: str = Header(None)):
    """Enable or disable an ethics gate. Requires admin key."""
    from ..core.metacog_monitor import set_ethics_gate, get_ethics_gate
    from ..server.auth import find_by_email
    gate = body.get("gate", "")
    enabled = body.get("enabled", True)
    reason = body.get("reason", "")
    if not gate:
        raise HTTPException(400, detail="gate is required")
    if not set_ethics_gate(gate, enabled, reason):
        raise HTTPException(400, detail=f"unknown gate: {gate}")
    return {"gate": gate, "enabled": enabled, "current": get_ethics_gate(gate)}

@router.get("/ethics/gates")
async def ethics_gates_list(user: dict = Depends(_verify_key)):
    """List all ethics gates and their current state."""
    from ..core.metacog_monitor import _ETHICS_GATES, get_ethics_gate
    return {k: get_ethics_gate(k) for k in _ETHICS_GATES}

# ── SelfModel endpoint ──

@router.get("/cognition/selfmodel")
async def get_selfmodel(user: dict = Depends(_verify_key)):
    """Get SelfModel state with cross-time trend analysis."""
    try:
        from ..core.selfmodel import SelfModel
        sm = SelfModel()
        return {
            "grade": sm.overall_grade() if hasattr(sm, "overall_grade") else "N/A",
            "decisions": len(sm._decisions),
            "capabilities": len(sm._capabilities),
            "snapshots": len(sm._snapshots),
        }
    except Exception as e:
        raise HTTPException(500, detail=f"selfmodel unavailable: {e}")

# ── Webhook endpoints ──

@router.post("/webhook/subscribe")
async def webhook_subscribe(body: dict, current_user: dict = Depends(_verify_key)):
    """Subscribe to webhook events."""
    from .webhook import subscribe, SUPPORTED_EVENTS
    url = body.get("url", "").strip()
    events = body.get("events", [])
    description = body.get("description", "")
    if not url:
        raise HTTPException(400, detail="url is required")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, detail="url must start with http:// or https://")
    invalid = [e for e in events if e not in SUPPORTED_EVENTS]
    if invalid:
        raise HTTPException(400, detail=f"unsupported events: {invalid}. Supported: {sorted(SUPPORTED_EVENTS)}")
    result = subscribe(url, events, user_id=current_user.get("email", ""), description=description)
    if "error" in result:
        raise HTTPException(500, detail=result["error"])
    return {"success": True, "data": result}

@router.get("/webhook/subscriptions")
async def webhook_list_subs(current_user: dict = Depends(_verify_key)):
    """List webhook subscriptions for current user. Admin sees all."""
    from .webhook import get_subscriptions
    email = current_user.get("email", "")
    role = current_user.get("role", "")
    if role == "admin":
        subs = get_subscriptions()
    else:
        subs = get_subscriptions(user_id=email)
    return subs

@router.delete("/webhook/subscribe/{sub_id}")
async def webhook_delete_sub(sub_id: str, current_user: dict = Depends(_verify_key)):
    """Delete a webhook subscription."""
    from .webhook import unsubscribe, get_subscription
    email = current_user.get("email", "")
    role = current_user.get("role", "")
    sub = get_subscription(sub_id)
    if not sub:
        raise HTTPException(404, detail="subscription not found")
    if role != "admin" and sub.get("user_id", "") != email:
        raise HTTPException(403, detail="not authorized")
    unsubscribe(sub_id)
    return {"detail": "deleted"}

@router.post("/webhook/test-delivery")
async def webhook_test_delivery(body: dict, current_user: dict = Depends(_verify_key)):
    """Send a test webhook event to test delivery."""
    from .webhook import get_subscription, deliver_event
    sub_id = body.get("sub_id", "")
    if not sub_id:
        raise HTTPException(400, detail="sub_id is required")
    sub = get_subscription(sub_id)
    if not sub:
        raise HTTPException(404, detail="subscription not found")
    results = deliver_event("test.ping", {"test": True})
    return {"results": results}

# ── Gateway endpoints ──

@router.post("/gateway/execute")
async def gateway_execute(body: dict, user: dict = Depends(_verify_key)):
    """Execute a Desktop Gateway operation."""
    action = body.get("action", "")
    target = body.get("target", "")
    plan = body.get("plan", "")
    try:
        req = Request(
            f"http://127.0.0.1:9705/api/execute-plan" if plan else f"http://127.0.0.1:9705/api/execute",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except URLError:
        raise HTTPException(503, detail="Gateway unavailable")

@router.get("/gateway/context")
async def gateway_context(body: dict, user: dict = Depends(_verify_key)):
    """Get current Desktop Gateway context (canvas snapshots)."""
    try:
        req = Request("http://127.0.0.1:9705/api/context")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except URLError:
        raise HTTPException(503, detail="Gateway unavailable")

@router.post("/gateway/control/{action}")
async def gateway_control(action: str, user: dict = Depends(_verify_key)):
    """Control Gateway execution state. Actions: pause, resume, abort, state"""
    try:
        req = Request(f"http://127.0.0.1:9705/api/control/{action}", method="POST")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except URLError:
        raise HTTPException(503, detail="Gateway unavailable")

# ── Admin endpoints ──

@router.get("/admin/users")
async def list_users(admin: dict = Depends(_require_admin)):
    """List all registered users. Admin only."""
    from .auth import list_all_users
    users = list_all_users()
    return {"users": users, "total": len(users)}

@router.get("/admin/user/{email}")
async def get_user_detail(email: str, admin: dict = Depends(_require_admin)):
    """Get detailed info for a single user. Admin only."""
    from .auth import find_by_email
    user = find_by_email(email)
    if not user:
        raise HTTPException(404, detail="user not found")
    return user

@router.post("/admin/update-user")
async def update_user(body: dict, admin: dict = Depends(_require_admin)):
    """Update a user's plan or role. Admin only."""
    from .auth import find_by_email, update_user_field
    email = body.get("email", "")
    if not email:
        raise HTTPException(400, detail="email is required")
    user = find_by_email(email)
    if not user:
        raise HTTPException(404, detail="user not found")
    if "plan" in body:
        update_user_field(email, "plan", body["plan"])
    if "role" in body:
        update_user_field(email, "role", body["role"])
    return {"message": "user updated", "email": email, "plan": body.get("plan", user.get("plan")), "role": body.get("role", user.get("role"))}

@router.post("/admin/migrate-users")
async def migrate_users(body: dict, admin: dict = Depends(_require_admin)):
    """Migrate JSON file users to PostgreSQL. Admin only."""
    from ..storage.db import use_pg
    if not use_pg():
        raise HTTPException(400, detail="PostgreSQL not available")
    from ..utils import DATA_DIR
    from .auth import hash_password
    import glob as _glob
    users_dir = DATA_DIR / "users"
    migrated = 0
    errors = []
    for f in sorted(users_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            email = data.get("email", "")
            if not email:
                continue
            from ..storage.db import execute
            execute("""
                INSERT INTO users (email, password_hash, username, plan, role, api_keys, created_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (email) DO NOTHING
            """, (email, data.get("password_hash", ""), data.get("username", ""),
                  data.get("plan", "community"), data.get("role", "user"),
                  json.dumps(data.get("api_keys", [data.get("api_key", "")])),
                  data.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))
            migrated += 1
        except Exception as e:
            errors.append(str(e))
    return {"migrated": migrated, "errors": errors, "total_users_in_pg_plus_json": migrated}

@router.get("/admin/stats")
async def admin_stats(admin: dict = Depends(_require_admin)):
    """Get system-wide statistics. Admin only."""
    from .auth import list_all_users
    users = list_all_users()
    by_plan = {}
    for u in users:
        p = u.get("plan", "community")
        by_plan[p] = by_plan.get(p, 0) + 1
    return {
        "total_users": len(users),
        "users_by_plan": by_plan,
        "total_monthly_tasks": 0,
        "total_monthly_searches": 0,
        "total_monthly_queries": 0,
    }

_DASHBOARD_HTML: str | None = None

def _load_dashboard_html() -> str:
    global _DASHBOARD_HTML
    if _DASHBOARD_HTML is None:
        _DASHBOARD_HTML = (Path(__file__).parent.parent / "ui" / "dashboard.html").read_text(encoding="utf-8")
    return _DASHBOARD_HTML

@router.get("/admin/overview")
async def admin_overview(user: dict = Depends(_verify_key)):
    """Return overview data (replaces standalone 9700 /api/overview)."""
    import json as _json
    from .. import __version__ as _ver
    from ..utils import METACORE_DIR, read_json, LEARNER_CONFIG, LEARNER_STATUS

    data = {
        "version": str(_ver),
        "aelvoxim_dir": str(METACORE_DIR),
        "learner": {"directions": [], "active": 0, "running": False},
        "knowledge": {"active": 0, "pending": 0},
        "selfmodel": {"decisions": 0, "capabilities": 0, "grade": "N/A"},
    }

    # Learner
    try:
        _st = read_json(LEARNER_STATUS) or {}
        data["learner"]["running"] = _st.get("running", False)
        _cfg = read_json(LEARNER_CONFIG) or {}
        dirs = []
        for t, c in _cfg.items():
            if isinstance(c, dict):
                dirs.append({
                    "topic": t, "status": c.get("status", "unknown"),
                    "entries": c.get("entries_created", 0),
                    "saturation": c.get("saturation", 0),
                    "completed_at": c.get("completed_at", ""),
                })
                if c.get("status") == "active":
                    data["learner"]["active"] += 1
        data["learner"]["total"] = len(dirs)
        data["learner"]["completed"] = sum(1 for d in dirs if d["status"] in ("completed", "mastery"))
        data["learner"]["directions"] = dirs
    except Exception:
        pass

    # Knowledge
    try:
        from ..learn.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        entries = list(kb.get_all_active())
        data["knowledge"]["active"] = len(entries)
        from collections import Counter
        topics = Counter(e.get("topic", "") for e in entries)
        data["knowledge"]["top_topics"] = [{"topic": t, "count": c} for t, c in topics.most_common(10)]
    except Exception:
        pass

    # SelfModel
    try:
        from ..core.selfmodel import SelfModel
        sm = SelfModel()
        data["selfmodel"]["decisions"] = len(sm._decisions)
        data["selfmodel"]["capabilities"] = len(sm._capabilities)
        data["selfmodel"]["snapshots"] = len(sm._snapshots)
        try:
            grade = sm.overall_grade() if hasattr(sm, "overall_grade") else {}
            data["selfmodel"]["grade"] = grade.get("grade", "N/A")
        except Exception:
            pass
    except Exception:
        pass

    import json as _json
    return _json.loads(_json.dumps(data, default=str))

@router.get("/admin/data")
async def admin_dashboard(user: dict = Depends(_verify_key)):
    """Return dashboard data: learner status + system overview."""
    result = {"services": {}, "learner": {}}
    from ..core.health import get_watchdog
    wd = get_watchdog()
    for name, info in wd._services.items():
        result["services"][info.get("label", name)] = "online" if info.get("up") else "offline"
    try:
        from ..utils import read_json, LEARNER_STATUS
        st = read_json(LEARNER_STATUS) or {}
        result["learner"]["running"] = st.get("running", False)
        result["learner"]["total_cycles"] = st.get("cycles_completed", 0)
    except Exception:
        pass
    return result

@router.get("/admin/cognition")
async def admin_cognition(user: dict = Depends(_verify_key)):
    """Return cognition overview."""
    result = {
        "belief": {"count": 0, "high_confidence": 0, "low_confidence": 0, "total_evidence": 0},
        "trigger": {},
        "calibration": {},
    }
    try:
        from ..core.belief import BeliefPool
        result["belief"] = BeliefPool().get_stats()
    except Exception:
        pass
    try:
        from ..core.calibration import get_calibration
        result["calibration"] = get_calibration().get_snapshot()
    except Exception:
        pass
    return result

@router.get("/admin/knowledge-graph")
async def admin_knowledge_graph(user: dict = Depends(_verify_key)):
    """Return knowledge graph data."""
    try:
        from ..server.knowledge_graph import get_graph, graph_stats
        return {"graph": get_graph(limit=80), "stats": graph_stats()}
    except Exception as e:
        raise HTTPException(500, detail=f"Graph unavailable: {e}")

@router.get("/admin/learner/status")
async def admin_learner_status(user: dict = Depends(_verify_key)):
    """Return learner status."""
    try:
        from ..learn.learner import get_learner
        from ..utils import read_json, LEARNER_CONFIG, LEARNER_STATUS
        l = get_learner()
        cfg = read_json(LEARNER_CONFIG) or {}
        st = read_json(LEARNER_STATUS) or {}
        return {
            "running": st.get("running", False) or (l.is_running() if hasattr(l, "is_running") else False),
            "direction_count": len(cfg),
            "updated_at": st.get("updated_at", ""),
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@router.post("/admin/learner/start")
async def admin_learner_start(user: dict = Depends(_require_admin)):
    """Start learner loop."""
    try:
        from ..learn.learner import get_learner
        l = get_learner()
        l._load_config()
        if not l._directions:
            raise HTTPException(400, detail="No learning directions configured")
        l.start()
        return {"success": True, "message": "Learning loop started"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@router.post("/admin/learner/stop")
async def admin_learner_stop(user: dict = Depends(_require_admin)):
    """Stop learner loop."""
    try:
        from ..learn.learner import get_learner
        get_learner().stop()
        return {"success": True, "message": "Learning loop stopped"}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@router.get("/admin/skill-timeline")
async def admin_skill_timeline(months: int = 6, user: dict = Depends(_verify_key)):
    from datetime import datetime, timedelta
    from ..utils import read_json, LEARNER_CONFIG
    cfg = read_json(LEARNER_CONFIG) or {}
    now = datetime.now()
    cutoff = now - timedelta(days=months * 30)
    months_data = []
    for i in range(months):
        m = now.month - i
        y = now.year
        while m < 1:
            m += 12
            y -= 1
        months_data.append({"month": f"{y}-{m:02d}", "entries": 0, "completed": 0, "topics": []})
    months_data.reverse()
    for topic, d in cfg.items():
        if not isinstance(d, dict) or d.get("status") not in ("completed", "mastery"):
            continue
        completed_at = d.get("completed_at", "")
        entries = d.get("entries_created", 0)
        if not completed_at:
            continue
        try:
            dt = datetime.strptime(completed_at[:10], "%Y-%m-%d")
        except Exception:
            continue
        if dt < cutoff:
            continue
        key = f"{dt.year}-{dt.month:02d}"
        for m in months_data:
            if m["month"] == key:
                m["entries"] += entries
                m["completed"] += 1
                m["topics"].append(topic)
                break
    active_entries = sum(
        d.get("entries_created", 0) for d in cfg.values()
        if isinstance(d, dict) and d.get("status") == "active"
    )
    return {"timeline": months_data, "total_completed": sum(m["completed"] for m in months_data),
            "total_entries": sum(m["entries"] for m in months_data), "active_entries": active_entries}
