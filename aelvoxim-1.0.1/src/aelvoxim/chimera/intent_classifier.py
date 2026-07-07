# SPDX-License-Identifier: MIT

"""
metacore.chimera.intent_classifier — Intent type and parameter extraction.

Classifies user input into execute, chat, or query intent types.
Extracts structured parameters for execute intents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Action


INTENT_TYPE_CHAT = "chat"
INTENT_TYPE_EXECUTE = "execute"
INTENT_TYPE_QUERY = "query"


@dataclass
class IntentResult:
    """Result of intent classification."""
    type: str = INTENT_TYPE_CHAT
    action: Optional[Action] = None
    confidence: float = 0.0
    is_question: bool = False

    @property
    def is_execute(self) -> bool:
        return self.type == INTENT_TYPE_EXECUTE

    @property
    def is_query(self) -> bool:
        return self.type == INTENT_TYPE_QUERY


# ── Keyword rules ─────────────────────────────────────

_EXECUTE_KEYWORDS: Dict[str, List[str]] = {
    "send_message": ["发消息", "发送", "发微信", "tell", "send"],
    "search_in_chrome": ["搜索", "搜一下", "查一下", "search", "find", "google"],
    "open_chrome": ["打开chrome", "打开浏览器", "open chrome", "open browser"],
    "open_website": ["打开网站", "访问", "open", "go to"],
    "delete_file": ["删除文件", "删除", "delete file", "remove"],
    "open_file": ["打开文件", "open file"],
    "save_file": ["保存文件", "save file"],
    "screenshot": ["截图", "截屏", "screenshot", "capture screen"],
}

_APP_KEYWORDS: Dict[str, List[str]] = {
    "WeChat": ["微信", "wechat", "weixin"],
    "Chrome": ["chrome", "浏览器", "browser", "google"],
    "File Explorer": ["文件管理器", "explorer", "文件夹", "directory"],
    "Excel": ["excel", "表格", "spreadsheet"],
    "Word": ["word", "文档", "document"],
}

_QUERY_PREFIXES = [
    "什么是", "怎么", "如何", "为什么", "what is", "how to", "why", "explain",
    "介绍", "tell me about", "what's the difference",
]


class IntentClassifier:
    """Classifies user input into execute, chat, or query intents.

    Usage:
        classifier = IntentClassifier()
        result = classifier.classify("帮我在Chrome搜索AI新闻")
        # IntentResult(type="execute", action=Action(task="search_in_chrome"))
    """

    def classify(self, text: str, language: str = "zh") -> IntentResult:
        """Classify the intent of user input.

        Args:
            text: User input.
            language: User language.

        Returns:
            IntentResult.
        """
        text_lower = text.lower().strip()

        # 1. Detect question
        is_question = self._detect_question(text, language)

        # 2. Detect target app
        target_app = ""
        for app, keywords in _APP_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                target_app = app
                break

        # 3. Detect execute intent
        for task, keywords in _EXECUTE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                params = self._extract_params(text, task)
                serpent_task = self._map_task(task, target_app)
                action = Action(
                    task=serpent_task,
                    target_app=target_app,
                    params=params,
                )
                return IntentResult(
                    type=INTENT_TYPE_EXECUTE,
                    action=action,
                    confidence=0.85,
                    is_question=is_question,
                )

        # 4. Detect query (knowledge-base lookup)
        if is_question or any(p in text_lower for p in _QUERY_PREFIXES):
            return IntentResult(
                type=INTENT_TYPE_QUERY,
                confidence=0.7,
                is_question=is_question,
            )

        # 5. Default: chat
        return IntentResult(
            type=INTENT_TYPE_CHAT,
            confidence=0.5,
            is_question=is_question,
        )

    def _detect_question(self, text: str, language: str) -> bool:
        """Check if input is a question."""
        question_markers = [
            "?", "？",
            "what", "how", "why", "when", "where", "who", "which",
            "can you", "could you", "would you",
            "有没有", "能不能", "会不会",
        ]
        return any(m in text.lower() for m in question_markers)

    def _extract_params(self, text: str, task: str) -> Dict[str, Any]:
        """Extract action parameters from text."""
        params: Dict[str, Any] = {}

        if task in ("send_message",):
            for prefix in ["说：", "说:", "发：", "发:", "说 ", "发 ", "发送消息：", "发送消息:"]:
                if prefix in text:
                    parts = text.rsplit(prefix, 1)
                    if len(parts) > 1 and parts[1].strip():
                        params["text"] = parts[1].strip()
                        break
            m = re.search(r"给(.+?)(说|发|发送)", text)
            if m:
                params["recipient"] = m.group(1).strip()

        elif task in ("search_in_chrome",):
            for prefix in ["搜索", "搜一下", "search", "查一下"]:
                if prefix in text:
                    parts = text.split(prefix, 1)
                    if len(parts) > 1 and parts[1].strip():
                        params["query"] = parts[1].strip()
                        break

        return params

    @staticmethod
    def _map_task(detected_task: str, target_app: str) -> str:
        """Map generic intent to Serpent task name."""
        if detected_task == "send_message" and target_app == "WeChat":
            return "send_message_in_wechat"
        if detected_task == "search_in_chrome":
            return "search_in_chrome"
        if detected_task == "open_website":
            return "navigate_to_url"
        return detected_task


__all__ = ["IntentClassifier", "IntentResult", "INTENT_TYPE_CHAT", "INTENT_TYPE_EXECUTE", "INTENT_TYPE_QUERY"]
