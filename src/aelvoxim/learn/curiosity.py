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

import logging
_log = logging.getLogger("aelvoxim.learn.curiosity")

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
# Track derived topics to prevent self-loop
_DERIVED_DONE: Set[str] = set()
# 120-second short-term dedup: suppress redundant derived topic output
_DERIVED_RECENT: Dict[str, float] = {}
_DERIVED_DEDUP_TTL = 120
# Cache: topics that failed to add (e.g. due to plan limit) — skip for a while
_FAILED_TOPICS: Dict[str, float] = {}
_FAILED_TTL = 300  # re-try after 5 minutes
# Anti-no-op backoff state (see activate_curiosity)
_LAST_ATTEMPT_TS: float = 0.0
_EMPTY_ATTEMPTS: int = 0

# ── Exploration circuit breaker ─────────────────────────
# 20 consecutive rounds with no new derived topic → switch seed
_EMPTY_ROUNDS: int = 0
_EMPTY_LIMIT = 20
_CUR_SEED_INDEX: int = 0  # current seed position for forced switch

# ── Diversity metrics ───────────────────────────────────
_CURIOSITY_STATS_FILE = None  # lazy init

def _stats_path() -> str:
    global _CURIOSITY_STATS_FILE
    if _CURIOSITY_STATS_FILE is None:
        try:
            from ..utils import DATA_DIR
            _CURIOSITY_STATS_FILE = str(DATA_DIR / "curiosity_stats.json")
        except Exception:
            _CURIOSITY_STATS_FILE = ""
    return _CURIOSITY_STATS_FILE

def _load_stats() -> None:
    """Load persisted curiosity statistics from disk."""
    global _CURIOSITY_STATS
    fp = _stats_path()
    if not fp:
        return
    try:
        import json
        from pathlib import Path
        p = Path(fp)
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                for k in _CURIOSITY_STATS:
                    if k in data:
                        _CURIOSITY_STATS[k] = data[k]
    except Exception:
        pass

def _save_stats() -> None:
    """Persist curiosity statistics to disk."""
    fp = _stats_path()
    if not fp:
        return
    try:
        import json
        from pathlib import Path
        Path(fp).write_text(json.dumps(_CURIOSITY_STATS, ensure_ascii=False, indent=2))
    except Exception:
        pass

# Stats dict must be defined BEFORE _load_stats() runs at module init —
# previously _load_stats() was called first and hit NameError on the undefined
# _CURIOSITY_STATS, which was swallowed, so persisted stats never loaded
# (C15, 9.txt audit).
_CURIOSITY_STATS: Dict[str, float] = {
    "total_picks": 0.0,
    "seed_picks": 0.0,
    "derived_picks": 0.0,
    "branch_picks": 0.0,
    "unique_topics": 0.0,
    "empty_rounds": 0.0,
    "last_topic": "",
    "branch_depth": 0.0,
}  # accessible via get_curiosity_stats()

# Load persisted stats on module init (AFTER the dict is defined — C15).
_load_stats()

def get_curiosity_stats() -> Dict[str, float]:
    """Return current curiosity diversity metrics for dashboard display."""
    return dict(_CURIOSITY_STATS)

