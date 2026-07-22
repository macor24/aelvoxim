"""
metacore.memory.decay — Confidence decay engine.

Applies time-based strength decay to all memory entries.
Semantic/procedural entries no longer stay at full strength forever.
Instead their `strength` decays daily by `decay_rate` unless re-accessed.

Constants:
  DORMANT_THRESHOLD = 0.2   — strength below this = hidden from active queries
  ARCHIVE_THRESHOLD  = 0.05 — strength below this = archived (audit only)
  WAKE_BOOST         = 0.5  — strength restored to on wake_up()
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .entry import (
    MemoryEntry, LAYER_WORKING, LAYER_EPISODIC,
    LAYER_SEMANTIC, LAYER_PROCEDURAL, DEFAULT_TTL,
)
from .fusion import MemoryFusion

import logging
_log = logging.getLogger("aelvoxim.decay")


# ── Thresholds ──

DORMANT_THRESHOLD = 0.2
ARCHIVE_THRESHOLD = 0.05
WAKE_BOOST = 0.5

# Default decay rates by layer (strength lost per day)
LAYER_DECAY_RATES = {
    LAYER_WORKING: 0.10,
    LAYER_EPISODIC: 0.05,
    LAYER_SEMANTIC: 0.01,
    LAYER_PROCEDURAL: 0.005,
}


# ── Per-entry decay ──


def apply_decay(entry: MemoryEntry, days_since_last_access: float = 0) -> bool:
    """Apply time-based decay to a single entry.

    Returns True if entry is still active (strength > DORMANT_THRESHOLD).
    False means dormant or archived.
    """
    if days_since_last_access <= 0:
        if entry.last_access:
            try:
                last = datetime.strptime(entry.last_access[:19], "%Y-%m-%d %H:%M:%S")
                days_since_last_access = (datetime.now() - last).total_seconds() / 86400
            except Exception:
                days_since_last_access = 1

    if days_since_last_access <= 0:
        return True

    # Frequency bonus: frequently accessed entries decay slower
    freq_bonus = min(entry.access_count / 20, 0.5)
    effective_rate = entry.decay_rate * (1.0 - freq_bonus)
    decay_amount = effective_rate * days_since_last_access

    entry.strength = max(0.0, entry.strength - decay_amount)

    if entry.strength <= ARCHIVE_THRESHOLD:
        entry.conflict_status = "archived"
        return False
    if entry.strength <= DORMANT_THRESHOLD:
        entry.conflict_status = "dormant"
        return False
    return True


def wake_up(entry: MemoryEntry) -> MemoryEntry:
    """Restore strength when user re-mentions a dormant entry."""
    entry.strength = max(entry.strength, entry.base_importance * WAKE_BOOST)
    if entry.conflict_status in ("dormant", "archived"):
        entry.conflict_status = "active"
    entry.touch()
    return entry


# ── Batch operations ──


def batch_decay(fusion: MemoryFusion, db_path: str = "") -> Dict[str, Any]:
    """Scan all layers, apply decay, return statistics."""
    now = datetime.now()
    stats = {
        "scanned": 0, "decayed": 0, "dormant": 0,
        "archived": 0, "woken": 0,
    }
    layers = [
        fusion.working, fusion.episodic,
        fusion.semantic, fusion.procedural,
    ]
    to_archive: List[str] = []
    to_dormant: List[str] = []
    for layer in layers:
        for entry in list(layer._entries.values()):
            stats["scanned"] += 1
            if entry.immutable:
                continue
            if entry.ttl_seconds is not None and entry.is_expired():
                layer._entries.pop(entry.key, None)
                stats["archived"] += 1
                to_archive.append(entry.key)
                continue
            active = apply_decay(entry)
            if active:
                stats["decayed"] += 1
            elif entry.conflict_status == "dormant":
                stats["dormant"] += 1
                to_dormant.append(entry.key)
            elif entry.conflict_status == "archived":
                layer._entries.pop(entry.key, None)
                stats["archived"] += 1
                to_archive.append(entry.key)

    # Batch-sync status to SQLite
    if db_path and (to_dormant or to_archive):
        try:
            _db = sqlite3.connect(db_path)
            for _key in to_dormant:
                _db.execute(
                    "UPDATE entities SET attributes = json_set(COALESCE(attributes,'{}'), '$_status', ?, '$_strength', ?) WHERE id = ?",
                    ("dormant", 0.19, _key)
                )
            for _key in to_archive:
                _db.execute(
                    "UPDATE entities SET attributes = json_set(COALESCE(attributes,'{}'), '$_status', ?, '$_strength', ?) WHERE id = ?",
                    ("archived", 0.04, _key)
                )
            _db.commit()
            _db.close()
        except Exception:
            _log.exception("decay error")

    # ── Relation decay (extension) ──
    if db_path:
        try:
            from .relation_decay import apply_relation_decay
            _rel_stats = apply_relation_decay(db_path)
            stats["rel_decayed"] = _rel_stats.get("decayed", 0)
            stats["rel_dormant"] = _rel_stats.get("dormant", 0)
            stats["rel_archived"] = _rel_stats.get("archived", 0)
        except Exception:
            _log.exception("decay error")

    return stats


# ── Fusion-level integration ──


def install_decay_cleanup(fusion: MemoryFusion, db_path: str = "", interval_hours: float = 24.0):
    """Run batch_decay on a timer. Runs once immediately, then on schedule."""
    from threading import Thread
    import time as _time

    # Run immediately on startup
    try:
        batch_decay(fusion, db_path)
    except Exception:
        _log.exception("decay error")

    def _loop():
        while True:
            _time.sleep(interval_hours * 3600)
            try:
                batch_decay(fusion, db_path)
            except Exception:
                _log.exception("decay error")

    t = Thread(target=_loop, daemon=True, name="memory-decay")
    t.start()
    return t


__all__ = [
    "DORMANT_THRESHOLD", "ARCHIVE_THRESHOLD", "WAKE_BOOST",
    "LAYER_DECAY_RATES",
    "apply_decay", "wake_up", "batch_decay", "install_decay_cleanup",
]
