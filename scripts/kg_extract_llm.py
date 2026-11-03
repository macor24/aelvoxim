#!/usr/bin/env python3
"""kg_extract_llm.py — P1.1 LLM 三元组抽取（影子，不写生产图）

与 kg_extract_shadow.py 同一套闸，只是把"取词"换成模型：
  语料筛除  标识符方向 / low_value / 模板重复组
  谓词      只收 memory/predicates.CANONICAL
  回溯闸    s、o 必须在同一条笔记里出现，且能落在同一句；否则丢弃
  负向      禁自环、端点 2–60 字符、停用词
  批次      batch_id 时间戳；唯一键 (s,p,o,entry_id)

用法：
  python3 scripts/kg_extract_llm.py --dir <jsonl目录> --out /tmp/kg_shadow --limit 200
  python3 scripts/kg_extract_llm.py --dsn "..." --out ~/.aelvoxim/kg_shadow --limit 200 --max-calls 30

成本护栏：--max-calls（默认 30）、--budget-tokens（默认 400000），任一触发即停。
凭据只从 ~/.aelvoxim/llm-config.json 读，不进 argv、不打印。
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
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRED_PATH = os.path.join(ROOT, "src", "aelvoxim", "memory", "predicates.py")

_spec = importlib.util.spec_from_file_location("_ael_predicates", _PRED_PATH)
assert _spec is not None and _spec.loader is not None
_p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_p)
canonicalize, is_knowledge_edge = _p.canonicalize, _p.is_knowledge_edge
VOCAB = sorted(_p.CANONICAL)

CFG = os.path.expanduser("~/.aelvoxim/llm-config.json")
SENT_SPLIT = re.compile(r"[。！？\n]+|(?<=[.!?])\s+")  # 不在 "." 处硬切：Node.js / psycopg2 / v1.2 会被切碎
IDENT_TOPIC = re.compile(r"^[A-Za-z][A-Za-z0-9_\.]*$")

SYSTEM = """You are a strict knowledge-graph tuple extractor.
Given numbered notes, extract ONLY tuples literally supported by each note's own text.

Predicates allowed (use exactly these strings):
{preds}

Rules:
1. Subject and object must be concrete named things that appear verbatim in the note
   (library, standard, protocol, product, concept, person, place). Never use pronouns,
   "we/this/the/以下/我们", numbers alone, or generic words.
2. The predicate must be readable off one sentence of the note. Do NOT add world
   knowledge, do NOT infer transitive relations, do NOT guess.
3. Max 3 tuples per note. If nothing is supported, return an empty list for that note.
4. Output JSON only, no prose, no code fences:
{{"items":[{{"id":"<note id>","triples":[{{"s":"...","p":"is_a","o":"..."}}]}}]}}
"""

USER_TMPL = """Notes:
{notes}

