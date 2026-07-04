# SPDX-License-Identifier: MIT
"""
metacore.memory.entry — Memory entry data model.

Shared MemoryEntry dataclass for all memory layers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


# ── Layer name constants ──

LAYER_WORKING = "working"       # Short-term context, 24h TTL
LAYER_EPISODIC = "episodic"     # Conversation records, 7d TTL
LAYER_SEMANTIC = "semantic"     # Long-term knowledge/preferences, permanent


LAYER_PROCEDURAL = "procedural" # Verified knowledge, never decays
ALL_LAYERS = [LAYER_WORKING, LAYER_EPISODIC, LAYER_SEMANTIC, LAYER_PROCEDURAL]

DEFAULT_TTL = {
    LAYER_WORKING: 86400,        # 1 day
    LAYER_EPISODIC: 604800,      # 7 days
    LAYER_SEMANTIC: None,        # permanent
}


@dataclass
class MemoryEntry:
    """A single memory entry. Shared structure across all layers."""
    key: str
    value: Any
    layer: str = LAYER_WORKING
    tags: List[str] = field(default_factory=list)
    timestamp: str = ""
    access_count: int = 0
    last_access: str = ""
    ttl_seconds: Optional[int] = None
    source: str = ""
    importance: float = 0.5
    strength: float = 1.0
    entities: List[str] = field(default_factory=list)
    is_private: bool = False
    immutable: bool = False
    version: int = 1
    superseded_by: Optional[str] = None
    conflict_status: str = "active"
    decay_rate: float = 0.05
    base_importance: float = 0.5

    def __post_init__(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.timestamp:
            self.timestamp = now
        if not self.last_access:
            self.last_access = now
        if self.ttl_seconds is None:
            self.ttl_seconds = DEFAULT_TTL.get(self.layer)

    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        try:
            created = datetime.strptime(self.timestamp[:19], "%Y-%m-%d %H:%M:%S")
            return datetime.now() - created > timedelta(seconds=self.ttl_seconds)
        except Exception:
            return False

    def touch(self) -> None:
        self.access_count += 1
        self.strength = min(1.0, self.strength + 0.1)
        self.last_access = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "value": self.value,
            "layer": self.layer,
            "tags": self.tags,
            "timestamp": self.timestamp,
            "access_count": self.access_count,
            "last_access": self.last_access,
            "ttl_seconds": self.ttl_seconds,
            "source": self.source,
            "importance": self.importance,
            "strength": round(self.strength, 2),
            "entities": self.entities,
            "is_private": self.is_private,
            "immutable": self.immutable,
            "version": self.version,
            "superseded_by": self.superseded_by,
            "conflict_status": self.conflict_status,
            "decay_rate": self.decay_rate,
            "base_importance": self.base_importance,
        }

    @staticmethod
    def from_dict(d: Dict) -> MemoryEntry:
        return MemoryEntry(**{k: v for k, v in d.items()
                              if k in MemoryEntry.__dataclass_fields__})


# ── Pre-write checks ──

LOW_VALUE_KEY_PREFIXES = {"echo-", "heartbeat-", "tick-", "status-"}


def should_store(
    key: str,
    value: Any,
    importance: float,
    tags: Optional[List[str]] = None,
) -> bool:
    """Pre-write check: determine whether this entry is worth storing.

    Returns False to discard, True to allow storage.
    """
    text = str(value).strip()
    if not text:
        return False

    # Low-value key prefixes (auto-generated system records)
    for prefix in LOW_VALUE_KEY_PREFIXES:
        if key.startswith(prefix):
            return False

    # Low importance → discard
    if importance < 0.2:
        return False

    return True


__all__ = [
    "MemoryEntry",
    "LAYER_WORKING", "LAYER_EPISODIC", "LAYER_SEMANTIC",
    "ALL_LAYERS", "DEFAULT_TTL",
    "should_store",
]
