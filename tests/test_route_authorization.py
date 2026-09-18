"""Route authorization classification — the frozen table (2026-09-18).

WHY THIS TEST EXISTS
--------------------
Authorization in this codebase was added per-route by hand: 79 routes, three
levels, no model. Audits kept finding leaks a greps-and-eyeballs pass missed —
4 admin endpoints that accepted any customer key, /v1/memory/* with no tenant
dimension at all, /v1/config list+write open to any account. This test turns
"did someone remember the dependency?" into a CI failure instead of a
production leak.

HOW TO EXTEND
-------------
Add the route to TABLE with its intended level. A route that is missing from
TABLE, or whose level drifts from TABLE, fails the test and prints both.

  public = no auth (login/register/landing/health/static)
  user   = any valid API key (customer-facing)
  admin  = admin role required (operator-facing)
  ws     = WebSocket, authenticated inside the handler (auth message + token)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.routing import APIRoute, APIWebSocketRoute  # noqa: E402

from aelvoxim.server import create_app  # noqa: E402

# Paths that must never be reachable with a plain customer key. Guards the
# specific historical failure mode: an operator-looking route wired to
# _verify_key (or to nothing at all).
PRIVILEGED_PREFIXES = ("/v1/admin/", "/v1/logs", "/v1/llm/config", "/v1/config", "/v1/memory")

TABLE = {
    ("GET", ""): "public",
    ("GET", "/"): "public",
    ("GET", "/api"): "public",
    ("GET", "/api/v1/metacore/intent"): "public",
    ("GET", "/api/v1/metacore/intent/mock"): "public",
    ("GET", "/chatael"): "public",
    ("GET", "/chatael/{path:path}"): "public",
    ("GET", "/public/llm/test"): "user",
    ("GET", "/v1"): "public",
    ("GET", "/v1/admin/cognition"): "admin",
    ("GET", "/v1/admin/cognitive"): "admin",
    ("GET", "/v1/admin/dash-full"): "public",
    ("GET", "/v1/admin/data"): "admin",
    ("GET", "/v1/admin/gateway/connections"): "admin",
    ("GET", "/v1/admin/knowledge-graph"): "admin",
    ("GET", "/v1/admin/learn-directory"): "admin",
    ("GET", "/v1/admin/learn-directory/scan-now"): "admin",
    ("GET", "/v1/admin/learn-directory/status"): "admin",
    ("GET", "/v1/admin/learner/start"): "admin",
    ("GET", "/v1/admin/learner/status"): "admin",
    ("GET", "/v1/admin/learner/stop"): "admin",
    ("GET", "/v1/admin/migrate-users"): "admin",
    ("GET", "/v1/admin/overview"): "admin",
    ("GET", "/v1/admin/panel"): "public",
    ("GET", "/v1/admin/skill-timeline"): "admin",
    ("GET", "/v1/admin/stats"): "admin",
    ("GET", "/v1/admin/update-user"): "admin",
    ("GET", "/v1/admin/user/{email}"): "admin",
    ("GET", "/v1/admin/users"): "admin",
    ("GET", "/v1/auth/api-keys"): "user",
    ("GET", "/v1/auth/api-keys/{key_id}"): "user",
    ("GET", "/v1/auth/api-keys/{key_id}/refresh"): "user",
    ("GET", "/v1/auth/change-password"): "user",
    ("GET", "/v1/auth/forgot-password"): "public",
    ("GET", "/v1/auth/license"): "user",
    ("GET", "/v1/auth/login"): "public",
    ("GET", "/v1/auth/register"): "public",
    ("GET", "/v1/auth/reset-password"): "public",
    ("GET", "/v1/auth/send-verification"): "public",
    ("GET", "/v1/auth/verify-email"): "public",
    ("GET", "/v1/brain/classify"): "public",
    ("GET", "/v1/brain/experts"): "public",
    ("GET", "/v1/brain/report"): "user",
    ("GET", "/v1/brain/reports"): "user",
    ("GET", "/v1/brain/status"): "user",
    ("GET", "/v1/brain/think"): "public",
    ("GET", "/v1/chat/sessions"): "user",
    ("GET", "/v1/chat/sessions/{session_id}"): "user",
    ("GET", "/v1/chat/sessions/{session_id}/messages"): "user",
    ("GET", "/v1/cognition/selfmodel"): "user",
    ("GET", "/v1/config"): "admin",
    ("GET", "/v1/config/{key}"): "admin",
    ("GET", "/v1/cortex/planner/create"): "public",
    ("GET", "/v1/cortex/planner/list"): "public",
    ("GET", "/v1/cortex/planner/next"): "public",
    ("GET", "/v1/cortex/planner/{plan_id}"): "public",
    ("GET", "/v1/ethics/gates"): "user",
    ("GET", "/v1/ethics/update"): "admin",
    ("GET", "/v1/gateway/context"): "user",
    ("GET", "/v1/gateway/control/{action}"): "user",
    ("GET", "/v1/gateway/execute"): "user",
    ("GET", "/v1/health"): "public",
    ("GET", "/v1/llm/chat"): "user",
    ("GET", "/v1/llm/chat/stream"): "user",
    ("GET", "/v1/llm/config"): "admin",
    ("GET", "/v1/llm/test"): "user",
    ("GET", "/v1/logs"): "admin",
    ("GET", "/v1/memory"): "admin",
    ("GET", "/v1/memory/search"): "admin",
    ("GET", "/v1/memory/{key}"): "admin",
    ("GET", "/v1/orchestrate"): "public",
    ("GET", "/v1/status/planner"): "public",
    ("GET", "/v1/task"): "user",
    ("GET", "/v1/task/{task_id}"): "user",
    ("GET", "/v1/user/me"): "user",
    ("GET", "/v1/webhook/subscribe"): "user",
    ("GET", "/v1/webhook/subscribe/{sub_id}"): "user",
    ("GET", "/v1/webhook/subscriptions"): "user",
    ("GET", "/v1/webhook/test-delivery"): "user",
    ("POST", "/orchestrate"): "public",
    ("POST", "/v1/windows-mcp"): "user",
    ("WS", "/v1/gateway/ws"): "ws",
}


def _dep_names(dependant, seen=None):
    if dependant is None:
        return set()
    seen = seen if seen is not None else set()
    out = set()
    for d in list(getattr(dependant, "dependencies", []) or []):
        call = getattr(d, "call", None)
        if call is not None:
            nm = getattr(call, "__name__", str(call))
            out.add(nm)
            if nm not in seen:
                seen.add(nm)
                out |= _dep_names(d, seen)
    return out


def _level_of(names):
    return ("admin" if "_require_admin" in names
            else "user" if "_verify_key" in names else "public")


def _collect():
    app = create_app()
    rows = []
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            for c in r.effective_route_contexts():
                methods = getattr(getattr(c, "from_api_route", None), "methods", None) or set()
                rows.append((sorted((methods or {"GET"}) - {"HEAD", "OPTIONS"}), c.path,
                             _level_of(_dep_names(getattr(c, "dependant", None)))))
        elif isinstance(r, APIRoute):
            rows.append((sorted(r.methods - {"HEAD", "OPTIONS"}), r.path,
                         _level_of(_dep_names(r.dependant))))
        elif isinstance(r, APIWebSocketRoute):
            rows.append((["WS"], r.path, "ws"))
    return {(m, p): l for ms, p, l in rows for m in ms}


def test_every_route_is_classified():
    got = _collect()
    problems = []
    for key in sorted(got):
        if key not in TABLE:
            problems.append(f"未分级的新路由: {key} 实测={got[key]} → 先在 TABLE 声明意图")
        elif TABLE[key] != got[key]:
            problems.append(f"分级漂移: {key} TABLE={TABLE[key]} 实测={got[key]}")
    for key in sorted(TABLE):
        if key not in got:
            problems.append(f"表中有陈旧项(路由已删或改名?): {key}")
    assert not problems, "路由授权分级异常:\n" + "\n".join(problems)


def test_privileged_paths_are_admin_only():
    # Deliberate exceptions: these two are the admin HTML shell (login form +
    # JS). They are served without auth ON PURPOSE — public reachability is
    # blocked at nginx (404 for /v1/admin/*) and the panel is only reachable
    # through the loopback SSH tunnel; every data endpoint behind them is
    # admin-gated. See docs/plans/2026-09-18-admin-surface-independent-entry.md
    # A NEW operator-looking route that is public or merely key-gated still fails.
    page_exceptions = {("GET", "/v1/admin/panel"), ("GET", "/v1/admin/dash-full")}
    got = _collect()
    bad = [(k, v) for k, v in sorted(got.items())
           if any(k[1].startswith(p) or k[1] == p for p in PRIVILEGED_PREFIXES)
           and v != "admin" and k not in page_exceptions]
    assert not bad, ("下列运维面路由没有 admin 角色校验(历史漏洞复发):\n"
                     + "\n".join(f"  {k} -> {v}" for k, v in bad))


def test_table_is_not_empty():
    assert len(TABLE) > 50, "分级表异常缩小, 检查采集逻辑"
