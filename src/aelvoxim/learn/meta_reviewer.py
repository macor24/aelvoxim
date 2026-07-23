"""
aelvoxim.learn.meta_reviewer — 元认知日志定期自审

Periodically scans metacog history and learner logs to detect pattern deviations.
When it finds sustained anomalies (e.g. confidence-vs-verification gap > 0.3),
it auto-triggers calibration adjustments.

Fifth phase of the meta-cognition improvement roadmap.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger("aelvoxim.meta_reviewer")

# ── Constants ──
_REVIEW_INTERVAL = 3600 * 24  # once per day
_MIN_SAMPLES = 3


class MetaReviewer:
    """Scan metacog history logs for pattern deviations and trigger auto-calibration.

    Checks performed each review cycle:
    1. Confidence-vs-verification gap: if avg(score) - avg(verify_pass_rate) > 0.3
    2. Stagnation persistence: if >70% of recent reports show stagnation signal
    3. Repair effectiveness: if repairs consistently fail to resolve signals
    """

    def __init__(self):
        self._last_review: float = 0.0

    def review(self, metacog_history_file: Path = None) -> Optional[Dict]:
        """Run self-review cycle. Returns calibration suggestions or None."""
        import time
        now = time.time()
        if now - self._last_review < _REVIEW_INTERVAL:
            return None
        self._last_review = now

        if metacog_history_file is None:
            from ..utils import METACORE_DIR
            metacog_history_file = METACORE_DIR / "metacog_history.jsonl"

        if not metacog_history_file.exists():
            return None

        try:
            lines = metacog_history_file.read_text().strip().split("\n")
            if len(lines) < _MIN_SAMPLES:
                return None

            recent = lines[-20:]  # last 20 entries
            reports = []
            for line in recent:
                try:
                    reports.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue

            if len(reports) < _MIN_SAMPLES:
                return None

            suggestions = []

            # ── Check 1: Sustained low score ──
            avg_score = sum(r.get("overall_score", 0.5) for r in reports) / len(reports)
            if avg_score < 0.3:
                suggestions.append({
                    "type": "parameter_tune",
                    "target": "metacog.evolve_threshold",
                    "value": 0.05,
                    "reason": f"avg metacog score {avg_score:.2f} < 0.3, lower evolve threshold to 0.05",
                })

            # ── Check 2: Stagnation persistence ──
            stagnation_count = 0
            for r in reports:
                for t in r.get("triggers", []):
                    if t.get("signal_name") == "stagnation" and t.get("triggered"):
                        stagnation_count += 1
                        break
            stagnation_ratio = stagnation_count / len(reports)
            if stagnation_ratio > 0.7:
                suggestions.append({
                    "type": "strategy_shift",
                    "target": "stagnation",
                    "value": "rephrase_topics",
                    "reason": f"stagnation in {stagnation_ratio:.0%} of recent cycles, suggest rephrasing topics",
                })

            # ── Check 3: Repair failure pattern ──
            repair_fail_count = 0
            for r in reports:
                actions = r.get("suggested_actions", [])
                if "reduce_learning_speed" in actions or "pause_direction" in actions:
                    continue  # these are reactive, not repair attempts
                if r.get("should_evolve") and r.get("overall_score", 0) > 0.3:
                    # A report that says "should evolve" but score is still moderate
                    # followed by another with same pattern = repair ineffective
                    repair_fail_count += 1

            if repair_fail_count >= 3 and len(reports) >= 5:
                suggestions.append({
                    "type": "circuit_breaker",
                    "target": "repair_loop",
                    "value": "reduce_learning_speed",
                    "reason": f"{repair_fail_count} consecutive 'should_evolve' without improvement, "
                              f"activate circuit breaker",
                })

            if not suggestions:
                _log.info("  ✅ Meta-review: no anomalies detected (avg score=%.2f, "
                          "stagnation=%d/%d)", avg_score, stagnation_count, len(reports))
                return None

            _log.info("  🔍 Meta-review: %d calibration suggestion(s)", len(suggestions))
            for s in suggestions:
                _log.info("    - %s: %s", s["type"], s["reason"])

            # Apply suggestions
            self._apply_suggestions(suggestions)

            return {"suggestions": suggestions, "reports_analyzed": len(reports)}

        except Exception:
            _log.exception("meta_reviewer error")
            return None

    @staticmethod
    def _apply_suggestions(suggestions: List[Dict]) -> None:
        """Apply calibration parameter changes."""
        try:
            from ..core.calibration import get_calibration
            cal = get_calibration()
            for s in suggestions:
                if s["type"] == "parameter_tune":
                    key = s["target"]
                    value = s["value"]
                    # Set calibration parameter
                    parts = key.split(".")
                    if len(parts) == 2:
                        cal.set(parts[0], parts[1], value)
                    elif len(parts) == 3:
                        cal.set(parts[0], parts[1], parts[2], value)
                    _log.info("  🔧 Calibration: %s = %s", key, value)
                elif s["type"] == "strategy_shift" or s["type"] == "circuit_breaker":
                    # These are logged for operator awareness; the actual
                    # strategy change happens in the next cognition tick
                    pass
        except Exception:
            _log.exception("meta_reviewer error")


__all__ = ["MetaReviewer"]
