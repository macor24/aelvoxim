"""
metacore.server.routes_chat — LLM chat and test endpoints.

Routes:
    POST /v1/llm/chat       — Chat with configured LLM
    POST /v1/llm/test       — Test LLM connection
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from .routes import _verify_key

router = APIRouter()


@router.post("/llm/chat")
async def llm_chat(
    request: dict,
    user: dict = Depends(_verify_key),
):
    """Chat with the configured LLM. Accepts OpenAI-compatible messages format.

    Optional fields:
      - mode: "simple" (default) | "auto" | "expert"
              "auto" runs cortex routing (router rules + experts + GenerationController).
              "expert" forces expert-level processing.
    """
    from ..learn.extract import call_llm_if_available
    from ..learn.llm import ModelConfig
    from .service_chat import chat_pipeline

    llm = call_llm_if_available()
    if not llm:
        raise HTTPException(400, detail="no LLM configured")
    call_fn, model = llm
    mc = model if isinstance(model, ModelConfig) else ModelConfig()
    messages = request.get("messages", [])
    temperature = request.get("temperature", 0.7)
    max_tokens = request.get("max_tokens", 4096)
    mode = request.get("mode", "simple")
    if not messages:
        raise HTTPException(400, detail="missing messages")

    # Backward compat: _orchestrator metadata from old 9703 calls
    skip_experts = bool(request.get("_orchestrator", {}).get("experts_ran", False))
    skip_memory = bool(request.get("_orchestrator", {}).get("skip_memory", False))

    result = chat_pipeline(call_fn, mc, messages, user, temperature, max_tokens,
                           skip_experts=skip_experts, skip_memory=skip_memory, mode=mode)

    if result.get("blocked"):
        raise HTTPException(403, detail=str(result.get("reason", "Blocked by safety rules")))

    return {
        "content": result.get("text") or "",
        "model": model.name if hasattr(model, 'name') else "deepseek-chat",
    }


@router.post("/llm/test")
async def test_llm(body: dict, user: dict = Depends(_verify_key)):
    """Test the configured LLM with a simple message."""
    from ..learn.extract import call_llm_if_available
    import json, urllib.request

    llm = call_llm_if_available()
    if llm:
        call_fn, model = llm
        try:
            text = call_fn(
                model=model,
                system_prompt="You are a helpful assistant.",
                user_message="Say OK",
                max_tokens=10,
            )
            provider = model.get("provider", "deepseek") if isinstance(model, dict) else "configured"
            return {"status": "ok", "response": (text or "")[:100], "provider": provider}
        except Exception as e:
            raise HTTPException(502, detail=f"LLM call failed: {e}")

    api_key = body.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(400, detail="No LLM configured and no api_key provided")
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 10,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        return {
            "status": "ok",
            "response": result.get("choices", [{}])[0].get("message", {}).get("content", ""),
        }


@router.post("/orchestrate")
async def orchestrate_compat(request: dict):
    """Backward-compatible endpoint for ChatAEL-v2 frontend (formerly 9703 orchestrator)."""
    return await _handle_orchestrate(request)


async def _handle_orchestrate(request: dict) -> dict:
    """Shared orchestrate handler — callable from /orchestrate and /v1/orchestrate."""
    from ..learn.extract import call_llm_if_available
    from ..learn.llm import ModelConfig
    from .service_chat import chat_pipeline
    from .auth import find_user

    api_key = request.get("api_key", "")
    if not api_key:
        raise HTTPException(401, detail="api_key is required")
    user = find_user(api_key)
    if not user:
        raise HTTPException(401, detail="unknown API key")
    ok, reason = _check_quota_sync(user)
    if not ok:
        raise HTTPException(429, detail=reason)

    llm = call_llm_if_available()
    if not llm:
        raise HTTPException(400, detail="no LLM configured")
    call_fn, model = llm
    mc = model if isinstance(model, ModelConfig) else ModelConfig()

    messages = request.get("messages", [])
    query = request.get("query", "")
    session_id = request.get("session_id", "")
    mode = request.get("mode", "auto")
    temperature = request.get("temperature", 0.7)
    max_tokens = request.get("max_tokens", 4096)

    # Build messages list: if query provided and messages empty, create single message
    if not messages and query:
        messages = [{"role": "user", "content": query}]

    if not messages:
        raise HTTPException(400, detail="missing messages")

    result = chat_pipeline(call_fn, mc, messages, user, temperature, max_tokens,
                           skip_experts=False, skip_memory=False, mode=mode)

    if result.get("blocked"):
        raise HTTPException(403, detail=str(result.get("reason", "Blocked by safety rules")))

    text = result.get("text") or ""
    return {
        "content": text,
        "model": model.name if hasattr(model, 'name') else "deepseek-chat",
    }


def _check_quota_sync(user: dict) -> tuple[bool, str]:
    """Synchronous quota check (no async Depends)."""
    from .auth import check_quota
    return check_quota(user)


@router.get("/chat/sessions")
async def list_sessions(limit: int = 50, user: dict = Depends(_verify_key)):
    """List recent chat sessions for the current user."""
    from ..storage.db import get_sessions_from_pg
    email = user.get("email", "")
    if not email:
        return {"sessions": [], "error": "no email"}
    return {"sessions": get_sessions_from_pg(email=email, limit=limit)}


@router.post("/chat/sessions")
async def sync_session(request: dict, user: dict = Depends(_verify_key)):
    """Sync a session from frontend to PG."""
    from ..storage.db import save_session_to_pg
    session = request.get("session", {})
    if not session.get("id"):
        raise HTTPException(400, detail="missing session.id")
    _uid = user.get("id") or user.get("user_id", "")
    save_session_to_pg({
        "id": session["id"],
        "user_id": str(_uid) if _uid else "",
        "title": session.get("title", "新对话"),
        "messages": [],
    })
    return {"success": True, "pg_id": session["id"]}


@router.get("/chat/sessions/{session_id}")
async def get_session_messages(session_id: str, user: dict = Depends(_verify_key)):
    """Get messages for a chat session (user-scoped)."""
    from ..storage.db import get_messages_from_pg, fetch_dict
    # Verify session belongs to current user
    uid = str(user.get("user_id") or user.get("id", ""))
    if uid and uid != "None":
        owner = fetch_dict(
            "SELECT user_id FROM chat_sessions WHERE id = %s AND user_id = %s::uuid",
            (session_id, uid),
        )
        if not owner:
            return {"messages": []}
    return {"messages": get_messages_from_pg(session_id)}


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(_verify_key)):
    """Delete a chat session and its messages."""
    from ..storage.db import delete_session_from_pg
    uid = user.get("id") or user.get("user_id", "")
    ok = delete_session_from_pg(session_id, user_id=str(uid))
    return {"success": ok}


@router.post("/llm/chat/stream")
async def llm_chat_stream(
    request: dict,
    user: dict = Depends(_verify_key),
):
    """SSE streaming chat. Same pipeline as /v1/llm/chat, but yields tokens
    one-by-one via Server-Sent Events. Pre-LLM phases run the same code."""
    from ..learn.extract import call_llm_if_available
    from ..learn.llm import ModelConfig, call_llm_stream
    from .service_chat import (
        build_system_prompt, build_conversation_history, inject_system_time,
        inject_topic_anchor, build_identity_prefix, process_memory_commands,
        inject_memory_status, inject_safety_and_metacog, enhance_with_knowledge,
        inject_memory_context, inject_security_context, run_safety_check,
        _is_reference_phrase, _get_search_query, _needs_real_time, _real_time_search,
    )
    from fastapi.responses import StreamingResponse
    import json

    llm = call_llm_if_available()
    if not llm:
        raise HTTPException(400, detail="no LLM configured")
    _, model = llm
    mc = model if isinstance(model, ModelConfig) else ModelConfig()
    messages = request.get("messages", [])
    temperature = request.get("temperature", 0.7)
    max_tokens = request.get("max_tokens", 4096)
    if not messages:
        raise HTTPException(400, detail="missing messages")

    # ── Pre-LLM phases (same as chat_pipeline) ──
    system_msg = next((m["content"] for m in messages if m.get("role") == "system"), None)
    user_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    extra_context = ""

    # Reference phrase
    if _is_reference_phrase(user_msg):
        refs = []
        for m in reversed(messages[:-1]):
            if m.get("role") == "assistant":
                refs.append(m["content"])
                if len(refs) >= 2:
                    break
        if refs:
            ref_text = "\n\n".join(refs)
            extra_context = f"[Referring to recent context:\n{ref_text[:500]}]\n"

    # Phase 1: Safety
    safety = run_safety_check(user_msg, user)
    if safety:
        raise HTTPException(403, detail=str(safety.get("reason", "Blocked by safety rules")))

    # Phase 2: Knowledge
    search_query = _get_search_query(user_msg)
    extra_context = enhance_with_knowledge(search_query, extra_context, user)
    if _needs_real_time(search_query):
        rt_context = _real_time_search(search_query)
        if rt_context:
            extra_context += rt_context

    # Phase 3: Memory
    extra_context = inject_memory_context(user_msg, user, extra_context)

    # Phase 4: Security
    extra_context = inject_security_context(extra_context)

    # Phase 5: Experts
    from ..cortex import classify_fine, run_experts as cortex_run_experts, decide as cortex_decide
    _cortex_routing = classify_fine(user_msg)
    _cortex_tone = "normal"
    _cortex_clarify = None
    _cortex_recap = None
    _cortex_drift_warning = None
    if _cortex_routing.get("level") == "expert":
        expert_result = cortex_run_experts(user_msg, user.get("email", "") if user else "",
                                           expert_subset=_cortex_routing.get("experts"))
        decision = cortex_decide(expert_result,
                                 first_msg=next((m["content"] for m in messages if m.get("role") == "user"), ""),
                                 latest_reply=next((m["content"] for m in reversed(messages) if m.get("role") == "assistant"), ""))
        if decision["blocked"]:
            # Return block reason as SSE message instead of HTTP error
            # — HTTP 403 causes frontend to show "API Key invalid" which is misleading
            _block_msg = str(decision.get("opinion", "I'm not able to answer that."))
            async def _blocked_stream():
                yield f"data: {json.dumps({'token': _block_msg})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(_blocked_stream(), media_type="text/event-stream")
        _cortex_tone = decision["adjustments"]["tone"]
        _cortex_clarify = decision["adjustments"]["clarify"]
        _cortex_recap = decision["adjustments"]["recap"]
        _cortex_drift_warning = decision["adjustments"]["drift_warning"]
        if decision["expert_notes"]:
            extra_context += "\n" + decision["expert_notes"].strip()

    # Build prompt
    enhanced_system = build_system_prompt(system_msg)
    if extra_context:
        enhanced_system += f"\n\nContext:\n{extra_context}"
    enhanced_system = build_conversation_history(messages, enhanced_system)
    enhanced_system = inject_system_time(enhanced_system)
    enhanced_system = inject_topic_anchor(messages, enhanced_system)
    # Apply cortex tone adjustment
    if _cortex_tone == "concise":
        enhanced_system += "\n[Instruction] Respond concisely and directly, no unnecessary detail.\n"
    elif _cortex_tone == "warm":
        enhanced_system += "\n[Instruction] Respond warmly and empathetically.\n"
    if _cortex_clarify == "contradiction":
        enhanced_system += (
            "\n[Note] The user's current statement may contradict earlier conversation. "
            "If appropriate, gently ask for clarification before proceeding.\n"
        )
    if _cortex_recap:
        enhanced_system += (
            f"\n[Note] The topic '{_cortex_recap[:50]}' was discussed recently. "
            "If appropriate, ask if the user wants to recap previous conclusions.\n"
        )
    if _cortex_drift_warning:
        enhanced_system += f"\n[Note] {_cortex_drift_warning}\n"

    # ── Self-evaluation + Planning + Curiosity (same as service_chat) ──
    try:
        # Correction injection (from previous turn, per-user)
        from .service_chat import _pop_correction
        _correction_text = _pop_correction(user)
        if _correction_text:
            enhanced_system += f"\n[Correction] {_correction_text}\n"

        # Pre-generation safety guard
        _risky = ["rm ", "drop ", "delete ", "shutdown", "kill ", "chmod "]
        if any(p in user_msg.lower() for p in _risky):
            enhanced_system += (
                "\n[Safety] The user's request involves potentially destructive operations. "
                "If executing the request would cause data loss or damage, "
                "warn the user before proceeding. Explain the risks.\n"
            )

        # Quality trend (per-user)
        from . import service_chat
        _user_scores = service_chat._recent_scores.get(user, default=[])
        _recent = _user_scores[-5:]
        if len(_recent) >= 2:
            _avg = sum(s for _, s in _recent) / len(_recent)
            if _avg < 60:
                enhanced_system += f"\n[Self Review] 近期回复质量评分平均 {_avg:.0f} 分，请注意提升回答质量。\n"
            if len(_recent) >= 3:
                _recent_3 = _recent[-3:]
                if all(_recent_3[i][1] > _recent_3[i+1][1] for i in range(2)):
                    enhanced_system += "\n[Alert] 你的回复质量呈持续下降趋势，请谨慎确认信息后再回复。\n"

        # Learning plans
        from ..planner import LongTermPlanner
        _all_plans = LongTermPlanner().list_plans()
        _active = [p for p in _all_plans if p.get("status") == "active"]
        if _active:
            _lines = [f"  · {p['goal'][:50]}: {sum(1 for m in p.get('milestones',[]) if m.get('status')=='done')}/{len(p.get('milestones',[]))}" for p in _active]
            enhanced_system += "\n[Learning Plans]\n" + "\n".join(_lines) + "\n"

        # Curiosity
        from .service_chat import _get_recently_learned_topics
        _learned = _get_recently_learned_topics(hours=24)
        if _learned:
            enhanced_system += f"\n[Curiosity] You recently learned about: {', '.join(_learned[:3])}. If natural, you may mention what you learned.\n"
    except Exception:
        pass

    # Inject routing-based style template
    _rt = _cortex_routing.get("routing_type", "") if _cortex_routing else ""
    if _rt:
        _style_map = {
            "code": "The user is asking a coding question.\n- Provide working code examples.\n- Explain the logic and trade-offs.",
            "analysis": "The user is asking for analysis or comparison.\n- Use structured reasoning (pros/cons, tables, comparisons).\n- Cite evidence and mention trade-offs.",
            "creative": "The user is asking for creative content.\n- Be imaginative, descriptive, and engaging.\n- Use vivid language and storytelling.",
            "security": "The user is asking about security.\n- Be precise and cautious.\n- Emphasize best practices and potential risks.",
            "planning": "The user is asking about planning or architecture.\n- Provide structured frameworks.\n- Discuss trade-offs and actionable steps.",
        }
        _style = _style_map.get(_rt)
        if _style:
            enhanced_system += f"\n[Style]\n{_style}\n"
    identity_prefix = build_identity_prefix(user)
    identity_prefix = process_memory_commands(user_msg, user, identity_prefix)
    identity_prefix = inject_memory_status(user_msg, user, identity_prefix)
    enhanced_system, identity_prefix = inject_safety_and_metacog(
        enhanced_system, identity_prefix, user)

    full_prompt = identity_prefix + user_msg

    # ── Streaming LLM call — use config from call_llm_if_available ──
    models = [mc]
    stream = call_llm_stream(models, enhanced_system, full_prompt)

    # Collect full response in background task
    _pg_collected = []
    _pg_email = user.get("email", "") if user else ""
    _pg_uid = str(user.get("id") or user.get("user_id", "")) if user else ""
    _pg_msg = user_msg or ""

    # Generate a session ID upfront so user message is saved immediately
    # — this prevents data loss when user switches sessions mid-stream
    import time as _tmod
    _pg_sid = _pg_email.replace("@", "_at_") + ":" + str(int(_tmod.time()))

    # Save user message immediately (not waiting for stream to finish)
    if _pg_email and _pg_msg:
        try:
            from ..storage.db import save_session_to_pg, save_message_to_pg
            save_session_to_pg({"id": _pg_sid, "user_id": _pg_uid,
                                "title": _pg_msg[:100] or "新对话", "messages": []})
            save_message_to_pg(_pg_sid, "user", _pg_msg or "")
        except Exception:
            pass

    def _generate():
        try:
            for chunk in stream:
                if chunk:
                    _pg_collected.append(chunk)
                    yield f"data: {json.dumps({'token': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        # Save assistant response after stream finishes (best-effort)
        if _pg_collected and _pg_email:
            _text = "".join(_pg_collected)
            try:
                from ..storage.db import save_message_to_pg
                save_message_to_pg(_pg_sid, "assistant", _text)
            except Exception:
                pass
    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
