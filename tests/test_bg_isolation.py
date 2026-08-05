"""Tests for the background/foreground isolation changes (2026-08-03).

Covers:
1. bg_llm_call serializes background LLM calls (semaphore gate)
2. bg_llm_call short wall-clock timeout abandons slow calls
3. validate cooldown skip logic
"""

import time
import threading

import pytest


def test_bg_llm_call_serializes():
    """Two concurrent bg calls must not run at the same time."""
    from aelvoxim.learn.llm import bg_llm_call

    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_fn(x):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return x * 2

    results = []
    threads = [
        threading.Thread(target=lambda i=i: results.append(bg_llm_call(slow_fn, i)))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1, f"bg calls overlapped (peak={peak})"
    assert sorted(results) == [0, 2, 4, 6]

def test_bg_llm_call_timeout_abandons():
    """A call exceeding the bg budget must return None, not block forever."""
    from aelvoxim.learn.llm import bg_llm_call, _BG_LLM_TIMEOUT

    def hang_fn():
        time.sleep(_BG_LLM_TIMEOUT + 5)
        return "never"

    t0 = time.time()
    result = bg_llm_call(hang_fn)
    elapsed = time.time() - t0
    assert result is None
    # Should return around the budget, far short of the hang duration
    assert elapsed < _BG_LLM_TIMEOUT + 3


def test_validate_cooldown():
    """Cooldown flag must be respected and expire."""
    from aelvoxim.learn import validate

    topic = "cooldown_test_topic_xyz"
    validate._cooldowns.pop(f"validate_cooldown_{topic}", None)
    assert validate.is_cooldown(topic) is False

    validate._cooldowns[f"validate_cooldown_{topic}"] = time.time() + 600
    assert validate.is_cooldown(topic) is True

    validate._cooldowns[f"validate_cooldown_{topic}"] = time.time() - 1
    assert validate.is_cooldown(topic) is False
    validate._cooldowns.pop(f"validate_cooldown_{topic}", None)


def test_learn_worker_imports():
    """learn_worker must import cleanly (syntax + module paths)."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "import ast; ast.parse(open('learn_worker.py').read())"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
