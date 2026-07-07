# -*- coding: utf-8 -*-
"""
metacore.hooks.tracker — Tuning effectiveness tracker for data feedback loop

Tracks the effect of auto-tune changes by monitoring the next N outcomes.
If success rate improves, solidifies the changes.
If success rate degrades, trigger rollback.

Zero dependencies, pure stdlib.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

_TRACKER_FILE = METACORE_DIR / "hooks" / "tracker.json"
_OUTCOME_FILE = METACORE_DIR / "hooks" / "outcomes.jsonl"

# Default tracking window
_DEFAULT_WINDOW = 10


# ── Data helpers ──────────────────────────


def _load() -> Dict:
    if not _TRACKER_FILE.exists():
        return {"active_trackers": [], "history": []}
    try:
        return json.loads(_TRACKER_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"active_trackers": [], "history": []}


def _save(data: Dict):
    _TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TRACKER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _read_recent_outcomes(n: int) -> List[Dict]:
    """Read the last N outcome records."""
    if not _OUTCOME_FILE.exists():
        return []
    try:
        with open(_OUTCOME_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        outcomes = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return outcomes
    except OSError:
        return []


# ── Tracker API ───────────────────────────


def start_tracking(tuning_report: Optional[Dict] = None,
                   window: int = _DEFAULT_WINDOW) -> str:
    """Begin tracking effectiveness of auto-tune changes.

    Records the baseline success rate from recent outcomes,
    then monitors the next `window` outcomes.

    Args:
        tuning_report: The tuning changes that were applied.
        window: Number of outcomes to track.

    Returns:
        tracking_id: Unique ID for this tracking session.
    """
    baseline_outcomes = _read_recent_outcomes(window)
    baseline_total = len(baseline_outcomes)
    baseline_successes = sum(1 for o in baseline_outcomes if o.get("success", True))
    baseline_rate = round(baseline_successes / baseline_total, 4) if baseline_total else 1.0

    tracking_id = "track_" + uuid.uuid4().hex[:12]
    now = time.time()

    tracker = {
        "id": tracking_id,
        "created_at": now,
        "window": window,
        "baseline_success_rate": baseline_rate,
        "baseline_total": baseline_total,
        "baseline_successes": baseline_successes,
        "tuning_report": tuning_report or {},
        "outcomes_collected": 0,
        "outcome_successes": 0,
        "completed": False,
        "result": None,
    }

    data = _load()
    data["active_trackers"].append(tracker)
    _save(data)
    return tracking_id


def record_outcome(tracking_id: str, success: bool) -> Optional[Dict]:
    """Record one outcome for a specific tracking session.

    Args:
        tracking_id: The tracking session ID.
        success: Whether the outcome was successful.

    Returns:
        The tracker state if tracking ongoing, or the final result if window completed.
    """
    data = _load()
    for t in data["active_trackers"]:
        if t["id"] != tracking_id:
            continue
        if t.get("completed"):
            return t

        t["outcomes_collected"] = t.get("outcomes_collected", 0) + 1
        if success:
            t["outcome_successes"] = t.get("outcome_successes", 0) + 1

        # Check if window is complete
        if t["outcomes_collected"] >= t["window"]:
            t["completed"] = True
            current_rate = round(t["outcome_successes"] / t["outcomes_collected"], 4)
            baseline = t["baseline_success_rate"]
            improvement = round(current_rate - baseline, 4)

            t["result"] = {
                "current_success_rate": current_rate,
                "baseline_success_rate": baseline,
                "improvement": improvement,
                "improved": improvement > 0,
                "stable": abs(improvement) <= 0.05,
                "degraded": improvement < -0.05,
                "total_outcomes": t["outcomes_collected"],
                "successes": t["outcome_successes"],
            }
            _save(data)
            return t["result"]

        _save(data)
        return {"tracking": True, "collected": t["outcomes_collected"],
                "window": t["window"], "successes": t["outcome_successes"]}

    return None


def get_result(tracking_id: str) -> Optional[Dict]:
    """Get the final result of a completed tracking session."""
    data = _load()
    for t in data["active_trackers"]:
        if t["id"] == tracking_id:
            return t.get("result") or {
                "tracking": True,
                "collected": t.get("outcomes_collected", 0),
                "window": t["window"],
            }
    # Check history
    for h in data.get("history", []):
        if h["id"] == tracking_id:
            return h.get("result")
    return None


def list_active() -> List[Dict]:
    """List all active (not yet completed) trackers."""
    data = _load()
    return [t for t in data["active_trackers"] if not t.get("completed")]


def list_history(limit: int = 10) -> List[Dict]:
    """List completed tracking sessions."""
    data = _load()
    completed = [t for t in data["active_trackers"] if t.get("completed")]
    history = data.get("history", [])
    all_items = completed + history
    all_items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return all_items[:limit]


def archive_completed():
    """Move completed trackers to history."""
    data = _load()
    completed = [t for t in data["active_trackers"] if t.get("completed")]
    data["active_trackers"] = [t for t in data["active_trackers"] if not t.get("completed")]
    data.setdefault("history", [])
    data["history"].extend(completed)
    # Keep last 50 in history
    if len(data["history"]) > 50:
        data["history"] = data["history"][-50:]
    _save(data)
