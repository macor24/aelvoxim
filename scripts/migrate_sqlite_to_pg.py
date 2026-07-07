#!/usr/bin/env python3
"""
migrate_sqlite_to_pg — One-shot migration of SQLite/JSON data to PostgreSQL.

Migrates:
  - memory_entities (SQLite)   → PG memory_entities (with embedding)
  - memory_relations (SQLite)  → PG memory_relations (re-mapped to PG UUIDs)
  - knowledge_entries (JSON)   → PG knowledge_entries (with embedding)

Idempotent: safe to run multiple times; uses ON CONFLICT / DO NOTHING.
"""

import json
import os
import sqlite3
import sys
import uuid as _uuid
from pathlib import Path

# ── Paths ──
METACORE_DIR = Path.home() / ".aelvoxim"
SQLITE_DB = METACORE_DIR / "memory.db"
KB_ENTRIES_DIR = METACORE_DIR / "knowledge" / "entries"
PG_DSN = os.environ.get(
    "AELVOXIM_DATABASE_URL",
    "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass",
)


def _pseudo_embedding(text: str, dim: int = 384) -> list[float]:
    """Deterministic pseudo-embedding (matches aelvoxim.storage.embedding)."""
    import hashlib
    h = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "little")
    rng = __import__("random").Random(seed)
    return [round(rng.gauss(0, 0.1), 6) for _ in range(dim)]


def _stable_uuid(seed: str) -> str:
    """Deterministic UUID v5 from a seed string (idempotent)."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, seed))


def migrate_memory(sq: sqlite3.Connection, pg_conn) -> dict[str, str]:
    """Migrate SQLite entities → PG memory_entities. Returns {sqlite_id: pg_uuid}."""
    cur = pg_conn.cursor()
    mapping: dict[str, str] = {}
    rows = sq.execute("SELECT * FROM entities").fetchall()
    cols = [d[1] for d in sq.execute("PRAGMA table_info(entities)").fetchall()]

    migrated = 0
    skipped = 0
    for row in rows:
        rec = dict(zip(cols, row))
        sid = rec["id"]
        pg_id = _stable_uuid(f"entity:{sid}")
        content = str(rec.get("value", ""))
        etype = str(rec.get("type", "general"))
        tags_raw = rec.get("tags", "[]")
        if isinstance(tags_raw, str):
            try:
                tags = json.loads(tags_raw)
            except Exception:
                tags = []
        else:
            tags = tags_raw or []
        attrs_raw = rec.get("attributes", "{}")
        if isinstance(attrs_raw, str):
            try:
                attrs = json.loads(attrs_raw)
            except Exception:
                attrs = {}
        else:
            attrs = attrs_raw or {}
        created = str(rec.get("created_at", ""))[:19]

        emb = _pseudo_embedding(content[:500])
        try:
            cur.execute(
                """
                INSERT INTO memory_entities (id, name, entity_type, content, embedding, confidence, source, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s::jsonb, %s)
                ON CONFLICT (name, entity_type) DO NOTHING
                """,
                (pg_id, sid[:200], etype, content, str(emb), 1.0, "migration",
                 json.dumps({"tags": tags, "original_attributes": attrs}),
                 created if created else None),
            )
            migrated += 1
        except Exception:
            skipped += 1
        mapping[sid] = pg_id

    pg_conn.commit()
    print(f"  memory_entities: {migrated} inserted, {skipped} skipped (conflict)")
    return mapping


def migrate_relations(sq: sqlite3.Connection, pg_conn, mapping: dict[str, str]):
    """Migrate SQLite relations → PG memory_relations."""
    cur = pg_conn.cursor()
    rows = sq.execute("SELECT * FROM relations").fetchall()
    cols = [d[1] for d in sq.execute("PRAGMA table_info(relations)").fetchall()]

    migrated = 0
    skipped = 0
    for row in rows:
        rec = dict(zip(cols, row))
        rid = _stable_uuid(f"relation:{rec['id']}")
        src_pg = mapping.get(rec.get("source", ""))
        tgt_pg = mapping.get(rec.get("target", ""))
        if not src_pg or not tgt_pg:
            skipped += 1
            continue
        rel_type = str(rec.get("rel_type", "related"))
        try:
            cur.execute(
                """
                INSERT INTO memory_relations (id, source_id, target_id, relation_type, weight, created_at)
                VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (rid, src_pg, tgt_pg, rel_type, 1.0, None),
            )
            migrated += 1
        except Exception:
            skipped += 1

    pg_conn.commit()
    print(f"  memory_relations: {migrated} inserted, {skipped} skipped (orphan/conflict)")


