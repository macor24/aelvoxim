"""关系落库链路判据（2026-11-03）

背景：生产库 1100 条关系边里语义边 0 条，两个独立缺陷：
  ① 抽取器返回 relations，调用点只存 entities ⇒ 语义关系从来没进库；
  ② 迁移行只有 id、name 为空，而读取端只按 name 过滤 ⇒ 一条都读不回来。
本文件把这两条锁死，并锁住受控谓词表的口径。
"""
import json
import os
import subprocess
import sys
import tempfile

import aelvoxim.storage.db as dbmod
from aelvoxim.memory import _pg_get_relations, _pg_store_relation, predicates


# ── 1. 受控谓词表 ───────────────────────────────────────────

def test_predicates_canonicalize_aliases():
    assert predicates.canonicalize("isa") == "is_a"
    assert predicates.canonicalize(" 属于 ") == "is_a"
    assert predicates.canonicalize("Belongs_To") == "member_of"
    assert predicates.canonicalize("Discussed") == "discussed"


def test_predicates_classification_and_transitivity():
    assert predicates.classification("is_a") == "knowledge"
    assert predicates.classification("discussed") == "non_knowledge"
    assert predicates.classification("whatever_new") == "unknown"
    assert predicates.is_transitive("part_of") is True
    assert predicates.is_transitive("causes") is False   # 因果不传递


# ── 2. 写入：name + id + created_at 一起落，且幂等 ───────────

def test_pg_store_relation_writes_name_and_id(monkeypatch):
    calls = []

    def _fake_execute(sql, params=None):
        calls.append((sql, params))
        return 1

    monkeypatch.setattr(dbmod, "execute", _fake_execute)
    assert _pg_store_relation("r1", "张三", "北京", "located_in") is True
    sql, params = calls[0]
    assert "source_id" in sql and "target_id" in sql
    assert "created_at" in sql
    assert "NOT EXISTS" in sql          # 同一 (s,p,o) 不重复插入
    assert params[0] == "张三" and params[1] == "北京"
    assert params[4] == "located_in"
    assert len(params) == 9


def test_pg_store_relation_canonicalizes_predicate(monkeypatch):
    calls = []
    monkeypatch.setattr(dbmod, "execute", lambda sql, params=None: calls.append(params) or 1)
    _pg_store_relation("r2", "A", "B", "属于")
    assert calls[0][4] == "is_a"
    assert calls[0][8] == "is_a"


def test_pg_store_relation_rejects_empty_parts(monkeypatch):
    calls = []
    monkeypatch.setattr(dbmod, "execute", lambda sql, params=None: calls.append(params) or 1)
    assert _pg_store_relation("r3", "", "B", "is_a") is False
    assert _pg_store_relation("r4", "A", "", "is_a") is False
    assert _pg_store_relation("r5", "A", "B", "  ") is False
    assert calls == []


# ── 3. 读取：name 空时用 id 兜底（迁移行的救法）─────────────

def test_pg_get_relations_falls_back_to_id(monkeypatch):
    seq = [
        [{"id": "ent-aaa"}],                                  # name -> id
        [{"source_name": "", "target_name": "",               # 迁移行：只有 id
          "source_id": "ent-aaa", "target_id": "ent-bbb",
          "relation_type": "located_in", "weight": 0.6, "created_at": None}],
        [{"id": "ent-aaa", "name": "张三"}, {"id": "ent-bbb", "name": "北京"}],
    ]
    monkeypatch.setattr(dbmod, "fetch_dict", lambda sql, params=None: seq.pop(0))
    rows = _pg_get_relations("张三")
    assert rows and rows[0]["source"] == "张三" and rows[0]["target"] == "北京"
    assert rows[0]["type"] == "located_in"


def test_pg_get_relations_canonicalizes_filter(monkeypatch):
    seen = []

    def _fake(sql, params=None):
        seen.append(params)
        if len(seen) == 1:
            return [{"id": "ent-aaa"}]
        return []

    monkeypatch.setattr(dbmod, "fetch_dict", _fake)
    _pg_get_relations("张三", rel_type="属于")
    assert "is_a" in seen[1]


def test_pg_get_relations_nulls_last(monkeypatch):
    seen = []
    monkeypatch.setattr(dbmod, "fetch_dict", lambda sql, params=None: seen.append(sql) or [])
    _pg_get_relations(None)
    assert "NULLS LAST" in seen[0]


# ── 4. 对话链路：关系必须被写下去（本次的核心修复）───────────

def test_extract_and_store_entities_persists_relations(monkeypatch):
    from aelvoxim.server import entity_extractor, service_chat
    import aelvoxim.memory as memmod

    monkeypatch.setattr(entity_extractor, "extract_entities", lambda u, a="": {
        "entities": [{"name": "张三", "type": "person"}],
        "relations": [
            {"subject": "张三", "predicate": "位于", "object": "北京"},
            {"subject": "张三", "predicate": "", "object": "空谓词"},   # 应被跳过
        ],
    })
    stored = {"entities": [], "relations": []}
    monkeypatch.setattr(memmod, "store_entity", lambda *a, **k: stored["entities"].append(a))
    monkeypatch.setattr(memmod, "store_relation", lambda *a, **k: stored["relations"].append(a))

    service_chat.extract_and_store_entities("我是张三，在北京", "你好", {"email": "x@y.z"}, "ev1")

    assert len(stored["entities"]) == 1
    assert len(stored["relations"]) == 1, "关系被丢掉了"
    _rid, s, t, p, _attrs = stored["relations"][0]
    assert (s, t, p) == ("张三", "北京", "位于")
    assert _rid.startswith("chat:")


def test_extract_and_store_entities_survives_store_failure(monkeypatch):
    """单条关系写失败不许把整轮记忆链路带崩。"""
    from aelvoxim.server import entity_extractor, service_chat
    import aelvoxim.memory as memmod

    monkeypatch.setattr(entity_extractor, "extract_entities", lambda u, a="": {
        "entities": [{"name": "张三", "type": "person"}],
        "relations": [{"subject": "张三", "predicate": "位于", "object": "北京"}],
    })
    monkeypatch.setattr(memmod, "store_entity", lambda *a, **k: True)
    monkeypatch.setattr(memmod, "store_relation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pg down")))

    service_chat.extract_and_store_entities("我是张三，在北京", "你好", {}, "ev2")  # 不抛


# ── 5. 读数仪器：合成正例必须开火 ────────────────────────────

def test_iq_readout_fires_on_synthetic_graph():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as d:
        ents = [{"id": f"e{i}", "name": f"E{i}", "entity_type": "concept"} for i in range(10)]
        rels = [{"id": f"r{i}", "source_id": f"e{i}", "target_id": f"e{i+1}",
                 "relation_type": "is_a", "weight": "1", "created_at": None,
                 "source_name": f"E{i}", "target_name": f"E{i+1}"} for i in range(9)]
        for name, rows in (("memory_entities", ents), ("memory_relations", rels)):
            with open(os.path.join(d, name + ".jsonl"), "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
        out = subprocess.run(
            [sys.executable, os.path.join(root, "scripts", "iq_readout.py"), "--dir", d],
            capture_output=True, text=True, timeout=120,
        )
    assert out.returncode == 0, out.stderr
    assert "智商不可测" not in out.stdout
    assert "语义边      9" in out.stdout
