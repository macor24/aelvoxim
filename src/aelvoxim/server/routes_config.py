"""
metacore.server.routes_config — System and LLM configuration endpoints.

Routes:
    GET  /v1/config              — List all config
    GET  /v1/config/{key}        — Get a config value
    POST /v1/config              — Set a config value
    GET  /v1/llm/config          — Get LLM config
    POST /v1/llm/config          — Set LLM config
    # SentriKit routes have been removed (project is independent)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from .routes import _verify_key

router = APIRouter()


@router.get("/config")
async def list_all_config(user: dict = Depends(_verify_key)):
    """List all system configuration keys."""
    from ..api import list_config
    return {"config": list_config()}


@router.get("/config/{key}")
async def get_config_value(key: str, user: dict = Depends(_verify_key)):
    """Get a specific configuration value."""
    from ..api import get_config
    value = get_config(key)
    if value is None:
        raise HTTPException(404, detail="config key not found")
    return {"key": key, "value": value}


@router.post("/config")
async def set_config_value(
    body: dict,
    user: dict = Depends(_verify_key),
):
    """Set a configuration value. Body: {\"key\": \"...\", \"value\": \"...\"}"""
    key = body.get("key", "")
    value = body.get("value", "")
    if not key:
        raise HTTPException(400, detail="key is required")
    from ..api import set_config
    set_config(key, value)
    return {"status": "ok"}


@router.get("/llm/config")
async def get_llm_config(user: dict = Depends(_verify_key)):
    """Get current LLM configuration."""
    from ..utils import read_json, LLM_CONFIG_FILE
    return read_json(LLM_CONFIG_FILE) or {}


@router.post("/llm/config")
async def set_llm_config(body: dict, user: dict = Depends(_verify_key)):
    """Set LLM configuration."""
    from ..client.security_gate import check_config_change
    result = check_config_change(body)
    if not result.get("allowed", True):
        raise HTTPException(403, detail=result.get("reason", "Blocked by safety rules"))
    from ..utils import write_json, LLM_CONFIG_FILE
    write_json(LLM_CONFIG_FILE, body)
    return {"status": "ok"}


# SentriKit routes (sentrikit/config, sentrikit/key, sentrikit/test) removed.
# SentriKit is an independent project; Aelvoxim no longer calls it.
