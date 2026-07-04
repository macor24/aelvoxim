"""aelvoxim.learn.validate — Knowledge validation and storage

Execute a sub-task, validate, and store the result.
Pipeline: execute → validate → store → log
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .knowledge import KnowledgeBase
from .execute import try_execute_task
from .extract import extract_knowledge, is_valid_content, content_has_real_value
from .patches.validate_safe import safe_is_validated


def _topic_is_english(topic: str) -> bool:
    """Check if a topic is primarily English (no Chinese characters)."""
    return not bool(re.search(r'[\u4e00-\u9fff]', topic))


def _is_overly_chinese(text: str) -> bool:
    """Check if Chinese character ratio exceeds 50%."""
    if not text:
        return False
    total = len(text)
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return total > 20 and (cn / total) > 0.5


def execute_and_validate(
    topic: str,
    task: str,
    log_func: Optional[Callable[[str], None]] = None,
    on_store: Optional[Callable[[str, str, float], None]] = None,
    value_level: int = 2,
) -> bool:
    """Execute a sub-task, validate the result, and store to knowledge base.

    Pipeline:
    1. Try true execution (code templates)
    2. Fall back to search + LLM extraction
    3. Validate quality (execution_result bypasses AutoValidator)
    4. Store to KnowledgeBase
    5. Call on_store(topic, title, score) for tracking (e.g. save config)

    Returns True if knowledge was produced and stored.
    """
    import time
    from datetime import datetime

    # ── SentriKit safety check ──
    try:
        from ..client.security_gate import check_write as _sk_w
        _sk_r = _sk_w(f"{topic}: {task}", trigger="knowledge_store")
        if not _sk_r.get("allowed", True):
            if log_func:
                log_func(f"  🛑 Safety block: {_sk_r.get('reason', '')}")
            return False
    except Exception:
        pass

    log = log_func or (lambda msg: None)

    title = f"{topic} - {task}"

    # Check if already exists
    if KnowledgeBase.get_by_title(title):
        log(f"  ⏭️ [{topic}] Task already exists: {task}")
        if on_store:
            on_store(topic, title, 0.5)
        return True

    # Step 1: Try true execution
    content = try_execute_task(topic, task)
    source_type = "execution_result"
    confidence = 0.9

    # Step 2: Fall back to search extraction
    if content is None:
        content = extract_knowledge(task, "practice")
        source_type = "learner_task"
        confidence = 0.7
        # English topic + Chinese-only content → low quality
        if content and _topic_is_english(topic) and _is_overly_chinese(content):
            log(f"  🚫 [{topic}] Chinese-only content for English topic, discarded: {task[:30]}")
            return False

    if content is None:
        log(f"  ⏭️ [{topic}] No real content: {task}")
        return False

    # Step 3: Validate
    combined_score = 0.5  # default fallback
    if source_type == "execution_result":
        from .extract import is_generic_template_output
        # 1. Reject generic template output
        if is_generic_template_output(content):
            log(f"  🚫 [{topic}] Generic template output, not real knowledge: {task}")
            return False
        # 2. LLM topic relevance check
        llm_value = _execution_has_value(topic, task, content)
        if llm_value is False:
            log(f"  🚫 [{topic}] LLM: output not relevant: {task}")
            return False
        elif llm_value is True:
            combined_score = 1.0
            log(f"  ✅ [{topic}] LLM verified: content has learning value")
        else:
            # LLM unavailable — downgrade confidence
            confidence = 0.5
            combined_score = 0.5
            log(f"  ⚠️ [{topic}] No LLM available, lower confidence: {task}")
    elif source_type == "search":
        from .. import _EDITION
        if _EDITION == "community":
            combined_score = 0.6
            log(f"  ✅ [{topic}] Community edition: search result accepted without L3-L4 validation")
        else:
            try:
                from .validator import AutoValidator
                validator = AutoValidator()
                auto_result = validator.verify({
                    "title": title,
                    "content": content,
                    "topic": topic,
                })
                combined_score = auto_result.get("combined_score", 0.5)
            except Exception:
                combined_score = 0.5

    if combined_score < 0.4:
        log(f"  🚫 [{topic}] AutoValidator failed ({combined_score:.2f}): {task}")
        return False

    if combined_score < 0.6:
        confidence = round(confidence * 0.5, 2)
        log(f"  ⚠️ [{topic}] AutoValidator weak pass ({combined_score:.2f}), conf→{confidence}: {task}")

    # Step 4: Quality checks
    if source_type == "learner_task":
        if not is_valid_content(topic, task[:8], content):
            log(f"  ⏭️ [{topic}] Quality check failed: {task}")
            return False
        if not content_has_real_value(content):
            log(f"  ⏭️ [{topic}] Content too generic: {task}")
            return False
        if len(content.strip()) < 150:
            log(f"  ⏭️ [{topic}] Content too short ({len(content.strip())} < 300): {task}")
            return False
        if not _has_technical_keywords(content, min_count=1):
            log(f"  🚫 [{topic}] No technical keywords found: {task}")
            return False
    else:
        # Execution results also pass quality check
        if not content_has_real_value(content):
            log(f"  ⏭️ [{topic}] Execution content too generic: {task}")
            return False
        if len(content.strip()) < 100:
            log(f"  ⏭️ [{topic}] Execution output too short ({len(content.strip())} < 100): {task}")
            return False
        if not _has_technical_keywords(content, min_count=1):
            log(f"  🚫 [{topic}] Execution output no technical keywords: {task}")
            return False

    # Step 4b: Judge scoring (knowledge quality gate)
    try:
        from ..core.judge import KnowledgeProposal, score_knowledge_entry
        _kp = KnowledgeProposal(
            topic=topic,
            content=content,
            source=source_type,
            confidence=confidence,
            content_length=len(content),
            has_execution=(source_type == "execution_result"),
        )
        _jr = score_knowledge_entry(_kp)
        if _jr.grade.value == "D":
            log(f"  🚫 [{topic}] Judge D-grade, rejected: {task}")
            return False
        if _jr.grade.value == "C":
            confidence = round(confidence * 0.6, 2)
            log(f"  ⚠️ [{topic}] Judge C-grade, conf→{confidence}: {task}")
        if _jr.grade.value in ("A", "S"):
            log(f"  ✅ [{topic}] Judge {_jr.grade.value}-grade ({_jr.total_score:.2f}): {task}")
    except Exception:
        log(f"  ⚠️ [{topic}] Judge unavailable, lowering confidence: {task}")
        confidence = round(confidence * 0.5, 2)

    # Determine validated flag: true only if AutoValidator + Judge both passed well
    is_validated = safe_is_validated(combined_score, _jr.grade.value if '_jr' in dir() else None)

    # Step 5: Store to pending quarantine (requires 5 practice verifications)
    summary = f"About '{task}' ({source_type}):\n{content[:120].strip()}..."
    entry = KnowledgeBase.store_pending(
        topic=topic,
        title=title,
        summary=summary,
        content=content,
        source=source_type,
        tags=[topic, task, source_type],
        confidence=confidence,
        depth=3,
        validated=is_validated,
        value_level=value_level,
    )

    # Step 6: Callback for tracking
    if on_store:
        on_store(topic, title, combined_score)

    emoji = "⚡" if source_type == "execution_result" else "📝"
    log(f"{emoji} [{topic}] {'Execution' if source_type=='execution_result' else 'Search'} done: {task} (score={combined_score:.2f})")
    return True


# ── LLM-based execution content evaluation ────

# ── Technical keyword detection ────────────────

# Common technical keywords that indicate real knowledge content
_TECHNICAL_KEYWORDS = [
    # Programming languages & frameworks
    "python", "javascript", "typescript", "java", "golang", "rust", "c++", "ruby",
    "react", "vue", "angular", "django", "flask", "fastapi", "spring", "express",
    "nextjs", "nuxt", "svelte", "tailwind", "bootstrap",
    "pytorch", "tensorflow", "keras", "jax", "scikit", "numpy", "pandas",
    # Databases
    "sql", "mysql", "postgresql", "sqlite", "mongodb", "redis", "elasticsearch",
    "cassandra", "dynamodb", "bigquery", "oracle", "mariadb",
    # Tools & platforms
    "docker", "kubernetes", "k8s", "nginx", "apache", "jenkins", "github",
    "gitlab", "ansible", "terraform", "helm", "prometheus", "grafana",
    "aws", "azure", "gcp", "cloud", "serverless", "lambda",
    # Protocols & standards
    "http", "https", "rest", "graphql", "grpc", "websocket", "tcp", "udp",
    "oauth", "jwt", "tls", "ssl", "cors", "api",
    # Concepts
    "asyncio", "async", "concurrency", "parallel", "cache", "index",
    "thread", "process", "pool", "queue", "lock", "mutex", "semaphore",
    "middleware", "hook", "decorator", "singleton", "factory", "proxy",
    "monitoring", "logging", "observability", "telemetry",
    "orm", "migration", "sharding", "replication", "partition",
    "ci/cd", "pipeline", "deploy", "rollback", "canary",
    # File formats & data
    "json", "yaml", "xml", "csv", "protobuf", "markdown",
    "query", "schema", "endpoint", "route", "handler", "callback",
    # Patterns
    "algorithm", "pattern", "strategy", "benchmark", "profiling",
    "refactor", "refactoring", "optimization", "validation",
]

# Compile a single regex for fast matching
_TECH_REGEX_PATTERN = re.compile(
    r'\b(?:' + '|'.join(re.escape(kw) for kw in _TECHNICAL_KEYWORDS) + r')\b',
    re.IGNORECASE
)


def _has_technical_keywords(content: str, min_count: int = 2) -> bool:
    """Check if content contains at least 2 distinct technical keywords.

    Returns True only if the content has real technical substance.
    """
    if not content or len(content.strip()) < 10:
        return False
    matches = set(m.group(0).lower() for m in _TECH_REGEX_PATTERN.finditer(content))
    return len(matches) >= min_count


def _execution_has_value(topic: str, task: str, content: str) -> Optional[bool]:
    """Use LLM to judge if execution output has real learning value."""
    try:
        from .extract import call_llm_if_available
        llm = call_llm_if_available()
        if not llm:
            return None
        call_fn, model = llm
        prompt = (
            "Topic: " + str(topic) + "\\n"
            "Sub-task: " + str(task) + "\\n"
            "Execution output:\\n" + str(content[:500]) + "\\n\\n"
            "Does this output contain genuine technical knowledge "
            "relevant to the topic that someone could learn from?\\n"
            "Answer ONLY: 'yes' / 'no' / 'metadata_only'"
        )
        text = call_fn(
            model=model,
            system_prompt="You are a technical content evaluator.",
            user_message=prompt,
            max_tokens=10,
        )
        answer = (text or "").strip().lower()
        if "yes" in answer and "metadata" not in answer:
            return True
        return False
    except Exception:
        return None

