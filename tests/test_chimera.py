"""Tests for aelvoxim.chimera — intent classification and text utilities.

Covers:
- _bilingual (zh/en text generation)
- _extract_text_params (parameter extraction from text)
- Intent classifier basic routing
"""
import pytest
from aelvoxim.chimera.routes import (
    _bilingual,
    _extract_text_params,
)
from aelvoxim.chimera.intent_classifier import (
    IntentClassifier,
    IntentResult,
)


class TestBilingual:
    """Bilingual text generation (zh/en)."""

    def test_returns_chinese(self):
        result = _bilingual("你好", "hello", "zh")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_english(self):
        result = _bilingual("你好", "hello", "en")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_input(self):
        result = _bilingual("", "", "zh")
        assert isinstance(result, str)

    def test_invalid_lang_fallback(self):
        result = _bilingual("你好", "hello", "fr")
        assert isinstance(result, str)
        assert len(result) > 0


class TestExtractTextParams:
    """Extract structured params from user text."""

    def test_empty_content(self):
        result = _extract_text_params("")
        assert isinstance(result, dict)

    def test_simple_text(self):
        result = _extract_text_params("帮我写一封邮件")
        assert isinstance(result, dict)

    def test_with_keywords(self):
        result = _extract_text_params("用Python写一个排序算法")
        assert isinstance(result, dict)

    def test_long_text(self):
        result = _extract_text_params("写一篇关于人工智能的文章" * 10)
        assert isinstance(result, dict)


class TestIntentClassifier:
    """Intent classification from user queries."""

    def setup_method(self):
        self.classifier = IntentClassifier()

    def test_code_intent(self):
        result = self.classifier.classify("写一个Python函数")
        assert isinstance(result, IntentResult)

    def test_chat_intent(self):
        result = self.classifier.classify("你好，今天天气怎么样？")
        assert isinstance(result, IntentResult)

    def test_empty_query(self):
        result = self.classifier.classify("")
        assert isinstance(result, IntentResult)

    def test_long_query(self):
        result = self.classifier.classify("A" * 2000)
        assert isinstance(result, IntentResult)


class TestIntentResult:
    """IntentResult dataclass behavior."""

    def test_is_execute_property(self):
        r = IntentResult(type="execute", confidence=0.0)
        assert r.is_execute is True

    def test_is_query_property(self):
        r = IntentResult(type="query", confidence=0.0)
        assert r.is_query is True

    def test_is_chat_by_default(self):
        r = IntentResult()
        assert r.is_execute is False
        assert r.is_query is False

    def test_confidence_range(self):
        r = IntentResult(confidence=0.85)
        assert r.confidence == 0.85
