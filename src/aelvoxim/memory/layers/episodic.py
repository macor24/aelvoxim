# SPDX-License-Identifier: MIT
"""aelvoxim.memory.layers.episodic — Episodic memory, 7-day TTL."""

from __future__ import annotations

from typing import Any, List, Optional

from ..entry import MemoryEntry, LAYER_EPISODIC
from .base import BaseMemoryLayer


class EpisodicMemory(BaseMemoryLayer):
    def __init__(self):
        super().__init__(LAYER_EPISODIC)

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        entry.layer = LAYER_EPISODIC
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

    def timeline(self, limit: int = 20) -> List[MemoryEntry]:
        entries = self.all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]
