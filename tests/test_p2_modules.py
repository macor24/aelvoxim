"""Tests for cortex, orchestrator, and DGM-H modules."""

import pytest


class TestDgmh:
    def test_import(self):
        from aelvoxim.core.dgmh import SafetyShield
        shield = SafetyShield()
        assert shield is not None


class TestOrchestrator:
    def test_import(self):
        from aelvoxim.experts.orchestrator import ExpertOrchestrator
        orch = ExpertOrchestrator()
        assert orch is not None

    def test_think(self):
        from aelvoxim.experts.orchestrator import ExpertOrchestrator
        from aelvoxim.experts.base import ExpertInput
        orch = ExpertOrchestrator()
        inp = ExpertInput(query="What is Python?", context={})
        result = orch.think(inp)
        assert "opinion" in result or "blocked" in result


class TestCortex:
    def test_import(self):
        from aelvoxim.cortex import classify_fine
        assert callable(classify_fine)

    def test_classify(self):
        from aelvoxim.cortex import classify_fine
        result = classify_fine("What is Python?")
        assert result is not None
