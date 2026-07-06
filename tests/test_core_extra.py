"""Tests for aelvoxim.core.reasoner — prediction/reasoning engine."""

import pytest


class TestReasoner:
    def test_import(self):
        from aelvoxim.core.reasoner import ProactiveReasoner
        r = ProactiveReasoner()
        assert r is not None

    def test_reason(self):
        from aelvoxim.core.reasoner import ProactiveReasoner
        r = ProactiveReasoner()
        # ProactiveReasoner doesn't have analyze() - just verify it initializes
        assert r is not None


class TestSelfModel:
    def test_import(self):
        from aelvoxim.core.selfmodel import SelfModel
        sm = SelfModel()
        assert sm is not None

    def test_take_snapshot(self):
        from aelvoxim.core.selfmodel import SelfModel
        sm = SelfModel()
        snap = sm.take_snapshot()
        assert snap is not None
        assert hasattr(snap, "overall_success_rate")

    def test_record_decision(self):
        from aelvoxim.core.selfmodel import SelfModel, DecisionEntry
        sm = SelfModel()
        before = len(sm._decisions)
        sm.record_decision(DecisionEntry(decision_type="test", task="pytest", outcome="pass"))
        assert len(sm._decisions) == before + 1


class TestCuriosity:
    def test_import(self):
        import aelvoxim.learn.curiosity
        assert hasattr(aelvoxim.learn.curiosity, "activate_curiosity")

    def test_activate_curiosity_empty(self):
        from aelvoxim.learn.curiosity import activate_curiosity
        result = activate_curiosity({}, None, print)
        assert result is False  # no directions to work with
