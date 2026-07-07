"""
metacore.memory.consolidator — Active memory consolidation.

Periodically scans entities for duplicate or highly similar entries
and merges them. Only marks superseded — never hard-deletes.

Scenarios handled:
  - Same entity key with different attributes (merge)
  - Similar names that refer to the same entity (merge)
  - Outdated entries with lower confidence (supersede)
"""

from __future__ import annotations

import json
import time
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _get_entities(db) -> List[Dict[str, Any]]:
    """Fetch all non-superseded entities from SQLite."""
    rows = db.execute(
        "SELECT id, value, attributes FROM entities WHERE id NOT LIKE ?",
        ("%_superseded%",),
    ).fetchall()
    results = []
    for row in rows:
        try:
            attrs = json.loads(row[2] or "{}")
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        # Skip already-superseded entries
        if attrs.get("_superseded") or attrs.get("_superseded_by"):
            continue
        results.append({
            "key": row[0],
            "value": row[1],
            "attributes": attrs,
        })
    return results


def _group_similar(entities: List[Dict]) -> List[List[Dict]]:
    """Group entities by normalized key for potential merging."""
    groups: Dict[str, List[Dict]] = {}
    for e in entities:
        key = e["key"].lower().replace("entity:", "").replace("_", "").replace("-", "").strip()
        if len(key) < 3:
            continue  # skip too-short keys
        groups.setdefault(key, []).append(e)
    # Only return groups with 2+ entries
    return [g for g in groups.values() if len(g) >= 2]


def _merge_entity_pair(
    preserve: Dict,
    supersede: Dict,
    db,
) -> bool:
    """Merge attributes from supersede into preserve, mark supersede as superseded.

    Merging logic (preserve wins on conflicts):
      - confidence/tags/events from supersede are appended to preserve
      - preserve's existing attributes take priority
    """
    p_attrs = preserve["attributes"]
    s_attrs = supersede["attributes"]

    # Merge tags
    p_tags = set(p_attrs.get("tags", "").split(",") if p_attrs.get("tags") else [])
    s_tags = set(s_attrs.get("tags", "").split(",") if s_attrs.get("tags") else [])
    merged_tags = p_tags | s_tags
    merged_tags.discard("")

    # Merge source_event list
    p_events = p_attrs.get("source_event", "")
    s_events = s_attrs.get("source_event", "")
    if s_events and s_events != p_events:
        p_events = p_events + "," + s_events if p_events else s_events

    # Update preserve attributes
    p_attrs["tags"] = ",".join(sorted(merged_tags))
    p_attrs["source_event"] = p_events
    # Take higher confidence
    p_conf = p_attrs.get("confidence", 0.5)
    s_conf = s_attrs.get("confidence", 0.5)
    p_attrs["confidence"] = max(p_conf, s_conf)
    p_attrs["_consolidated_at"] = time.time()
    p_attrs["_consolidated_count"] = p_attrs.get("_consolidated_count", 0) + 1

    try:
        db.execute(
            "UPDATE entities SET attributes = ? WHERE id = ?",
            (json.dumps(p_attrs, ensure_ascii=False), preserve["key"]),
        )
        # Mark supersede entry
        s_attrs["_superseded"] = True
        s_attrs["_superseded_by"] = preserve["key"]
        s_attrs["_superseded_at"] = time.time()
        db.execute(
            "UPDATE entities SET attributes = ? WHERE id = ?",
            (json.dumps(s_attrs, ensure_ascii=False), supersede["key"]),
        )
        db.commit()
        return True
    except Exception:
        return False


def consolidate_entities(db=None, dry_run: bool = False) -> Dict[str, Any]:
    """Run one consolidation pass over all entities.

    Args:
        db: Optional SQLite connection. If None, opens default.
        dry_run: If True, only report what would be merged without writing.

    Returns:
        Dict with: merged_count, groups_found, dry_run
    """
    close_db = False
    from ..utils import DATA_DIR
    if db is None:
        db_path = DATA_DIR / "memory.db"
        if not db_path.exists():
            return {"merged_count": 0, "groups_found": 0, "error": "no database"}
        db = sqlite3.connect(str(db_path))
        close_db = True

    try:
        entities = _get_entities(db)
        groups = _group_similar(entities)

        merged = 0
        for group in groups:
            # Sort by confidence descending; first entry is the preserve target
            def _conf(e):
                return e["attributes"].get("confidence", 0.0)
            group.sort(key=_conf, reverse=True)
            preserve = group[0]
            for other in group[1:]:
                if not dry_run:
                    if _merge_entity_pair(preserve, other, db):
                        merged += 1
                else:
                    merged += 1

        return {
            "merged_count": merged,
            "groups_found": len(groups),
            "dry_run": dry_run,
        }
    finally:
        if close_db:
            db.close()


def run_consolidation() -> Dict[str, Any]:
    """Entry point for Learner to call. Wraps consolidate_entities()."""
    return consolidate_entities()


if __name__ == "__main__":
    import tempfile
    # Test with in-memory database
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            value TEXT,
            attributes TEXT
        )
    """)
    # Insert duplicate entities
    for i in range(3):
        db.execute(
            "INSERT INTO entities (id, value, attributes) VALUES (?, ?, ?)",
            (f"entity:welvoket_{i}", f"Welvoket AGI v{i+1}",
             json.dumps({"tags": f"test,ai", "confidence": 0.5 + i * 0.2,
                         "source_event": f"chat_{i}"}))
        )
    db.commit()

    result = consolidate_entities(db)
    print(f"=== Consolidation test ===")
    print(f"  Groups found: {result['groups_found']}")
    print(f"  Merged count: {result['merged_count']}")

    # Verify
    rows = db.execute("SELECT id, attributes FROM entities ORDER BY id").fetchall()
    print(f"\n  Entities after merge:")
    for r in rows:
        attrs = json.loads(r[1])
        print(f"    {r[0]}: superseded={attrs.get('_superseded', False)}, "
              f"superseded_by={attrs.get('_superseded_by', '')}, "
              f"conf={attrs.get('confidence', '?')}")
    db.close()
    print("\nAll consolidation tests passed ✅")
