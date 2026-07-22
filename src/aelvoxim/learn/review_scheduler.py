"""aelvoxim.learn.review_scheduler — Forgetting curve + spaced repetition review scheduler.

Each knowledge entry has:
  - next_review_at: ISO timestamp of next scheduled review
  - review_interval_days: current interval length (starts at 1)
  - review_count: total reviews completed
  - last_review_result: "pass" | "fail" | None

Interval progression (Ebbinghaus-like, adaptive):
  pass:  1 → 3 → 7 → 15 → 30 → 90 (max 90)
  fail:  reset to 1

Called from cognition_tick every ~5 minutes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

import logging
_log = logging.getLogger("aelvoxim.learn.review_scheduler")

# ── Config ──

REVIEW_INTERVALS = [1, 3, 7, 15, 30, 90]
_MAX_INTERVAL_DAYS = 90
_REVIEW_FILE = METACORE_DIR / "learner" / "review_schedule.json"


def _ensure_file() -> None:
    _REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _REVIEW_FILE.exists():
        _REVIEW_FILE.write_text(json.dumps({}))


def _load_schedule() -> Dict[str, Dict[str, Any]]:
    _ensure_file()
    try:
        return json.loads(_REVIEW_FILE.read_text())
    except Exception:
        return {}


def _save_schedule(schedule: Dict[str, Dict[str, Any]]) -> None:
    _ensure_file()
    tmp = _REVIEW_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(schedule, ensure_ascii=False, indent=2))
    tmp.replace(_REVIEW_FILE)


def register_entry(entry_id: str, initial_interval: int = 1) -> None:
    """Register a knowledge entry for scheduled review."""
    schedule = _load_schedule()
    if entry_id not in schedule:
        schedule[entry_id] = {
            "next_review_at": (datetime.now() + timedelta(days=initial_interval)).isoformat(),
            "review_interval_days": initial_interval,
            "review_count": 0,
            "last_review_result": None,
        }
        _save_schedule(schedule)


def record_review(entry_id: str, passed: bool) -> None:
    """Record a review result and compute next interval."""
    schedule = _load_schedule()
    entry = schedule.get(entry_id)
    if not entry:
        return

    entry["review_count"] = entry.get("review_count", 0) + 1
    entry["last_review_result"] = "pass" if passed else "fail"

    current = entry.get("review_interval_days", 1)
    if passed:
        # Move to next interval in the progression
        try:
            idx = REVIEW_INTERVALS.index(current)
            next_interval = REVIEW_INTERVALS[min(idx + 1, len(REVIEW_INTERVALS) - 1)]
        except ValueError:
            next_interval = min(current * 2, _MAX_INTERVAL_DAYS)
    else:
        # Reset to 1 day
        next_interval = 1

    entry["review_interval_days"] = next_interval
    entry["next_review_at"] = (datetime.now() + timedelta(days=next_interval)).isoformat()
    _save_schedule(schedule)


def get_due_entries(max_count: int = 10) -> List[Dict[str, Any]]:
    """Get knowledge entries whose review is due."""
    schedule = _load_schedule()
    now = datetime.now()
    due = []

    for entry_id, info in schedule.items():
        next_str = info.get("next_review_at", "")
        if not next_str:
            continue
        try:
            next_time = datetime.fromisoformat(next_str)
        except Exception:
            continue
        if now >= next_time:
            due.append({
                "entry_id": entry_id,
                "overdue_days": (now - next_time).days,
                "interval_days": info.get("review_interval_days", 1),
                "review_count": info.get("review_count", 0),
            })

    due.sort(key=lambda x: -x["overdue_days"])
    return due[:max_count]


def get_stats() -> Dict[str, Any]:
    """Return review scheduler statistics."""
    schedule = _load_schedule()
    if not schedule:
        return {"total": 0, "due": 0, "avg_interval": 0}

    now = datetime.now()
    total = len(schedule)
    due = 0
    total_interval = 0

    for info in schedule.values():
        total_interval += info.get("review_interval_days", 1)
        try:
            if datetime.fromisoformat(info.get("next_review_at", "")) <= now:
                due += 1
        except Exception:
            _log.exception("review_scheduler error")

    return {
        "total": total,
        "due": due,
        "avg_interval": round(total_interval / max(total, 1), 1),
    }


def run_review_cycle(log_func=None) -> Dict[str, Any]:
    """Run one review cycle: check due entries and re-verify via AutoValidator."""
    log = log_func or (lambda msg: None)
    due = get_due_entries(max_count=5)
    if not due:
        return {"checked": 0, "passed": 0, "failed": 0}

    checked = 0
    passed = 0
    failed = 0
    for item in due:
        try:
            from ..learn.knowledge import KnowledgeBase
            entries = list(KnowledgeBase.search(query=item["entry_id"], limit=1))
            if entries:
                from ..learn.validator import AutoValidator
                result = AutoValidator().verify(entries[0])
                ok = result.get("verified", False) and result.get("combined_score", 0) >= 0.5
                record_review(item["entry_id"], ok)
                if ok:
                    passed += 1
                else:
                    failed += 1
                    log(f"  📖 [Review] Failed: {item['entry_id']} (score={result.get('combined_score', 0):.2f})")
                checked += 1
            else:
                # Entry no longer exists — remove from schedule
                schedule = _load_schedule()
                schedule.pop(item["entry_id"], None)
                _save_schedule(schedule)
                log(f"  📖 [Review] Removed stale: {item['entry_id']}")
        except Exception as e:
            log(f"  ⚠️ [Review] Error for {item['entry_id']}: {e}")

    log(f"  📖 [Review] Cycle: {checked} checked, {passed} passed, {failed} failed")
    return {"checked": checked, "passed": passed, "failed": failed}
