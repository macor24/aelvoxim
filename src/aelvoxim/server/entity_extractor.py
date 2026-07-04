# SPDX-License-Identifier: MIT
"""
metacore.entity_extractor — Rule-based entity extraction from chat turns.

Extracts named entities (person, location, technology, job, preference)
from user messages using pattern matching. Supports both Chinese and English.

Zero external dependencies. Used as fast path in routes.py's memory storage.
LLM extraction is used as fallback when rules don't match.
"""
from __future__ import annotations

import re
from typing import Dict, List

# ── Noise constants ────────────────────────────

# Chinese fragment prefixes — entities starting with these are truncation artifacts
_FRAGMENT_PREFIXES = frozenset('的于在是把被让将从以对为由该此这那各每某线')

# Chinese fragment suffixes — entities ending with these are truncation artifacts
_FRAGMENT_SUFFIXES = frozenset('的了着过中后时上下里前内外间以其该此呢吗吧的于')

# Chinese single-character names/places are extremely noisy — require min 2 chars
_MIN_CN_LENGTH = 2
# Chinese entity over 8 chars is almost certainly a sentence fragment
_MAX_CN_LENGTH = 8
# English entity over 6 words is likely a quoted sentence
_MAX_EN_WORDS = 6
# Emotion prefix tag — extract key only, not full sentence
_EMOTION_PREFIX = "情感:"
# Max total length for emotion entity value (prefix + key)
_MAX_EMOTION_LENGTH = 12
# Max Chinese chars for emotion keyword (excluding prefix label)
_MAX_EMOTION_CN = 6

# ── Technical keywords (same list as validate.py) ──

_TECH_KEYWORDS = [
    # Programming languages & frameworks
    "python", "javascript", "typescript", "java", "golang", "rust", "c++", "c#", "ruby",
    "react", "vue", "vue.js", "angular", "django", "flask", "fastapi", "spring", "express",
    "next.js", "nextjs", "nuxt", "svelte", "tailwind", "bootstrap", "jquery",
    "pytorch", "tensorflow", "keras", "jax", "scikit", "numpy", "pandas",
    # Databases
    "mysql", "postgresql", "postgres", "sqlite", "mongodb", "redis", "elasticsearch",
    "cassandra", "dynamodb", "bigquery", "mariadb", "oracle", "sql server",
    # DevOps & Cloud
    "docker", "kubernetes", "k8s", "aws", "gcp", "azure", "terraform", "ansible",
    "jenkins", "gitlab", "github", "git", "ci/cd", "nginx",
    # Tools & Platforms
    "vscode", "webpack", "vite", "esbuild", "babel", "node", "node.js", "deno",
    "graphql", "rest", "grpc", "websocket", "kafka", "rabbitmq",
    # AI/ML
    "llm", "gpt", "transformer", "bert", "diffusion", "gan", "lstm", "cnn", "rnn",
    "opencv", "yolo", "stable diffusion", "langchain", "llamaindex",
    # Concepts
    "machine learning", "deep learning", "nlp", "computer vision", "reinforcement learning",
]

_TECH_PATTERN = re.compile(
    r'\b(?:' + '|'.join(re.escape(kw) for kw in _TECH_KEYWORDS) + r')\b',
    re.IGNORECASE
)

# ── Chinese job title keywords ──

_JOB_TITLES = [
    "工程师", "程序员", "开发者", "设计师", "产品经理", "项目经理",
    "科学家", "分析师", "研究员", "教师", "教授", "医生", "律师",
    "运营", "市场", "销售", "HR", "人事", "财务", "会计",
    "学生", "实习生", "创始人", "CTO", "CEO", "总监", "经理",
    "架构师", "技术总监", "全栈", "前端", "后端", "算法",
]

_JOB_PATTERN = re.compile(
    r'(?:我是|我是一个|我是一名|我是名|我是位).{0,6}?('
    + '|'.join(re.escape(j) for j in _JOB_TITLES)
    + r')',
    re.IGNORECASE
)