# ── Domain branch expansion rules ──────────────────────────
# Known root topics → realistic sub-topics for curiosity to explore
_BRANCH_RULES: Dict[str, List[str]] = {
    "python": [
        "Python syntax and data structures",
        "Python asynchronous programming",
        "Python numerical computation and NumPy",
        "Python machine learning and scikit-learn",
        "Python deep learning and PyTorch",
        "Python web development and FastAPI",
        "Python testing and pytest",
        "Python package management and pip",
        "Python performance optimization and profiling",
        "Python object-oriented programming",
    ],
    "large language model": [
        "Transformer architecture and attention mechanisms",
        "LLM fine-tuning and instruction tuning",
        "LLM quantization and model compression",
        "Prompt engineering and chain-of-thought",
        "Retrieval augmented generation pipelines",
        "LLM safety and alignment",
        "Multi-modal LLMs and vision-language models",
        "LLM evaluation and benchmarks",
    ],
    "reinforcement learning": [
        "Q-learning and value-based methods",
        "Policy gradient and actor-critic methods",
        "Deep reinforcement learning with DQN",
        "Proximal policy optimization algorithms",
        "Multi-agent reinforcement learning",
        "Inverse reinforcement learning",
        "Reward model training and RLHF",
    ],
    "quantum": [
        "Quantum circuit design and simulation",
        "Quantum gate decomposition and transpilation",
        "Variational quantum algorithms and VQE",
        "Quantum error correction codes",
        "Quantum machine learning models",
        "Quantum optimization and QAOA",
        "Quantum hardware and noise mitigation",
    ],
    "neural network": [
        "Feedforward neural networks and backpropagation",
        "Convolutional neural networks for vision",
        "Recurrent neural networks and LSTMs",
        "Attention mechanisms and transformers",
        "Graph neural networks",
        "Neural architecture search",
        "Regularization and dropout techniques",
    ],
    "bayesian": [
        "Bayesian inference and MCMC methods",
        "Probabilistic programming with PyMC",
        "Gaussian processes and Bayesian optimization",
        "Hierarchical Bayesian models",
        "Variational inference and VI",
    ],
}


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
    _now = time.time()

    def _record_pick(topic: str, pick_type: str) -> str:
        """Track diversity metrics for dashboard."""
        global _EMPTY_ROUNDS
        _EMPTY_ROUNDS = 0  # any successful pick resets empty counter
        _CURIOSITY_STATS["total_picks"] += 1
        _CURIOSITY_STATS[f"{pick_type}_picks"] = _CURIOSITY_STATS.get(f"{pick_type}_picks", 0.0) + 1.0
        _CURIOSITY_STATS["last_topic"] = topic
        if topic not in _DERIVED_DONE:
            _CURIOSITY_STATS["unique_topics"] += 1.0
        # Branch depth: count how many branch rules were consumed for this root
        for root, branches in _BRANCH_RULES.items():
            if root in topic.lower():
                _consumed = sum(1 for b in branches if b in _DERIVED_DONE)
                _CURIOSITY_STATS["branch_depth"] = max(_CURIOSITY_STATS["branch_depth"], float(_consumed))
        _save_stats()  # persist after every pick
        return topic

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
            return _record_pick(seed, "seed")

    # 2. Derive from completed directions
    completed = [
        name for name, d in existing_directions.items()
        if getattr(d, 'status', '') in ('completed', 'mastery')
    ] + list(_SEEDS_DONE)
    if completed:
        # Pick the most recently completed one
        target = completed[-1]
        derived = derive_topics(target, existing_names)
        if derived:
            # Dedup: skip if already derived or within 120s window
            for d in derived:
                if d in _DERIVED_DONE:
                    continue
                if d in _DERIVED_RECENT and _now - _DERIVED_RECENT[d] < _DERIVED_DEDUP_TTL:
                    continue
                _DERIVED_RECENT[d] = _now
                _DERIVED_DONE.add(d)
                log_func(f"  🧠 [Curiosity] Derived from '{target}': {d}")
                return _record_pick(d, "derived")
            return None
        # Fallback: branch rules for known root topics
        for root, branches in _BRANCH_RULES.items():
            if root in target.lower():
                for b in branches:
                    if b in _DERIVED_DONE:
                        continue
                    if b in _DERIVED_RECENT and _now - _DERIVED_RECENT[b] < _DERIVED_DEDUP_TTL:
                        continue
                    _DERIVED_RECENT[b] = _now
                    _DERIVED_DONE.add(b)
                    log_func(f"  🧠 [Curiosity] Branch from '{target}': {b}")
                    return _record_pick(b, "branch")

    # ── Exploration circuit breaker ──
    global _EMPTY_ROUNDS, _CUR_SEED_INDEX
    _EMPTY_ROUNDS += 1
    # Also record emptiness metric
    _CURIOSITY_STATS["empty_rounds"] = float(_EMPTY_ROUNDS)
    if _EMPTY_ROUNDS >= _EMPTY_LIMIT:
        _EMPTY_ROUNDS = 0
        _CUR_SEED_INDEX += 1
        # Try the next seed that hasn't been done
        for i in range(5):  # try up to 5 ahead
            idx = (_CUR_SEED_INDEX + i) % len(_INTEREST_SEEDS)
            forced = _INTEREST_SEEDS[idx]
            if forced not in _SEEDS_DONE:
                already = any(s.lower() in forced.lower() or forced.lower() in s.lower() for s in existing_names)
                if not already:
                    _CUR_SEED_INDEX = idx
                    _SEEDS_DONE.add(forced)
                    log_func(f"  🔄 [Curiosity] Circuit breaker — forced switch to seed: {forced}")
                    return _record_pick(forced, "seed")

    return None


