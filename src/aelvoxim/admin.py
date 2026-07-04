"""aelvoxim.admin — CLI admin commands for MetaCore SaaS

Usage:
    python -m metacore admin user-list
    python -m metacore admin user-info <api_key>
    python -m metacore admin create-user [--plan free]
    python -m metacore admin reset-usage <api_key>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _get_users_dir() -> Path:
    from .utils import DATA_DIR
    return DATA_DIR / "users"


def _load_user(key: str) -> dict:
    """Load user by full API key or suffix."""
    users_dir = _get_users_dir()
    # Try exact match (full key as suffix filename)
    path = users_dir / f"{key[-16:]}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Try direct filename
    path = users_dir / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    raise ValueError(f"user not found: {key}")


def cmd_user_list(args):
    users_dir = _get_users_dir()
    if not users_dir.exists():
        print("No users found.")
        return
    files = sorted(users_dir.glob("*.json"))
    print(f"{'API Key (suffix)':<25} {'Plan':<15} {'Tasks':<10} {'Created'}")
    print("-" * 70)
    for f in files:
        try:
            u = json.loads(f.read_text())
            suffix = u.get("api_key", f.stem)[-16:]
            plan = u.get("plan", "free")
            usage = u.get("monthly_usage", {})
            tasks = usage.get("tasks", 0)
            created = u.get("created_at", "")[:10]
            print(f"{suffix:<25} {plan:<15} {tasks:<10} {created}")
        except Exception:
            pass  # non-critical, continue


def cmd_user_info(args):
    try:
        u = _load_user(args.api_key)
        print(json.dumps(u, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_create_user(args):
    from .server.auth import create_user
    email = args.email.strip().lower()
    if not email:
        print("Error: email is required")
        sys.exit(1)
    u = create_user(email=email, password=args.password, plan=args.plan)
    print(f"API Key: {u['api_key']}")
    print(f"Plan:    {u['plan']}")


def cmd_reset_usage(args):
    try:
        u = _load_user(args.api_key)
        u["monthly_usage"] = {"month": "", "tasks": 0, "searches": 0, "queries": 0}
        key = u.get("api_key", args.api_key[-16:] if len(args.api_key) >= 16 else args.api_key)
        path = _get_users_dir() / f"{key[-16:]}.json"
        path.write_text(json.dumps(u, indent=2, ensure_ascii=False))
        print(f"Usage reset for {key[-16:]}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Aelvoxim Admin CLI")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("user-list", help="List all users")
    p_list.set_defaults(func=cmd_user_list)

    p_info = sub.add_parser("user-info", help="Show user details")
    p_info.add_argument("api_key", help="API key or suffix")
    p_info.set_defaults(func=cmd_user_info)

    p_create = sub.add_parser("create-user", help="Create a new user")
    p_create.add_argument("email", help="User email")
    p_create.add_argument("password", help="User password")
    p_create.add_argument("--plan", default="free", help="Plan: free/basic/pro/enterprise")
    p_create.set_defaults(func=cmd_create_user)

    p_reset = sub.add_parser("reset-usage", help="Reset monthly usage")
    p_reset.add_argument("api_key", help="API key or suffix")
    p_reset.set_defaults(func=cmd_reset_usage)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
