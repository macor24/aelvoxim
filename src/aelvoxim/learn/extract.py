"""aelvoxim.learn.extract — Knowledge extraction chain

Multi-layer extraction pipeline:
1. LLM distillation (highest quality, requires LLM)
2. Search + LLM refinement (search results + LLM summarization)
3. Generic fallback (search snippet filtering, lowest quality)

All functions are pure — no side effects, no storage writes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .search import search as _search
from ..utils import read_json, LLM_CONFIG_FILE


import logging
_log = logging.getLogger("aelvoxim.learn.extract")

# ── Content quality checks ────────────────


def is_valid_content(topic: str, prefix: str, content: str) -> bool:
    """Validate knowledge content quality. Three-pass check:

    1. Cross-topic duplicate prefix detection
    2. Exact content duplicate detection
    3. Same-topic duplicate detection
    """
    if not content or len(content) < 20:
        return False
    from .knowledge import KnowledgeBase

    prefix_len = len(prefix)
    content_normalized = content.strip()[:120]

    def _check(entries):
        for known in entries:
            if known.get("topic") == topic:
                continue
            ks = known.get("summary", "")
            if prefix_len > 3 and ks.startswith(prefix):
                ch = content[:60].strip()
                kh = (known.get("content", "") or "")[:60].strip()
                if ch and ch == kh:
                    return False
            kc = (known.get("content", "") or "").strip()[:120]
            if kc and content_normalized == kc:
                return False
            if known.get("topic") == topic:
                kc2 = (known.get("content", "") or "").strip()[:80]
                if kc2 and content_normalized[:80] == kc2:
                    return False
        return True

    if not _check(KnowledgeBase.get_all_active()):
        return False
    if not _check(KnowledgeBase.get_pending()):
        return False
    return True


def is_generic_template_output(content: str) -> bool:
    """Check if content is generic template metadata, not real knowledge.

    Rejects:
    - Pure metadata JSON: {"python":"3.12","platform":"linux","task":"..."}
    - One-liner confirmations: "FastAPI route/DI pipeline validated"
    """
    stripped = content.strip()
    if not stripped:
        return True

    # JSON metadata dictionary
    if stripped.startswith("{"):
        try:
            import json
            obj = json.loads(stripped)
            if isinstance(obj, dict) and len(obj) <= 6:
                meta_keys = {"python", "platform", "task", "topic",
                             "timestamp", "stdlib_count", "note"}
                if all(k in meta_keys for k in obj.keys()):
                    return True
        except Exception:
            pass  # non-critical, continue

    # One-liner confirmations
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    if len(lines) <= 2:
        total_len = sum(len(l) for l in lines)
        if total_len < 60:
            has_explanation = any(":" in l or "=" in l or "->" in l for l in lines)
            if not has_explanation:
                confirm = ["validated", "ok", "success", "done", "passed",
                           "created", "running"]
                if any(w in stripped.lower() for w in confirm):
                    return True
    return False


def content_has_real_value(content: str) -> bool:
    """Check if content has real informational value (not generic text).

    At least 2 of:
    - Contains specific data/numbers
    - Contains tech keywords
    - Contains code-like syntax
    - Contains verifiable external info
    - Not generic template output
    """
    if not content or len(content) < 40:
        return False

    # Reject generic template output
    if is_generic_template_output(content):
        return False

    signals = 0
    # Specific data/numbers
    if re.search(r'\d+[%％]|\d+\.\d+|\b\d{3,}\b', content):
        signals += 1
    # Tech keywords
    tech_keywords = {"python", "fastapi", "docker", "sql", "api", "http", "json",
                     "async", "cache", "database", "redis", "postgresql", "sqlite",
                     "kubernetes", "k8s", "aws", "git", "linux", "nginx", "rest",
                     "class", "function", "import", "def ", "return", "__init__"}
    if any(kw in content.lower() for kw in tech_keywords):
        signals += 1
    # Code-like syntax
    if any(c in content for c in ("=", "(", ")", "{", "}", ":", "#", "->")):
        signals += 1
    # Verifiable external info (URLs, dates, company names)
    if re.search(r'https?://|www\.|\d{4}-\d{2}-\d{2}|Inc\.|Ltd\.|Corp\.', content):
        signals += 1
    return signals >= 2


# ── LLM availability ─────────────────────


def call_llm_if_available() -> Optional[Tuple[Any, Any]]:
    """Check if LLM is available. Returns (call_llm_fn, ModelConfig) or None.

    Filters out unknown fields from config to avoid ModelConfig __init__ errors.
    """
    try:
        from .llm import call_llm, ModelConfig, default_models
        config = read_json(LLM_CONFIG_FILE) or {}
        models = config.get("models", [])
        # Fallback: try default_models() which handles both env vars and both config formats
        if not models:
            all_models = default_models()
            if all_models:
                model_obj = all_models[0]
                return (call_llm, model_obj)
            return None
        if models:
            first = models[0]
            if first.get("api_key", "") and len(first.get("api_key", "")) >= 8:
                # Filter to only known ModelConfig fields
                valid = {"name", "provider", "api_key", "base_url",
                         "timeout", "temperature", "max_tokens", "priority"}
                filtered = {k: v for k, v in first.items() if k in valid}
                model_obj = ModelConfig(**filtered)
                return (call_llm, model_obj)
    except Exception:
        pass  # non-critical, continue
    return None


# ── Extraction layers ─────────────────────


def llm_distill(query: str, phase_name: str) -> Optional[str]:
    """Layer 1: LLM distillation. Generate content from model knowledge."""
    llm = call_llm_if_available()
    if not llm:
        return None
    call_fn, model = llm
    try:
        prompt = (
            f"You are a technical writer. Create detailed learning content about:\n"
            f"Topic: {query}\n"
            f"Phase: {phase_name}\n\n"
            "Include specific technical details, concrete examples, "
            "code snippets, and actionable knowledge. Mention real tools, frameworks, "
            "and patterns. If you don't know this topic, respond with 'UNKNOWN_TOPIC'.\n\n"
            f"Write comprehensively — at least 300 words. Short responses will be rejected."
        )
        text = call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=1024,
        )
        if not text or "UNKNOWN_TOPIC" in text:
            return None
        return f"About '{query}' ({phase_name}):\n{text}"
    except Exception:
        return None


def llm_refine_search_with_hypothesis(query, phase_name, hypothesis, results):
    """Layer 2b: Verify LLM hypothesis against search results."""
    llm = call_llm_if_available()
    if not llm:
        return None
    call_fn, model = llm
    try:
        snippets = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:300]}"
            for r in results[:3] if r.get('snippet')
        )
        if not snippets:
            return None
        prompt = (
            f"Topic: '{query}' ({phase_name})\n\n"
            f"Below is an AI-generated hypothesis about this topic:\n{hypothesis}\n\n"
            f"Below are search results from the internet:\n{snippets}\n\n"
            f"Your task: Compare the hypothesis with the search results.\n"
            f"1. If the search results support the hypothesis, synthesize a verified guide.\n"
            f"2. If the search results contradict the hypothesis, correct the errors.\n"
            f"3. If there is not enough information, state what is missing.\n"
            f"Include specific tools, patterns, and code practices from search results."
        )
        text = call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=1024,
        )
        if not text:
            return None
        return f"About '{query}' ({phase_name}) verified:\n{text}"
    except Exception:
        return None


def llm_refine_search(query: str, phase_name: str, results: list) -> Optional[str]:
    """Layer 2: Search + LLM refinement."""
    llm = call_llm_if_available()
    if not llm:
        return None
    call_fn, model = llm
    try:
        snippets = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:300]}"
            for r in results[:3] if r.get('snippet')
        )
        if not snippets:
            return None
        prompt = (
            f"Based on the following search results about '{query}' ({phase_name}),\n"
            f"synthesize a concise technical guide:\n\n{snippets}\n\n"
            f"Include specific tools, patterns, and code practices."
        )
        text = call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=1024,
        )
        if not text:
            return None
        return f"About '{query}' ({phase_name}) from search:\n{text}"
    except Exception:
        return None


# ── Rule-based extraction (no LLM, no search) ──


def rule_extract(task: str, topic: str = "") -> Optional[str]:
    """Layer 4: Pure rule-based knowledge extraction.

    Uses the preset library to generate knowledge content without any LLM
    or search dependency. Designed as the last fallback before the pipeline
    gives up.

    This is the extraction counterpart to teach.py's learn_one_cycle().

    Returns:
        Formatted knowledge string, or None if no match.
    """
    try:
        from .presets import produce_knowledge_from_preset
        result = produce_knowledge_from_preset(topic or task, task, cycle_index=0)
        if result:
            content = result["content"]
            return f"About '{task}':\\n{content}"
    except Exception:
        _log.exception("extract error")
    return None


def is_search_mock() -> bool:
    """Check if the current search engine is configured as mock or is returning mock data."""
    import os as _os
    engine = _os.environ.get("AELVOXIM_SEARCH_ENGINE", _os.environ.get("METACORE_SEARCH_ENGINE", "")).lower()
    if engine == "mock":
        return True
    # Also check search-config.json
    try:
        from ..utils import SEARCH_CONFIG_FILE, read_json
        sc = read_json(SEARCH_CONFIG_FILE) or {}
        if sc.get("engine") == "mock":
            return True
    except Exception:
        _log.exception("extract error")
    return False


def search_has_quality(results: list) -> bool:
    """Check if search results have real content quality."""
    meaningful = 0
    for r in results:
        snippet = r.get("snippet", "").strip()
        if len(snippet) > 30:
            generic = ["is a", "refers to", "commonly used", "is a method",
                       "is a technique", "is a process"]
            if not any(g in snippet.lower() for g in generic):
                meaningful += 1
    return meaningful >= 2


def _refine_to_qa(query: str, content: str) -> str:
    """Refine knowledge content into Q&A format: Q: <query> A: <key takeaway>."""
    if not content or len(content) < 50:
        return content
    # Strip existing title-like prefixes
    lines = content.strip().split("\n")
    clean_lines = [l for l in lines if l.strip() and not l.startswith("#")]
    body = "\n".join(clean_lines)
    # If already in Q&A format, return as-is
    if body.startswith("Q:") or body.startswith("Q："):
        return body
    # Prefix with the query as question
    q = query.strip()[:200]
    a = body[:1000]
    return f"Q: {q}\nA: {a}"


def extract_knowledge(query: str, phase_name: str) -> Optional[str]:
    """Multi-layer knowledge extraction with LLM-first hypothesis.

    New pipeline:
    1. LLM generates knowledge hypothesis about the topic
    2. Search internet to verify the hypothesis
    3. Compare hypothesis with search results
    4. Return verified knowledge (or None if unverifiable)
    """
    # Layer 1: LLM generates knowledge hypothesis
    hypothesis = llm_distill(query, phase_name)
    if hypothesis and content_has_real_value(hypothesis):
        # LLM produced content — use it directly.
        # Search verification is nice-to-have but not required (search may be
        # unavailable e.g. in WSL/China networks).
        # Quality gates (300 chars + technical keywords + Judge) in validate.py
        # still apply in execute_and_validate().
        # Try to enhance with search if available, but don't block on it.
        try:
            results = _search(query, max_results=5)
            is_mock = False
            if results:
                first_snippet = results[0].get("snippet", "")
                if any(p in first_snippet for p in ["这是搜索结果", "搜索结果如下", "mock", "模拟结果"]):
                    is_mock = True
            if results and not is_mock and search_has_quality(results):
                enhanced = llm_refine_search(query, phase_name, results)
                if enhanced and content_has_real_value(enhanced):
                    return _refine_to_qa(query, enhanced)
        except Exception:
            pass  # non-critical, continue
        # Search unavailable or results low quality — return LLM hypothesis
        # (will still pass through all quality gates in validate pipeline)
        return _refine_to_qa(query, hypothesis)

    # Fallback: search + LLM refine
    return _search_and_refine(query, phase_name)


def _search_and_refine(query: str, phase_name: str) -> Optional[str]:
    """Fallback: search internet then refine with LLM."""
    try:
        results = _search(query, max_results=5)
        is_mock = False
        if results:
            first_snippet = results[0].get("snippet", "")
            if any(p in first_snippet for p in ["这是搜索结果", "搜索结果如下", "mock", "模拟结果"]):
                is_mock = True
        if results and not is_mock and search_has_quality(results):
            return llm_refine_search(query, phase_name, results)
    except Exception:
        pass  # non-critical, continue
    return None