def derive_topics(completed_topic: str, existing_names: Set[str]) -> List[str]:
    """Extract candidate new topics from knowledge entries of a completed direction.

    Scans knowledge entries whose topic matches `completed_topic`, extracts
    capitalized noun phrases (potential concept names), filters out already-learned
    topics, and returns the top 2 candidates.

    Forbids returning the original root topic to prevent self-loop.
    """
    from .knowledge import KnowledgeBase

    try:
        entries = list(KnowledgeBase.search(query=completed_topic, min_confidence=0.3, limit=20))
    except Exception:
        return []

    candidates: Counter = Counter()
    root_lower = completed_topic.lower()
    for e in entries:
        title = e.get("title", "") or ""
        content = (e.get("content") or e.get("summary") or "")[:500]
        text = f"{title} {content}"

        # Extract capitalized noun phrases and lowercase sub-topic references
        # e.g. "Tool Use", "Shor's Algorithm", "Gradient Descent", "async/await"
        phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|\d+[a-z]*)){0,3}|\b[a-z]+\s+(?:implementation|patterns|algorithms|functions|techniques|optimization|programming)\b', text)
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
            # Forbid self-loop: skip if phrase is same as or too similar to root topic
            _pl = phrase.lower().strip()
            _rl = root_lower.strip()
            if _pl == _rl or _pl.startswith(_rl) and len(_pl) < len(_rl) + 10:
                continue
            candidates[phrase] += 1

    # Score candidates for relevance and expansion value
    scored = []
    for phrase, freq in candidates.items():
        score = _score_derived_topic(phrase, root_lower, freq)
        if score >= 2.0:  # minimum quality threshold
            scored.append((phrase, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in scored[:2]]


def _score_derived_topic(phrase: str, root_lower: str, freq: int) -> float:
    """Score a candidate derived topic for relevance, expansion value, and uniqueness.

    Returns a score >= 0. Higher is better. Minimum threshold is 2.0.
    """
    pl = phrase.lower()
    score = 0.0

    # 1. Frequency bonus (appeared in multiple KB entries)
    score += min(freq, 5) * 1.0

    # 2. Topic relevance: shares significant words with root
    root_words = set(root_lower.split())
    phrase_words = set(pl.split())
    common = root_words & phrase_words
    if common:
        score += len(common) * 0.5

    # 3. Expansion potential: multi-word phrases are better
    word_count = len(pl.split())
    score += min(word_count, 5) * 0.3

    # 4. Penalty for vague/short phrases
    vague = {'introduction', 'overview', 'basics', 'fundamentals', 'getting started',
             'quick start', 'what is', 'how to', 'guide', 'tutorial'}
    if any(v in pl for v in vague):
        score -= 1.0

    # 5. Penalty for near-duplicates of root (too similar = no expansion)
    similarity = len(set(pl.split()) & set(root_lower.split())) / max(len(set(pl.split()) | set(root_lower.split())), 1)
    if similarity > 0.7:
        score -= 2.0

    # 6. Technical depth: has technical keywords
    tech = {'algorithm', 'architecture', 'implementation', 'optimization', 'framework',
            'protocol', 'pipeline', 'analysis', 'system', 'model', 'network', 'learning',
            'programming', 'design', 'pattern', 'testing', 'deployment', 'integration'}
    if any(t in pl for t in tech):
        score += 1.0

    return round(score, 1)


def activate_curiosity(
    directions: Dict[str, object],
    add_direction_fn,
    log_func,
) -> bool:
    """Try to activate a new direction via the curiosity engine.

    Called during Learner's idle cycle when no active directions exist.
    Returns True if a new direction was added.

    Edition gate: community edition disables curiosity-driven discovery.

    Anti-no-op backoff: consecutive fruitless attempts (no candidate, no seed,
    or blocked topic) widen the retry interval (1min → 2 → 4 → 8 → 15min cap),
    so idle cycles stop hammering the engine when there is nothing to learn.
    """
    global _LAST_ATTEMPT_TS, _EMPTY_ATTEMPTS
    _now0 = time.time()
    # Near-capacity bonus: with many directions already tracked, attempts are
    # likely to be rejected (blacklist / duplicates) — widen the retry window.
    # Pure noise reduction: no capacity change, no free slots created.
    _near_cap = 2 if len(directions) >= 80 else 0
    _backoff = min(300 * (2 ** (_EMPTY_ATTEMPTS + _near_cap)), 7200)  # 5min→…→2h cap (was 2min→1h; fewer polls, less CPU churn when idle/full)
    if _now0 - _LAST_ATTEMPT_TS < _backoff:
        return False
    _LAST_ATTEMPT_TS = _now0

    # Edition gate
    try:
        from aelvoxim.server.edition import get as _ed_get
        if not _ed_get("curiosity_enabled", False):
            return False
    except ImportError:
        _log.exception("curiosity error")

    if add_direction_fn is None:
        return False

    _now = time.time()

    # Priority: UnknownDiscovery candidates first (fresh discovered terms)
    try:
        from .unknown_discovery import pop_unknown_candidates
        _ud_candidates = pop_unknown_candidates(max_count=1)
        if _ud_candidates:
            _candidate = _ud_candidates[0]
            if _candidate not in _FAILED_TOPICS or _now - _FAILED_TOPICS.get(_candidate, 0) >= _FAILED_TTL:
                topic_short = _candidate[:180]
                if add_direction_fn(topic_short):
                    log_func(f"  🧠 [UnknownDiscovery] Started learning: {topic_short}")
                    _EMPTY_ATTEMPTS = 0
                    return True
                _FAILED_TOPICS[_candidate] = _now
                log_func(f"  ⚠️ [UnknownDiscovery] Failed to add: {topic_short}")
                _EMPTY_ATTEMPTS += 1
                return False
    except Exception:
        _log.exception("curiosity error")

    topic = pick_next_topic(directions, log_func)
    if not topic:
        _EMPTY_ATTEMPTS += 1
        return False

    # Skip if this topic recently failed
    now = time.time()
    if topic in _FAILED_TOPICS and now - _FAILED_TOPICS[topic] < _FAILED_TTL:
        _EMPTY_ATTEMPTS += 1
        return False

    # Truncate very long topic names (direction topic limit is 200 chars)
    topic_short = topic[:180]

    if add_direction_fn(topic_short):
        log_func(f"  🧠 [Curiosity] Started learning: {topic_short}")
        _EMPTY_ATTEMPTS = 0
        return True

    log_func(f"  ⚠️ [Curiosity] Failed to add: {topic_short}")
    _FAILED_TOPICS[topic] = now
    _EMPTY_ATTEMPTS += 1
    return False
