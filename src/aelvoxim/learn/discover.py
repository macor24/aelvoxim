"""aelvoxim.learn.discover — Auto-discovery of new learning directions

Two discovery modes:
1. External: arXiv API + search trend extraction (new)
2. Internal: Knowledge base topic analysis (existing)

All functions are pure — learner.py calls these with its own state.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Set

import logging
_log = logging.getLogger("aelvoxim.learn.discover")

# ── arXiv discovery ──────────────────────────

# Categories most relevant to MetaCore's domain
ARXIV_CATEGORIES = [
    "cs.AI",   # Artificial Intelligence
    "cs.LG",   # Machine Learning
    "cs.SE",   # Software Engineering
    "cs.CL",   # Computation and Language (NLP)
    "cs.IR",   # Information Retrieval
]

# Generic words that should not become learning directions
GENERIC_STOPWORDS: Set[str] = {
    "research", "study", "approach", "method", "system", "framework",
    "analysis", "survey", "review", "introduction", "overview",
    "technique", "application", "tool", "model", "algorithm",
    "learning", "optimization", "detection", "recognition", "prediction",
    "estimation", "classification", "generation", "understanding",
    "towards", "based", "using", "via", "with", "for",
    "new", "novel", "efficient", "effective", "improved",
    "large", "deep", "neural", "convolutional", "recurrent",
}


def fetch_arxiv_titles(category: str = "cs.AI", max_results: int = 10) -> List[str]:
    """Fetch recent paper titles from arXiv API for a given category.

    Args:
        category: arXiv category code (e.g. 'cs.AI', 'cs.SE')
        max_results: Number of recent papers to fetch

    Returns:
        List of paper titles (strings). Empty list if network unreachable.
    """
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=cat:{category}&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={max_results}"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "MetaCore/0.1 (academic direction discovery)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read().decode("utf-8", errors="replace")
    except Exception:
        # arXiv may be unreachable (e.g. in China networks). Silent degrade.
        return []

    titles: List[str] = []
    for entry in re.finditer(r'<entry>(.*?)</entry>', xml_data, re.DOTALL):
        title_match = re.search(r'<title>(.*?)</title>', entry.group(1), re.DOTALL)
        if title_match:
            title = title_match.group(1).strip()
            title = re.sub(r'\s+', ' ', title)
            if title:
                titles.append(title)
    return titles


def extract_direction_candidates(titles: List[str], existing: Set[str]) -> List[str]:
    """Extract learning direction candidates from paper titles.

    Filters out generic titles and duplicates.

    Args:
        titles: List of paper title strings.
        existing: Set of topic names already being learned.

    Returns:
        List of candidate direction names (deduplicated).
    """
    candidates: List[str] = []
    seen: Set[str] = set(existing)

    for title in titles:
        cleaned = title.strip()
        for prefix in ["Towards ", "Toward ", "A ", "An ", "The "]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]

        if len(cleaned) < 15:
            continue

        words = cleaned.lower().split()
        stop_count = sum(1 for w in words if w.rstrip(".,:;!?") in GENERIC_STOPWORDS)
        if stop_count > 2 and len(words) > 5:
            continue

        parts = re.split(r'[.:;]', cleaned)
        first_part = parts[0].strip() if parts else cleaned

        if len(first_part) < 10:
            continue

        first_part = first_part[0].upper() + first_part[1:] if first_part else first_part
        first_part = first_part.rstrip(".,;:")

        # Anti-pollution: reject overly short/long candidates
        if len(first_part) < 15 or len(first_part) > 60:
            continue

        # Anti-pollution: must contain at least one technical keyword
        _tech_kw = ["Multi", "Reinforcement", "Graph", "Neural", "Network", "Learning",
                     "Agent", "Reasoning", "Optimization", "Generative", "Transformer",
                     "Diffusion", "Attention", "Language", "Vision", "Knowledge",
                     "Inference", "Decision", "Control", "Estimation", "Detection",
                     "Segmentation", "Generation", "Retrieval", "Preference", "Feedback",
                     "Representation", "Quantization", "Distillation", "Ensemble",
                     "Generalization", "Adaptation", "Transfer", "Federated",
                     "Reinforcement", "Unsupervised", "Supervised", "Self",
                     "Causal", "Probabilistic", "Bayesian", "Stochastic",
                     "Autonomous", "Robotics", "Manipulation", "Navigation",
                     "Algorithm", "Ranking", "Summarization", "Translation",
                     "Dialogue", "Conversation", "Multimodal", "Cross",
                     "Scalable", "Distributed", "Efficient", "Privacy",
                     "Domain", "Object", "Action", "Policy", "Reward",
                     "Encoding", "Embedding", "Zero", "Few", "Meta", "Continual"]
        if not any(kw.lower() in first_part.lower() for kw in _tech_kw):
            continue

        if first_part in seen:
            continue
        seen.add(first_part)
        candidates.append(first_part)

    return candidates


def discover_from_arxiv(existing: Set[str], categories: Optional[List[str]] = None) -> List[str]:
    """Discover new learning directions from recent arXiv papers.

    Each category API call has a 10s timeout. If arXiv is unreachable
    (e.g. China networks), returns empty list within ~15s total.
    """
    import time as _time
    _deadline = _time.time() + 15  # total budget: 15 seconds

    all_candidates: List[str] = []
    seen: Set[str] = set(existing)

    cats = categories or ARXIV_CATEGORIES
    for cat in cats:
        if _time.time() > _deadline:
            break
        try:
            titles = fetch_arxiv_titles(cat, max_results=10)
            if not titles:
                continue
            candidates = extract_direction_candidates(titles, seen)
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    all_candidates.append(c)
        except Exception:
            continue

    return all_candidates


# ── Combined discovery entry point ────────────


def discover_directions(
    existing: Set[str],
    all_entries: Optional[list] = None,
    max_suggestions: int = 5,
) -> List[str]:
    """Main discovery entry point. Tries arXiv first, falls back to KB analysis.

    Args:
        existing: Set of topic names already being learned.
        all_entries: Full knowledge base entries list (for KB discovery).
        max_suggestions: Max number of suggestions to return.

    Returns:
        List of suggested direction names (to be added as 'paused').
    """
    candidates: List[str] = []

    # 1. arXiv discovery
    try:
        arxiv_candidates = discover_from_arxiv(existing | set(candidates))
        candidates.extend(arxiv_candidates[:3])
    except Exception:
        _log.exception("discover error")

    # 2. KB-based discovery (existing logic)
    if all_entries:
        try:
            kb_candidates = suggest_directions_from_knowledge(
                existing | set(candidates), all_entries,
                max_suggestions=max_suggestions - len(candidates),
            )
            candidates.extend(kb_candidates)
        except Exception:
            _log.exception("discover error")

    return candidates[:max_suggestions]


# ── Existing KB-based discovery (backward compat) ──


def suggest_directions_from_knowledge(
    existing_topics: Set[str],
    all_entries: list,
    max_suggestions: int = 3,
) -> List[str]:
    """Analyze knowledge base and suggest topics not yet being learned."""
    user_chat_candidates: Dict[str, float] = {}
    other_candidates: Dict[str, float] = {}

    for e in all_entries:
        t = e.get("topic", "").strip()
        if not t or t in existing_topics:
            continue
        src = e.get("source", "")
        conf = e.get("confidence", 0)
        vl = e.get("value_level", 0)

        if src == "user_chat" and vl >= 2:
            if t not in user_chat_candidates or conf > user_chat_candidates[t]:
                user_chat_candidates[t] = conf
        elif src != "user_chat":
            if t not in other_candidates or conf > other_candidates[t]:
                other_candidates[t] = conf

    suggestions = sorted(user_chat_candidates.keys(),
                         key=lambda t: user_chat_candidates[t],
                         reverse=True)
    suggestions += sorted(other_candidates.keys(),
                          key=lambda t: other_candidates[t],
                          reverse=True)

    return suggestions[:max_suggestions]


# ── W9: Simulated task generation ────────────


def generate_simulated_tasks(
    existing: Set[str],
    all_entries: Optional[list] = None,
    max_tasks: int = 5,
) -> List[Dict[str, str]]:
    """Generate simulated learning tasks from knowledge gaps.

    Args:
        existing: Set of topic names already being learned.
        all_entries: Full knowledge base entries list.
        max_tasks: Maximum tasks to generate (default 5, safety limit).

    Returns:
        List of dicts: [{"goal": "Learn about X", "task_type": "learn"}]
        Empty if no gaps found or self-iteration disabled.
    """
    if not all_entries:
        return []
    tasks: List[Dict[str, str]] = []
    seen: Set[str] = set(existing)
    # Look for topics with low confidence that need deeper exploration
    candidates: Dict[str, float] = {}
    for e in all_entries:
        t = e.get("topic", "").strip()
        if not t or t in seen:
            continue
        conf = e.get("confidence", 0)
        vl = e.get("value_level", 0)
        if conf < 0.5:
            candidates[t] = candidates.get(t, 0) + (1 - conf)
    # Sort by gap severity
    for topic in sorted(candidates, key=candidates.get, reverse=True)[:max_tasks]:
        tasks.append({
            "goal": f"Research and deepen knowledge on: {topic[:60]}",
            "task_type": "learn",
        })
        seen.add(topic)
    return tasks
