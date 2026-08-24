# SPDX-License-Identifier: MIT
"""
metacore.memory.store — Memory with 3-layer architecture + SQLite persistence

Three layers:
- working: short-term context, 24h TTL
- episodic: conversation records, 7d TTL
- semantic: long-term knowledge, permanent

External API (unchanged):
    store_entity, search_entities, store_relation, get_relations,
    store_event, search_events, get_timeline,
    memory_read, memory_store, memory_search, memory_timeline
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import METACORE_DIR
from .entry import MemoryEntry, LAYER_WORKING, LAYER_EPISODIC, LAYER_SEMANTIC, LAYER_PROCEDURAL, should_store
from .fusion import MemoryFusion
from .forget import cleanup_all

_log = logging.getLogger("aelvoxim.memory")

_DB_PATH = str(Path(METACORE_DIR) / "memory.db")
_LEGACY_JSON_PATH = str(Path(METACORE_DIR) / "memory.json")
_local = threading.local()

# ── Fusion (3-layer) ──────────────────────

_fusion = MemoryFusion()


def _clean_stale_sqlite_files(path: str) -> None:
    """Remove orphaned .rollback, -wal, -shm files from prior crashes.

    These files are safe to delete ONLY when no other process holds the DB open.
    On server restart this is always the case.
    """
    import glob, os as _os
    for suffix in (".rollback", "-wal", "-shm"):
        for f in glob.glob(path + suffix):
            try:
                _os.remove(f)
            except OSError:
                pass


def _get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    else:
        # A cached connection that hit a transient disk I/O error stays broken
        # in the thread-local cache forever (every subsequent query raises).
        # Cheap probe: rebuild the connection once if it no longer works.
        try:
            conn.execute("SELECT 1").fetchone()
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            try:
                conn.close()
            except Exception:
                pass
            _local.conn = None
            return _get_db()
    return conn


# ── WARNING: do NOT add stale -wal/-shm cleanup back ─────────────
# SQLite WAL mode manages -wal/-shm file lifecycle itself, and crash
# recovery (replay on open) is built in. Manually deleting these files
# while another connection/process holds them open sends that writer's
# commits into orphaned (deleted) inodes: store_entity returns True but
# the row is invisible to every other reader. Root cause of the 1094
# "disk I/O error" events and the lost entity writes seen 2026-08-01.


# ── Layer-aware helpers ───────────────────


def _determine_layer(entry: MemoryEntry) -> str:
    """Determine which layer a MemoryEntry belongs to based on importance + access.

    Dormant/archived entries stay in their current layer.
    person/preference entities are long-term by nature → semantic layer.
    """
    if entry.conflict_status not in ("active", "pending"):
        return entry.layer
    if entry.access_count >= 5 or entry.importance >= 0.95:
        return LAYER_PROCEDURAL
    if entry.immutable or entry.importance >= 0.8:
        return LAYER_SEMANTIC
    if any(t in ("person", "preference") for t in entry.tags):
        return LAYER_SEMANTIC
    if entry.importance >= 0.5 or entry.access_count >= 3:
        return LAYER_EPISODIC
    return LAYER_WORKING


def _store_to_fusion(entry: MemoryEntry) -> MemoryEntry:
    """Store entry into the appropriate layer."""
    layer = _determine_layer(entry)
    target = _fusion.get_layer(layer)
    if target and layer != entry.layer:
        entry.layer = layer
        if layer == LAYER_SEMANTIC:
            entry.ttl_seconds = None
    if target:
        target.store(entry)
    else:
        _fusion.working.store(entry)
    # Update inverted index incrementally
    _fusion.add_to_index(layer, entry.key, entry)
    return entry


# ── PG-first persistence layer ─────────────────────────
# When PostgreSQL is available, memory reads/writes go through PG
# (memory_entities / memory_relations / memory_events). SQLite stays as
# the no-PG fallback. The in-memory fusion layers remain the retrieval
# cache and are kept in sync by the callers.

def _pg_active() -> bool:
    """Return True when PostgreSQL is usable for memory storage."""
    try:
        from ..storage.db import use_pg
        return use_pg()
    except Exception:
        return False


def _pg_upsert_entity(eid: str, etype: str, value: str, tags: list,
                      attributes: dict, user_id: str) -> bool:
    """Upsert one entity row into PG memory_entities (name = eid)."""
    try:
        from ..storage.db import execute
        import json as _js
        from ..storage.embedding import get_embedding
        _emb = get_embedding(value or eid)
        _meta = {"tags": tags or [], "attributes": attributes or {}}
        execute(
            """INSERT INTO memory_entities (name, entity_type, content, embedding, source, metadata, user_id)
               VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb, %s)
               ON CONFLICT (name, entity_type) DO UPDATE SET
                   content = EXCLUDED.content,
                   embedding = EXCLUDED.embedding,
                   metadata = EXCLUDED.metadata,
                   user_id = EXCLUDED.user_id,
                   updated_at = NOW()""",
            (eid[:200], etype, value, str(_emb), "chat", _js.dumps(_meta), user_id or ""),
        )
        return True
    except Exception:
        _log.exception("pg upsert entity error")
        return False


def _pg_delete_entity(eid: str, user_id: str = "") -> bool:
    """Delete an entity row from PG memory_entities by name."""
    try:
        from ..storage.db import execute
        if user_id:
            execute("DELETE FROM memory_entities WHERE name = %s AND user_id = %s", (eid, user_id))
        else:
            execute("DELETE FROM memory_entities WHERE name = %s", (eid,))
        return True
    except Exception:
        _log.exception("pg delete entity error")
        return False


def _pg_store_event(eid: str, event_type: str, participants: list,
                    content: str, ts: str, user_id: str = "") -> bool:
    """Insert one event row into PG memory_events."""
    try:
        from ..storage.db import execute
        import json as _js
        execute(
            """INSERT INTO memory_events (event_type, participants, content, event_ts, user_id)
               VALUES (%s, %s::jsonb, %s, %s, %s)""",
            (event_type, _js.dumps(participants or []), content, ts, user_id or ""),
        )
        return True
    except Exception:
        _log.exception("pg store event error")
        return False


def _pg_search_events(event_type: Optional[str] = None, participant: Optional[str] = None,
                      since: Optional[str] = None, query: str = "",
                      limit: int = 50, user_id: str = "") -> List[dict]:
    """Search events in PG memory_events."""
    try:
        from ..storage.db import fetch_dict
        clauses, params = [], []
        if event_type:
            clauses.append("event_type = %s"); params.append(event_type)
        if participant:
            clauses.append("participants::text LIKE %s"); params.append(f"%{participant}%")
        if since:
            clauses.append("event_ts >= %s"); params.append(since)
        if query:
            clauses.append("(content LIKE %s OR event_type LIKE %s)"); params.extend([f"%{query}%", f"%{query}%"])
        if user_id:
            clauses.append("user_id = %s"); params.append(user_id)
        where = " AND ".join(clauses) if clauses else "TRUE"
        rows = fetch_dict(
            f"SELECT event_type, participants, content, event_ts, user_id FROM memory_events "
            f"WHERE {where} ORDER BY event_ts DESC LIMIT %s",
            tuple(params + [limit]),
        )
        return [{
            "id": r["event_ts"],
            "type": r["event_type"],
            "participants": r.get("participants") or [],
            "content": r.get("content", ""),
            "timestamp": r.get("event_ts", ""),
            "user_id": r.get("user_id", ""),
        } for r in rows]
    except Exception:
        _log.exception("pg search events error")
        return []


def _pg_fetch_entity(eid: str, user_id: str = "") -> Optional[dict]:
    """Fetch one entity row from PG memory_entities by name."""
    try:
        from ..storage.db import fetch_dict
        if user_id:
            rows = fetch_dict(
                "SELECT name, entity_type, content, metadata, user_id FROM memory_entities "
                "WHERE name = %s AND user_id = %s LIMIT 1", (eid, user_id))
        else:
            rows = fetch_dict(
                "SELECT name, entity_type, content, metadata, user_id FROM memory_entities "
                "WHERE name = %s LIMIT 1", (eid,))
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r["name"],
            "key": r["name"],
            "type": r.get("entity_type", "concept"),
            "value": r.get("content", ""),
            "tags": (r.get("metadata") or {}).get("tags", []),
            "attributes": (r.get("metadata") or {}).get("attributes", {}),
            "user_id": r.get("user_id", ""),
        }
    except Exception:
        _log.exception("pg fetch entity error")
        return None


def _pg_search_entities(query: str, etype: Optional[str] = None,
                        limit: int = 20, user_id: str = "") -> List[dict]:
    """Search entities in PG memory_entities (name/content LIKE)."""
    try:
        from ..storage.db import fetch_dict
        clauses, params = [], []
        if query:
            q = f"%{query}%"
            clauses.append("(name LIKE %s OR content LIKE %s)")
            params.extend([q, q])
        if etype:
            clauses.append("entity_type = %s"); params.append(etype)
        if user_id:
            clauses.append("user_id = %s"); params.append(user_id)
        where = " AND ".join(clauses) if clauses else "TRUE"
        rows = fetch_dict(
            f"SELECT name, entity_type, content, metadata, user_id FROM memory_entities "
            f"WHERE {where} ORDER BY updated_at DESC LIMIT %s",
            tuple(params + [limit]),
        )
        return [{
            "id": r["name"],
            "key": r["name"],
            "type": r.get("entity_type", "concept"),
            "value": r.get("content", ""),
            "tags": (r.get("metadata") or {}).get("tags", []),
            "attributes": (r.get("metadata") or {}).get("attributes", {}),
            "user_id": r.get("user_id", ""),
        } for r in rows]
    except Exception:
        _log.exception("pg search entities error")
        return []


def _pg_store_relation(rel_id: str, source: str, target: str, rel_type: str,
                       attributes: Optional[Dict] = None) -> bool:
    """Insert a relation row into PG memory_relations (by entity names)."""
    try:
        from ..storage.db import execute
        import json as _js
        execute(
            """INSERT INTO memory_relations (source_name, target_name, relation_type, weight)
               VALUES (%s, %s, %s, %s)""",
            (source[:200], target[:200], rel_type,
             float((attributes or {}).get("_strength", 0.5))),
        )
        return True
    except Exception:
        _log.exception("pg store relation error")
        return False


def _pg_get_relations(entity_id: Optional[str] = None,
                      rel_type: Optional[str] = None,
                      direction: str = "both") -> List[dict]:
    """Fetch relations from PG memory_relations by entity name."""
    try:
        from ..storage.db import fetch_dict
        clauses, params = [], []
        if entity_id:
            if direction == "out":
                clauses.append("source_name = %s"); params.append(entity_id)
            elif direction == "in":
                clauses.append("target_name = %s"); params.append(entity_id)
            else:
                clauses.append("(source_name = %s OR target_name = %s)")
                params.extend([entity_id, entity_id])
        if rel_type:
            clauses.append("relation_type = %s"); params.append(rel_type)
        where = " AND ".join(clauses) if clauses else "TRUE"
        rows = fetch_dict(
            f"SELECT source_name, target_name, relation_type, weight, created_at "
            f"FROM memory_relations WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [{
            "id": f"{r.get('source_name','')}-{r.get('target_name','')}",
            "source": r.get("source_name", ""),
            "target": r.get("target_name", ""),
            "type": r.get("relation_type", "related"),
            "attributes": {"_strength": r.get("weight", 0.5)},
            "created_at": str(r.get("created_at", "") or ""),
        } for r in rows]
    except Exception:
        _log.exception("pg get relations error")
        return []


def _read_from_layers(key: str) -> Optional[MemoryEntry]:
    """Read from all layers, check for promotion."""
    for l in [_fusion.working, _fusion.episodic, _fusion.semantic]:
        entry = l._entries.get(key)
        if entry and not entry.is_expired():
            entry.touch()
            # Check promotion
            current_layer = _determine_layer(entry)
            if current_layer != entry.layer:
                l._entries.pop(key, None)
                _store_to_fusion(entry)
            return entry
    return None


# ── Schema ────────────────────────────────


def _init_db():
    db = _get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'concept',
            value TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            attributes TEXT NOT NULL DEFAULT '{}',
            user_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            rel_type TEXT NOT NULL DEFAULT 'related',
            attributes TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'event',
            participants TEXT NOT NULL DEFAULT '[]',
            content TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
        CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id);
        CREATE INDEX IF NOT EXISTS idx_entities_tags ON entities(tags);
        CREATE INDEX IF NOT EXISTS idx_entities_locked ON entities(locked);
        CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
        CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
    """);
    # Safe migration: add locked column for existing DBs (no-op if column already exists)
    try:
        db.execute("ALTER TABLE entities ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
        db.commit()
    except Exception:
        pass  # column already exists
    db.commit()


# ── Migration ─────────────────────────────


def _migrate_from_json():
    db = _get_db()
    count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    if count > 0:
        return
    legacy = Path(_LEGACY_JSON_PATH)
    if not legacy.exists():
        return
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for eid, entity in data.get("entities", {}).items():
        etype = entity.get("type", "concept")
        tags = json.dumps(entity.get("tags", []), ensure_ascii=False)
        attrs = json.dumps(entity.get("attributes", {}), ensure_ascii=False)
        value = entity.get("value") or entity.get("attributes", {}).get("name", "") or ""
        user_id = ""
        for t in (entity.get("tags") or []):
            if t.startswith("user:"):
                user_id = t[5:]
                break
        try:
            db.execute(
                "INSERT OR IGNORE INTO entities (id, type, value, tags, attributes, user_id, created_at) VALUES (?,?,?,?,?,?,?)",
                (eid, etype, str(value)[:500], tags, attrs, user_id, now),
            )
            # Also populate fusion layers
            entry = MemoryEntry(key=eid, value=value, tags=entity.get("tags", []),
                                importance=0.5, timestamp=now, source="migration",
                                user_id=user_id)
            _store_to_fusion(entry)
        except Exception:
            _log.exception("__init__ error")

    for relation in data.get("relations", []):
        rid = relation.get("id") or f"rel:{uuid.uuid4().hex[:12]}"
        try:
            db.execute(
                "INSERT OR IGNORE INTO relations (id, source, target, rel_type, attributes, created_at) VALUES (?,?,?,?,?,?)",
                (rid, relation.get("source", ""), relation.get("target", ""),
                 relation.get("type", "related"),
                 json.dumps(relation.get("attributes", {}), ensure_ascii=False), now),
            )
        except Exception:
            _log.exception("__init__ error")

    for event in data.get("events", []):
        eid = event.get("id") or f"ev:{uuid.uuid4().hex[:12]}"
        try:
            db.execute(
                "INSERT OR IGNORE INTO events (id, type, participants, content, timestamp, user_id) VALUES (?,?,?,?,?,?)",
                (eid, event.get("type", "event"),
                 json.dumps(event.get("participants", []), ensure_ascii=False),
                 event.get("content", ""), event.get("timestamp", ""),
                 event.get("user_id", "")),
            )
        except Exception:
            _log.exception("__init__ error")

    db.commit()
    legacy.rename(legacy.with_suffix(".json.bak"))


def _load_fusion_from_db():
    """Load entities into fusion layers on startup.

    PG-first: loads from PostgreSQL when available, else SQLite.
    Restores importance (from confidence_metadata.overall), strength, status
    and TTL from attributes so restart does not lose memory state.
    """
    if _pg_active():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict(
                "SELECT name, entity_type, content, metadata, user_id FROM memory_entities "
                "ORDER BY updated_at DESC")
            loaded = 0
            for row in rows:
                eid = row["name"]
                value = row.get("content") or ""
                _meta = row.get("metadata") or {}
                tags_list = _meta.get("tags", []) or []
                attrs = _meta.get("attributes", {}) or {}
                _cm = attrs.get("confidence_metadata") if isinstance(attrs, dict) else None
                if isinstance(_cm, dict) and isinstance(_cm.get("overall"), (int, float)):
                    importance = float(_cm["overall"])
                else:
                    importance = 0.5
                    if "person" in tags_list or "preference" in tags_list:
                        importance = 0.7
                entry = MemoryEntry(
                    key=eid, value=value or "", tags=tags_list,
                    importance=importance,
                    # PG returns datetime for timestamps; MemoryEntry.timestamp
                    # is a str field — datetime breaks strptime() in TTL/age
                    # calcs (B2, 9.txt audit).
                    timestamp=str(row.get("updated_at") or ""),
                    source="db_reload", entities=[eid],
                    user_id=row.get("user_id") or "")
                _store_to_fusion(entry)
                loaded += 1
            if loaded > 0:
                _fusion.rebuild_index()
                _log.info("🧠 PG 加载 %d 条实体到融合层，索引 %d 词条", loaded, len(_fusion._inverted_index))
            return
        except Exception:
            _log.exception("pg load fusion error; falling back to SQLite")
    db = _get_db()
    rows = db.execute(
        "SELECT id, value, tags, attributes, user_id, created_at FROM entities ORDER BY created_at DESC"
    ).fetchall()
    loaded = 0
    for row in rows:
        eid = row["id"]
        value = row["value"]
        tags_list = json.loads(row["tags"] or "[]")
        attrs = {}
        try:
            attrs = json.loads(row["attributes"] or "{}")
        except Exception:
            _log.warning("db_reload: invalid attributes JSON for %s", eid)
        # Importance: prefer persisted confidence_metadata.overall, else tag-based default
        _cm = attrs.get("confidence_metadata")
        if isinstance(_cm, dict) and isinstance(_cm.get("overall"), (int, float)):
            importance = float(_cm["overall"])
        else:
            importance = 0.5
            if "extracted" in tags_list:
                importance = 0.6
            if "person" in tags_list or "preference" in tags_list:
                importance = 0.7
        # Strength / status written by decay.py
        _strength = attrs.get("strength", 1.0)
        try:
            _strength = float(_strength)
        except (TypeError, ValueError):
            _strength = 1.0
        _status = attrs.get("status", "active")
        if _status not in ("active", "pending", "dormant", "archived"):
            _status = "active"
        # TTL: _ttl is in days (0 / -1 / absent = permanent)
        _ttl_days = attrs.get("_ttl")
        _ttl_seconds = None
        if isinstance(_ttl_days, (int, float)) and _ttl_days > 0:
            _ttl_seconds = int(_ttl_days) * 86400
        entry = MemoryEntry(key=eid, value=value or "", tags=tags_list,
                            importance=importance, timestamp=row["created_at"],
                            source="db_reload", entities=[eid],
                            user_id=row["user_id"] or "",
                            strength=_strength, conflict_status=_status,
                            ttl_seconds=_ttl_seconds)
        _store_to_fusion(entry)
        loaded += 1
    if loaded > 0:
        _fusion.rebuild_index()
        _log.info("🧠 已加载 %d 条实体到融合层，索引 %d 词条", loaded, len(_fusion._inverted_index))


_init_db()
_migrate_from_json()
_load_fusion_from_db()

# ── Confidence metadata migration for legacy entities ──

def _migrate_confidence_metadata():
    """Backfill confidence_metadata for entities that lack it.

    One-shot migration on startup. Only processes entities whose
    attributes JSON does not contain 'confidence_metadata'.
    """
    try:
        from .conf_matrix import compute_5d as _c5d
        db = _get_db()
        rows = db.execute(
            "SELECT id, type, value, tags, attributes, created_at FROM entities "
            "WHERE attributes NOT LIKE ? AND attributes != '' AND attributes != '{}' LIMIT 1000",
            ('%confidence_metadata%',)
        ).fetchall()
        if not rows:
            return
        updated = 0
        for row in rows:
            try:
                tags_list = json.loads(row["tags"] or "[]")
                attrs = json.loads(row["attributes"] or "{}")
            except Exception as _mig_e:
                _log.warning("Migration: skip entity %s: %s", row["id"], _mig_e)
                continue
            if "confidence_metadata" in attrs:
                continue
            meta = _c5d(
                tags=tags_list,
                source=attrs.get("source", ""),
                value=row["value"] or "",
                timestamp_str=row["created_at"] or "",
                mention_count=1,
                has_conflict=attrs.get("_conflict", False),
            )
            attrs["confidence_metadata"] = meta
            db.execute(
                "UPDATE entities SET attributes = ? WHERE id = ?",
                (json.dumps(attrs, ensure_ascii=False), row["id"]),
            )
            updated += 1
        if updated:
            db.commit()
            _log.info("Backfilled %d entities with confidence metadata", updated)
    except Exception:
        _log.exception("__init__ error")

_migrate_confidence_metadata()


# ═══════════════════════════════════════════
# External API (unchanged signatures)
# ═══════════════════════════════════════════


# ── Entity operations ─────────────────────


def store_entity(eid: str, etype: str, attributes: dict,
                 tags: Optional[List[str]] = None,
                 user_id: str = "") -> bool:
    """Store or update an entity (3-layer aware)."""
    # PG-first: when PostgreSQL is available, persist there; fusion layers
    # (in-memory) are still updated below via _store_to_fusion.
    if _pg_active():
        _value = str(attributes.get("name") or attributes.get("value") or "")[:500]
        _ok = _pg_upsert_entity(eid, etype, _value, tags or [], attributes, user_id)
        if not _ok:
            return False
        try:
            _pentry = MemoryEntry(
                key=eid, value=_value, tags=tags or [],
                importance=0.5, timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                source="chat", entities=[eid], user_id=user_id,
                base_importance=0.5, access_count=1)
            _store_to_fusion(_pentry)
        except Exception:
            _log.exception("pg store_entity fusion sync error")
        _audit_memory("memory_write", eid, user_id, {"type": etype})
        return True
    db = _get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    attrs_json = json.dumps(attributes, ensure_ascii=False)
    value = str(attributes.get("name") or attributes.get("value") or "")[:500]
    # Check for lock-on-write
    _lock_flag = attributes.pop("_lock", False) if isinstance(attributes, dict) else False
    try:
        # ── Prevalidation (write-before-simulate check) ──
        try:
            from .prevalidation import prevalidate
            _pv = prevalidate(eid, value, tags, _fusion, db)
            if _pv["action"] == "block":
                # Mark attributes with conflict metadata and return early
                attributes["_prevalidated"] = "blocked"
                attributes["_pv_reason"] = _pv["reason"][:200]
                attrs_json = json.dumps(attributes, ensure_ascii=False)
                # Still write but with conflict metadata instead of blocking entirely
                # This lets the existing promotion/review pipeline handle it
        except Exception:
            _log.exception("__init__ error")
        # Preserve original type on re-insert, but upgrade org→location
        _old_type_row = db.execute("SELECT type FROM entities WHERE id = ?", (eid,)).fetchone()
        if _old_type_row:
            _old_t = _old_type_row[0]
            if not (etype == "location" and _old_t == "organization"):
                etype = _old_t  # keep old unless upgrading org→location
            # L2 belief locking: confidence >= 0.9 entities cannot be auto-modified
            if not attributes.get("_force_update"):
                _old_attrs = db.execute("SELECT attributes FROM entities WHERE id = ?", (eid,)).fetchone()
                if _old_attrs:
                    import json as _js
                    _oa = _js.loads(_old_attrs[0] or "{}") if isinstance(_old_attrs[0], str) else (_old_attrs[0] or {})
                    if _oa.get("_confidence", 0) >= 0.9:
                        return True  # skip modification
        # ── Cross-session mention count: read BEFORE INSERT (id is PRIMARY KEY,
        #    so COUNT(*) after INSERT is always 1 — old code always yielded 2) ──
        _prev_mention = 0
        _old_attrs_row = db.execute(
            "SELECT attributes FROM entities WHERE id = ? AND user_id = ?",
            (eid, user_id),
        ).fetchone()
        if _old_attrs_row and _old_attrs_row[0]:
            try:
                _prev_attrs = json.loads(_old_attrs_row[0])
                _prev_mention = int(_prev_attrs.get("_mention_count", 0))
            except Exception:
                _prev_mention = 0
        _mention_count = _prev_mention + 1
        db.execute(
            """INSERT OR REPLACE INTO entities
               (id, type, value, tags, attributes, user_id, created_at, locked)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                   (SELECT created_at FROM entities WHERE id = ?), ?
               ), ?)""",
            (eid, etype, value, tags_json, attrs_json, user_id, eid, now,
             1 if _lock_flag else 0),
        )
        db.commit()
        # ── Adaptive scoring ──
        try:
            from .scorer import compute_confidence, detect_ttl
            from datetime import timedelta
            # _mention_count was computed BEFORE the INSERT above (id is PRIMARY
            # KEY, so a COUNT(*) here would always be 1 — see the pre-INSERT block)
            # Check if previous value exists (conflict detection)
            _prev = db.execute(
                "SELECT value FROM entities WHERE id = ? AND user_id = ?",
                (eid, user_id)
            ).fetchone()
            _prev_value = _prev[0] if _prev else ""
            if _prev_value and value and _prev_value != value:
                # Value changed — record version chain
                attributes["_superseded"] = _prev_value
                attributes["_superseded_at"] = now
                # Detect conflict for important entities
                try:
                    from .conflict import detect_conflict as _dc
                    _cf = _dc(eid, value, tags or [], _fusion, db)
                    if _cf and _cf.get("_conflict"):
                        attributes.update(_cf)
                except Exception:
                    _log.exception("__init__ error")
            # Time tag detection
            _src = str(attributes.get("extracted_from", "")) or value
            _ttl = detect_ttl(_src)
            if _ttl is not None:
                attributes["_ttl"] = _ttl
                if _ttl > 0:
                    _exp = (datetime.now() + timedelta(days=_ttl)).strftime("%Y-%m-%d")
                    attributes["_expires_at"] = _exp
            _conf = compute_confidence(
                tag=tags[1] if len(tags) >= 2 else tags[0] if tags else "general",
                text=_src,
                mention_count=_mention_count,
                days_since_last=0,
            )
            importance = _conf
            # ── 5-dimension confidence metadata ──
            try:
                from .scorer import compute_5d_metadata as _c5d
                _has_conflict = attributes.get("_conflict", False)
                _c5d_result = _c5d(
                    tags=tags or [],
                    source=attributes.get("source", ""),
                    value=value,
                    timestamp_str=now,
                    mention_count=_mention_count,
                    has_conflict=_has_conflict,
                )
                attributes["confidence_metadata"] = _c5d_result
            except Exception:
                _log.exception("__init__ error")
            # Persist cross-session mention count so the next store() can
            # read it back (the pre-INSERT block above re-reads this key)
            attributes["_mention_count"] = _mention_count
            # Update DB with attributes (TTL, superseded, etc.)
            _attrs_j2 = json.dumps(attributes, ensure_ascii=False)
            db.execute("UPDATE entities SET attributes = ? WHERE id = ? AND user_id = ?",
                       (_attrs_j2, eid, user_id))
            db.commit()
        except Exception:
            # Adaptive scoring failed — fall back to tag-based importance.
            # Only reached when the scoring block above raised; on success
            # `importance` already holds the computed confidence score.
            _log.exception("adaptive scoring failed; using tag-based fallback")
            importance = 0.5
            if tags:
                if "extracted" in tags:
                    importance = 0.6
                if "person" in tags or "preference" in tags:
                    importance = 0.8  # semantic-level
                if "location" in tags:
                    importance = 0.7  # episodic-level
        # Also update fusion layer
        # Check if this key already exists with higher importance (upgrade path)
        # Note: must include the *working* layer — entries stored moments ago
        # live there, and without it the upgrade check never ran for them
        # (test_l4_promotion failed on clean runners; only stale local DBs
        # happened to have the entity in semantic/episodic).
        existing = _fusion.get_layer(LAYER_SEMANTIC)._entries.get(eid)
        if not existing:
            existing = _fusion.get_layer(LAYER_EPISODIC)._entries.get(eid)
        if not existing:
            existing = _fusion.get_layer(LAYER_WORKING)._entries.get(eid)
        if existing:
            existing.touch()
            entry = existing
            # Cross-session mention count (computed in adaptive scoring block above)
            if _mention_count >= 2 or entry.access_count >= 2 or entry.importance >= 0.7:
                # Upgrade to semantic
                entry.layer = LAYER_SEMANTIC
                entry.ttl_seconds = None
                # Remove from old layer
                if entry.key in _fusion.working._entries:
                    del _fusion.working._entries[entry.key]
                if entry.key in _fusion.episodic._entries:
                    del _fusion.episodic._entries[entry.key]
                _fusion.semantic._entries[entry.key] = entry
                # Write to SQLite too
                _tags_j = json.dumps(entry.tags, ensure_ascii=False)
                # Merge (not overwrite): preserve confidence_metadata,
                # _mention_count, _ttl, _superseded etc. already computed
                # in the adaptive-scoring block above.
                _attrs_j = json.dumps({**attributes, "name": value}, ensure_ascii=False)
                db.execute(
                    "INSERT OR REPLACE INTO entities (id, type, value, tags, attributes, user_id, created_at, locked) VALUES (?,?,?,?,?,?,COALESCE((SELECT created_at FROM entities WHERE id = ?), ?), COALESCE((SELECT locked FROM entities WHERE id = ?), 0))",
                    (entry.key, etype, str(value)[:500], _tags_j, _attrs_j, user_id, entry.key, now, entry.key),
                )
                db.commit()
                # Upgrade to procedural if access_count >= 5
                try:
                    _e = _fusion.semantic._entries.get(eid) or _fusion.episodic._entries.get(eid) or _fusion.working._entries.get(eid)
                    if _e and _e.access_count >= 5 and eid not in _fusion.procedural._entries:
                        _e.layer = LAYER_PROCEDURAL
                        for _lk in [LAYER_SEMANTIC, LAYER_EPISODIC, LAYER_WORKING]:
                            _l = _fusion.get_layer(_lk)
                            if _l and eid in _l._entries:
                                del _l._entries[eid]
                        _fusion.procedural._entries[eid] = _e
                except Exception:
                    _log.exception("__init__ error")
                _audit_memory("memory_write", eid, user_id, {"type": etype})
                return True
        entry = MemoryEntry(key=eid, value=value, tags=tags or [],
                            importance=importance, timestamp=now,
                            source="chat", entities=[eid], user_id=user_id,
                            base_importance=importance, access_count=1,
                            decay_rate=0.02 if importance >= 0.8 else 0.05)
        _store_to_fusion(entry)
        _audit_memory("memory_write", eid, user_id, {"type": etype, "confidence": importance})
        return True
    except Exception:
        return False


# ── Lock/unlock operations ──────────────────


def lock_entity(eid: str, user_id: str = "") -> bool:
    """Lock an entity to protect it from cache cleanup.

    Locked entities go into 'confirmed info' (permanent layer)
    and are not removed by session cache cleanup.
    """
    if _pg_active():
        try:
            from ..storage.db import execute
            execute(
                "UPDATE memory_entities SET metadata = jsonb_set(COALESCE(metadata,'{}'), '{locked}', 'true'::jsonb) WHERE name = %s",
                (eid,))
            _audit_memory("memory_lock", eid, user_id or "", None)
            return True
        except Exception:
            _log.exception("pg lock entity error")
            return False
    db = _get_db()
    try:
        _uid = user_id or ""
        db.execute(
            "UPDATE entities SET locked = 1 WHERE id = ? AND user_id = ?",
            (eid, _uid),
        )
        db.commit()
        _audit_memory("memory_lock", eid, _uid, None)
        return True
    except Exception:
        return False


def unlock_entity(eid: str, user_id: str = "") -> bool:
    """Unlock a previously locked entity."""
    if _pg_active():
        try:
            from ..storage.db import execute
            execute(
                "UPDATE memory_entities SET metadata = jsonb_set(COALESCE(metadata,'{}'), '{locked}', 'false'::jsonb) WHERE name = %s",
                (eid,))
            _audit_memory("memory_unlock", eid, user_id or "", None)
            return True
        except Exception:
            _log.exception("pg unlock entity error")
            return False
    db = _get_db()
    try:
        _uid = user_id or ""
        db.execute(
            "UPDATE entities SET locked = 0 WHERE id = ? AND user_id = ?",
            (eid, _uid),
        )
        db.commit()
        _audit_memory("memory_unlock", eid, _uid, None)
        return True
    except Exception:
        return False


def is_locked(eid: str) -> bool:
    """Check whether an entity is locked."""
    if _pg_active():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict(
                "SELECT metadata FROM memory_entities WHERE name = %s LIMIT 1", (eid,))
            if rows:
                return bool((rows[0].get("metadata") or {}).get("locked"))
            return False
        except Exception:
            _log.exception("pg is_locked error")
            return False
    db = _get_db()
    try:
        row = db.execute(
            "SELECT locked FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


# ── Cache cleanup ───────────────────────────


def cleanup_events(before_days: int = 30) -> int:
    """Delete chat events older than before_days.

    Only removes events of type 'chat_inquiry' (conversation logs).
    Returns count of deleted rows.
    """
    from datetime import timedelta as _td
    _cutoff = (datetime.now() - _td(days=before_days)).strftime("%Y-%m-%d %H:%M:%S")
    if _pg_active():
        try:
            from ..storage.db import execute
            execute(
                "DELETE FROM memory_events WHERE event_type = 'chat_inquiry' AND event_ts < %s",
                (_cutoff,))
            return 1
        except Exception:
            _log.exception("pg cleanup events error")
            return 0
    db = _get_db()
    try:
        _cur = db.execute(
            "DELETE FROM events WHERE type = 'chat_inquiry' AND timestamp < ?",
            (_cutoff,),
        )
        db.commit()
        return _cur.rowcount
    except Exception:
        return 0


def cleanup_unlocked_entities(before_days: int = 30, user_id: str = "") -> int:
    """Delete unlocked entities created before before_days.

    Preserves locked (confirmed) entities.
    When user_id is given, only that user's entities are deleted.
    Returns count of deleted rows.
    """
    from datetime import timedelta as _td
    _cutoff = (datetime.now() - _td(days=before_days)).strftime("%Y-%m-%d %H:%M:%S")
    if _pg_active():
        try:
            from ..storage.db import execute
            # locked=0 guard: confirmed entities must survive cleanup (B3,
            # 9.txt audit — the DELETE previously removed locked rows too).
            _sql = "DELETE FROM memory_entities WHERE created_at < %s AND (locked = 0 OR locked IS NULL)"
            _params = [_cutoff]
            if user_id:
                _sql += " AND user_id = %s"
                _params.append(user_id)
            execute(_sql, tuple(_params))
            return 1
        except Exception:
            _log.exception("pg cleanup entities error")
            return 0
    db = _get_db()
    try:
        _sql = "DELETE FROM entities WHERE locked = 0 AND created_at < ?"
        _params = [_cutoff]
        if user_id:
            _sql += " AND user_id = ?"
            _params.append(user_id)
        _cur = db.execute(_sql, _params)
        db.commit()
        return _cur.rowcount
    except Exception:
        return 0


# ── Helper: search_entities returns locked field ──


def search_entities(query: str, etype: Optional[str] = None,
                    limit: int = 20, user_id: str = "") -> List[dict]:
    """Search entities (fusion cache first, then PG or SQLite)."""
    # First try fusion (3-layer in-memory)
    fusion_results = _fusion.search(query=query, limit=limit * 2)
    if fusion_results:
        result_dicts = []
        for e in fusion_results:
            if user_id and getattr(e, "user_id", "") != user_id:
                continue
            result_dicts.append({
                "id": e.key,
                "key": e.key,
                "type": e.layer,
                "value": str(e.value),
                "tags": e.tags,
                "attributes": {"name": str(e.value)},
                "user_id": getattr(e, "user_id", ""),
            })
            if len(result_dicts) >= limit:
                break
        if result_dicts:
            return result_dicts

    # PG fallback (no-PG environments keep using SQLite below)
    if _pg_active():
        _pg_results = _pg_search_entities(query, etype=etype, limit=limit, user_id=user_id)
        if _pg_results:
            return _pg_results

    # Fallback to SQLite
    db = _get_db()
    q = query.lower().strip()
    if not q:
        return []
    clauses = ["1=1"]
    params: list = []
    # Handle special query: "extracted" → search tags
    if q == "extracted":
        clauses.append("tags LIKE ?")
        params.append("%extracted%")
    elif etype:
        clauses.append("type = ?")
        params.append(etype)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)

    sql = f"SELECT * FROM entities WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"
    rows = db.execute(sql, params).fetchall()

    scored: List[Tuple[int, dict]] = []
    q_chars = set(c for c in q if '\u4e00' <= c <= '\u9fff')
    for row in rows:
        eid = row["id"]
        value = row["value"]
        tags_list = json.loads(row["tags"] or "[]")
        attributes = json.loads(row["attributes"] or "{}")
        locked = bool(row["locked"]) if "locked" in row.keys() else False
        score = 0
        if q in eid.lower():
            score += 20
        if q in value.lower():
            score += 10
        if any(q in t.lower() for t in tags_list):
            score += 10
        if any(q in str(v).lower() for v in attributes.values()):
            score += 10
        for w in q.split():
            if len(w) > 2:
                score += 5 if (w in eid.lower() or w in value.lower()) else 0
        if q_chars:
            _all = set(c for c in eid.lower() if '\u4e00' <= c <= '\u9fff')
            _all.update(c for c in value.lower() if '\u4e00' <= c <= '\u9fff')
            for t in tags_list:
                _all.update(c for c in t.lower() if '\u4e00' <= c <= '\u9fff')
            for v in attributes.values():
                _all.update(c for c in str(v).lower() if '\u4e00' <= c <= '\u9fff')
            _common = len(q_chars & _all)
            if _common >= 2:
                score += _common * 2
        if score > 0:
            scored.append((score, {
                "id": eid, "key": eid, "type": row["type"],
                "value": value, "tags": tags_list,
                "attributes": attributes, "user_id": row["user_id"],
                "locked": locked,
            }))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [e for _, e in scored[:limit]]

    # ── Relation enhancement: for each result entity, attach 1-hop relations ──
    try:
        _rel_db = _get_db()
        for _r_ent in results:
            _eid = _r_ent.get("id") or _r_ent.get("key", "")
            if not _eid:
                continue
            _rel_rows = _rel_db.execute(
                "SELECT source, target, rel_type, attributes FROM relations "
                "WHERE source = ? OR target = ? LIMIT 5",
                (_eid, _eid)
            ).fetchall()
            if _rel_rows:
                _rels = []
                for _rr in _rel_rows:
                    _src, _tgt, _rtype = _rr["source"], _rr["target"], _rr["rel_type"]
                    _attrs = {}
                    try:
                        _attrs = json.loads(_rr["attributes"] or "{}")
                    except Exception:
                        _log.exception("__init__ error")
                    _rels.append({
                        "source": _src, "target": _tgt, "type": _rtype,
                        "strength": _attrs.get("_strength", 0.5),
                    })
                _r_ent["relations"] = _rels
    except Exception:
        _log.exception("__init__ error")

    return results


def delete_entity(eid: str, user_id: str = "") -> bool:
    if _pg_active():
        _ok = _pg_delete_entity(eid, user_id)
        if _ok:
            _fusion.remove_from_index(eid)
            for _l in [_fusion.working, _fusion.episodic, _fusion.semantic, _fusion.procedural]:
                if eid in _l._entries:
                    del _l._entries[eid]
        return _ok
    db = _get_db()
    try:
        if user_id:
            db.execute("DELETE FROM entities WHERE id = ? AND user_id = ?", (eid, user_id))
        else:
            db.execute("DELETE FROM entities WHERE id = ?", (eid,))
        db.commit()
        return True
    except Exception:
        return False


# ── Relation operations ───────────────────


def store_relation(rel_id: str, source: str, target: str, rel_type: str,
                   attributes: Optional[Dict] = None) -> bool:
    if _pg_active():
        return _pg_store_relation(rel_id, source, target, rel_type, attributes)
    db = _get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
    try:
        db.execute(
            """INSERT OR REPLACE INTO relations
               (id, source, target, rel_type, attributes, created_at)
               VALUES (?, ?, ?, ?, ?, COALESCE(
                   (SELECT created_at FROM relations WHERE id = ?), ?
               ))""",
            (rel_id, source, target, rel_type, attrs_json, rel_id, now),
        )
        db.commit()
        return True
    except Exception:
        return False


def get_relations(entity_id: Optional[str] = None,
                  rel_type: Optional[str] = None,
                  direction: str = "both") -> List[dict]:
    if _pg_active():
        return _pg_get_relations(entity_id, rel_type, direction)
    db = _get_db()
    clauses: List[str] = []
    params: List[str] = []
    if entity_id:
        if direction == "out":
            clauses.append("source = ?"); params.append(entity_id)
        elif direction == "in":
            clauses.append("target = ?"); params.append(entity_id)
        else:
            clauses.append("(source = ? OR target = ?)"); params.extend([entity_id, entity_id])
    if rel_type:
        clauses.append("rel_type = ?"); params.append(rel_type)
    where = " AND ".join(clauses) if clauses else "1=1"
    rows = db.execute(f"SELECT * FROM relations WHERE {where} ORDER BY created_at DESC", params).fetchall()
    return [{"id": r["id"], "source": r["source"], "target": r["target"],
             "type": r["rel_type"], "attributes": json.loads(r["attributes"] or "{}"),
             "created_at": r["created_at"]} for r in rows]


# ── Event operations ──────────────────────


def store_event(eid: str, event_type: str, participants: List[str],
                content: str, timestamp: Optional[str] = None,
                user_id: str = "") -> bool:
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _pg_active():
        return _pg_store_event(eid, event_type, participants, content, ts, user_id)
    db = _get_db()
    try:
        db.execute(
            "INSERT OR REPLACE INTO events (id, type, participants, content, timestamp, user_id) VALUES (?,?,?,?,?,?)",
            (eid, event_type, json.dumps(participants, ensure_ascii=False),
             content, ts, user_id),
        )
        db.commit()
        return True
    except Exception:
        return False


def search_events(query: str = "", event_type: Optional[str] = None,
                  participant: Optional[str] = None,
                  since: Optional[str] = None,
                  limit: int = 50) -> List[dict]:
    if _pg_active():
        return _pg_search_events(event_type=event_type, participant=participant,
                                 since=since, query=query, limit=limit)
    db = _get_db()
    clauses: List[str] = []
    params: List[str] = []
    if event_type:
        clauses.append("type = ?"); params.append(event_type)
    if participant:
        clauses.append("participants LIKE ?"); params.append(f"%{participant}%")
    if since:
        clauses.append("timestamp >= ?"); params.append(since)
    if query:
        clauses.append("(content LIKE ? OR id LIKE ?)"); params.extend([f"%{query}%", f"%{query}%"])
    where = " AND ".join(clauses) if clauses else "1=1"
    rows = db.execute(
        f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [{"id": r["id"], "type": r["type"],
             "participants": json.loads(r["participants"] or "[]"),
             "content": r["content"], "timestamp": r["timestamp"],
             "user_id": r["user_id"]} for r in rows]


def get_timeline(entity_id: str, limit: int = 30) -> List[dict]:
    events = search_events(participant=entity_id, limit=limit)
    relations = get_relations(entity_id)
    timeline: List[Tuple[str, str, dict]] = []
    for e in events:
        timeline.append(("event", e.get("timestamp", ""), e))
    for r in relations:
        timeline.append(("relation", r.get("created_at", ""), r))
    timeline.sort(key=lambda x: x[1], reverse=True)
    return [{"type": t, "data": d} for t, _, d in timeline[:limit]]


# ── Read APIs (legacy compatibility) ──────


def memory_read(key: str) -> Optional[dict]:
    # PG first
    from ..storage.db import fetch_dict, use_pg
    if use_pg():
        try:
            rows = fetch_dict("SELECT * FROM memory_entities WHERE id = %s::uuid OR name = %s", (key, key))
            if rows and len(rows) > 0:
                r = rows[0]
                return {"key": r.get("id", key), "type": r.get("entity_type", "memory"),
                        "value": r.get("content", ""),
                        "tags": r.get("metadata", {}).get("tags", []),
                        "attributes": r.get("metadata", {})}
        except Exception:
            _log.exception("__init__ error")
    entry = _read_from_layers(key)
    if entry:
        return {"key": entry.key, "type": entry.layer, "value": entry.value,
                "tags": entry.tags, "attributes": {"name": str(entry.value)}}
    db = _get_db()
    row = db.execute("SELECT * FROM entities WHERE id = ?", (key,)).fetchone()
    if not row:
        return None
    return {"key": row["id"], "type": row["type"], "value": row["value"],
            "tags": json.loads(row["tags"] or "[]"),
            "attributes": json.loads(row["attributes"] or "{}")}


def memory_store(key: str, value: str, tags: Optional[List[str]] = None,
                 etype: str = "memory", user_id: str = "") -> bool:
    # PG-first: store in PG with embedding, then sync fusion layers.
    if _pg_active():
        _ok = _pg_upsert_entity(key, etype, value, tags or [], {"value": value}, user_id)
        if _ok:
            try:
                _pentry = MemoryEntry(
                    key=key, value=value, tags=tags or [],
                    importance=0.5, timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source="chat", entities=[key], user_id=user_id,
                    base_importance=0.5, access_count=1)
                _store_to_fusion(_pentry)
            except Exception:
                _log.exception("memory_store fusion sync error")
        return _ok
    return store_entity(key, etype, {"value": value}, tags=tags, user_id=user_id)


def memory_search(query: str, limit: int = 10, user_id: str = "") -> List[dict]:
    """Search memory entries.

    PG vector search (semantic) is used only when explicitly enabled via
    AELVOXIM_VECTOR_SEARCH=1 (embedding API latency/cost is off by default).
    Any failure falls back to the local keyword/fusion search.
    """
    import os as _os
    if _os.environ.get("AELVOXIM_VECTOR_SEARCH") == "1":
        try:
            from ..storage.embedding import get_embedding
            from ..storage.db import fetch_dict, use_pg
            if use_pg():
                _emb = get_embedding(query)
                # user_id scoping: without it, vector search leaked every
                # user's entities (B4, 9.txt audit).
                _rows = fetch_dict(
                    "SELECT name, entity_type, content, metadata FROM memory_entities "
                    "WHERE embedding <=> %s::vector < 0.8 "
                    "AND (user_id = %s OR user_id IS NULL) "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (str(_emb), user_id, str(_emb), limit),
                )
                if _rows:
                    return [{
                        "key": r["name"],
                        "id": r["name"],
                        "type": r.get("entity_type", "memory"),
                        "value": r.get("content", ""),
                        "tags": r.get("metadata", {}).get("tags", []) if r.get("metadata") else [],
                        "attributes": r.get("metadata", {}),
                        "user_id": user_id,
                    } for r in _rows]
        except Exception:
            _log.exception("PG vector search failed; falling back to local search")
    return search_entities(query, limit=limit, user_id=user_id)


def memory_timeline(entity_id: str, limit: int = 30) -> List[dict]:
    return get_timeline(entity_id, limit)


def get_layer_stats() -> Dict:
    """Get 3-layer memory statistics."""
    return _fusion.stats()


# ── W12: Audit log for memory operations ──


def _audit_memory(event: str, eid: str, user_id: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """Write memory operation to audit log."""
    try:
        import json as _js
        from datetime import datetime as _dt
        from pathlib import Path as _Pt
        log_path = _Pt(METACORE_DIR) / "ethics" / "audit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = _js.dumps({
            "ts": _dt.now().isoformat(),
            "event": event,
            "entity_id": eid,
            "user_id": user_id,
            **(extra or {}),
        }, ensure_ascii=False)
        with open(str(log_path), "a") as f:
            f.write(entry + "\n")
    except Exception:
        _log.exception("__init__ error")


__all__ = [
    "store_entity", "search_entities", "delete_entity",
    "lock_entity", "unlock_entity", "is_locked",
    "store_relation", "get_relations",
    "store_event", "search_events", "get_timeline",
    "cleanup_events", "cleanup_unlocked_entities",
    "memory_read", "memory_store", "memory_search", "memory_timeline",
    "get_layer_stats",
    "cleanup_all",
    "update_emotion_profile", "get_emotion_profile",
]


# ── Emotion profile (W8) ─────────────────────


def _load_emotion_profile(user_id: str) -> Dict[str, Any]:
    """Load the emotion profile dict from real storage.

    Previously read via search_entities(), whose fusion-cache rows return a
    fake attributes {"name": ...} and a value that is the entity *name*, not
    the counters — so the count dict was never recovered and every update
    reset it to zero (B5, 9.txt audit). Read the stored attributes directly.
    """
    eid = f"emotion:{user_id.replace(':', '_')}"
    default = {"positive": 0, "negative": 0, "neutral": 0, "total": 0, "last_check": ""}
    try:
        if _pg_active():
            from ..storage.db import fetch_dict
            rows = fetch_dict(
                "SELECT metadata FROM memory_entities WHERE name = %s AND entity_type = 'emotion_profile'",
                (eid,))
            if rows and rows[0].get("metadata"):
                attrs = rows[0]["metadata"]
                val = attrs.get("value", attrs.get("name"))
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        val = None
                if isinstance(val, dict):
                    return {**default, **val}
        else:
            db = _get_db()
            row = db.execute("SELECT attributes FROM entities WHERE id = ?", (eid,)).fetchone()
            if row and row[0]:
                attrs = json.loads(row[0])
                val = attrs.get("value", attrs.get("name"))
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        val = None
                if isinstance(val, dict):
                    return {**default, **val}
    except Exception:
        _log.exception("__init__ error")
    return default


def update_emotion_profile(
    user_id: str,
    sentiment: str,  # "positive", "negative", "neutral"
    strength: float = 0.5,
    message: str = "",
) -> None:
    """Update a user's emotion profile via Bayesian count."""
    eid = f"emotion:{user_id.replace(':', '_')}"
    current = _load_emotion_profile(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current[sentiment] = current.get(sentiment, 0) + 1
    current["total"] = current.get("total", 0) + 1
    current["last_check"] = now
    store_entity(
        eid=eid,
        etype="emotion_profile",
        attributes={"name": "emotion_profile", "value": current, "message": message[:100]},
        tags=["emotion", sentiment],
        user_id=user_id,
    )


def get_emotion_profile(user_id: str) -> Dict[str, Any]:
    """Get a user's emotion profile dict."""
    return _load_emotion_profile(user_id)


def is_negative_streak(user_id: str, consecutive: int = 3) -> bool:
    """Check if user has N+ consecutive negative sentiments."""
    profile = get_emotion_profile(user_id)
    if not isinstance(profile, dict):
        return False
    return profile.get("negative", 0) >= consecutive


# ── Decay install ──
try:
    from .decay import install_decay_cleanup as _install_decay
    _install_decay(_fusion, _DB_PATH)
except Exception:
    _log.exception("__init__ error")
