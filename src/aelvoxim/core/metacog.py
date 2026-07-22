"""aelvoxim.core.metacog — MetaCogTrigger 

6 trigger signals + 2 extended signals for determining if evolution is needed. 
All thresholds loaded from calibration.json dynamically, auto-tune supported. 
Optional MemorySystem/SelfModel injection supported. 
Standalone, zero external deps (injection optional, works without). 
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

import logging
_log = logging.getLogger("aelvoxim.core.metacog")

if TYPE_CHECKING:
    from .selfmodel import SelfModel


class TriggerLevel(Enum):
    MILD = "mild"
    MODERATE = "moderate"
    CRITICAL = "critical"


# Default weights (used when calibration fails to load)
FALLBACK_WEIGHTS = {
    "success_rate": 0.20,
    "stagnation": 0.15,
    "repeat_failure": 0.15,
    "external_signal": 0.05,
    "introspection": 0.05,
    "memory_health": 0.15,
    "snapshot_trend": 0.10,
    "belief_health": 0.15,
}


@dataclass
class TriggerResult:
    signal_name: str = ""
    level: TriggerLevel = TriggerLevel.MILD
    triggered: bool = False
    score: float = 0.0
    reason: str = ""
    details: Dict = field(default_factory=dict)


@dataclass
class MetaCogReport:
    timestamp: str = ""
    should_evolve: bool = False
    max_level: str = "mild"
    overall_score: float = 0.0
    triggers: List[TriggerResult] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = {
            "timestamp": self.timestamp,
            "should_evolve": self.should_evolve,
            "max_level": self.max_level,
            "overall_score": round(self.overall_score, 3),
            "suggested_actions": self.suggested_actions,
            "triggers": [],
        }
        for t in self.triggers:
            td = asdict(t)
            td["level"] = t.level.value
            d["triggers"].append(td)
        return d

    def to_json(self) -> str:
        import json as _json
        return _json.dumps(self.to_dict(), ensure_ascii=False)

    @property
    def summary_line(self) -> str:
        triggered = [t.signal_name for t in self.triggers if t.triggered]
        if triggered:
            return f"trigger: {', '.join(triggered)} (level={self.max_level}, score={self.overall_score:.2f})"
        return f"no trigger (score={self.overall_score:.2f})"


class MetaCogTrigger:
    """Degradation detection trigger. 

    Monitors 8 signals. All thresholds loaded from calibration.json dynamically. 
    """

    def __init__(self, self_model: Optional[SelfModel] = None):
        self._history: List[MetaCogReport] = []
        self._self_model = self_model
        self._cal = None  # 懒加载
        self._last_memory_check: Optional[str] = None
        self._cached_memory_metrics: Dict = {}

    @property
    def _calibration(self):
        if self._cal is None:
            from .calibration import get_calibration
            self._cal = get_calibration()
        return self._cal

    def set_self_model(self, sm: SelfModel) -> None:
        self._self_model = sm

    def _cw(self, *keys: str, default: Any = None) -> Any:
        """Get metacog config from calibration."""
        return self._calibration.get("metacog", *keys, default=default)

    def evaluate(
        self,
        success_rate_7d: float = 1.0,
        success_rate_3d: float = 1.0,
        success_rate_1d: float = 1.0,
        days_since_last_improvement: int = 0,
        repeat_error_count: int = 0,
        external_signal_strength: float = 0.0,
        memory_system=None,
        belief_health: Optional[Dict[str, float]] = None,
    ) -> MetaCogReport:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        triggers = [
            self._check_success_rate(success_rate_7d, success_rate_3d, success_rate_1d),
            self._check_stagnation(days_since_last_improvement),
            self._check_repeat_failure(repeat_error_count),
            self._check_external_signal(external_signal_strength),
            self._check_introspection(days_since_last_improvement),
            self._check_memory_health(memory_system),
            self._check_snapshot_trend(),
            self._check_belief_health(belief_health),
        ]

        weights = self._cw("trigger_weights", default=FALLBACK_WEIGHTS)
        overall = sum(t.score * weights.get(t.signal_name, 0.1) for t in triggers)
        levels = [t for t in triggers if t.triggered]
        max_level = max(
            (l.level for l in levels),
            key=lambda x: list(TriggerLevel).index(x)
        ).value if levels else TriggerLevel.MILD.value

        evolve_threshold = self._cw("evolve_threshold", default=0.10)
        # Build suggested actions based on triggered signals
        actions: List[str] = []
        for t in triggers:
            if not t.triggered:
                continue
            if t.signal_name == "success_rate":
                actions.append("reduce_learning_speed")
                actions.append("increase_validation_threshold")
            elif t.signal_name == "stagnation":
                actions.append("switch_search_engine")
                actions.append("rephrase_subtopics")
            elif t.signal_name == "repeat_failure":
                actions.append("increase_practice_cycles")
                actions.append("pause_direction")
            elif t.signal_name == "belief_health":
                actions.append("cleanup_low_confidence_kb")
                actions.append("archive_stale_entries")
        report = MetaCogReport(
            timestamp=now,
            should_evolve=overall >= evolve_threshold,
            max_level=max_level,
            overall_score=overall,
            triggers=triggers,
            suggested_actions=actions,
        )
        # ── SentriKit integration (non-blocking) ──
        try:
            from ..client.sentrikit import is_available as _sk_ok, get_status as _sk_st
            if _sk_ok():
                _st = _sk_st()
                if _st and _st.get("metacog", {}).get("should_evolve"):
                    report.should_evolve = True
                    report.suggested_actions.insert(0, "consult_sentrikit")
                    report.suggested_actions.append("align_evolution_schedule")
        except Exception:
            _log.exception("metacog error")
        self._history.append(report)
        return report

    @staticmethod
    def _check_success_rate(sr7: float, sr3: float, sr1: float) -> TriggerResult:
        from .calibration import get_calibration as _get_cal
        cal = _get_cal()
        score, level, reasons = 0.0, TriggerLevel.MILD, []
        c1 = cal.get("metacog", "success_rate_1d_critical", default=0.70)
        c3 = cal.get("metacog", "success_rate_3d_moderate", default=0.80)
        c7 = cal.get("metacog", "success_rate_7d_warning", default=0.90)
        if sr1 < c1:
            score, level = 0.9, TriggerLevel.CRITICAL
            reasons.append(f"1d {sr1:.0%}<{c1:.0%}")
        if sr3 < c3:
            score = max(score, 0.7)
            level = TriggerLevel.MODERATE if level.value != "critical" else level
            reasons.append(f"3d {sr3:.0%}<{c3:.0%}")
        if sr7 < c7:
            score = max(score, 0.5)
            reasons.append(f"7d {sr7:.0%}<{c7:.0%}")
        return TriggerResult(
            signal_name="success_rate", level=level, triggered=score > 0,
            score=score, reason="; ".join(reasons) or "ok",
        )

    @staticmethod
    def _check_external_signal(strength: float) -> TriggerResult:
        from .calibration import get_calibration as _get_cal
        trigger = _get_cal().get("metacog", "external_signal_trigger", default=0.3)
        return TriggerResult(
            signal_name="external_signal", triggered=strength > trigger,
            score=strength,
            reason=f"external signal {strength:.2f}" if strength > trigger else "ok",
        )

    def _check_stagnation(self, days: int) -> TriggerResult:
        trigger_days = self._cw("stagnation_days_trigger", default=3)
        moderate_days = self._cw("stagnation_days_moderate", default=14)
        rate = self._cw("stagnation_score_rate", default=14.0)
        score, level = min(1.0, days / rate), TriggerLevel.MILD
        if days >= moderate_days:
            level = TriggerLevel.MODERATE
        return TriggerResult(
            signal_name="stagnation", level=level, triggered=days >= trigger_days,
            score=score, reason=f"{days}d no improvement" if days > 0 else "ok",
        )

    def _check_repeat_failure(self, count: int) -> TriggerResult:
        moderate = self._cw("repeat_failure_moderate", default=3)
        critical = self._cw("repeat_failure_critical", default=5)
        rate = self._cw("repeat_failure_score_rate", default=5.0)
        trigger = self._cw("repeat_failure_trigger", default=1)
        score, level = min(1.0, count / rate), TriggerLevel.MILD
        if count >= critical:
            level = TriggerLevel.CRITICAL
        elif count >= moderate:
            level = TriggerLevel.MODERATE
        return TriggerResult(
            signal_name="repeat_failure", level=level, triggered=count >= trigger,
            score=score, reason=f"repeat fail {count}x" if count > 0 else "ok",
        )

    def _check_introspection(self, days: int) -> TriggerResult:
        trigger_days = self._cw("introspection_trigger_days", default=7)
        rate = self._cw("introspection_score_rate", default=10.0)
        return TriggerResult(
            signal_name="introspection", triggered=days >= trigger_days,
            score=min(1.0, days / rate),
            reason=f"introspect: {days}d no improvement" if days >= trigger_days else "ok",
        )

    # ── Signal 6: Memory health ────────────────────

    def _check_memory_health(self, memory_system=None) -> TriggerResult:
        score, level, reasons = 0.0, TriggerLevel.MILD, []

        # Auto-read from SQLite DB directly
        try:
            import json as _js, sqlite3 as _sq
            from ..utils import METACORE_DIR as _md
            _db_path = str(_md / "memory.db")
            _db = _sq.connect(_db_path)
            _db.row_factory = _sq.Row

            # Entity stats
            _total_entities = _db.execute(
                "SELECT COUNT(*) FROM entities"
            ).fetchone()[0]
            _expired_entities = _db.execute(
                "SELECT COUNT(*) FROM entities WHERE locked = 0 AND created_at < date('now', '-30 days')"
            ).fetchone()[0]
            _locked_entities = _db.execute(
                "SELECT COUNT(*) FROM entities WHERE locked = 1"
            ).fetchone()[0]

            # Event stats
            _total_events = _db.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            _old_events = _db.execute(
                "SELECT COUNT(*) FROM events WHERE type = 'chat_inquiry' AND timestamp < date('now', '-30 days')"
            ).fetchone()[0]

            # Entity type distribution (for noise detection)
            _type_dist = _db.execute(
                "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC"
            ).fetchall()
            _type_map = {r["type"]: r["cnt"] for r in _type_dist}

            _db.close()

            if _total_entities == 0:
                return TriggerResult(
                    signal_name="memory_health", triggered=False,
                    score=0.0, reason="memory is empty",
                )

            # Metric 1: Stale entity ratio (>30d unlocked)
            stale_ratio = _expired_entities / max(_total_entities, 1)
            if stale_ratio > 0.3:
                score = max(score, 0.5)
                reasons.append(f"stale entities {_expired_entities} ({stale_ratio:.0%})")

            # Metric 2: Old event ratio
            old_event_ratio = _old_events / max(_total_events, 1)
            if old_event_ratio > 0.5 and _total_events > 50:
                score = max(score, 0.4)
                reasons.append(f"old events {old_event_ratio:.0%}")

            # Metric 3: Noise detection — location/org dominated by long entries
            _noise_types = {"location", "organization"}
            _noise_total = sum(_type_map.get(t, 0) for t in _noise_types)
            _all_ents = _type_map.get("entity", _type_map.get("concept", 0))
            if _noise_total > _all_ents * 2 and _all_ents > 0:
                score = max(score, 0.3)
                reasons.append(f"noise loc/org={_noise_total}")

            # Metric 4: Locked entity ratio (positive signal)
            if _total_entities > 10 and _locked_entities < 2:
                score = max(score, 0.2)
                reasons.append("too few locked entities")

            if not reasons:
                reasons.append(f"ok ({_total_entities} entities, {_locked_entities} locked)")

        except Exception as e:
            return TriggerResult(
                signal_name="memory_health", triggered=False,
                score=0.0, reason=f"read failed: {e}",
            )

        min_score = self._cw("memory_health_min_score", default=0.30)
        return TriggerResult(
            signal_name="memory_health",
            level=level,
            triggered=score > min_score,
            score=score,
            reason="; ".join(reasons),
            details={
                "total_entities": _total_entities,
                "expired_entities": _expired_entities,
                "locked_entities": _locked_entities,
                "total_events": _total_events,
            },
        )

    # ── Signal 7: Snapshot trend ──────────────────────

    def _check_snapshot_trend(self) -> TriggerResult:
        # Auto-detect trend from learner status + memory DB
        try:
            import json as _js, sqlite3 as _sq
            from ..utils import METACORE_DIR as _md, LEARNER_STATUS

            # 1. Learner cycles trend
            learner_cycles = 0
            if LEARNER_STATUS.exists():
                st = _js.loads(LEARNER_STATUS.read_text())
                learner_cycles = st.get("cycles", 0)

            # 2. Entity growth from DB (compare recent vs older)
            _db_path = str(_md / "memory.db")
            _db = _sq.connect(_db_path)
            _recent_ents = _db.execute(
                "SELECT COUNT(*) FROM entities WHERE created_at >= date('now', '-7 days')"
            ).fetchone()[0]
            _old_ents = _db.execute(
                "SELECT COUNT(*) FROM entities WHERE created_at < date('now', '-7 days') AND created_at >= date('now', '-30 days')"
            ).fetchone()[0]
            _db.close()

            # 3. Decline detection
            reasons = []
            score = 0.0
            level = TriggerLevel.MILD

            # If old period had entities but recent has none → stagnation
            if _old_ents > 10 and _recent_ents == 0:
                score = max(score, 0.6)
                level = TriggerLevel.MODERATE
                reasons.append(f"no new entities in 7d (prev {_old_ents})")

            # If learner cycles are very low relative to time
            if learner_cycles == 0:
                score = max(score, 0.3)
                reasons.append("learner loop not running")

            if not reasons:
                if _recent_ents > 0:
                    reasons.append(f"trend ok (+{_recent_ents} new entities)")
                else:
                    reasons.append("stable (no new data)")

            trigger = self._cw("snapshot_trend_trigger", default=0.3)
            return TriggerResult(
                signal_name="snapshot_trend",
                level=level,
                triggered=score > trigger,
                score=score,
                reason="; ".join(reasons),
                details={
                    "recent_entities": _recent_ents,
                    "old_entities": _old_ents,
                    "learner_cycles": learner_cycles,
                },
            )

        except Exception as e:
            return TriggerResult(
                signal_name="snapshot_trend", triggered=False,
                score=0.0, reason=f"check failed: {e}",
            )

    # ── Signal 8: Belief health ──────────────────────────

    @staticmethod
    def _check_belief_health(belief_health: Optional[Dict[str, float]] = None) -> TriggerResult:
        if belief_health is None:
            try:
                from ..utils import METACORE_DIR as _md
                fp = _md / "reasoner_beliefs.json"
                if fp.exists():
                    import json
                    with open(fp, encoding="utf-8") as f:
                        data = json.load(f)
                    belief_health = {k: v.get("health", 1.0) for k, v in data.items()}
                else:
                    return TriggerResult(
                        signal_name="belief_health", triggered=False,
                        score=0.0, reason="no belief data",
                    )
            except Exception:
                return TriggerResult(
                    signal_name="belief_health", triggered=False,
                    score=0.0, reason="cannot access belief data",
                )

        if not belief_health:
            return TriggerResult(
                signal_name="belief_health", triggered=False,
                score=0.0, reason="no belief data",
            )

        from .calibration import get_calibration
        cal = get_calibration()
        unhealthy_th = cal.get("metacog", "belief_unhealthy_threshold", default=0.6)
        critical_th = cal.get("metacog", "belief_critical_threshold", default=0.3)

        critical_unhealthy = {n: h for n, h in belief_health.items() if h < critical_th}
        unhealthy = {n: h for n, h in belief_health.items() if h < unhealthy_th}

        if critical_unhealthy:
            score = 0.9
            level = TriggerLevel.CRITICAL
            reason = f"critical: {', '.join(critical_unhealthy.keys())}"
        elif unhealthy:
            score = 0.6
            level = TriggerLevel.MODERATE
            reason = f"unhealthy: {', '.join(unhealthy.keys())}"
        else:
            return TriggerResult(
                signal_name="belief_health", triggered=False,
                score=0.0, reason="ok",
            )

        return TriggerResult(
            signal_name="belief_health", level=level,
            triggered=True, score=score, reason=reason,
            details={"unhealthy": unhealthy, "critical": critical_unhealthy},
        )

    def get_history(self, limit: int = 10) -> List[Dict]:
        return [r.to_dict() for r in self._history[-limit:]]


__all__ = [
    "MetaCogTrigger", "MetaCogReport", "TriggerResult",
    "TriggerLevel", "FALLBACK_WEIGHTS",
]
