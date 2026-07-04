# SPDX-License-Identifier: MIT
"""
metacore.memory.semantic — Lightweight semantic enhancement for memory.

Rule-based (no LLM cost):
1. Keyword extraction from entity values for summary
2. Chinese query expansion (2-gram)
3. Semantic relevance scoring by keyword overlap + importance
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .entry import MemoryEntry, LAYER_SEMANTIC, LAYER_EPISODIC, LAYER_WORKING


# ── Chinese char utils ──

_CHINESE = re.compile(r"[\u4e00-\u9fff]")


def _is_chinese(text: str) -> bool:
    return bool(_CHINESE.search(text))


def _extract_keywords(text: str) -> List[str]:
    """Extract keywords from text using simple heuristics.

    - Chinese: 2-char and 3-char substrings (covers most 2/3-char words)
    - English: capitalized words, words longer than 3 chars
    """
    if not text:
        return []
    keywords: Set[str] = set()
    # Chinese chars: extract 2-gram and 3-gram
    cn_chars = _CHINESE.findall(text)
    if len(cn_chars) >= 2:
        for i in range(len(cn_chars) - 1):
            keywords.add("".join(cn_chars[i:i+2]))
        if len(cn_chars) >= 3:
            for i in range(len(cn_chars) - 2):
                keywords.add("".join(cn_chars[i:i+3]))
    # English: extract capitalized or long words
    for w in re.findall(r"[A-Za-z][a-zA-Z0-9_-]+", text):
        if len(w) >= 3:
            keywords.add(w.lower())
    return sorted(keywords)


def _expand_query(query: str) -> List[str]:
    """Expand a Chinese query with common variants.

    Example:
        "名字" → ["名字", "名字叫什么", "叫什么名字", ...]
    """
    if not query:
        return [query]
    expanded = {query}
    # Chinese 2-gram expansion
    cn_chars = _CHINESE.findall(query)
    if len(cn_chars) >= 2:
        for i in range(len(cn_chars) - 1):
            expanded.add("".join(cn_chars[i:i+2]))
    # English lowercase variant
    expanded.add(query.lower())
    # Prefix/suffix patterns
    for prefix in ["i am ", "my name is ", "我叫", "我是"]:
        if query.lower().startswith(prefix):
            expanded.add(query[len(prefix):])
    return list(expanded)


def semantic_score(entry: MemoryEntry, query: str) -> float:
    """Score an entry's semantic relevance to a query (0-1)."""
    if not query:
        return entry.importance
    q_lower = query.lower()
    key_lower = entry.key.lower()
    val_text = str(entry.value).lower()

    # Exact match boost
    if q_lower == key_lower or q_lower == val_text:
        return min(1.0, entry.importance + 0.3)
    # Substring match
    if q_lower in key_lower or q_lower in val_text:
        return min(1.0, entry.importance + 0.2)

    # Keyword overlap
    entry_kw = _extract_keywords(val_text + " " + key_lower)
    query_kw = _extract_keywords(q_lower)
    if query_kw and entry_kw:
        overlap = len(set(query_kw) & set(entry_kw))
        max_possible = max(len(query_kw), 1)
        score = overlap / max_possible
        return entry.importance * 0.5 + score * 0.5

    # Chinese char coveage
    q_chars = set(c for c in q_lower if '\u4e00' <= c <= '\u9fff')
    if q_chars:
        e_chars = set(c for c in key_lower + val_text if '\u4e00' <= c <= '\u9fff')
        coverage = len(q_chars & e_chars) / max(len(q_chars), 1)
        if coverage >= 0.2:
            return entry.importance * 0.6 + coverage * 0.4

    return entry.importance * 0.3  # low relevance


def semantic_search(entries: List[MemoryEntry], query: str, limit: int = 20) -> List[MemoryEntry]:
    """Rank entries by semantic relevance score."""
    scored = [(semantic_score(e, query), e) for e in entries]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


def get_summary(entry: MemoryEntry) -> str:
    """Generate a text summary for an entry (no LLM)."""
    value = str(entry.value)[:200]
    kw = _extract_keywords(value)
    kw_str = ", ".join(kw[:5]) if kw else ""
    parts = []
    if value:
        parts.append(value[:80])
    if kw_str:
        parts.append(f"[{kw_str}]")
    if entry.tags:
        parts.append(f"| {', '.join(entry.tags[:3])}")
    return " ".join(parts) if parts else value[:80]
