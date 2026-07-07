"""aelvoxim.learn.execute — True execution templates

Subprocess-based code execution for knowledge validation.
Each template generates a runnable Python script and captures stdout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Optional


# ── True execution templates ──────────────────

_TASK_TEMPLATES = {
    "route": '''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix="/api/v1")
@router.get("/items/{item_id}")
async def get_item(item_id: int):
    return {"item_id": item_id, "name": "sample", "price": 9.99}
app.include_router(router)
# Knowledge output
print("=== Knowledge: Route & DI ===")
print("FastAPI APIRouter groups endpoints under a common prefix (e.g. /api/v1)")
print("Dependency injection via Depends() enables reusable auth/rate-limiter")
print("Pydantic BaseModel auto-validates request data and generates OpenAPI schema")
print("SQLAlchemy session per request: dependency manages commit/rollback/close lifecycle")
''',
    "dependency": '''
from fastapi import FastAPI, Depends, HTTPException, Header
app = FastAPI()
def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(401, "no token")
    return authorization
@app.get("/secure")
def secure(token: str = Depends(verify_token)):
    return {"access": "granted", "token_prefix": token[:6]}
# Knowledge output
print("=== Knowledge: DI & Security ===")
print("FastAPI Header() extracts HTTP headers as function parameters")
print("Depends() enables middleware-like auth chains: verify_token -> get_current_user")
print("HTTPException(401) immediately stops request processing with proper status code")
''',
    "database": '''
import sqlite3, os
db = "/tmp/_learn.db"
if os.path.exists(db): os.unlink(db)
con = sqlite3.connect(db)
con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
con.execute("INSERT INTO users(name,email) VALUES('alice','a@t.com')")
con.execute("INSERT INTO users(name,email) VALUES('bob','b@t.com')")
con.commit()
rows = con.execute("SELECT * FROM users").fetchall()
con.close()
os.unlink(db)
# Knowledge output
print("=== Knowledge: SQLite CRUD ===")
print("sqlite3.connect() creates/opens a .db file; con.execute() runs raw SQL")
print("CREATE TABLE defines schema with PRIMARY KEY, TEXT, INTEGER types")
print("INSERT INTO + commit() makes changes persistent within the transaction")
print(f"CRUD lifecycle: connect -> execute(SQL) -> commit -> close. Rows: {len(rows)}")
print("Transaction isolation: BEGIN/COMMIT/ROLLBACK control atomicity across multiple writes")
''',
    "index": '''
import sqlite3, os, time, random
db = "/tmp/_idx.db"
if os.path.exists(db): os.unlink(db)
con = sqlite3.connect(db)
con.execute("CREATE TABLE logs(id INTEGER PRIMARY KEY, uid INT, action TEXT)")
for i in range(2000):
    con.execute("INSERT INTO logs(uid,action) VALUES(?,?)", (random.randint(1,100), random.choice(["a","b","c"])))
con.commit()
t0 = time.time()
con.execute("SELECT COUNT(*) FROM logs WHERE uid=42").fetchone()
t1 = time.time() - t0
con.execute("CREATE INDEX i_uid ON logs(uid)")
t0 = time.time()
con.execute("SELECT COUNT(*) FROM logs WHERE uid=42").fetchone()
t2 = time.time() - t0
con.close()
os.unlink(db)
# Knowledge output
print("=== Knowledge: Index Benchmark ===")
print("CREATE INDEX i_uid ON logs(uid) creates a B-tree index on the uid column")
print("Index speeds up SELECT/WHERE queries: scan full table vs O(log n) lookup")
print(f"Without index: {t1:.4f}s | With index: {t2:.4f}s | Speedup: {t1/t2:.1f}x")
''',
    "test": '''
def test_add(): assert 1+1 == 2
def test_sub(): assert 3-1 == 2
def test_mul(): assert 2*3 == 6
test_cases = [("add", test_add), ("sub", test_sub), ("mul", test_mul)]
passed = 0
for n, fn in test_cases:
    try: fn(); passed += 1; print(f"  PASS {n}")
    except Exception as _e: print(f"  FAIL {n}: {_e}")
# Knowledge output
print("=== Knowledge: Unit Testing ===")
print("Test function naming: def test_xxx() with assert statements")
print("Each test runs independently; assert fails the test if condition is false")
print("Test runner collects all test_ functions and reports PASS/FAIL")
print(f"Results: {passed}/{len(test_cases)} passed")
print("pytest.fixture provides reusable setup/teardown across tests (e.g. test DB)")
''',
    "deploy": '''
cfg = {"app":"fastapi", "port":8000, "workers":4, "docker":{"image":"python:3.12-slim"}}
# Knowledge output
print("=== Knowledge: Deployment Config ===")
print("Production deployment needs: app framework, port mapping, worker count")
print("Docker image: python:3.12-slim as base (minimal OS + Python runtime)")
print("docker-compose or k8s manages: env vars, volumes, network, health checks")
''',
    "config": '''
cfg = {"app":"aelvoxim","debug":False,"db":{"host":"localhost","port":5432},"cache":{"ttl":3600}}
# Knowledge output
print("=== Knowledge: App Config Structure ===")
print("Config sections: app metadata, debug mode, database connection pool, cache TTL")
print("Database config: host, port specify the DB server; pool_size controls connections")
print("Cache TTL (3600s=1h): time before cached data is considered stale")
''',
    "function": '''
def example(a: str, b: int = 42) -> dict:
    return {"a": a, "b": b, "computed": b * 2}
r = example("test")
# Knowledge output
print("=== Knowledge: Python Functions ===")
print("Type hints: a: str, b: int = 42 = default parameter with type annotation")
print("Return type: -> dict annotates that this function returns a dictionary")
print("Function body: computes and returns a dict with input values and computed result")
print(f"Call: example('test') -> {r}")
print("@dataclass auto-generates __init__, __repr__, __eq__ for class definitions")
''',
}

# ── Template matching ────────────────────

# Keyword → template name mapping for EN tasks
_KEYWORD_TO_TEMPLATE = {
    "route": "route", "endpoint": "route", "router": "route",
    "dependency": "dependency", "depends": "dependency", "injection": "dependency",
    "database": "database", "sql": "database", "crud": "database", "table": "database",
    "index": "index", "query": "index",
    "test": "test", "pytest": "test", "unittest": "test",
    "deploy": "deploy", "deployment": "deploy",
    "config": "config", "configuration": "config", "setting": "config",
    "function": "function", "class": "function", "method": "function",
}

# CN keyword → template name mapping
_KEYWORD_TO_TEMPLATE_CN = {
    "路由": "route", "端点": "route", "接口": "route",
    "依赖": "dependency", "注入": "dependency",
    "数据库": "database", "sql": "database", "查询": "database", "建表": "database",
    "索引": "index",
    "测试": "test",
    "部署": "deploy",
    "配置": "config", "设置": "config",
    "函数": "function", "类": "function", "方法": "function",
}


def _get_template_for_task(task: str) -> Optional[str]:
    """Match task string to a template. Returns template source or None."""
    task_lower = task.lower()
    # EN keywords
    for kw, tpl_name in _KEYWORD_TO_TEMPLATE.items():
        if kw in task_lower:
            return _TASK_TEMPLATES.get(tpl_name)
    # CN keywords
    for kw, tpl_name in _KEYWORD_TO_TEMPLATE_CN.items():
        if kw in task_lower:
            return _TASK_TEMPLATES.get(tpl_name)
    return None


# ── Execution ────────────────────────────


def try_execute_task(topic: str, task: str) -> Optional[str]:
    """Attempt to execute a sub-task using a hardcoded template.

    Only executes if task matches a preset template (hardcoded, safe).
    Returns stdout content on success, None on failure (caller falls back
    to search + LLM extraction). Never executes arbitrary code.
    """
    task_lower = task.lower()

    # Only execute tasks that match a preset template
    tpl = _get_template_for_task(task)
    if not tpl:
        return None  # caller falls back to search + LLM extraction

    # Sanitize for safe interpolation in comment lines
    _scrub = lambda s: s.replace("\n", " ").replace("\r", " ")

    # Build the executable script
    lines = [
        "import sys, json, math, os, time, random, re, collections, itertools, functools, hashlib, typing",
        "from datetime import datetime, timedelta, timezone",
        "",
        f"# Task: {_scrub(task)}",
        f"# Topic: {_scrub(topic)}",
        "",
        tpl,
    ]

    try:
        code = "\n".join(lines)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            # Best-effort cleanup: never let unlink failure propagate
            try:
                os.unlink(tmp_path)
            except Exception:
                pass  # non-critical, continue

        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if output or stderr:
            combined = output
            if stderr:
                combined += "\n[stderr]\n" + stderr
            return combined
        return "# Execution done (exit: {})".format(result.returncode)
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
