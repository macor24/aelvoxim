# SPDX-License-Identifier: MIT
"""aelvoxim.memory.layers.procedural — L4 procedural memory (permanent)."""

from __future__ import annotations

from typing import Any, List, Optional

from ..entry import MemoryEntry, LAYER_PROCEDURAL
from .base import BaseMemoryLayer


class ProceduralMemory(BaseMemoryLayer):
    """L4 procedural memory — never decays, holds verified knowledge.

    Entries here are:
    - Learner A-grade knowledge
    - User-confirmed memories
    - Entities with access_count >= 5 that reached semantic layer
    """

    def __init__(self) -> None:
        super().__init__(name="procedural")

    @property
    def layer_name(self) -> str:
        return LAYER_PROCEDURAL

    def store(self, entry: MemoryEntry) -> None:
        entry.layer = LAYER_PROCEDURAL
        entry.ttl_seconds = None  # Never expire
        self._entries[entry.key] = entry

    def remove(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    def search(self, query: str, limit: int = 10) -> List[MemoryEntry]:
        """Search by key or value substring."""
        q = query.lower()
        results = []
        for e in self._entries.values():
            if q in e.key.lower() or q in str(e.value).lower():
                results.append(e)
                if len(results) >= limit:
                    break
        return results

    def count(self) -> int:
        return len(self._entries)

    def retrieve(self, key: str) -> Optional[MemoryEntry]:
        return self._entries.get(key)
