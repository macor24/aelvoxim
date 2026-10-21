"""窗口成功率：样本不足就返回 None，不退化成 lifetime（2026-10-21）。

旧实现：learner 重启清空内存快照 → _window_rate 返回整体成功率（偏乐观）
→ 重启后一段时间内永远不会报成功率问题。
"""
import time

from aelvoxim.core.metacog import window_success_rate


def test_not_enough_samples_returns_none():
    now = time.time()
    assert window_success_rate([], 24, now) is None
    assert window_success_rate([(now - 60, 10, 1)], 24, now) is None


def test_samples_outside_window_are_ignored():
    now = time.time()
    old = [(now - 10 * 86400, 100, 100), (now - 9 * 86400, 200, 200)]
    assert window_success_rate(old, 24, now) is None


def test_rate_uses_window_delta():
    now = time.time()
    snaps = [(now - 3600, 100, 100), (now - 1800, 140, 110), (now - 60, 180, 120)]
    # 从第一条到第三条：+80 条目 / +20 轮 = 4.0
    assert window_success_rate(snaps, 24, now) == 4.0


def test_zero_cycles_does_not_divide_by_zero():
    now = time.time()
    snaps = [(now - 600, 5, 5), (now - 60, 9, 5)]
    assert window_success_rate(snaps, 24, now) == 4.0
