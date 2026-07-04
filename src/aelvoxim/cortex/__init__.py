"""
metacore.cortex — Brain cortex layer (merged from 9703 orchestrator).

Provides:
  - Planner management routes (create/list/next/delete plans)
  - Router + intent classification for chat_pipeline
  - Scheduler (background tick)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from .router import Router
from ..planner import LongTermPlanner

log = logging.getLogger("aelvoxim.cortex")

router = APIRouter(prefix="/cortex")

# Global planner instance (lazy-init)
_planner: Optional[LongTermPlanner] = None


def init_planner():
    global _planner
    if _planner is None:
        _planner = LongTermPlanner()


# ── Intent classification (used by chat_pipeline) ──


def classify_coarse(query: str) -> str:
    """Coarse intent: chat / reason / learn / system."""
    q = query.lower()
    if any(kw in q for kw in ["learn", "study", "research", "teach", "train"]):
        return "learn"
    if any(kw in q for kw in ["status", "health", "memory", "config", "settings"]):
        return "system"
    reason_keywords = [
        "if", "would", "should", "why", "what if",
        "analyze", "evaluate", "difference",
        "比较", "哪个", "应该", "为什么", "which", "better",
    ]
    reason_phrases = [
        "what if", "compare", "difference between",
        "why is", "why do", "why does", "why would",
        "应该怎么", "为什么这样", "哪个更好",
        "how does", "how would",
    ]
    if any(p in q for p in reason_phrases):
        return "reason"
    match_count = sum(1 for kw in reason_keywords if kw in q)
    if match_count >= 2:
        return "reason"
    return "chat"


def classify_fine(query: str) -> dict:
    """Fine-grained routing using Router + routing_rules.json."""
    _r = Router()
    return _r.classify(query)


# ── Build expert context (used by chat_pipeline) ──


def run_experts(query: str, user_id: str = "", session_id: str = "",
                expert_subset: Optional[list] = None) -> dict:
    """Run ExpertOrchestrator (optionally filtered to a subset of experts)."""
    from ..experts.base import ExpertInput
    from ..experts.orchestrator import ExpertOrchestrator

    inp = ExpertInput(
        query=query,
        user_id=user_id,
        session_id=session_id,
        context={},
    )
    orch = ExpertOrchestrator()
    return orch.think(inp, expert_filter=expert_subset)


def build_expert_context(expert_result: dict) -> str:
    """Build [Expert Analysis] context string from expert results."""
    if not expert_result.get("expert_results"):
        return ""
    lines = []
    for er in expert_result["expert_results"]:
        if er.error:
            lines.append(f"  [{er.expert_name}] unavailable")
        else:
            lines.append(f"  [{er.expert_name}] (confidence={er.confidence}) {er.opinion[:150]}")
    return "\n[Expert Analysis]\n" + "\n".join(lines) + "\n"


def check_topic_drift(first_msg: str, latest_reply: str) -> float:
    """Rough check: how much of the reply overlaps with the original topic.
    Returns 0.0 (on-topic) to 1.0 (completely drifted)."""
    if not first_msg or not latest_reply:
        return 0.0
    first_words = set(first_msg.lower().split())
    reply_words = set(latest_reply.lower().split())
    if not first_words or not reply_words:
        return 0.5
    skip = {"the","a","an","is","are","was","were","in","on","at","to","for","of",
            "and","or","but","i","you","he","she","it","we","they","this","that",
            "with","from","by","as","be","have","has","had","do","does","did",
            "will","would","can","could","may","might","about","how","what","when"}
    first_content = first_words - skip
    reply_content = reply_words - skip
    if not first_content or not reply_content:
        return 0.5
    overlap = len(first_content & reply_content)
    drift = 1.0 - (overlap / min(len(first_content), len(reply_content)) * 0.8)
    return max(0.0, min(1.0, drift))


def decide(expert_result: dict, first_msg: str = "", latest_reply: str = "") -> dict:
    """Make pipeline decisions based on expert results and topic drift.

    Returns:
        {
            "blocked": bool,
            "adjustments": {
                "tone": "normal" | "concise" | "warm",
                "max_tokens": null | int,
                "clarify": null | "contradiction",
                "recap": null | "topic",
                "drift_warning": null | str,
                "probe": null | str,  # from enhance_with_knowledge gap detection
            },
            "expert_notes": str,
        }
    """
    decision = {
        "blocked": False,
        "adjustments": {
            "tone": "normal",
            "max_tokens": None,
            "clarify": None,
            "recap": None,
            "drift_warning": None,
        },
        "expert_notes": "",
    }

    # 1. Check if blocked
    if expert_result.get("blocked") or expert_result.get("opinion", "").startswith("BLOCKED"):
        decision["blocked"] = True
        return decision

    # 2. Check topic drift
    drift = check_topic_drift(first_msg, latest_reply)
    if drift > 0.7:
        decision["adjustments"]["drift_warning"] = (
            "The current reply has drifted from the original topic. "
            "If appropriate, ask if the user wants to return to the original topic."
        )

    # 3. Parse expert results for adjustments
    expert_results = expert_result.get("expert_results", [])
    notes = []
    for er in expert_results:
        if er.error or er.skipped:
            continue
        notes.append(f"  [{er.expert_name}] (confidence={er.confidence}) {er.opinion[:150]}")
        # Emotion expert → tone
        if er.expert_name == "emotion":
            details = er.details or {}
            sentiment = details.get("sentiment", "neutral")
            if sentiment in ("negative", "angry"):
                decision["adjustments"]["tone"] = "concise"
            elif sentiment in ("sad", "anxious"):
                decision["adjustments"]["tone"] = "warm"
            if details.get("tone_suggestion"):
                pass  # tone already set above
        # Logic expert → clarify
        if er.expert_name == "logic":
            conflicts = (er.details or {}).get("conflicts", [])
            if conflicts:
                decision["adjustments"]["clarify"] = "contradiction"
        # Memory expert → recap
        if er.expert_name == "memory":
            entities = (er.details or {}).get("entities", [])
            if entities:
                decision["adjustments"]["recap"] = entities[0].get("key", "")

    if notes:
        decision["expert_notes"] = "\n[Expert Analysis]\n" + "\n".join(notes) + "\n"

    return decision


# ── Planner routes ──


@router.post("/planner/create")
async def planner_create(body: dict):
    init_planner()
    global _planner
    goal = body.get("goal", "").strip()
    if not goal:
        raise HTTPException(400, detail="goal is required")
    plan = _planner.create_plan(goal, source=body.get("source", "user"))
    return {"status": "created", "plan": plan.to_dict()}


@router.get("/planner/list")
async def planner_list():
    init_planner()
    return {"plans": _planner.list_plans()}


@router.get("/planner/next")
async def planner_next():
    init_planner()
    return {"action": _planner.next_action()}


@router.delete("/planner/{plan_id}")
async def planner_delete(plan_id: str):
    init_planner()
    ok = _planner.delete_plan(plan_id)
    if not ok:
        raise HTTPException(404, detail="Plan not found")
    return {"status": "deleted"}
