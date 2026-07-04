# SPDX-License-Identifier: MIT
"""
metacore.core.metacog_monitor — Unified meta-cognition monitor.

Integrates:
- MetaCogTrigger evaluation (from metacog.py)
- SelfModel scoring (from selfmodel.py)
- Cognitive overload detection
- Self-reflection summary
- Ethics L5 (rate limit) and L6 (circuit breaker)
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

# ── Constants ──

_ETHICS_LOG_DIR = METACORE_DIR / "ethics"
_REFLECTION_DIR = METACORE_DIR / "reflections"
_L6_CONSECUTIVE_LOW = 3
_OVERLOAD_WINDOW = 3600  # 1 hour
_RATE_WINDOW = 86400    # 24 hours


def _cal(key: str, default: float) -> float:
    """Read a float from Calibration module, falling back to default."""
    try:
        from .calibration import get_calibration
        return get_calibration().get("metacog_monitor", key, default=default)
    except Exception:
        return default


def _get_overload_threshold() -> float:
    return _cal("overload_threshold", 5)


def _get_low_confidence_threshold() -> float:
    return _cal("low_confidence_threshold", 0.3)


def _get_rate_limit_max() -> int:
    return int(_cal("rate_limit_max", 5))


# ── Ethics gates ──

_ETHICS_GATES: Dict[str, bool] = {
    "L1_input_filter": True,
    "L2_belief_lock": True,
    "L3_rollback": True,
    "L4_human_supervision": True,
    "L5_rate_limit": True,
    "L6_circuit_breaker": True,
}


def audit_log(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    entry = json.dumps({
        "ts": datetime.now().isoformat(),
        "event": event,
        **(data or {}),
    }, ensure_ascii=False)
    log_path = _ETHICS_LOG_DIR / "audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(log_path), "a") as f:
        f.write(entry + "\n")


def get_ethics_gate(gate: str) -> bool:
    return _ETHICS_GATES.get(gate, True)


def set_ethics_gate(gate: str, enabled: bool, reason: str = "") -> bool:
    if gate not in _ETHICS_GATES:
        return False
    old = _ETHICS_GATES[gate]
    _ETHICS_GATES[gate] = enabled
    audit_log("gate_toggle", {"gate": gate, "from": old, "to": enabled, "reason": reason})
    return True


class MetaCogMonitor:
    """Unified monitor for meta-cognition, overload detection, ethics, and reflection."""

    def __init__(self) -> None:
        self._tick_times: List[float] = []
        self._overload_ticks: List[float] = []
        self._low_conf_streak: int = 0
        self._tripped: bool = False
        self._trip_reason: str = ""
        self._trip_time: float = 0.0
        self._last_reflection: str = ""
        _ETHICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _REFLECTION_DIR.mkdir(parents=True, exist_ok=True)

    def record_tick(self) -> None:
        """Record one cognition tick for overload tracking."""
        now = time.time()
        self._overload_ticks.append(now)
        cutoff = now - _OVERLOAD_WINDOW
        self._overload_ticks = [t for t in self._overload_ticks if t > cutoff]

    def evaluate(
        self,
        learner_stats: Optional[Dict[str, Any]] = None,
        belief_stats: Optional[Dict[str, Any]] = None,
        metacog_report: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run full evaluation and return structured report."""
        self.record_tick()

        report: Dict[str, Any] = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "score": 0.5,
            "overall_score": 0.5,
            "overload": False,
            "rate_limited": False,
            "breaker_tripped": self._tripped,
            "should_evolve": False,
            "triggers": [],
            "reflection": "",
            "suggested_actions": [],
        }

        # 1. Overload
        report["overload"] = self.check_overload()

        # 2. Rate limit — applies to all editions
        report["rate_limited"] = self.check_rate_limit()
        if report["rate_limited"]:
            report["suggested_actions"].append("rate_limit_hit")

        # 3. Score from metacog report
        if metacog_report:
            report["score"] = getattr(metacog_report, "overall_score", 0.5)
            report["triggers"] = getattr(metacog_report, "triggers", [])
            actions = getattr(metacog_report, "suggested_actions", [])
            report["suggested_actions"].extend(actions)
        elif learner_stats:
            # Fallback: score from learner cycle stats
            total = learner_stats.get("total_cycles", 1)
            entries = learner_stats.get("total_entries", 0)
            report["score"] = min(1.0, entries / max(total, 1))

        # 4. Low-confidence streak for L6 breaker
        if report["score"] < _get_low_confidence_threshold():
            self._low_conf_streak += 1
        else:
            self._low_conf_streak = 0

        # 5. Circuit breaker
        if not self._tripped and self._low_conf_streak >= _L6_CONSECUTIVE_LOW:
            self.trip_breaker(f"Low confidence streak: {self._low_conf_streak}")
            report["breaker_tripped"] = True
            report["suggested_actions"].append("circuit_breaker_tripped")

        # 6. Reflection + narrative
        report["reflection"] = self.generate_reflection(report)
        self._last_reflection = report["reflection"]

        return report

    # ── Overload ──

    def check_overload(self) -> bool:
        cutoff = time.time() - _OVERLOAD_WINDOW
        recent = sum(1 for t in self._overload_ticks if t > cutoff)
        return recent >= _get_overload_threshold()

    # ── Rate limit (L5) ──

    def check_rate_limit(self, pre_consume: bool = True) -> bool:
        """Check if 24h tick count exceeds the maximum. Pre-consume on check."""
        limit = _get_rate_limit_max()
        cutoff = time.time() - _RATE_WINDOW
        recent = sum(1 for t in self._tick_times if t > cutoff)
        if recent >= limit:
            return True
        if pre_consume:
            self._tick_times.append(time.time())
            self._tick_times = [t for t in self._tick_times if t > cutoff]
        return False

    # ── Circuit breaker (L6) ──

    def trip_breaker(self, reason: str) -> None:
        self._tripped = True
        self._trip_reason = reason
        self._trip_time = time.time()
        log_path = _ETHICS_LOG_DIR / "l6_events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log_path), "a") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "event": "l6_trip",
                "reason": reason,
                "tick_count_24h": len(self._tick_times),
            }, ensure_ascii=False) + "\n")

    def is_tripped(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        log_path = _ETHICS_LOG_DIR / "l6_events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log_path), "a") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "event": "l6_reset",
                "previous_reason": self._trip_reason,
                "duration_seconds": time.time() - self._trip_time,
            }, ensure_ascii=False) + "\n")
        self._tripped = False
        self._trip_reason = ""
        self._trip_time = 0
        self._low_conf_streak = 0

    def get_trip_info(self) -> Dict[str, Any]:
        return {
            "tripped": self._tripped,
            "reason": self._trip_reason,
            "since": datetime.fromtimestamp(self._trip_time).isoformat() if self._trip_time else "",
        }

    # ── Reflection ──

    def generate_reflection(self, report: Dict[str, Any]) -> str:
        report["narrative"] = NarrativeEngine.narrate(report)
        score = report.get("score", 0.5)
        signals = []
        if report.get("overload"):
            signals.append("overload")
        if report.get("rate_limited"):
            signals.append("rate_limited")
        if report.get("breaker_tripped"):
            signals.append("breaker")
        triggers = report.get("triggers", [])
        trig_count = len([t for t in triggers if getattr(t, "triggered", False)]) if triggers else 0
        parts = [f"score={score:.2f}"]
        if signals:
            parts.append("|" + ",".join(signals))
        if trig_count:
            parts.append(f"triggers={trig_count}")
        reflection = " ".join(parts)
        today = datetime.now().strftime("%Y-%m-%d")
        ref_path = _REFLECTION_DIR / f"{today}.jsonl"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(ref_path), "a") as f:
            f.write(json.dumps({
                "ts": report.get("ts", ""),
                "reflection": reflection,
                "narrative": report.get("narrative", ""),
            }, ensure_ascii=False) + "\n")
        return reflection


