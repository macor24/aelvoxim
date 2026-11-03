"""回答尾巴判据（2026-11-03）

旧版在"回答里出现不确定词 + 知识库没命中"时追加英文免责声明，实测在中文回答后面很出戏。
新版：只有**成串的具体数字**（≥3 个 4 位以上数字）且知识库没命中，才按语言加一句短提示。
"""
import aelvoxim.learn.knowledge as kb_mod
from aelvoxim.server.service_chat import verify_response_facts


def _no_kb(monkeypatch):
    monkeypatch.setattr(kb_mod.KnowledgeBase, "search", staticmethod(lambda **kw: []))


def _has_kb(monkeypatch):
    monkeypatch.setattr(kb_mod.KnowledgeBase, "search", staticmethod(lambda **kw: [{"title": "x"}]))


def test_hedged_chinese_answer_gets_no_tail(monkeypatch):
    """模型自己说了"可能/没定论"，不该再被贴英文标签。"""
    _no_kb(monkeypatch)
    text = "这个话题我其实没有定论，可能是上面某一种，想听你的判断。"
    assert verify_response_facts(text, "问", {}) == text


def test_chinese_numbers_get_chinese_note(monkeypatch):
    _no_kb(monkeypatch)
    text = "2024 年出货 12345 台，均价 6789 元，库存 4321 件。"
    out = verify_response_facts(text, "问", {})
    assert "没核实" in out
    assert "Note:" not in out


def test_english_numbers_get_english_note(monkeypatch):
    _no_kb(monkeypatch)
    text = "In 2024 the plant shipped 12345 units at 6789 each, 4321 in stock."
    out = verify_response_facts(text, "q", {})
    assert "unverified" in out


def test_few_numbers_untouched(monkeypatch):
    _no_kb(monkeypatch)
    text = "延迟从 1234ms 降到 567ms。"
    assert verify_response_facts(text, "问", {}) == text


def test_kb_hit_suppresses_note(monkeypatch):
    _has_kb(monkeypatch)
    text = "2024 年出货 12345 台，均价 6789 元，库存 4321 件。"
    assert verify_response_facts(text, "问", {}) == text


def test_failure_is_silent(monkeypatch):
    """守护自己炸了不许把回答搞丢。"""
    def _boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(kb_mod.KnowledgeBase, "search", staticmethod(_boom))
    text = "2024 年出货 12345 台，均价 6789 元，库存 4321 件。"
    assert verify_response_facts(text, "问", {}) == text
