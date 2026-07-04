"""
metacore.proactive.gate — FrequencyGate

Controls push frequency per user: rate limiting, quiet hours, cooldown.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..storage.db import fetch_one, execute


PUSH_COOLDOWN_HOURS = {
    "off": 9999,
    "daily": 24,
    "every_other_day": 48,
    "weekly": 168,
}

QUIET_HOURS_DEFAULT = {"start": "22:00", "end": "08:00"}


class FrequencyGate:
    """Check if a push is allowed for this user."""

    def should_push(self, user_id: str, config: dict) -> bool:
        """Return True if a push can be sent now."""
        if not config.get("enabled", False):
            return False

        # Quiet hours check
        quiet = config.get("quiet_hours", QUIET_HOURS_DEFAULT)
        now = datetime.now()
        try:
            q_start = datetime.strptime(quiet.get("start", "22:00"), "%H:%M").time()
            q_end = datetime.strptime(quiet.get("end", "08:00"), "%H:%M").time()
        except Exception:
            q_start, q_end = None, None
        if q_start and q_end:
            if q_start <= q_end:
                # Range that crosses midnight (e.g. 22:00-08:00): block if within
                if q_start <= now.time() or now.time() <= q_end:
                    return False
            else:
                # Normal range (e.g. 09:00-17:00): block if within
                if q_start <= now.time() and now.time() <= q_end:
                    return False

        # Cooldown check
        frequency = config.get("frequency", "daily")
        cooldown_hours = PUSH_COOLDOWN_HOURS.get(frequency, 24)
        last_push = config.get("last_push")
        if last_push:
            try:
                last = datetime.fromisoformat(str(last_push).replace("Z", "+00:00"))
                if datetime.now() - last < timedelta(hours=cooldown_hours):
                    return False
            except Exception:
                pass

        return True

    def record_push(self, user_id: str):
        """Update last_push timestamp for user."""
        now = datetime.now().isoformat()
        execute("""
            UPDATE users
            SET proactive_config = jsonb_set(
                COALESCE(proactive_config, '{}'::jsonb),
                '{last_push}',
                %s::jsonb
            )
            WHERE id = %s::uuid
        """, (json.dumps(now), user_id))

import json
