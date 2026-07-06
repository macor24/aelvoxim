"""
metacore.server.routes — Shared utilities and auth middleware for MetaCore API.

This file provides:
    - _mask_api_key()         — Mask API keys in error messages
    - _safety_response()      — Raise user-friendly safety blocks
    - _verify_key()           — Auth dependency for protected routes
    - public_router           — Public endpoints (no auth required)

Sub-routers are imported directly in __init__.py.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query

from ..api import submit_task, get_task_status, memory_store, memory_read, memory_search, memory_timeline
from ..api import get_config, set_config, list_config
from .auth import find_user, check_quota, increment_usage, PLANS, create_user, _all_users, ADMIN_KEY

_log = logging.getLogger("aelvoxim.routes")
_API_KEY_PATTERN = re.compile(r"sk-[a-zA-Z0-9.*]{3,}")

router = APIRouter(prefix="/v1")
public_router = APIRouter()


def _mask_api_key(text: str) -> str:
    """Mask API key patterns in error messages."""
    return _API_KEY_PATTERN.sub("sk-***", text)


def _safety_response(
    reason: str = "",
    scene: str = "chat",
    user_id: str = "",
    result: Optional[dict] = None,
) -> None:
    """Raise a user-friendly safety block response with graded guidance."""
    from ..client.security_gate import (
        get_user_friendly_response as _gf,
        is_overridden as _is_ovr,
    )

    if result:
        reason = result.get("reason", reason)
        friendly = result.get("_friendly")
        if not friendly:
            friendly = _gf(reason, scene=scene)
        bypass_key = friendly.get("bypass_key", "")
        if bypass_key and _is_ovr(bypass_key):
            return
        detail = json.dumps({
            "code": "safety_block",
            "risk": friendly.get("risk", "medium"),
            "scene": scene,
            "message": friendly.get("message", "This operation was blocked by safety rules."),
            "reason": reason,
            "suggestions": friendly.get("suggestions", []),
            "bypass_key": bypass_key or "",
        })
    else:
        detail = json.dumps({
            "code": "safety_block",
            "risk": "medium",
            "scene": scene,
            "message": "This operation was blocked by safety rules.",
            "reason": reason or "unknown",
            "suggestions": [
                'If this is a false positive, reply "false positive"',
                'If you need to proceed, reply "I accept the risk"',
            ],
            "bypass_key": "",
        })
    raise HTTPException(403, detail=detail)


# ── Dependency: API Key auth ──


async def _verify_key(authorization: str = Header(None)) -> dict:
    """Extract and verify API Key from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="missing or invalid Authorization header")
    api_key = authorization[7:]
    # Rate limit by API key suffix (last 8 chars)
    from .ratelimit import api_limiter
    allowed, retry_after = api_limiter.check(api_key[-8:])
    if not allowed:
        raise HTTPException(429, detail=f"Rate limit exceeded. Retry after {retry_after}s")
    user = find_user(api_key)
    if not user:
        from .audit import log as _audit_log
        _audit_log("auth.failure", user=api_key[-8:], status="failure", detail={"reason": "unknown api key"})
        raise HTTPException(401, detail="unknown API key")
    ok, reason = check_quota(user)
    if not ok:
        raise HTTPException(429, detail=reason)
    return user


async def _require_admin(user: dict = Depends(_verify_key)):
    """Require admin role. Must follow _verify_key dependency."""
    if user.get("role") != "admin":
        raise HTTPException(403, detail="Admin access required")
    return user


# ── Public endpoints (no auth required) ──


@public_router.get("/public/sentrikit/status")
async def public_sentrikit_status():
    """Get SentriKit connection status (no auth required)."""
    from ..client.sentrikit import get_configured_host, is_available
    return {"host": get_configured_host(), "available": is_available()}


@public_router.post("/public/sentrikit/config")
async def public_set_sentrikit_config(body: dict, _user: dict = Depends(_verify_key)):
    """Set SentriKit host URL (requires auth)."""
    host = body.get("host", "").strip()
    if not host:
        raise HTTPException(400, detail="host is required")
    from ..client.sentrikit import set_host, is_available
    set_host(host)
    return {"host": host, "available": is_available()}


@public_router.post("/public/sentrikit/key")
async def public_set_sentrikit_key(body: dict, _user: dict = Depends(_verify_key)):
    """Set SentriKit API key (requires auth)."""
    api_key = body.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(400, detail="api_key is required")
    from ..client.sentrikit import set_api_key
    set_api_key(api_key)
    return {"status": "ok", "message": "SentriKit API key saved"}


@public_router.post("/public/sentrikit/test")
async def public_test_sentrikit(body: dict, _user: dict = Depends(_verify_key)):
    """Test SentriKit connection (requires auth)."""
    from ..client.sentrikit import test_connection
    result = test_connection(
        host=body.get("host", ""),
        api_key=body.get("api_key", ""),
    )
    return result


@public_router.post("/public/llm/test")
async def public_test_llm(body: dict, _user: dict = Depends(_verify_key)):
    """Test LLM connection with provided API key (requires auth)."""
    import urllib.request, json
    api_key = body.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(400, detail="api_key is required")
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 10,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        return {
            "status": "ok",
            "response": result.get("choices", [{}])[0].get("message", {}).get("content", ""),
        }
