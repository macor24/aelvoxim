"""aelvoxim.learn.patches.learner_cache — Cache SelfModel/BeliefPool instances.

Reduces file I/O: each cognition tick creates fresh SelfModel and BeliefPool
instances, each reading from disk. Cache them with TTL to avoid redundant I/O.

Usage: called at the start of _cognition_tick() before creating instances.
"""

from __future__ import annotations

import time
from typing import Any, Optional

_SM_CACHE_TTL = 300.0  # 5 minutes
_POOL_CACHE_TTL = 300.0

_sm_instance: Any = None
_sm_ts: float = 0

_pool_instance: Any = None
_pool_ts: float = 0


def get_selfmodel() -> Any:
    """Get cached SelfModel instance, refreshing every _SM_CACHE_TTL seconds."""
    global _sm_instance, _sm_ts
    now = time.time()
    if _sm_instance is None or (now - _sm_ts) > _SM_CACHE_TTL:
        from ...core.selfmodel import SelfModel
        _sm_instance = SelfModel()
        _sm_ts = now
    return _sm_instance


def get_beliefpool() -> Any:
    """Get cached BeliefPool instance, refreshing every _POOL_CACHE_TTL seconds."""
    global _pool_instance, _pool_ts
    now = time.time()
    if _pool_instance is None or (now - _pool_ts) > _POOL_CACHE_TTL:
        from ...core.belief import BeliefPool
        _pool_instance = BeliefPool()
        _pool_ts = now
    return _pool_instance


def invalidate_cache() -> None:
    """Force cache refresh on next get_*() call. Call after write operations."""
    global _sm_ts, _pool_ts
    _sm_ts = 0
    _pool_ts = 0
