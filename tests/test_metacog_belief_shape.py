"""metacog belief_health 的形状契约（2026-10-19）。

调用方传的是能力统计 {count, high_confidence, low_confidence, total_evidence}，
而早先的实现按 {信念名: 健康度} 解释 ⇒ 键名 `low_confidence` 被当成"一条健康度 0
的信念"，reason 变成 `critical: low_confidence`，整体分数被顶到 critical。
这组测试锁住两种形状都被正确处理。
"""
from aelvoxim.core.metacog import MetaCogTrigger


def _stats(count=1000, high=999, low=1):
    return {"count": count, "high_confidence": high, "low_confidence": low,
            "total_evidence": count + 7}


def test_stats_shape_healthy_is_not_triggered():
    """真实生产值：success_rate 1.0 / task_count 114 万。"""
    r = MetaCogTrigger._check_belief_health(_stats(count=1142674, high=1142674, low=0))
    assert r.triggered is False, r.reason
    assert r.score == 0.0


def test_stats_shape_low_ratio_is_critical():
    r = MetaCogTrigger._check_belief_health(_stats(count=100, high=10, low=90))
    assert r.triggered is True and r.score >= 0.9
    assert "ratio 10%" in r.reason


def test_stats_shape_mid_ratio_is_moderate():
    r = MetaCogTrigger._check_belief_health(_stats(count=100, high=40, low=60))
    assert r.triggered is True and 0.4 <= r.score <= 0.6


def test_stats_shape_with_zero_count_is_not_triggered():
    r = MetaCogTrigger._check_belief_health(_stats(count=0, high=0, low=0))
    assert r.triggered is False and "no beliefs" in r.reason


def test_legacy_per_belief_shape_still_works():
    """旧形状 {信念名: 健康度} 必须保持原语义。"""
    r = MetaCogTrigger._check_belief_health({"belief_a": 0.1, "belief_b": 0.9})
    assert r.triggered is True and "belief_a" in r.reason


def test_empty_input_not_triggered():
    assert MetaCogTrigger._check_belief_health({}).triggered is False
