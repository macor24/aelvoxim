# SPDX-License-Identifier: MIT
"""
metacore.memory.forget — Active forgetting for memory layers.

Periodic cleanup: expires entries past their TTL, removes low-value entries.

Part of MetaCore's 3-layer memory system. Works alongside the forgetting
curve decay in learner.py (which handles Belief decay).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .entry import MemoryEntry, LAYER_WORKING, LAYER_EPISODIC, LAYER_SEMANTIC
from .layers import WorkingMemory, EpisodicMemory, SemanticMemory


def cleanup_working(layer: WorkingMemory, max_seconds: int = 86400) -> int:
    """Expire working entries past their TTL and remove stale ones."""
    return layer.cleanup()


def cleanup_episodic(layer: EpisodicMemory, max_seconds: int = 604800) -> int:
    """Expire episodic entries past 7 days and low-value ones."""
    return layer.cleanup()


def cleanup_semantic(layer: SemanticMemory, max_idle_days: int = 30) -> int:
    """Remove low-importance semantic entries that haven't been accessed in 30+ days.

    Entries with importance >= 0.5 are always kept. Only entries below 0.2
    and idle for 30+ days are removed.
    """
    now = datetime.now()
    removed = 0
    for key, entry in list(layer._entries.items()):
        if entry.immutable:
            continue
        if entry.importance >= 0.5:
            continue
        try:
            last_acc = datetime.strptime(entry.last_access[:19], "%Y-%m-%d %H:%M:%S")
            idle_days = (now - last_acc).days
            if idle_days >= max_idle_days and entry.importance < 0.2:
                del layer._entries[key]
                removed += 1
        except Exception:
            pass
    return removed


def cleanup_all(layers: Dict[str, object]) -> Dict[str, int]:
    """Clean up all layers. Returns {layer_name: count_removed}."""
    results = {}
    if hasattr(layers, 'working') or 'working' in layers:
        l = getattr(layers, 'working', None) or layers.get('working')
        if l:
            results['working'] = cleanup_working(l)
    if hasattr(layers, 'episodic') or 'episodic' in layers:
        l = getattr(layers, 'episodic', None) or layers.get('episodic')
        if l:
            results['episodic'] = cleanup_episodic(l)
    if hasattr(layers, 'semantic') or 'semantic' in layers:
        l = getattr(layers, 'semantic', None) or layers.get('semantic')
        if l:
            results['semantic'] = cleanup_semantic(l)
    return results