def migrate_knowledge(pg_conn):
    """Migrate JSON knowledge entries → PG knowledge_entries."""
    cur = pg_conn.cursor()
    if not KB_ENTRIES_DIR.exists():
        print("  knowledge_entries: no entries directory found")
        return

    files = sorted(KB_ENTRIES_DIR.iterdir())
    migrated = 0
    skipped = 0
    for fp in files:
        if fp.suffix != ".json":
            continue
        try:
            entry = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue

        topic = str(entry.get("topic", "general"))[:200]
        title = str(entry.get("title", fp.stem))[:300]
        content = str(entry.get("content", entry.get("summary", "")))
        status = "active" if entry.get("validated", False) else "pending"
        src = str(entry.get("source", "migration"))[:50]
        tags = entry.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        validated = entry.get("validated_count", 1 if entry.get("validated") else 0)
        if isinstance(validated, bool):
            validated = 1 if validated else 0

        if not content.strip():
            skipped += 1
            continue

        emb = _pseudo_embedding(content[:500])
        try:
            cur.execute(
                """
                INSERT INTO knowledge_entries (id, topic, title, content, status, embedding, tags, validated_count, source)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, %s)
                ON CONFLICT (topic, title) DO NOTHING
                """,
                (_stable_uuid(f"kb:{topic}:{title}"), topic, title, content,
                 status, str(emb), json.dumps(tags), validated, src),
            )
            if cur.rowcount and cur.rowcount > 0:
                migrated += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    pg_conn.commit()
    print(f"  knowledge_entries: {migrated} inserted, {skipped} skipped (conflict/error)")


def main():
    import psycopg2

    print("=" * 50)
    print("SQLite/JSON → PostgreSQL Migration")
    print("=" * 50)

    # Connect SQLite
    if not SQLITE_DB.exists():
        print(f"SQLite DB not found: {SQLITE_DB}")
        sq = None
    else:
        sq = sqlite3.connect(str(SQLITE_DB))
        print(f"\n✓ SQLite: {SQLITE_DB} ({os.path.getsize(SQLITE_DB)} bytes)")

    # Connect PG
    try:
        pg = psycopg2.connect(PG_DSN)
        pg.autocommit = False
        print(f"✓ PostgreSQL: {PG_DSN.split()[0]}...")
    except Exception as e:
        print(f"✗ PostgreSQL connection failed: {e}")
        sys.exit(1)

    # Step 1: Memory entities
    print("\n── Step 1: memory_entities ──")
    mapping = {}
    if sq:
        mapping = migrate_memory(sq, pg)
    else:
        print("  Skipped (no SQLite)")

    # Step 2: Memory relations
    print("\n── Step 2: memory_relations ──")
    if sq and mapping:
        migrate_relations(sq, pg, mapping)
    else:
        print("  Skipped (no SQLite or no entity mapping)")

    # Step 3: Knowledge entries
    print("\n── Step 3: knowledge_entries ──")
    migrate_knowledge(pg)

    # Summary
    print("\n" + "=" * 50)
    cur = pg.cursor()
    for tbl in ("memory_entities", "memory_relations", "knowledge_entries"):
        cur.execute(f"SELECT count(*) FROM {tbl}")
        print(f"  {tbl}: {cur.fetchone()[0]} rows")
    cur.close()
    pg.close()
    if sq:
        sq.close()
    print("=" * 50)
    print("Migration complete.")


if __name__ == "__main__":
    main()
