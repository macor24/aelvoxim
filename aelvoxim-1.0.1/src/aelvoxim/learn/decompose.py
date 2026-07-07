"""aelvoxim.learn.decompose — Direction decomposition

Topic decomposition strategies:
1. Keyword preset matching (TASK_DECOMPOSE_PRESETS)
2. Search-based sub-topic extraction (language-aware, 5 branches)
3. Generic category fallback (TASK_DECOMPOSE_CATEGORIES)
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .search import search as _search


# ── Generic category fallback ─────────────

TASK_DECOMPOSE_CATEGORIES = [
    "Core Concepts",
    "Main Tools and Frameworks",
    "Implementation Steps",
    "Common Issues and Solutions",
    "Best Practices",
    "Performance Optimization",
]

# ── Preset decomposition templates ────────
# Direction keyword → specific sub-tasks (matched in order)

TASK_DECOMPOSE_PRESETS: Dict[str, List[str]] = {
    "FastAPI": [
        "Route and dependency injection design", "Pydantic models and data validation",
        "Async request processing optimization", "Database session management",
        "Middleware and CORS configuration", "API documentation generation",
        "Authentication and authorization", "Performance benchmarking and tuning",
    ],
    "performance": [
        "Benchmarking and bottleneck analysis", "Caching strategies and implementation",
        "Async and concurrency optimization", "Database query optimization",
        "Connection pooling and resource management", "Monitoring and alerting",
        "Memory and GC tuning", "Configuration parameter tuning",
    ],
    "Python": [
        "Core syntax and advanced features", "Standard library modules",
        "Async programming (asyncio)", "Testing frameworks (pytest)",
        "Package management and distribution", "Performance profiling and optimization",
        "Type annotations and static checking", "Cython and extension development",
    ],
    "Docker": [
        "Dockerfile optimization", "Multi-stage builds",
        "Docker Compose orchestration", "Network and storage configuration",
        "Logging and monitoring", "Security best practices",
        "CI/CD integration", "Image size optimization",
    ],
    "DevOps": [
        "CI/CD pipeline design", "Infrastructure as code",
        "Configuration and secret management", "Log aggregation and monitoring",
        "Container orchestration (K8s)", "Blue-green deployment and canary",
        "Disaster recovery and rollback", "Security scanning and compliance",
    ],
    "database": [
        "Data modeling and normalization", "Index optimization and query analysis",
        "Transactions and concurrency control", "Backup, restore and migration",
        "Sharding strategies", "Read-write separation",
        "Connection pooling and tuning", "Monitoring and slow query analysis",
    ],
    "frontend": [
        "Component-based development", "State management patterns",
        "Routing and navigation", "HTTP requests and caching",
        "UI framework selection", "Performance optimization and lazy loading",
        "Responsive design", "Build tool configuration",
    ],
    "security": [
        "Authentication and authorization", "Input validation and injection prevention",
        "XSS/CSRF protection", "HTTPS and certificate management",
        "Key management and encryption", "Security auditing and logging",
        "Vulnerability scanning", "Compliance and privacy",
    ],
    "network": [
        "TCP/IP protocol stack", "HTTP/HTTPS protocol",
        "DNS resolution and optimization", "Load balancing strategies",
        "CDN acceleration", "Firewall and security groups",
        "Network performance monitoring", "VPN and tunneling",
    ],
    "machine learning": [
        "Data preprocessing and feature engineering", "Model selection and evaluation",
        "Training pipeline construction", "Hyperparameter tuning",
        "Model deployment and serving", "A/B testing",
        "Explainability analysis", "Continuous learning",
    ],
}


# ── Language detection ────────────────────


def detect_lang(text: str) -> str:
    """Detect text language family for search strategy.

    Returns: 'en', 'zh', 'ja', 'kr', 'other'
    """
    if not text:
        return 'other'
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309f')
    katakana = sum(1 for c in text if '\u30a0' <= c <= '\u30ff')
    hangul = sum(1 for c in text if '\uac00' <= c <= '\ud7af')
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    if hiragana + katakana > 0:
        return 'ja'
    if hangul > 0:
        return 'kr'
    if cjk > 0:
        return 'zh'
    if ascii_alpha > max(len(text), 1) * 0.5:
        return 'en'
    return 'other'


# ── Decomposition ─────────────────────────


def _decompose_with_llm(topic: str, log_func) -> Optional[List[str]]:
    """Use LLM to generate precise sub-task titles for a topic.

    Returns list of 4-6 focused sub-task strings, or None if LLM unavailable.
    """
    log = log_func or (lambda msg: None)
    try:
        from .extract import call_llm_if_available
        llm = call_llm_if_available()
        if not llm:
            return None
        call_fn, model = llm
        prompt = (
            "You are a curriculum designer. Given a learning topic, decompose it into "
            "4-6 specific, actionable sub-topics that a developer could search for and "
            "study independently.\n\n"
            "RULES:\n"
            "- Each sub-topic must be a concrete noun phrase (e.g. 'Async programming with asyncio', "
            "'Docker multi-stage builds', 'Database connection pooling')\n"
            "- NO single words like 'testing', 'performance', 'security'\n"
            "- NO vague categories like 'Core Concepts', 'Best Practices'\n"
            "- Each sub-topic must be specific enough to find a dedicated tutorial or guide\n"
            "- Output as a JSON array of strings, nothing else\n\n"
            f"Topic: {topic}"
        )
        text = call_fn(
            model=model,
            user_message=prompt,
            system_prompt="You are a technical task decomposer.",
            max_tokens=300,
            temperature=0.3,
        )
        import json
        tasks = json.loads((text or "").strip())
        if isinstance(tasks, list) and len(tasks) >= 2:
            tasks = [t.strip() for t in tasks if isinstance(t, str) and t.strip()]
            if len(tasks) >= 2:
                log(f"  🧠 [{topic}] LLM decomposed: {len(tasks)} tasks")
                return tasks
    except Exception:
        pass  # non-critical, continue
    return None


def _decompose_by_search(topic: str, log_func) -> List[str]:
    """Decompose by searching and extracting sub-topics from page titles.

    Searches for topic + 'guide'/'tutorial'/'best practices', then extracts
    meaningful sub-topic candidates from search result titles.
    """
    log = log_func or (lambda msg: None)
    tasks: List[str] = []
    try:
        import re
        from .search import search as _search

        # Search with learning-oriented queries
        queries = [
            f"{topic} guide",
            f"{topic} tutorial",
            f"{topic} best practices",
        ]
        seen = set()
        for q in queries:
            results = _search(q, max_results=5)
            for r in results:
                title = (r.get("title", "") or "").strip()
                if not title:
                    continue
                # Clean title: remove site name separators
                for sep in [" | ", " — ", " – ", " - ", " :: ", " » "]:
                    if sep in title:
                        title = title.split(sep)[0]
                # Remove common boilerplate
                title = title.strip()
                if not title or len(title) < 6:
                    continue
                key = title.lower().strip()
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(title)
                if len(tasks) >= 6:
                    break
            if len(tasks) >= 6:
                break

        # Filter tasks that look like actual sub-topics (contain technical context)
        filtered = []
        for t in tasks:
            t_lower = t.lower()
            # Skip titles that are just the topic name repeated
            if len(t_lower) <= len(topic) + 5 and topic.lower() in t_lower:
                continue
            # Prefer titles that contain concrete indicators
            has_indicators = any(kw in t_lower for kw in
                ["with", "using", "for", "in ", "and", "vs ",
                 "guide", "how to", "example", "pattern", "design",
                 "optimization", "management", "configuration",
                 "deployment", "testing", "monitoring", "security",
                 "integration", "migration", "architecture"])
            # Allow titles that look like specific technique names
            has_technique = bool(re.search(r'[A-Z][a-z]+ [A-Z]|[a-z]+-[a-z]+', t))
            if has_indicators or has_technique or len(t) > 20:
                filtered.append(t)
            else:
                filtered.append(t)  # keep anyway as last resort

        tasks = filtered[:6]

        if len(tasks) >= 3:
            log(f"  🔍 [{topic}] From search titles: {len(tasks)} sub-tasks")
            return tasks
    except Exception:
        pass  # non-critical, continue
    return tasks


def decompose_direction(topic: str, log_func=None, direction_meta: Optional[Dict] = None) -> List[str]:
    """Decompose a topic into sub-tasks.

    Uses 4 strategies in order:
    0. LLM-based decomposition (new, best quality)
    1. Keyword preset matching (TASK_DECOMPOSE_PRESETS)
    2. Search-based title extraction (new, improved from old snippet-scraping)
    3. Generic category fallback

    When `direction_meta` is provided (e.g. {"saturation": 0.8, "entries_created": 5}),
    adapts the task count based on saturation level:
    - saturation > 0.8  → reduce tasks (highly saturated, less needed)
    - saturation < 0.3  → increase tasks (needs more coverage)
    - 0.3-0.8           → default count (6 tasks)

    Returns:
        List of sub-task strings.
    """
    log = log_func or (lambda msg: None)

    # Adaptive task limit based on direction metadata
    default_count = 6
    if direction_meta:
        sat = direction_meta.get("saturation", 0.5)
        entries = direction_meta.get("entries_created", 0)
        if sat >= 0.8 or entries >= 5:
            default_count = 3  # Nearly saturated, fewer tasks needed
            log(f"  🎯 [{topic}] Adaptive: saturation={sat:.2f}, reducing tasks to {default_count}")
        elif sat < 0.3 and entries < 2:
            default_count = 8  # Early stage, more exploration needed
            log(f"  🎯 [{topic}] Adaptive: saturation={sat:.2f} (early), increasing tasks to {default_count}")
        else:
            default_count = 6
            log(f"  🎯 [{topic}] Adaptive: saturation={sat:.2f}, default {default_count} tasks")

    # 0. LLM-based decomposition (best quality)
    llm_tasks = _decompose_with_llm(topic, log)
    if llm_tasks:
        return llm_tasks[:default_count]

    # 1. Keyword preset matching
    for keyword, tasks in TASK_DECOMPOSE_PRESETS.items():
        if keyword.lower() in topic.lower():
            log(f"  🔧 [{topic}] Matched preset: {keyword}")
            return tasks[:min(len(tasks), default_count)]

    # 2. Search-based title extraction (improved)
    search_tasks = _decompose_by_search(topic, log)
    if search_tasks:
        return search_tasks[:default_count]

    # 3. Generic category fallback
    log(f"  🔧 [{topic}] Using generic fallback")
    return [f"{topic} - {cat}" for cat in TASK_DECOMPOSE_CATEGORIES[:default_count]]
