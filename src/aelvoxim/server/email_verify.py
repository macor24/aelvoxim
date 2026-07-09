# SPDX-License-Identifier: MIT
"""
metacore.server.email_verify — File-based email verification without external SMTP.

Stores 6-char verification codes in ~/.metacore/verification/pending.json.
Each code expires after 6 hours.

For production: replace create_verification() with SendGrid / WeCom bot call.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Optional

from ..utils import DATA_DIR

VERIFY_DIR = DATA_DIR / "verification"
VERIFY_FILE = VERIFY_DIR / "pending.json"
CODE_EXPIRY = 21600  # 6 hours in seconds


def create_verification(email: str) -> str:
    """Generate a 6-char alphanumeric verification code for an email.

    Returns the code as a string. The code is stored in pending.json
    with a 6-hour expiry. The admin retrieves it from the API response
    or the file and sends it to the user out-of-band.
    """
    code = secrets.token_hex(3).upper()
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if VERIFY_FILE.exists():
        try:
            data = json.loads(VERIFY_FILE.read_text())
        except Exception:
            data = {}
    data[email] = {"code": code, "expires": time.time() + CODE_EXPIRY}
    VERIFY_FILE.write_text(json.dumps(data, indent=2))
    return code


def verify_email(email: str, code: str) -> bool:
    """Verify a code for an email. Returns True if valid, False otherwise.

    Consumes the code (removes from pending.json) regardless of result.
    """
    if not VERIFY_FILE.exists():
        return False
    try:
        data = json.loads(VERIFY_FILE.read_text())
    except Exception:
        return False

    entry = data.pop(email, None)
    if not entry:
        VERIFY_FILE.write_text(json.dumps(data, indent=2))
        return False

    now = time.time()
    if now > entry.get("expires", 0):
        VERIFY_FILE.write_text(json.dumps(data, indent=2))
        return False

    valid = entry.get("code", "") == code
    VERIFY_FILE.write_text(json.dumps(data, indent=2))
    return valid


def mark_user_verified(email: str) -> bool:
    """Mark a user as verified in the user database.

    Returns True if the user was found and marked, False otherwise.
    """
    try:
        from .auth import find_by_email, _save_user
        from datetime import datetime

        user = find_by_email(email)
        if not user:
            return False
        user["verified"] = True
        user["updated_at"] = datetime.now().isoformat()
        _save_user(user)
        return True
    except Exception:
        return False


def cleanup_expired() -> int:
    """Remove expired entries from pending.json. Returns count removed."""
    if not VERIFY_FILE.exists():
        return 0
    try:
        data = json.loads(VERIFY_FILE.read_text())
    except Exception:
        return 0
    now = time.time()
    before = len(data)
    data = {k: v for k, v in data.items() if v.get("expires", 0) > now}
    removed = before - len(data)
    if removed > 0:
        VERIFY_FILE.write_text(json.dumps(data, indent=2))
    return removed
