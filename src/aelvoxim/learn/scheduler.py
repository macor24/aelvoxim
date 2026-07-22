"""aelvoxim.learn.scheduler — Spaced repetition review + pending promotion system

Split from learner.py (1969-line monolith).
Responsibility: schedule reviews (spaced repetition), pending entry promotion (L1/L2/L3).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


import logging
_log = logging.getLogger("aelvoxim.learn.scheduler")

def submit_verification_task(
    topic: str,
    is_review: bool,
    directions: dict,
    save_config_fn: Callable,
    log_func: Callable,
) -> None:
    """Submit verification task for a completed direction.

    If `is_review`, also schedules next review using exponential intervals.
    """
    try:
        from ..learn.knowledge import KnowledgeBase

        entries = KnowledgeBase.search(query=topic, limit=3)
        if not entries:
            if is_review:
                direction = directions.get(topic)
                if direction:
                    rh = json.loads(direction.review_history or "[]") if direction.review_history else []
                    intervals = [1, 3, 7, 30, 90, 180]
                    next_idx = min(len(rh), len(intervals) - 1)
                    next_days = intervals[next_idx]
                    next_time = datetime.now() + timedelta(days=next_days)
                    rh.append(next_time.strftime("%Y-%m-%d %H:%M:%S"))
                    direction.review_history = json.dumps(rh)
                    direction.status = "completed"
                    save_config_fn()
                    log_func(f"  📅 [{topic}] No knowledge, next review in {next_days} day(s)")
            else:
                log_func(f"  ⏭️ [{topic}] No knowledge, skip verification")
            return

        # Record to SelfModel
        try:
            from ..core.selfmodel import SelfModel, DecisionEntry
            sm = SelfModel()
            sm.record_decision(DecisionEntry(
                decision_type="knowledge_verify",
                task=f"{'Review' if is_review else 'Verify'} topic: {topic}",
                outcome="submitted",
            ))
        except Exception:
            _log.exception("scheduler error")

        # Schedule review if is_review
        if is_review:
            direction = directions.get(topic)
            if direction:
                rh = json.loads(direction.review_history or "[]") if direction.review_history else []
                intervals = [1, 3, 7, 30]
                next_idx = min(len(rh), len(intervals) - 1)
                next_days = intervals[next_idx]
                next_time = datetime.now() + timedelta(days=next_days)
                rh.append(next_time.strftime("%Y-%m-%d %H:%M:%S"))
                direction.review_history = json.dumps(rh)
                direction.status = "completed"
                save_config_fn()
                log_func(f"  📅 [{topic}] Next review in {next_days} day(s)")
    except Exception:
        _log.exception("scheduler error")


def schedule_review(topic: str, directions: dict, save_config_fn: Callable, log_func: Callable) -> None:
    """Schedule first review for a newly completed direction."""
    direction = directions.get(topic)
    if not direction:
        return
    rh = json.loads(direction.review_history or "[]") if direction.review_history else []
    if rh:
        return
    next_time = datetime.now() + timedelta(days=1)
    rh.append(next_time.strftime("%Y-%m-%d %H:%M:%S"))
    direction.review_history = json.dumps(rh)
    save_config_fn()
    log_func(f"  📅 [{topic}] Review scheduled in 1 day")


def check_reviews(directions: dict, save_config_fn: Callable, log_func: Callable) -> bool:
    """Check and execute any due reviews. Returns True if a review was triggered."""
    now = datetime.now()
    triggered = False
    for topic, direction in list(directions.items()):
        if direction.status not in ("completed", "mastery"):
            continue
        rh = json.loads(direction.review_history or "[]") if direction.review_history else []
        if not rh:
            continue
        next_review = rh[-1]
        try:
            review_time = datetime.strptime(next_review, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if now >= review_time:
            log_func(f"  🔄 [{topic}] Review due, running periodic_review")
            try:
                from ..learn.knowledge import periodic_review as _pr
                _pr_result = _pr()
                log_func(f"  📊 [{topic}] periodic_review: {_pr_result.get('reviewed',0)} reviewed, "
                         f"{_pr_result.get('flagged',0)} flagged, {_pr_result.get('downgraded',0)} downgraded")
            except Exception:
                log_func(f"  ⚠️ [{topic}] periodic_review failed")
            submit_verification_task(topic, is_review=True, directions=directions,
                                     save_config_fn=save_config_fn, log_func=log_func)
            triggered = True

    # ── Memory review queue: check semantic layer for low-strength entries ──
    try:
        from ..memory import _fusion as _mem_fusion
        from ..memory.entry import LAYER_SEMANTIC
        _sem = _mem_fusion.get_layer(LAYER_SEMANTIC)
        _queue = []
        for _entry in list(_sem._entries.values()):
            if _entry.strength < 0.3 and _entry.access_count >= 1:
                _queue.append({
                    "key": _entry.key,
                    "value": str(_entry.value)[:200],
                    "strength": round(_entry.strength, 2),
                    "last_access": _entry.last_access,
                })
        if _queue:
            from ..utils import METACORE_DIR
            _qf = Path(METACORE_DIR) / "review_queue.json"
            _qf.write_text(json.dumps({
                "timestamp": now.isoformat(),
                "entries": sorted(_queue, key=lambda x: x["strength"])[:20],
            }, ensure_ascii=False, indent=2))
            log_func(f"  🧠 Memory review queue: {len(_queue)} low-strength semantic entries")
    except Exception:
        _log.exception("scheduler error")

    return triggered


def check_pending_promotions(
    directions: dict,
    save_config_fn: Callable,
    log_func: Callable,
    state: dict,
) -> bool:
    """Check pending entries and record practice verifications with L1/L2/L3 protection.

    Each check = 1 practice verification for a pending entry.
    5 successful practices → auto promote to active.
    10 failed practices → auto discard.

    L1: max 20 practice attempts
    L2: stale result detection
    L3: streak detection (same eid 10+ times)
    """
    from ..learn.knowledge import KnowledgeBase

    try:
        pending = KnowledgeBase.get_pending()
        if not pending:
            return False
        entry = pending[0]
        if not isinstance(entry, dict):
            return False
        eid = entry.get("id")
        title = entry.get("title", "?")
        practice_count = entry.get('_practice_count', 0)
        fail_count = entry.get('_failed_count', 0)

        # L1: Max 20 attempts
        if practice_count >= 20:
            KnowledgeBase.discard_pending(eid)
            log_func(f"  🗑️ [Pending] Max 20 attempts reached, discard: {title}")
            return True

        # L3: Streak detection
        streak = state.setdefault("_pending_streak", 0)
        last_eid = state.setdefault("_last_pending_eid", "")
        if eid == last_eid:
            streak += 1
        else:
            streak = 0
        state["_last_pending_eid"] = eid
        state["_pending_streak"] = streak

        if streak >= 10:
            KnowledgeBase.discard_pending(eid)
            log_func(f"  🗑️ [Pending] Streak ({streak}) same eid, discard: {title}")
            return True

        log_func(f"  🔄 [Pending] Practice verify: {title} (practice #{practice_count})")

        if fail_count >= 10:
            KnowledgeBase.discard_pending(eid)
            log_func(f"  🗑️ [Pending] Auto-discarded after {fail_count} fails: {title}")
            return True

        success = llm_verify_practice(entry, log_func)
        result = KnowledgeBase.practice_verify(eid, success)

        # L2: Stale result detection
        r_pc = result.get('practice_count', 0)
        r_fc = result.get('failed_count', 0)
        if r_pc == 0 and r_fc == 0:
            KnowledgeBase.discard_pending(eid)
            log_func(f"  🗑️ [Pending] Stale result (0/0), discard: {title}")
            return True

        status = "✅" if success else "❌"
        log_func(f"  {status} [Pending] Practice result for '{title}': "
                 f"{r_pc}/5 (failed={r_fc})")
        return True
    except AttributeError as _ae:
        log_func(f"  ⚠️ [Pending] Method missing: {_ae}")
        return False
    except Exception as _e:
        log_func(f"  ❌ [Pending] Unexpected error: {_e}")
        import traceback as _tb
        log_func(f"     Traceback: {_tb.format_exc()}")
        return False


def llm_verify_practice(entry: dict, log_func: Callable) -> bool:
    """Use LLM to verify if a pending knowledge entry holds true in practice."""
    from ..utils import read_json, LLM_CONFIG_FILE

    try:
        from .llm import call_llm, ModelConfig
        config = read_json(LLM_CONFIG_FILE) or {}
        models = config.get("models", [])
        if not models:
            return True
        first = models[0]
        if not first.get("api_key", "") or len(first.get("api_key", "")) < 8:
            return True
        # Convert dict to ModelConfig so call_llm can access .temperature etc.
        model = ModelConfig(**first)
        prompt = (
            f"You are a knowledge validator. Judge whether the following knowledge claim is factually correct "
            f"and practically useful. Reply with ONLY 'YES' or 'NO'.\n\n"
            f"Knowledge claim:\n{entry.get('content', '')[:500]}"
        )
        text = call_llm(
            model=model,
            user_message=prompt,
            system_prompt="You are a knowledge validator.",
            max_tokens=10,
        )
        if text and text.strip().upper().startswith("YES"):
            return True
        return False
    except Exception as _e:
        log_func(f"  ⚠️ [LLM Verify] Error: {_e}")
        return True