def _extract_emotion_keywords(text: str) -> List[str]:
    """Extract emotional phrases from user message.

    Returns simplified labels like "情感:开心" or "情感:焦虑",
    not full sentence fragments. Max length is capped by _MAX_EMOTION_LENGTH.
    """
    if not text:
        return []
    results = []
    text_l = text.lower()
    for kw in ['超爱', '超级', '极度', '最', '绝对', '一定要', '非常重要',
               'really love', 'extremely', 'absolutely', 'must', 'hate', 'terrible',
               '烦死了', '受不了', '崩溃', '绝望', '开心死了', '太好了', '太棒了',
               '失望', '难过', '伤心', '焦虑', '担心', '害怕']:
        if kw in text_l:
            # Use only the keyword itself, not the surrounding 10 chars
            label = kw[:_MAX_EMOTION_LENGTH - len(_EMOTION_PREFIX)]
            entry = f"{_EMOTION_PREFIX}{label}"
            if entry not in results:
                results.append(entry)
    for kw in ['特别', '非常', '很喜欢', '很讨厌', 'very much', 'really like',
               '挺不错', '还可以']:
        if kw in text_l:
            entry = f"{_EMOTION_PREFIX}强烈偏好"
            if entry not in results:
                results.append(entry)
            break
    for kw in ['喜欢', '感兴趣', '不错', 'like', 'enjoy', 'interested',
               '挺好', '可以']:
        if kw in text_l and not results:
            entry = f"{_EMOTION_PREFIX}偏好"
            if entry not in results:
                results.append(entry)
            break
    return results


def _detect_sentiment(text: str) -> str:
    """Classify sentiment from user message: positive, negative, or neutral.

    Pure rule-based (no LLM). Returns sentiment label + strength (0-1).
    """
    if not text:
        return "neutral"
    text_l = text.lower()
    strong_pos = ['超爱', '太好了', '太棒了', '开心', '开心死了', 'really love', 'amazing', 'wonderful',
                  'excellent', 'fantastic', 'love it', 'perfect']
    strong_neg = ['烦死了', '受不了', '崩溃', '绝望', 'hate', 'terrible', 'awful',
                  'horrible', 'disappointed', 'miserable']
    weak_pos = ['喜欢', '不错', '挺好', '可以', '谢谢', '感谢', 'nice', 'good', 'fine', 'ok', 'happy',
                'glad', 'thank', 'thanks']
    weak_neg = ['失望', '难过', '伤心', '焦虑', '担心', '害怕', '不好', '不行',
                'bad', 'sad', 'angry', 'upset', 'sorry', 'regret']
    # Negative check first (safety: err on side of negative detection)
    for kw in strong_neg:
        if kw in text_l:
            return "negative"
    for kw in weak_neg:
        if kw in text_l:
            return "negative"
    for kw in strong_pos:
        if kw in text_l:
            return "positive"
    for kw in weak_pos:
        if kw in text_l:
            return "positive"
    return "neutral"

# ── Chinese name pattern ──

_NAME_CN_PATTERN = re.compile(
    r'(?:我叫|名字叫|称呼我为|称呼我|叫我)(.{1,20}?)(?:[，。,。！!？?】吧]|\s{2,}|我|你|他|她|它|$)'
)

# ── English name pattern ──

_NAME_EN_PATTERN = re.compile(
    r"(?:I'?m\s+|my\s+name\s+is\s+|call\s+me\s+|named\s+)([A-Z][a-zA-Z]{1,20})",
    re.IGNORECASE
)

# ── Location patterns (Chinese) ──

_LOCATION_CN_PATTERN = re.compile(
    r'(?:住在|来自|家在|居于|生活在|目前在|现在在|人在|在)([^，。,。\s做当在是]{2,12}?)'
    r'(?:工作|生活|居住|学习|发展|[，。,。]|$)'
)

# ── Location from "X人" pattern (他是阳江人 → 阳江)
_LOCATION_CN_PERSON_PATTERN = re.compile(
    r'([\u4e00-\u9fff]{2,6})人(?:$|[，。,。\s])'
)

# ── Location patterns (English) ──

_LOCATION_EN_PATTERN = re.compile(
    r"(?:I\s+(?:live\s+in|work\s+in|am\s+from|am\s+based\s+in)\s+)([A-Za-z]+(?:\s+[A-Za-z]+)?)(?=[.,!?]|\s+and|\s+but|\s+where|\s+as\s+|$)",
    re.IGNORECASE
)

# ── Preference patterns (Chinese) ──

_PREF_CN_PATTERN = re.compile(
    r'(?:最喜欢|喜欢|热爱|爱|偏好|倾向于使用|常用|习惯用)(.{2,16}?)(?:[，。,。！!？?]|$|的|和|与)'
)

# ── Preference patterns (English) ──

_PREF_EN_PATTERN = re.compile(
    r"(?:I\s+(?:like|love|prefer|enjoy|favorite|keen\s+on)\s+)([A-Za-z][A-Za-z\s]{1,20}?)(?:[.,!?]|$|\s+and|\s+but)",
    re.IGNORECASE
)

# ── Organization patterns (Chinese) ──

_ORG_CN_PATTERN = re.compile(
    r'(?:就职于|供职于|任职于|上班于)([^，。,。\s做当在是]{2,16}?)(?:工作|上班|[，。,。]|$)'
)

# ── Organization patterns (English) ──

