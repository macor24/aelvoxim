"""
metacore.storage.patches.db_pool — Monkey-patch fix for PG connection leak (P0-7).

Original bug: db.py:55-75, _start_pool_health_check()
When SELECT 1 on a bad connection throws, conn.close() is called but
p.putconn(conn) is NOT (because `raise` skips the `else` block).
The pool loses track of this connection. Over time, all pool connections
leak out.

Fix: Replace _start_pool_health_check with a version that properly
returns connections on both success and failure paths.
"""

from __future__ import annotations

import logging
import time
import threading

_log = logging.getLogger("aelvoxim.patches.db_pool")


def _safe_ping_loop(pool):
    """Drop-in replacement for the inner _check() closure.
    
    Properly handles all paths:
    - Success: execute SELECT 1, putconn back to pool
    - Failure (bad conn): close it, then putconn the reference back
      (psycopg2 pool handles closed connections by removing them)
    - Pool gone: just sleep and retry
    """
    while True:
        try:
            if pool is None:
                time.sleep(60)
                continue
            conn = pool.getconn()
            try:
                conn.cursor().execute("SELECT 1")
                pool.putconn(conn)
            except Exception:
                # Bad connection: close it, then return None to pool
                try:
                    conn.close()
                except Exception:
                    _log.exception("db_pool error")
                # putconn with a closed connection: psycopg2 pool
                # detects the closed state and removes it from rotation
                try:
                    pool.putconn(conn)
                except Exception:
                    _log.exception("db_pool error")
        except Exception:
            pass  # pool.getconn() can also fail (pool exhausted etc.)
        time.sleep(60)


def patch_start_pool_health_check():
    """Replace _start_pool_health_check in db module with fixed version."""
    import ast
    import os
    
    # Find db.py
    here = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(here, "..", "..", "storage", "db.py")
    db_path = os.path.normpath(db_path)
    
    if not os.path.exists(db_path):
        _log.warning("db.py not found at %s, skip patch", db_path)
        return False
    
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            source = f.read()
        
        # Backup
        bak_path = db_path + ".bak.p0"
        if not os.path.exists(bak_path):
            with open(bak_path, "w", encoding="utf-8") as f:
                f.write(source)
            _log.info("Backup saved to %s", bak_path)
        
        # The old function body
        old = '''def _start_pool_health_check():
    """Background thread: ping idle connections every 60s; discard dead ones."""
    def _check():
        while True:
            try:
                p = _POOL
                if p:
                    conn = p.getconn()
                    try:
                        conn.cursor().execute("SELECT 1")
                    except Exception:
                        conn.close()
                        raise
                    else:
                        p.putconn(conn)
            except Exception:
                pass  # bad connection already closed above
            time.sleep(60)

    t = threading.Thread(target=_check, daemon=True)
    t.start()'''
        
        new = '''def _start_pool_health_check():
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
                            _log.exception("db_pool error")
                        try:
                            p.putconn(conn)
                        except Exception:
                            _log.exception("db_pool error")
            except Exception:
                _log.exception("db_pool error")
            time.sleep(60)

    t = threading.Thread(target=_check, daemon=True)
    t.start()'''
        
        if old in source:
            source = source.replace(old, new, 1)
            with open(db_path, "w", encoding="utf-8") as f:
                f.write(source)
            
            try:
                ast.parse(source)
                _log.info("db.py patched successfully (P0-7)")
                return True
            except SyntaxError as e:
                _log.error("Patch syntax error: %s", e)
                with open(bak_path, "r", encoding="utf-8") as f:
                    source = f.read()
                with open(db_path, "w", encoding="utf-8") as f:
                    f.write(source)
                return False
        else:
            _log.warning("Old function not found (already patched?)")
            # Check if the new version is already there
            if "properly release dead ones" in source:
                return True
            return False
    except Exception as e:
        _log.error("Patch failed: %s", e)
        return False
