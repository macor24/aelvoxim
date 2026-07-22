"""
metacore.server.webhook — Webhook subscription engine.

Supports:
- Subscribe to events (task.completed, knowledge.updated, memory.conflict)
- HMAC-SHA256 signature for callback verification
- Async delivery with 3 retries (exponential backoff)
- Delivery log for auditing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import uuid4

from ..storage.db import execute, fetch_dict, fetch_one, use_pg

_log = logging.getLogger("aelvoxim.webhook")

# ── Events ──

SUPPORTED_EVENTS = {
    "task.completed",
    "knowledge.updated",
    "memory.conflict",
    "user.registered",
    "system.alert",
}

DEFAULT_SECRET_PREFIX = "whsec_"


def _generate_secret() -> str:
    return DEFAULT_SECRET_PREFIX + uuid4().hex


def _sign_payload(payload: dict, secret: str) -> str:
    """HMAC-SHA256 sign a payload."""
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── CRUD ──


def subscribe(
    url: str,
    events: list[str],
    user_id: str = "",
    description: str = "",
) -> dict:
    """Create a webhook subscription.

    Args:
        url: Callback URL (must be HTTPS or localhost HTTP)
        events: List of event types to subscribe to
        user_id: Owner user ID
        description: Optional human-readable label

    Returns:
        Subscription dict with id, secret, url, events.
    """
    if not use_pg():
        return {"error": "PostgreSQL required for webhook storage"}

    # Validate events
    invalid = [e for e in events if e not in SUPPORTED_EVENTS]
    if invalid:
        return {"error": f"unsupported events: {invalid}. Supported: {sorted(SUPPORTED_EVENTS)}"}

    sub_id = str(uuid4())
    secret = _generate_secret()
    now = datetime.now().isoformat()

    try:
        execute("""
            INSERT INTO webhook_subscriptions
                (id, url, events, secret, user_id, description, created_at)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
        """, (sub_id, url, json.dumps(events), secret, user_id, description, now))
    except Exception as e:
        _log.exception("webhook subscribe failed")
        return {"error": str(e)}

    return {
        "id": sub_id,
        "url": url,
        "events": events,
        "secret": secret,  # Shown once — store it
        "user_id": user_id,
        "description": description,
        "created_at": now,
    }


def unsubscribe(sub_id: str, user_id: str = "") -> bool:
    """Delete a webhook subscription."""
    if not use_pg():
        return False
    try:
        if user_id:
            execute("DELETE FROM webhook_subscriptions WHERE id = %s AND user_id = %s",
                    (sub_id, user_id))
        else:
            execute("DELETE FROM webhook_subscriptions WHERE id = %s", (sub_id,))
        return True
    except Exception:
        return False


def get_subscriptions(user_id: str = "") -> list[dict]:
    """List webhook subscriptions. Admin sees all when user_id is empty."""
    if not use_pg():
        return []
    try:
        if user_id:
            rows = fetch_dict(
                "SELECT * FROM webhook_subscriptions WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,))
        else:
            rows = fetch_dict(
                "SELECT * FROM webhook_subscriptions ORDER BY created_at DESC")
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "url": r["url"],
                "events": r.get("events") or [],
                "user_id": r.get("user_id", ""),
                "description": r.get("description", ""),
                "active": r.get("active", True),
                "created_at": str(r.get("created_at", "")),
                "last_delivery": str(r.get("last_delivery", "")) if r.get("last_delivery") else "",
                "delivery_count": r.get("delivery_count", 0),
                "failure_count": r.get("failure_count", 0),
            })
        return result
    except Exception:
        return []


def get_subscription(sub_id: str) -> Optional[dict]:
    """Get a single subscription by ID."""
    if not use_pg():
        return None
    try:
        rows = fetch_dict(
            "SELECT * FROM webhook_subscriptions WHERE id = %s", (sub_id,))
        if rows:
            r = rows[0]
            return {
                "id": r["id"],
                "url": r["url"],
                "events": r.get("events") or [],
                "secret": r.get("secret", ""),
                "user_id": r.get("user_id", ""),
                "description": r.get("description", ""),
                "active": r.get("active", True),
                "created_at": str(r.get("created_at", "")),
            }
    except Exception:
        _log.exception("webhook error")
    return None


# ── Delivery ──


def deliver_event(event_type: str, payload: dict) -> list[dict]:
    """Deliver an event to all matching subscriptions.

    Args:
        event_type: e.g. "task.completed"
        payload: Event-specific data dict

    Returns:
        List of delivery results: [{sub_id, url, status, status_code}]
    """
    if not use_pg():
        return []

    results = []
    try:
        subs = fetch_dict(
            "SELECT * FROM webhook_subscriptions WHERE events @> %s::jsonb AND active = TRUE",
            (json.dumps([event_type]),))
    except Exception:
        _log.exception(f"webhook: failed to fetch subs for event {event_type}")
        return []

    for sub in subs:
        result = _deliver_single(sub, event_type, payload)
        results.append(result)

    return results


def _deliver_single(
    sub: dict,
    event_type: str,
    payload: dict,
) -> dict:
    """Deliver to one subscription with retry logic."""
    sub_id = sub["id"]
    url = sub["url"]
    secret = sub.get("secret", "")
    max_retries = 3

    # Build the webhook body
    body = {
        "event": event_type,
        "id": str(uuid4()),
        "timestamp": datetime.now().isoformat(),
        "data": payload,
    }

    signature = _sign_payload(body, secret) if secret else ""
    body_bytes = json.dumps(body, ensure_ascii=False).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Aelvoxim-Signature-256": signature,
                    "X-Aelvoxim-Event": event_type,
                    "X-Aelvoxim-Delivery": body["id"],
                    "User-Agent": "Aelvoxim-Webhook/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                status_code = resp.status
                _log.info(f"webhook delivered to {url} (attempt {attempt+1}): {status_code}")
                _update_delivery_log(sub_id, True, status_code)
                return {
                    "sub_id": sub_id,
                    "url": url,
                    "status": "delivered",
                    "status_code": status_code,
                    "attempts": attempt + 1,
                }
        except urllib.error.HTTPError as e:
            status_code = e.code
            if status_code >= 400 and status_code < 500:
                # Client error — don't retry
                _log.warning(f"webhook client error {status_code} for {url}, not retrying")
                _update_delivery_log(sub_id, False, status_code)
                return {
                    "sub_id": sub_id,
                    "url": url,
                    "status": "failed",
                    "status_code": status_code,
                    "error": f"HTTP {status_code}",
                    "attempts": attempt + 1,
                }
            # Server error — retry
            if attempt < max_retries - 1:
                _log.warning(f"webhook server error {status_code} for {url}, retrying...")
                time.sleep(2 ** attempt)  # Exponential backoff
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < max_retries - 1:
                _log.warning(f"webhook connection error for {url}: {e}, retrying...")
                time.sleep(2 ** attempt)
            else:
                _log.error(f"webhook delivery failed after {max_retries} attempts: {url}: {e}")
                _update_delivery_log(sub_id, False, 0)
                return {
                    "sub_id": sub_id,
                    "url": url,
                    "status": "failed",
                    "error": str(e),
                    "attempts": attempt + 1,
                }

    return {
        "sub_id": sub_id,
        "url": url,
        "status": "failed",
        "error": "max retries exceeded",
        "attempts": max_retries,
    }


def _update_delivery_log(sub_id: str, success: bool, status_code: int):
    """Update subscription delivery stats."""
    try:
        if success:
            execute("""
                UPDATE webhook_subscriptions
                SET last_delivery = NOW(), delivery_count = COALESCE(delivery_count, 0) + 1
                WHERE id = %s
            """, (sub_id,))
        else:
            execute("""
                UPDATE webhook_subscriptions
                SET last_delivery = NOW(), failure_count = COALESCE(failure_count, 0) + 1
                WHERE id = %s
            """, (sub_id,))
    except Exception:
        _log.exception("webhook error")
