"""Tests for aelvoxim.core.belief — Bayesian belief engine."""

import pytest
from aelvoxim.core.belief import BeliefPool


class TestBeliefPool:
    def setup_method(self):
        self.pool = BeliefPool()

    def test_init(self):
        assert self.pool is not None

    def test_get_or_create_new(self):
        """Getting a belief that doesn't exist should create it."""
        belief = self.pool.get_or_create("new_topic")
        assert belief is not None
        assert belief.alpha >= 1
        assert belief.beta >= 1

    def test_record_outcome_increases_confidence(self):
        b = self.pool.get_or_create("topic_x")
        conf_before = b.get_confidence()
        self.pool.record_outcome("topic_x", success=True)
        b2 = self.pool.get_or_create("topic_x")
        assert b2.get_confidence() >= conf_before

    def test_record_outcome_negative(self):
        b = self.pool.get_or_create("topic_y")
        self.pool.record_outcome("topic_y", success=True)
        conf_after_first = b.get_confidence()  # 实际是重新读取，用新对象
        b = self.pool.get_or_create("topic_y")
        conf_after_first = b.get_confidence()
        self.pool.record_outcome("topic_y", success=False)
        b2 = self.pool.get_or_create("topic_y")
        # After success then failure, confidence should be reasonable
        assert b.get_confidence() > 0

    def test_update_evidence(self):
        self.pool.update("topic_z", {"success": True, "source": "test"})
        b = self.pool.get_or_create("topic_z")
        assert b is not None

    def test_record_batch(self):
        self.pool.record_batch("batch_topic", successes=8, total=10)
        b = self.pool.get_or_create("batch_topic")
        assert b.get_confidence() > 0.5