class NarrativeEngine:
    """Generate human-readable self-narratives from meta-cognition signals."""

    @staticmethod
    def narrate(report: dict) -> str:
        score = report.get("score", 0.5)
        overload = report.get("overload", False)
        breaker = report.get("breaker_tripped", False)
        triggers = report.get("triggers", [])
        actions = report.get("suggested_actions", [])
        parts = []

        triggered_names = {
            getattr(t, "signal_name", "")
            for t in triggers
            if hasattr(t, "triggered") and t.triggered
        }

        if breaker:
            parts.append("Circuit breaker triggered — learning paused as protective measure")
        if overload:
            parts.append("Overloaded — too many cognition cycles in a short period")

        if "stagnation" in triggered_names:
            parts.append("Learning stagnated — keywords may be stale")
        if "success_rate" in triggered_names:
            parts.append("Success rate below threshold — approach needs adjustment")
        if "repeat_failure" in triggered_names:
            parts.append("Repeated failures on same topic — may need different strategy")
        if "belief_health" in triggered_names:
            parts.append("Knowledge quality degrading — some beliefs may be unreliable")

        if "switch_engine" in actions or "repeat_failure" in triggered_names:
            parts.append("Considering switching search engine or re-decomposing the topic")

        if not parts:
            parts.append("All systems running smoothly" if score > 0.3 else "Low activity — no significant changes")

        return " | ".join(parts)
