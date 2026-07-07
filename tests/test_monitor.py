"""Tests for MetaCogMonitor."""

import time

from aelvoxim.core.metacog_monitor import MetaCogMonitor, set_ethics_gate, get_ethics_gate


def test_monitor_rate_limit():
    monitor = MetaCogMonitor()
    monitor._tick_times = []
    monitor.start_tick_count = 0
    for _ in range(10):
        monitor._tick_times.append(time.time())
    monitor._tick_times = monitor._tick_times[-50:]
    result = monitor.check_rate_limit()
    assert result, "Rate limit should trigger after 10 ticks in 24h range"


def test_ethics_gate():
    set_ethics_gate("L5_rate_limit", True, "restored")
    assert get_ethics_gate("L5_rate_limit") is True


def test_mask_api_key():
    """API key masking works in llm error paths."""
    from aelvoxim.learn.llm import _mask_api_key as _m
    result = _m("error: sk-abc123xy34567890")
    assert "..." in result, "API key should be masked: " + repr(result)
    assert _m("normal error") == "normal error", "Normal messages unchanged"
