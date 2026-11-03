"""对话不再推销"能教我一些吗"（2026-11-03）

删掉的东西：
  1. `[Probe: ask_teach]` / `[Probe: ask_deepen]` 注入 —— 它要求模型在回答末尾反问
     "这个话题我了解不多，{问题} —— 能教我一些吗？"
  2. 顺带把用户问句写进好奇心队列（聊天提问 ≠ 学习方向）
保留：KB 命中时正常注入 [Related Knowledge]。
"""
import uuid

import aelvoxim.learn.knowledge as kb_mod
import aelvoxim.server.service_chat as sc


def _run(monkeypatch, hits, q=None):
    q = q or f"硅胶厨具怎么防粘灰？{uuid.uuid4().hex[:6]}"   # 绕开 30s 查询缓存
    monkeypatch.setattr(kb_mod.KnowledgeBase, "search", staticmethod(lambda **kw: hits))
    added = []
    monkeypatch.setattr(sc, "_add_curiosity_topic", lambda t, u=None: added.append(t))
    out = sc.enhance_with_knowledge(q, "", {"email": "t@example.com", "username": "t"})
    return out, added


def test_no_probe_when_kb_miss(monkeypatch):
    out, added = _run(monkeypatch, [])
    assert "[Probe" not in out
    assert "能教我" not in out
    assert added == [], "聊天提问不该再进好奇心队列"


def test_no_probe_when_kb_hit(monkeypatch):
    hits = [{"title": "硅胶防粘灰", "content": "用食品级硅油薄涂，降低表面能。", "confidence": 0.7, "topic": "厨具"}]
    out, added = _run(monkeypatch, hits)
    assert "[Probe" not in out
    assert "能教我" not in out
    assert added == []


def test_kb_block_still_injected(monkeypatch):
    """删注入不许顺手把知识注入也删了。"""
    hits = [{"title": "硅胶防粘灰", "content": "用食品级硅油薄涂，降低表面能。", "confidence": 0.7, "topic": "厨具"}]
    out, _ = _run(monkeypatch, hits)
    assert "[Related Knowledge]" in out
    assert "硅胶防粘灰" in out


def test_low_confidence_still_marked(monkeypatch):
    hits = [{"title": "某低置信条目", "content": "可能是这样。", "confidence": 0.35, "topic": "别的"}]
    out, _ = _run(monkeypatch, hits)
    assert "Low Confidence" in out
