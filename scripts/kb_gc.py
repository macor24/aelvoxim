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


_PLAN_PREFIX_RE = re.compile(r"^(已完成计划|完成计划)\s*[:：]?\s*")


def topic_passes(gate, topic: str):
    """Gate verdict, but a plan-completion title with a real payload counts as fine."""
    ok, reason = gate.check(topic or "")
    if ok:
        return True, reason
    if reason == "plan_title":
        payload = _PLAN_PREFIX_RE.sub("", topic or "")
        ok2, _ = gate.check(payload)
        if ok2:
            return True, "plan_title_with_payload"
    return False, reason


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


LAST_METRICS: dict = {}


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
    LAST_METRICS.update(entries=len(entries), active=len(active), not_indexed=len(not_indexed),
                        not_embedded=len(not_embedded), dup_groups=len(dup_groups), wasted=wasted)

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
    gate_dirs = gate_kb = -1
    if gate:
        bad_dirs, bad_kb = [], defaultdict(int)
        try:
            from aelvoxim.storage.db import fetch_dict, use_pg
            if use_pg():
                for r in fetch_dict("SELECT topic FROM learning_directions"):
                    ok, reason = topic_passes(gate, r["topic"])
                    if not ok:
                        bad_dirs.append((reason, r["topic"]))
                for r in fetch_dict("SELECT topic, count(*) n FROM knowledge_entries GROUP BY 1"):
                    ok, reason = topic_passes(gate, r["topic"])
                    if not ok:
                        bad_kb[reason] += int(r["n"])
        except Exception:
            pass
        gate_dirs, gate_kb = len(bad_dirs), sum(bad_kb.values())
        print("── gate")
        print(f"  directions_failing_gate={len(bad_dirs)}  kb_rows_failing_gate={sum(bad_kb.values())}")
        for reason, topic in bad_dirs[:6]:
            print(f"    [{reason}] {topic[:60]!r}")
        if bad_kb:
            print("   " + ", ".join(f"{k}={v}" for k, v in sorted(bad_kb.items())))

    if getattr(args, "check", False):
        warns = []
        if len(not_indexed) > 20:
            warns.append(f"active_not_indexed={len(not_indexed)} (>20)")
        if len(not_embedded) > 20:
            warns.append(f"active_not_embedded={len(not_embedded)} (>20)")
        if wasted > 50:
            warns.append(f"duplicate_titles_wasted={wasted} (>50)")
        if gate_dirs > 0:
            warns.append(f"directions_failing_gate={gate_dirs}")
        if gate_kb > 0:
            warns.append(f"kb_rows_failing_gate={gate_kb}")
        print(f"── check @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if warns:
            for w in warns:
                print(f"  WARN {w}")
            print("  RESULT=WARN")
            return 1
        print("  RESULT=OK (nothing above threshold)")
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
    """Rebuild the vector cache in the space _vector_search actually scores.

    Earlier this used storage.embedding.get_embedding (hash pseudo-vectors, 384/2048
    dims of noise). Those live in a different space than the bag-of-words TF vectors
    the retriever builds from the shared vocabulary, so backfilled entries could
    never rank. Delegate to the module's own rebuild instead.
    """
    from aelvoxim.learn import knowledge as kn

    index_ids = kn._read_index().get("entries", [])
    have = kn._load_embeddings()
    print(f"index_ids={len(index_ids)} embedded={len(have)}")
    if not args.apply:
        print("DRY-RUN (would run knowledge._build_or_refresh_embeddings(force=True))")
        return 0
    kn._EMBEDDING_CACHE = None          # force a fresh read of the file on disk
    kn._build_or_refresh_embeddings(force=True)
    kn._EMBEDDING_CACHE = None
    after = kn._load_embeddings()
    print(f"embedded_after={len(after)}")
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
    audit_dir = DATA / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (audit_dir / f"prune_dupes_{stamp}.json").write_text(json.dumps(
        [{"id": i, "title": active[i][1].get("title"), "topic": active[i][1].get("topic")} for i in victims],
        ensure_ascii=False, indent=1), encoding="utf-8")
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
    return len(victims)


def cmd_weekly(args):
    """Weekly upkeep: report → prune duplicate titles → catch index/vectors up → report.

    Order matters: prune first (drops ids from index+emb), then rebuild the index,
    then backfill vectors, so nothing that survived is left unindexed.
    """
    args.check = True
    cmd_status(args)
    m = dict(LAST_METRICS)
    print(f"── weekly plan: wasted={m.get('wasted')} not_indexed={m.get('not_indexed')} "
          f"not_embedded={m.get('not_embedded')}")

    if args.apply and (m.get("wasted") or 0) > 50:
        victims = cmd_prune_dupes(args)
        print(f"   pruned duplicate entries: {victims}")
    elif (m.get("wasted") or 0) > 50:
        print("   (dry-run) would prune duplicate titles")

    if args.apply and (m.get("not_indexed") or 0) > 0:
        cmd_rebuild_index(args)
    if args.apply and (m.get("not_embedded") or 0) > 0:
        cmd_backfill_emb(args)

    if args.apply:
        print("── after")
        cmd_status(args)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Aelvoxim knowledge-base GC")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--backfill-emb", action="store_true")
    ap.add_argument("--prune-dupes", action="store_true")
    ap.add_argument("--weekly", action="store_true",
                    help="prune dupes + catch up index/vectors (dry-run unless --apply)")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the KB drifts past thresholds (for the weekly timer)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.weekly:
        return cmd_weekly(args)
    if args.rebuild_index:
        return cmd_rebuild_index(args)
    if args.backfill_emb:
        return cmd_backfill_emb(args)
    if args.prune_dupes:
        cmd_prune_dupes(args)
        return 0
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
