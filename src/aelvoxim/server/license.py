# SPDX-License-Identifier: MIT
"""
metacore.server.license — HMAC-SHA256 license key verification.

Generates and verifies license keys for Pro/Enterprise plans.
Keys are HMAC-signed with a server-side secret.

Format: plan:expires_timestamp:hex_signature

Usage:
    generate_license("pro", 365)  -> "pro:1735689600:a1b2c3d4e5f6..."
    verify_license(key)           -> {"valid": True, "plan": "pro", ...}
    current_edition()             -> "community" (from env or license file)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

# Secret key — MUST set AELVOXIM_LICENSE_SECRET in production
_SECRET = os.environ.get("AELVOXIM_LICENSE_SECRET")
if not _SECRET:
    import logging
    logging.getLogger("aelvoxim.license").warning(
        "AELVOXIM_LICENSE_SECRET not set — license verification disabled, defaulting to community edition"
    )

from ..utils import DATA_DIR

LICENSE_FILE = DATA_DIR / "license.json"


def generate_license(plan: str, expires_days: int = 365) -> str:
    """Generate an HMAC-SHA256 license key.

    Args:
        plan: Plan name (starter, growth, pro, enterprise, flagship).
        expires_days: Days from now until expiration.

    Returns:
        License key string: "plan:expires_ts:signature[:optional_features]"
    """
    expires_ts = int(time.time()) + expires_days * 86400
    payload = f"{plan}:{expires_ts}"
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


def verify_license(key: str) -> Dict[str, Any]:
    """Verify a license key.

    Args:
        key: License key string.

    Returns:
        Dict with keys: valid (bool), plan (str), expires_at (int),
        reason (str, present only if invalid).
    """
    try:
        parts = key.split(":")
        plan = parts[0]
        expires_ts = int(parts[1])
        sig = parts[2]
    except (IndexError, ValueError):
        return {"valid": False, "plan": "community", "reason": "Malformed license key"}

    # Validate plan
    from .auth import PLANS
    if plan not in PLANS:
        return {"valid": False, "plan": "community", "reason": f"Unknown plan: {plan}"}

    # Validate signature
    payload = f"{plan}:{expires_ts}"
    expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return {"valid": False, "plan": "community", "reason": "Invalid signature"}

    # Validate expiration
    now = time.time()
    if now > expires_ts:
        return {"valid": False, "plan": "community", "reason": "License expired"}

    return {"valid": True, "plan": plan, "expires_at": expires_ts}


def save_license(key: str) -> bool:
    """Save a license key to disk. Returns True if valid."""
    result = verify_license(key)
    if not result.get("valid"):
        return False
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "key": key,
        "plan": result["plan"],
        "expires_at": result["expires_at"],
        "saved_at": int(time.time()),
    }
    LICENSE_FILE.write_text(json.dumps(data, indent=2))
    return True


def load_license() -> Dict[str, Any]:
    """Load saved license from disk.

    Returns:
        Dict with plan (str), is_valid (bool), reason (str if invalid).
    """
    if not LICENSE_FILE.exists():
        return {"plan": "community", "is_valid": False, "reason": "No license file"}

    try:
        data = json.loads(LICENSE_FILE.read_text())
        key = data.get("key", "")
        if not key:
            return {"plan": "community", "is_valid": False, "reason": "Empty license key in file"}
        result = verify_license(key)
        return {
            "plan": result.get("plan", "community"),
            "is_valid": result.get("valid", False),
            "reason": result.get("reason", ""),
            "expires_at": result.get("expires_at", 0),
            "saved_at": data.get("saved_at", 0),
        }
    except (json.JSONDecodeError, OSError):
        return {"plan": "community", "is_valid": False, "reason": "Corrupt license file"}


def apply_license(key: str) -> bool:
    """Verify a license key and apply the edition.

    On success, sets the runtime edition to the license plan.
    Returns True if valid and applied.
    """
    result = verify_license(key)
    if result.get("valid"):
        from .edition import set_edition
        set_edition(result["plan"])
        return True
    return False


def current_edition() -> str:
    """Get current runtime edition.

    Priority:
    1. AELVOXIM_EDITION env var (explicit override, handled by edition.py)
    2. Valid license file
    3. Default: community
    """
    from .edition import current as _ed_current
    _ed = _ed_current()
    if _ed and _ed != "community":
        return _ed
    lic = load_license()
    if lic.get("is_valid"):
        return lic["plan"]
    return "community"


# ── Trial license for self-hosted new users ──
from .auth import TRIAL_DAYS

TRIAL_DIR = DATA_DIR / "trials"


def create_trial_license(email: str) -> int:
    """Create a 30-day full-feature trial for a new user.

    Args:
        email: User's email address.

    Returns:
        expires_at (unix timestamp). Existing trial is not overwritten.
    """
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)
    trial_file = TRIAL_DIR / f"{email.lower().strip()}.json"
    if trial_file.exists():
        # Already has a trial — return existing expiry
        try:
            data = json.loads(trial_file.read_text())
            return data.get("expires_at", 0)
        except Exception:
            pass
    expires_at = int(time.time()) + TRIAL_DAYS * 86400
    record = {
        "email": email.lower().strip(),
        "plan": "trial",
        "created_at": int(time.time()),
        "expires_at": expires_at,
    }
    trial_file.write_text(json.dumps(record, indent=2))
    from .audit import log as _audit_log
    _audit_log("user.trial_created", user=email, detail={"days": TRIAL_DAYS, "expires_at": expires_at})
    return expires_at


def check_trial_expiry(user: dict) -> dict:
    """Check if a user's trial has expired and downgrade if so.

    Args:
        user: User dict with 'email' and 'plan' keys.

    Returns:
        Updated user dict (plan may be downgraded to 'community').
    """
    if user.get("plan") != "trial":
        return user
    email = user.get("email", "").lower().strip()
    trial_file = TRIAL_DIR / f"{email}.json"
    if not trial_file.exists():
        # No trial record — shouldn't happen, but downgrade safely
        user["plan"] = "community"
        return user
    try:
        data = json.loads(trial_file.read_text())
        expires_at = data.get("expires_at", 0)
        if time.time() > expires_at:
            user["plan"] = "community"
            from .audit import log as _audit_log
            _audit_log("user.trial_expired", user=email)
    except Exception:
        user["plan"] = "community"
    return user
