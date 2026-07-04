"""
metacore.storage.db — PostgreSQL + pgvector storage layer

Unified database access with connection pool, heartbeat, and auto table creation.
Dual-mode: when AELVOXIM_DATABASE_URL is set, use PG; otherwise fall back to JSON/SQLite.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

# ── Lazy psycopg2 import (try binary first, then source) ──
_PSYCOPG2: any = None  # type: ignore[valid-type]

def _get_psycopg2():
    """Lazy import psycopg2 — try binary first, then source."""
    global _PSYCOPG2
    if _PSYCOPG2 is not None:
        return _PSYCOPG2
    import importlib
    for mod_name in ("psycopg2",):
        try:
            _m = importlib.import_module(mod_name)
            # Verify pool submodule is accessible
            importlib.import_module("psycopg2.pool")
            importlib.import_module("psycopg2.extras")
            _PSYCOPG2 = _m
            return _PSYCOPG2
        except ImportError:
            continue
    return None

# ── Config ──

PG_DSN = os.environ.get(
    "AELVOXIM_DATABASE_URL",
    "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass",
)
_USE_PG = bool(os.environ.get("AELVOXIM_DATABASE_URL", "true"))  # default to PG
# For backward compat: if the env var is explicitly set to empty string, disable PG
if "AELVOXIM_DATABASE_URL" in os.environ and not os.environ["AELVOXIM_DATABASE_URL"]:
    _USE_PG = False

_POOL: Optional[any] = None  # ThreadedConnectionPool, lazy-initialized
_POOL_LOCK = threading.Lock()
_POOL_RETRY_TIME: float = 0  # next retry timestamp (epoch), cooldown 30s


def get_pool() -> Optional[any]:  # ThreadedConnectionPool
    """Get the connection pool (lazy init, auto-retry). Returns None if PG is disabled."""
    import time as _time
    global _POOL, _POOL_RETRY_TIME
    if not _USE_PG:
        return None

    now = _time.time()

    # Lazy init if never tried or marked for retry
    if _POOL is None:
        # Cooldown: skip retries until POOL_RETRY_TIME
        if now < _POOL_RETRY_TIME:
            return None
        with _POOL_LOCK:
            if _POOL is None:
                try:
                    _pg2 = _get_psycopg2()
                    if _pg2 is None:
                        print("psycopg2 not installed — falling back to JSON/SQLite storage")
                        return None
                    _POOL = _pg2.pool.ThreadedConnectionPool(1, 20, dsn=PG_DSN)
                    _init_tables()
                    _start_pool_health_check()
                except Exception as e:
                    print(f"PostgreSQL connection failed: {e}")
                    print("   Will retry connection in 30s")
                    _POOL_RETRY_TIME = now + 30
                    return None
        return _POOL

    # Pool exists — verify it's still alive
    try:
        conn = _POOL.getconn()
        conn.cursor().execute("SELECT 1")
        conn.commit()
        _POOL.putconn(conn)
    except Exception:
        # Pool is dead — reset so next call re-initializes
        try:
            _POOL.close()
        except Exception:
            pass
        _POOL = None
        _POOL_RETRY_TIME = now + 30
        print("PG connection lost, will retry in 30s")
        return None

    return _POOL


def _start_pool_health_check():
    """Background thread: ping idle connections every 60s; properly release dead ones."""
    def _check():
        while True:
            try:
                p = _POOL
                if p:
                    conn = p.getconn()
                    try:
                        conn.cursor().execute("SELECT 1")
                        p.putconn(conn)
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        try:
                            p.putconn(conn)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(60)

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def use_pg() -> bool:
    """Check if PostgreSQL is active."""
    return _USE_PG and get_pool() is not None


def execute(sql_str: str, params: tuple = ()) -> None:
    """Execute a write query."""
    p = get_pool()
    if not p:
        raise RuntimeError("PostgreSQL not available")
    conn = p.getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql_str, params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        p.putconn(conn)


def fetch_one(sql_str: str, params: tuple = ()) -> Optional[tuple]:
    """Fetch a single row."""
    p = get_pool()
    if not p:
        return None
    conn = p.getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql_str, params)
        return cur.fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        p.putconn(conn)


def fetch_all(sql_str: str, params: tuple = ()) -> list[tuple]:
    """Fetch all rows."""
    p = get_pool()
    if not p:
        return []
    conn = p.getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql_str, params)
        return cur.fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        p.putconn(conn)


def fetch_dict(sql_str: str, params: tuple = ()) -> list[dict]:
    """Fetch all rows as dicts."""
    p = get_pool()
    if not p:
        return []
    conn = p.getconn()
    try:
        cur = conn.cursor(cursor_factory=_get_psycopg2().extras.RealDictCursor)
        cur.execute(sql_str, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        p.putconn(conn)


# ── Connection context manager ──


from contextlib import contextmanager


@contextmanager
def db_conn():
    """Context manager for a single PG connection.
    
    Usage:
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute(...)
            results = cur.fetchall()
            conn.commit()
    
    Avoids getconn/putconn thrashing when multiple queries
    need to run in sequence within one request.
    """
    p = get_pool()
    if not p:
        raise RuntimeError("PostgreSQL not available")
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ── Table initialization ──


def _init_tables():
    """Create tables if they don't exist. Each statement is idempotent and isolated."""
    conn = _POOL.getconn()
    try:
        cur = conn.cursor()
        # Run each DDL in isolation — any single failure won't abort the connection
        _safe_execute(cur, "CREATE EXTENSION IF NOT EXISTS vector")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                username VARCHAR(100) DEFAULT '',
                password_hash VARCHAR(200) NOT NULL,
                plan VARCHAR(20) DEFAULT 'community',
                role VARCHAR(20) DEFAULT 'user',
                verified BOOLEAN DEFAULT FALSE,
                api_keys JSONB DEFAULT '[]',
                monthly_usage JSONB DEFAULT '{}',
                proactive_config JSONB DEFAULT '{"enabled": false}',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, "ALTER TABLE users ADD COLUMN IF NOT EXISTS proactive_config JSONB DEFAULT '{\"enabled\": false}'")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS memory_entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(200) NOT NULL,
                entity_type VARCHAR(50) DEFAULT 'general',
                content TEXT DEFAULT '',
                embedding vector(384),
                confidence FLOAT DEFAULT 1.0,
                source VARCHAR(50) DEFAULT 'chat',
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (name, entity_type)
            )""")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_memory_entity_name ON memory_entities(name)")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_memory_entity_type ON memory_entities(entity_type)")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_memory_type_time ON memory_entities(entity_type, created_at DESC)")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS memory_relations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id UUID REFERENCES memory_entities(id) ON DELETE CASCADE,
                target_id UUID REFERENCES memory_entities(id) ON DELETE CASCADE,
                relation_type VARCHAR(50) NOT NULL,
                weight FLOAT DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS knowledge_entries (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                topic VARCHAR(200) NOT NULL,
                title VARCHAR(300) NOT NULL,
                content TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                embedding vector(384),
                tags JSONB DEFAULT '[]',
                validated_count INT DEFAULT 0,
                source VARCHAR(50) DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (topic, title)
            )""")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge_entries(topic)")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status)")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS learning_directions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                topic VARCHAR(200) NOT NULL UNIQUE,
                status VARCHAR(20) DEFAULT 'active',
                phase_index INT DEFAULT 0,
                saturation FLOAT DEFAULT 0,
                entries_created INT DEFAULT 0,
                config JSONB DEFAULT '{}',
                started_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS chat_sessions (
                id VARCHAR(64) PRIMARY KEY,
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                title VARCHAR(200) DEFAULT '新对话',
                message_count INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS chat_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id VARCHAR(64) REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role VARCHAR(10) NOT NULL,
                content TEXT NOT NULL,
                embedding vector(384),
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id)")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_chat_session_time ON chat_messages(session_id, created_at)")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS proactive_push_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                push_type VARCHAR(20) NOT NULL,
                content TEXT DEFAULT '',
                topic VARCHAR(200) DEFAULT '',
                channel VARCHAR(20) DEFAULT 'chat',
                responded BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_push_user ON proactive_push_log(user_id)")
        _safe_execute(cur, """CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                id UUID PRIMARY KEY,
                url VARCHAR(1024) NOT NULL,
                events JSONB DEFAULT '[]',
                secret VARCHAR(128) DEFAULT '',
                user_id VARCHAR(64) DEFAULT '',
                description VARCHAR(200) DEFAULT '',
                active BOOLEAN DEFAULT TRUE,
                delivery_count INT DEFAULT 0,
                failure_count INT DEFAULT 0,
                last_delivery TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
        _safe_execute(cur, "CREATE INDEX IF NOT EXISTS idx_webhook_events ON webhook_subscriptions USING GIN (events)")

        conn.commit()
        print("✅ PostgreSQL tables initialized")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _POOL.putconn(conn)


def _safe_execute(cur, sql: str):
    """Execute a single SQL statement with transaction protection.
    If it fails, rollback so the connection stays usable.
    """
    try:
        cur.execute(sql)
    except Exception:
        try:
            cur.connection.rollback()
        except Exception:
            pass


# ── Vector search helpers ──


def search_memory(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search memory entities by cosine similarity."""
    vec = str(query_embedding)
    sql_str = f"""
        SELECT id, name, entity_type, content, confidence, source,
               1 - (embedding <=> '{vec}'::vector) AS similarity,
               created_at
        FROM memory_entities
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> '{vec}'::vector
        LIMIT {limit}
    """
    return fetch_dict(sql_str)


def search_knowledge(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search knowledge entries by cosine similarity."""
    vec = str(query_embedding)
    sql_str = f"""
        SELECT id, topic, title, content, status,
               1 - (embedding <=> '{vec}'::vector) AS similarity
        FROM knowledge_entries
        WHERE embedding IS NOT NULL AND status = 'active'
        ORDER BY embedding <=> '{vec}'::vector
        LIMIT {limit}
    """
    return fetch_dict(sql_str)


# ── Chat session helpers (dual-mode: PG + JSON fallback) ──


def save_session_to_pg(session: dict) -> None:
    """Upsert a chat session (bypass pool — direct connection)."""
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        _uid = session.get("user_id", "") or None
        cur.execute("""
            INSERT INTO chat_sessions (id, user_id, title, message_count, updated_at)
            VALUES (%s, %s::uuid, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                message_count = EXCLUDED.message_count,
                updated_at = NOW()
        """, (session["id"], _uid, session.get("title", "新对话"), len(session.get("messages", []))))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"save_session_to_pg error: {e}")


def save_message_to_pg(session_id: str, role: str, content: str) -> None:
    """Insert a chat message (bypass pool — direct connection)."""
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (%s, %s, %s)
        """, (session_id, role, content))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"save_message_to_pg error: {e}")


def _pg_connect():
    """Get a direct psycopg2 connection (bypass pool for chat storage)."""
    import psycopg2
    return psycopg2.connect(PG_DSN)

def get_sessions_from_pg(user_id: str = "", email: str = "", limit: int = 50) -> list[dict]:
    """List recent sessions for a user. Accepts user_id (UUID) or email."""
    if not _USE_PG:
        return []
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        if user_id:
            cur.execute("""
                SELECT id, title, message_count, created_at, updated_at
                FROM chat_sessions
                WHERE user_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
            """, (user_id, limit))
        elif email:
            cur.execute("""
                SELECT s.id, s.title, s.message_count, s.created_at, s.updated_at
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE u.email = %s
                ORDER BY s.updated_at DESC
                LIMIT %s
            """, (email, limit))
        else:
            cur.execute(f"""
                SELECT id, title, message_count, created_at, updated_at
                FROM chat_sessions
                ORDER BY updated_at DESC
                LIMIT {limit}
            """)
        rows = [{
            "id": r[0], "title": r[1], "message_count": r[2],
            "created_at": str(r[3]) if r[3] else "",
            "updated_at": str(r[4]) if r[4] else "",
        } for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"get_sessions_from_pg error: {e}")
        return []

def get_messages_from_pg(session_id: str) -> list[dict]:
    """Get messages for a session."""
    if not _USE_PG:
        return []
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
        """, (session_id,))
        rows = [{
            "id": str(r[0]), "role": r[1], "content": r[2],
            "created_at": str(r[3]) if r[3] else "",
        } for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"get_messages_from_pg error: {e}")
        return []


def delete_session_from_pg(session_id: str, user_id: str = "") -> bool:
    """Delete a session and its messages from PG. Returns True if any row was deleted."""
    if not _USE_PG:
        return False
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        # Delete messages first (FK)
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
        # Delete session (no user_id guard — session_id is already unique)
        cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    except Exception as e:
        print(f"delete_session_from_pg error: {e}")
        return False


# ── JSON-based fallback storage ──
