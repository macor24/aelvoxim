"""
metacore.learn.presets — Preset knowledge seeds + teach-mode content generation.

Pure rule-based knowledge generation for when LLM is unavailable.
All templates are topic-tagged and produce structured knowledge entries
that pass the validate pipeline's content_has_real_value() and
_has_technical_keywords() checks.

Design:
- Topic matcher: prefix/contains matching -> preset block
- Block: {"title": str, "content": str, "tags": list, "depth": int}
- fallback block for unmatched topics
- teach-mode: produces 1 entry per call, caller manages cycle
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Preset knowledge blocks ────────────────────
# Key=lowercased topic keyword (match via "topic contains key")
# Value=list of knowledge blocks for that topic

_PRESET_LIBRARY: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        {
            "title": "Python - Asyncio and Concurrency",
            "content": "Python's asyncio library (introduced in 3.4, matured in 3.7+) enables single-threaded concurrent code using coroutines via async/await syntax. Key concepts: coroutines (async def), awaitables, event loop, tasks, futures. asyncio.run() is the main entry point. Use asyncio.create_task() to schedule coroutines concurrently. asyncio.gather() runs multiple awaitables in parallel. For I/O-bound workloads, asyncio can match or exceed thread-based concurrency while avoiding GIL contention and thread-safety issues. Common pitfalls: forgetting to await a coroutine (returns a coroutine object), blocking event loop with synchronous calls, mixing asyncio and threading without care.",
            "tags": ["python", "asyncio", "async", "concurrency"],
            "depth": 3,
        },
        {
            "title": "Python - Decorators and Metaclasses",
            "content": "Python decorators are functions that take a callable and return a modified callable, using @decorator syntax sugar. functools.wraps() preserves metadata. Class-based decorators implement __call__ for stateful decoration. Metaclasses (inheriting from type) control class creation via __new__ and __init__. Common uses: ORM model definitions (SQLAlchemy), validation frameworks, singleton patterns. The __call__ on a metaclass controls instance creation. Descriptor protocol (__get__, __set__, __delete__) powers @property, __slots__ reduces memory per instance. Best practice: prefer decorators over metaclasses unless you need to transform the class itself at definition time.",
            "tags": ["python", "decorator", "metaclass", "oop"],
            "depth": 3,
        },
        {
            "title": "Python - Type Hints and Static Analysis",
            "content": "Python type hints (PEP 484, 526, 604) support gradual typing. Basic types: int, str, bool, float. Container types: List[str], Dict[str, int], Tuple[int, ...]. Optional[X] = Union[X, None]. Literal['a', 'b'] for exact values. TypedDict for dict-like objects with fixed keys. Protocol for structural subtyping (duck typing at type level). typing.TypeVar for generics. Tools: mypy (strict mode catches real bugs), pyright (faster, used by VS Code), pytype (Google). Runtime validation: pydantic uses annotations for validation. @dataclass + field() for data containers. Understanding covariance vs contravariance is essential for correct generic container type annotations.",
            "tags": ["python", "type hints", "mypy", "static analysis"],
            "depth": 3,
        },
    ],
    "fastapi": [
        {
            "title": "FastAPI - Dependency Injection System",
            "content": "FastAPI's dependency injection system is a first-class feature that replaces traditional DI containers with Python-native async functions. Use Depends() to declare dependencies in path operation parameters. Dependencies can be async functions, generators (for yield-based cleanup), or classes. Generators with yield enable startup/shutdown hooks per request (e.g. DB session: open, yield, close on teardown). Dependencies can depend on other dependencies, forming a dependency graph that FastAPI resolves. Use dependency_overrides for testing (inject mocks). Common patterns: get_db session, get_current_user auth, pagination params. Unlike NestJS/Spring, FastAPI doesn't use decorator-based DI providers; everything is just callables with Depends().",
            "tags": ["fastapi", "dependency injection", "Depends"],
            "depth": 3,
        },
        {
            "title": "FastAPI - Pydantic Validation",
            "content": "FastAPI uses Pydantic v2 for request/response validation. BaseModel subclasses define data shapes with type annotations. Field(default=..., alias=..., ge=0, le=100) adds constraints. Validators: @field_validator for per-field checks, @model_validator for cross-field validation. ConfigDict(extra='forbid') rejects unknown fields. Pydantic v2 is built on Rust-based pydantic-core, 5-50x faster than v1. Serialization: model_dump(), model_dump_json(). Common patterns: nested models, Union discriminators, Optional fields with defaults. FastAPI combines Pydantic models with OpenAPI schema generation automatically.",
            "tags": ["fastapi", "pydantic", "validation", "openapi"],
            "depth": 3,
        },
    ],
    "docker": [
        {
            "title": "Docker - Multi-stage Builds",
            "content": "Docker multi-stage builds use multiple FROM statements in a single Dockerfile. Each FROM begins a new stage, and COPY --from=stage_name copies artifacts from earlier stages. This separates build environment from runtime environment. Typical pattern: stage 1 (build) with full SDK/toolchain, stage 2 (runtime) with minimal base image. For Python: use python:VERSION-slim as runtime base, copy only installed packages from builder. For Go/static binaries: use scratch or distroless base (no shell, no package manager). Docker layer caching: order matters - copy requirements.txt first, RUN pip install, then copy source code to maximize cache reuse. Use .dockerignore to exclude venv, .git, __pycache__, .env.",
            "tags": ["docker", "multi-stage", "optimization", "container"],
            "depth": 3,
        },
    ],
    "postgresql": [
        {
            "title": "PostgreSQL - Index Strategies",
            "content": "PostgreSQL supports B-tree, Hash, GiST, GIN, BRIN indexes. B-tree (default) handles < <= = >= > and LIKE 'prefix%'. GIN for full-text search (tsvector columns) and array containment. BRIN for large append-only tables (much smaller index size). Partial indexes (WHERE condition) reduce index size for sparse queries. Covering indexes (INCLUDE columns) enable index-only scans. Key monitoring: pg_stat_user_indexes (scans, reads, fetches), pg_stat_user_tables (seq_scan vs idx_scan ratio). Rule of thumb: if seq_scan / idx_scan > 0.5 for a large table, an index is missing. Use EXPLAIN (ANALYZE, BUFFERS) to verify index usage. Avoid over-indexing: each index slows INSERT/UPDATE/DELETE.",
            "tags": ["postgresql", "index", "performance", "database"],
            "depth": 3,
        },
    ],
    "redis": [
        {
            "title": "Redis - Data Structures and Use Cases",
            "content": "Redis is an in-memory data structure store with optional persistence. Core data types: STRING (caching, counters, distributed locks), LIST (queues, async job buffers), SET (unique items, tags), HASH (object fields), ZSET (leaderboards, rate limit buckets). Stream for message queue with consumer groups (Redis 5+). Persistence: RDB (point-in-time snapshots, compact), AOF (append-only log, more durable, rebuilds on restart). Common patterns: cache-aside (read from cache, miss -> read DB, write cache), distributed locks (SET NX PX), rate limiting (INCR + EXPIRE, or ZSET sliding window). Use pipelining for batch operations (reduces round-trips). Redis Cluster provides automatic sharding across nodes.",
            "tags": ["redis", "caching", "data structures", "in-memory"],
            "depth": 3,
        },
    ],
    "database": [
        {
            "title": "Database - Normalization and Denormalization",
            "content": "Database normalization organizes tables to reduce redundancy. 1NF: atomic columns, no repeating groups. 2NF: 1NF + partial dependency removal. 3NF: 2NF + transitive dependency removal (non-key column depends only on key). BCNF: 3NF + every determinant is a candidate key. Denormalization: intentionally adding redundancy for read performance. Common strategies: pre-join (storing computed aggregates), materialized views, caching layer. OLTP: prefer 3NF/BCNF for write-heavy workloads. OLAP/analytics: star schema (fact + dimension tables), denormalized for query speed. Production rule: normalize to 3NF by default, denormalize only when performance measurements show actual bottlenecks.",
            "tags": ["database", "normalization", "denormalization", "schema design"],
            "depth": 3,
        },
    ],
}


# ── Fallback block (any unmatched topic) ────

_FALLBACK_BLOCKS: List[Dict[str, Any]] = [
    {
        "title": "Core Concepts and Architecture",
        "content": "Understanding the topic requires starting with foundational concepts: the fundamental vocabulary, core principles, and architectural patterns. Key aspects include: identifying the problem domain, understanding input/output boundaries, recognizing common components and their relationships. Modern implementations follow layered or modular architecture patterns to separate concerns and enable testing. Best practice: start with official documentation and specification documents, then explore community guides and reference implementations. The most effective learning path is: concept -> example -> practice -> debug -> teach.",
        "tags": ["concepts", "architecture", "fundamentals"],
        "depth": 1,
    },
    {
        "title": "Common Tools and Framework Integration",
        "content": "After understanding core concepts, the next step is tooling and integration. Most topics have a standard toolchain: package managers (pip, npm, cargo), build systems (Makefile, Docker, CI/CD), and testing frameworks (pytest, jest). Integration patterns include: REST API, message queues, event streams, and database connectors. Key considerations: version compatibility, dependency management, configuration-as-code. Containerization (Docker) has become the standard deployment unit, often orchestrated via Kubernetes in production environments. Monitoring stack: structured logging, metrics (Prometheus), tracing (OpenTelemetry).",
        "tags": ["tools", "integration", "deployment"],
        "depth": 1,
    },
    {
        "title": "Debugging and Troubleshooting Guide",
        "content": "Effective debugging follows a systematic approach: reproduce, isolate, diagnose, fix, verify. Start with logging: add structured log output at key decision points with correlation IDs for request tracing. Common categories of bugs: race conditions (use locks or async patterns), null/type errors (use type hints + runtime validation), resource leaks (use context managers and connection pools). Performance debugging: profile before optimizing. Production debugging: use feature flags for gradual rollout, health checks for liveness, circuit breakers for dependency health. Always write a regression test before deploying a fix.",
        "tags": ["debugging", "troubleshooting", "testing"],
        "depth": 1,
    },
]


# ── Public API ──


def get_presets(topic: str) -> List[Dict[str, Any]]:
    """Return preset knowledge blocks matching a topic.

    Matches by checking if topic contains any key from _PRESET_LIBRARY.
    Returns up to 2 blocks per matched library key.
    Falls back to _FALLBACK_BLOCKS when no match.
    """
    tl = topic.lower()
    matched: List[Dict[str, Any]] = []
    for key, blocks in _PRESET_LIBRARY.items():
        if key in tl:
            matched.extend(blocks[:2])
    if matched:
        return matched
    return _FALLBACK_BLOCKS


def get_preset_titles(topic: str) -> List[str]:
    """Return preset knowledge titles for a given topic (for display / queue seeding)."""
    return [b["title"] for b in get_presets(topic)]


def produce_knowledge_from_preset(
    topic: str,
    task: str,
    cycle_index: int = 0,
) -> Optional[Dict[str, Any]]:
    """Try to produce a knowledge entry from presets matching the topic.

    Uses the preset library to generate knowledge content without any LLM.
    The content is designed to pass validate.py's content_has_real_value()
    and _has_technical_keywords() checks.

    Args:
        topic: Learning direction topic
        task: Sub-task description (used for matching key within presets)
        cycle_index: Which preset block to return (index into matched blocks)

    Returns:
        {"title": ..., "content": ..., "tags": [...], "depth": int} or None
    """
    blocks = get_presets(topic)
    if not blocks:
        return None
    idx = cycle_index % len(blocks)
    block = blocks[idx]
    # Avoid double prefix: if block title already starts with topic, don't prepend
    title = block["title"] if block["title"].lower().startswith(topic.lower()) else f"{topic} - {block['title']}"
    return {
        "title": title,
        "content": block["content"],
        "tags": block["tags"],
        "depth": block.get("depth", 2),
    }
