# SPDX-License-Identifier: MIT
"""aelvoxim.memory.layers.base — Base layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..entry import MemoryEntry


class BaseMemoryLayer(ABC):
    """Memory layer base class."""

    def __init__(self, name: str):
        self.name = name
        self._entries: Dict[str, MemoryEntry] = {}

    @abstractmethod
    def store(self, entry: MemoryEntry) -> MemoryEntry:
        ...

    @abstractmethod
    def retrieve(self, key: str) -> Optional[Any]:
        ...

    def all(self) -> List[MemoryEntry]:
        return [e for e in self._entries.values() if not e.is_expired()]

    def count(self) -> int:
        return len(self.all())

    def cleanup(self) -> int:
        expired = [k for k, e in self._entries.items() if e.is_expired()]
        for k in expired:
            del self._entries[k]
        return len(expired)
