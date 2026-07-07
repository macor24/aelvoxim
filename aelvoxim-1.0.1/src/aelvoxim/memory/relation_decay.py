"""
metacore.memory.relation_decay — Relation strength decay engine.

Extends batch_decay with relation-level strength tracking.
Each relation gets a _strength field in its attributes JSON that decays
with time since last mention. Weak relations (< 0.2) are candidates for
re-verification; very weak relations (< 0.05) are archived.

Hooks into batch_decay in decay.py — no separate thread needed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from typing import Any, Dict

# ── Thresholds (aligned with decay.py constants) ──

RELATION_DORMANT_THRESHOLD = 0.2
RELATION_ARCHIVE_THRESHOLD = 0.05
RELATION_DECAY_RATE = 0.03  # strength lost per day of no mention
RELATION_BOOST_PER_MENTION = 0.2  # strength gained per mention

_DUMP = lambda d: json.dumps(d, ensure_ascii=False)


def _days_since(ts: float) -> float:
    """Days between now and a unix timestamp. Clamped to 0."""
    return max(0.0, (datetime.now().timestamp() - ts) / 86400.0)


def _parse_created_at(raw: str) -> float:
    """Parse 'YYYY-MM-DD HH:MM:SS' string to unix timestamp. Returns 1 day ago on failure."""
    try:
        dt = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except (ValueError, TypeError):
        return datetime.now().timestamp() - 86400.0


def _decayed_strength(strength: float, days_since: float) -> float:
    """Apply exponential decay: strength · e^(-rate · days)"""
    if days_since <= 0:
        return strength
    return max(0.0, strength * math.exp(-RELATION_DECAY_RATE * days_since))


def _classify_strength(strength: float, attrs: dict, stats: dict) -> None:
    """Tag attributes with dormant/archived status and bump stat counter."""
    if strength <= RELATION_ARCHIVE_THRESHOLD:
        attrs["_archived"] = True
        stats["archived"] += 1
    elif strength <= RELATION_DORMANT_THRESHOLD:
        attrs["_dormant"] = True
        stats["dormant"] += 1
    else:
        stats["decayed"] += 1


def apply_relation_decay(db_path: str = "") -> Dict[str, Any]:
    """Walk all relations in SQLite, apply time-based strength decay.

    Returns stats dict for logging:
        {decayed, dormant, archived, errors}
    """
    if not db_path:
        return {"decayed": 0, "dormant": 0, "archived": 0, "errors": 0}

    stats: Dict[str, Any] = {"decayed": 0, "dormant": 0, "archived": 0, "errors": 0}
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        db = sqlite3.connect(db_path)
        rows = db.execute("SELECT id, attributes, created_at FROM relations").fetchall()
    except Exception:
        return {"**error": f"cannot open {db_path}", **stats}

    for rid, attrs_json, created_at in rows:
        try:
            attrs: dict = json.loads(attrs_json or "{}")
        except (json.JSONDecodeError, TypeError):
            stats["errors"] += 1
            continue

        strength = attrs.get("_strength", 0.5)
        last_ts = attrs.get("_last_mentioned", 0.0)
        days = _days_since(last_ts) if last_ts else _days_since(_parse_created_at(created_at))
        strength = _decayed_strength(strength, days)

        attrs["_strength"] = round(strength, 4)
        attrs["_last_checked"] = now_str
        _classify_strength(strength, attrs, stats)

        db.execute(
            "UPDATE relations SET attributes = ? WHERE id = ?",
            (_DUMP(attrs), rid),
        )

    try:
        db.commit()
        db.close()
    except Exception:
        stats["errors"] += 1

    return stats


def record_relation_mention(rel_id: str, db_path: str = "") -> bool:
    """Boost a relation's strength when it's mentioned/used.

    Called from store_relation or any code path that touches a relation.
    Returns True on success.
    """
    if not db_path or not rel_id:
        return False

    try:
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT attributes FROM relations WHERE id = ?", (rel_id,)
        ).fetchone()
        if not row:
            db.close()
            return False

        attrs: dict = json.loads(row[0] or "{}")
        strength = min(1.0, attrs.get("_strength", 0.5) + RELATION_BOOST_PER_MENTION)
        mention_count = attrs.get("_mention_count", 0) + 1

        attrs["_strength"] = round(strength, 4)
        attrs["_mention_count"] = mention_count
        attrs["_last_mentioned"] = datetime.now().timestamp()

        db.execute(
            "UPDATE relations SET attributes = ? WHERE id = ?",
            (_DUMP(attrs), rel_id),
        )
        db.commit()
        db.close()
        return True
    except Exception:
        return False


__all__ = [
    "apply_relation_decay",
    "record_relation_mention",
    "RELATION_DORMANT_THRESHOLD",
    "RELATION_ARCHIVE_THRESHOLD",
    "RELATION_DECAY_RATE",
]
