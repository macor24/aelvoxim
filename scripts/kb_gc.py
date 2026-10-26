#!/usr/bin/env python3
"""kb_gc — knowledge-base hygiene for Aelvoxim.

One command to answer "is the KB clean?" and to repair the three stores it
writes (entry files / index.json+embeddings.json / PostgreSQL).

    python scripts/kb_gc.py                       # read-only report
    python scripts/kb_gc.py --rebuild-index       # dry-run: show what would be indexed
    python scripts/kb_gc.py --rebuild-index --apply
    python scripts/kb_gc.py --backfill-emb --apply
    python scripts/kb_gc.py --prune-dupes --apply

--prune-dupes only touches the file store + index + embeddings (that is what
retrieval reads). PG duplicates are reported, never deleted here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if REPO_SRC.exists():
    sys.path.insert(0, str(REPO_SRC))
PROD_SRC = Path("/opt/aelvoxim/src")
if PROD_SRC.exists() and str(PROD_SRC) not in sys.path:
    sys.path.insert(0, str(PROD_SRC))

DATA = Path(os.environ.get("AELVOXIM_DATA_DIR") or (Path.home() / ".aelvoxim"))


def _load_service_env() -> None:
    """Pick up PG DSN etc. from the api unit when run outside systemd."""
    if os.environ.get("AELVOXIM_DATABASE_URL"):
        return
    try:
        import shlex
        import subprocess
        out = subprocess.run(["systemctl", "show", "aelvoxim-api", "-p", "Environment"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        body = out[len("Environment="):] if out.startswith("Environment=") else out
        for tok in shlex.split(body):
            if "=" in tok:
                k, v = tok.split("=", 1)
                os.environ.setdefault(k, v)
    except Exception:
        pass


_load_service_env()
K = DATA / "knowledge"
E = K / "entries"
IDX = K / "index.json"
EMB = K / "embeddings.json"

HEX_RE = re.compile(r"[0-9a-f]{16}")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

DEFAULT_DENY = ("zenttop", "kitchen knife", "alibaba", "阿里巴巴", "刀具", "hindi",
                "kavita", "कविता", "devanagari", "拓客", "客户线索", "发邮件", "whatsapp", "watsap")


def load_gate():
    for cand in (Path(__file__).resolve().parents[1] / "src/aelvoxim/learn/topic_gate.py",
                 PROD_SRC / "aelvoxim/learn/topic_gate.py"):
        if cand.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("topic_gate_kb_gc", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def load_entries():
    out = {}
    for p in E.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out[str(d.get("id") or p.stem)] = (p, d)
    return out


def read_index():
    if not IDX.exists():
        return {"entries": [], "topics": {}}
    try:
        return json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": [], "topics": {}}


def read_emb_keys():
    if not EMB.exists():
        return set()
    try:
        return set(json.loads(EMB.read_text(encoding="utf-8")).keys())
    except Exception:
        # compact or single-line file: fall back to regex scan
        return set(re.findall(r'"([0-9A-Za-z_\-]{8,})": \{"vector"', EMB.read_text(encoding="utf-8")))


def pg_rows():
    try:
        from aelvoxim.storage.db import fetch_dict, use_pg
        if not use_pg():
            return None
        return fetch_dict("SELECT id, topic, title, source, count(*) OVER (PARTITION BY content) AS dup_of FROM knowledge_entries")
    except Exception as exc:  # pragma: no cover - env dependent
        print(f"(PG unavailable: {exc})")
        return None


def cmd_status(args):
    entries = load_entries()
    active = {i: v for i, v in entries.items() if v[1].get("_status", "active") == "active"}
    idx = set(str(x) for x in read_index().get("entries", []))
    emb = read_emb_keys()
    hex_n = sum(1 for i in entries if HEX_RE.fullmatch(i))
    uuid_n = sum(1 for i in entries if UUID_RE.fullmatch(i))
    not_indexed = sorted(set(active) - idx)
    not_embedded = sorted(set(active) - emb)

    groups = defaultdict(list)
    for i, (_p, d) in active.items():
        groups[norm_title(d.get("title", ""))].append((d.get("updated_at") or d.get("created_at") or "", i))
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1 and k}
    wasted = sum(len(v) - 1 for v in dup_groups.values())

    print("── file store")
    print(f"  entries={len(entries)} active={len(active)} hex_id={hex_n} uuid_id={uuid_n}")
    print(f"  indexed={len(idx)} embedded={len(emb)}")
    print(f"  active_not_indexed={len(not_indexed)}  active_not_embedded={len(not_embedded)}")
    print(f"  duplicate_titles={len(dup_groups)} wasted_entries={wasted}")

    print("── postgres")
    rows = pg_rows()
    if rows is not None:
        topics = {r["topic"] for r in rows}
        file_topics = {v[1].get("topic") for v in entries.values()}
        dup_rows = sum(1 for r in rows if (r.get("dup_of") or 1) > 1)
        print(f"  rows={len(rows)} topics={len(topics)} rows_sharing_content={dup_rows}")
        print(f"  topics_only_in_pg={len(topics - file_topics)} topics_only_in_files={len(file_topics - topics)}")

    gate = load_gate()
    if gate:
        bad_dirs, bad_kb = [], defaultdict(int)
        try:
            from aelvoxim.storage.db import fetch_dict, use_pg
            if use_pg():
                for r in fetch_dict("SELECT topic FROM learning_directions"):
                    ok, reason = gate.check(r["topic"] or "")
                    if not ok:
                        bad_dirs.append((reason, r["topic"]))
                for r in fetch_dict("SELECT topic, count(*) n FROM knowledge_entries GROUP BY 1"):
                    ok, reason = gate.check(r["topic"] or "")
                    if not ok:
                        bad_kb[reason] += int(r["n"])
        except Exception:
            pass
        print("── gate")
        print(f"  directions_failing_gate={len(bad_dirs)}  kb_rows_failing_gate={sum(bad_kb.values())}")
        for reason, topic in bad_dirs[:6]:
            print(f"    [{reason}] {topic[:60]!r}")
        if bad_kb:
            print("   " + ", ".join(f"{k}={v}" for k, v in sorted(bad_kb.items())))
    return 0


def cmd_rebuild_index(args):
    entries = load_entries()
    idx = read_index()
    have = set(str(x) for x in idx.get("entries", []))
    active = {i for i, v in entries.items() if v[1].get("_status", "active") == "active"}
    add = sorted(active - have)
    stale = sorted(have - set(entries))
    print(f"index={len(have)} active_entries={len(active)} to_add={len(add)} stale={len(stale)}")
    for i in add[:5]:
        print(f"  + {i} {entries[i][1].get('topic','')[:40]!r}")
    if not args.apply:
        print("DRY-RUN")
        return 0
    idx["entries"] = sorted(have & set(entries) | active)
    topics = defaultdict(str)
    for i in idx["entries"]:
        d = entries[i][1]
        t = d.get("topic") or ""
        ts = d.get("updated_at") or d.get("created_at") or ""
        if ts > topics[t]:
            topics[t] = ts
    idx["topics"] = dict(topics)
    tmp = IDX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(IDX)
    print(f"index rewritten: {len(idx['entries'])} ids, {len(idx['topics'])} topics")
    return 0


def cmd_backfill_emb(args):
    from aelvoxim.storage.embedding import get_embedding

    entries = load_entries()
    idx = set(str(x) for x in read_index().get("entries", []))
    emb = json.loads(EMB.read_text(encoding="utf-8")) if EMB.exists() else {}
    missing = sorted(i for i in idx if i not in emb and i in entries)
    print(f"index={len(idx)} embedded={len(emb)} missing={len(missing)}")
    if not args.apply:
        print("DRY-RUN")
        return 0
    for i in missing:
        d = entries[i][1]
        text = (d.get("title") or "") + " " + (d.get("content") or "")[:400]
        emb[i] = {"vector": get_embedding(text), "text": (d.get("title") or "")[:200]}
    tmp = EMB.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(emb, fh, ensure_ascii=False)
    json.loads(tmp.read_text(encoding="utf-8"))  # validate before swap
    tmp.replace(EMB)
    print(f"embeddings rewritten: {len(emb)}")
    return 0


def cmd_prune_dupes(args):
    entries = load_entries()
    active = {i: v for i, v in entries.items() if v[1].get("_status", "active") == "active"}
    groups = defaultdict(list)
    for i, (_p, d) in active.items():
        groups[norm_title(d.get("title", ""))].append(
            (str(d.get("updated_at") or d.get("created_at") or ""), i))
    victims = []
    for title, items in groups.items():
        if not title or len(items) < 2:
            continue
        items.sort(reverse=True)          # newest first
        victims.extend(i for _ts, i in items[1:])
    print(f"duplicate_groups={sum(1 for v in groups.values() if len(v) > 1)} victims={len(victims)}")
    if not args.apply:
        print("DRY-RUN")
        return 0
    idx = read_index()
    emb = json.loads(EMB.read_text(encoding="utf-8")) if EMB.exists() else {}
    for i in victims:
        p = entries[i][0]
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        emb.pop(i, None)
    idx["entries"] = [x for x in idx.get("entries", []) if str(x) not in set(victims)]
    tmp = IDX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(IDX)
    tmp = EMB.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(emb, fh, ensure_ascii=False)
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(EMB)
    print(f"pruned files={len(victims)} index={len(idx['entries'])} emb={len(emb)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Aelvoxim knowledge-base GC")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--backfill-emb", action="store_true")
    ap.add_argument("--prune-dupes", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.rebuild_index:
        return cmd_rebuild_index(args)
    if args.backfill_emb:
        return cmd_backfill_emb(args)
    if args.prune_dupes:
        return cmd_prune_dupes(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
