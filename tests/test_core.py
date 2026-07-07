"""Tests for metacore.core modules"""

from aelvoxim.core.selfmodel import SelfModel, DecisionEntry


def test_selfmodel_record():
    sm = SelfModel()
    before = len(sm._decisions)
    sm.record_decision(DecisionEntry(decision_type="verify", task="test", outcome="pass"))
    assert len(sm._decisions) == before + 1


def test_selfmodel_snapshot():
    sm = SelfModel()
    snap = sm.take_snapshot()
    assert snap is not None
    assert snap.overall_success_rate >= 0
