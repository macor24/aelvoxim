"""
metacore.proactive.predictor — TopicPredictor

Predicts next topics a user might be interested in,
based on query_tracker history.
"""

from __future__ import annotations

from typing import Any

from ..storage.db import fetch_dict


class TopicPredictor:
    """Predict topics a user may want to hear about next."""

    def predict(self, user_id: str, limit: int = 3) -> list[str]:
        """
        Return predicted topic strings for this user.
        Uses query_tracker data or recent knowledge as fallback.
        """
        topics = []

        # 1. Try query_tracker predictions
        try:
            from ..server.query_tracker import predict_next_topics
            pred = predict_next_topics(hours=48)
            topics = pred.get("predictions", [])
        except Exception:
            pass

        # 2. Fallback: most active knowledge topics
        if not topics:
            try:
                rows = fetch_dict("""
                    SELECT topic, COUNT(*) as cnt
                    FROM knowledge_entries
                    WHERE status = 'active'
                    GROUP BY topic
                    ORDER BY cnt DESC
                    LIMIT %s
                """, (limit,))
                topics = [r["topic"] for r in rows]
            except Exception:
                pass

        return topics[:limit]
