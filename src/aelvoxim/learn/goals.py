"""aelvoxim.learn.goals — Active goal system (motivation layer)

Split from learner.py (1969-line monolith).
Responsibility: web search for current info, goal setting, goal progress tracking.
"""

from __future__ import annotations

import time as _t
from datetime import datetime
from typing import Any, Dict, List, Optional


def search_and_learn(directions: dict, log_func, search_rr: int = 0) -> int:
    """Search the web for current info on active learning directions and store to KB.

    Picks one active direction per cycle via round-robin.
    Returns the updated round-robin index.
    """
    from ..learn.search import search as _web_search
    from ..learn.knowledge import KnowledgeBase

    now = _t.time()
    active = [
        (name, d) for name, d in directions.items()
        if d.status == "active"
        and (now - getattr(d, '_last_search_cycle', 0)) > 300
    ]
    if not active:
        return search_rr

    idx = search_rr % len(active)
    name, direction = active[idx]
    next_rr = idx + 1

    try:
        results = _web_search(direction.topic[:100], max_results=5)
    except Exception:
        direction._last_search_cycle = now
        return next_rr
    if not results:
        direction._last_search_cycle = now
        return next_rr

    stored = 0
    for r in results[:3]:
        title = (r.get("title") or "")[:80]
        snippet = (r.get("snippet") or "")[:200]
        url = (r.get("url") or "")[:200]
        if not title.strip() or not snippet.strip():
            continue
        existing = KnowledgeBase.search(query=title[:30], min_confidence=0.3, limit=1)
        if existing:
            continue
        KnowledgeBase.store(
            kid=f"search:{int(now)}:{hash(title) & 0xFFFFFF:06x}",
            title=title,
            content=snippet,
            summary=snippet[:120],
            source="web_search",
            confidence=0.3,
        )
        stored += 1

    direction._last_search_cycle = now
    if stored:
        log_func(f"  🔍 Searched '{direction.topic}' → stored {stored} entries")
    return next_rr


def set_active_goals(existing_goals: list, log_func) -> list:
    """Set 1-3 active improvement goals based on SelfModel.

    Rules:
    - belief_health < 0.5 → goal: raise to 0.6
    - knowledge quality < 0.6 → goal: raise to 0.7
    - success_rate < 0.5 → goal: raise to 0.6
    - Max 3 active goals at once
    """
    try:
        from ..core.selfmodel import SelfModel, CapabilityScore, Goal

        sm = SelfModel()
        caps = sm._capabilities
        active = [g for g in existing_goals if g.status == "active"]

        if len(active) >= 3:
            return existing_goals

        now = datetime.now().isoformat()
        new_goals = []

        # Belief health goal
        bc = caps.get("belief_health")
        if bc and bc.success_rate < 0.5:
            if not any(g.category == "belief_health" for g in active):
                _focus = "cleanup"
                _skip = []
                if bc.success_rate < 0.3:
                    _skip = ["auto_tune"]
                new_goals.append(Goal(
                    id=f"goal:{hash('belief_health') & 0xFFFFFF:06x}",
                    description=f"Raise belief health from {bc.success_rate:.0%} to 60%",
                    category="belief_health",
                    target_value=0.6,
                    current_value=bc.success_rate,
                    created_at=now,
                    focus=_focus,
                    skip_actions=_skip,
                ))

        # Knowledge quality goal
        kq = caps.get("knowledge_quality")
        if kq and kq.success_rate < 0.6:
            if not any(g.category == "knowledge_quality" for g in active):
                _focus = "validate"
                _skip = []
                if kq.success_rate < 0.4:
                    _skip = ["search"]
                new_goals.append(Goal(
                    id=f"goal:{hash('knowledge_quality') & 0xFFFFFF:06x}",
                    description=f"Raise knowledge quality from {kq.success_rate:.0%} to 70%",
                    category="knowledge_quality",
                    target_value=0.7,
                    current_value=kq.success_rate,
                    created_at=now,
                    focus=_focus,
                    skip_actions=_skip,
                ))

        # Success rate goal
        learn = caps.get("learning")
        if learn and learn.success_rate < 0.5:
            if not any(g.category == "success_rate" for g in active):
                new_goals.append(Goal(
                    id=f"goal:{hash('success_rate') & 0xFFFFFF:06x}",
                    description=f"Raise success rate from {learn.success_rate:.0%} to 60%",
                    category="success_rate",
                    target_value=0.6,
                    current_value=learn.success_rate,
                    created_at=now,
                    focus="search",
                ))

        for g in new_goals:
            existing_goals.append(g)
            log_func(f"  🎯 New goal: {g.description}")
        return existing_goals
    except Exception:
        return existing_goals


# ── Focus strategy cycle ──
# Each time a focus has run for enough cycles without progress, rotate to next
_FOCUS_CYCLE = {
    "belief_health":     ["cleanup", "validate", "search", "balanced"],
    "knowledge_quality":  ["validate", "cleanup", "balanced"],
    "success_rate":       ["search", "validate", "balanced"],
}
_FOCUS_ROTATE_AFTER = 20  # cycles (~5 minutes at 15s per tick)


def progress_goals(existing_goals: list, log_func) -> list:
    """Check all active goals for progress each cognition cycle.

    Updates current_value from SelfModel.
    If a goal's current focus has run enough cycles without hitting target,
    rotates to the next focus in _FOCUS_CYCLE.
    """
    try:
        from ..core.selfmodel import SelfModel, Goal

        existing = existing_goals
        if not existing:
            return existing

        sm = SelfModel()
        caps = sm._capabilities
        now = datetime.now().isoformat()

        for g in existing:
            if g.status != "active":
                continue

            if g.category == "belief_health":
                bc = caps.get("belief_health")
                if bc:
                    g.current_value = bc.success_rate
            elif g.category == "knowledge_quality":
                kq = caps.get("knowledge_quality")
                if kq:
                    g.current_value = kq.success_rate
            elif g.category == "success_rate":
                learn = caps.get("learning")
                if learn:
                    g.current_value = learn.success_rate

            if g.current_value >= g.target_value:
                g.status = "completed"
                g.completed_at = now
                log_func(f"  ✅ Goal completed: {g.description}")
                continue

            if g.current_value < g.target_value * 0.3:
                g.status = "failed"
                g.completed_at = now
                g.progress_note = f"Stalled at {g.current_value:.0%}"
                log_func(f"  ❌ Goal failed: {g.description} (stalled at {g.current_value:.0%})")
                continue

            # Focus rotation: if current focus has run enough cycles, try next
            g._focus_cycles += 1
            if g.category in _FOCUS_CYCLE:
                cycle = _FOCUS_CYCLE[g.category]
                if g.focus not in cycle:
                    g.focus = cycle[0]
                    g._focus_cycles = 0
                elif g._focus_cycles >= _FOCUS_ROTATE_AFTER:
                    current_idx = cycle.index(g.focus)
                    next_idx = (current_idx + 1) % len(cycle)
                    g.focus = cycle[next_idx]
                    g._focus_cycles = 0
                    log_func(f"  🔄 Goal '{g.category}' focus: {cycle[current_idx]} → {cycle[next_idx]} (value={g.current_value:.0%})")

        return existing
    except Exception:
        return existing_goals
