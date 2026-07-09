"""
aelvoxim_orchestrator — Aelvoxim Brain Orchestrator (port 9703)

大脑皮层 — Cortex layer above Aelvoxim base (9701).
All external input passes through here first.

Architecture (5 layers):
  1. Entry layer — API Key auth, SentriKit pre-check, rate limit
  2. Intent routing — coarse classify + fine-grained router (routing_rules.json)
  3. Long-term planning engine — goal decomposition, milestone tracking
  4. Scheduler — background tick, dispatches planner actions to 9701
  5. Forwarding — simple→9701 fast path, expert→local experts then 9701
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request as FastAPIRequest
from fastapi.responses import JSONResponse

# ── Backend URLs ──
AELVOXIM_URL = os.environ.get("AELVOXIM_URL", "http://127.0.0.1:9701")
SENTRIKIT_URL = os.environ.get("SENTRIKIT_URL", "https://127.0.0.1:8899")
SENTRIKIT_API_KEY = os.environ.get("SENTRIKIT_API_KEY", "")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

log = logging.getLogger("orchestrator")

# ── Globals ──
_router = None
_planner = None
_scheduler = None


# ═══════════════════════════════════════════
# Layer 0: Internal HTTP helpers
# ═══════════════════════════════════════════


def _call_aelvoxim(method, path, body=None, headers=None, timeout=60):
    """Call Aelvoxim API (9701). Same signature as original, keeps backward compat."""
    url = f"{AELVOXIM_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        if e.code == 403:
            raise HTTPException(e.code, detail=detail)
        raise HTTPException(e.code, detail=f"Aelvoxim API error: {detail}")


def _call_sentrikit(action, target, content=""):
    """Call SentriKit (8899) safety check."""
    url = f"{SENTRIKIT_URL}/api/safety/check"
    body = json.dumps({
        "action": action, "target": target[:500],
        "trigger": "orchestrator", "content": content[:2000],
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "X-API-Key": SENTRIKIT_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {"allowed": True, "reason": "SentriKit unavailable, continuing"}


# ═══════════════════════════════════════════
# Layer 1: Coarse intent classification (kept from original)
# ═══════════════════════════════════════════


def _classify_intent(query: str) -> str:
    """Coarse intent: chat / reason / learn / system."""
    q = query.lower()
    if any(kw in q for kw in ["learn", "study", "research", "teach", "train"]):
        return "learn"
    if any(kw in q for kw in ["status", "health", "memory", "config", "settings"]):
        return "system"

    reason_keywords = ["if", "would", "should", "why", "what if",
                       "analyze", "evaluate", "difference",
                       "比较", "哪个", "应该", "为什么", "which", "better"]
    reason_phrases = ["what if", "compare", "difference between",
                      "why is", "why do", "why does", "why would",
                      "应该怎么", "为什么这样", "哪个更好",
                      "how does", "how would"]
    if any(p in q for p in reason_phrases):
        return "reason"
    match_count = sum(1 for kw in reason_keywords if kw in q)
    if match_count >= 2:
        return "reason"
    return "chat"


# ═══════════════════════════════════════════
# Layer 2: Experts (kept from original, used for expert-level routing)
# ═══════════════════════════════════════════


def _run_experts(query: str, user_id: str = "", session_id: str = "",
                 expert_subset: Optional[list] = None) -> Dict:
    """Run ExpertOrchestrator (optionally filtered to a subset of experts)."""
    from aelvoxim.experts.base import ExpertInput
    from aelvoxim.experts.orchestrator import ExpertOrchestrator

    inp = ExpertInput(
        query=query,
        user_id=user_id,
        session_id=session_id,
        context={},
    )
    orch = ExpertOrchestrator()
    result = orch.think(inp, expert_filter=expert_subset)  # uses expert_filter param
    return result


def _build_expert_context(expert_result: Dict) -> str:
    """Build [Expert Analysis] context string."""
    if not expert_result.get("expert_results"):
        return ""
    lines = []
    for er in expert_result["expert_results"]:
        if er.error:
            lines.append(f"  [{er.expert_name}] unavailable")
        else:
            lines.append(f"  [{er.expert_name}] (confidence={er.confidence}) {er.opinion[:150]}")
    return "\n[Expert Analysis]\n" + "\n".join(lines) + "\n"


# ═══════════════════════════════════════════
# Layer 3: User-friendly hint (from original)
# ═══════════════════════════════════════════


def _build_user_hint(reason="", query=""):
    """Build user-friendly safety block hint."""
    r = reason.lower()
    q = query.lower()
    has_cn = any('\u4e00' <= c <= '\u9fff' for c in query)
    hints_cn, hints_en = [], []

    if '~' in q or 'tilde' in r or 'path' in r:
        hints_cn.append("路径中请使用完整绝对路径，例如 /home/user/file")
        hints_en.append("Use full absolute paths instead of ~/file")
    if '@' in q:
        hints_cn.append("邮箱地址等含 @ 的内容请用文字描述")
        hints_en.append("Avoid sending email addresses directly in chat")
    if 'api_key' in r or 'key' in r or 'credential' in r or 'leak' in r:
        hints_cn.append("敏感信息（API Key、密码）请在配置文件中设置")
        hints_en.append("Sensitive info should be set in config files")
    if 'drop' in r or 'delete' in r or 'destructive' in r:
        hints_cn.append("危险操作已被阻止，请先说明用途")
        hints_en.append("Destructive operations are blocked")
    if 'injection' in r or 'prompt' in r:
        hints_cn.append("系统指令注入已被阻止")
        hints_en.append("System instruction injection is blocked")
    if 'sql' in r or 'rm ' in r or 'chmod' in r:
        hints_cn.append("危险命令已被阻止")
        hints_en.append("Potentially dangerous commands are blocked")

    if has_cn:
        body = "；".join(hints_cn) if hints_cn else "您的请求因安全规则被临时阻止。请换一种方式描述。"
    else:
        body = "; ".join(hints_en) if hints_en else "Your request was blocked by safety rules. Please rephrase."
    return body


# ═══════════════════════════════════════════
# App creation
# ═══════════════════════════════════════════


def create_app() -> FastAPI:
    global _router, _planner, _scheduler

    app = FastAPI(
        title="Aelvoxim Orchestrator",
        version="0.2.0",
        description="Brain cortex — routes all input to the right processor",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Lazy init on first request ──
    @app.on_event("startup")
    async def _startup():
        global _router, _planner, _scheduler
        # Init router
        from .router import Router as _Router
        _router = _Router()

        # Init planner
        from aelvoxim.planner import LongTermPlanner
        _planner = LongTermPlanner()

        print("  皮层 Orchestrator 9703 已启动")
        print(f"  Aelvoxim backend: {AELVOXIM_URL}")
        print(f"  Routes: /orchestrate, /health, /planner/*, /experts")

    # ── Routes ──

    @app.get("/")
    async def root():
        return {
            "name": "Aelvoxim Orchestrator",
            "version": "0.2.0",
            "endpoints": {
                "orchestrate": "POST /orchestrate",
                "health": "GET /health",
                "experts": "GET /experts",
                "planner": "GET/POST /planner/*",
            },
        }

    @app.get("/health")
    async def health():
        result = {"service": "orchestrator", "status": "ok", "dependencies": {}}
        # Check 9701
        try:
            mc = _call_aelvoxim("GET", "/v1/health")
            result["dependencies"]["aelvoxim"] = {"status": "ok"}
        except Exception:
            result["dependencies"]["aelvoxim"] = {"status": "error"}
            result["status"] = "degraded"
        # Check SentriKit
        try:
            sk = _call_sentrikit("check", "orchestrator_health", content="health")
            result["dependencies"]["sentrikit"] = {"status": "ok" if sk.get("allowed") else "blocked"}
        except Exception:
            result["dependencies"]["sentrikit"] = {"status": "unavailable"}
        # Planner status
        if _planner:
            result["plans"] = len(_planner.list_plans())
        return result

    @app.get("/experts")
    async def list_experts():
        return {
            "experts": [
                {"name": "memory", "description": "Memory retrieval and confidence scoring"},
                {"name": "logic", "description": "Conflict detection and reasoning"},
                {"name": "ethics", "description": "Ethical principles and priority matrix"},
                {"name": "emotion", "description": "Sentiment analysis and tone suggestion"},
                {"name": "creative", "description": "LLM-powered creative alternatives"},
                {"name": "safety", "description": "SentriKit red-line safety rules (R0-R28)"},
            ],
            "routing_types": list(
                _router._task_routes.keys() if _router else ["chat", "code", "analysis", "creative", "security", "planning"]
            ),
        }

    # ═══════════════════════════════════════════
    # Core endpoint: /orchestrate
    # ═══════════════════════════════════════════

    @app.post("/orchestrate")
    async def orchestrate(request: dict, fastapi_req: FastAPIRequest):
        """Main entry point — receives ALL input, decides routing."""
        query = request.get("query") or ""
        messages = request.get("messages", [])
        user_id = request.get("user_id", "")
        session_id = request.get("session_id", "")
        mode = request.get("mode", "auto")
        api_key = request.get("api_key", request.get("authorization", ""))

        # Also extract Authorization from HTTP header
        auth_header = fastapi_req.headers.get("authorization", "")
        if auth_header and not api_key:
            api_key = auth_header

        # Extract query from messages
        if not query and messages:
            for m in reversed(messages):
                if m.get("role") == "user":
                    query = m.get("content", "")
                    break
        if not query:
            raise HTTPException(400, detail="query or messages is required")

        coarse_intent = mode if mode != "auto" else _classify_intent(query)

        # Auth headers for downstream
        headers = {}
        if api_key:
            if api_key.startswith("Bearer "):
                headers["Authorization"] = api_key
            elif api_key.startswith("sk-"):
                headers["Authorization"] = f"Bearer {api_key}"

        # ── Learn: submit to 9701 Learner ──
        if coarse_intent == "learn":
            return _handle_learn(query, headers)

        # ── System: direct return ──
        if coarse_intent == "system":
            return _handle_system(query, headers)

        # ── Fine-grained routing ──
        routing = _router.classify(query) if _router else {"routing_type": "chat", "level": "simple", "experts": ["memory"], "risky": False}

        # SentriKit pre-check
        try:
            sk = _call_sentrikit("check", query, content=query)
            if not sk.get("allowed", True):
                hint = _build_user_hint(reason=sk.get("reason", ""), query=query)
                raise HTTPException(403, detail=hint)
        except HTTPException:
            raise
        except Exception:
            pass

        # ── Expert level: run local experts ──
        extra_context = ""
        expert_result = None
        if routing["level"] == "expert" or coarse_intent == "reason":
            try:
                expert_result = _run_experts(
                    query, user_id, session_id,
                    expert_subset=routing.get("experts"),
                )
                if expert_result.get("blocked"):
                    raise HTTPException(
                        403,
                        detail=f"Request blocked: {expert_result.get('opinion', 'Blocked by expert system')}",
                    )
                extra_context = _build_expert_context(expert_result)
            except HTTPException:
                raise
            except Exception:
                log.exception("Expert run failed, continuing without expert analysis")

        # ── Forward to 9701 with post-generation metacognition check ──
        if not messages:
            messages = [{"role": "user", "content": query}]
        else:
            if extra_context:
                sys_msg = next((m for m in messages if m.get("role") == "system"), None)
                if sys_msg:
                    sys_msg["content"] += "\n" + extra_context
                else:
                    messages.insert(0, {"role": "system", "content": extra_context})

        # 智能记忆开关：仅当用户主动询问记忆时才启用
        _memory_keywords = ["记得", "还记得", "记住", "忘记",
                            "remember", "forget", "recall",
                            "我上次", "我之前", "我前面"]
        skip_memory = not any(kw in query.lower() for kw in _memory_keywords)

        body = {
            "messages": messages,
            "temperature": request.get("temperature", 0.7),
            "max_tokens": request.get("max_tokens", 2000),
            "_orchestrator": {
                "skip_memory": skip_memory,
                "routing_type": routing["routing_type"],
                "level": routing["level"],
                "experts_ran": bool(expert_result),
            },
        }

        from aelvoxim.control.controller import GenerationController

        controller = GenerationController(
            max_retries=3,
            llm_check_enabled=False,  # rules engine only — no extra LLM calls
        )
        reply = controller.generate(
            query=query,
            system_prompt="",
            topic=query,
            call_llm=lambda _: _call_aelvoxim(
                "POST", "/v1/llm/chat",
                body=body,
                headers=headers,
            ).get("content", ""),
        )

        result = {"content": reply.get("text", "")}

        # ── Build reasoning trace ──
        reasoning_steps = []

        # 1. Routing decision
        reasoning_steps.append({
            "type": "routing",
            "detail": f"路由: {routing['routing_type']} (层级={routing['level']})",
        })

        # 2. Memory flag
        if skip_memory:
            reasoning_steps.append({"type": "memory", "detail": "记忆注入: 关闭（当前问题与记忆无关）"})
        else:
            reasoning_steps.append({"type": "memory", "detail": "记忆注入: 开启（用户主动询问记忆）"})

        # 3. Experts
        if expert_result:
            votes = expert_result.get("vote", {})
            reasoning_steps.append({
                "type": "experts",
                "detail": f"专家层: {votes.get('experts_voted', 0)} 位专家参与, 置信度={expert_result.get('confidence', 'N/A')}",
            })

        # 4. Metacognition check
        if reply.get("issues", 0) > 0:
            reasoning_steps.append({
                "type": "metacog",
                "detail": f"元认知检查: 发现 {reply['issues']} 个问题, 重试 {reply.get('retries', 0)} 次",
            })
        else:
            reasoning_steps.append({"type": "metacog", "detail": "元认知检查: 通过（无问题）"})

        result["_reasoning"] = reasoning_steps

        # Strip tool error JSON that leaked into the response
        import re as _re_json
        result["content"] = _re_json.sub(
            r'\{\s*"success":\s*false\s*,.*?"[^}]+?\}\s*',
            "", result["content"]
        ).strip()

        if expert_result:
            result["_orchestrator"] = {
                "confidence": expert_result.get("confidence"),
                "experts_voted": expert_result.get("vote", {}).get("experts_voted", 0),
            }

        log.info(
            "Metacog: issues=%d retries=%d",
            reply.get("issues", 0), reply.get("retries", 0),
        )
        return result

    # ── Planner endpoints ──

    @app.post("/planner/create")
    async def planner_create(body: dict):
        if not _planner:
            raise HTTPException(503, detail="Planner not available")
        goal = body.get("goal", "").strip()
        if not goal:
            raise HTTPException(400, detail="goal is required")
        plan = _planner.create_plan(goal, source=body.get("source", "user"))
        return {"status": "created", "plan": plan.to_dict()}

    @app.get("/planner/list")
    async def planner_list():
        if not _planner:
            return {"plans": []}
        return {"plans": _planner.list_plans()}

    @app.get("/planner/next")
    async def planner_next():
        if not _planner:
            return {"action": None}
        return {"action": _planner.next_action()}

    @app.delete("/planner/{plan_id}")
    async def planner_delete(plan_id: str):
        if not _planner:
            raise HTTPException(503, detail="Planner not available")
        ok = _planner.delete_plan(plan_id)
        if not ok:
            raise HTTPException(404, detail="Plan not found")
        return {"status": "deleted"}

    return app


# ═══════════════════════════════════════════
# Handlers (kept from original, cleaned)
# ═══════════════════════════════════════════


def _handle_learn(query: str, headers: dict) -> dict:
    """Submit a learning task to 9701 /v1/task."""
    try:
        result = _call_aelvoxim(
            "POST", f"/v1/task?goal={urllib.parse.quote(query)}&task_type=learn",
            body=None, headers=headers, timeout=30,
        )
        if result and result.get("task_id"):
            return {"status": "accepted", "task_id": result["task_id"], "message": "Learning task submitted"}
        return {"status": "accepted", "message": "Learning direction queued"}
    except HTTPException:
        return {"status": "error", "message": "Learning task submission failed"}
    except Exception:
        log.exception("Task submission failed")
        return {"status": "error", "message": "Learning task submission failed"}


def _make_llm_caller(headers: dict):
    """Return a callable that sends a prompt to 9701 and returns text."""
    def _call(prompt: str) -> str:
        try:
            body = {
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 2000,
            }
            result = _call_aelvoxim(
                "POST", "/v1/llm/chat", body=body, headers=headers,
            )
            return result.get("content", "") or ""
        except Exception as e:
            log.warning("LLM chunk call failed: %s", e)
            return ""
    return _call


def _handle_system(query: str, headers: dict) -> dict:
    """Handle system queries by forwarding to 9701 /v1/health."""
    try:
        health = _call_aelvoxim("GET", "/v1/health", timeout=5)
        return {"status": "ok", "system": health}
    except Exception as e:
        return {"status": "degraded", "detail": str(e)[:200]}


# ═══════════════════════════════════════════
# Convenience runner
# ═══════════════════════════════════════════

app = create_app()


def start_server(host: str = "127.0.0.1", port: int = 9703):
    import uvicorn
    uvicorn.run("aelvoxim_orchestrator.app:app", host=host, port=port, log_level="info")
