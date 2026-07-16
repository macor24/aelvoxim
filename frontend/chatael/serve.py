"""
chatael-v2 serve.py — Static file server with PG + JSON fallback persistence.

Provides API endpoints for session/message persistence.
PG primary, JSON file fallback (survives PG outages).
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import os
import time
import urllib.request
import uuid

DIST = Path(__file__).parent / "dist"
DATA_DIR = Path(__file__).parent / "data"
PORT = 9702

# ── PG connection (optional) ──
_PG_CONN = None
_PG_DSN = os.environ.get(
    "CHATAEL_DATABASE_URL",
    "host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass",
)
try:
    import psycopg2 as _pg2
    _PG_CONN = _pg2.connect(_PG_DSN)
except Exception:
    _PG_CONN = None


def _pg() -> bool:
    return _PG_CONN is not None


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
            "title": meta.get("title", "New Chat"),
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
                "title": meta.get("title", "New Chat"),
                "message_count": meta.get("message_count", 0),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            })
        except Exception:
            pass
    return results


# ── Session persistence (PG primary, JSON fallback) ──


def _save_session(session: dict, user_id: str = ""):
    """Save a session to PG with JSON fallback."""
    if _pg():
        try:
            cur = _PG_CONN.cursor()
            now = session.get("updated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            cur.execute(
                "INSERT INTO chat_sessions (id, title, message_count, created_at, updated_at, user_id) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, message_count=EXCLUDED.message_count, updated_at=EXCLUDED.updated_at;",
                (session["id"], session.get("title", "New Chat"), len(session.get("messages", [])),
                 session.get("created_at", now), now, user_id or None),
            )
            cur.execute("DELETE FROM chat_messages WHERE session_id = %s;", (session["id"],))
            for m in session.get("messages", []):
                cur.execute(
                    "INSERT INTO chat_messages (session_id, role, content, metadata, created_at) "
                    "VALUES (%s, %s, %s, '{}'::jsonb, %s);",
                    (session["id"], m.get("role", "user"), m.get("content", ""),
                     m.get("timestamp", now)),
                )
            _PG_CONN.commit()
        except Exception:
            pass  # Fall through to JSON fallback
    # JSON fallback (always, for PG-down resilience)
    _save_session_json(session)


def _load_session(session_id: str, user_id: str = "") -> dict | None:
    """Load a session from PG, then JSON fallback."""
    if _pg():
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
                    "id": r[0], "title": r[1] or "New Chat",
                    "created_at": str(r[2]) if r[2] else "",
                    "updated_at": str(r[3]) if r[3] else "",
                    "messages": msgs,
                }
        except Exception:
            pass
    return _load_session_json(session_id)


def _list_sessions(user_id: str = "") -> list:
    """List sessions — PG then JSON fallback. PG requires user_id."""
    if _pg() and user_id:
        try:
            cur = _PG_CONN.cursor()
            cur.execute(
                "SELECT id, title, message_count, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC;",
                (user_id,),
            )
            return [
                {"id": r[0], "title": r[1] or "New Chat",
                 "message_count": r[2] or 0,
                 "created_at": str(r[3]) if r[3] else "",
                 "updated_at": str(r[4]) if r[4] else ""}
                for r in cur.fetchall()
            ]
        except Exception:
            pass
    # JSON fallback (works without auth)
    return _list_sessions_json()


def _search_sessions(q: str, user_id: str = "") -> list:
    """Search sessions by keyword in messages."""
    if _pg() and user_id:
        try:
            cur = _PG_CONN.cursor()
            cur.execute(
                "SELECT DISTINCT s.id, s.title, s.message_count, s.created_at, s.updated_at "
                "FROM chat_sessions s JOIN chat_messages m ON m.session_id = s.id "
                "WHERE LOWER(m.content) LIKE %s AND s.user_id = %s ORDER BY s.updated_at DESC;",
                ('%' + q.lower() + '%', user_id),
            )
            return [
                {"id": r[0], "title": r[1] or "New Chat",
                 "message_count": r[2] or 0,
                 "created_at": str(r[3]) if r[3] else "",
                 "updated_at": str(r[4]) if r[4] else ""}
                for r in cur.fetchall()
            ]
        except Exception:
            pass
    # JSON fallback — search content
    ql = q.lower()
    results = []
    for f in sorted(_SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(f.read_text())
            msg_path = _messages_path(meta["id"])
            if not msg_path.exists():
                continue
            msgs = json.loads(msg_path.read_text())
            if any(ql in m.get("content", "").lower() for m in msgs):
                results.append({
                    "id": meta["id"],
                    "title": meta.get("title", "New Chat"),
                    "message_count": meta.get("message_count", 0),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                })
        except Exception:
            pass
    return results


def _new_session(user_id: str = "") -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    session = {
        "id": "sess_" + uuid.uuid4().hex[:12],
        "title": "New Chat",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    _save_session(session, user_id=user_id)
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
        self.wfile.write(data)

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
        path = self.path.split("?")[0].lstrip("/")
        if not path:
            path = "index.html"
        # Block path traversal characters early
        if ".." in path or path.startswith("/") or path.startswith("~"):
            self._send(b"Not found", 404)
            return
        # Path traversal protection: resolve and verify it stays inside DIST
        try:
            full_path = (DIST / path).resolve()
            full_path.relative_to(DIST.resolve())
        except (ValueError, RuntimeError):
            self._send(b"Not found", 404)
            return
        if full_path.exists() and full_path.is_file():
            content_type = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".png": "image/png",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }.get(full_path.suffix, "application/octet-stream")
            self._send(full_path.read_bytes(), content_type=content_type)
        else:
            # SPA fallback
            self._send((DIST / "index.html").read_bytes(), content_type="text/html")

    def _handle_list_sessions(self):
        key = self._get_auth_key()
        uid = _verify_and_get_user_id(key) if key else ""
        sessions = _list_sessions(user_id=uid)
        self._json({"success": True, "sessions": sessions})

    def _handle_get_session(self):
        session_id = self.path.rstrip("/").split("/")[-1]
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
    server = HTTPServer((host, port), SpaHandler)
    server.serve_forever()


if __name__ == "__main__":
    serve()
