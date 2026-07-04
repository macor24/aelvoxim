"""
metacore.storage.migrate — Import existing JSON/SQLite data into PostgreSQL.

Usage:
    python -m metacore.storage.migrate          # full migration
    python -m metacore.storage.migrate --validate-only  # check only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..utils import METACORE_DIR

from .db import execute, fetch_one, use_pg


# ── Count helpers (old storage) ──


def _count_json_users() -> int:
    users_dir = METACORE_DIR / "users"
    if not users_dir.exists():
        return 0
    return len(list(users_dir.glob("*.json")))


def _count_json_knowledge() -> int:
    """Count actual knowledge entries in JSON files (sum all items across all files)."""
    kb_dir = METACORE_DIR / "knowledge"
    count = 0
    if kb_dir.exists():
        for f in kb_dir.glob("*"):
            if not f.is_file():
                continue
            try:
                data = json.loads(f.read_text())
                if isinstance(data, list):
                    count += len(data)
                elif isinstance(data, dict):
                    count += 1
            except Exception:
                pass
    return count


def _count_json_directions() -> int:
    cfg_file = METACORE_DIR / "learner" / "config.json"
    if not cfg_file.exists():
        return 0
    try:
        data = json.loads(cfg_file.read_text())
        return len(data) if isinstance(data, dict) else 0
    except Exception:
        return 0


# ── Chat session paths (configurable via env) ──
_CHAT_SESSIONS_DIR = Path(os.environ.get(
    "AELVOXIM_CHAT_SESSIONS_DIR",
    str(Path.home() / ".chatael" / "sessions"),
))


def _count_json_sessions() -> int:
    sessions_dir = _CHAT_SESSIONS_DIR
    if not sessions_dir.exists():
        return 0
    return len(list(sessions_dir.glob("*.json")))


def _count_json_messages() -> int:
    sessions_dir = _CHAT_SESSIONS_DIR
    count = 0
    if sessions_dir.exists():
        for f in sessions_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                count += len(data.get("messages", []))
            except Exception:
                pass
    return count


_VALID_TABLES = {"users", "knowledge_entries", "learning_directions", "chat_sessions", "chat_messages"}

def _count_pg(table: str) -> int:
    if table not in _VALID_TABLES:
        return 0
    r = fetch_one(f"SELECT COUNT(*) FROM {table}")
    return r[0] if r else 0


# ── Validation ──


def validate_migration():
    """Compare old JSON vs new PG record counts."""
    checks = [
        ("users",              _count_json_users(),     "users"),
        ("knowledge_entries",  _count_json_knowledge(), "knowledge_entries"),
        ("learning_directions",_count_json_directions(),"learning_directions"),
        ("chat_sessions",      _count_json_sessions(),  "chat_sessions"),
        ("chat_messages",      _count_json_messages(),  "chat_messages"),
    ]
    print()
    print("Migration validation report")
    print(40 * "=")
    all_ok = True
    for name, old_count, table in checks:
        pg_count = _count_pg(table)
        status = "OK" if old_count == pg_count else "MISMATCH"
        if old_count != pg_count:
            all_ok = False
        print(f"  [{status}] {name}: JSON {old_count} -> PG {pg_count}")

    # Field-level spot check: compare a few records for data integrity
    print()
    spot_ok = _spot_check_users() and _spot_check_directions()
    if not spot_ok:
        all_ok = False
    print()
    print(40 * "=")
    if all_ok:
        print("  All checks passed")
    else:
        print("  Some checks failed")
    return all_ok


# ── Migration runners ──


def migrate_users():
    """Import users from JSON files to PG."""
    users_dir = METACORE_DIR / "users"
    if not users_dir.exists():
        return
    count = 0
    for f in sorted(users_dir.glob("*.json")):
        try:
            u = json.loads(f.read_text())
            execute("""
                INSERT INTO users (email, username, password_hash, plan, role,
                                   verified, api_keys, monthly_usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (email) DO NOTHING
            """, (
                u.get("email", f"auto_{f.stem}@unknown"),
                u.get("username", ""),
                u.get("password_hash", ""),
                u.get("plan", "community"),
                u.get("role", "user"),
                u.get("verified", False),
                json.dumps(u.get("api_keys", [])),
                json.dumps(u.get("monthly_usage", {})),
            ))
            count += 1
        except Exception as e:
            print(f"  User {f.name}: {e}")
    print(f"  Users: {count} imported")


def migrate_knowledge():
    """Import knowledge entries from JSON files."""
    kb_dir = METACORE_DIR / "knowledge"
    if not kb_dir.exists():
        return
    count = 0
    for f in sorted(kb_dir.glob("*")):
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
            if isinstance(data, list):
                for entry in data:
                    _insert_kb_entry(entry)
                    count += 1
            elif isinstance(data, dict):
                _insert_kb_entry(data)
                count += 1
        except Exception:
            pass
    print(f"  Knowledge: {count} imported")


def _insert_kb_entry(entry: dict):
    execute("""
        INSERT INTO knowledge_entries (topic, title, content, status, tags, source)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (topic, title) DO NOTHING
    """, (
        entry.get("topic", ""),
        entry.get("title", entry.get("topic", "")),
        entry.get("content", entry.get("summary", "")),
        entry.get("status", "pending"),
        json.dumps(entry.get("tags", [])),
        entry.get("source", "migration"),
    ))


def migrate_directions():
    """Import learning directions from JSON."""
    cfg_file = METACORE_DIR / "learner" / "config.json"
    if not cfg_file.exists():
        return
    try:
        data = json.loads(cfg_file.read_text())
        count = 0
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            execute("""
                INSERT INTO learning_directions
                    (topic, status, phase_index, saturation, entries_created, config)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (topic) DO UPDATE SET
                    status = EXCLUDED.status,
                    saturation = EXCLUDED.saturation
            """, (
                val.get("topic", key),
                val.get("status", "active"),
                val.get("phase_index", 0),
                val.get("saturation", 0.0),
                val.get("entries_created", 0),
                json.dumps(val),
            ))
            count += 1
        print(f"  Learning directions: {count} imported")
    except Exception as e:
        print(f"  Directions migration failed: {e}")


def migrate_sessions():
    """Import chat sessions and messages from JSON."""
    sessions_dir = _CHAT_SESSIONS_DIR
    if not sessions_dir.exists():
        return
    session_count = 0
    msg_count = 0
    for f in sorted(sessions_dir.glob("*.json")):
        try:
            session = json.loads(f.read_text())
            sid = session.get("id", f.stem)
            title = session.get("title", "新对话")
            msgs = session.get("messages", [])
            # Insert session (without user_id for now)
            execute("""
                INSERT INTO chat_sessions (id, title, message_count, created_at, updated_at)
                VALUES (%s, %s, %s, %s::timestamp, %s::timestamp)
                ON CONFLICT (id) DO NOTHING
            """, (sid, title, len(msgs),
                  session.get("created_at", "2026-01-01T00:00:00Z").replace("T", " ").replace("Z", ""),
                  session.get("updated_at", "2026-01-01T00:00:00Z").replace("T", " ").replace("Z", "")))
            session_count += 1
            for m in msgs:
                execute("""
                    INSERT INTO chat_messages (session_id, role, content)
                    VALUES (%s, %s, %s)
                """, (sid, m.get("role", "user"), m.get("content", "")))
                msg_count += 1
        except Exception as e:
            pass
    print(f"  Sessions: {session_count}, Messages: {msg_count}")


# ── Main ──


def main():
    if not use_pg():
        print("PostgreSQL not available. Set AELVOXIM_DATABASE_URL")
        sys.exit(1)

    if "--validate-only" in sys.argv:
        validate_migration()
        return

    print(40 * "=")
    print("  Aelvoxim data migration tool")
    print(40 * "=")
    print()
    print("Migrating...")
    print()

    migrate_users()
    migrate_knowledge()
    migrate_directions()
    migrate_sessions()

    print()
    validate_migration()


if __name__ == "__main__":
    main()


# ── Field-level spot checks ──


def _spot_check_users() -> bool:
    """Compare sample user fields between JSON and PG."""
    users_dir = METACORE_DIR / "users"
    if not users_dir.exists():
        return True
    ok = True
    from ..storage.db import fetch_dict
    samples = sorted(users_dir.glob("*.json"))[:3]  # check up to 3 users
    for f in samples:
        try:
            u = json.loads(f.read_text())
            email = u.get("email", "")
            if not email:
                continue
            row = fetch_dict("SELECT email, username, plan FROM users WHERE email=%s", (email,))
            if not row:
                print(f"  User {email} not found in PG")
                ok = False
                continue
            row = row[0]
            for field in ("email", "username", "plan"):
                if str(u.get(field, "")) != str(row.get(field, "")):
                    print(f"  User {email} field '{field}' mismatch: JSON='{u.get(field)}' PG='{row.get(field)}'")
                    ok = False
        except Exception as e:
            print(f"  User spot check error: {e}")
    if ok:
        print("  Users spot check passed")
    return ok


def _spot_check_directions() -> bool:
    """Compare sample learning direction fields between JSON and PG."""
    cfg_file = METACORE_DIR / "learner" / "config.json"
    if not cfg_file.exists():
        return True
    ok = True
    from ..storage.db import fetch_dict
    try:
        data = json.loads(cfg_file.read_text())
        if not isinstance(data, dict):
            return True
        topics = list(data.keys())[:3]  # check up to 3 directions
        for topic in topics:
            info = data[topic]
            row = fetch_dict("SELECT topic, status, entries_created FROM learning_directions WHERE topic=%s", (topic,))
            if not row:
                print(f"  Direction '{topic}' not found in PG")
                ok = False
                continue
            row = row[0]
            if str(info.get("status", "active")) != str(row.get("status", "")):
                print(f"  Direction '{topic}' status mismatch: JSON='{info.get('status')}' PG='{row.get('status')}'")
                ok = False
            if info.get("entries_created", 0) != row.get("entries_created", 0):
                print(f"  Direction '{topic}' entries_created mismatch: JSON={info.get('entries_created')} PG={row.get('entries_created')}")
                ok = False
    except Exception as e:
        print(f"  Directions spot check error: {e}")
    if ok:
        print("  Directions spot check passed")
    return ok
