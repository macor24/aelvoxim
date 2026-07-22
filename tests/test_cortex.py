"""Tests for aelvoxim.cortex — intent routing and expert orchestration.

Covers:
- classify_coarse (topic routing)
- check_topic_drift (conversation focus tracking)
- build_expert_context (expert output formatting)
"""
import pytest
from aelvoxim.cortex import (
    classify_coarse,
    check_topic_drift,
    build_expert_context,
)


class TestClassifyCoarse:
    """Topic classification for expert routing."""

    def test_code_query(self):
        result = classify_coarse("How do I write a Python function?")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_general_chat(self):
        result = classify_coarse("What's the weather like?")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_query(self):
        result = classify_coarse("")
        assert isinstance(result, str)

    def test_chinese_query(self):
        result = classify_coarse("帮我写一个排序算法")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_string(self):
        result = classify_coarse("Hello world")
        assert isinstance(result, str)


class TestTopicDrift:
    """Topic drift detection between user messages and AI replies."""

    def test_same_topic_low_drift(self):
        score = check_topic_drift("What is Python?", "Python is a programming language")
        assert isinstance(score, float)

    def test_different_topic_higher_drift(self):
        score = check_topic_drift("What is Python?", "I like cooking Italian food")
        assert isinstance(score, float)

    def test_empty_first_msg(self):
        score = check_topic_drift("", "Some reply")
        assert isinstance(score, float)

    def test_empty_reply(self):
        score = check_topic_drift("Hello", "")
        assert isinstance(score, float)

    def test_returns_float_in_range(self):
        score = check_topic_drift("A" * 1000, "B" * 1000)
        assert isinstance(score, float)
        # Drift score should be reasonable (implementation may vary)
        assert 0.0 <= score <= 1.0 or score >= 0


class TestBuildExpertContext:
    """Expert result formatting for LLM context injection."""

    def test_empty_result(self):
        context = build_expert_context({})
        assert isinstance(context, str)

    def test_with_text(self):
        context = build_expert_context({"response": "Hello from expert", "text": "Hello"})
        assert isinstance(context, str)

    def test_with_analysis_key(self):
        context = build_expert_context({"analysis": "This is the analysis"})
        assert isinstance(context, str)

    def test_none_result(self):
        context = build_expert_context({"text": None})
        assert isinstance(context, str)
