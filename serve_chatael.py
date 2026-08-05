"""
chatael-v2 serve.py — Static file server with PG + JSON fallback persistence.

Provides API endpoints for session/message persistence.
PG primary, JSON file fallback (survives PG outages).
"""

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import os
import time
import urllib.request
import uuid

DIST = Path(__file__).parent / "frontend" / "chatael" / "dist"
DATA_DIR = Path(__file__).parent / "frontend" / "chatael-v2" / "data"
PORT = 9702

import logging
_log = logging.getLogger("chatael")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [chatael.%(levelname)s] %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# ── PG connection (optional, self-healing) ──
_PG_CONN = None
_PG_DSN = os.environ.get(
    "CHATAEL_DATABASE_URL",
    "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass",
)
_last_pg_attempt = 0.0  # throttle reconnect attempts to 1 per 15s


def _pg() -> bool:
    """Return True if a healthy PG connection is available.

    Self-healing: if startup failed (e.g. PG not ready when chatael booted)
    or the connection dropped, lazily retry connecting (throttled). Without
    this, a failed init at boot leaves chatael stuck on JSON fallback forever.
    """
    global _PG_CONN, _last_pg_attempt
    now = time.time()
    if _PG_CONN is not None:
        try:
            _PG_CONN.cursor().execute("SELECT 1")
            return True
        except Exception:
            try:
                _PG_CONN.close()
            except Exception:
                pass
            _PG_CONN = None  # stale connection → retry below
    if now - _last_pg_attempt < 15.0:
        return False
    _last_pg_attempt = now
    try:
        import psycopg2 as _pg2
        _PG_CONN = _pg2.connect(_PG_DSN)
        return True
    except Exception:
        _PG_CONN = None
        return False


# ── JSON session persistence (fallback) ──

_SESSIONS_DIR = DATA_DIR / "sessions"
_MESSAGES_DIR = DATA_DIR / "messages"


def _ensure_dirs():
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _MESSAGES_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    """Path to JSON session file. Validates session_id to prevent path traversal."""
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(f"Invalid session_id: {session_id}")
    _validate_session_id(session_id)
    return _SESSIONS_DIR / f"{session_id}.json"


def _messages_path(session_id: str) -> Path:
    """Path to JSON messages file for a session."""
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(f"Invalid session_id: {session_id}")
    _validate_session_id(session_id)
    return _MESSAGES_DIR / f"{session_id}.json"


def _validate_session_id(session_id: str) -> None:
    """Ensure session_id contains only safe characters (alphanumeric, underscore, colon, hyphen)."""
    import re
    if not re.match(r'^[\w.:-]+$', session_id):
        raise ValueError(f"Invalid session_id: {session_id}")


def _save_session_json(session: dict):
    """Save session + messages to JSON files."""
    _ensure_dirs()
    meta = {
        "id": session["id"],
        "title": session.get("title", "New Chat"),
        "message_count": len(session.get("messages", [])),
        "created_at": session.get("created_at", ""),
        "updated_at": session.get("updated_at", ""),
    }
    _session_path(session["id"]).write_text(json.dumps(meta, indent=2))
    msgs = []
    for m in session.get("messages", []):
        msgs.append({
            "role": m.get("role", "user"),
            "content": m.get("content", ""),
            "timestamp": m.get("timestamp", ""),
        })
    _messages_path(session["id"]).write_text(json.dumps(msgs, indent=2))


def _load_session_json(session_id: str) -> dict | None:
    """Load session from JSON files."""
    meta_path = _session_path(session_id)
    msg_path = _messages_path(session_id)
    if not meta_path.exists() or not msg_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
        msgs = json.loads(msg_path.read_text())
        return {
            "id": meta["id"],
            "title": (meta.get("title") or "New Chat"),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "messages": msgs,
        }
    except Exception:
        return None


