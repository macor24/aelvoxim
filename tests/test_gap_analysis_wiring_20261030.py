"""P0-6 判据：缺口分析的开关与调用必须同处。

修前：`analyze_knowledge_gaps` 只在 `if gap_analysis_enabled` 内 import，
却在 if 外无条件调用 ⇒ 社区版每次 cognition tick 抛 NameError，被
`except Exception` 吞成一条 `loop error`。

运行：.venv/bin/python -m pytest tests/test_gap_analysis_wiring_20261030.py -q
"""
from __future__ import annotations

import pytest

from aelvoxim.learn import loop as loop_mod


def _learner():
    # skip_load=True：不读方向配置，避免依赖本机数据目录
    return loop_mod.Learner(enable_auto_discover=False, skip_load=True)


@pytest.fixture()
def gap_recorder(monkeypatch):
    calls = []

    def _fake(directions, topics, entries, **kwargs):
        calls.append((directions, topics, entries, kwargs))
        return {"recommendations": []}

    monkeypatch.setattr("aelvoxim.learn.gap_analysis.analyze_knowledge_gaps", _fake)
    return calls


def test_switch_off_never_calls(monkeypatch, gap_recorder):
    monkeypatch.setattr("aelvoxim.server.edition.get", lambda key, default=None: False)

    _learner()._run_gap_analysis()  # 修前这里抛 NameError

    assert gap_recorder == []


def test_switch_on_calls_once_with_expected_args(monkeypatch, gap_recorder):
    monkeypatch.setattr("aelvoxim.server.edition.get", lambda key, default=None: True)
    # 隔离本机知识库：不读真实数据目录
    monkeypatch.setattr("aelvoxim.learn.knowledge.KnowledgeBase.get_all_active", staticmethod(lambda: []))

    _learner()._run_gap_analysis()

    assert len(gap_recorder) == 1
    _dirs, _topics, _entries, kwargs = gap_recorder[0]
    assert _topics == {}
    assert _entries == []
    assert kwargs == {"min_entries_threshold": 5, "saturation_threshold": 0.8}


def test_kb_topic_counts_are_passed_through(monkeypatch, gap_recorder):
    monkeypatch.setattr("aelvoxim.server.edition.get", lambda key, default=None: True)
    monkeypatch.setattr(
        "aelvoxim.learn.knowledge.KnowledgeBase.get_all_active",
        staticmethod(lambda: [{"topic": "k8s"}, {"topic": "k8s"}, {"topic": ""}]),
    )

    _learner()._run_gap_analysis()

    _dirs, topics, _entries, _kwargs = gap_recorder[0]
    assert topics == {"k8s": 2}


def test_switch_on_recommendation_creates_plan(monkeypatch):
    created = []

    monkeypatch.setattr("aelvoxim.server.edition.get", lambda key, default=None: True)
    monkeypatch.setattr(
        "aelvoxim.learn.gap_analysis.analyze_knowledge_gaps",
        lambda *a, **k: {"recommendations": ["Create direction from user demand: 刀具选型"]},
    )

    class _FakePlanner:
        def create_plan(self, goal, source=""):
            created.append((goal, source))

            class _P:
                id = "plan_test"

            return _P()

    monkeypatch.setattr("aelvoxim.planner.LongTermPlanner", _FakePlanner)

    _learner()._run_gap_analysis()

    assert created == [("刀具选型", "gap_analysis")]


def test_analyzer_exception_does_not_get_swallowed_here(monkeypatch):
    """_run_gap_analysis 自身不吞异常；兜底在 _cognition_tick 的 try 里（日志标签不同）。"""
    monkeypatch.setattr("aelvoxim.server.edition.get", lambda key, default=None: True)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("aelvoxim.learn.gap_analysis.analyze_knowledge_gaps", _boom)

    with pytest.raises(RuntimeError):
        _learner()._run_gap_analysis()
