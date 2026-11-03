"""P1.0 规则影子抽取判据（2026-11-03）

锁四件事：
  1. 三元组只在"端点夹住线索词"时产出（回溯闸的规则版）
  2. 自环/端点不足/超上限都按口径丢弃
  3. 语料筛选：标识符方向 / low_value / 模板重复组
  4. CLI 在夹具上真能跑出文件，且同输入两次结果一致
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "kg_extract_shadow.py")


def _load():
    spec = importlib.util.spec_from_file_location("kg_shadow", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kg = _load()


def test_extracts_when_endpoints_straddle_cue():
    triples, drops = kg.extract_from_entry("`Redis` is a `database` used by services.")
    assert triples, "应抽到一条"
    s, p, o, ev = triples[0]
    assert p == "is_a"
    assert (s, o) == ("Redis", "database")


def test_no_triple_when_subject_side_missing():
    """线索词之前没有端点 → 不产出（回溯闸的规则版）。"""
    triples, drops = kg.extract_from_entry("It is a `Redis` and `Memcached` stack.")
    assert triples == []
    assert drops.get("one_side_missing", 0) >= 1


def test_chinese_object_not_mined_yet():
    """已知限制（锁住，别让它悄悄变成假象）：中文宾语目前不被取词。

    这条不是"对的"，是"当前口径"：规则路只对英文/反引号内容有效，
    覆盖率因此停在 ~20%（见 P1 计划 §6 预测未兑现的诊断）。
    要放开中文宾语，得先给它加受控词根 + 停用词，再重跑预测表。
    """
    triples, _ = kg.extract_from_entry("Redis 是一种内存数据库。")
    assert triples == []


def test_self_loop_rejected():
    triples, drops = kg.extract_from_entry("Kafka 包含 Kafka。")
    assert all(kg.canon_key(s) != kg.canon_key(o) for s, _, o, _ in triples)


def test_cap_five_per_entry():
    text = " ".join([f"Service{i} 使用 Tool{i}。" for i in range(12)])
    triples, _ = kg.extract_from_entry(text)
    assert len(triples) <= 5


def test_stopwords_and_numbers_not_endpoints():
    triples, _ = kg.extract_from_entry("This is a 1234 based on 5678.")
    assert triples == []


def test_filter_corpus_drops_identifier_lowvalue_and_duplicates():
    entries = [
        {"id": "1", "topic": "PaymentDeclined", "content": "A 使用 B。", "status": "active"},
        {"id": "2", "topic": "内存调优", "content": "C 使用 D。", "status": "low_value"},
        {"id": "3", "topic": "并发模型", "content": "同样的正文" * 20, "status": "active"},
        {"id": "4", "topic": "并发模型", "content": "同样的正文" * 20, "status": "active"},
        {"id": "5", "topic": "存储引擎", "content": "E 使用 F。", "status": "active"},
    ]
    dirs = [{"id": "d1", "topic": "PaymentDeclined", "status": "active"},
            {"id": "d2", "topic": "内存调优", "status": "active"}]
    kept, reasons = kg.filter_corpus(entries, dirs)
    ids = {k["id"] for k in kept}
    assert ids == {"5"}, f"只应保留 5，实际 {ids}"
    assert reasons["identifier_direction"] == 1
    assert reasons["low_value"] == 1
    assert reasons["template_duplicate"] == 2


def test_cli_writes_files_and_is_deterministic():
    with tempfile.TemporaryDirectory() as d:
        entries = [
            {"id": "e1", "topic": "存储引擎", "status": "active",
             "content": "Redis 是一种内存数据库。Kafka 使用 Zookeeper。"},
        ]
        for name, rows in (("knowledge_entries", entries), ("learning_directions", []),
                           ("memory_relations", [])):
            with open(os.path.join(d, name + ".jsonl"), "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        outs = []
        for _ in range(2):
            o = os.path.join(d, "out" + str(len(outs)))
            r = subprocess.run([sys.executable, SCRIPT, "--dir", d, "--out", o,
                                "--batch", "T"], capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stderr
            with open(os.path.join(o, "triples_T.jsonl"), encoding="utf-8") as fh:
                outs.append([json.loads(l) for l in fh])
        assert outs[0] == outs[1], "同输入两次结果必须一致"
        assert len(outs[0]) >= 1
        assert all(t["extractor"] == "rules_v1" and t["batch"] == "T" for t in outs[0])