def _list_sessions_json() -> list:
    """List all sessions from JSON files."""
    _ensure_dirs()
    results = []
    for f in sorted(_SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(f.read_text())
            results.append({
                "id": meta["id"],
                "title": (meta.get("title") or "New Chat"),
                "message_count": meta.get("message_count", 0),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            })
        except Exception:
            pass
    return results


# ── Title cleaning ──────────────────────────


def _clean_title(raw: str, max_len: int = 30) -> str:
    """Normalize a session title for the list UI.

    Collapses newlines/whitespace into single spaces and truncates.
    Falls back to 'New Chat' when nothing usable remains.
    """
    if not raw:
        return "New Chat"
    import re as _re
    s = _re.sub(r"\s+", " ", str(raw)).strip()
    s = _re.sub(r"[ \t]+", " ", s)
    if not s:
        return "New Chat"
    return s[:max_len] if len(s) > max_len else s


# ── Session persistence (PG only) ──


def _save_session(session: dict, user_id: str = "") -> bool:
    """Save a session to PG (primary store). Returns True on success."""
    if not _pg():
        return False
    try:
        import datetime as _dt
        # Use local server time (PG column is timestamp without tz, server TZ=Asia/Shanghai).
        # Never store UTC "Z" strings here — they sort 8h behind local rows.
        now_local = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created = session.get("created_at") or now_local
        if isinstance(created, str) and created.endswith("Z"):
            try:
                created = _dt.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                created = now_local
        cur = _PG_CONN.cursor()
        title = _clean_title(session.get("title", "New Chat"))
        cur.execute(
            "INSERT INTO chat_sessions (id, title, message_count, created_at, updated_at, user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, message_count=EXCLUDED.message_count, updated_at=EXCLUDED.updated_at;",
            (session["id"], title, len(session.get("messages", [])),
             created, now_local, user_id or None),
        )
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s;", (session["id"],))
        for m in session.get("messages", []):
            cur.execute(
                "INSERT INTO chat_messages (session_id, role, content, metadata, created_at) "
                "VALUES (%s, %s, %s, '{}'::jsonb, %s);",
                (session["id"], m.get("role", "user"), m.get("content", ""),
                 m.get("timestamp", now_local)),
            )
        _PG_CONN.commit()
        return True
    except Exception:
        _log.exception("chatael save_session error")
        return False


def _load_session(session_id: str, user_id: str = "") -> dict | None:
    """Load a session from PG only."""
    if not _pg():
        return None
    try:
        cur = _PG_CONN.cursor()
        cur.execute(
            "SELECT id, title, created_at, updated_at, user_id FROM chat_sessions WHERE id = %s;",
            (session_id,),
        )
        r = cur.fetchone()
        if r:
            _uid = str(r[4]) if r[4] else ""
            if user_id and _uid and _uid != user_id:
                return None
            cur.execute(
                "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s ORDER BY created_at;",
                (session_id,),
            )
            msgs = [{"role": m[0], "content": m[1], "timestamp": str(m[2]) if m[2] else ""}
                    for m in cur.fetchall()]
            return {
                "id": r[0], "title": _clean_title(r[1]),
                "created_at": str(r[2]) if r[2] else "",
                "updated_at": str(r[3]) if r[3] else "",
                "messages": msgs,
            }
        return None
    except Exception:
        _log.exception("chatael load_session error")
        return None


def _list_sessions(user_id: str = "") -> list:
    """List sessions from PG only. Requires user_id."""
    if not _pg() or not user_id:
        return []
    try:
        cur = _PG_CONN.cursor()
        cur.execute(
            "SELECT id, title, message_count, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC;",
            (user_id,),
        )
        return [
            {"id": r[0], "title": _clean_title(r[1]),
             "message_count": r[2] or 0,
             "created_at": str(r[3]) if r[3] else "",
             "updated_at": str(r[4]) if r[4] else ""}
            for r in cur.fetchall()
        ]
    except Exception:
        _log.exception("chatael list_sessions error")
        return []


def _search_sessions(q: str, user_id: str = "") -> list:
    """Search sessions by keyword in messages. PG only."""
    if not _pg() or not user_id:
        return []
    try:
        cur = _PG_CONN.cursor()
        cur.execute(
            "SELECT DISTINCT s.id, s.title, s.message_count, s.created_at, s.updated_at "
            "FROM chat_sessions s JOIN chat_messages m ON m.session_id = s.id "
            "WHERE LOWER(m.content) LIKE %s AND s.user_id = %s ORDER BY s.updated_at DESC LIMIT 50;",
            ('%' + q.lower() + '%', user_id),
        )
        return [
            {"id": r[0], "title": _clean_title(r[1]),
             "message_count": r[2] or 0,
             "created_at": str(r[3]) if r[3] else "",
             "updated_at": str(r[4]) if r[4] else ""}
            for r in cur.fetchall()
        ]
    except Exception:
        _log.exception("chatael search_sessions error")
        return []


def _new_session(user_id: str = "") -> dict:
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session = {
        "id": "sess_" + uuid.uuid4().hex[:12],
        "title": "New Chat",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    ok = _save_session(session, user_id=user_id)
    if not ok:
        _log.warning("chatael new_session: PG save failed for %s", user_id or "(no user)")
    return session


# ── API Key helper (reuse from 9701) ──

def _verify_and_get_user_id(api_key: str) -> str:
    """Verify key against 9701 and return PG user UUID."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:9701/v1/user/me",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            email = result.get("email", "")
            if email and _pg():
                cur = _PG_CONN.cursor()
                cur.execute("SELECT id::text FROM users WHERE email = %s", (email,))
                r = cur.fetchone()
                if r:
                    return r[0]
            return email
    except Exception:
        return ""


class SpaHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.rstrip("/")
        if path.startswith("/api/sessions/search"):
            self._handle_search_sessions()
        elif path.startswith("/api/sessions/"):
            self._handle_get_session()
        elif path == "/api/sessions":
            self._handle_list_sessions()
        else:
            self._serve_static()

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/api/sessions/sync":
            self._handle_sync_session()
        elif path == "/api/sessions":
            self._handle_new_session()
        elif path == "/api/search":
            self._handle_search_web()
        elif path == "/api/windows":
            self._handle_windows_mcp()
        else:
            self._serve_static()

    def do_OPTIONS(self):
        self._send(b"", 204)

    def _send(self, data, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # client disconnected, safe to ignore

    def _json(self, data, status=200):
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _get_auth_key(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""

    def _serve_static(self):
        from urllib.parse import unquote
        import posixpath
        raw = self.path.split("?")[0]
        # Decode percent-encoding BEFORE any traversal checks so encoded
        # ".." (%2e%2e) cannot bypass the guard.
        path = unquote(raw).lstrip("/")
        if not path:
            path = "index.html"
        # Normalize and verify the resolved path stays inside DIST.
        norm = posixpath.normpath(path)
        if norm.startswith("..") or norm.startswith("/") or norm.startswith("~"):
            self._send(b"Not found", 404)
            return
        file = (DIST / norm).resolve()
        # Path traversal guard (defense in depth)
        try:
            file.relative_to(DIST.resolve())
        except ValueError:
            self._send(b"Not found", 404)
            return
        if file.exists() and file.is_file():
            content_type = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".png": "image/png",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }.get(file.suffix, "application/octet-stream")
            self._send_gzip(file.read_bytes(), content_type, file.suffix)
        else:
            # SPA fallback
            self._send_gzip((DIST / "index.html").read_bytes(), "text/html", ".html")

    # Text assets are gzip'd when the client accepts it (JS 246KB → ~78KB,
    # CSS 23KB → ~5KB). Compressed bytes are cached per file+size so a hot
    # static server never re-compresses the same asset.
    _gzip_cache: dict = {}

    def _send_gzip(self, data: bytes, content_type: str, suffix: str):
        if suffix not in (".html", ".js", ".css", ".json", ".svg"):
            self._send(data, content_type=content_type)
            return
        accept = self.headers.get("Accept-Encoding", "")
        if "gzip" not in accept.lower():
            self._send(data, content_type=content_type)
            return
        cache_key = (len(data), suffix)
        cached = SpaHandler._gzip_cache.get(cache_key)
        if cached is None:
            import gzip as _gz
            cached = _gz.compress(data, 6)
            # Cap the cache: a handful of assets, not unbounded
            if len(SpaHandler._gzip_cache) < 64:
                SpaHandler._gzip_cache[cache_key] = cached
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(cached)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # client disconnected, safe to ignore

    def _handle_list_sessions(self):
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        sessions = _list_sessions(user_id=uid)
        self._json({"success": True, "sessions": sessions})

    def _handle_get_session(self):
        session_id = self.path.rstrip("/").split("/")[-1]
        _validate_session_id(session_id)
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        session = _load_session(session_id, user_id=uid)
        if session:
            self._json({"success": True, "data": session})
        else:
            self._json({"success": False, "error": "Not found"}, 404)

    def _handle_sync_session(self):
        """Save a session uploaded from frontend localStorage."""
        body = self._read_body()
        session = body.get("session", {})
        if not session.get("id"):
            self._json({"success": False, "error": "Missing session id"}, 400)
            return
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        _save_session(session, user_id=uid)
        self._json({"success": True})

    def _handle_new_session(self):
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        session = _new_session(user_id=uid)
        self._json({"success": True, "data": session})

    def _handle_search_sessions(self):
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        q = (params.get("q") or [""])[0]
        if not q:
            self._json({"success": True, "sessions": []})
            return
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        sessions = _search_sessions(q, user_id=uid)
        self._json({"success": True, "sessions": sessions})

    def _handle_search_web(self):
        """Bing search via chatAEL's duckduckgo-style scraper."""
        body = self._read_body()
        query = body.get("query", "").strip()[:100]
        if not query:
            self._json({"success": False, "error": "query required"}, 400)
            return
        results = self._search_bing(query)
        self._json({"success": True, "data": results})

    def _handle_windows_mcp(self):
        """Forward a tool call to Windows-MCP (running on Windows host)."""
        body = self._read_body()
        action = body.get("action", "")
        params = body.get("params", {})

        # Windows-MCP auth key
        WINDOWS_MCP_KEY = "sk-aelvoxim-38179e1738a8b83daaf8145e5a85f7db5200753ab2100811"
        WINDOWS_MCP_URL = "http://172.24.80.1:8000"

        try:
            # First get a session from SSE endpoint
            sse_req = urllib.request.Request(
                WINDOWS_MCP_URL + "/sse",
                headers={"Authorization": f"Bearer {WINDOWS_MCP_KEY}"},
            )
            sse_resp = urllib.request.urlopen(sse_req, timeout=5)
            # Read the first event to get the session endpoint
            data = sse_resp.read(500).decode("utf-8")
            msg_endpoint = None
            for line in data.split("\n"):
                if line.startswith("data: "):
                    msg_endpoint = line[6:].strip()
            sse_resp.close()

            if not msg_endpoint:
                self._json({"success": False, "error": "Failed to get MCP session"}, 500)
                return

            # Build the MCP call
            messages_url = WINDOWS_MCP_URL + msg_endpoint
            mcp_body = json.dumps({
                "jsonrpc": "2.0",
                "id": str(int(time.time() * 1000)),
                "method": "tools/call",
                "params": {
                    "name": action,
                    "arguments": params,
                },
            }).encode()

            mcp_req = urllib.request.Request(
                messages_url,
                data=mcp_body,
                headers={
                    "Authorization": f"Bearer {WINDOWS_MCP_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            mcp_resp = urllib.request.urlopen(mcp_req, timeout=30)
            result = json.loads(mcp_resp.read().decode("utf-8"))
            self._json({"success": True, "data": result})
        except Exception as e:
            self._json({"success": False, "error": str(e)}, 500)

    def _search_bing(self, query: str, max_results: int = 5) -> list:
        import re, urllib.parse
        try:
            url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&count=" + str(max_results)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            results = []
            pairs = re.findall(
                r'<h2[^>]*><a[^>]*href="(https?://[^\"]+)"[^>]*>(.*?)</a>.*?<p[^>]*class="b_lineclamp[^\"]*"[^>]*>(.*?)</p>',
                html, re.DOTALL,
            )
            for url, title, snippet in pairs[:max_results]:
                title = re.sub(r'<[^>]+>', '', title).strip()
                snippet = re.sub(r'<[^>]+>', '', snippet).strip()
                if title:
                    results.append({"title": title, "snippet": snippet, "url": url})
            return results
        except Exception:
            return []


def serve(host="0.0.0.0", port=9702):
    _ensure_dirs()
    print(f"ChatAEL v2 started at http://{host}:{port}")
    print(f"  Serving: {DIST}")
    # Threading server: a slow request (PG query, SSE forward) must not block
    # other clients. The old single-threaded HTTPServer let CLOSE-WAIT
    # connections pile up until the backlog (5) filled and 9702 hung.
    server = ThreadingHTTPServer((host, port), SpaHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    serve()
