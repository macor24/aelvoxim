# SPDX-License-Identifier: MIT
"""
metacore.memory.scorer — Adaptive memory scoring engine.

Pure rule-based (no LLM). Evaluates entity retention value based on:
1. Base signal type (personal info, emotional, explicit instruction, etc.)
2. Repetition across sessions (mention count)
3. User confirmation / rejection
4. Time decay (recency weight)

Score range: 0.0 - 1.0
Decision thresholds:
  >= 0.90 → semantic (permanent)
  >= 0.70 → episodic (30d TTL)
  >= 0.50 → working (24h TTL, pending confirmation)
  <  0.50 → do not store
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

# ── Confidence adjustment based on entity tags ──

# Base confidence for each signal type (first mention)
_BASE_CONFIDENCE: Dict[str, float] = {
    "person": 0.40,
    "location": 0.40,
    "organization": 0.35,
    "preference": 0.35,
    "topic": 0.30,
}

# Emotional keyword levels
_HIGH_EMOTION: Set[str] = {
    '超爱', '超级', '极度', '最', '绝对', '一定要', '非常重要', 'really love',
    'extremely', 'absolutely', 'must', 'hate', 'terrible',
}
_MEDIUM_EMOTION: Set[str] = {
    '特别', '非常', '很喜欢', '很讨厌', 'very much', 'really like', 'really hate',
}
_LOW_EMOTION: Set[str] = {
    '喜欢', '感兴趣', '不错', 'like', 'enjoy', 'interested', 'good',
}

_EXPLICIT_INSTRUCTION: Set[str] = {
    '记住', '别忘了', '不要忘记', '一定要记住', '务必记住',
    'remember', "don't forget", 'never forget', 'keep in mind',
}

_CONFIRM_POSITIVE: Set[str] = {
    '对', '是', '记得', '记住了', '是的', '对的', '没错', '对呀',
    'yes', 'right', 'correct', 'yeah', 'sure', "that's right",
}
_CONFIRM_NEGATIVE: Set[str] = {
    '不对', '不是', '不用', '不用记', '不要记', '忘了', '删除',
    'no', 'wrong', "don't", 'delete', 'forget', 'incorrect', 'nope',
}

_TIME_SENSITIVE: Set[str] = {
    '下周', '明天', '下个月', '下周', '后天', '下个', '月底',
    'next week', 'tomorrow', 'next month', 'next monday',
}

# ── Scoring helpers ──


def detect_signal(text: str) -> Tuple[str, float]:
    """Detect signal type and base confidence from user message text.

    Returns:
        (signal_type, base_confidence)
    """
    if not text:
        return "general", 0.10
    text_lower = text.lower()

    # Explicit instruction → highest base
    for kw in _EXPLICIT_INSTRUCTION:
        if kw in text_lower:
            return "explicit", 0.80

    # Emotional → medium-high
    for kw in _HIGH_EMOTION:
        if kw in text_lower:
            return "emotional_high", 0.50
    for kw in _MEDIUM_EMOTION:
        if kw in text_lower:
            return "emotional_medium", 0.40
    for kw in _LOW_EMOTION:
        if kw in text_lower:
            return "emotional_low", 0.30

    # Time-sensitive
    for kw in _TIME_SENSITIVE:
        if kw in text_lower:
            return "time_sensitive", 0.60

    return "general", 0.10


def compute_confidence(
    tag: str,
    text: str,
    mention_count: int = 1,
    user_confirmed: bool = False,
    days_since_last: float = 0,
) -> float:
    """Compute final confidence score for an entity.

    Args:
        tag: Entity type tag (person, location, etc.)
        text: Original user message that produced this entity
        mention_count: How many times this entity has been extracted (same key)
        user_confirmed: Whether user explicitly confirmed this entity
        days_since_last: Days since this entity was last mentioned (for decay)

    Returns:
        Confidence score 0.0-1.0.
    """
    # 1. Base from tag
    base = _BASE_CONFIDENCE.get(tag, 0.30)

    # 2. Signal boost from text
    _, signal_conf = detect_signal(text)
    base = max(base, signal_conf)

    # 3. Repetition boost
    if mention_count >= 3:
        base += 0.20
    elif mention_count >= 2:
        base += 0.15
    elif mention_count >= 2 and user_confirmed:
        base += 0.30  # Second mention + confirmed → big jump

    # 4. User confirmation boost
    if user_confirmed:
        base += 0.15

    # 5. Time decay
    if days_since_last > 0:
        decay = min(0.05 * days_since_last, 0.50)
        base -= decay

    return max(0.0, min(base, 0.95))


def compute_5d_metadata(
    tags: List[str],
    source: str = "",
    value: str = "",
    timestamp_str: str = "",
    mention_count: int = 1,
    has_conflict: bool = False,
) -> Dict[str, float]:
    """Compute 5-dimension confidence metadata (delegates to conf_matrix).

    Added as a convenience wrapper so callers don't need to import
    conf_matrix directly.
    """
    from .conf_matrix import compute_5d
    return compute_5d(
        tags=tags or [],
        source=source,
        value=value,
        timestamp_str=timestamp_str,
        mention_count=mention_count,
        has_conflict=has_conflict,
    )


def determine_layer(confidence: float) -> str:
    """Map confidence to memory layer name."""
    if confidence >= 0.90:
        return "semantic"
    if confidence >= 0.70:
        return "episodic"
    return "working"


def needs_confirmation(confidence: float) -> bool:
    """Whether this entity needs user confirmation before permanent storage."""
    return 0.50 <= confidence < 0.70


def detect_confirmation(text: str) -> Optional[bool]:
    """Detect if user is confirming or rejecting a pending memory.

    Returns:
        True if confirmed, False if rejected, None if unclear.
    """
    if not text:
        return None
    text_lower = text.lower()
    # Negative prefix check first (e.g. "不对" should not match "对")
    if text_lower.startswith(("不对", "不是的", "你记错了", "no,", "No,")):
        return False
    for kw in _CONFIRM_POSITIVE:
        if kw in text_lower:
            return True
    for kw in _CONFIRM_NEGATIVE:
        if kw in text_lower:
            return False
    return None


def detect_clear_command(text: str) -> bool:
    """Detect 'clear my memory' command."""
    if not text:
        return False
    text_lower = text.lower()
    patterns = [
        r'清理.*记忆', r'清除.*记忆', r'忘记.*所有', r'删除.*记忆',
        r'clear.*memory', r'delete.*memory', r'forget.*everything',
        r'reset.*memory',
    ]
    return bool(re.search('|'.join(patterns), text_lower))


def detect_ttl(text: str) -> Optional[int]:
    """Detect TTL (time-to-live) in days from user message."""
    if not text:
        return None
    text_l = text.lower().strip()
    # Explicit labels
    _labels = {"永久": -1, "永远": -1, "forever": -1, "always": -1,
               "一年": 365, "半年": 180, "一个月": 30, "一周": 7,
               "1年": 365, "6个月": 180, "1个月": 30, "1周": 7}
    for label, days in _labels.items():
        if label in text_l:
            return days
    # Regex: "remember for 3 days", "keep 2 weeks", "keep a week", "keep this info for a week"
    _ttl_re = re.search(r'(\d+)\s*(天|周|月|年)(?:内|后|有效)', text_l) or \
              re.search(r'(?:保留)(\d+)\s*(天|周|月|年)', text_l) or \
              re.search(r'(?:记住|keep|remember)\D{0,15}(\d+)\s*(天|周|月|年)', text_l)
    if _ttl_re:
        u = {'天': 1, '周': 7, '月': 30, '年': 365}.get(_ttl_re.group(2), 1)
        return int(_ttl_re.group(1)) * u
    if re.search(r'(?:永久|永远|forever|always)', text_l):
        return -1
    if re.search(r'(?:下周|明天|下个月|下周六|next week|tomorrow|next month)', text_l):
        return 7
    return None


def apply_decay(confidence: float, days_since_last: float, ttl: Optional[int] = None) -> float:
    """Apply time-based decay."""
    if days_since_last <= 0:
        return confidence
    if ttl is not None:
        if ttl == -1:
            return confidence
        if ttl == 0:
            return 0.0
        if days_since_last > ttl:
            return 0.0
    if days_since_last > 90:
        return confidence * 0.3
    if days_since_last > 30:
        return confidence * 0.7
    if days_since_last > 7:
        return confidence * 0.9
    return confidence


def decay_needs_confirmation(confidence: float, ttl: Optional[int]) -> bool:
    """Check if decayed entity needs user confirmation before deletion."""
    return 0.20 < confidence < 0.40 and ttl != -1

