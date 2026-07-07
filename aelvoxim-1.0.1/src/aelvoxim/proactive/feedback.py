"""
metacore.proactive.feedback — FeedbackLearner

Tracks whether users respond to proactive pushes and adjusts strategy.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..storage.db import execute, fetch_one, fetch_dict


class FeedbackLearner:
    """Record push events and track user responses."""

    def record_push(self, user_id: str, push_type: str, topic: str):
        """Record that a push was sent (already logged by dispatcher)."""
        pass  # logged in dispatcher.dispatch()

    def record_response(self, user_id: str, push_id: str):
        """Mark a push as responded to."""
        try:
            execute("""
                UPDATE proactive_push_log
                SET responded = TRUE
                WHERE id = %s::uuid
            """, (push_id,))
        except Exception:
            pass

    def get_accepted_count(self, user_id: str) -> int:
        """Count how many pushes this user responded to."""
        try:
            r = fetch_one("""
                SELECT COUNT(*) FROM proactive_push_log
                WHERE user_id = %s::uuid AND responded = TRUE
            """, (user_id,))
            return r[0] if r else 0
        except Exception:
            return 0

    def get_ignored_count(self, user_id: str) -> int:
        """Count pushes that were ignored."""
        try:
            r = fetch_one("""
                SELECT COUNT(*) FROM proactive_push_log
                WHERE user_id = %s::uuid AND responded = FALSE
            """, (user_id,))
            return r[0] if r else 0
        except Exception:
            return 0

    def get_last_push_id(self, session_id: str) -> str:
        """Get the most recent push ID for a session (to match response)."""
        try:
            rows = fetch_dict("""
                SELECT l.id
                FROM proactive_push_log l
                JOIN chat_sessions s ON s.id = %s
                WHERE s.user_id = l.user_id::uuid
                ORDER BY l.created_at DESC LIMIT 1
            """, (session_id,))
            if rows:
                return rows[0]["id"]
        except Exception:
            pass
        return ""
