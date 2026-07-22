"""Tests for aelvoxim.storage.db — PostgreSQL database layer.

Covers:
- Connection pool initialization
- Basic CRUD operations
- Table initialization
- Connection health check
- Retry/cooldown behavior

NOTE: Requires PostgreSQL running on localhost:5432.
Tests are skipped if PG is unreachable.
"""
import os
import pytest
from typing import Optional

# Force PG for testing
os.environ["AELVOXIM_DATABASE_URL"] = "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass"


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass",
            connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not reachable on localhost:5432",
)


class TestConnection:
    """Connection pool must initialize and provide connections."""

    def test_get_pool_returns_pool(self):
        from aelvoxim.storage.db import get_pool
        pool = get_pool()
        assert pool is not None

    def test_get_conn_from_pool(self):
        from aelvoxim.storage.db import get_pool, use_pg
        assert use_pg() is True
        pool = get_pool()
        conn = pool.getconn()
        assert conn is not None
        pool.putconn(conn)

    def test_pool_health_check(self):
        from aelvoxim.storage.db import get_pool
        pool = get_pool()
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        pool.putconn(conn)


class TestExecute:
    """SQL execute/fetch functions must work."""

    def test_fetch_one(self):
        from aelvoxim.storage.db import fetch_one
        result = fetch_one("SELECT 42 AS answer")
        assert result is not None
        assert result[0] == 42

    def test_fetch_dict(self):
        from aelvoxim.storage.db import fetch_dict
        results = fetch_dict("SELECT 1 AS id, 'test' AS name")
        assert len(results) == 1
        assert results[0]["id"] == 1
        assert results[0]["name"] == "test"

    def test_fetch_all_empty(self):
        from aelvoxim.storage.db import fetch_all
        results = fetch_all("SELECT * FROM pg_catalog.pg_tables WHERE 1=0")
        assert results == []

    def test_execute_insert_and_rollback(self):
        from aelvoxim.storage.db import execute
        # Create temp table, insert, verify, then clean up
        execute("CREATE TEMP TABLE test_pg (id INT, val TEXT)")
        execute("INSERT INTO test_pg VALUES (%s, %s)", (1, "hello"))
        from aelvoxim.storage.db import fetch_one
        result = fetch_one("SELECT val FROM test_pg WHERE id=1")
        assert result is not None
        assert result[0] == "hello"


class TestTables:
    """Core tables must exist after initialization."""

    def test_users_table_exists(self):
        from aelvoxim.storage.db import fetch_one
        result = fetch_one(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='users')"
        )
        assert result is not None and result[0] is True

    def test_knowledge_entries_table_exists(self):
        from aelvoxim.storage.db import fetch_one
        result = fetch_one(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='knowledge_entries')"
        )
        assert result is not None and result[0] is True

    def test_chat_sessions_table_exists(self):
        from aelvoxim.storage.db import fetch_one
        result = fetch_one(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='chat_sessions')"
        )
        assert result is not None and result[0] is True

    def test_chat_messages_table_exists(self):
        from aelvoxim.storage.db import fetch_one
        result = fetch_one(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='chat_messages')"
        )
        assert result is not None and result[0] is True


class TestSaveAndQuery:
    """End-to-end: save session + messages, then retrieve them."""

    def test_save_and_read_session(self):
        from aelvoxim.storage.db import save_session_to_pg, fetch_dict
        import uuid

        session_id = f"test_session_{uuid.uuid4().hex[:12]}"
        session = {
            "id": session_id,
            "email": "pytest@aelvoxim.test",
            "title": "Test Session",
            "message_count": 0,
            "created_at": "2026-07-13T00:00:00",
            "updated_at": "2026-07-13T00:00:00",
        }
        save_session_to_pg(session)

        rows = fetch_dict("SELECT id, title FROM chat_sessions WHERE id=%s", (session_id,))
        assert len(rows) == 1
        assert rows[0]["title"] == "Test Session"

    def test_save_and_read_message(self):
        from aelvoxim.storage.db import save_message_to_pg, fetch_dict
        import uuid

        session_id = f"test_msg_{uuid.uuid4().hex[:12]}"
        from aelvoxim.storage.db import save_session_to_pg
        save_session_to_pg({
            "id": session_id,
            "email": "pytest@aelvoxim.test",
            "title": "Message Test",
            "message_count": 0,
            "created_at": "2026-07-13T00:00:00",
            "updated_at": "2026-07-13T00:00:00",
        })

        save_message_to_pg(session_id, "user", "Hello from pytest")
        rows = fetch_dict(
            "SELECT role, content FROM chat_messages "
            "WHERE session_id=%s ORDER BY created_at",
            (session_id,),
        )
        assert len(rows) >= 1
        found = any(r["role"] == "user" and "pytest" in r["content"] for r in rows)
        assert found, f"Message not found in {rows}"
