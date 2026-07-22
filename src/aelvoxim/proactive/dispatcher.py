"""
metacore.proactive.dispatcher — ChannelDispatcher

Sends proactive messages through the appropriate channel.
Currently supports: chat (through ChatAEL API).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime

from ..storage.db import execute, use_pg


import logging
_log = logging.getLogger("aelvoxim.proactive.dispatcher")

class ChannelDispatcher:
    """Dispatch proactive messages to users through available channels."""

    def dispatch(
        self,
        user_id: str,
        email: str,
        push_type: str,
        content: str,
        topic: str = "",
    ) -> bool:
        """
        Send a proactive message. Currently pushes to chat via ChatAEL.
        Returns True if dispatched successfully.
        """
        # Log the push attempt
        if use_pg():
            try:
                execute("""
                    INSERT INTO proactive_push_log
                        (user_id, push_type, content, topic, channel)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, push_type, content[:500], topic, "chat"))
            except Exception:
                _log.exception("dispatcher error")

        # Actually push to the user's ChatAEL session
        return self._push_to_chat(user_id, content)

    def _push_to_chat(self, user_id: str, content: str) -> bool:
        """
        Push a message into the user's ChatAEL conversation.
        This creates a system message in their active session.
        """
        try:
            # Find the user's most recent active session
            from ..storage.db import fetch_dict
            sessions = fetch_dict("""
                SELECT id FROM chat_sessions
                WHERE user_id = %s::uuid
                ORDER BY updated_at DESC LIMIT 1
            """, (user_id,))
            if not sessions:
                return False

            session_id = sessions[0]["id"]
            # Insert a system message
            execute("""
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (%s, 'system', %s)
            """, (session_id, content))

            # Update session timestamp
            execute("""
                UPDATE chat_sessions SET updated_at = NOW()
                WHERE id = %s
            """, (session_id,))
            return True
        except Exception:
            return False
