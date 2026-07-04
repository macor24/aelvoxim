"""
metacore.server.routes_memory — Memory read/write/search endpoints.

Routes:
    GET  /v1/memory/search   — Search memory entities
    GET  /v1/memory/{key}    — Read a specific memory entity
    POST /v1/memory          — Write a memory entity
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from .routes import _verify_key

router = APIRouter()


@router.get("/memory/search")
async def search_memory(
    q: str = Query("", description="Search query"),
    limit: int = Query(10, description="Max results"),
    user: dict = Depends(_verify_key),
):
    """Search memory entities by query string."""
    from ..api import memory_search
    results = memory_search(q, limit=limit)
    return {"results": results, "total": len(results)}


@router.get("/memory/{key}")
async def read_memory(
    key: str,
    user: dict = Depends(_verify_key),
):
    """Read a specific memory entity by key."""
    from ..api import memory_read
    value = memory_read(key)
    if value is None:
        raise HTTPException(404, detail="not found")
    return {"key": key, "value": value}


@router.post("/memory")
async def write_memory(
    request: dict,
    user: dict = Depends(_verify_key),
):
    """Write a memory entity. Body: {\"key\": \"...\", \"value\": \"...\"}"""
    from ..api import memory_store
    key = request.get("key", "")
    value = request.get("value", "")
    if not key or value is None:
        raise HTTPException(400, detail="key and value are required")
    memory_store(key, value)
    return {"status": "ok", "key": key}
