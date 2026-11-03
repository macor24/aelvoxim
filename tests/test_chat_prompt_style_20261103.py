"""人设切换判据（2026-11-03）

用户要求"做回去 AI 味之前的对话提示"。三代人设都在代码里，用 AELVOXIM_CHAT_PROMPT 切：
  terse（默认，10-19 起）  short & human
  classic（7-09 ~ 10-19）  cognitive-brain + REASONING GUIDELINES + CAPABILITIES
  plain（7-09 之前）       intelligent, friendly AI assistant + REASONING GUIDELINES
本文件锁住：默认不变、三个开关各自可用、失效句不再出现、显式 system_msg 优先。
"""
import importlib
import os


def _fresh(monkeypatch, value=None):
    import aelvoxim.server.service_chat as sc
    if value is None:
        monkeypatch.delenv("AELVOXIM_CHAT_PROMPT", raising=False)
    else:
        monkeypatch.setenv("AELVOXIM_CHAT_PROMPT", value)
    importlib.reload(sc)
    return sc.build_system_prompt(None)


def test_default_is_terse(monkeypatch):
    p = _fresh(monkeypatch)
    assert "How to talk" in p
    assert "REASONING GUIDELINES" not in p


def test_classic_has_reasoning_and_capabilities(monkeypatch):
    p = _fresh(monkeypatch, "classic")
    assert "REASONING GUIDELINES" in p and "CAPABILITIES" in p
    assert "How to talk" not in p


def test_plain_has_reasoning_without_capabilities(monkeypatch):
    p = _fresh(monkeypatch, "plain")
    assert "friendly AI assistant" in p
    assert "REASONING GUIDELINES" in p
    assert "CAPABILITIES" not in p


def test_no_dead_desktop_capability(monkeypatch):
    """Gateway 10 月已摘除，人设里不许再承诺操作桌面。"""
    for style in ("classic", "plain", "terse"):
        p = _fresh(monkeypatch, style)
        assert "desktop" not in p.lower(), f"{style} 仍在承诺桌面控制"


def test_unknown_value_falls_back_to_terse(monkeypatch):
    p = _fresh(monkeypatch, "whatever")
    assert "How to talk" in p


def test_explicit_system_message_wins(monkeypatch):
    import aelvoxim.server.service_chat as sc
    monkeypatch.setenv("AELVOXIM_CHAT_PROMPT", "classic")
    importlib.reload(sc)
    assert sc.build_system_prompt("CUSTOM") == "CUSTOM"
