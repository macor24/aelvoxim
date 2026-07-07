"""
metacore.learn.unknown_discovery — Passive unknown term discovery.

Scans recent learner outputs for terms not yet in the entity graph,
and injects promising candidates into the curiosity engine's queue.

Runs inside _cognition_tick — background only, no user interaction.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Dict, List, Set

from .knowledge import KnowledgeBase

# ── English / Chinese stop lists ──

_EN_STOP: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "from",
    "up", "down", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "as", "until", "while",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "it", "its", "i", "you", "he", "she", "they", "we", "me", "him",
    "her", "them", "us", "my", "your", "his", "its", "our", "their",
    "tell", "told", "ask", "asked", "help", "need", "want", "know",
    "make", "made", "take", "took", "give", "gave", "find", "found",
    "show", "use", "used", "using", "get", "got", "let", "set", "put",
    "say", "said", "see", "look", "call", "come", "go", "went", "run",
    "try", "work", "think", "like", "tell me", "let me", "based", "also",
    "well", "much", "many", "new", "one", "two", "first", "last",
}

_CN_STOP: Set[str] = {
    "的", "了", "是", "在", "与", "和", "或", "有", "对", "以",
    "被", "从", "为", "由", "于", "向", "要", "能", "会", "这",
    "那", "它", "并", "也", "还", "但", "可", "帮", "助", "用",
    "户", "我", "你", "他", "她", "们", "都", "就", "而", "且",
    "其", "中", "上", "下", "大", "小", "多", "少", "没", "很",
    "最", "不", "好", "让", "给", "把", "将", "做", "成", "能",
    "该", "这", "哪", "什", "么", "怎", "样", "已", "经",
}

# ── Constants ──

_MAX_PENDING = 20
_SCAN_INTERVAL = 300  # seconds between scans

# ── Module-level state ──

_pending_unknowns: List[str] = []
_last_scan_ts: float = 0.0


# ══════════════════════════════════════════════
# Candidate extraction
# ══════════════════════════════════════════════


def _extract(text: str) -> List[str]:
    """Pull candidate noun phrases / CJK chunks from text."""
    if not text or len(text) < 8:
        return []

    candidates: List[str] = []
    seen: Set[str] = set()
    clean = text.strip()

    def _add(t: str) -> None:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            candidates.append(t)

    # 2-4 word Title Case phrases  e.g. "Quantum Computing"
    for m in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b', clean):
        phrase = m.group()
        if len(phrase) >= 6 and phrase.lower() not in _EN_STOP:
            _add(phrase)

    # 4-20 char lowercase words (not stopwords)
    for m in re.finditer(r'\b[a-zA-Z]{4,20}\b', clean):
        w = m.group()
        if w.lower() not in _EN_STOP:
            _add(w)

    # 4-12 CJK chunks
    for m in re.finditer(r'[\u4e00-\u9fff]{4,12}', clean):
        chunk = m.group()
        stop_ratio = sum(1 for c in chunk if c in _CN_STOP) / max(len(chunk), 1)
        if stop_ratio < 0.5:
            _add(chunk)

    return candidates


def _score(term: str) -> float:
    """Priority score 0.0-1.0 — higher = more likely worth learning."""
    score = 0.5
    if len(term) >= 8:
        score += 0.15
    if len(term) >= 12:
        score += 0.1
    if re.match(r'^[A-Z]', term):                       # Title Case → named concept
        score += 0.15
    if re.search(r'[\u4e00-\u9fff]', term) and re.search(r'[a-zA-Z]', term):  # mixed script
        score += 0.1
    if re.search(r'\d', term):                           # version / protocol
        score += 0.05
    return min(1.0, score)


# ══════════════════════════════════════════════
# Known-state check
# ══════════════════════════════════════════════


def _is_known(term: str) -> bool:
    """Check memory entities + knowledge base for the term."""
    from aelvoxim.memory import search_entities  # lazy to avoid circular import
    if search_entities(term, limit=1):
        return True
    try:
        for entry in KnowledgeBase.get_all_active():
            title = entry.get("title") or entry.get("topic") or ""
            if title and term.lower() in title.lower():
                return True
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════


def scan_unknowns(directions: Dict[str, object], log_func: Callable) -> bool:
    """Scan knowledge base + direction topics for unknown concepts.

    Called from _cognition_tick.  Rate-limited to once per _SCAN_INTERVAL.
    Returns True if at least one candidate was queued.
    """
    global _pending_unknowns, _last_scan_ts

    now = time.time()
    if now - _last_scan_ts < _SCAN_INTERVAL:
        return False
    _last_scan_ts = now

    # 1. Collect source text
    sources: List[str] = []
    try:
        for entry in list(KnowledgeBase.get_all_active())[:50]:
            t = entry.get("title") or entry.get("topic") or ""
            c = entry.get("content") or entry.get("summary") or ""
            sources.append(f"{t} {c}")
    except Exception:
        pass
    sources.extend(directions.keys())

    # 2. Extract candidates in one pass
    candidates: List[str] = []
    for s in sources:
        candidates.extend(_extract(s))
    if not candidates:
        return False

    # 3. Score, filter, deduplicate against known + pending
    fresh: list = []
    seen: Set[str] = set()
    pending_lower = {t.lower() for t in _pending_unknowns}

    for term in candidates:
        low = term.lower()
        if low in seen or low in pending_lower:
            continue
        seen.add(low)
        if _is_known(term):
            continue
        s = _score(term)
        if s >= 0.5:
            fresh.append((term, s))

    if not fresh:
        return False

    # 4. Take top 3 by score
    fresh.sort(key=lambda x: x[1], reverse=True)
    top = [t for t, _ in fresh[:3]]

    _pending_unknowns.extend(top)
    if len(_pending_unknowns) > _MAX_PENDING:
        _pending_unknowns = _pending_unknowns[-_MAX_PENDING:]

    for term in top:
        log_func(f"  🔍 [UnknownDiscovery] queued: {term}")

    return True


def pop_unknown_candidates(max_count: int = 1) -> List[str]:
    """Pop highest-scored pending candidates for learning.

    Called by curiosity.activate_curiosity during idle cycles.
    """
    global _pending_unknowns
    if not _pending_unknowns:
        return []
    result = _pending_unknowns[:max_count]
    _pending_unknowns = _pending_unknowns[max_count:]
    return result


def pending_unknown_count() -> int:
    return len(_pending_unknowns)


__all__ = [
    "scan_unknowns",
    "pop_unknown_candidates",
    "pending_unknown_count",
]
