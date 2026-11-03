#!/usr/bin/env python3
"""iq_readout.py — Aelvoxim 智商读数 v0（只读，不写任何库）

回答一个问题：**现在有没有可推理的结构，能不能测出智商。**

口径（冻结；改口径必须改本文件 + docs）：

  语义边    谓词在 memory/predicates.CANONICAL 里的边
  噪声边    discussed / mentioned / related 等共现痕迹（推理与出题都不用）
  可检索边  source_name 与 target_name 都非空（或可由 id 解析出名字）
  可判题数  单跳(语义边) + 两跳(传递谓词的桥) + 反例(互为传递关系)
  闭包召回  按边哈希留出 20%，用其余边做传递闭包，看能推回多少留出边
            —— 这个数不靠 LLM、灌文本刷不高，可直接当演化/调参的适应度

三种数据源：
  --dump  <pg_dump.sql.gz>     离线的生产备份
  --dir   <jsonl 目录>          parse 出来的四张表
  --dsn   <PG 连接串>           直连库（强制 default_transaction_read_only=on）

用法：
  python3 scripts/iq_readout.py --dsn "$AELVOXIM_DATABASE_URL"
  python3 scripts/iq_readout.py --dump /path/backup.sql.gz
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    from aelvoxim.memory.predicates import canonicalize, classification, is_transitive
except Exception:  # 未安装包时退化为本地口径，读数不中断
    def canonicalize(pred) -> str:
        return (pred or "").strip().lower().replace(" ", "_")

    def classification(pred) -> str:
        return "unknown"

    def is_transitive(pred) -> bool:
        return canonicalize(pred) in {"is_a", "part_of", "has_part", "member_of", "prerequisite", "depends_on"}

TABLES = ["memory_entities", "memory_relations", "knowledge_entries", "learning_directions"]
DSN_ENV = "AELVOXIM_DATABASE_URL"


# ── 数据加载 ────────────────────────────────────────────────

def _unescape(s: str):
    if s == "\\N":
        return None
    out, i = [], 0
    m = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\", "b": "\b", "f": "\f", "v": "\v"}
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in m:
                out.append(m[nxt]); i += 2; continue
            if nxt.isdigit():
                try:
                    out.append(chr(int(s[i + 1:i + 4], 8))); i += 4; continue
                except ValueError:
                    pass
            out.append(nxt); i += 2; continue
        out.append(ch); i += 1
    return "".join(out)


def load_dump(path: str) -> dict:
    out = {t: [] for t in TABLES}
    cols, cur = [], None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("COPY public."):
                t = line.split(".")[1].split(" ")[0]
                if t in TABLES:
                    cols = [c.strip() for c in line[line.index("(") + 1:line.rindex(")")].split(",")]
                    cur = t
                else:
                    cur = None
                continue
            if cur and line.rstrip("\n") == "\\.":
                cur = None
                continue
            if cur:
                out[cur].append(dict(zip(cols, [_unescape(v) for v in line.rstrip("\n").split("\t")])))
    return out


def load_dir(path: str) -> dict:
    out = {}
    for t in TABLES:
        p = os.path.join(path, t + ".jsonl")
        out[t] = [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
    return out


def load_pg(dsn: str) -> dict:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
        q = {
            "memory_entities": "SELECT id::text, name, entity_type FROM memory_entities",
            "memory_relations": ("SELECT id::text, source_id::text, target_id::text, relation_type, "
                                 "weight, created_at, source_name, target_name FROM memory_relations"),
            "knowledge_entries": ("SELECT id::text, topic, title, status, confidence, validated, "
                                  "validated_count, source, created_at FROM knowledge_entries"),
            "learning_directions": "SELECT id::text, topic, status, entries_created FROM learning_directions",
        }
        missing = []
        for t, sql in q.items():
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql)
                    out[t] = [dict(r) for r in cur.fetchall()]
            except Exception as exc:  # 某些部署没有这些表：报出来，不要把整张读数打掉
                out[t] = []
                missing.append(f"{t}({type(exc).__name__})")
        if missing:
            print("[warn] 缺表或读不到:", ", ".join(missing), file=sys.stderr)
    finally:
        conn.close()
    return out


# ── 计算 ────────────────────────────────────────────────────

def resolve_edges(rels, ents):
    by_id = {str(e["id"]): e for e in ents}
    edges, unresolvable = [], 0
    for r in rels:
        s = r.get("source_name") or ""
        t = r.get("target_name") or ""
        nameable = bool(s and t)
        if not nameable:
            se, te = by_id.get(str(r.get("source_id") or "")), by_id.get(str(r.get("target_id") or ""))
            if not se or not te:
                unresolvable += 1
                continue
            s, t = se["name"], te["name"]
        edges.append({"s": s, "p": canonicalize(r.get("relation_type") or ""), "t": t,
                      "nameable": nameable})
    return edges, unresolvable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--dir")
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()
    if args.dsn:
        data = load_pg(args.dsn)
        src = "PG(只读)"
    elif args.dump:
        data = load_dump(args.dump)
        src = args.dump
    elif args.dir:
        data = load_dir(args.dir)
        src = args.dir
    else:
        print("需要 --dump / --dir / --dsn 之一", file=sys.stderr)
        return 2

    ents, rels = data["memory_entities"], data["memory_relations"]
    ke, dirs = data["knowledge_entries"], data["learning_directions"]
    edges, unresolvable = resolve_edges(rels, ents)

    sem = [e for e in edges if classification(e["p"]) == "knowledge"]
    noise = [e for e in edges if classification(e["p"]) == "non_knowledge"]
    other = [e for e in edges if classification(e["p"]) == "unknown"]
    nameable = [e for e in edges if e["nameable"]]

    sem_out = collections.defaultdict(list)
    for e in sem:
        sem_out[e["s"]].append(e)
    two_hop, trans_hop = [], []
    for a, outs in sem_out.items():
        for e1 in outs:
            for e2 in sem_out.get(e1["t"], []):
                if e2["t"] == a:
                    continue
                two_hop.append(1)
                if is_transitive(e1["p"]) and is_transitive(e2["p"]):
                    trans_hop.append((a, e1["t"], e2["t"]))

    pair = {(e["s"], e["p"], e["t"]) for e in sem}
    cycles = sum(1 for e in sem if is_transitive(e["p"]) and (e["t"], e["p"], e["s"]) in pair)

    def _h(e):
        return int(hashlib.sha1(f"{e['s']}|{e['p']}|{e['t']}".encode()).hexdigest()[:8], 16)

    train = [e for e in sem if _h(e) % 5 != 0]
    test = [e for e in sem if _h(e) % 5 == 0 and is_transitive(e["p"])]
    closure = {(e["s"], e["t"]) for e in train if is_transitive(e["p"])}
    frontier = set(closure)
    for _ in range(4):
        nxt = {(a, c) for a, b in frontier for (b2, c) in closure if b == b2 and a != c}
        if not nxt - frontier:
            break
        frontier |= nxt
    hit = sum(1 for e in test if (e["s"], e["t"]) in frontier)

    vc = collections.Counter(int(k.get("validated_count") or 0) for k in ke)
    conf = sorted(float(k.get("confidence") or 0) for k in ke) if ke else [0.0]

    print("=" * 62)
    print("Aelvoxim 智商读数 v0   源:", src)
    print("=" * 62)
    print(f"实体 {len(ents)} · 关系 {len(rels)} · 知识条目 {len(ke)} · 学习方向 {len(dirs)}")
    print()
    print("── 1. 可推理的结构 ──")
    print(f"  语义边      {len(sem)}   谓词: {sorted({e['p'] for e in sem}) or '无'}")
    print(f"  噪声边      {len(noise)}   {sorted({e['p'] for e in noise}) or '无'}")
    print(f"  其它边      {len(other)}  {sorted({e['p'] for e in other})[:6]}")
    print(f"  按名字可检索 {len(nameable)}/{len(edges)}（名字空={len(edges)-len(nameable)}，端点解析失败={unresolvable}）")
    print()
    print("── 2. 可判题规模 ──")
    print(f"  单跳 {len(sem)} · 两跳 {len(two_hop)}（传递 {len(trans_hop)}）· 反例 {cycles}"
          f" ⇒ 合计 {len(sem) + len(trans_hop) + cycles}")
    print()
    print("── 3. 闭包召回（留出20%，仅传递谓词）──")
    if test:
        print(f"  留出 {len(test)} 条，推回 {hit} 条 ⇒ 召回 {hit/len(test):.3f}；闭包新增可推导对 "
              f"{len(frontier - {(e['s'], e['t']) for e in train})}")
    else:
        print("  不可测：没有可传递的语义边")
    print()
    print("── 4. 知识面验证质量 ──")
    print(f"  validated=True {sum(1 for k in ke if k.get('validated') in (True,'t','True'))}"
          f" / count≥2 {sum(v for k, v in vc.items() if k >= 2)} 条")
    print(f"  validated_count 分布 {dict(sorted(vc.items())[:6])}")
    print(f"  confidence p50={conf[len(conf)//2]:.3f} avg={sum(conf)/len(conf):.3f}")
    print()
    q = len(sem) + len(trans_hop) + cycles
    print("── 结论 ──")
    if q == 0:
        print("  智商不可测：没有语义边 ⇒ 既没有题，也没有可推理的对象。")
    elif test and hit == 0:
        print("  可测但推理为零：有语义边，闭包一条都推不回 ⇒ 规则/结构不足。")
    else:
        r = hit / len(test) if test else 0.0
        print(f"  可判题 {q} 条，闭包召回 {r:.3f} ⇒ 当前智商基线。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
