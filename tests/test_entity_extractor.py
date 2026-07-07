"""Tests for metacore.server.entity_extractor — name, location, emotion."""

from aelvoxim.server.entity_extractor import extract_entities, _detect_sentiment, _extract_emotion_keywords


def test_extract_name():
    """Extract person name from Chinese sentence."""
    r = extract_entities("我叫大力哥，在做App开发", "")
    names = [e["name"] for e in r["entities"] if e["type"] == "person"]
    assert "大力哥" in names, f"Expected 大力哥 in {names}"


def test_extract_location():
    """Extract location from Chinese sentence."""
    r = extract_entities("我住在深圳，在大公司上班", "")
    locs = [e["name"] for e in r["entities"] if e["type"] == "location"]
    assert "深圳" in locs, f"Expected 深圳 in {locs}"


def test_extract_name_english():
    """Extract person name from English sentence."""
    r = extract_entities("My name is Alice Johnson", "")
    names = [e["name"] for e in r["entities"] if e["type"] == "person"]
    assert any("Alice" in n for n in names), f"Expected Alice in {names}"


def test_extract_preference():
    """Extract preference from Chinese sentence."""
    r = extract_entities("我喜欢喝咖啡", "")
    prefs = [e["name"] for e in r["entities"] if e["type"] == "preference"]
    assert "喝咖啡" in prefs, f"Expected 喝咖啡 in {prefs}"


def test_detect_sentiment_negative():
    """Negative sentiment should be detected first (safety)."""
    assert _detect_sentiment("我烦死了") == "negative"
    assert _detect_sentiment("太失望了") == "negative"
    assert _detect_sentiment("我好难过") == "negative"


def test_detect_sentiment_positive():
    """Positive sentiment should be detected."""
    assert _detect_sentiment("今天太开心了") == "positive"
    assert _detect_sentiment("太棒了") == "positive"
    assert _detect_sentiment("谢谢") == "positive"


def test_detect_sentiment_neutral():
    """Neutral messages should return neutral."""
    assert _detect_sentiment("今天天气不错") == "positive"
    assert _detect_sentiment("什么是AI") == "neutral"


def test_extract_emotion_keywords():
    """Emotion keywords should be extracted."""
    kws = _extract_emotion_keywords("我超爱喝咖啡")
    assert any("超爱" in kw for kw in kws), f"Expected 超爱 keyword in {kws}"