Return JSON only."""


def load_cfg():
    with open(CFG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    models = sorted(cfg.get("models", []), key=lambda m: m.get("priority", 9))
    if not models:
        raise SystemExit("llm-config.json 里没有模型")
    m = models[0]
    return m["api_key"], m.get("base_url", "https://api.deepseek.com/v1").rstrip("/"), m.get("name", "deepseek-chat")


def chat(key, base, model, system, user, timeout=180):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 12000,
    }).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    ch = payload["choices"][0]
    text = ch["message"].get("content") or ""
    usage = payload.get("usage") or {}
    return text, int(usage.get("total_tokens") or 0), ch.get("finish_reason")


def parse_items(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1]).get("items", [])
    except Exception:
        return None


def sentence_of(content, needle):
    for s in SENT_SPLIT.split(content):
        if needle.lower() in s.lower():
            return s
    return ""


def verify(s, p, o, content):
    """回溯闸 + 负向断言。返回 (ok, reason)。"""
    if not (s and o and p):
        return False, "empty"
    if len(s) < 2 or len(o) < 2 or len(s) > 60 or len(o) > 60:
        return False, "length"
    if not is_knowledge_edge(p):
        return False, "predicate"
    if s.strip().lower() == o.strip().lower():
        return False, "self_loop"
    if s.lower() not in content.lower():
        return False, "subject_absent"
    if o.lower() not in content.lower():
        return False, "object_absent"
    ss, so = sentence_of(content, s), sentence_of(content, o)
    if not ss or not so or ss != so:
        return False, "not_same_sentence"
    return True, "ok"


def load_dir(path):
    out = {}
    for t in ("knowledge_entries", "learning_directions"):
        f = os.path.join(path, t + ".jsonl")
        out[t] = [json.loads(l) for l in open(f, encoding="utf-8")] if os.path.exists(f) else []
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
        for t, sql in {
            "knowledge_entries": ("SELECT id::text, topic, title, content, status, source FROM knowledge_entries"),
            "learning_directions": "SELECT id::text, topic, status FROM learning_directions",
        }.items():
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
    dup = {i for v in fp.values() if len(v) > 1 for i in v}
    kept = [k for k in entries
            if k.get("topic") not in ident
            and (k.get("status") or "") != "low_value"
            and k["id"] not in dup]
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    ap.add_argument("--dsn")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--budget-tokens", type=int, default=400000)
    ap.add_argument("--chars-per-entry", type=int, default=1500)
    ap.add_argument("--batch", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()
    if not args.dir and not args.dsn:
        print("需要 --dir 或 --dsn", file=sys.stderr)
        return 2

    data = load_dir(args.dir) if args.dir else load_pg(args.dsn)
    kept = filter_corpus(data["knowledge_entries"], data["learning_directions"])[args.skip: args.skip + args.limit]
    key, base, model = load_cfg()
    system = SYSTEM.format(preds=", ".join(VOCAB))

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"triples_{args.batch}.jsonl")
    _fh = open(path, "a", encoding="utf-8")

    triples, drops = [], collections.Counter()
    n_triples = 0
    per_pred = collections.Counter()
    seen = set()
    calls = used_tokens = 0
    hit_entries = 0
    t0 = time.time()

    for start in range(0, len(kept), args.batch_size):
        if calls >= args.max_calls or used_tokens >= args.budget_tokens:
            drops["budget_stop"] += 1
            break
        chunk = kept[start:start + args.batch_size]
        notes = "\n\n".join(
            f"[{k['id']}]\ntitle: {k.get('title','')[:120]}\n{(k.get('content') or '')[:args.chars_per_entry]}"
            for k in chunk)
        try:
            text, tk, finish = chat(key, base, model, system, USER_TMPL.format(notes=notes))
        except Exception as exc:
            drops[f"call_error:{type(exc).__name__}"] += 1
            calls += 1
            continue
        calls += 1
        used_tokens += tk
        if not text.strip():
            # deepseek 推理模型的 thinking pass 会吃掉 max_tokens，content 为空
            drops[f"empty_content:{finish}"] += 1
            continue
        items = parse_items(text)
        if items is None:
            drops["unparsable"] += 1
            continue
        by_id = {k["id"]: k for k in chunk}
        for item in items:
            k = by_id.get(str(item.get("id", "")))
            if not k:
                drops["unknown_id"] += 1
                continue
            content = k.get("content") or ""
            got = 0
            for tr in (item.get("triples") or [])[:3]:
                s = str(tr.get("s", "")).strip()
                p = canonicalize(str(tr.get("p", "")))
                o = str(tr.get("o", "")).strip()
                ok, why = verify(s, p, o, content)
                if not ok:
                    drops[why] += 1
                    continue
                ukey = (s.lower(), p, o.lower(), k["id"])
                if ukey in seen:
                    drops["duplicate"] += 1
                    continue
                seen.add(ukey)
                per_pred[p] += 1
                n_triples += 1
                got += 1
                triples.append({
                    "batch": args.batch, "entry_id": k["id"], "topic": k.get("topic", ""),
                    "s": s, "p": p, "o": o,
                    "evidence": sentence_of(content, s)[:200],
                    "source_kind": "firsthand" if k.get("source") == "execution_result" else "secondhand",
                    "extractor": f"llm:{model}",
                })
            if got:
                hit_entries += 1
        if triples:
            _fh.write("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in triples))
            _fh.flush()
            triples = []

    _fh.close()
    json.dump(dict(drops), open(os.path.join(args.out, f"dropped_{args.batch}.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)

    n = max(1, len(kept))
    skel = per_pred.get("is_a", 0) + per_pred.get("part_of", 0)
    print("=" * 60)
    print(f"kg_extract_llm  batch={args.batch}  model={model}")
    print("=" * 60)
    print(f"送入条目 {len(kept)} · 调用 {calls} 次 · token {used_tokens} · 耗时 {time.time()-t0:.0f}s")
    print(f"三元组 {n_triples} · 有产出条目 {hit_entries} ({hit_entries/n:.1%})")
    print(f"按谓词 {dict(per_pred)}")
    print(f"骨架 {skel} ({(skel/max(1,n_triples)):.1%})")
    print(f"丢弃 {dict(drops)}")
    print(f"写出 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
