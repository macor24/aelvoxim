#!/usr/bin/env python3
"""kg_extract_shadow.py — P1.0 规则影子抽取（零成本、零写入生产）

把知识条目里的陈述句抽成受控谓词三元组，落到影子文件，**不写 memory_relations**。

口径（冻结，改口径必须改本文件）：
  语料筛除  ① 标识符型学习方向产出的条目  ② status=low_value  ③ 正文指纹重复组
  端点取词  反引号词 / 首字母大写词 / 中文术语（后接"是/属于/用于/包含/依赖/支持"）
  谓词      只认 memory/predicates.CANONICAL；线索词定位，s 取线索前最近端点，o 取线索后最近端点
  回溯闸    s、o 必须与线索词同句（按句切分后逐句处理，天然满足），证据句随边保存
  上限      每条目 ≤5 条；禁自环；端点长度 2–60
  去重      唯一键 (canonical_key(s), p, canonical_key(o), entry_id)

用法：
  python3 scripts/kg_extract_shadow.py --dir <jsonl目录> --out /tmp/kg_shadow
  python3 scripts/kg_extract_shadow.py --dsn "host=... dbname=aelvoxim user=..." --out ~/.aelvoxim/kg_shadow
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRED_PATH = os.path.join(ROOT, "src", "aelvoxim", "memory", "predicates.py")

try:  # 按路径加载，避免 import aelvoxim.memory 触发 sqlite/PG 初始化
    _spec = importlib.util.spec_from_file_location("_ael_predicates", _PRED_PATH)
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    canonicalize = _mod.canonicalize
    is_knowledge_edge = _mod.is_knowledge_edge
    VOCAB = set(_mod.CANONICAL)
except Exception as exc:  # 不许静默退化
    raise SystemExit(f"谓词表加载失败({type(exc).__name__}): {_PRED_PATH}")

# ── 线索词 → 受控谓词 ─────────────────────────────────────
CUES = [
    ("is_a", re.compile(r"(是一种|是一个|属于|is an? |is a )", re.I)),
    ("part_of", re.compile(r"(的一部分|组成部分|part of)", re.I)),
    ("has_part", re.compile(r"(包含|含有|includes?)", re.I)),
    ("uses", re.compile(r"(使用|基于|采用|uses? |based on)", re.I)),
    ("depends_on", re.compile(r"(依赖|depends on|requires)", re.I)),
    ("causes", re.compile(r"(导致|引起|causes?|leads to)", re.I)),
    ("located_in", re.compile(r"(位于|地处|located in)", re.I)),
]

TOKEN = re.compile(
    r"`([^`]{2,40})`"                                     # 反引号
    r"|\b([A-Z][A-Za-z0-9_+#.\-]{2,30})\b"                # 大写开头
    r"|([\u4e00-\u9fa5]{2,8})(?=是|属于|用于|包含|依赖|支持)"
)

STOP = {
    "the", "this", "that", "a", "an", "in", "it", "if", "you", "we", "they", "for", "and",
    "but", "or", "when", "while", "because", "however", "note", "example", "step", "about",
    "use", "based", "according", "given", "here", "there", "these", "those", "as", "to",
    "我们", "这个", "那个", "可以", "需要", "使用", "包含", "支持", "依赖", "以下", "首先",
}

SENT_SPLIT = re.compile(r"[。！？\n]+|(?<=[.!?])\s+")  # 不在 "." 处硬切：Node.js / psycopg2 / v1.2 会被切碎


def canon_key(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def mine_tokens(sent: str):
    """返回 [(pos, token)]，按出现位置排序。"""
    seen, out = set(), []
    for m in TOKEN.finditer(sent):
        tok = (m.group(1) or m.group(2) or m.group(3) or "").strip(" *_`|")
        if not tok or len(tok) < 2 or len(tok) > 60:
            continue
        if tok.lower() in STOP or tok.isdigit():
            continue
        k = canon_key(tok)
        if k in seen:
            continue
        seen.add(k)
        out.append((m.start(), tok))
    return out


def extract_from_entry(content: str):
    """返回 (triples, drops)；triples 为 [(s, p, o, evidence)]。"""
    triples, drops = [], collections.Counter()
    if not content:
        return triples, drops
    for sent in SENT_SPLIT.split(content):
        if not (8 < len(sent) < 240):
            continue
        toks = mine_tokens(sent)
        if len(toks) < 2:
            continue
        for pred, rx in CUES:
            m = rx.search(sent)
            if not m:
                continue
            if not is_knowledge_edge(pred):
                drops["predicate_not_in_vocab"] += 1
                continue
            before = [t for pos, t in toks if pos < m.start()]
            after = [t for pos, t in toks if pos >= m.end()]
            if not before or not after:
                drops["one_side_missing"] += 1
                continue
            s, o = before[-1], after[0]
            if canon_key(s) == canon_key(o):
                drops["self_loop"] += 1
                continue
            triples.append((s, canonicalize(pred), o, sent.strip()[:200]))
            break  # 一句只取一个谓词，取最先命中的
        if len(triples) >= 5:
            break
    return triples[:5], drops


# ── 语料读取与筛选 ────────────────────────────────────────

IDENT_TOPIC = re.compile(r"^[A-Za-z][A-Za-z0-9_\.]*$")


def load_dir(path):
    out = {}
    for t in ("memory_relations", "knowledge_entries", "learning_directions"):
        p = os.path.join(path, t + ".jsonl")
        out[t] = [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
    return out


def load_pg(dsn):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
        q = {
            "memory_relations": "SELECT count(*) AS n FROM memory_relations",
            "knowledge_entries": ("SELECT id::text, topic, title, content, status, source, "
                                  "created_at::text AS created_at FROM knowledge_entries"),
            "learning_directions": "SELECT id::text, topic, status FROM learning_directions",
        }
        for t, sql in q.items():
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                out[t] = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return out


def filter_corpus(entries, directions):
    ident = {d["topic"] for d in directions if IDENT_TOPIC.match((d.get("topic") or "").strip())}
    fp = collections.defaultdict(list)
    for k in entries:
        fp[hashlib.sha1(((k.get("content") or "")[:200]).encode()).hexdigest()].append(k["id"])
    dup_ids = {i for v in fp.values() if len(v) > 1 for i in v}

    kept, reasons = [], collections.Counter()
    for k in entries:
        if (k.get("topic") in ident):
            reasons["identifier_direction"] += 1
            continue
        if (k.get("status") or "") == "low_value":
            reasons["low_value"] += 1
            continue
        if k["id"] in dup_ids:
            reasons["template_duplicate"] += 1
            continue
        kept.append(k)
    return kept, reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    ap.add_argument("--dsn")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（探针用）")
    args = ap.parse_args()
    if not args.dir and not args.dsn:
        print("需要 --dir 或 --dsn", file=sys.stderr)
        return 2

    data = load_dir(args.dir) if args.dir else load_pg(args.dsn)
    entries = data["knowledge_entries"]
    kept, reasons = filter_corpus(entries, data["learning_directions"])
    if args.limit:
        kept = kept[: args.limit]

    triples, drops = [], collections.Counter()
    seen = set()
    hit_entries = 0
    per_pred = collections.Counter()
    for k in kept:
        got, d = extract_from_entry(k.get("content") or "")
        drops.update(d)
        if got:
            hit_entries += 1
        for s, p, o, ev in got:
            key = (canon_key(s), p, canon_key(o), k["id"])
            if key in seen:
                drops["duplicate"] += 1
                continue
            seen.add(key)
            per_pred[p] += 1
            triples.append({
                "batch": args.batch, "entry_id": k["id"], "topic": k.get("topic", ""),
                "s": s, "p": p, "o": o, "evidence": ev,
                "source_kind": "provenance" if (k.get("source") == "execution_result") else "secondhand",
                "extractor": "rules_v1",
            })

    os.makedirs(args.out, exist_ok=True)
    tri_path = os.path.join(args.out, f"triples_{args.batch}.jsonl")
    with open(tri_path, "w", encoding="utf-8") as fh:
        for t in triples:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    drop_path = os.path.join(args.out, f"dropped_{args.batch}.json")
    json.dump(dict(drops), open(drop_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    n_kept = max(1, len(kept))
    print("=" * 60)
    print(f"kg_extract_shadow 规则影子   batch={args.batch}")
    print("=" * 60)
    print(f"入库条目 {len(entries)} · 筛后 {len(kept)} · 有产出条目 {hit_entries} ({hit_entries/n_kept:.1%})")
    print(f"筛除明细 {dict(reasons)}")
    print(f"三元组 {len(triples)}  按谓词 {dict(per_pred)}")
    skel = per_pred.get("is_a", 0) + per_pred.get("part_of", 0)
    print(f"骨架(is_a+part_of) {skel} ({(skel/max(1,len(triples))):.1%})  丢弃原因 {dict(drops)}")
    print(f"写出 {tri_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
