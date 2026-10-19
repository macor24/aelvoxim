"""metacog 事件去重（2026-10-19）。

背景：每轮检查都 append 一条内容相同的记录，生产堆积 2558 条、监控每半小时报警。
规则：level 变 / score 变 >0.02 / 触发信号集合变 —— 三者任一才落盘。
"""
from aelvoxim.core.metacog import (
    MetaCogReport,
    TriggerLevel,
    TriggerResult,
    _should_record,
)


def _report(score=0.209, level="critical", signals=("belief_health",), ts="t"):
    return MetaCogReport(
        timestamp=ts,
        max_level=level,
        overall_score=score,
        triggers=[TriggerResult(signal_name=s, level=TriggerLevel.MILD, triggered=True, score=0.5)
                  for s in signals],
    )


def test_identical_state_is_not_recorded():
    assert _should_record(_report(), _report()) is False


def test_score_change_above_delta_is_recorded():
    assert _should_record(_report(score=0.209), _report(score=0.25)) is True


def test_tiny_score_change_is_not_recorded():
    """抖动 (<0.02) 不该产生新事件。"""
    assert _should_record(_report(score=0.209), _report(score=0.215)) is False


def test_level_change_is_recorded():
    assert _should_record(_report(level="mild"), _report(level="critical")) is True


def test_triggered_signal_set_change_is_recorded():
    assert _should_record(_report(signals=("belief_health",)),
                          _report(signals=("belief_health", "memory_health"))) is True


def test_first_record_always_written():
    assert _should_record(None, _report()) is True