_ORG_EN_PATTERN = re.compile(
    r"(?:I\s+work\s+(?:at|for)\s+|I'?m\s+(?:at|with)\s+)([A-Za-z][A-Za-z\s]{1,20}?)(?:[.,!?]|$|\s+as\s+)",
    re.IGNORECASE
)


# ── Noise filter ──────────────────────────────


def _is_noisy_entity(name: str, etype: str) -> bool:
    """Check if an extracted entity name looks like noise/truncation.

    Returns True to discard, False to keep.
    """
    if not name or not name.strip():
        return True
    name = name.strip()

    # Short entities (1 char) — almost always noise except technology
    if len(name) <= 1 and etype != "technology":
        return True

    # Emotion entities: must be short keyword, no sentences
    if name.startswith(_EMOTION_PREFIX):
        emotion_key = name[len(_EMOTION_PREFIX):]
        if len(name) > _MAX_EMOTION_LENGTH:
            return True
        cn_in_key = [c for c in emotion_key if '\u4e00' <= c <= '\u9fff']
        if len(cn_in_key) > _MAX_EMOTION_CN:
            return True
        # Emotion should not contain verbs or prepositions that indicate a sentence
        if any(kw in emotion_key for kw in ['在', '是', '把', '对', '为', '与', '的', '将']):
            return True
        # Exception: "了" as interjection is fine (e.g. "烦死了", "开心死了")
        # But "了" in verb phrases like "进行了"/"记录了" is aspect marker — skip if preceded by a verb verb
        if '了' in emotion_key and len(emotion_key) > 3:
            # "了" at end of keyword is interjection (e.g. "烦死了") — allow
            if emotion_key.endswith('了'):
                pass  # interjection like "烦死了"
            else:
                return True
        return False  # short emotion keywords are valid

    # Chinese entities
    if any('\u4e00' <= c <= '\u9fff' for c in name):
        cn_chars = [c for c in name if '\u4e00' <= c <= '\u9fff']
        # Too many Chinese chars = sentence fragment
        if len(cn_chars) > _MAX_CN_LENGTH:
            return True
        # Too few = likely non-entity
        if len(cn_chars) < _MIN_CN_LENGTH and etype != "technology":
            return True
        # Starts with fragment prefix = truncation artifact
        first_cn = next((c for c in name if '\u4e00' <= c <= '\u9fff'), '')
        if first_cn in _FRAGMENT_PREFIXES:
            return True

        # Chinese entity with positional markers "中"/"后"/"时" in a sentence contextntence context
        if etype in ("location", "organization") and len(cn_chars) >= 5:
            joined = ''.join(cn_chars)
            # Pattern: something + 中/后/时/期间 + something (sentence structure marker))
            if any(kw in joined[1:-1] for kw in ('中', '后', '时')):
                return True
        # Ends with fragment suffix and has no sentence-ending punctuation
        if cn_chars and cn_chars[-1] in _FRAGMENT_SUFFIXES:
            # Allow if it's a person name or technology (names can end with any char)
            if etype not in ("person", "technology"):
                return True

        # Contains Chinese sentence-ending punctuation → it's a sentence, not an entity
        if etype in ("location", "organization", "preference"):
            if any(c in name for c in '。！？；.!?;'):
                return True

    # English entities: word count check
    en_words = [w for w in name.split() if w.isascii() and w.isalpha()]
    if en_words:
        if len(en_words) > _MAX_EN_WORDS:
            return True

    return False


# ── Main extraction function ──


