"""aelvoxim.client.security_gate — SentriKit safety check call layer

Sends key operations to SentriKit SafetyGuard for 37-rule red-line checking.
Auto-degrades to local is_safe_input() when SentriKit is unavailable.

Risk level system + user preference learning:
- Low risk: friendly prompt + suggested replacement
- Medium risk: guide correction + provide alternatives
- High risk: clear explanation + safe alternative operations
- User feedback: "false positive" or "accept risk" auto-learns preferences
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from ..utils import DATA_DIR

# ── SentriKit host (same config as sentrikit.py) ──

_SENTRIKIT_HOST = os.environ.get("SENTRIKIT_HOST", "https://127.0.0.1:8899")
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_CACHE_DIR = str(DATA_DIR)
_SK_KEY_FILE = os.path.join(_CACHE_DIR, "sentrikit_key.json")

# ── Risk level classification ──

_RISK_LEVELS: Dict[str, str] = {
    # Low: keyword pattern match → path/command detection
    "forbidden pattern": "low",
    "Local block": "low",
    # Medium: suspicious but possible false positive
    "data leak": "medium",
    "R2": "medium",
    "R9": "medium",
    "prompt injection": "medium",
    "config": "medium",
    "R4": "medium",
    "R5": "medium",
    # High: clearly dangerous
    "self-replicat": "high",
    "self_replicat": "high",
    "clone itself": "high",
    "autonomous replicat": "high",
    "destructive": "high",
    "R3": "high",
    "R26": "high",
    "memory poison": "high",
}

_DEFAULT_RISK = "medium"

# ── User-friendly advice per rule ──

_BLOCK_ADVICE: Dict[str, str] = {
    "forbidden pattern": (
        "使用完整绝对路径代替相对路径或通配符。"
        "例如使用 '/home/user/file.txt' 而非 '~/file.txt'。"
    ),
    "Local block": "输入内容触发了安全模式匹配，请检查后重试。",
    "data leak": "请勿发送密码、API Key、Token 等敏感信息。如需配置请在设置面板中操作。",
    "R2": "检测到可能包含敏感信息。请检查消息中是否含有密码或密钥。",
    "R9": "请不要尝试修改 AI 的系统指令或行为规则。",
    "prompt injection": "系统指令不允许通过对话修改，请使用设置面板。",
    "config": "配置变更已被安全规则拦截。请在安全面板查看配置保护规则。",
    "R4": "配置保护规则已拦截此变更。",
    "R5": "权限变更已被拦截。",
    "self-replicat": "自动复制/繁殖操作已被安全规则禁止。如需研究请在安全面板中临时关闭此规则。",
    "self_replicat": "自动复制/繁殖操作已被安全规则禁止。",
    "clone itself": "自动复制/繁殖操作已被安全规则禁止。",
    "autonomous replicat": "自主繁殖操作已被安全规则禁止。",
    "destructive": "检测到破坏性操作。建议在安全面板查看当前允许的操作范围。",
    "R3": "检测到破坏性操作。",
    "R26": "检测到可能污染记忆库的内容，已自动拦截。",
    "memory poison": "检测到可能污染记忆库的内容，已自动拦截。",
}

_BLOCK_ADVICE_EN: Dict[str, str] = {
    "forbidden pattern": "Use absolute paths instead of '~' or '..'.",
    "Local block": "This input triggered a safety pattern match.",
    "data leak": "Avoid sending passwords, API keys, or tokens. Use the settings panel.",
    "R2": "Sensitive information may be detected. Check your message content.",
    "R9": "System prompt modification is not allowed via chat.",
    "prompt injection": "System instructions cannot be modified through chat.",
    "config": "Configuration changes are blocked by safety rules.",
    "R4": "Configuration protection rules blocked this change.",
    "self-replicat": "Self-replication is blocked by safety rules.",
    "self_replicat": "Self-replication is blocked by safety rules.",
    "destructive": "Destructive operations are blocked.",
    "R3": "Destructive operation detected.",
    "R26": "Memory poisoning detection blocked this content.",
    "memory poison": "Memory poisoning detection blocked this content.",
}

# ── Suggestions per risk level (displayed to user) ──

_SUGGESTIONS: Dict[str, list] = {
    "low": [
        "使用完整绝对路径代替相对路径",
        "使用 '--dry-run' 预览变更效果",
        "如需执行此操作，请在安全面板中调整规则",
    ],
    "medium": [
        "检查消息中是否包含敏感信息（密码、密钥等）",
        "如需传递 API Key，请在设置面板中填写",
        '如果这是误判，请回复 "这是误判"，系统会学习',
    ],
    "high": [
        "此操作涉及安全红线，不建议执行",
        "如需研究/测试，请在安全面板中临时关闭对应规则",
        '如果确实需要执行，请回复 "我接受风险"',
    ],
}

_SUGGESTIONS_EN: Dict[str, list] = {
    "low": [
        "Use absolute paths instead of relative paths",
        "Use '--dry-run' to preview changes first",
        "Adjust rules in the safety panel if needed",
    ],
    "medium": [
        "Check your message for sensitive information (passwords, keys)",
        "Use the settings panel for API Key configuration",
        'Reply "false positive" if this was a mistake — I will learn',
    ],
    "high": [
        "This operation is blocked by high-priority safety rules",
        "Temporarily disable the rule in the safety panel for research",
        'Reply "I accept the risk" if you are certain',
    ],
}


# ── User preference learning ──

_SAFETY_PREFS_PATH = DATA_DIR / "safety_preferences.json"


def _load_prefs() -> dict:
    try:
        if _SAFETY_PREFS_PATH.exists():
            return json.loads(_SAFETY_PREFS_PATH.read_text())
    except Exception:
        pass
    return {"overrides": {}, "false_positive_feedback": [], "accepted_risks": {}}


def _save_prefs(prefs: dict) -> None:
    try:
        _SAFETY_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SAFETY_PREFS_PATH.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2)
        )
    except Exception:
        pass


def record_feedback(rule_key: str, is_false_positive: bool, user_id: str = "") -> None:
    """Record user feedback on a safety block.

    When the same rule gets 3+ false positive reports within recent records,
    the system auto-downgrades it to 'allow' to reduce false positives.
    """
    prefs = _load_prefs()
    prefs.setdefault("false_positive_feedback", [])
    now = datetime.now().isoformat()
    prefs["false_positive_feedback"].append({
        "rule": rule_key,
        "is_false_positive": is_false_positive,
        "user_id": user_id,
        "ts": now,
    })
    # Keep only last 50 entries, auto-prune
    prefs["false_positive_feedback"] = prefs["false_positive_feedback"][-50:]

    if is_false_positive and rule_key:
        recent = [
            f for f in prefs["false_positive_feedback"][-20:]
            if f["rule"] == rule_key and f["is_false_positive"]
        ]
        if len(recent) >= 3:
            prefs.setdefault("overrides", {})
            prefs["overrides"][rule_key] = "allow"

    _save_prefs(prefs)


def accept_risk(rule_key: str, user_id: str = "") -> None:
    """User explicitly accepts risk — allow for 1 hour."""
    prefs = _load_prefs()
    prefs.setdefault("accepted_risks", {})
    prefs["accepted_risks"][rule_key] = {
        "user_id": user_id,
        "ts": datetime.now().isoformat(),
        "expires": (datetime.now() + timedelta(hours=1)).isoformat(),
    }
    _save_prefs(prefs)


def is_overridden(rule_key: str) -> bool:
    """Check if this rule has been overridden by user feedback."""
    if not rule_key:
        return False
    prefs = _load_prefs()
    # Hard override from false-positive learning
    if rule_key in prefs.get("overrides", {}):
        return True
    # Time-limited accepted risk
    ar = prefs.get("accepted_risks", {})
    entry = ar.get(rule_key)
    if entry:
        expires = entry.get("expires", "")
        if expires and expires >= datetime.now().isoformat():
            return True
        # Expired — remove it and save
        ar.pop(rule_key, None)
        _save_prefs(prefs)
    return False


# ── Helper: classify risk + build advice ──


def _detect_risk_level(reason: str) -> str:
    """Map a block reason to a risk level."""
    reason_lower = reason.lower()
    for key, level in _RISK_LEVELS.items():
        if key.lower() in reason_lower:
            return level
    return _DEFAULT_RISK


def _detect_is_chinese(text: str) -> bool:
    """Check if text contains Chinese characters (for lang-aware messages)."""
    if not text:
        return False
    return any('\u4e00' <= c <= '\u9fff' for c in text)


def get_user_friendly_response(reason: str, scene: str = "chat") -> dict:
    """Build a user-friendly structured response for a blocked operation.

    Returns:
        dict with: message, risk, suggestions, bypass_key (for accept_risk)
    """
    level = _detect_risk_level(reason)
    is_cn = _detect_is_chinese(reason)

    advice_map = _BLOCK_ADVICE if is_cn else _BLOCK_ADVICE_EN
    sugg_map = _SUGGESTIONS if is_cn else _SUGGESTIONS_EN

    # Pick advice by best matching rule key
    reason_lower = reason.lower()
    advice = "This operation was blocked by safety rules."
    triggered_rule = ""
    for key, msg in advice_map.items():
        if key.lower() in reason_lower:
            advice = msg
            triggered_rule = key
            break

    suggestions = sugg_map.get(level, sugg_map[_DEFAULT_RISK])

    return {
        "message": advice,
        "risk": level,
        "scene": scene,
        "reason": reason,
        "rule_triggered": triggered_rule,
        "suggestions": suggestions,
        "bypass_key": triggered_rule,
    }


# ── Local fallback check ──

_LOCAL_BLOCK_PATTERNS = [
    "DROP TABLE", "DROP DATABASE", "TRUNCATE",
    "rm -rf", "rm -rf /", ":(){ :|:& };:", "fork bomb",
    "chmod 777", "chown root",
    "wget ", "curl ",  # remote download in system commands
    "self-replicat", "self_replicat", "clone itself",
    "autonomous replicat",
]

# Patterns that indicate a false positive in chat context
_SAFE_PREFIXES = [
    "what is ", "how to ", "example of ", "i use ", "using ",
    "you can ", "try ", "like ", "such as ", "e.g., ",
    "什么是", "怎么用", "例子", "比如", "例如", "使用", "用",
    "i have ", "have you ", "can i ", "should i ", "would you ",
    "tutorial", "guide", "documentation", "文档", "教程",
]

_SAFE_SUFFIXES = [
    " example", " tutorial", " command", " tool",
    "用法", "工具", "命令", "示例",
]

# Context patterns that indicate code/talk about commands, not execution
_CODE_CONTEXT_PREFIX = r"`"
_CODE_CONTEXT_DELIM = r"```"


def _is_code_context(text: str, pattern: str) -> bool:
    """Check if a pattern appears inside code blocks or backtick-quoted text.

    Returns True if the pattern is inside backticks, triple backticks,
    or quotes — meaning it's being discussed, not executed.
    """
    if not text or not pattern:
        return False
    pl = pattern.lower()
    tl = text.lower()

    # Triple backtick code blocks
    import re
    code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
    for block in code_blocks:
        if pl in block.lower():
            return True

    # Inline backtick `code`
    backtick_segs = re.findall(r'`([^`]+)`', text)
    for seg in backtick_segs:
        if pl in seg.lower():
            return True

    return False


def _is_quoted_context(text: str, pattern: str) -> bool:
    """Check if a pattern appears inside double or single quotes.

    People often talk about commands inside quotes without executing them.
    """
    if not text or not pattern:
        return False
    pl = pattern.lower()

    import re
    # Double quotes
    dq_segs = re.findall(r'"([^"]*)"', text)
    for seg in dq_segs:
        if pl in seg.lower():
            return True
    # Single quotes
    sq_segs = re.findall(r"'([^']*)'", text)
    for seg in sq_segs:
        if pl in seg.lower():
            return True

    return False


def _has_safe_context(text: str, pattern: str) -> bool:
    """Check if the pattern match is preceded by a safe prefix or followed by safe suffix.

    In natural chat, patterns like 'curl' after 'example of' are clearly
    discussions, not commands.
    """
    if not text or not pattern:
        return False
    tl = text.lower()
    pl = pattern.lower()
    idx = tl.find(pl)
    if idx < 0:
        return False

    # Check 40 chars before the match
    before = tl[max(0, idx - 40):idx].strip()
    if not before:
        return False
    if before.endswith("...") or before.endswith("——") or before.endswith(".."):
        return True
    # Token-based prefix check: split before into words, check if the
    # last N words (up to 4) match any safe prefix
    before_words = before.split()
    for n in range(1, min(len(before_words) + 1, 5)):
        tail = " ".join(before_words[-n:]).strip()
        if tail in _SAFE_PREFIXES or (tail + " ") in _SAFE_PREFIXES:
            return True
        # Also check if tail starts with a prefix word
        for prefix in _SAFE_PREFIXES:
            if tail == prefix.rstrip():
                return True
            if tail.startswith(prefix.rstrip()) and len(tail) < len(prefix.rstrip()) + 10:
                return True

    # Check 30 chars after the match (for suffixes like "example", "tool")
    after_start = idx + len(pl)
    after = tl[after_start:after_start + 30].strip()
    for suffix in _SAFE_SUFFIXES:
        if after.startswith(suffix):
            return True
        # Allow single-word follow ups
        first_space = after.find(" ")
        if first_space > 0:
            first_word = after[:first_space]
            if first_word == suffix.strip():
                return True

    return False


def _is_false_positive(text: str, pattern: str) -> bool:
    """Determine if a match is a false positive in chat context."""
    if _is_code_context(text, pattern):
        return True
    if _is_quoted_context(text, pattern):
        return True
    if _has_safe_context(text, pattern):
        return True
    return False


def _local_check(target: str) -> Dict:
    """Local safety check fallback when SentriKit is unreachable.

    Includes context-aware false positive detection to avoid blocking
    natural chat about technical topics.
    """
    target_lower = (target or "").lower()
    for pattern in _LOCAL_BLOCK_PATTERNS:
        if pattern.lower() in target_lower:
            if not _is_false_positive(target, pattern):
                return {"allowed": False, "reason": f"Local block: pattern '{pattern}' detected"}
    return {"allowed": True, "reason": "Local check passed"}


# ── Core: check with override + user-friendly response ──


def _check_with_feedback(
    check_fn, action: str, target: str, trigger: str = "api",
    content: str = "", timeout: int = 8, user_id: str = ""
) -> Dict:
    """Run a safety check, apply overrides, and add user-friendly response."""
    result = check_fn(action, target, trigger, content, timeout)
    if result is None:
        result = _local_check(target if target else content)
    if not result.get("allowed", True):
        triggered = result.get("rule_triggered", "")
        if not triggered:
            for key in _RISK_LEVELS:
                if key in result.get("reason", "").lower():
                    triggered = key
                    break
        # Check if user has overridden this rule
        if triggered and is_overridden(triggered):
            result["allowed"] = True
            result["reason"] = "Allowed by user preference override"
            result["user_override"] = True
            return result
        # Add user-friendly response
        friendly = get_user_friendly_response(
            result.get("reason", ""), scene=trigger
        )
        result["_friendly"] = friendly
    return result


# ── Helper: build SentriKit HTTP call ──


def _call_safety(
    action: str,
    target: str,
    trigger: str = "api",
    content: str = "",
    timeout: int = 10,
) -> Optional[Dict]:
    """Call SentriKit /api/safety/check. Returns result dict or None on failure."""
    api_key = _get_api_key()
    if not api_key:
        return None
    try:
        body = json.dumps({
            "action": action,
            "target": (target or "")[:500],
            "trigger": trigger,
            "content": (content or "")[:2000],
        }).encode()
        req = urllib.request.Request(
            f"{_SENTRIKIT_HOST}/api/safety/check",
            data=body,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ── Public API ──


def check_user_input(text: str, user_id: str = "") -> Dict:
    """Check user message before LLM processing.

    Covers: R2 (data leak), R9 (prompt injection), R3 (destructive ops)
    Supports user preference learning for false positives.
    """
    return _check_with_feedback(
        lambda a, t, tr, c, to: _call_safety(a, t, tr, c, to),
        "create", text, trigger="llm_chat", content=text, timeout=8,
        user_id=user_id,
    )


def check_write(content: str, trigger: str = "knowledge_store",
                user_id: str = "") -> Dict:
    """Check before storing knowledge/memory.

    Covers: R2 (data leak), R9 (prompt injection), R26 (memory poisoning)
    """
    return _check_with_feedback(
        lambda a, t, tr, c, to: _call_safety(a, t, tr, c, to),
        "create", content[:200], trigger=trigger, content=content,
        timeout=8, user_id=user_id,
    )


def check_config_change(key: str, value: str, user_id: str = "") -> Dict:
    """Check before modifying system configuration.

    Covers: R4 (safety rules protection), R5 (self-authorization), R12 (network exfil)
    """
    target = f"config:{key}={value}"
    return _check_with_feedback(
        lambda a, t, tr, c, to: _call_safety(a, t, tr, c, to),
        "config", target, trigger="config_set", content=value,
        timeout=8, user_id=user_id,
    )


def check_evolution(action: str, target: str, user_id: str = "") -> Dict:
    """Check before learner/evolution operations.

    Covers: R13 (self-replication), R28 (recursive improvement), R3 (destructive)
    """
    return _check_with_feedback(
        lambda a, t, tr, c, to: _call_safety(a, t, tr, c, to),
        "execute", target, trigger="evolution", content=target,
        timeout=8, user_id=user_id,
    )


def check_memory_write(key: str, value: str, user_id: str = "") -> Dict:
    """Check before writing to memory store.

    Covers: R2 (data leak), R26 (memory poisoning)
    """
    target = f"memory:{key}={value[:100]}"
    return _check_with_feedback(
        lambda a, t, tr, c, to: _call_safety(a, t, tr, c, to),
        "create", target, trigger="memory_write", content=value,
        timeout=8, user_id=user_id,
    )
