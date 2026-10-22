"""aelvoxim.core.metacog — MetaCogTrigger 

6 trigger signals + 2 extended signals for determining if evolution is needed. 
All thresholds loaded from calibration.json dynamically, auto-tune supported. 
Optional MemorySystem/SelfModel injection supported. 
Standalone, zero external deps (injection optional, works without). 
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
    "success_rate": 0.15,
    "stagnation": 0.10,
    "repeat_failure": 0.10,
    "external_signal": 0.05,
    "introspection": 0.05,
    "memory_health": 0.10,
    "snapshot_trend": 0.10,
    "belief_health": 0.10,
    "execution_health": 0.25,  # highest weight: execution layer resources
    "retrieval_gap": 0.10,     # 2026-10-21：用户问了但知识检索空手（外部视角）
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


SCORE_DELTA = 0.02


def window_success_rate(snapshots, window_hours: int, now: float = None):
    """按时间窗算"每轮产出条目"的比率。

    2026-10-21：样本不足时返回 None，而不是退回整体成功率。
    此前 learner 重启会清空内存里的 tick 快照，于是窗口退化成 lifetime 值，
    重启后的一段时间内永远不会报成功率问题（假健康）。
    """
    import time as _t
    now = now if now is not None else _t.time()
    cutoff = now - window_hours * 3600
    recent = [(e, c) for ts, e, c in (snapshots or []) if ts >= cutoff]
    if len(recent) < 2:
        return None
    (e0, c0), (e1, c1) = recent[0], recent[-1]
    return (e1 - e0) / max(c1 - c0, 1)


def _triggered_signals(report) -> set:
    return {t.signal_name for t in (getattr(report, "triggers", None) or [])
            if getattr(t, "triggered", False)}


def _should_record(prev, report, delta: float = SCORE_DELTA) -> bool:
    """状态没变就不落盘。

    2026-10-19：此前每轮检查都 append 一条，生产堆积 2558 条内容相同的记录，
    监控每半小时报一次警（写侧无去重）。判定只认三件事：level 变、score 变 > delta、
    触发信号集合变。持续中这件事不丢 —— 记在 metacog_state.json 的 streak 里。
    """
    if prev is None:
        return True
    if prev.max_level != report.max_level:
        return True
    if abs(float(prev.overall_score or 0.0) - float(report.overall_score or 0.0)) > delta:
        return True
    return _triggered_signals(prev) != _triggered_signals(report)


def _touch_state(report, recorded: bool) -> None:
    """把"持续中"写进 state：不落历史，但 streak / last_seen 不丢。"""
    from ..utils import METACORE_DIR
    p = METACORE_DIR / "metacog_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        prev = json.loads(p.read_text())
    except Exception:
        prev = {}
    streak = 1 if recorded else int(prev.get("streak") or 0) + 1
    p.write_text(json.dumps({
        "last_seen": report.timestamp,
        "last_score": round(float(report.overall_score or 0.0), 3),
        "max_level": report.max_level,
        "streak": streak,
        "recorded_at": report.timestamp if recorded else prev.get("recorded_at"),
    }, ensure_ascii=False), encoding="utf-8")


def _learner_liveness(status) -> tuple:
    """返回 ("running"|"stale"|"unknown", 心跳年龄秒)。判活以心跳为准，不信计数器。

    2026-10-19：原实现读 status.json 的 "cycles" 键 —— 该键根本不存在（实际是
    loop_count），于是永远取 0、永远判"learner loop not running"，而 learner 心跳
    只有 12 秒。计数器还有一个坑：重启后 loop_count 归零，单看绝对值会误判。
    """
    if not status:
        return "unknown", -1.0
    try:
        age = (datetime.now() - datetime.strptime(
            str(status.get("updated_at") or ""), "%Y-%m-%d %H:%M:%S")).total_seconds()
    except Exception:
        return ("running" if status.get("running") else "unknown"), -1.0
    if status.get("running") is False or age > 600:
        return "stale", age
    return "running", age


def _unusable_ratio_score(total: int, empty_value: int) -> float:
    """不可用条目占比 → 分数。0 = 正常；>20% 轻微；>50% 明显。

    2026-10-19：这是 memory_health 的新打分依据（内容为空 = 真缺陷），
    取代原来按 ">30 天未锁" 扣分的口径（那等于惩罚知识积累）。
    """
    if total <= 0:
        return 0.0
    ratio = empty_value / total
    if ratio > 0.5:
        return 0.5
    if ratio > 0.2:
        return 0.4
    return 0.0


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
        self.load_history()

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
            self._check_execution_health(),
            self._check_retrieval_gap(),
        ]

        weights = self._cw("trigger_weights", default=FALLBACK_WEIGHTS)
        _unweighted = [t.signal_name for t in triggers if t.signal_name not in weights]
        if _unweighted:
            # 2026-10-21：此前未登记的信号静默拿 0.1，权重表漏项完全看不出来
            _log.warning("metacog signals without weight (using 0.1): %s", _unweighted)
        overall = sum(t.score * weights.get(t.signal_name, 0.1) for t in triggers)
        levels = [t for t in triggers if t.triggered]
        max_level = max(
            (l.level for l in levels),
            key=lambda x: list(TriggerLevel).index(x)
        ).value if levels else TriggerLevel.MILD.value

        evolve_threshold = self._cw("evolve_threshold", default=0.03)
        # Recording gate — deliberately decoupled from evolve_threshold.
        # auto_tune raises evolve_threshold when evolution hit-rate is low;
        # if recording shared that threshold, tuning silently starved the
        # metacog history + self-review chain (2026-08-24: threshold was 0.3,
        # steady-state overall≈0.198 → every evaluation skipped recording).
        _record_threshold = self._cw("record_threshold", default=0.05)
        if overall < _record_threshold:
            # 2026-10-20：早退也要留痕。此前早退直接 return，state 的 last_seen
            # 只在越过阈值时才更新 ⇒ "健康地安静"和"已经死了"在读数上完全一样
            # （生产上被误判成自省链断了 28 小时）。history 仍按阈值节流。
            _early = MetaCogReport(
                timestamp=now, should_evolve=False,
                max_level=TriggerLevel.MILD.value, overall_score=overall,
            )
            try:
                _touch_state(_early, False)
            except Exception:
                _log.exception("metacog state error")
            return _early

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
                # 2026-10-19：这两个动作全代码没有执行者，留着只会每轮重复"建议"。
                # 待清理量仍在 memory_health 的 reason 里可见（stale entities N (x%)）。
                pass
        report = MetaCogReport(
            timestamp=now,
            should_evolve=overall >= evolve_threshold,
            max_level=max_level,
            overall_score=overall,
            triggers=triggers,
            suggested_actions=actions,
        )
        # 2026-10-19：状态未变就不落盘（此前每轮都 append，2558 条同内容记录）
        _recorded = _should_record(self._history[-1] if self._history else None, report)
        self._history.append(report)
        if _recorded:
            # ── Persist history to file ──
            try:
                from ..utils import METACORE_DIR
                _hist_file = METACORE_DIR / "metacog_history.jsonl"
                _hist_file.parent.mkdir(parents=True, exist_ok=True)
                with open(str(_hist_file), "a") as _fh:
                    _fh.write(report.to_json() + "\n")
            except Exception:
                _log.exception("metacog error")
        try:
            _touch_state(report, _recorded)
        except Exception:
            _log.exception("metacog state error")
        return report

    @staticmethod
    def _check_success_rate(sr7: float, sr3: float, sr1: float) -> TriggerResult:
        from .calibration import get_calibration as _get_cal
        cal = _get_cal()
        score, level, reasons = 0.0, TriggerLevel.MILD, []
        c1 = cal.get("metacog", "success_rate_1d_critical", default=0.70)
        c3 = cal.get("metacog", "success_rate_3d_moderate", default=0.80)
        c7 = cal.get("metacog", "success_rate_7d_warning", default=0.90)
        if sr1 is not None and sr1 < c1:
            score, level = 0.9, TriggerLevel.CRITICAL
            reasons.append(f"1d {sr1:.0%}<{c1:.0%}")
        if sr3 is not None and sr3 < c3:
            score = max(score, 0.7)
            level = TriggerLevel.MODERATE if level.value != "critical" else level
            reasons.append(f"3d {sr3:.0%}<{c3:.0%}")
        if sr7 is not None and sr7 < c7:
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
            import sqlite3 as _sq
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

            # Metric 1（2026-10-19 改口径）：**年龄不是缺陷**。知识库本来就该越积越旧，
            # 旧口径把 ">30 天未锁" 当降级（1828/2558 = 71% ⇒ 0.5 分）等于惩罚"积累"。
            # 现在年龄只进 details，不参与打分；打分改用"不可用条目"（内容为空 = 真缺陷）。
            _empty_value = _db.execute(
                "SELECT COUNT(*) FROM entities WHERE value IS NULL OR length(trim(value)) = 0"
            ).fetchone()[0]
            _unusable_score = _unusable_ratio_score(_total_entities, _empty_value)
            if _unusable_score > 0:
                score = max(score, _unusable_score)
                reasons.append(
                    f"unusable entries {_empty_value} "
                    f"({_empty_value / max(_total_entities, 1):.0%} empty)")
            stale_ratio = _expired_entities / max(_total_entities, 1)  # 仅记录

            # Metric 2（同日改口径）：事件"旧"同理，不参与打分
            old_event_ratio = _old_events / max(_total_events, 1)

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
                "empty_value": _empty_value,
                "old_30d_entities": _expired_entities,   # 仅信息：不作为降级依据
                "old_30d_ratio": round(stale_ratio, 3),
                "locked_entities": _locked_entities,
                "total_events": _total_events,
                "old_event_ratio": round(old_event_ratio, 3),
            },
        )

    # ── Signal 7: Snapshot trend ──────────────────────

    def _check_snapshot_trend(self) -> TriggerResult:
        # Auto-detect trend from learner status + memory DB
        try:
            import json as _js, sqlite3 as _sq
            from ..utils import METACORE_DIR as _md, LEARNER_STATUS

            # 1. Learner liveness（判活看心跳；loop_count 只作趋势佐证）
            learner_cycles = 0
            _live, _hb_age = "unknown", -1.0
            if LEARNER_STATUS.exists():
                try:
                    st = _js.loads(LEARNER_STATUS.read_text())
                    learner_cycles = st.get("loop_count", st.get("cycles", 0)) or 0
                    _live, _hb_age = _learner_liveness(st)
                except Exception:
                    _live, _hb_age = "unknown", -1.0

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

            # If the learner looks dead (heartbeat stale / no status at all)
            if _live == "stale":
                score = max(score, 0.3)
                reasons.append(f"learner heartbeat stale {int(_hb_age)}s")
            elif _live == "unknown" and learner_cycles == 0:
                score = max(score, 0.3)
                reasons.append("learner loop not running (no status)")
            # 在跑就什么都不说：让下面的 trend ok 分支照常工作

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
    def _check_belief_health(belief_health: Optional[Dict[str, Any]] = None) -> TriggerResult:
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

        # 2026-10-19：形状归一化。调用方（learn/loop.py:805）传的是**能力统计**
        # {"count","high_confidence","low_confidence","total_evidence"}，而这里原按
        # {信念名: 健康度} 解释 ⇒ 键名 "low_confidence" 被当成"一条健康度 0 的信念"，
        # 于是永久 critical（reason 里那句 critical: low_confidence 就是键名）。
        # 真实数值曾是 success_rate=1.0 / task_count=114 万。
        if {"count", "high_confidence"} <= set(belief_health):
            _cnt = int(belief_health.get("count") or 0)
            _high = int(belief_health.get("high_confidence") or 0)
            if _cnt <= 0:
                return TriggerResult(
                    signal_name="belief_health", triggered=False, score=0.0,
                    reason="no beliefs yet",
                )
            _ratio = _high / _cnt
            if _ratio < critical_th:
                return TriggerResult(
                    signal_name="belief_health", level=TriggerLevel.CRITICAL,
                    triggered=True, score=0.9,
                    reason=f"critical: high-confidence ratio {_ratio:.0%} (n={_cnt})",
                    details={"count": _cnt, "high_confidence": _high, "ratio": round(_ratio, 4)},
                )
            if _ratio < unhealthy_th:
                return TriggerResult(
                    signal_name="belief_health", level=TriggerLevel.MODERATE,
                    triggered=True, score=0.5,
                    reason=f"unhealthy: high-confidence ratio {_ratio:.0%} (n={_cnt})",
                    details={"count": _cnt, "high_confidence": _high, "ratio": round(_ratio, 4)},
                )
            return TriggerResult(
                signal_name="belief_health", triggered=False, score=0.0,
                reason=f"ok (high-conf {_ratio:.0%}, n={_cnt})",
                details={"count": _cnt, "high_confidence": _high, "ratio": round(_ratio, 4)},
            )

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

    # ── Signal 10: Retrieval gap（用户问了，但知识没答上）──────────
    def _check_retrieval_gap(self) -> TriggerResult:
        """近 24h 的检索空手率。样本不足不出分（防"7 条数据算 100%"）。"""
        try:
            from .blindspots import stats as _bs
            s = _bs(24)
        except Exception as e:  # noqa: BLE001
            return TriggerResult(signal_name="retrieval_gap", triggered=False,
                                 score=0.0, reason=f"stats failed: {e}")
        n = int(s.get("total") or 0)
        ratio = float(s.get("miss_ratio") or 0.0)
        min_n = int(self._cw("retrieval_gap_min_samples", default=10))
        ex = "; ".join(s.get("top_queries") or [])[:60]
        if n < min_n:
            return TriggerResult(signal_name="retrieval_gap", triggered=False, score=0.0,
                                 reason=f"样本不足 (n={n}/{min_n})", details=s)
        if ratio >= 0.8:
            return TriggerResult(signal_name="retrieval_gap", level=TriggerLevel.CRITICAL,
                                 triggered=True, score=0.9,
                                 reason=f"空手率 {ratio:.0%} ({s['miss']}/{n}) 例: {ex}", details=s)
        if ratio >= 0.5:
            return TriggerResult(signal_name="retrieval_gap", level=TriggerLevel.MODERATE,
                                 triggered=True, score=0.6,
                                 reason=f"空手率 {ratio:.0%} ({s['miss']}/{n}) 例: {ex}", details=s)
        return TriggerResult(signal_name="retrieval_gap", triggered=False, score=0.0,
                             reason=f"空手 {s['miss']}/{n} ({ratio:.0%})", details=s)

    # ── Signal 9: Execution health (expert workers, heartbeat) ──

    def _check_execution_health(self) -> TriggerResult:
        """Check expert worker health and system heartbeat status."""
        score, level, reasons = 0.0, TriggerLevel.MILD, []
        try:
            from aelvoxim.experts.orchestrator import ExpertOrchestrator
            eo = ExpertOrchestrator()
            health = getattr(eo, "_expert_health", {})
            if health:
                total = len(health)
                failures = sum(1 for h in health.values()
                               if h.get("failures", 0) >= 5 and h.get("runs", 0) > 0)
                if failures > 0:
                    fail_ratio = failures / total
                    score = min(1.0, fail_ratio * 3)
                    if fail_ratio > 0.5:
                        level = TriggerLevel.CRITICAL
                    elif fail_ratio > 0.3:
                        level = TriggerLevel.MODERATE
                    reasons.append(f"{failures}/{total} experts failing")
        except Exception:
            pass
        if not reasons:
            reasons.append("execution layer ok")
        trigger = self._cw("execution_health_trigger", default=0.20)
        return TriggerResult(
            signal_name="execution_health",
            level=level, triggered=score > trigger, score=score,
            reason="; ".join(reasons),
        )

    def get_history(self, limit: int = 10) -> List[Dict]:
        return [r.to_dict() for r in self._history[-limit:]]

    def load_history(self) -> None:
        """Load persisted metacog history from JSONL file into memory."""
        try:
            from ..utils import METACORE_DIR
            _hist_file = METACORE_DIR / "metacog_history.jsonl"
            if _hist_file.exists():
                with open(str(_hist_file)) as _fh:
                    for line in _fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            # Reconstruct MetaCogReport from dict
                            triggers = []
                            for td in data.get("triggers", []):
                                t = TriggerResult(
                                    signal_name=td.get("signal_name", ""),
                                    level=TriggerLevel(td.get("level", "mild")),
                                    triggered=td.get("triggered", False),
                                    score=td.get("score", 0.0),
                                    reason=td.get("reason", ""),
                                    details=td.get("details", {}),
                                )
                                triggers.append(t)
                            report = MetaCogReport(
                                timestamp=data.get("timestamp", ""),
                                should_evolve=data.get("should_evolve", False),
                                max_level=data.get("max_level", "mild"),
                                overall_score=data.get("overall_score", 0.0),
                                triggers=triggers,
                                suggested_actions=data.get("suggested_actions", []),
                            )
                            self._history.append(report)
                        except Exception:
                            _log.exception("metacog error")
        except Exception:
            _log.exception("metacog error")


__all__ = [
    "MetaCogTrigger", "MetaCogReport", "TriggerResult",
    "TriggerLevel", "FALLBACK_WEIGHTS",
]
