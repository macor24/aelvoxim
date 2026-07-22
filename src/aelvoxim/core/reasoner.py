# SPDX-License-Identifier: MIT
"""
metacore.core.reasoner — Proactive reasoning engine (rule-based).

W5: Scenario analogy via topic similarity matching.
W6 (optional): Bayesian network for entity co-occurrence prediction.

All predictions carry a confidence score. Below 0.7 nothing is shown.
No LLM calls, no automatic decisions.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import METACORE_DIR


import logging
_log = logging.getLogger("aelvoxim.core.reasoner")

class ProactiveReasoner:
    """Proactive reasoning engine using rule-based similarity matching.

    Predicts what the user might need next by comparing current
    topic focus with historical session patterns.
    """

    def __init__(self) -> None:
        self._silent_until: float = 0.0  # Timestamp until which to stay silent
        self._consecutive_unanswered: int = 0

    # ── W5: Scenario analogy (similarity matching) ──

    def predict(
        self,
        current_topics: List[str],
        user_id: str = "",
    ) -> Dict[str, Any]:
        """Predict next user needs based on topic similarity.

        Args:
            current_topics: Topic keywords from the current session.
            user_id: Optional user identifier.

        Returns:
            Dict with keys:
            - predictions: list of {"topic": str, "confidence": float}
            - matched: list of matched historical session summaries
            - should_show: bool (True only if any confidence >= 0.7)
        """
        result: Dict[str, Any] = {
            "predictions": [],
            "matched": [],
            "should_show": False,
        }
        if not current_topics:
            return result

        # Check silence period
        if time.time() < self._silent_until:
            return result

        # Compare with historical snapshots
        try:
            from ..server.session_manager import _user_dir as _sd
            user_snap_dir = _sd(user_id)
            if not user_snap_dir.exists():
                return result

            current_set = set(t.lower() for t in current_topics)
            predictions: List[Tuple[str, float]] = []

            for snap_file in sorted(user_snap_dir.glob("*.json"), key=os.path.getmtime, reverse=True)[:10]:
                try:
                    data = json.loads(snap_file.read_text())
                    snap_topics = [t.lower() for t in data.get("topic_focus", [])]
                    if not snap_topics:
                        continue
                    # Compute overlap ratio
                    overlap = len(current_set & set(snap_topics))
                    total = len(current_set | set(snap_topics))
                    if total == 0:
                        continue
                    similarity = overlap / total
                    if similarity >= 0.4:
                        # Suggest topics from the matched snapshot
                        for t in snap_topics:
                            if t not in current_set:
                                predictions.append((t, similarity))
                except Exception:
                    continue

            # Deduplicate and sort
            seen: Set[str] = set()
            for topic, conf in sorted(predictions, key=lambda x: -x[1]):
                if topic not in seen and conf >= 0.5:
                    seen.add(topic)
                    result["predictions"].append({
                        "topic": topic[:40],
                        "confidence": round(conf, 2),
                    })
                    if conf >= 0.7:
                        result["should_show"] = True

            result["predictions"] = result["predictions"][:5]
        except Exception:
            _log.exception("reasoner error")

        return result

    # ── W6: Record outcome (for future Bayesian update) ──

    def record_outcome(self, topic: str, accepted: bool) -> None:
        """Record whether a prediction was accepted or rejected by the user.

        This data can be used for Bayesian updates in a future version.
        """
        log_path = Path(METACORE_DIR) / "reasoner" / "outcomes.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "ts": datetime.now().isoformat(),
            "topic": topic,
            "accepted": accepted,
        }, ensure_ascii=False)
        with open(str(log_path), "a") as f:
            f.write(entry + "\n")

    # ── W7: Propriety control ──

    def record_user_response(self, responded: bool) -> None:
        """Record whether the user responded to the last proactive message.

        After 2 consecutive unresponded messages, silence for 24 hours.
        """
        if responded:
            self._consecutive_unanswered = 0
        else:
            self._consecutive_unanswered += 1
            if self._consecutive_unanswered >= 2:
                self._silent_until = time.time() + 86400  # 24h silence

    def is_silenced(self) -> bool:
        """Check if the reasoner is currently in silence period."""
        return time.time() < self._silent_until

    def reset_silence(self) -> None:
        """Manually reset silence period (user re-enabled proactive mode)."""
        self._silent_until = 0.0
        self._consecutive_unanswered = 0
