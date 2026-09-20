"""The cognition tick must be reachable without a successful learning cycle.

2026-09-19: the tick was only called at the tail of _learn_one_cycle(), after a
quality-skip branch that returned early on every task. Production stalled, so the
metacog chain (self-assessment, self-model sync, memory maintenance) went dark
for two days with it. These tests pin the timer gate that decouples them.
"""
import time

import pytest


@pytest.fixture()
def bare():
    """A Learner without __init__ side effects (only the tick gate is tested)."""
    from aelvoxim.learn.loop import Learner
    return Learner.__new__(Learner)


def test_fresh_learner_is_due_immediately(bare, monkeypatch):
    """A learner that never completed a cycle must still be able to tick."""
    monkeypatch.setenv("AELVOXIM_COGNITION_MIN_GAP_SEC", "1800")
    assert bare._cognition_tick_due() is True


def test_recent_tick_is_not_due_again(bare, monkeypatch):
    monkeypatch.setenv("AELVOXIM_COGNITION_MIN_GAP_SEC", "1800")
    bare._last_cognition_tick_ts = time.time()
    assert bare._cognition_tick_due() is False


def test_tick_becomes_due_after_the_gap(bare, monkeypatch):
    monkeypatch.setenv("AELVOXIM_COGNITION_MIN_GAP_SEC", "1800")
    bare._last_cognition_tick_ts = time.time() - 1801
    assert bare._cognition_tick_due() is True


def test_gap_is_configurable(bare, monkeypatch):
    bare._last_cognition_tick_ts = time.time() - 61
    monkeypatch.setenv("AELVOXIM_COGNITION_MIN_GAP_SEC", "60")
    assert bare._cognition_tick_due() is True
    monkeypatch.setenv("AELVOXIM_COGNITION_MIN_GAP_SEC", "3600")
    assert bare._cognition_tick_due() is False
