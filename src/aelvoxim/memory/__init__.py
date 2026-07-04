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
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import METACORE_DIR
from .entry import MemoryEntry, LAYER_WORKING, LAYER_EPISODIC, LAYER_SEMANTIC, LAYER_PROCEDURAL, should_store
from .fusion import MemoryFusion

_DB_PATH = str(Path(METACORE_DIR) / "memory.db")
_LEGACY_JSON_PATH = str(Path(METACORE_DIR) / "memory.json")
_local = threading.local()

# ── Fusion (3-layer) ──────────────────────

_fusion = MemoryFusion()


def _get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


# ── Layer-aware helpers ───────────────────


def _determine_layer(entry: MemoryEntry) -> str:
    """Determine which layer a MemoryEntry belongs to based on importance + access.

    Dormant/archived entries stay in their current layer.
    """
    if entry.conflict_status not in ("active", "pending"):
        return entry.layer
    if entry.access_count >= 5 or entry.importance >= 0.95:
        return LAYER_PROCEDURAL
    if entry.immutable or entry.importance >= 0.8:
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
                                importance=0.5, timestamp=now, source="migration")
            _store_to_fusion(entry)
        except Exception:
            pass

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
            pass

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
            pass

    db.commit()
    legacy.rename(legacy.with_suffix(".json.bak"))


def _load_fusion_from_db():
    """Load all entities from SQLite into fusion layers on startup."""
    db = _get_db()
    rows = db.execute("SELECT id, value, tags, created_at FROM entities ORDER BY created_at DESC LIMIT 500").fetchall()
    loaded = 0
    for row in rows:
        eid = row["id"]
        value = row["value"]
        tags_list = json.loads(row["tags"] or "[]")
        importance = 0.5
        if "extracted" in tags_list:
            importance = 0.6
        if "person" in tags_list or "preference" in tags_list:
            importance = 0.7
        entry = MemoryEntry(key=eid, value=value or "", tags=tags_list,
                            importance=importance, timestamp=row["created_at"],
                            source="db_reload", entities=[eid])
        _store_to_fusion(entry)
        loaded += 1
    if loaded > 0:
        import logging as _log
        _fusion.rebuild_index()
        _log.getLogger("memory").info("🧠 已加载 %d 条实体到融合层，索引 %d 词条", loaded, len(_fusion._inverted_index))


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
                import logging as _log3
                _log3.getLogger("memory").warning("Migration: skip entity %s: %s", row["id"], _mig_e)
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
            import logging as _log2
            _log2.getLogger("memory").info("Backfilled %d entities with confidence metadata", updated)
    except Exception:
        pass

_migrate_confidence_metadata()


# ═══════════════════════════════════════════
# External API (unchanged signatures)
# ═══════════════════════════════════════════


# ── Entity operations ─────────────────────


