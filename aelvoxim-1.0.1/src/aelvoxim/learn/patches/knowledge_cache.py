"""aelvoxim.learn.patches.knowledge_cache — Cache for KnowledgeBase.get_all_active().

Avoids redundant full-scan reads of all knowledge entries from disk.
30-second TTL cache. Used by post_validation.py and Learner._review_mode().
"""

from __future__ import annotations

import time
from typing import Any, List

_CACHE: List[Any] = []
_CACHE_TS: float = 0
_TTL: float = 30.0


def get_all_active_cached() -> List[Any]:
    """Return cached get_all_active() results, refreshing every TTL seconds."""
    global _CACHE, _CACHE_TS
    now = time.time()
    if not _CACHE or (now - _CACHE_TS) > _TTL:
        from ..knowledge import KnowledgeBase
        _CACHE = list(KnowledgeBase.get_all_active())
        _CACHE_TS = now
    return _CACHE


def invalidate_active_cache() -> None:
    """Force refresh on next call. Call after store/delete operations."""
    global _CACHE_TS
    _CACHE_TS = 0
