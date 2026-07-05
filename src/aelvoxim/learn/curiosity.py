"""
metacore.learn.curiosity — Curiosity engine: auto-discover new learning directions.

When the Learner has no active directions, the curiosity engine picks the next
topic from an interest seed list, or derives a new topic from recently completed
directions. This lets the agent explore new knowledge autonomously.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Dict, List, Optional, Set

# ── Interest seeds ──────────────────────────────────────────
# Learner works through these in order when no active directions exist.
# After seeds are exhausted, derive_next() generates new topics from completed ones.

_INTEREST_SEEDS: List[str] = [
    # AI & ML
    "Large language model architectures and training",
    "Multi-agent AI systems and coordination",
    "Reinforcement learning from human feedback",
    "Neural network interpretability and mechanistic interpretability",
    "Diffusion models and generative AI",
    "Retrieval augmented generation and knowledge integration",
    # Mathematics
    "Information theory and entropy in machine learning",
    "Bayesian statistics and probabilistic programming",
    "Linear algebra and tensor computation in ML",
    "Optimization theory and gradient-based methods",
    # Quantum
    "Quantum computing fundamentals and qubit systems",
    "Quantum error correction and fault tolerance",
    "Quantum machine learning and variational circuits",
]

# Track which seeds have been picked — stored as a simple set of completed topic names
_SEEDS_DONE: Set[str] = set()
# Cache: topics that failed to add (e.g. due to plan limit) — skip for a while
_FAILED_TOPICS: Dict[str, float] = {}
_FAILED_TTL = 300  # re-try after 5 minutes


def pick_next_topic(
    existing_directions: Dict[str, object],
    log_func,
) -> Optional[str]:
    """Pick the next topic to learn.

    Priority:
      1. A seed not yet learned (not in existing_directions and not in _SEEDS_DONE).
      2. A derived topic from the most recently completed direction.
      3. None (nothing to learn).

    Returns a topic string, or None.
    """
    existing_names = set(existing_directions.keys())

    # 1. Check seeds
    for seed in _INTEREST_SEEDS:
        # Match by checking if any existing direction name is a substring of the seed
        # or vice versa (catches "AI agent architectures" when seed is longer)
        already_learning = any(
            s.lower() in seed.lower() or seed.lower() in s.lower()
            for s in existing_names
        )
        if not already_learning and seed not in _SEEDS_DONE:
            _SEEDS_DONE.add(seed)
            log_func(f"  🧠 [Curiosity] Picked seed: {seed}")
            return seed

    # 2. Derive from completed directions
    completed = [
        name for name, d in existing_directions.items()
        if getattr(d, 'status', '') in ('completed', 'mastery')
    ]
    if completed:
        # Pick the most recently completed one
        target = completed[-1]
        derived = derive_topics(target, existing_names)
        if derived:
            log_func(f"  🧠 [Curiosity] Derived from '{target}': {derived[0]}")
            return derived[0]

    return None


def derive_topics(completed_topic: str, existing_names: Set[str]) -> List[str]:
    """Extract candidate new topics from knowledge entries of a completed direction.

    Scans knowledge entries whose topic matches `completed_topic`, extracts
    capitalized noun phrases (potential concept names), filters out already-learned
    topics, and returns the top 2 candidates.
    """
    from .knowledge import KnowledgeBase

    try:
        entries = list(KnowledgeBase.search(query=completed_topic, min_confidence=0.3, limit=20))
    except Exception:
        return []

    candidates: Counter = Counter()
    for e in entries:
        title = e.get("title", "") or ""
        content = (e.get("content") or e.get("summary") or "")[:500]
        text = f"{title} {content}"

        # Extract capitalized noun phrases (2-5 words, first letter uppercase)
        # e.g. "Tool Use", "Shor's Algorithm", "Gradient Descent"
        phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|\d+[a-z]*)){0,3}', text)
        for phrase in phrases:
            phrase = phrase.strip()
            if len(phrase) < 5 or len(phrase) > 60:
                continue
            # Filter out noise: single common words, directions, URLs
            if phrase.lower() in (
                "this", "that", "from", "with", "they", "what", "when",
                "where", "which", "there", "their", "about", "would", "could",
                "should", "after", "before", "between", "without", "through",
                "during", "because", "support", "result", "results", "using",
                "based", "related", "common", "other", "these", "those",
                "value", "values", "method", "methods", "approach",
            ):
                continue
            if phrase.lower() in existing_names:
                continue
            candidates[phrase] += 1

    # Return top 2 candidates that appear more than once
    top = [p for p, _ in candidates.most_common(5) if _ > 1][:2]
    return top


def activate_curiosity(
    directions: Dict[str, object],
    add_direction_fn,
    log_func,
) -> bool:
    """Try to activate a new direction via the curiosity engine.

    Called during Learner's idle cycle when no active directions exist.
    Returns True if a new direction was added.

    Edition gate: community edition disables curiosity-driven discovery.
    """
    # Edition gate
    try:
        from aelvoxim.server.edition import get as _ed_get
        if not _ed_get("curiosity_enabled", False):
            return False
    except ImportError:
        pass

    topic = pick_next_topic(directions, log_func)
    if not topic:
        return False

    # Skip if this topic recently failed
    now = time.time()
    if topic in _FAILED_TOPICS and now - _FAILED_TOPICS[topic] < _FAILED_TTL:
        return False

    # Truncate very long topic names (direction topic limit is 200 chars)
    topic_short = topic[:180]

    if add_direction_fn(topic_short):
        log_func(f"  🧠 [Curiosity] Started learning: {topic_short}")
        return True

    log_func(f"  ⚠️ [Curiosity] Failed to add: {topic_short}")
    _FAILED_TOPICS[topic] = now
    return False
