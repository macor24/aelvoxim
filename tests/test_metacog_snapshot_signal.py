"""metacog 的 learner 判活（2026-10-19）。

背景：原实现读 status.json 的 "cycles" 键——该键不存在（实际是 loop_count），
于是永远取 0、永远判 "learner loop not running"，而 learner 心跳只有 12 秒。
判活改以心跳新鲜度为准；计数器只因重启归零，不能单独用来判死。
"""
from datetime import datetime, timedelta

from aelvoxim.core.metacog import _learner_liveness


def _status(age_s: int = 0, running: bool = True, **extra) -> dict:
    ts = (datetime.now() - timedelta(seconds=age_s)).strftime("%Y-%m-%d %H:%M:%S")
    d = {"running": running, "updated_at": ts}
    d.update(extra)
    return d


def test_fresh_heartbeat_means_running():
    state, age = _learner_liveness(_status(age_s=5, loop_count=412))
    assert state == "running" and age < 60


def test_stale_heartbeat_means_dead():
    state, _ = _learner_liveness(_status(age_s=1800))
    assert state == "stale"


def test_running_false_is_stale_even_with_fresh_heartbeat():
    assert _learner_liveness(_status(age_s=5, running=False))[0] == "stale"


def test_loop_count_alone_is_enough():
    """status.json 没有 cycles 键（真实情况就是只有 loop_count）。"""
    assert _learner_liveness(_status(age_s=3, loop_count=7))[0] == "running"


def test_missing_status_is_unknown():
    assert _learner_liveness({})[0] == "unknown"
    assert _learner_liveness(None)[0] == "unknown"
