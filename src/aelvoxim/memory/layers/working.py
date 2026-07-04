# SPDX-License-Identifier: MIT
"""aelvoxim.memory.layers.working — Working memory, 24h TTL."""

from __future__ import annotations

from typing import Any, List, Optional

from ..entry import MemoryEntry, LAYER_WORKING
from .base import BaseMemoryLayer


class WorkingMemory(BaseMemoryLayer):
    def __init__(self):
        super().__init__(LAYER_WORKING)

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        entry.layer = LAYER_WORKING
        entry.touch()
        self._entries[entry.key] = entry
        return entry

    def retrieve(self, key: str) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._entries[key]
            return None
        entry.touch()
        return entry.value

    def recall(self, limit: int = 10) -> List[MemoryEntry]:
        entries = self.all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]
