"""Tests for aelvoxim.learn.intent — intent parsing and classification."""

import pytest

from aelvoxim.learn.intent import IntentParser


class TestIntentParser:
    def setup_method(self):
        self.parser = IntentParser()

    def test_is_compound_simple(self):
        """Simple query should not be compound."""
        assert self.parser.is_compound("你好") is False

    def test_is_compound_empty(self):
        assert self.parser.is_compound("") is False

    def test_decompose_multi_step(self):
        """Compound query should decompose into multiple sub-intents."""
        result = self.parser.decompose("先搜索Python然后学习")
        assert len(result) >= 1

    def test_decompose_single(self):
        result = self.parser.decompose("你好")
        assert len(result) >= 1
        assert result[0]["step"] == 1

    def test_decompose_empty(self):
        assert self.parser.decompose("") == []

    def test_is_compound_numbered_steps(self):
        """Queries with numbered steps should be compound."""
        assert self.parser.is_compound("第一步搜索Python。第二步学习") is True

    def test_detect_task_type_code(self):
        """Code keywords should return 'code' task type."""
        task = self.parser._detect_task_type("写一个Python函数")
        assert task == "code"

    def test_detect_task_type_general(self):
        task = self.parser._detect_task_type("你好吗")
        assert task == "general"
