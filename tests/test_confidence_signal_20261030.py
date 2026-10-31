"""P0-2 / P0-3 判据：置信度必须来自真实评估分，且流式路径也要记录。

修前：
- P0-2 `_confidence_score` 在 chat_pipeline 开头写死 0.5，此后从不重新赋值
- P0-3 流式端点（生产实际入口）没有任何 record_confidence 调用 ⇒ samples=0

运行：.venv/bin/python -m pytest tests/test_confidence_signal_20261030.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aelvoxim.server import service_chat


@pytest.fixture(autouse=True)
def clean_window():
    service_chat._CONFIDENCE_WINDOW.clear()
    yield
    service_chat._CONFIDENCE_WINDOW.clear()


def test_no_samples_is_unavailable_not_05():
    t = service_chat.get_confidence_trend()

    assert t["samples"] == 0
    assert t["current"] is None, "没样本时不能返回 0.5 冒充读数"
    assert t["trend"] == "unavailable"
    assert t["basis"] == "evaluate.robustness_score"


def test_recorded_score_becomes_current():
    service_chat.record_confidence(0.8)

    t = service_chat.get_confidence_trend()
    assert t["samples"] == 1
    assert t["current"] == 0.8


def test_trend_tracks_real_values_not_constant():
    for s in (0.4, 0.5, 0.9):
        service_chat.record_confidence(s)

    t = service_chat.get_confidence_trend()
    assert t["samples"] == 3
    assert t["current"] == 0.9
    assert t["avg_50"] == pytest.approx(0.6, abs=0.01)


def test_stream_finish_records_from_eval_report(monkeypatch):
    recorded = []
    monkeypatch.setattr(service_chat, "record_confidence", lambda s: recorded.append(s))
    monkeypatch.setattr(
        "aelvoxim.server.chat_monitor.evaluate_conversation",
        lambda **kwargs: {"robustness_score": 42.0, "level": "poor"},
    )
    from aelvoxim.server import routes_chat

    routes_chat._post_stream_finish(
        {"email": "p03@test", "id": "u1"}, "sid-p03", "p03@test", "问题", ["回答内容够长"], 0.0)

    assert recorded == [0.42], "流式收尾必须用评估分记录置信度"


def test_stream_finish_skips_when_eval_fails(monkeypatch):
    recorded = []
    monkeypatch.setattr(service_chat, "record_confidence", lambda s: recorded.append(s))

    def _boom(**kwargs):
        raise RuntimeError("eval down")

    monkeypatch.setattr("aelvoxim.server.chat_monitor.evaluate_conversation", _boom)
    from aelvoxim.server import routes_chat

    routes_chat._post_stream_finish(
        {"email": "p03@test", "id": "u1"}, "sid-p03b", "p03@test", "问题", ["回答"], 0.0)

    assert recorded == [], "拿不到评估分就不记，不能退回默认值"


def test_pipeline_no_hardcoded_confidence():
    """静态守卫：chat_pipeline 里不得再出现写死的 _confidence_score。"""
    src = Path("src/aelvoxim/server/service_chat.py").read_text(encoding="utf-8")

    assert "_confidence_score" not in src
    assert "record_confidence(float(_eval_report.get(\"robustness_score\"" in src
