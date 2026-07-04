"""
metacore.control.metacog_check — In-generation metacognition checker.

Evaluates each generated chunk against 5 rules:
  R1 fact_conflict   — LLM check (optional, off by default)
  R2 drift           — keyword + LLM check
  R3 unverified_fact — regex (numbers/dates)
  R4 safety          — keyword match
  R5 clarity         — rule engine

Default mode: rules engine only (R4, R5, R3). R1 and R2 LLM checks
are disabled unless LLM_CHECK_ENABLED=True.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ── Safety keywords (R4) ──
_SAFETY_PATTERNS = [
    r"(?i)rm\s+-rf\s+/",
    r"(?i)drop\s+table",
    r"(?i)truncate\s+table",
    r"(?i)delete\s+from\s+\S+\s+where\s+1\s*=\s*1",
    r"(?i)shutdown\s+system",
    r"(?i)format\s+disk",
    r"(?i)mkfs\.",
    r"(?i)dd\s+if=.*of=.*/dev/",
]

# ── Clarity issues (R5) — vague/ambiguous phrases ──
_VAGUE_PATTERNS = [
    r"(?i)等\s*(等|\.\.\.)",
    r"(?i)and\s+so\s+on",
    r"(?i)etc\.?\s*$",
    r"(?i)诸如此类",
    r"(?i)可能\s*(吧|大概|也许)(?!\s*是)",
]

# ── Repetition detection (R5) — repeated sentences ──
_REPEAT_REGEX = re.compile(
    r"(.{15,100}[。！？.!?])\s*\1"
)

# ── Suspicious data patterns (R3) ──
_SUSPECT_NUMBERS = re.compile(
    r"(?:(?<!\d)\d{4,}(?!\d))"   # 4+ digit numbers (may be fabricated)
    r"|"
    r"(?:\b\d{1,3}[,.]\d{3}\b)"  # formatted numbers like 1,234 or 1.234
)

# ── Topic drift keywords (R2 quick check) ──
_DRIFT_KEYWORDS = {
    "python": {"javascript", "rust", "golang", "swift", "kotlin", "c++", "c#"},
    "前端": {"后端", "数据库", "运维", "服务器"},
    "react": {"vue", "angular", "svelte", "jquery"},
    "fastapi": {"flask", "django", "tornado", "aiohttp"},
    "postgresql": {"mongodb", "redis", "mysql", "sqlite"},
    "docker": {"kubernetes", "podman", "containerd"},
    "linux": {"windows", "macos"},
}


def evaluate(
    chunk: str,
    accumulated: str,
    topic: str = "",
    llm_check_enabled: bool = False,
    call_llm_fn=None,
) -> Tuple[str, List[Dict[str, str]]]:
    """Evaluate a generated chunk. Returns (severity, issues[]).

    Severity: "PASS" | "MINOR" | "SEVERE"
    """
    issues: List[Dict[str, str]] = []
    severity = "PASS"

    # ── R4: Safety check (always on) ──
    for pat in _SAFETY_PATTERNS:
        if re.search(pat, chunk):
            issues.append({
                "type": "safety",
                "detail": f"检测到安全敏感内容: {pat[:40]}",
            })
            severity = _max_severity(severity, "SEVERE")

    # ── R5: Clarity check (always on) ──
    for pat in _VAGUE_PATTERNS:
        if re.search(pat, chunk):
            issues.append({
                "type": "clarity",
                "detail": "表达较模糊，建议更具体",
            })
            # clarity never raises severity

    # ── R5b: Repetition detection ──
    repeat_matches = _REPEAT_REGEX.findall(chunk)
    if repeat_matches:
        issues.append({
            "type": "repetition",
            "detail": f"检测到内容重复，同一句子出现了多次",
        })
        severity = _max_severity(severity, "SEVERE")

    # ── R5c: Accumulated repetition (same content in both chunk and accumulated) ──
    if accumulated and len(accumulated) > 50:
        tail = accumulated[-200:].strip()
        chunk_stripped = chunk.strip()
        if chunk_stripped and tail and chunk_stripped in tail:
            issues.append({
                "type": "repetition",
                "detail": "新生成内容与已有内容高度重复",
            })
            severity = _max_severity(severity, "SEVERE")

    # ── R2b: Complete topic ignore — chunk doesn't address the query at all ──
    if topic and len(topic) > 3 and len(chunk) > 15:
        # Chinese query: check first 8 chars overlap
        if any('\u4e00' <= c <= '\u9fff' for c in topic):
            topic_keywords = set(topic.strip()[:8])
            chunk_head = chunk[:100]
            overlap = topic_keywords & set(chunk_head)
            if len(overlap) == 0:
                issues.append({
                    "type": "drift",
                    "detail": "生成内容与用户问题完全无关",
                })
                severity = _max_severity(severity, "SEVERE")
        else:
            # English: check if chunk mentions session/history without answering
            session_phrases = ["session history", "session record", "your conversation",
                               "your previous", "based on your", "您之前的", "根据您的",
                               "根据记录", "根据会话", "会话记录", "上下文"]
            has_session_ref = any(p in chunk.lower() for p in session_phrases)
            query_phrases = topic.lower().split()
            has_query_ref = any(p in chunk.lower() for p in query_phrases)
            if has_session_ref and not has_query_ref:
                issues.append({
                    "type": "drift",
                    "detail": "生成内容仅复述了历史记录，未回应当前问题",
                })
                severity = _max_severity(severity, "SEVERE")

    # ── R3: Unverified data (always on) ──
    matches = _SUSPECT_NUMBERS.findall(chunk)
    if len(matches) >= 2:
        issues.append({
            "type": "unverified_fact",
            "detail": f"包含 {len(matches)} 个数值，请确认准确性",
        })
        severity = _max_severity(severity, "MINOR")

    # ── R2: Quick topic drift via keyword matching ──
    if topic:
        drift = _quick_drift_check(chunk, topic)
        if drift:
            issues.append({
                "type": "drift",
                "detail": drift,
            })
            severity = _max_severity(severity, "MINOR")

    # ── R1: Fact contradiction (LLM check, off by default) ──
    if llm_check_enabled and call_llm_fn and accumulated:
        contradiction = _llm_contradiction_check(
            chunk, accumulated, call_llm_fn
        )
        if contradiction:
            issues.append({
                "type": "fact_conflict",
                "detail": contradiction,
            })
            severity = _max_severity(severity, "SEVERE")

    # ── R2 deep: LLM drift check (off by default) ──
    if llm_check_enabled and call_llm_fn and topic and severity != "SEVERE":
        deep_drift = _llm_drift_check(chunk, topic, call_llm_fn)
        if deep_drift:
            issues.append({
                "type": "drift",
                "detail": deep_drift,
            })
            severity = _max_severity(severity, "MINOR")

    return severity, issues


def _max_severity(a: str, b: str) -> str:
    order = {"PASS": 0, "MINOR": 1, "SEVERE": 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def _quick_drift_check(chunk: str, topic: str) -> str:
    """Quick keyword-based drift detection."""
    topic_lower = topic.lower()
    for key, off_topics in _DRIFT_KEYWORDS.items():
        if key in topic_lower:
            for off in off_topics:
                if off in chunk.lower():
                    return f"话题从 '{key}' 偏离到 '{off}'"
    return ""


def _llm_contradiction_check(
    chunk: str, accumulated: str, call_llm_fn,
) -> str:
    """LLM-based fact contradiction check."""
    prompt = (
        "以下新文本与已有上下文是否存在事实矛盾？\n"
        "如果存在矛盾，简要说明是什么矛盾。如果不存在，只回答 NO。\n\n"
        f"已有上下文（末尾500字）：\n{accumulated[-500:]}\n\n"
        f"新文本：\n{chunk}\n\n"
        "回答（YES + 矛盾说明，或 NO）："
    )
    try:
        result = call_llm_fn(prompt)
        if result and result.strip().upper().startswith("YES"):
            return result.strip()[4:100].lstrip(": ")
    except Exception:
        pass
    return ""


def _llm_drift_check(
    chunk: str, topic: str, call_llm_fn,
) -> str:
    """LLM-based topic drift check."""
    prompt = (
        "以下文本是否偏离了原话题？只回答 YES/NO。\n\n"
        f"原话题：{topic}\n"
        f"文本：{chunk[:300]}\n\n"
        "回答（YES 或 NO）："
    )
    try:
        result = call_llm_fn(prompt)
        if result and result.strip().upper().startswith("YES"):
            return "LLM 检测到话题偏离"
    except Exception:
        pass
    return ""
