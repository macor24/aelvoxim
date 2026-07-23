"""
aelvoxim.learn.meta_reviewer — Periodic meta-cognition self-review

Scans metacog history and learner logs for pattern deviations.
When sustained anomalies are detected (e.g. low scores, stagnation,
repair failures), auto-triggers calibration adjustments.

Outputs to dedicated meta_review.log and includes resource utilization checks.
Fifth phase of the meta-cognition improvement roadmap.
"""

from __future__ import annotations

import json
import logging
import time as _time
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
    1. Sustained low score
    2. Stagnation persistence
    3. Repair effectiveness
    4. Resource utilization (direction count, KB growth rate)
    """

    def __init__(self):
        self._last_review: float = 0.0
        self._review_log_path: Optional[Path] = None

    def _get_review_log(self) -> Path:
        if self._review_log_path is None:
            from ..utils import METACORE_DIR
            self._review_log_path = METACORE_DIR / "meta_review.log"
            self._review_log_path.parent.mkdir(parents=True, exist_ok=True)
        return self._review_log_path

    def _write_review_log(self, entry: str) -> None:
        """Append to dedicated meta_review.log with timestamp."""
        try:
            log_path = self._get_review_log()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(str(log_path), "a") as f:
                f.write(f"[{ts}] {entry}\n")
        except Exception:
            _log.exception("meta_reviewer error")

    def _get_resource_metrics(self) -> Dict:
        """Gather resource utilization stats for review."""
        metrics = {"directions_active": 0, "directions_total": 0,
                    "kb_entry_count": 0, "learner_cycles": 0}
        try:
            from ..learn.knowledge import KnowledgeBase
            metrics["kb_entry_count"] = len(list(KnowledgeBase.get_all_active_cached()))
        except Exception:
            pass
        try:
            from ..utils import METACORE_DIR, LEARNER_STATUS
            if LEARNER_STATUS.exists():
                st = json.loads(LEARNER_STATUS.read_text())
                metrics["learner_cycles"] = st.get("cycles", 0)
        except Exception:
            pass
        try:
            from ..learn.loop import get_learner
            l = get_learner()
            directions = l.list_directions()
            metrics["directions_total"] = len(directions)
            metrics["directions_active"] = sum(
                1 for d in directions if d.get("status") == "active"
            )
        except Exception:
            pass
        return metrics

    def review(self, metacog_history_file: Path = None) -> Optional[Dict]:
        """Run self-review cycle. Returns calibration suggestions or None."""
        now = _time.time()
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

            recent = lines[-20:]
            reports = []
            for line in recent:
                try:
                    reports.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue

            if len(reports) < _MIN_SAMPLES:
                return None

            suggestions = []
            resource_metrics = self._get_resource_metrics()

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
                    "reason": f"stagnation in {stagnation_ratio:.0%} of recent cycles",
                })

            # ── Check 3: Repair failure pattern ──
            repair_fail_count = 0
            for r in reports:
                actions = r.get("suggested_actions", [])
                if "reduce_learning_speed" in actions or "pause_direction" in actions:
                    continue
                if r.get("should_evolve") and r.get("overall_score", 0) > 0.3:
                    repair_fail_count += 1

            if repair_fail_count >= 3 and len(reports) >= 5:
                suggestions.append({
                    "type": "circuit_breaker",
                    "target": "repair_loop",
                    "value": "reduce_learning_speed",
                    "reason": f"{repair_fail_count} consecutive should_evolve without improvement",
                })

            # ── Check 4: Resource utilization ──
            kb_count = resource_metrics.get("kb_entry_count", 0)
            active_dirs = resource_metrics.get("directions_active", 0)
            total_dirs = resource_metrics.get("directions_total", 0)
            if active_dirs == 0 and total_dirs > 0:
                suggestions.append({
                    "type": "resource_idle",
                    "target": "learner",
                    "value": "suggest_new_directions",
                    "reason": f"learner has {total_dirs} directions but 0 active — all paused/completed",
                })
            if kb_count > 3000:
                suggestions.append({
                    "type": "resource_cleanup",
                    "target": "knowledge_base",
                    "value": "archive_old_entries",
                    "reason": f"KB has {kb_count} entries — nearing capacity, suggest archiving",
                })

            # ── Build review entry for log ──
            review_line = (
                f"score_avg={avg_score:.2f} stagnation={stagnation_count}/{len(reports)} "
                f"repair_fail={repair_fail_count} "
                f"dirs={active_dirs}/{total_dirs} kb={kb_count} "
                f"suggestions={len(suggestions)}"
            )

            if not suggestions:
                self._write_review_log(f"OK {review_line}")
                _log.info("  ✅ Meta-review: no anomalies detected (%s)", review_line)
                return None

            self._write_review_log(f"ISSUE {review_line}")
            for s in suggestions:
                self._write_review_log(f"  {s['type']}: {s['reason']}")

            _log.info("  🔍 Meta-review: %d suggestion(s) (%s)", len(suggestions), review_line)
            for s in suggestions:
                _log.info("    - %s: %s", s["type"], s["reason"])

            # Apply suggestions
            self._apply_suggestions(suggestions)

            return {"suggestions": suggestions, "reports_analyzed": len(reports),
                    "resource_metrics": resource_metrics}

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
                    parts = key.split(".")
                    if len(parts) == 2:
                        cal.set(parts[0], parts[1], value)
                    elif len(parts) == 3:
                        cal.set(parts[0], parts[1], parts[2], value)
                    _log.info("  🔧 Calibration: %s = %s", key, value)
                elif s["type"] in ("strategy_shift", "circuit_breaker", "resource_idle", "resource_cleanup"):
                    pass
        except Exception:
            _log.exception("meta_reviewer error")


__all__ = ["MetaReviewer"]
