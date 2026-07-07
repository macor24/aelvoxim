"""aelvoxim.core.content_filter — Input/output content safety filter.

Filters both user input (prompt injection, dangerous commands) and LLM output
(PII leakage, harmful content). Pure rule-based, no LLM calls.

Activated via env var METACORE_CONTENT_FILTER=true (off by default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FilterVerdict:
    passed: bool = True
    reason: str = ""
    flagged_patterns: List[str] = field(default_factory=list)


# ── PII patterns ──

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "phone_number"),        # Phone
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "credit_card"),  # Credit card
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "email"),  # Email
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"), "api_key"),                    # API Key (sk-...)
    (re.compile(r"ghp_[a-zA-Z0-9]{36}\b"), "github_token"),                 # GitHub PAT
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "base64_token"),          # Base64 tokens
]

# ── Harmful content patterns ──

_HARMFUL_PATTERNS = [
    (re.compile(r"(?i)how to (build|make|create) a (bomb|weapon|explosive)"), "weapon_instructions"),
    (re.compile(r"(?i)(malware|ransomware|trojan|keylogger) code"), "malware_code"),
    (re.compile(r"(?i)child.*(porn|abuse|exploit)"), "child_safety"),
    (re.compile(r"(?i)self.?harm|suicide.*method"), "self_harm"),
]

# ── Prompt injection patterns ──

_INJECTION_PATTERNS = [
    (re.compile(r"(?i)ignore (all )?(previous|prior) (instructions|directions)"), "ignore_instructions"),
    (re.compile(r"(?i)act as (if you were|though you are) (an? )?(admin|root|superuser|god)"), "role_escalation"),
    (re.compile(r"(?i)you are (now|no longer) (bound by|required to follow|limited by)"), "bypass_restrictions"),
    (re.compile(r"(?i)system prompt[:：]"), "system_prompt_leak"),
    (re.compile(r"(?i)你是一个|你是系统|忽略(所有|之前)的(指令|规则)"), "cn_injection"),
]


def filter_input(text: str) -> FilterVerdict:
    """Filter user input for prompt injection and harmful content.

    Returns FilterVerdict. If not passed, the input should be blocked.
    """
    if not text:
        return FilterVerdict()

    verdict = FilterVerdict()

    # Check prompt injection
    for pattern, name in _INJECTION_PATTERNS:
        if pattern.search(text):
            verdict.passed = False
            verdict.flagged_patterns.append(name)
            verdict.reason = f"Prompt injection detected: {name}"
            return verdict

    # Check harmful content
    for pattern, name in _HARMFUL_PATTERNS:
        if pattern.search(text):
            verdict.passed = False
            verdict.flagged_patterns.append(name)
            verdict.reason = f"Harmful content detected: {name}"
            return verdict

    return verdict


def filter_output(text: str, check_pii: bool = True) -> FilterVerdict:
    """Filter LLM output for PII leakage and harmful content.

    Args:
        text: LLM output to filter.
        check_pii: Whether to scan for PII patterns.

    Returns:
        FilterVerdict with passed/reason/flagged_patterns.
    """
    if not text:
        return FilterVerdict()

    verdict = FilterVerdict()

    # Check harmful content in output
    for pattern, name in _HARMFUL_PATTERNS:
        if pattern.search(text):
            verdict.passed = False
            verdict.flagged_patterns.append(name)
            verdict.reason = f"Harmful content in output: {name}"
            return verdict

    # Check PII (only when enabled)
    if check_pii:
        for pattern, name in _PII_PATTERNS:
            if pattern.search(text):
                verdict.passed = False
                verdict.flagged_patterns.append(name)
                verdict.reason = f"PII detected in output: {name}"
                return verdict

    return verdict


def sanitize_output(text: str) -> str:
    """Sanitize LLM output — mask PII patterns.

    Less aggressive than filter_output: masks PII instead of blocking.
    """
    if not text:
        return text

    for pattern, name in _PII_PATTERNS:
        if name == "phone_number":
            text = pattern.sub("[PHONE]", text)
        elif name == "email":
            text = pattern.sub("[EMAIL]", text)
        elif name in ("api_key", "github_token", "base64_token"):
            text = pattern.sub("[REDACTED]", text)

    return text
