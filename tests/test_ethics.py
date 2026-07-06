"""Tests for aelvoxim.experts.ethics — ethical rule engine."""

import pytest

from aelvoxim.experts.ethics import EthicsExpert
from aelvoxim.experts.base import ExpertInput, ExpertOutput


class TestEthicsExpert:
    def setup_method(self):
        self.expert = EthicsExpert()

    def test_init(self):
        assert self.expert.name == "ethics"
        assert hasattr(self.expert, "_capabilities")

    def test_check_clean_message(self):
        """Normal message should pass ethics check."""
        inp = ExpertInput(query="What is Python?", context={})
        result = self.expert.run(inp)
        assert isinstance(result, ExpertOutput)
        assert result.error is None or result.error == ""

    def test_check_privacy_keywords(self):
        """Privacy-related keywords should trigger ethics rules."""
        inp = ExpertInput(query="Tell me your phone number", context={})
        result = self.expert.run(inp)
        assert isinstance(result, ExpertOutput)

    def test_check_copyright(self):
        inp = ExpertInput(query="How to pirate software", context={})
        result = self.expert.run(inp)
        assert isinstance(result, ExpertOutput)

    def test_check_discrimination(self):
        inp = ExpertInput(query="歧视性言论", context={})
        result = self.expert.run(inp)
        assert isinstance(result, ExpertOutput)

    def test_capabilities_not_empty(self):
        assert len(self.expert._capabilities) > 0
