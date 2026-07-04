"""
metacore.learn.teach — Pure rule-based learning engine (Teach mode)

When LLM is unavailable AND search is mock/unavailable, the learning loop
would spin without producing quality knowledge. Teach mode replaces the
LLM-dependent pipeline steps with rule-based alternatives:

1. **Decompose**: Use preset blocks instead of LLM decomposition
2. **Extract**: Use rule_extract() — keyword/phrase extraction + preset match
3. **Validate**: Always pass with moderate confidence (no LLM debate needed)
4. **Insert**: Store with teach-mode confidence floor

Design:
- TeachEngine manages teach-mode cycling across available presets
- Each tick produces one knowledge entry from the preset library
- All content passes the validate pipeline's quality checks
- Confidence is capped at 0.6 (lower than LLM-sourced, higher than nothing)

Integration:
  Called from Learner._teach_one_cycle() when llm_status == "degraded"
  AND search is mock/unavailable.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Callable, Dict, List, Optional

from .presets import produce_knowledge_from_preset, get_presets, get_preset_titles

# ── Confidence constants ──
TEACH_CONFIDENCE_CAP = 0.6
TEACH_MIN_CONFIDENCE = 0.4


# ── Rule-based extract (no LLM, no search) ──


KEYWORD_TAG_MAP: Dict[str, List[str]] = {
    "python": ["python", "programming"],
    "fastapi": ["python", "web", "api"],
    "docker": ["container", "devops"],
    "postgresql": ["database", "sql"],
    "redis": ["cache", "database"],
    "sql": ["database", "sql"],
    "async": ["concurrency", "async"],
    "api": ["web", "api", "integration"],
    "test": ["testing", "quality"],
    "deploy": ["devops", "deployment"],
    "security": ["security", "auth"],
    "auth": ["security", "auth"],
    "performance": ["performance", "optimization"],
    "algorithm": ["cs", "algorithms"],
    "javascript": ["javascript", "frontend"],
    "react": ["javascript", "frontend", "react"],
    "node": ["javascript", "backend"],
    "git": ["devops", "vcs"],
    "linux": ["os", "devops"],
    "aws": ["cloud", "aws"],
    "docker": ["container", "devops"],
    "kubernetes": ["container", "k8s", "devops"],
    "grpc": ["api", "rpc"],
    "graphql": ["api", "graphql"],
    "oop": ["programming", "design"],
    "design pattern": ["programming", "design"],
}


def rule_extract_tags(topic: str, task: str) -> List[str]:
    """Extract tags from topic + task using keyword map.

    Returns at least [topic] + matched tags, never empty.
    """
    tl = topic.lower()
    combined = f"{tl} {task.lower()}"
    tags = set()
    tags.add("teach_mode")
    # Add the topic itself
    tags.add(topic.lower().replace(" ", "_"))
    # Match against keyword map
    for kw, mapped in KEYWORD_TAG_MAP.items():
        if kw in combined:
            tags.update(mapped)
    # Fallback: extract any noun-like words
    if len(tags) <= 1:
        words = re.findall(r'[a-z]{4,}', combined)
        tags.update(words[:3])
    return sorted(tags)


def rule_content_score(content: str) -> float:
    """Score content quality via pure rules (no LLM).

    Returns 0.0-1.0 score based on:
    - Length (bonus for >300 chars)
    - Technical keyword density
    - Code-like syntax presence
    - Structure (bullets, sections)
    """
    if not content or len(content) < 100:
        return TEACH_MIN_CONFIDENCE

    score = TEACH_MIN_CONFIDENCE
    signals = 0

    # Length bonus
    if len(content) > 300:
        score += 0.1
        signals += 1
    if len(content) > 600:
        score += 0.05
        signals += 1

    # Technical keywords
    tech = {"api", "async", "cache", "config", "database", "deploy",
            "docker", "error", "function", "import", "index", "log",
            "model", "pipeline", "query", "schema", "server", "session",
            "thread", "token", "type"}
    found = sum(1 for t in tech if t in content.lower())
    if found >= 3:
        score += 0.1
        signals += 1
    if found >= 6:
        score += 0.05
        signals += 1

    # Code-like syntax
    if "()" in content or "->" in content or "=>" in content:
        score += 0.05
        signals += 1
    if "(" in content and ")" in content and "{" in content:
        score += 0.05
        signals += 1

    # Structure
    if ":" in content or content.count("\n") > 5:
        score += 0.05
        signals += 1

    # Cap at TEACH_CONFIDENCE_CAP
    return min(score, TEACH_CONFIDENCE_CAP)


# ── Teach engine ──


class TeachEngine:
    """Pure rule-based learning engine for when LLM + real search are unavailable.

    Cycles through preset knowledge blocks matched to the topic.
    Produces structured content that passes all validate pipeline quality gates.
    """

    def __init__(self):
        self._cycle_counters: Dict[str, int] = {}

    def can_teach(self, topic: str) -> bool:
        """Check if teach mode has presets for this topic."""
        blocks = get_presets(topic)
        return len(blocks) > 0

    def learn_one_cycle(self, topic: str, task: str, log_func: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
        """Run one teach-mode learning cycle for a given topic.

        Returns a knowledge entry dict ready for storage, or None if exhausted.

        The returned dict matches what KnowledgeBase.store_pending expects:
            {"title": str, "content": str, "summary": str,
             "source": "teach", "tags": [...], "confidence": float,
             "depth": int, "validated": bool}
        """
        log = log_func or (lambda msg: None)
        cycle_idx = self._cycle_counters.get(topic, 0)

        # Try to produce from presets
        result = produce_knowledge_from_preset(topic, task, cycle_index=cycle_idx)
        if not result:
            log(f"  📕 [{topic}] Teach: no matching presets, skipping")
            return None

        # Score and cap confidence
        confidence = rule_content_score(result["content"])

        # Build entry
        summary = result["content"][:120].strip() + "..."
        entry = {
            "title": result["title"],
            "content": result["content"],
            "summary": summary,
            "source": "teach",
            "tags": rule_extract_tags(topic, task),
            "confidence": confidence,
            "depth": result.get("depth", 2),
            "validated": (confidence >= 0.5),  # validated if confidence is at least medium
        }

        # Advance cycle counter
        self._cycle_counters[topic] = cycle_idx + 1

        log(f"  📗 [{topic}] Teach produced: {result['title'][:50]} (conf={confidence:.2f})")
        return entry

    def reset_topic(self, topic: str):
        """Reset cycle counter for a topic (e.g. when direction restarts)."""
        self._cycle_counters.pop(topic, None)
