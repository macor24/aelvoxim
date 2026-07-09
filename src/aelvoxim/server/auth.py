"""
metacore.server.auth — API Key authentication + user management

Dual-mode storage: PostgreSQL (when available) or JSON file fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import bcrypt
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..utils import METACORE_DIR, ensure_dir
from ..storage.db import execute, fetch_one, fetch_all, use_pg, get_pool

# ── Admin key ──
ADMIN_KEY = os.environ.get("AELVOXIM_ADMIN_KEY", "")

USERS_DIR = METACORE_DIR / "users"
ensure_dir(USERS_DIR)

API_KEY_PREFIX = "sk-aelvoxim-"

# ── Plans ──
PLANS = {
    "community":  {"tasks_per_month": 1000,   "max_directions": 1,   "max_kb_entries": 500,    "max_api_keys": 1,   "memory_mb": 10,   "max_experts": 6},
    "starter":    {"tasks_per_month": 10000,  "max_directions": 3,   "max_kb_entries": 5000,   "max_api_keys": 3,   "memory_mb": 1000, "max_experts": 8},
    "growth":     {"tasks_per_month": 50000,  "max_directions": 6,   "max_kb_entries": 20000,  "max_api_keys": 5,   "memory_mb": 5000, "max_experts": 8},
    "pro":        {"tasks_per_month": 100000, "max_directions": 10,  "max_kb_entries": 100000, "max_api_keys": 10,  "memory_mb": 10000,"max_experts": 8},
    "enterprise": {"tasks_per_month": 1000000,"max_directions": 999, "max_kb_entries": 999999, "max_api_keys": 999, "memory_mb": 100000,"max_experts": 8},
    "flagship":   {"tasks_per_month": 9999999,"max_directions": 9999,"max_kb_entries": 9999999,"max_api_keys": 9999,"memory_mb": 999999,"max_experts": 8},
    "trial":      {"tasks_per_month": 9999999,"max_directions": 9999,"max_kb_entries": 9999999,"max_api_keys": 9999,"memory_mb": 999999,"max_experts": 12},
}

TRIAL_DAYS = 30  # New users get 30-day full-feature trial


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


# ── Password helpers ──


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (salt + key stretching)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), stored.encode())
    except (ValueError, AttributeError):
        return False


# ── API Key helpers ──


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_hex(24)


def _user_path(api_key: str) -> Path:
    suffix = api_key[-16:]
    return USERS_DIR / f"{suffix}.json"


def _user_to_dict(row: tuple) -> dict:
    """Convert PG row to dict matching old JSON format."""
    return {
        "api_key": row[0],
        "email": row[1],
        "username": row[2] or "",
        "password_hash": row[3],
        "plan": row[4] or "community",
        "role": row[5] or "user",
        "verified": row[6] or False,
        "api_keys": row[7] if isinstance(row[7], list) else [],
        "monthly_usage": row[8] if isinstance(row[8], dict) else {},
        "created_at": str(row[9]) if row[9] else "",
        "updated_at": str(row[10]) if row[10] else "",
    }


def _user_to_dict_pg(u: dict) -> dict:
    """Convert PG RealDict row to dict matching old JSON format."""
    return {
        "api_key": u.get("api_key", ""),
        "email": u.get("email", ""),
        "username": u.get("username", "") or "",
        "password_hash": u.get("password_hash", ""),
        "plan": u.get("plan", "community"),
        "role": u.get("role", "user"),
        "verified": u.get("verified", False),
        "api_keys": u.get("api_keys") or [],
        "monthly_usage": u.get("monthly_usage") or {},
        "created_at": str(u.get("created_at", "")),
        "updated_at": str(u.get("updated_at", "")),
    }


# ── User CRUD (dual-mode) ──


def list_all_users() -> list[dict]:
    """Load all users — PG + JSON combined. PG takes priority (no duplicates by email)."""
    return _all_users()

def _all_users() -> list[dict]:
    """Load all users — PG + JSON combined. PG takes priority (no duplicates by email)."""
    seen_emails = set()
    users = []

    # PG first
    if use_pg():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict("""
                SELECT * FROM users ORDER BY created_at DESC
            """)
            if rows is not None:
                for r in rows:
                    u = _user_to_dict_pg(r)
                    seen_emails.add(u.get("email", "").lower())
                    users.append(u)
        except Exception:
            pass

    # JSON fallback — skip emails already in PG
    for f in sorted(USERS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            u = json.loads(f.read_text())
            email = u.get("email", "").lower()
            if email and email not in seen_emails:
                seen_emails.add(email)
                users.append(u)
        except Exception:
            pass
    return users


def find_user(api_key: str) -> Optional[dict]:
    """Find user by API key."""
    if use_pg():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict("""
                SELECT * FROM users WHERE api_keys @> %s::jsonb
            """, (json.dumps([api_key]),))
            if rows and len(rows) > 0:
                return _user_to_dict_pg(rows[0])
        except Exception:
            pass
    # JSON fallback
    path = _user_path(api_key)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def find_by_email(email: str) -> Optional[dict]:
    email_lower = email.lower().strip()
    if use_pg():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict("SELECT * FROM users WHERE email = %s", (email_lower,))
            if rows and len(rows) > 0:
                return _user_to_dict_pg(rows[0])
        except Exception:
            pass
    for user in _all_users():
        if user.get("email", "").lower() == email_lower:
            return user
    return None


def find_by_username(username: str) -> Optional[dict]:
    uname = username.strip().lower()
    if use_pg():
        try:
            from ..storage.db import fetch_dict
            rows = fetch_dict("SELECT * FROM users WHERE LOWER(username) = %s", (uname,))
            if rows and len(rows) > 0:
                return _user_to_dict_pg(rows[0])
        except Exception:
            pass
    for user in _all_users():
        if user.get("username", "").lower() == uname:
            return user
        if user.get("email", "").lower() == uname:
            return user
    return None


def create_user(user_or_email, password="", username="", plan="community"):
    """Create a new user account.

    Accepts either:
        create_user(email, password, username='', plan='community')
        create_user(user_dict)  -- where user_dict has 'email', 'password_hash', etc.
    """
    api_key = generate_api_key()
    now = datetime.now().isoformat()
    if isinstance(user_or_email, dict):
        d = user_or_email
        api_key = d.get("api_keys", [api_key])[0]
        now = d.get("created_at", now)
        pw_hash = d.get("password_hash", hash_password(""))
        email = d.get("email", "").lower().strip()
        user = {
            "api_key": api_key,
            "email": email,
            "username": d.get("username", "") or email.split("@")[0],
            "password_hash": pw_hash,
            "plan": d.get("plan", "community"),
            "role": d.get("role", "user"),
            "verified": d.get("verified", False),
            "api_keys": d.get("api_keys", [api_key]),
            "created_at": now,
            "updated_at": now,
            "monthly_usage": {"month": _current_month(), "tasks": 0, "searches": 0, "queries": 0},
        }
    else:
        pw_hash = hash_password(password)
        email = user_or_email.lower().strip()
        user = {
            "api_key": api_key,
            "email": email,
            "username": (username.strip() or email.split("@")[0]),
            "password_hash": pw_hash,
            "plan": plan,
            "role": "user",
            "verified": False,
            "api_keys": [api_key],
            "created_at": now,
            "updated_at": now,
            "monthly_usage": {"month": _current_month(), "tasks": 0, "searches": 0, "queries": 0},
        }
    if use_pg():
        try:
            execute("""
                INSERT INTO users (email, username, password_hash, plan, role,
                                   verified, api_keys, monthly_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """, (
                user["email"], user["username"], user["password_hash"],
                user["plan"], user["role"], user["verified"],
                json.dumps(user["api_keys"]), json.dumps(user["monthly_usage"]),
            ))
            # Also write JSON fallback so find_user works if pool later fails
            path = _user_path(api_key)
            path.write_text(json.dumps(user, indent=2))
            return user
        except Exception:
            pass
    # JSON fallback
    path = _user_path(api_key)
    path.write_text(json.dumps(user, indent=2))
    return user


def check_quota(user: dict) -> tuple[bool, str]:
    plan = user.get("plan", "community")
    plan_config = PLANS.get(plan, PLANS["community"])
    limit = plan_config["tasks_per_month"]
    usage = user.get("monthly_usage", {})
    current = usage.get("tasks", 0)
    month = usage.get("month", "")
    now_month = _current_month()
    if month != now_month:
        usage["month"] = now_month
        usage["tasks"] = 0
        usage["searches"] = 0
        usage["queries"] = 0
        _save_user(user)
        return True, ""
    if current >= limit:
        if plan == "community":
            return False, "Monthly quota exceeded (1,000 calls). Upgrade for more."
        return True, "over quota, will be charged overage"
    return True, ""


def can_create_api_key(user: dict) -> tuple[bool, str]:
    plan = user.get("plan", "community")
    cfg = PLANS.get(plan, PLANS["community"])
    max_keys = cfg.get("max_api_keys", 1)
    existing = user.get("api_keys", [user.get("api_key", "")])
    if len(existing) >= max_keys:
        return False, f"API Key limit reached ({max_keys} for {plan})"
    return True, ""


def increment_usage(user: dict, action: str = "tasks") -> None:
    usage = user.get("monthly_usage", {})
    if usage.get("month") != _current_month():
        usage["month"] = _current_month()
        usage["tasks"] = 0
        usage["searches"] = 0
        usage["queries"] = 0
    usage[action] = usage.get(action, 0) + 1
    user["monthly_usage"] = usage
    _save_user(user)


def _save_user(user: dict) -> None:
    """Save user — PG upsert or JSON file."""
    if use_pg():
        try:
            execute("""
                UPDATE users SET
                    plan = %s, role = %s, verified = %s,
                    api_keys = %s::jsonb,
                    monthly_usage = %s::jsonb,
                    updated_at = NOW()
                WHERE email = %s
            """, (
                user.get("plan", "community"),
                user.get("role", "user"),
                user.get("verified", False),
                json.dumps(user.get("api_keys", [])),
                json.dumps(user.get("monthly_usage", {})),
                user.get("email", ""),
            ))
            return
        except Exception:
            pass
    path = _user_path(user.get("api_key", ""))
    path.write_text(json.dumps(user, indent=2))
