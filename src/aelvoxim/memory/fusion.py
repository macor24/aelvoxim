# SPDX-License-Identifier: MIT
"""
metacore.memory.fusion — Memory fusion layer.

Cross-layer retrieval with inverted index + layer-priority search.
- Inverted index: token -> [(layer_name, entry_key, weight), ...]
- Layer priority: procedural > semantic > episodic > working
- Fusion weights configurable via calibration.json "fusion" section
- Pure stdlib, zero external dependencies

Ported from MetaCore memory/fusion.py, adapted for pure-stdlib architecture.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .entry import MemoryEntry, ALL_LAYERS
from .layers import WorkingMemory, EpisodicMemory, SemanticMemory, ProceduralMemory


# ══════════════════════════════════════════════════════════════
# Inverted index tokenizer
# ══════════════════════════════════════════════════════════════


def _tokenize(*texts: str) -> List[Tuple[str, float]]:
    """Tokenize text into search terms with relevance weights.

    English words: weight 1.0
    Chinese characters (single): weight 0.5
    Chinese bigrams: weight 0.7

    Returns:
        List of (term, weight) tuples.
    """
    result: List[Tuple[str, float]] = []
    for text in texts:
        t = text.lower()
        # English words / identifiers
        for word in re.findall(r'[a-z0-9+#._/-]{2,}', t):
            result.append((word, 1.0))
        # Chinese characters (single char)
        chars = re.findall(r'[\u4e00-\u9fff]', t)
        for ch in chars:
            if ch:
                result.append((ch, 0.5))
        # Chinese bigrams
        for i in range(len(chars) - 1):
            result.append((chars[i] + chars[i+1], 0.7))

    # Deduplicate by term, keep highest weight
    best: Dict[str, float] = {}
    for term, w in result:
        if term not in best or w > best[term]:
            best[term] = w
    return list(best.items())


# ══════════════════════════════════════════════════════════════
# MemoryFusion
# ══════════════════════════════════════════════════════════════


class MemoryFusion:
    """Cross-layer fusion retriever. Queries all layers and returns
    sorted results from inverted-index hits with layer priority.

    Features:
        - Inverted index: token -> [(layer, key, weight)]
        - Priority search: procedural > semantic > episodic > working
        - Auto-rebuild after store/cleanup
    """

    # Default layer priority (overridable via calibration.json)
    _DEFAULT_LAYER_PRIORITY = {
        "procedural": 1.5,
        "semantic": 1.3,
        "episodic": 1.0,
        "working": 0.8,
    }

    def __init__(self):
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()

        # Inverted index: {term -> [(layer_name, entry_key, weight), ...]}
        self._inverted_index: Dict[str, List[Tuple[str, str, float]]] = {}
        self._layer_priority = dict(self._DEFAULT_LAYER_PRIORITY)
        self._index_dirty = True

    # ── Index management ──────────────────────────

    def _needs_rebuild(self) -> bool:
        """Check if the inverted index needs rebuilding."""
        return self._index_dirty or not self._inverted_index

    def rebuild_index(self) -> None:
        """Scan all layers and rebuild the inverted index."""
        self._inverted_index.clear()
        layers = [
            ("procedural", self.procedural),
            ("semantic", self.semantic),
            ("episodic", self.episodic),
            ("working", self.working),
        ]
        for layer_name, layer in layers:
            for key, entry in layer._entries.items():
                tokens = _tokenize(entry.key, str(entry.value))
                for token, weight in tokens:
                    self._inverted_index.setdefault(token, []).append(
                        (layer_name, key, weight)
                    )
        self._index_dirty = False

    def mark_dirty(self) -> None:
        """Mark the index as stale (call after store/cleanup)."""
        self._index_dirty = True

    def add_to_index(self, layer_name: str, key: str, entry) -> None:
        """Incrementally update inverted index for a single entry.
        
        Call this after storing a new entry, instead of marking dirty
        and forcing a full rebuild later.
        """
        tokens = _tokenize(entry.key, str(entry.value))
        for token, weight in tokens:
            self._inverted_index.setdefault(token, []).append(
                (layer_name, key, weight)
            )

    def remove_from_index(self, key: str) -> None:
        """Remove all references to a key from the inverted index."""
        for token in list(self._inverted_index.keys()):
            self._inverted_index[token] = [
                entry for entry in self._inverted_index[token]
                if entry[1] != key
            ]
        self._inverted_index = {
            k: v for k, v in self._inverted_index.items() if v
        }

    def set_layer_priority(self, priority: Dict[str, float]) -> None:
        """Override layer priority weights from external config (calibration)."""
        if priority:
            self._layer_priority.update(priority)

    # ── Search ──────────────────────────────────

    def search(self, query: str = "", limit: int = 20) -> List[MemoryEntry]:
        """Cross-layer search.

        Strategy:
            - With query: inverted index hit + layer priority ranking
            - Without query: timeline sort (importance + freshness + access frequency)

        Args:
            query: Search terms
            limit: Max results to return

        Returns:
            Sorted MemoryEntry list
        """
        if query:
            return self._search_by_priority(query, limit)
        return self._search_timeline(limit)

    def _search_by_priority(self, query: str, limit: int) -> List[MemoryEntry]:
        """Layer-priority search: procedural > semantic > episodic > working."""
        if self._needs_rebuild():
            self.rebuild_index()

        q = query.lower()
        tokens = [t for t, _ in _tokenize(q)]
        seen_keys: Set[str] = set()
        results: List[Tuple[MemoryEntry, float]] = []

        # Gather candidates from inverted index
        candidates: Dict[str, List[Tuple[str, float]]] = {}
        for token in tokens:
            matches = self._inverted_index.get(token, [])
            for layer_name, key, weight in matches:
                candidates.setdefault(key, []).append((layer_name, weight))

        # Scan layers in priority order
        for layer_name in ["procedural", "semantic", "episodic", "working"]:
            layer = self._get_layer_by_name(layer_name)
            if not layer:
                continue

            for key, entry in layer._entries.items():
                if entry.is_expired() or key in seen_keys:
                    continue
                if entry.conflict_status not in ("active", "pending"):
                    continue

                cand = candidates.get(key)
                if cand:
                    # Score = average hit weight * layer priority + importance
                    avg_weight = sum(w for _, w in cand) / len(cand)
                    priority = self._layer_priority.get(layer_name, 1.0)
                    score = avg_weight * priority + entry.importance * 0.3
                    seen_keys.add(key)
                    results.append((entry, score))

        results.sort(key=lambda x: -x[1])
        return [e for e, _ in results[:limit]]

    def _search_timeline(self, limit: int) -> List[MemoryEntry]:
        """Timeline sort (no query). Ranks by importance + freshness + access frequency."""
        results: List[Tuple[MemoryEntry, float]] = []
        now = datetime.now()

        layers = [self.working, self.episodic, self.semantic]
        for layer in layers:
            for entry in layer._entries.values():
                if entry.is_expired():
                    continue
                if entry.conflict_status not in ("active", "pending"):
                    continue

                imp = entry.importance
                try:
                    created = datetime.strptime(entry.timestamp[:19], "%Y-%m-%d %H:%M:%S")
                    hours_ago = (now - created).total_seconds() / 3600
                    freshness = max(0, 1.0 - hours_ago / 168)
                except Exception:
                    freshness = 0.5

                access = min(entry.access_count / 20, 1.0)
                score = imp * 0.5 + freshness * 0.3 + access * 0.2
                results.append((entry, score))

        results.sort(key=lambda x: -x[1])
        return [e for e, _ in results[:limit]]

    def search_by_layer(self, layer_name: str, query: str = "", limit: int = 20) -> List[MemoryEntry]:
        """Search within a single layer."""
        layer = self._get_layer_by_name(layer_name)
        if not layer:
            return []

        q = query.lower()
        results = []
        for entry in layer._entries.values():
            if entry.is_expired():
                continue
            if q and q not in entry.key.lower() and q not in str(entry.value).lower():
                continue
            results.append(entry)

        results.sort(key=lambda e: e.importance, reverse=True)
        return results[:limit]

    def get_layer(self, name: str):
        """Get layer object by name (compat with old callers)."""
        return self._get_layer_by_name(name)

    def _get_layer_by_name(self, name: str):
        mapping = {
            "working": self.working,
            "episodic": self.episodic,
            "semantic": self.semantic,
            "procedural": self.procedural,
        }
        return mapping.get(name)

    # ── Stats ────────────────────────────────────

    def stats(self) -> Dict:
        return {
            "total_active": sum(l.count() for l in [
                self.working, self.episodic, self.semantic]),
            "by_layer": {
                "working": self.working.count(),
                "episodic": self.episodic.count(),
                "semantic": self.semantic.count(),
            },
            "index_entries": len(self._inverted_index),
        }

    def cleanup_all(self) -> int:
        """Remove expired entries from all layers and rebuild the index."""
        total = 0
        for l in [self.working, self.episodic, self.semantic]:
            total += l.cleanup()
        if total > 0:
            self.mark_dirty()
        return total

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store entry to the appropriate layer (compat with old callers) and update index incrementally."""
        from ..memory import _determine_layer, _store_to_fusion
        layer = _determine_layer(entry)
        target = self.get_layer(layer)
        if target:
            target.store(entry)
        # Incremental index update instead of full rebuild
        self.add_to_index(layer, entry.key, entry)
        return entry
