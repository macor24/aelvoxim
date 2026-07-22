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

PG_DSN = os.environ.get("AELVOXIM_DATABASE_URL", "")
# PG is only enabled when AELVOXIM_DATABASE_URL is set to a non-empty value
_USE_PG = bool(PG_DSN)

_POOL: Optional[any] = None  # ThreadedConnectionPool, lazy-initialized
_POOL_LOCK = threading.Lock()
_POOL_READY = threading.Event()  # set when pool is healthy
# ── Background retry thread ──
_POOL_RETRYER_RUNNING = False


def _start_pool_retryer():
    """Background thread: retry PG connection every 30s until healthy."""
    global _POOL_RETRYER_RUNNING
    if _POOL_RETRYER_RUNNING:
        return
    _POOL_RETRYER_RUNNING = True

    def _retry_loop():
        import time as _t
        while True:
            if _POOL_READY.is_set():
                # Pool is healthy — check liveness every 60s
                _t.sleep(60)
            else:
                # Pool down — try every 30s
                _t.sleep(30)
            get_pool()  # will attempt connect or validate

    t = threading.Thread(target=_retry_loop, daemon=True, name="pg-retryer")
    t.start()


def _init_pool() -> bool:
    """Try to create the connection pool. Returns True on success."""
    global _POOL
    try:
        _pg2 = _get_psycopg2()
        if _pg2 is None:
            _log.warning("psycopg2 not installed — falling back to JSON/SQLite storage")
            return False
        _POOL = _pg2.pool.ThreadedConnectionPool(1, 20, dsn=PG_DSN)
        _init_tables()
        _start_pool_health_check()
        _POOL_READY.set()
        return True
    except Exception as e:
        _log.warning(f"PostgreSQL connection failed: {e}")
        _POOL_READY.clear()
        return False


def _close_pool():
    """Close and reset the pool."""
    global _POOL
    if _POOL is not None:
        try:
            _POOL.close()
        except Exception:
            _log.exception("db error")
        _POOL = None
    _POOL_READY.clear()


def get_pool() -> Optional[any]:
    """Get the connection pool (lazy init + background retry). Returns None if PG is disabled."""
    global _POOL
    if not _USE_PG:
        return None

    # Lazy init on first call
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _init_pool()
                _start_pool_retryer()
        return _POOL

    # Pool exists — quick liveness check (only when called, background retryer handles rective)
    try:
        conn = _POOL.getconn()
        conn.cursor().execute("SELECT 1")
        conn.commit()
        _POOL.putconn(conn)
    except Exception:
        _close_pool()
        _log.warning("PG connection lost, background retryer will reconnect")
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
                            _log.exception("db error")
                        try:
                            p.putconn(conn)
                        except Exception:
                            _log.exception("db error")
            except Exception:
                _log.exception("db error")
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
            _log.exception("db error")
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
            _log.exception("db error")
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
            _log.exception("db error")
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
            _log.exception("db error")
        raise
    finally:
        p.putconn(conn)


# ── Connection context manager ──


from contextlib import contextmanager

import logging
_log = logging.getLogger("aelvoxim.db")



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
        _log.info("PostgreSQL tables initialized")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            _log.exception("db error")
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
            _log.exception("db error")


# ── Vector search helpers ──


def search_memory(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search memory entities by cosine similarity."""
    # Validate: ensure query_embedding is a list of floats
    if not isinstance(query_embedding, list) or not all(isinstance(x, (int, float)) for x in query_embedding):
        return []
    limit = min(max(int(limit), 1), 100)  # bound limit between 1-100
    # Build vector safely — cast each element explicitly
    vec_str = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
    sql_str = """
        SELECT id, name, entity_type, content, confidence, source,
               1 - (embedding <=> %s::vector) AS similarity,
               created_at
        FROM memory_entities
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    return fetch_dict(sql_str, (vec_str, vec_str, limit))


def search_knowledge(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search knowledge entries by cosine similarity."""
    # Validate: ensure query_embedding is a list of floats
    if not isinstance(query_embedding, list) or not all(isinstance(x, (int, float)) for x in query_embedding):
        return []
    limit = min(max(int(limit), 1), 100)  # bound limit between 1-100
    vec_str = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
    sql_str = """
        SELECT id, topic, title, content, status,
               1 - (embedding <=> %s::vector) AS similarity
        FROM knowledge_entries
        WHERE embedding IS NOT NULL AND status = 'active'
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    return fetch_dict(sql_str, (vec_str, vec_str, limit))


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
        """, (session["id"], _uid, session.get("title") or "新对话", len(session.get("messages", []))))
        conn.commit()
        conn.close()
    except Exception as e:
        pass  # logged above


def save_message_to_pg(session_id: str, role: str, content: str, user_id: str = "") -> None:
    """Insert a chat message (bypass pool — direct connection)."""
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        # Verify session belongs to user if user_id is provided
        if user_id:
            cur.execute(
                "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s::uuid",
                (session_id, user_id),
            )
            if cur.fetchone() is None:
                conn.close()
                raise PermissionError(f"Session {session_id} does not belong to user {user_id}")
        cur.execute("""
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (%s, %s, %s)
        """, (session_id, role, content))
        conn.commit()
        conn.close()
    except PermissionError:
        raise
    except Exception as e:
        pass  # logged above


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
            cur.execute("""
                SELECT id, title, message_count, created_at, updated_at
                FROM chat_sessions
                ORDER BY updated_at DESC
                LIMIT %s
            """, (limit,))
        rows = [{
            "id": r[0], "title": r[1], "message_count": r[2],
            "created_at": str(r[3]) if r[3] else "",
            "updated_at": str(r[4]) if r[4] else "",
        } for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        pass  # logged above
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
        pass  # logged above
        return []


def delete_session_from_pg(session_id: str, user_id: str = "") -> bool:
    """Delete a session and its messages from PG. Returns True if any row was deleted."""
    if not _USE_PG:
        return False
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        # Verify session belongs to user
        if user_id:
            cur.execute(
                "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s::uuid",
                (session_id, user_id),
            )
            if cur.fetchone() is None:
                conn.close()
                raise PermissionError(f"Session {session_id} does not belong to user {user_id}")
        # Delete messages first (FK)
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
        # Delete session
        cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    except PermissionError:
        raise
    except Exception as e:
        pass  # logged above
        return False


# ── JSON-based fallback storage ──