def extract_entities(user_msg: str, assistant_msg: str = "") -> Dict:
    """Extract entities and relations from a conversation turn.

    Supports Chinese and English in all categories.

    Args:
        user_msg: The user's message text.
        assistant_msg: The assistant's reply (used for cross-reference, not required).

    Returns:
        dict with keys:
            entities: list of {"name": str, "type": str}
            relations: list of {"subject": str, "predicate": str, "object": str}
    """
    entities: List[Dict] = []
    relations: List[Dict] = []
    seen_names: set = set()
    _seen_type_keys: set = set()

    def _add_entity(name: str, etype: str) -> None:
        name = name.strip().rstrip(".,;:!?，。；：！？")
        if not name or len(name) > 50:
            return
        # Universal noise filter: discard truncation artifacts and fragments
        if _is_noisy_entity(name, etype):
            return
        # Anti-pollution: person type filter
        if etype == "person":
            # Single char is not a real name
            if len(name) <= 1:
                return
            # Contains question/function words
            _noise_set = {'什么', '谁', '哪里', '怎么', '为什么', '如何', '哪个', '哪些', '吗'}
            for nw in _noise_set:
                if nw in name:
                    return
            _func_chars = '吗什么的你我他她它'
            if all(c in _func_chars for c in name):
                return
            # Must not start with verb-like or common sentence starters
            _name_stop_prefixes = {'请', '帮', '要', '想', '能', '可以', '给', '把', '被', '让', '在'}
            for p in _name_stop_prefixes:
                if name.startswith(p):
                    return
            # Must not contain common verbs or prepositions
            if any(v in name for v in ['帮我', '给我', '一个', '这个', '那个', '什么', '一下']):
                return
        # Strip trailing verb-like characters from location/org
        if etype in ("location", "organization", "preference"):
            _trailing = ('住', '做', '当', '在', '是', '的', '了', '着', '过')
            while name and len(name) > 2 and name[-1] in _trailing:
                name = name[:-1]
        if not name:
            return
        # Deduplicate per type (location and org can share same name)
        _key = (name, etype)
        if _key in _seen_type_keys:
            return
        # If name exists as organization but we're now adding as location, upgrade
        if etype == "location" and (name, "organization") in _seen_type_keys:
            _seen_type_keys.discard((name, "organization"))
            # Also update entities list
            for _i, _ent in enumerate(entities):
                if _ent.get("name") == name and _ent.get("type") == "organization":
                    entities[_i] = {"name": name, "type": "location"}
                    break
        _seen_type_keys.add(_key)
        seen_names.add(name)
        entities.append({"name": name, "type": etype})

    def _add_relation(sub: str, pred: str, obj: str) -> None:
        sub = sub.strip().rstrip(".,;:!?，。；：！？")
        obj = obj.strip().rstrip(".,;:!?，。；：！？")
        if sub and pred and obj:
            relations.append({"subject": sub, "predicate": pred, "object": obj})

    # 1. Extract person name (Chinese)
    for m in _NAME_CN_PATTERN.finditer(user_msg):
        name = m.group(1).strip()
        _add_entity(name, "person")
        _person_name = name
        break  # Take first name found
    else:
        _person_name = None

    # 2. Extract person name (English) — only if Chinese didn't match
    if _person_name is None:
        for m in _NAME_EN_PATTERN.finditer(user_msg):
            name = m.group(1).strip()
            _add_entity(name, "person")
            _person_name = name
            break

    # 3. Extract location (Chinese)
    for m in _LOCATION_CN_PATTERN.finditer(user_msg):
        loc = m.group(1).strip()
        _add_entity(loc, "location")
        if _person_name:
            _add_relation(_person_name, "located_in", loc)

    # 3b. Extract location from "X人" pattern
    for m in _LOCATION_CN_PERSON_PATTERN.finditer(user_msg):
        loc_candidate = m.group(1).strip()
        _add_entity(loc_candidate, "location")
        if _person_name:
            _add_relation(_person_name, "located_in", loc_candidate)

    # 4. Extract location (English)
    for m in _LOCATION_EN_PATTERN.finditer(user_msg):
        loc = m.group(1).strip()
        _add_entity(loc, "location")
        if _person_name:
            _add_relation(_person_name, "located_in", loc)

    # 5. Extract technologies
    for m in _TECH_PATTERN.finditer(user_msg):
        tech = m.group(0).strip().lower()
        _add_entity(tech, "technology")
        if _person_name:
            _add_relation(_person_name, "uses", tech)

    # 6. Extract job title (Chinese + English)
    for m in _JOB_PATTERN.finditer(user_msg):
        job = m.group(1).strip()
        _add_entity(job, "job")

    # 7. Extract preferences (Chinese)
    for m in _PREF_CN_PATTERN.finditer(user_msg):
        pref = m.group(1).strip()
        _add_entity(pref, "preference")
        if _person_name:
            _add_relation(_person_name, "likes", pref)

    # 8. Extract preferences (English)
    for m in _PREF_EN_PATTERN.finditer(user_msg):
        pref = m.group(1).strip()
        _add_entity(pref, "preference")
        if _person_name:
            _add_relation(_person_name, "likes", pref)

    # 8b. Emotional keywords → preference
    _emotion_texts = _extract_emotion_keywords(user_msg)
    for _et in _emotion_texts:
        _add_entity(_et, "preference")
        if _person_name:
            _add_relation(_person_name, "feels", _et)

    # 9. Extract organization (Chinese)
    for m in _ORG_CN_PATTERN.finditer(user_msg):
        org = m.group(1).strip()
        _add_entity(org, "organization")
        if _person_name:
            _add_relation(_person_name, "works_at", org)

    # 10. Extract organization (English)
    for m in _ORG_EN_PATTERN.finditer(user_msg):
        org = m.group(1).strip()
        _add_entity(org, "organization")
        if _person_name:
            _add_relation(_person_name, "works_at", org)

    return {"entities": entities, "relations": relations}
