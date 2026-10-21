"""二阶认知读数拆解（2026-10-20）。

三条被拆开的语义：
1. 早退（分数低于 record_threshold）也要写 state —— 否则"健康地安静"和"死了"一样。
2. learning 成功率不再当 score 地板（旧写法 0.84*0.5=0.42 > 熔断阈值 0.3，
   使 L6 永久无法触发）；它单列成 learning_health。
3. 自省没有信号时 monitor 塞的 heartbeat_ok 属于合成信号，单列，不混进 triggers 语义。
"""
import json
import types

from aelvoxim.core import metacog as mc
from aelvoxim.core import metacog_monitor as mm
from aelvoxim.core.metacog import MetaCogTrigger


class _Cap:
    def __init__(self, rate=0.84, n=100):
        self.success_rate = rate
        self.task_count = n


class _FakeSelfModel:
    def __init__(self, *a, **k):
        self._capabilities = {"learning": _Cap()}


class _Report:
    def __init__(self, score=0.0):
        self.overall_score = score
        self.triggers = []
        self.should_evolve = False


def test_early_return_writes_state(tmp_path, monkeypatch):
    import aelvoxim.utils as u
    monkeypatch.setattr(u, "METACORE_DIR", tmp_path)
    r = MetaCogTrigger().evaluate(
        success_rate_7d=1.0, success_rate_3d=1.0, success_rate_1d=1.0,
        days_since_last_improvement=0, repeat_error_count=0,
        external_signal_strength=0.0,
        belief_health={"count": 100, "high_confidence": 100, "low_confidence": 0,
                       "total_evidence": 120},
    )
    assert r.overall_score < 0.05, "测试前提：分数应低于 record_threshold"
    state = json.loads((tmp_path / "metacog_state.json").read_text())
    assert state["last_seen"], "早退也必须更新 last_seen"
    assert not (tmp_path / "metacog_history.jsonl").exists(), "早退不该写历史"


def test_monitor_separates_scores(tmp_path, monkeypatch):
    import aelvoxim.core.selfmodel as smmod
    monkeypatch.setattr(smmod, "SelfModel", _FakeSelfModel)
    m = mm.MetaCogMonitor()
    rep = m.evaluate(learner_stats={"total_cycles": 100, "total_entries": 90},
                     belief_stats={}, metacog_report=_Report(0.0))
    assert rep["meta_score"] == 0.0
    assert rep["learning_health"] == 0.84
    assert rep["synthetic_triggers"] == ["heartbeat_ok"]
    assert rep["score"] <= 0.1 + 1e-9, f"score 不该被 learning 抬到 0.42，实际 {rep['score']}"
    assert rep["blended_score"] >= 0.4


def test_breaker_is_observe_only(tmp_path, monkeypatch):
    """新语义：只有"看不见"（缺输入）才计数；分数低不算。"""
    import aelvoxim.core.selfmodel as smmod
    monkeypatch.setattr(smmod, "SelfModel", _FakeSelfModel)
    monkeypatch.setattr(mm, "_ETHICS_LOG_DIR", tmp_path)
    m = mm.MetaCogMonitor()
    m._no_obs_streak = int(mm._cal("l6_no_obs_cycles", 6)) - 1
    rep = m.evaluate(learner_stats=None, belief_stats=None, metacog_report=_Report(0.0))
    assert m.is_tripped() is False, "观察模式不应真跳闸"
    assert "l6_would_trip" in rep["suggested_actions"]
    assert rep["missing_inputs"] == ["learner_stats", "belief_stats"]


def test_low_score_alone_never_counts(tmp_path, monkeypatch):
    """核心纠正：分数 0.0（无信号）但输入齐全 → 不计数、不告警。"""
    import aelvoxim.core.selfmodel as smmod
    monkeypatch.setattr(smmod, "SelfModel", _FakeSelfModel)
    monkeypatch.setattr(mm, "_ETHICS_LOG_DIR", tmp_path)
    m = mm.MetaCogMonitor()
    rep = None
    for _ in range(10):
        rep = m.evaluate(learner_stats={"total_cycles": 10, "total_entries": 9},
                         belief_stats={"count": 10, "high_confidence": 10,
                                       "total_evidence": 12},
                         metacog_report=_Report(0.0))
    assert rep is not None
    assert rep["no_obs_streak"] == 0
    assert not (tmp_path / "l6_events.jsonl").exists()


def test_partial_gap_resets_streak(tmp_path, monkeypatch):
    import aelvoxim.core.selfmodel as smmod
    monkeypatch.setattr(smmod, "SelfModel", _FakeSelfModel)
    m = mm.MetaCogMonitor()
    m.evaluate(learner_stats=None, belief_stats={}, metacog_report=_Report(0.0))
    assert m._no_obs_streak == 1
    rep = m.evaluate(learner_stats={"total_cycles": 1},
                     belief_stats={"count": 1, "total_evidence": 2},
                     metacog_report=_Report(0.0))
    assert rep["no_obs_streak"] == 0
