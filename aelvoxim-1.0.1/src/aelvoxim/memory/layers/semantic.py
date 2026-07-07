# SPDX-License-Identifier: MIT
"""aelvoxim.memory.layers.semantic — Semantic memory, no expiry."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..entry import MemoryEntry, LAYER_SEMANTIC
from .base import BaseMemoryLayer


class SemanticMemory(BaseMemoryLayer):
    def __init__(self):
        super().__init__(LAYER_SEMANTIC)

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        entry.layer = LAYER_SEMANTIC
        entry.ttl_seconds = None
        entry.touch()
        self._entries[entry.key] = entry
        return entry

    def retrieve(self, key: str) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.touch()
        return entry.value

    def search(self, query: str = "", limit: int = 20) -> List[MemoryEntry]:
        q = query.lower()
        results = []
        for e in self._entries.values():
            if q:
                if q in e.key.lower() or q in str(e.value).lower():
                    results.append(e)
            else:
                results.append(e)
        results.sort(key=lambda e: e.importance * 0.7 + min(e.access_count / 10, 1) * 0.3, reverse=True)
        return results[:limit]

    def get_important(self, threshold: float = 0.7) -> List[MemoryEntry]:
        return [e for e in self._entries.values() if e.importance >= threshold]
