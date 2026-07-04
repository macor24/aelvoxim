"""
metacore.proactive.detector — SilenceDetector

Finds users who haven't sent any message for more than `min_hours`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..storage.db import fetch_dict, use_pg


def find_silent_users(min_hours: int = 24) -> list[dict[str, Any]]:
    """
    Return users whose last chat message is older than min_hours,
    AND who have proactive enabled in their config.
    """
    if not use_pg():
        return []

    cutoff = datetime.now() - timedelta(hours=min_hours)
    rows = fetch_dict("""
        SELECT u.id, u.email, u.proactive_config
        FROM users u
        WHERE (
            SELECT MAX(cm.created_at)
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cs.user_id = u.id
        ) < %s
        OR (
            SELECT COUNT(*) FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cs.user_id = u.id
        ) = 0
    """, (cutoff,))
    # Filter: only those with proactive enabled
    result = []
    for r in rows:
        cfg = r.get("proactive_config")
        if isinstance(cfg, str):
            import json
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = {}
        if isinstance(cfg, dict) and cfg.get("enabled", False):
            r["proactive_config"] = cfg
            result.append(r)
    return result
