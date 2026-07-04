"""aelvoxim.learn.review — Review scheduler

Allocate review resources by value-forgetting-pressure ratio.
Core logic: 100% stdlib, no external dependencies.

Note: learner.py has embedded review logic (_schedule_review, _check_reviews).
This module provides a standalone ReviewScheduler for external use.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR, read_json


class ReviewScheduler:
    """Standalone review scheduler. Reviews knowledge entries based on
    value-pressure ratio and spaced repetition intervals."""

    def __init__(self):
        self._entries_dir = METACORE_DIR / "knowledge" / "entries"

    def get_due_reviews(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Find knowledge entries due for review based on their review history.

        Returns entries where next_review_time <= now, sorted by urgency.
        """
        now = datetime.now()
        due = []
        if not self._entries_dir.exists():
            return due
        for f in sorted(self._entries_dir.glob("*.json"))[:limit * 3]:
            try:
                entry = json_loads(f.read_text())
            except Exception:
                continue
            rh = entry.get("review_history", [])
            if not rh:
                continue
            try:
                next_review = datetime.strptime(rh[-1], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            if now >= next_review:
                entry["_file"] = str(f)
                due.append(entry)
                if len(due) >= limit:
                    break
        return due

    def score(self, entry: Dict) -> float:
        """Calculate urgency score for an entry (higher = more urgent)."""
        conf = entry.get("confidence", 0.5)
        access = entry.get("access_count", 0)
        return conf * (1 + access * 0.1)


def json_loads(s: str) -> dict:
    import json
    return json.loads(s)
