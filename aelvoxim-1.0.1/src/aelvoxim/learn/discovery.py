"""aelvoxim.learn.discovery — Direction discovery + auto_add

Split from learner.py (1969-line monolith).
Responsibility: discover new directions from KB, auto-discover from knowledge base + fallback search.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Set


def try_discover_new_directions(
    directions: dict,
    last_discovery: float,
    log_func,
    discover_directions_fn,
    add_direction_fn,
) -> float:
    """Try to discover new learning directions from external sources.

    New directions are added in 'paused' status — not automatically learned.
    Returns updated last_discovery timestamp.
    """
    now = time.time()
    if now - last_discovery < 300:
        return last_discovery
    last_discovery = now
    try:
        from .knowledge import KnowledgeBase
        existing = set(directions.keys())
        all_entries = list(KnowledgeBase.get_all_active())
        candidates = discover_directions_fn(existing, all_entries, max_suggestions=5)
        added = 0
        for topic in candidates:
            canonical = topic.strip()
            if canonical and add_direction_fn(canonical):
                directions[canonical].status = "paused"
                added += 1
                log_func(f"  🔍 Discovered new direction (paused): {canonical}")
        if added > 0:
            log_func(f"  🔍 Discovery: added {added} new direction(s) (paused)")
    except Exception as e:
        log_func(f"  ⚠️ Direction discovery error: {e}")
    return last_discovery


def auto_add_direction(
    directions: dict,
    last_auto_discover: float,
    log_func,
    add_direction_fn,
    knowledge_get_all_active_fn,
    suggest_directions_fn,
    search_fn,
) -> tuple[bool, float]:
    """Auto-discover new directions from knowledge base.

    Returns (found_any, updated_last_auto_discover).
    """
    now = time.time()
    if now - last_auto_discover < 180:
        return False, last_auto_discover
    last_auto_discover = now
    try:
        existing = set(directions.keys())
        all_entries = knowledge_get_all_active_fn()
        suggestions = suggest_directions_fn(existing, list(all_entries))
        for s in suggestions:
            if add_direction_fn(s):
                log_func(f"🔍 [Auto-discover] Added: {s}")
                return True, last_auto_discover
        # Fallback: search-based (English-only to avoid Chinese noise)
        results = search_fn("AI agent development system architecture 2025 tutorials", max_results=5)
        if results:
            import re
            for r in results:
                title = r.get("title", "") or ""
                snippet = r.get("snippet", "") or ""
                combined = title + " " + snippet
                for t in re.findall(r'[\u4e00-\u9fff]{4,16}', combined):
                    if t not in existing and len(t) >= 5:
                        stopwords = {"的", "了", "是", "在", "与", "和", "或", "有", "对", "以", "被", "从", "为", "由", "于", "向", "要", "能", "会", "这", "那", "它", "并", "也", "还", "但", "可", "帮", "助", "用", "户"}
                        if sum(1 for c in t if c in stopwords) >= 3:
                            continue
                        if any(c in t for c in "．。，,；;！!？?"):
                            continue
                        if add_direction_fn(t):
                            log_func(f"🔍 [Auto-discover] Added: {t}")
                            return True, last_auto_discover
    except Exception as e:
        log_func(f"⚠️ Auto-discover error: {e}")
    return False, last_auto_discover
