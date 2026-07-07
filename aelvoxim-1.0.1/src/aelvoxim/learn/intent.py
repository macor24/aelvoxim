"""
metacore.learn.intent — Intent parser for complex user queries.

Detects compound questions (multiple independent requests in a single message),
decomposes them into ordered sub-intents, and classifies each sub-intent
by task type for better response structuring.

Pure rule-based, no LLM calls.
"""

from __future__ import annotations

import re
from typing import Dict, List


# ── Compound query signals ──────────────────────────────────

# Connector words that join independent requests
_COMPOUND_CONNECTORS = [
    "然后", "接着", "再来", "再", "还有", "顺便", "另外",
    "然后呢", "接下来", "之后",
    "then", "next", "and then", "also", "additionally",
    "after that", "plus", "furthermore", "moreover",
]

# Complementary patterns: two distinct action verbs in one query
_DUAL_ACTION = re.compile(
    r"(?:写|创建|实现|生成|构建|做|画|绘制|发送|上传|下载|转换|翻译|分析|检查|查|看|比较|测试|部署|优化|重构|迁移|合并|拆分|"
    r"write|create|implement|build|generate|analyze|check|compare|test|deploy|optimize|refactor|merge|split|send|upload|download|convert|translate|draw|plot|make|do"
    r")"
    r".{2,80}"
    r"(?:然后|接着|再|还有|顺便|and|then|next|also)"
    r".{0,80}"
    r"(?:写|创建|实现|生成|构建|做|画|绘制|发送|上传|下载|转换|翻译|分析|检查|查|看|比较|测试|部署|优化|重构|迁移|合并|拆分|"
    r"write|create|implement|build|generate|analyze|check|compare|test|deploy|optimize|refactor|merge|split|send|upload|download|convert|translate|draw|plot|make|do"
    r")",
    re.IGNORECASE,
)

# Pattern: numbered steps like "1. ... 2. ..." or "first ... second ..."
_STEPPED_PATTERN = re.compile(
    r"(?:第[一二三四五六七八九十\d]|[\(（]\d+[\)）]|step\s+\d+|(?:first|second|third|fourth|fifth))",
    re.IGNORECASE,
)

# Pattern: multiple sentences each with an action verb
_MULTI_SENTENCE_ACTION = re.compile(
    r"[,，;；。.!！?？]\s*(?:请|帮我|帮|帮我|可以|能|能否|能不能)",
)

# Task type keywords for each sub-intent
_CODE_KEYWORDS = {"写", "创建", "实现", "生成", "构建", "修复", "重构", "优化", "调试", "部署",
                  "write", "create", "implement", "build", "generate", "refactor",
                  "fix", "debug", "deploy", "optimize"}

_ANALYSIS_KEYWORDS = {"分析", "比较", "检查", "评估", "测试", "验证", "对比",
                       "analyze", "compare", "check", "evaluate", "test",
                       "verify", "validate", "review"}

_QUERY_KEYWORDS = {"查", "看", "查一下", "看看", "查询", "搜索", "告诉我",
                    "what", "how", "why", "when", "where", "who",
                    "tell me", "show me", "find", "search", "look up"}


class IntentParser:
    """Detect complex vs simple intents, decompose compound requests."""

    @staticmethod
    def is_compound(query: str) -> bool:
        """Detect if a query contains multiple independent requests.

        Returns True if the query has:
        - Multiple action verbs connected by connectors
        - Numbered/stepped structure
        - Multiple sentences each with distinct actions
        - Very long queries (>200 chars) likely containing multiple topics
        """
        if not query:
            return False
        q = query.strip()

        # Rule 1: Dual action pattern with connector
        if _DUAL_ACTION.search(q):
            return True

        # Rule 2: Numbered steps
        if _STEPPED_PATTERN.search(q) and len(q) > 15:
            return True

        # Rule 3: Multiple sentence actions
        if _MULTI_SENTENCE_ACTION.search(q):
            return True

        # Rule 4: Very long query with multiple clauses
        if len(q) > 200:
            return True

        return False

    @staticmethod
    def decompose(query: str) -> List[Dict]:
        """Split a compound query into ordered sub-intents.

        Returns:
            List of dicts: {"step": int, "sub_query": str, "task_type": str}
        """
        if not query:
            return []

        q = query.strip()

        # Try splitting by numbered steps first
        segments = IntentParser._split_by_steps(q)
        if len(segments) >= 2:
            return [{"step": i + 1, "sub_query": s.strip(), "task_type": IntentParser._detect_task_type(s)}
                    for i, s in enumerate(segments)]

        # Try splitting by connectors
        segments = IntentParser._split_by_connectors(q)
        if len(segments) >= 2:
            return [{"step": i + 1, "sub_query": s.strip(), "task_type": IntentParser._detect_task_type(s)}
                    for i, s in enumerate(segments)]

        # Fallback: single intent
        return [{"step": 1, "sub_query": q, "task_type": IntentParser._detect_task_type(q)}]

    @staticmethod
    def _split_by_steps(query: str) -> List[str]:
        """Split on numbered markers: '1. ... 2. ...' or 'first ... then ...'."""
        parts = re.split(
            r'(?:^|[。.!！?？\n])?\s*(?:第[一二三\d]|[\(（]\d+[\)）]|step\s+\d+)',
            query,
            flags=re.IGNORECASE,
        )
        return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]

    @staticmethod
    def _split_by_connectors(query: str) -> List[str]:
        """Split on Chinese connector words between action verbs."""
        # Build a pattern: look for connector between two action phrases
        parts = re.split(
            r"(?:然后|接着|再来|还有|顺便|另外|then|next|and then|also|additionally)",
            query,
            flags=re.IGNORECASE,
        )
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _detect_task_type(sub_query: str) -> str:
        """Classify a sub-query as code / analysis / query."""
        words = set(sub_query.lower().split())
        # Count keyword overlap
        code_score = sum(1 for k in _CODE_KEYWORDS if k in sub_query)
        analysis_score = sum(1 for k in _ANALYSIS_KEYWORDS if k in sub_query)
        query_score = sum(1 for k in _QUERY_KEYWORDS if k in sub_query)

        if code_score >= analysis_score and code_score >= query_score and code_score > 0:
            return "code"
        if analysis_score >= query_score and analysis_score > 0:
            return "analysis"
        return "general"
