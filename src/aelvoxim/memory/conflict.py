"""
metacore.memory.conflict — Memory conflict detection and resolution layer.

Detects when new information contradicts existing semantic memory.
Stores conflict state in the entity's attributes and generates
prompts for the LLM to ask the user for clarification.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .entry import MemoryEntry, LAYER_SEMANTIC, LAYER_EPISODIC
from .fusion import MemoryFusion


import logging
_log = logging.getLogger("aelvoxim.memory.conflict")

# ── Similarity heuristic ──


def _text_similarity(a: str, b: str) -> float:
    """Simple character-level similarity (0.0-1.0)."""
    if not a or not b:
        return 0.0
    a = a.strip().lower()
    b = b.strip().lower()
    if a == b:
        return 1.0
    # Longest common substring ratio
    # Simple n-gram overlap for speed
    a_chars = set(a[:50])
    b_chars = set(b[:50])
    if not a_chars or not b_chars:
        return 0.0
    intersection = len(a_chars & b_chars)
    union = len(a_chars | b_chars)
    return intersection / max(union, 1)


# ── Conflict detection ──


def detect_conflict(
    entry_key: str,
    new_value: str,
    new_tags: List[str],
    fusion: MemoryFusion,
    db_connection=None,
) -> Optional[Dict[str, Any]]:
    """Check if new value contradicts existing semantic memory.

    Returns a dict with conflict metadata or None if no conflict:
        {"_conflict": True, "_conflict_old": "...", "_conflict_new": "...", ...}
    These keys can be merged directly into the entity's attributes dict.
    """
    # Only check semantic-level keys (person, location, preference)
    is_important = any(t in new_tags for t in ["person", "location", "preference", "extracted"])
    if not is_important:
        return None

    # Check semantic layer first, then episodic
    existing = fusion.semantic._entries.get(entry_key)
    if not existing:
        existing = fusion.episodic._entries.get(entry_key)
    if not existing:
        return None

    current_val = str(existing.value) if existing.value else ""
    if not current_val:
        return None

    # Don't flag if the new value is essentially the same
    sim = _text_similarity(current_val, new_value)
    if sim >= 0.8:
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "_conflict": True,
        "_conflict_old": current_val[:100],
        "_conflict_new": new_value[:100],
        "_conflict_detected_at": now,
        "_conflict_resolved": False,
    }


# ── Conflict resolution ──


def resolve_conflict(
    entry_key: str,
    user_choice: str,
    fusion: MemoryFusion,
    db_connection=None,
) -> bool:
    """Resolve a conflict based on user response.

    Args:
        entry_key: Entity key.
        user_choice: "old" (keep old), "new" (accept new), "neither" (reset).
        fusion: MemoryFusion instance.
        db_connection: Optional SQLite connection to persist changes.

    Returns:
        True if resolved successfully.
    """
    entry = fusion.semantic._entries.get(entry_key)
    if not entry:
        entry = fusion.episodic._entries.get(entry_key)
    if not entry:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if user_choice == "new":
        # The new value was already stored, just clear conflict flag
        entry.conflict_status = "active"
        entry.version += 1
        entry.strength = entry.base_importance
    elif user_choice == "old":
        # Delete the new value (which would be in episodic), restore old
        entry.conflict_status = "active"
        entry.strength = min(1.0, entry.strength + 0.1)
    elif user_choice == "neither":
        entry.conflict_status = "pending"
    else:
        return False

    # Clear conflict attributes
    entry.touch()

    if db_connection:
        try:
            import json as _js
            _attrs = {"_conflict": False, "_conflict_resolved": True,
                      "_resolved_at": now, "_resolution": user_choice,
                      "_strength": round(entry.strength, 3)}
            db_connection.execute(
                "UPDATE entities SET attributes = ? WHERE id = ?",
                (_js.dumps(_attrs, ensure_ascii=False), entry_key)
            )
            db_connection.commit()
        except Exception:
            _log.exception("conflict error")

    return True


# ── Conflict query (for routes.py injection) ──


def get_pending_conflicts(db_connection) -> List[Dict[str, Any]]:
    """Get all unresolved conflicts from SQLite.

    Returns list of dicts: [{key, old_value, new_value, detected_at}]
    """
    try:
        rows = db_connection.execute(
            "SELECT id, value, attributes FROM entities WHERE attributes LIKE ?",
            ('%"_conflict": true%',)
        ).fetchall()
        results = []
        for row in rows:
            try:
                attrs = json.loads(row[2] or "{}")
            except Exception:
                continue
            if attrs.get("_conflict") and not attrs.get("_conflict_resolved"):
                results.append({
                    "key": row[0],
                    "current_value": row[1] or "",
                    "old_value": attrs.get("_conflict_old", ""),
                    "new_value": attrs.get("_conflict_new", ""),
                    "detected_at": attrs.get("_conflict_detected_at", ""),
                })
        return results
    except Exception:
        return []


__all__ = [
    "detect_conflict", "resolve_conflict", "get_pending_conflicts",
]
