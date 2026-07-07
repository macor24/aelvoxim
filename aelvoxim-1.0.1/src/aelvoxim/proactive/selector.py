"""
metacore.proactive.selector — BehaviorSelector

Decides what type of proactive push to send and generates content.
"""

from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from ..storage.db import fetch_dict


class BehaviorSelector:
    """Choose push type and generate content based on context."""

    def choose(
        self,
        user_id: str,
        predicted_topics: list[str],
        config: dict,
    ) -> tuple[str, str, str]:
        """
        Returns (push_type, content, topic).
        push_type: 'knowledge' | 'reminder' | 'status'
        content: the message text
        topic: related topic (may be empty)
        """
        # Priority 1: predicted topic with knowledge entry
        if predicted_topics:
            topic = predicted_topics[0]
            content = self._build_knowledge_push(topic)
            if content:
                return ("knowledge", content, topic)

        # Priority 2: status update (Learner progress)
        content = self._build_status_push()
        if content:
            return ("status", content, "")

        # Priority 3: gentle reminder
        return ("reminder", "👋 有段时间没见了，有什么新问题想聊吗？", "")

    def _build_knowledge_push(self, topic: str) -> str:
        """Build a push message about a knowledge topic."""
        try:
            rows = fetch_dict("""
                SELECT title, content FROM knowledge_entries
                WHERE topic = %s AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
            """, (topic,))
            if rows:
                title = rows[0]["title"]
                return f"💡 我最近在学「{topic}」相关的知识，比如「{title}」。想了解更多吗？"
        except Exception:
            pass
        return f"💡 关于「{topic}」我学到了一些新东西，要不要看看？"

    def _build_status_push(self) -> str:
        """Build a system status push."""
        try:
            r = fetch_dict("SELECT running, active_count FROM learner_status")
            if r:
                status = "运行中" if r[0].get("running") else "已停止"
                return f"📊 Learner {status}，{r[0].get('active_count',0)} 个方向活跃中"
            # Fallback: memory count
            r2 = fetch_dict("SELECT COUNT(*) as cnt FROM memory_entities")
            if r2:
                return f"🧠 我已经记住了 {r2[0]['cnt']} 条记忆"
        except Exception:
            pass
        return ""
