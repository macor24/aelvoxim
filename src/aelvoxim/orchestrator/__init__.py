"""
metacore.orchestrator — Brain routing layer.

Determines what to do with each incoming request:
- Simple chat → forward to /v1/llm/chat (existing routes.py)
- Complex reasoning → dispatch to ExpertOrchestrator
- Learning task → forward to Learner
- System query → direct response

This layer does NOT modify routes.py. It provides additional
endpoints that call the same underlying modules.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, Optional

from ..experts.base import ExpertInput
from ..experts.orchestrator import ExpertOrchestrator

router = APIRouter(prefix="/v1/brain")
_orch = ExpertOrchestrator()


@router.post("/think")
async def brain_think(request: dict):
    """Run the expert orchestrator for complex reasoning.

    Request:
        {"query": "...", "context": {...}, "mode": "full"|"fast"}

    Response:
        {"opinion": "...", "confidence": 0.x, "blocked": bool, ...}
    """
    query = request.get("query", "")
    if not query:
        raise HTTPException(400, detail="query is required")

    inp = ExpertInput(
        query=query,
        context=request.get("context", {}),
        user_id=request.get("user_id", ""),
        session_id=request.get("session_id", ""),
    )

    mode = request.get("mode", "full")
    try:
        if mode == "fast":
            result = _orch.think_fast(inp)
        else:
            result = _orch.think(inp)

        if result.get("blocked"):
            raise HTTPException(403, detail=result.get("opinion", "Blocked by ethics expert"))

        return result
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger("aelvoxim.orchestrator").exception("brain_think failed")
        raise HTTPException(500, detail="Brain processing failed")


@router.get("/experts")
async def list_experts():
    """List all available experts with their status."""
    return {
        "experts": [
            {"name": "memory", "description": "Memory retrieval and confidence scoring"},
            {"name": "logic", "description": "Conflict detection and reasoning"},
            {"name": "ethics", "description": "Safety and ethical evaluation"},
            {"name": "emotion", "description": "Sentiment analysis and tone suggestion"},
            {"name": "creative", "description": "Alternative scenarios and idea generation"},
        ],
        "mode": "full (5 experts) / fast (memory + logic + ethics)",
    }


@router.post("/classify")
async def classify_intent(request: dict):
    """Classify an input to determine which path it should take.

    Returns one of: 'chat', 'reason', 'learn', 'system'
    """
    query = (request.get("query", "") or "").lower()

    # Learning-related
    if any(kw in query for kw in ["learn", "study", "research", "teach", "train", "direction"]):
        return {"intent": "learn", "target": "learner"}

    # System-related
    if any(kw in query for kw in ["status", "health", "memory", "config", "settings"]):
        return {"intent": "system", "target": "memory_direct"}

    # Complex reasoning
    if any(kw in query for kw in ["if", "would", "should", "compare", "why",
                                   "what if", "best", "worst", "analyze",
                                   "如果", "比较", "哪个", "应该"]):
        return {"intent": "reason", "target": "expert_orchestrator"}

    # Default: simple chat
    return {"intent": "chat", "target": "llm_chat"}
