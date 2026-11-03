"""Tests for metacore.memory.scorer — confidence, TTL, confirmation."""

from aelvoxim.memory.scorer import (
    compute_confidence,
    detect_ttl,
    detect_confirmation,
    needs_confirmation,
    detect_clear_command,
)


def test_confidence_person_first_time():
    """First-time person mention should have base confidence (~0.40)."""
    c = compute_confidence(tag="person", text="我叫大力哥", mention_count=1)
    assert 0.3 <= c <= 0.6, f"Expected ~0.40, got {c}"


def test_confidence_emotional_high():
    """Emotional text with strong keywords should boost confidence."""
    c = compute_confidence(tag="emotion_high", text="我超爱喝咖啡", mention_count=1)
    assert c >= 0.5, f"Expected >= 0.5, got {c}"


def test_confidence_repetition():
    """Repeated mentions should increase confidence."""
    c1 = compute_confidence(tag="person", text="大力哥", mention_count=1)
    c2 = compute_confidence(tag="person", text="大力哥", mention_count=3)
    assert c2 > c1, f"Expected c2 > c1, got c1={c1} c2={c2}"


def test_detect_ttl_week():
    """TTL should detect '保留一周' as 7 days."""
    ttl = detect_ttl("记住这个信息保留一周")
    assert ttl == 7, f"Expected TTL=7, got {ttl}"


def test_detect_ttl_forever():
    """TTL should detect '永久' as -1."""
    ttl = detect_ttl("永久记住这个信息")
    assert ttl == -1, f"Expected TTL=-1, got {ttl}"


def test_detect_ttl_none():
    """No TTL keywords should return None."""
    ttl = detect_ttl("今天天气不错")
    assert ttl is None, f"Expected None, got {ttl}"


def test_detect_confirmation_yes():
    """Positive confirmation should return True."""
    assert detect_confirmation("是的，记住了") is True
    assert detect_confirmation("对，没错") is True


def test_detect_confirmation_no():
    """Negative confirmation should return False."""
    result = detect_confirmation("不对，你记错了")
    assert result is False, f"Expected False, got {result}"


def test_detect_clear_command():
    """Clear memory command should be detected."""
    assert detect_clear_command("清理我的记忆") is True
    assert detect_clear_command("清除所有记忆") is True
    assert detect_clear_command("今天天气") is False


# ── 2026-11-03 安全回归 (CodeQL #152): ReDoS ─────────────────────────────
def test_detect_ttl_redos_is_linear():
    """长数字串 + 无单位 ⇒ 必须快速返回 None（改前 16k 数字要 3.5s）。"""
    import time
    for n in (4000, 16000):
        t0 = time.time()
        assert detect_ttl("1" * n + "x") is None
        dt = time.time() - t0
        assert dt < 0.05, "n=%d 用了 %.3fs ⇒ 二次回溯仍在" % (n, dt)


def test_detect_ttl_semantics_unchanged():
    """封顶数字段不影响正常语义。"""
    assert detect_ttl("保留3天") == 3
    assert detect_ttl("记住这个信息保留一周") == 7
    assert detect_ttl("keep 2 天") == 2
    assert detect_ttl("永久记住") == -1
    assert detect_ttl("今天天气不错") is None
    # 英文单位(months/weeks)本来就不支持 —— 既有行为, 不是本次改动引入
    assert detect_ttl("keep this 2 months") is None
    # 超长数字(>6 位)不再当 TTL —— 本来就不是合法值
    assert detect_ttl("保留1234567天") is None
