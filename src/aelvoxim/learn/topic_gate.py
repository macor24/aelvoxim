"""aelvoxim.learn.topic_gate — keep chat text out of the learning direction set.

Why this exists (2026-10-26): user chat messages were stored as knowledge topics
(``raw_topic``), then ``discover.suggest_directions_from_knowledge`` promoted
``source=user_chat`` topics into learning directions. One chat request therefore
turned into an autonomous direction that generated hundreds of entries:

    "कृपया मुझे दो पंक्तियों में एक छोटी कविता सुनाइए।"        -> 28 cycles
    "You are a content writer for Zenttop, a Chinese kitchen..." -> 184 cycles
    "你的意思是把客户资料给agent他帮我发邮件是吗？..."           ->   9 cycles

Rules are deliberately conservative: a real research topic is a short noun phrase.
A question, a pasted prompt, a phone number, a full sentence, or anything on the
operator denylist is not a topic.

Stdlib only — importable on its own (tests, ops scripts) without the package.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Tuple

_log = logging.getLogger("aelvoxim.learn.topic_gate")

_DATA_DIR = Path(os.environ.get("AELVOXIM_DATA_DIR") or (Path.home() / ".aelvoxim"))
DENY_FILE = _DATA_DIR / "learning_denylist.json"

# Fallback when the operator file is absent. The file (if present) replaces this.
DEFAULT_DENY: Tuple[str, ...] = (
    "zenttop", "kitchen knife", "alibaba", "阿里巴巴", "阿里文案", "刀具工厂",
    "hindi", "kavita", "कविता", "devanagari",
    "拓客", "客户线索", "自动拓客", "发邮件", "whatsapp", "watsap",
)

MAX_TOPIC_LEN = 80
MIN_TOPIC_LEN = 3

_PROMPT_PREFIXES = (
    "you are ", "you are a", "act as ", "please ", "please,", "write a ",
    "你是", "请你", "请写", "请帮", "请为", "कृपया",
)
_QUESTION_TAILS = ("吗", "呢", "吧", "啊", "么", "？", "?")
_SENTENCE_MARKS = ("，", "。", "；", "！")
# +90 5xx xxx xxxx / +905****5179 / 13800138000
_PHONE_RE = re.compile(r"\+?\d[\d\s*\-()]{5,}\d")
# Sentence openers. "自我进化" must survive, so only a LEADING pronoun counts.
_PERSON_LEADS = ("我", "你", "俺", "咱", "我们", "你们")
# Auto-generated plan-completion titles ("已完成计划: 深入学习 深入学习 ...")
_PLAN_TITLE_PREFIXES = ("已完成计划", "完成计划:", "完成计划：")

_deny_cache: List[str] | None = None


def deny_terms() -> List[str]:
    """Denylist terms: operator file wins, built-in defaults as fallback."""
    global _deny_cache
    if _deny_cache is not None:
        return _deny_cache
    terms: List[str] = list(DEFAULT_DENY)
    try:
        if DENY_FILE.exists():
            data = json.loads(DENY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("terms", [])
            if isinstance(data, list):
                terms = [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        _log.exception("denylist load failed, using defaults: %s", DENY_FILE)
    _deny_cache = terms
    return terms


def reload_denylist() -> None:
    global _deny_cache
    _deny_cache = None


def check(topic: str) -> Tuple[bool, str]:
    """Return (learnable, reason). reason == "ok" when learnable."""
    t = (topic or "").strip()
    if len(t) < MIN_TOPIC_LEN:
        return False, "too_short"
    if "\n" in t or "\r" in t:
        return False, "multiline"
    if len(t) > MAX_TOPIC_LEN:
        return False, "too_long"
    low = t.lower()
    for prefix in _PROMPT_PREFIXES:
        if low.startswith(prefix):
            return False, "prompt_like"
    if "?" in t or "？" in t:
        return False, "question"
    if t.endswith(_QUESTION_TAILS):
        return False, "question_tail"
    if any(m in t for m in _SENTENCE_MARKS):
        return False, "chat_sentence"
    if low.startswith(_PERSON_LEADS):
        return False, "first_second_person"
    if any(t.startswith(p) for p in _PLAN_TITLE_PREFIXES):
        return False, "plan_title"
    if _PHONE_RE.search(t):
        return False, "phone_number"
    for term in deny_terms():
        if term.lower() in low:
            return False, "denylist:%s" % term
    return True, "ok"


def is_learnable(topic: str) -> bool:
    """Convenience wrapper for call sites that only need a boolean."""
    return check(topic)[0]