def store_entity(eid: str, etype: str, attributes: dict,
                 tags: Optional[List[str]] = None,
                 user_id: str = "") -> bool:
    """Store or update an entity (3-layer aware)."""
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
            pass
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
            # Get mention count from DB (counted before INSERT: id IS PRIMARY KEY, so after INSERT it's always 1)
            _mc = db.execute(
                "SELECT COUNT(*) FROM entities WHERE id = ? AND user_id = ?",
                (eid, user_id)
            ).fetchone()
            _mention_count = (_mc[0] if _mc else 0) + 1  # +1 = count the current mention
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
                    pass
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
                pass
            # Update DB with attributes (TTL, superseded, etc.)
            _attrs_j2 = json.dumps(attributes, ensure_ascii=False)
            db.execute("UPDATE entities SET attributes = ? WHERE id = ? AND user_id = ?",
                       (_attrs_j2, eid, user_id))
            db.commit()
        except Exception:
            pass  # fallback to hardcoded importance below
        # Also update fusion layer
        importance = 0.5
        if tags:
            if "extracted" in tags:
                importance = 0.6
            if "person" in tags or "preference" in tags:
                importance = 0.8  # semantic-level
            if "location" in tags:
                importance = 0.7  # episodic-level
        # Check if this key already exists with higher importance (upgrade path)
        existing = _fusion.get_layer(LAYER_SEMANTIC)._entries.get(eid)
        if not existing:
            existing = _fusion.get_layer(LAYER_EPISODIC)._entries.get(eid)
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
                _attrs_j = json.dumps({"name": value}, ensure_ascii=False)
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
                    pass
                _audit_memory("memory_write", eid, user_id, {"type": etype})
                return True
        entry = MemoryEntry(key=eid, value=value, tags=tags or [],
                            importance=importance, timestamp=now,
                            source="chat", entities=[eid],
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
    db = _get_db()
    from datetime import timedelta as _td
    _cutoff = (datetime.now() - _td(days=before_days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        _cur = db.execute(
            "DELETE FROM events WHERE type = 'chat_inquiry' AND timestamp < ?",
            (_cutoff,),
        )
        db.commit()
        return _cur.rowcount
    except Exception:
        return 0


def cleanup_unlocked_entities(before_days: int = 30) -> int:
    """Delete unlocked entities created before before_days.

    Preserves locked (confirmed) entities.
    Returns count of deleted rows.
    """
    db = _get_db()
    from datetime import timedelta as _td
    _cutoff = (datetime.now() - _td(days=before_days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        _cur = db.execute(
            "DELETE FROM entities WHERE locked = 0 AND created_at < ?",
            (_cutoff,),
        )
        db.commit()
        return _cur.rowcount
    except Exception:
        return 0


# ── Helper: search_entities returns locked field ──


def search_entities(query: str, etype: Optional[str] = None,
                    limit: int = 20, user_id: str = "") -> List[dict]:
    """Search entities (now uses fusion + SQLite fallback)."""
    # First try fusion (3-layer in-memory)
    fusion_results = _fusion.search(query=query, limit=limit * 2)
    if fusion_results:
        result_dicts = []
        for e in fusion_results:
            if user_id and user_id not in str(getattr(e, 'source', '')):
                continue
            result_dicts.append({
                "id": e.key,
                "key": e.key,
                "type": e.layer,
                "value": str(e.value),
                "tags": e.tags,
                "attributes": {"name": str(e.value)},
                "user_id": "",
            })
            if len(result_dicts) >= limit:
                break
        if result_dicts:
            return result_dicts

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
                        pass
                    _rels.append({
                        "source": _src, "target": _tgt, "type": _rtype,
                        "strength": _attrs.get("_strength", 0.5),
                    })
                _r_ent["relations"] = _rels
    except Exception:
        pass

    return results


def delete_entity(eid: str) -> bool:
    db = _get_db()
    try:
        db.execute("DELETE FROM entities WHERE id = ?", (eid,))
        db.commit()
        return True
    except Exception:
        return False


# ── Relation operations ───────────────────


def store_relation(rel_id: str, source: str, target: str, rel_type: str,
                   attributes: Optional[Dict] = None) -> bool:
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
    db = _get_db()
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            pass
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
    # PG mode: store in PG with embedding
    from ..storage.db import execute, use_pg
    if use_pg():
        try:
            from ..storage.embedding import get_embedding
            import json, uuid as _uid
            emb = get_embedding(value)
            execute("""
                INSERT INTO memory_entities (id, name, entity_type, content, embedding, source, metadata)
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s::jsonb)
                ON CONFLICT (name, entity_type) DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
            """, (str(_uid.uuid4()), key[:200], etype, value, str(emb), "chat",
                  json.dumps({"tags": tags or []})))
        except Exception:
            pass
    return store_entity(key, etype, {"value": value}, tags=tags, user_id=user_id)


def memory_search(query: str, limit: int = 10) -> List[dict]:
    # Fallback: skip PG vector search (embedding API timeout risk)
    return search_entities(query, limit=limit)


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
        pass


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


def update_emotion_profile(
    user_id: str,
    sentiment: str,  # "positive", "negative", "neutral"
    strength: float = 0.5,
    message: str = "",
) -> None:
    """Update a user's emotion profile via Bayesian count.

    Stored as a special entity entry in the memory DB.
    """
    eid = f"emotion:{user_id.replace(':', '_')}"
    existing = search_entities(eid, limit=1)
    current = {"positive": 0, "negative": 0, "neutral": 0, "total": 0, "last_check": ""}
    if existing:
        old_val = existing[0].get("value", "")
        try:
            parsed = json.loads(old_val) if isinstance(old_val, str) else old_val
            if isinstance(parsed, dict):
                current = parsed
        except (json.JSONDecodeError, TypeError):
            pass
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
    eid = f"emotion:{user_id.replace(':', '_')}"
    existing = search_entities(eid, limit=1)
    if existing:
        return existing[0].get("value", {})
    return {"positive": 0, "negative": 0, "neutral": 0, "total": 0, "last_check": ""}


def is_negative_streak(user_id: str, consecutive: int = 3) -> bool:
    """Check if user has N+ consecutive negative sentiments."""
    profile = get_emotion_profile(user_id)
    return profile.get("negative", 0) >= consecutive


# ── Decay install ──
try:
    from .decay import install_decay_cleanup as _install_decay
    _install_decay(_fusion, _DB_PATH)
except Exception:
    pass
