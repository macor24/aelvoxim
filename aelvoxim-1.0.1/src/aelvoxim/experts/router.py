"""
metacore.experts.router — Task-based expert routing.

Classifies user query into task types, then selects relevant experts.

Two-tier classification:
  1. Keyword matching (fast, deterministic) — from config/routing_rules.json
  2. Embedding fallback (when keyword returns DEFAULT) — cosine similarity
     against EXAMPLE_QUERIES for each task type.

Edit routing_rules.json to add/modify patterns without code changes.

Usage:
    selector = RouteSelector()
    expert_names = selector.select(query, available_names)
    # -> ["memory", "logic", "safety"]
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ── Load routing rules from config file ──

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "routing_rules.json"


def _load_rules() -> dict:
    """Load routing rules from JSON config file."""
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


_rules = _load_rules()

TASK_ROUTES: Dict[str, Dict] = _rules.get("TASK_ROUTES", {})
_TASK_KEYWORDS: List[Tuple[List[str], str]] = [
    (keywords, task_type)
    for keywords, task_type in _rules.get("TASK_KEYWORDS", [])
]
_RISKY_PATTERNS: List[str] = _rules.get("RISKY_PATTERNS", [])
_DEFAULT_TASK: str = _rules.get("DEFAULT_TASK", "chat")
_EXAMPLE_QUERIES: Dict[str, List[str]] = _rules.get("EXAMPLE_QUERIES", {})
_EMBEDDING_CONFIDENCE: float = _rules.get("EMBEDDING_FALLBACK_CONFIDENCE", 0.6)


# ── Embedding fallback helpers ──


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _embedding_fallback_classify(query: str) -> str:
    """Classify query via embedding similarity when keyword matching fails.

    Computes embeddings for the query and all EXAMPLE_QUERIES,
    then picks the task type with the highest average similarity.
    Falls back to DEFAULT_TASK if below confidence threshold.
    """
    from ..storage.embedding import get_embedding

    q_emb = get_embedding(query)
    if not q_emb:
        return _DEFAULT_TASK

    best_task = _DEFAULT_TASK
    best_score = 0.0

    for task_type, examples in _EXAMPLE_QUERIES.items():
        if not examples:
            continue
        scores = []
        for ex in examples:
            e_emb = get_embedding(ex)
            if e_emb:
                scores.append(_cosine_similarity(q_emb, e_emb))
        if scores:
            avg = sum(scores) / len(scores)
            if avg > best_score:
                best_score = avg
                best_task = task_type

    if best_score >= _EMBEDDING_CONFIDENCE:
        return best_task
    return _DEFAULT_TASK


# ── Main classifier ──


class TaskClassifier:
    """Classify a user query into a task type based on keyword matching."""

    @staticmethod
    def classify(query: str) -> str:
        """Return the best-matching task type for a query."""
        if not query:
            return _DEFAULT_TASK
        q_lower = query.lower().strip()
        for keywords, task_type in _TASK_KEYWORDS:
            if any(kw in q_lower for kw in keywords):
                return task_type
        return _DEFAULT_TASK

    @staticmethod
    def classify_with_embedding_fallback(query: str) -> str:
        """Classify with two-tier: keywords first, then embedding fallback."""
        result = TaskClassifier.classify(query)
        if result != _DEFAULT_TASK or not _EXAMPLE_QUERIES:
            return result
        return _embedding_fallback_classify(query)

    @staticmethod
    def get_route(task_type: str) -> Optional[dict]:
        """Get route definition for a task type."""
        return TASK_ROUTES.get(task_type)


class RouteSelector:
    """Select which experts to run based on task type and context."""

    @staticmethod
    def select(query: str, available_names: Set[str],
               use_embedding_fallback: bool = False) -> List[str]:
        """Return ordered list of expert names to run for a given query.

        Args:
            query: User's input text.
            available_names: Set of registered expert names.
            use_embedding_fallback: If True, use embedding fallback when
                                     keyword matching returns DEFAULT.

        Returns:
            List of expert names in execution order.
        """
        if use_embedding_fallback:
            task_type = TaskClassifier.classify_with_embedding_fallback(query)
        else:
            task_type = TaskClassifier.classify(query)
        route = TASK_ROUTES.get(task_type)

        if not route:
            return sorted(available_names)

        selected = [name for name in route["experts"] if name in available_names]

        if task_type != "security" and "safety" in available_names:
            q_lower = query.lower()
            if any(p in q_lower for p in _RISKY_PATTERNS):
                if "safety" not in selected:
                    selected.append("safety")

        return selected or sorted(available_names)

    @staticmethod
    def get_supported_tasks() -> Dict[str, str]:
        """Return all supported task types with descriptions."""
        return {
            task: route["description"]
            for task, route in TASK_ROUTES.items()
        }
