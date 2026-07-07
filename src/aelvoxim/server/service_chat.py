"""metacore.server.service_chat — Chat pipeline and service functions."""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

_log = logging.getLogger("aelvoxim.routes")

# ═══ Thread-safe per-user scoped container ═══
from threading import Lock as _Lock
from typing import TypeVar, Generic

_T = TypeVar("_T")

class _UserScoped(Generic[_T]):
    """Each user (identified by email) gets an isolated copy of a mutable value.
    Thread-safe — uses Lock for concurrent FastAPI requests.
    """
    def __init__(self, factory):
        self._factory = factory
        self._store: dict[str, _T] = {}
        self._lock = _Lock()

    def _uid(self, user: dict) -> str:
        return (user or {}).get("email", "") or ""

    def get(self, user: dict, *, default=None) -> _T:
        uid = self._uid(user)
        if not uid:
            return default  # type: ignore[return-value]
        with self._lock:
            if uid not in self._store:
                self._store[uid] = self._factory()
            return self._store[uid]

    def get_raw(self, uid: str, *, default=None) -> _T:
        """Bypass user dict — use when only uid string is available."""
        if not uid:
            return default  # type: ignore[return-value]
        with self._lock:
            if uid not in self._store:
                self._store[uid] = self._factory()
            return self._store[uid]

    def clear(self, user: dict) -> None:
        uid = self._uid(user)
        with self._lock:
            self._store.pop(uid, None)

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()


def _mask_api_key(text: str) -> str:
    return re.sub(r"sk-[a-zA-Z0-9]{20,}", "sk-***", text) if text else ""


# ═══ Safety check ═══

def run_safety_check(user_msg: str, user: dict) -> Optional[dict]:
    """Local safety check — block common dangerous patterns.
    Falls back silently when SentriKit is unavailable.
    Also checks prompt injection via content_filter (env-toggle: METACORE_CONTENT_FILTER).
    """
    try:
        from ..client.sentrikit import check_user_input as _sk_check
        _sk_result = _sk_check(user_msg)
        if not _sk_result.get("allowed", True):
            return _sk_result
    except Exception:
        pass
    # Content filter prompt injection check
    try:
        if os.environ.get("METACORE_CONTENT_FILTER", "").lower() in ("true", "1", "yes"):
            from ..core.content_filter import filter_input
            verdict = filter_input(user_msg)
            if not verdict.passed:
                return {"allowed": False, "reason": f"Content filter blocked: {verdict.reason}"}
    except Exception:
        pass
    # Local keyword-based safety check (SentriKit unavailable fallback)
    _blocked_keywords = [
        "ignore all previous instructions", "ignore all prior",
        "你是一个", "system prompt:", "你是",
        "写病毒", "写木马", "黑客工具", "sql注入",
        "绕过", "破解", "入侵",
    ]
    q = user_msg.lower()
    for kw in _blocked_keywords:
        if kw in q:
            return {"allowed": False, "reason": f"本地安全规则阻止: 包含敏感词\"{kw}\""}
    return None


# ═══ System prompt ═══

def build_system_prompt(system_msg: Optional[str]) -> str:
    return system_msg or (
        "You are Aelvoxim, an intelligent, friendly AI assistant."
        " You have cross-session memory.\\n\\n"
        "Response style: Use plain text, not markdown.\\n"
        "Always reply in the same language.\\n\\n"
        "REASONING GUIDELINES:\\n"
        "- If the question involves comparison, causality, multi-step logic, "
        "math, code debugging, or contradictory info, reason step by step.\\n"
        "- For straightforward factual questions, answer directly.\\n"
    )


# ═══ User-scoped global state (per-user isolation) ═══
_cache_store = _UserScoped(dict)  # {str: (timestamp, context)}
_cache_ttl = 30
_recent_scores = _UserScoped(list)  # [(timestamp, overall_score), ...]
_MAX_RECENT = 20

# Last correction: per-user dict with lock (pop semantics)
_last_corrections: dict[str, str] = {}
_last_corrections_lock = _Lock()

def _set_correction(user: dict, val: str) -> None:
    uid = (user or {}).get("email", "") or ""
    if not uid:
        return
    with _last_corrections_lock:
        _last_corrections[uid] = val

def _pop_correction(user: dict) -> str:
    uid = (user or {}).get("email", "") or ""
    if not uid:
        return ""
    with _last_corrections_lock:
        return _last_corrections.pop(uid, "")

# Topic frequency tracking for autonomous planning
_topic_freq = _UserScoped(dict)  # {topic_lower: [timestamp, ...]}
_LAST_PLAN_CHECK: float = 0
_PLAN_CHECK_INTERVAL = 30  # seconds



def _get_search_query(user_msg: str, session_topic: str = "") -> str:
    """Get the best search query. For short user messages (<4 chars),
    use the session topic as proxy so context isn't lost on references."""
    msg = user_msg.strip()
    if len(msg) >= 4:
        return msg
    if session_topic and len(session_topic) >= 4:
        return session_topic
    return msg


def _needs_real_time(query: str) -> bool:
    """Detect if the query likely needs real-time web information."""
    if not query:
        return False
    q = query.lower().strip()
    triggers = [
        "今天", "昨天", "明天", "现在", "最新", "最近",
        "today", "yesterday", "now", "latest", "current",
        "price", "价格", "行情", "新闻", "news",
        "weather", "天气", "stock", "股票",
        "查", "查一下", "查询", "搜索", "search",
    ]
    return any(t in q for t in triggers)


def _real_time_search(query: str) -> str:
    """Run real-time web search and format results as context."""
    import re as _re
    import time as _rt
    _t0 = _rt.time()

    _clean = query
    for _ in range(3):
        _prev = _clean
        _clean = _re.sub(
            r'^(请|帮我|帮|查一下|查|查询|搜索|搜一下|找一下|看看|帮忙)\s*',
            '', _clean
        ).strip()
        if _clean == _prev:
            break

    try:
        from ..learn.search import search as _web_search
        results = _web_search(_clean[:100], max_results=5)
        if len(results) < 3 and '有限' not in _clean and len(_clean) >= 4:
            _retry_query = _clean + ' 有限公司'
            _retry = _web_search(_retry_query[:100], max_results=5)
            if _retry:
                _existing_urls = {r.get('url', '') for r in results if r.get('url')}
                for r in _retry:
                    if r.get('url') not in _existing_urls:
                        results.append(r)
                        _existing_urls.add(r.get('url', ''))
    except Exception:
        return ""

    if not results:
        return ""

    safe_lines = []
    for r in results[:3]:
        title = (r.get("title") or "")[:80]
        snippet = (r.get("snippet") or "")[:150]
        if not title.strip() and not snippet.strip():
            continue
        safe_lines.append(f"  - {title}: {snippet}")

    if not safe_lines:
        return ""

    context = "\n[Web Search Results]\n" + "\n".join(safe_lines) + "\n"
    context += "IMPORTANT: Use the search results above to answer accurately.\n"
    return context



def enhance_with_knowledge(user_msg: str, extra_context: str, user: dict, max_per_topic: int = 1) -> str:
    """Search knowledge base and append relevant entries to context.
    Caches results by query hash for 30s.
    Returns modified extra_context (appended knowledge results + probe hints)."""
    import time as _tm
    _cache_key = str(hash(user_msg.strip().lower()[:50]))
    _user_cache = _cache_store.get(user)
    if _cache_key in _user_cache:
        _entry = _user_cache[_cache_key]
        if _tm.time() - _entry[0] < _cache_ttl:
            cached = _entry[1]
            return extra_context + cached if cached else extra_context
    try:
        from ..learn.knowledge import KnowledgeBase
        _sq = user_msg.strip()[:200]
        if not _sq:
            return extra_context
        kb_results = KnowledgeBase.search(query=_sq, min_confidence=0.3, limit=5)
        if kb_results:
            kb_results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
            if max_per_topic > 0:
                seen = {}
                for r in kb_results:
                    t = r.get("topic", "")
                    if t not in seen or r.get("confidence", 0) > seen[t].get("confidence", 0):
                        seen[t] = r
                kb_results = list(seen.values())
            items = []
            for r in kb_results[:5]:
                title = r.get("title", "") or r.get("summary", "")[:60]
                content = (r.get("content") or r.get("summary") or "")[:200]
                if title and content and r.get("confidence", 0) >= 0.3:
                    conf = r.get("confidence", 0)
                    prefix = "  · [低置信度] " if conf < 0.5 else "  · "
                    items.append(f"{prefix}{title}: {content}")
            if items:
                extra_context += "\n[Related Knowledge]\n" + "\n".join(items) + "\n"
            # Track which knowledge entry was referenced (for iteration)
            if kb_results:
                _top = kb_results[0]
                _entry_id = _top.get("title", "") or _top.get("id", "")
                if _entry_id:
                    _set_recent_knowledge_hit(_sq, _entry_id, user)
            
            # Layer 1b: Probe detection based on search results
            _gap = _detect_knowledge_gap(_sq, kb_results)
        else:
            _gap = "ask_teach"
        
        if _gap:
            _t = _sq[:80]
            if _gap == "ask_teach":
                extra_context += (
                    f"\n[Probe: ask_teach]"
                    f" After answering, if natural:"
                    f" '这个话题我了解不多，{_t} —— 能教我一些吗？'\n"
                )
            elif _gap == "ask_deepen":
                extra_context += (
                    f"\n[Probe: ask_deepen]"
                    f" After answering, if natural:"
                    f" '关于 {_t} 还有一些细节，想了解更多吗？'\n"
                )
            # Record curiosity for autonomous learning
            if _gap == "ask_teach" and _t and len(_t) > 3:
                _add_curiosity_topic(_t, user)
    except Exception:
        pass
    # Cache result (per-user)
    _user_cache = _cache_store.get(user)
    _user_cache[_cache_key] = (_tm.time(), extra_context)
    return extra_context


def _detect_knowledge_gap(query: str, results: list) -> str:
    """Detect knowledge gap level based on search results.
    
    Returns:
        "ask_teach" — no useful results, should ask user to teach
        "ask_deepen" — some info but low confidence, could deepen
        "" — adequate coverage, no probe needed
    """
    if not results:
        return "ask_teach"
    # All results have low confidence
    if all(r.get("confidence", 0) < 0.4 for r in results[:3]):
        return "ask_deepen"
    return ""


# ═══ Memory injection ═══

def inject_memory_context(user_msg: str, user: dict, extra_context: str) -> str:
    try:
        from ..api import memory_search
        mem_results = memory_search(user_msg[:50], limit=15) if len(user_msg.strip()) > 3 else []
        if mem_results:
            msg_lower = user_msg.lower()
            def _relevance(m):
                v = str(m.get("value") or m.get("content") or "").lower()
                k = m.get("key", "").lower()
                score = 0.0
                if msg_lower in k or msg_lower in v:
                    score += 5.0
                for w in msg_lower.split():
                    if len(w) > 1 and (w in k or w in v):
                        score += 2.0
                return score
            mem_results.sort(key=_relevance, reverse=True)
            items = [f"  \xb7 {str(m.get('value') or m.get('content') or '')[:150]}"
                     for m in mem_results[:5] if _relevance(m) > 0]
            if items:
                extra_context += "\n[Memory]\n" + "\n".join(items) + "\n"
    except Exception:
        pass
    return extra_context


# ═══ Security context ═══

def inject_security_context(extra_context: str) -> str:
    try:
        from ..client.sentrikit import is_available as _sk_ok
        _sk_connected = _sk_ok()
        sk_line = "Connected" if _sk_connected else "Not connected"
        extra_context += f"\n[Security]\nSentriKit: {sk_line}\n"
    except Exception:
        pass
    return extra_context


# ═══ Expert orchestrator ═══

def run_experts(user_msg: str, user: dict, extra_context: str) -> str:
    try:
        from ..experts.base import ExpertInput as _ei
        from ..experts.orchestrator import ExpertOrchestrator as _orch
        _orch_instance = _orch()
        _inp = _ei(query=user_msg, user_id=user.get("email", "") if user else "")
        q_lower = (user_msg or "").lower()
        is_complex = any(kw in q_lower for kw in
                        ["if", "would", "should", "compare", "why", "what if",
                         "analyze", "evaluate", "difference"])
        result = _orch_instance.think_fast(_inp) if not is_complex else _orch_instance.think(_inp)
        if result.get("blocked"):
            return "BLOCKED: " + str(result.get("opinion", "Blocked"))
        if result.get("expert_results"):
            lines = []
            for er in result["expert_results"]:
                lines.append(f"  [{er.expert_name}] {er.opinion[:200]}")
            if lines:
                extra_context += "\n[Expert Analysis]\n" + "\n".join(lines) + "\n"
    except Exception:
        pass
    return extra_context


# ═══ Conversation history ═══

def build_conversation_history(messages: List[dict], enhanced_system: str) -> str:
    history = ""
    # 只取非 system 消息，保留最近 20 轮（40 条：user + assistant）
    non_system = [m for m in messages[:-1] if m.get("role") != "system"]
    if len(non_system) > 40:
        history += "[早期消息已省略]\n\n"
        non_system = non_system[-40:]
    for m in non_system:
        role = m.get("role", "user")
        content = m.get("content", "")
        prefix = "User" if role == "user" else "Assistant"
        history += f"{prefix}: {content}\n\n"
    if history:
        enhanced_system = (f"{enhanced_system}\n\n[History]\n{history}"
                          f"End of history. Respond to the latest message only.\n")
    return enhanced_system


# ═══ System time ═══

def inject_system_time(enhanced_system: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    return enhanced_system + f"\n[Time]\n{now}\n"


# ═══ Topic anchor ═══

def inject_topic_anchor(messages: List[dict], enhanced_system: str) -> str:
    try:
        # 从最后 3 条 user 消息中取第一条非短引用词的消息作为话题锚点
        user_msgs = []
        for m in reversed(messages):
            if m.get("role") == "user":
                content = (m.get("content") or "").strip()
                if not content:
                    continue
                user_msgs.append(content)
                if len(user_msgs) >= 3:
                    break
        # 从后往前找，取第一个不是短应答词的消息
        filler = {"嗯", "好", "行", "对", "是的", "没错", "继续", "next", "嗯嗯"}
        anchor = ""
        for msg in reversed(user_msgs):
            if msg.lower() not in filler and len(msg) > 3:
                anchor = msg[:80]
                break
        if not anchor and user_msgs:
            anchor = user_msgs[-1][:80]
        if anchor:
            enhanced_system += f"\n[Topic]\n{anchor}\n"
    except Exception:
        pass
    return enhanced_system


# ═══ Identity prefix ═══

def build_identity_prefix(user: dict) -> str:
    if not user:
        return ""
    email = user.get("email", "")
    name = user.get("username", email) if email else "user"
    return f"[Identity: {name}]\n"


# ═══ Memory commands ═══

def process_memory_commands(user_msg: str, user: dict, identity_prefix: str) -> str:
    if "forget" in user_msg.lower():
        try:
            from ..memory import memory_store
            memory_store(f"forget_flag:{user.get('email','')[:20]}", True)
            identity_prefix += "[Memory: forget requested]\n"
        except Exception:
            pass
    return identity_prefix


# ═══ Memory status ═══

def inject_memory_status(user_msg: str, user: dict, identity_prefix: str) -> str:
    try:
        from ..memory import memory_search
        results = memory_search("preference", limit=5)
        if results:
            identity_prefix += "[Memory: preferences exist]\n"
    except Exception:
        pass
    return identity_prefix


# ═══ Safety + metacog ═══

def inject_safety_and_metacog(enhanced_system: str, identity_prefix: str, user: dict) -> tuple:
    try:
        from ..core.metacog_monitor import MetaCogMonitor
        mcm = MetaCogMonitor()
        evaluation = mcm.evaluate()
        if evaluation.get("overload"):
            enhanced_system += "\n[Note: system under load, responses may be brief]\n"
    except Exception:
        pass
    return enhanced_system, identity_prefix


# ═══ Memory storage ═══

def store_conversation_memory(user_msg: str, text: str, user: dict) -> str:
    try:
        from ..memory import store_event
        eid = store_event("conversation", {"user": user_msg[:200], "assistant": str(text)[:200]})
        return eid or ""
    except Exception:
        return ""


def extract_and_store_entities(user_msg: str, text: str, user: dict, event_id: str) -> None:
    try:
        from .entity_extractor import extract
        entities = extract(user_msg + " " + (text or ""))
        if entities:
            for ent in entities:
                try:
                    from ..memory import store_entity
                    store_entity(key=ent.get("name", ent.get("key", "")),
                                etype=ent.get("type", "concept"),
                                attributes={"value": ent.get("value", ""), "source": "chat"})
                except Exception:
                    pass
    except Exception:
        pass


# ═══ Fact check ═══

def verify_response_facts(text: str, user_msg: str, user: dict) -> str:
    """Post-response hallucination guard."""
    try:
        from ..memory.conf_matrix import confidence_label
        label = confidence_label(text[:100])
        if label in ("low", "uncertain"):
            return text + "\n\n[Note: I'm less confident about some details above]"
    except Exception:
        pass
    return text


# ═══ Post-chat tasks ═══

def run_post_chat_tasks(user_msg: str, text: str, user: dict,
                        kb_results: list, t0: float, user_id: str) -> None:
    try:
        # Meta-learning: ingest feedback if user corrected the AI
        if text and "wrong" in text.lower() or "no" in text.lower()[:50]:
            from ..learn.meta_learner import MetaLearner
            MetaLearner().ingest_feedback(user_msg, text, user)
    except Exception:
        pass


# ═══ Reference detection ═══

def _is_reference_phrase(msg: str) -> bool:
    if not msg:
        return False
    m = msg.strip().lower()
    # 短引用词
    short_refs = {"that", "this", "next", "continue", "结果", "然后", "继续",
                  "elaborate", "explain", "tell me more", "go on", "嗯", "好",
                  "行", "对", "是的", "没错", "然后呢", "还有呢"}
    if m in short_refs:
        return True
    if any(m.startswith(r) for r in short_refs):
        return True
    # 消息很短（少于15字）且没有明显的新话题关键词，视为引用
    if len(m) < 15:
        # 如果有疑问词或新话题标记，不算引用
        question_words = {"什么", "怎么", "为什么", "如何", "是否", "有没有",
                          "who", "what", "why", "how", "where", "when", "which"}
        if not any(q in m for q in question_words):
            return True
    return False


# ═══ User ID builder ═══

def _build_user_id(user: dict) -> str:
    if not user:
        return ""
    email = user.get("email") or user.get("username", "")
    return email if email else ""


# ═══ Chat pipeline ═══

def chat_pipeline(
    call_fn, mc, messages: list, user: dict,
    temperature: float = 0.7, max_tokens: int = 2000,
    skip_experts: bool = False,
    skip_memory: bool = False,
    mode: str = "simple",
) -> dict:
    t0 = _time.time()
    _confidence_score = 0.5
    _uid = (user or {}).get("email", "")[:20]
    _chat_log = logging.getLogger("aelvoxim.chat")
    _chat_log.info("Chat pipeline start mode=%s user=%s msg=%.60s",
                   mode, _uid, next((m["content"] for m in messages if m.get("role") == "user"), "")[:60])
    system_msg = next((m["content"] for m in messages if m.get("role") == "system"), None)
    user_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    extra_context = ""

    # Cortex decision defaults (may be overridden in Phase 5)
    _cortex_tone = "normal"
    _cortex_max_tokens = None
    _cortex_clarify = None
    _cortex_recap = None
    _cortex_drift_warning = None

    # Reference phrase
    if _is_reference_phrase(user_msg):
        # 取最近 2 轮 assistant 回复作为引用上下文
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
        _chat_log.info("  Blocked by safety: %.60s", str(safety.get("reason", ""))[:60])
        return {"text": str(safety.get("reason", "Blocked")), "blocked": True}

    # Phase 2: Knowledge
    search_query = _get_search_query(user_msg)
    _chat_log.info("  Phase2=knowledge query=%.50s", search_query[:50])
    extra_context = enhance_with_knowledge(search_query, extra_context, user)
    if _needs_real_time(search_query):
        rt_context = _real_time_search(search_query)
        if rt_context:
            extra_context += rt_context
            _chat_log.info("  +real_time_search %d chars", len(rt_context))

    # Track topic frequency for autonomous planning (per-user)
    if search_query and len(search_query) > 3:
        _key = search_query.lower().strip()[:50]
        _now = __import__("time").time()
        _user_topic_freq = _topic_freq.get(user)
        if _key not in _user_topic_freq:
            _user_topic_freq[_key] = []
        _user_topic_freq[_key].append(_now)
        # Prune old entries (keep last 24h)
        _user_topic_freq[_key] = [t for t in _user_topic_freq[_key] if _now - t < 86400]

    # Auto-create learning plan for high-frequency gaps
    if _now - _LAST_PLAN_CHECK > _PLAN_CHECK_INTERVAL:
        _LAST_PLAN_CHECK = _now
        try:
            _user_topic_freq = _topic_freq.get(user)
            for _t in list(_user_topic_freq.keys()):
                if len(_user_topic_freq[_t]) >= 3 and _now - _user_topic_freq[_t][0] < 3600:
                    # Same topic asked 3+ times in 1 hour → check KB coverage
                    from ..learn.knowledge import KnowledgeBase
                    _existing = KnowledgeBase.search(query=_t, min_confidence=0.3, limit=1)
                    if not _existing:
                        from ..planner import LongTermPlanner
                        _planner = LongTermPlanner()
                        _existing_plans = {p["goal"].lower() for p in _planner.list_plans()}
                        if _t not in _existing_plans:
                            _planner.create_plan(_t, source="auto_detect")
                            import logging
                            logging.getLogger("aelvoxim.chat").info(
                                "📋 Auto-created learning plan: %s (asked %d times)", _t, len(_user_topic_freq[_t]))
                    break  # one plan per check
        except Exception:
            pass

    # Phase 3: Memory
    if not skip_memory:
        extra_context = inject_memory_context(user_msg, user, extra_context)

    # Phase 4: Security
    extra_context = inject_security_context(extra_context)

    # Phase 5: Experts (skip if simple routing)
    _cortex_routing = {}
    if not skip_experts:
        from ..cortex import classify_fine, run_experts as cortex_run_experts, decide as cortex_decide
        _cortex_routing = classify_fine(user_msg)
        if _cortex_routing.get("level") == "expert" or mode == "expert":
            expert_result = cortex_run_experts(user_msg, user.get("email", "") if user else "",
                                               expert_subset=_cortex_routing.get("experts"))
            decision = cortex_decide(expert_result,
                                     first_msg=next((m["content"] for m in messages if m.get("role") == "user"), ""),
                                     latest_reply=next((m["content"] for m in reversed(messages) if m.get("role") == "assistant"), ""))
            if decision["blocked"]:
                return {"text": str(decision.get("opinion", "Blocked")), "blocked": True}
            _cortex_tone = decision["adjustments"]["tone"]
            _cortex_max_tokens = decision["adjustments"]["max_tokens"]
            _cortex_clarify = decision["adjustments"]["clarify"]
            _cortex_recap = decision["adjustments"]["recap"]
            _cortex_drift_warning = decision["adjustments"]["drift_warning"]
            if decision["expert_notes"]:
                extra_context += "\n" + decision["expert_notes"].strip()

    # Extract probe marker from extra_context (set by enhance_with_knowledge)
    _cortex_probe = None
    if "[Probe: ask_teach]" in extra_context:
        _cortex_probe = "ask_teach"
    elif "[Probe: ask_deepen]" in extra_context:
        _cortex_probe = "ask_deepen"

    # Build prompt
    enhanced_system = build_system_prompt(system_msg)

    # Inject learning plan progress — show all active plans, not just topic-matched
    try:
        from ..planner import LongTermPlanner
        _planner = LongTermPlanner()
        _all_plans = _planner.list_plans()
        _active = [p for p in _all_plans if p.get("status") == "active"]
        if _active:
            _lines = []
            for _p in _active:
                _ms = _p.get("milestones", [])
                _done = sum(1 for m in _ms if m.get("status") == "done")
                _total = len(_ms)
                if _total > 0 and _done < _total:
                    _lines.append(f"  · {_p['goal'][:50]}: {_done}/{_total}")
            _done_plans = [p for p in _all_plans if p.get("status") == "done"]
            if _done_plans:
                _lines.append(f"  · 已完成: {len(_done_plans)} 个计划")
            if _lines:
                enhanced_system += "\n[Learning Plans]\n" + "\n".join(_lines) + "\n"
    except Exception:
        pass

    # Inject recently learned topics for proactive sharing
    if user_msg:
        try:
            _learned = _get_recently_learned_topics(hours=24)
            if _learned:
                enhanced_system += (
                    f"\n[Curiosity] You recently learned about: "
                    f"{', '.join(_learned[:3])}. "
                    f"If natural, you may mention what you learned.\n"
                )
        except Exception:
            pass

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

    # Immediate correction injection (from previous conversation turn)
    _correction_text = _pop_correction(user)
    if _correction_text:
        enhanced_system += f"\n[Correction] {_correction_text}\n"
        _chat_log.info("  +correction: %.60s", _correction_text[:60])

    # Pre-generation safety guard
    if user_msg:
        try:
            _risky = ["rm ", "drop ", "delete ", "shutdown", "kill ", "chmod "]
            if any(p in user_msg.lower() for p in _risky):
                enhanced_system += (
                    "\n[Safety] The user's request involves potentially destructive operations. "
                    "If executing the request would cause data loss or damage, "
                    "warn the user before proceeding. Explain the risks.\n"
                )
        except Exception:
            pass

    # Self-evaluation feedback: inject recent quality trend (per-user)
    try:
        _user_scores = _recent_scores.get(user, default=[])
        _recent = _user_scores[-5:]
        if len(_recent) >= 2:
            _avg = sum(s for _, s in _recent) / len(_recent)
            if _avg < 60:
                enhanced_system += f"\n[Self Review] 近期回复质量评分平均 {_avg:.0f} 分，请注意提升回答质量。\n"
            # Check declining trend (last 3)
            if len(_recent) >= 3:
                _recent_3 = _recent[-3:]
                if all(_recent_3[i][1] > _recent_3[i+1][1] for i in range(2)):
                    enhanced_system += "\n[Alert] 你的回复质量呈持续下降趋势，请谨慎确认信息后再回复。\n"
    except Exception:
        pass

    identity_prefix = build_identity_prefix(user)
    identity_prefix = process_memory_commands(user_msg, user, identity_prefix)
    identity_prefix = inject_memory_status(user_msg, user, identity_prefix)
    enhanced_system, identity_prefix = inject_safety_and_metacog(enhanced_system, identity_prefix, user)

    full_prompt = identity_prefix + user_msg

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

    # LLM call
    _max_tokens_actual = _cortex_max_tokens or max_tokens
    _t_before_llm = _time.time()
    from ..learn.llm import call_llm
    text = call_llm(mc, enhanced_system, full_prompt, temperature, _max_tokens_actual)
    _chat_log.info("  LLM call done in %.1fs text_len=%d", _time.time() - _t_before_llm, len(text or ""))

    # Tool execution: scan LLM output for [TOOL:action] markers and execute
    if text:
        try:
            from .tool_use import execute_tool_calls, has_tool_calls, available_tools
            if has_tool_calls(text):
                _tool_t0 = _time.time()
                _chat_log.info("  ⚡ Tool calls detected, executing... tools=%s", available_tools())
                text = execute_tool_calls(text)
                _chat_log.info("  ⚡ Tool execution done in %.1fs", _time.time() - _tool_t0)
        except Exception as _exc:
            _chat_log.warning("  Tool execution error: %s", _exc)

    # Fact check
    if text:
        try:
            text = verify_response_facts(text, user_msg, user)
        except Exception:
            pass

    # Post-generation quality check + auto-correction
    if text:
        try:
            from ..control.metacog_check import evaluate as _mc_eval
            _sev, _issues = _mc_eval(chunk=text, accumulated="", topic=user_msg)
            if _sev == "SEVERE" and _issues:
                _fname = ", ".join(i.get("type", "?") for i in _issues[:3])
                # Auto-correct: append fix instruction and call LLM again
                _fix_prompt = (
                    f"The following response has issues ({_fname}). "
                    f"Please correct it:\n\n{text}"
                )
                _corrected = call_llm(mc, enhanced_system, _fix_prompt, temperature, _max_tokens_actual)
                if _corrected and len(_corrected) > 10:
                    text = _corrected
        except Exception:
            pass

    # Memory
    event_id = store_conversation_memory(user_msg, text, user)
    extract_and_store_entities(user_msg, text, user, event_id)

    # Post-chat
    run_post_chat_tasks(user_msg, text, user, [], t0,
                        user.get("user_id", "") if user else "")

    # Record confidence for health monitoring
    try:
        record_confidence(_confidence_score)
    except Exception:
        pass

    # Post-chat quality evaluation
    if text:
        try:
            from .chat_monitor import evaluate_conversation
            evaluate_conversation(
                query=user_msg,
                answer=text,
                user_id=user.get("email", "") if user else "",
                knowledge_results=[],
                response_time_ms=(time.time() - t0) * 1000,
            )
        except Exception:
            pass

        # Self-review (content-level quality assessment)
        try:
            from ..core.self_review import hook_review
            _review_result = hook_review(
                conversation_id=f"sess_{int(time.time())}",
                user_question=user_msg,
                assistant_response=text,
            )
            _score = _review_result.get("overall_score", 0) if isinstance(_review_result, dict) else 0
            if _score:
                import time as _rt
                _user_scores = _recent_scores.get(user)
                _user_scores.append((_rt.time(), _score))
                while len(_user_scores) > _MAX_RECENT:
                    _user_scores.pop(0)
        except Exception:
            pass

        # Layer 1: Continuous learning — extract, associate, iterate
        if text:
            try:
                from .chat_monitor import extract_feedback_signals
                signals = extract_feedback_signals(user_msg, text)

                # ── 1a. Fact extraction from AI response ──
                _facts = _extract_facts_from_reply(text, user_msg)
                for _fact_title, _fact_content in _facts[:3]:
                    from ..learn.knowledge import KnowledgeBase as _kb
                    _kb.store_pending(
                        topic=signals.get("raw_topic", "conversation")[:80],
                        title=_fact_title[:80],
                        content=_fact_content[:500],
                        source="chat_fact",
                    )
                    # Record in belief pool
                    _record_belief(_fact_title, success=True)

                # ── 1b. Correction storage (existing) ──
                if signals.get("correction"):
                    from ..learn.knowledge import KnowledgeBase as _kb
                    _kb.store_pending(
                        topic=signals.get("raw_topic", "conversation")[:80],
                        title=f"对话纠正: {signals.get('raw_topic', 'conversation')[:40]}",
                        content=f"用户纠正: 原={signals['correction']['old_term']} 新={signals['correction']['new_term']}",
                        source="chat_correction",
                    )
                    _record_belief(f"纠正:{signals['correction']['old_term']}", success=False)
                    _record_belief(f"纠正:{signals['correction']['new_term']}", success=True)
                    # Save for immediate correction injection in follow-up
                    _set_correction(user, f"用户纠正: 原={signals['correction']['old_term']} 新={signals['correction']['new_term']}")

                # ── 1c. Repeat question → belief decay ──
                if signals.get("repeat_question"):
                    from ..core.belief import get_pool as _bp
                    _pool = _bp()
                    _pool.decay_belief(f"topic:{signals.get('raw_topic', 'unknown')}")

                # ── 1d. Iterate: update confidence for recently-referenced knowledge ──
                _recent_hit = _get_recent_knowledge_hit(user_msg, user)
                if _recent_hit:
                    _is_correction = bool(signals.get("correction"))
                    _record_belief(_recent_hit, success=not _is_correction)
            except Exception:
                pass

        # Self-reflection (async, doesn't block return)
        _text_for_reflect = text
        if _text_for_reflect and mc:
            try:
                import threading as _t
                _t.Thread(target=_self_reflect, args=(user_msg, _text_for_reflect, mc, user), daemon=True).start()
            except Exception:
                pass

    return {"text": text, "blocked": False}


# ── Continuous learning helpers ──

_recent_knowledge_hits: dict = {}  # {query_lower: entry_id, ...}


def _extract_facts_from_reply(reply: str, query: str) -> list:
    """Extract factual statements from AI reply using simple rules.

    Returns list of (title, content) tuples.
    """
    import re as _re
    facts = []
    sentences = _re.split(r'(?<=[。！？.!?])\s*', reply)
    for s in sentences[:10]:
        s = s.strip()
        if len(s) < 15 or len(s) > 300:
            continue
        # "X 是 Y" / "X 支持 Y" / "X Use Y" patterns
        m = _re.search(r'^(.{5,50})(是|支持|使用|采用|提供|基于)(.{5,})', s)
        if m:
            title = m.group(1).strip()[:80]
            content = s[:300]
            if title and content:
                facts.append((title, content))
    return facts


def _record_belief(key: str, success: bool) -> None:
    """Record a belief outcome in the BeliefPool."""
    try:
        from ..core.belief import get_pool as _bp
        _pool = _bp()
        _pool.record_outcome(key, success)
    except Exception:
        pass


# ── Continuous learning helpers ──

_recent_knowledge_hits = _UserScoped(dict)  # {query_lower: entry_id, ...}


def _get_recent_knowledge_hit(query: str, user: dict = None) -> str:
    """Find the most recently referenced knowledge entry for a query."""
    _key = query.strip().lower()[:80]
    _user_hits = _recent_knowledge_hits.get(user, default={})
    return _user_hits.get(_key, "")


def _set_recent_knowledge_hit(query: str, entry_id: str, user: dict = None) -> None:
    """Record that a knowledge entry was referenced in this conversation."""
    _key = query.strip().lower()[:80]
    _user_hits = _recent_knowledge_hits.get(user)
    _user_hits[_key] = entry_id
    # Keep dict bounded
    if len(_user_hits) > 100:
        _user_hits.clear()


# ── Confidence window for real-time monitoring ──
_CONFIDENCE_WINDOW: list = []  # list of (timestamp, score)

def record_confidence(score: float) -> None:
    """Record a confidence score for real-time trend tracking in /v1/health."""
    import time as _tm
    _CONFIDENCE_WINDOW.append((_tm.time(), score))
    # Keep last 200 entries
    if len(_CONFIDENCE_WINDOW) > 200:
        _CONFIDENCE_WINDOW.pop(0)


def get_confidence_trend() -> dict:
    """Return latest confidence trend for health endpoint."""
    import time as _tm
    if not _CONFIDENCE_WINDOW:
        return {"current": 0.5, "avg_50": 0.5, "trend": "stable", "samples": 0}
    now = _tm.time()
    recent = [s for t, s in _CONFIDENCE_WINDOW if now - t < 300]
    if not recent:
        return {"current": _CONFIDENCE_WINDOW[-1][1], "avg_50": 0.5, "trend": "stable", "samples": 0}
    avg = sum(recent) / len(recent)
    trend = "up" if len(recent) > 5 and recent[-1] > recent[0] * 1.1 else \
            "down" if len(recent) > 5 and recent[-1] < recent[0] * 0.9 else "stable"
    return {"current": round(recent[-1], 3), "avg_50": round(avg, 3),
            "trend": trend, "samples": len(recent)}


# ── Curiosity list for autonomous learning ──

def _add_curiosity_topic(topic: str) -> None:
    """Add a topic to the curiosity list (persisted to JSON)."""
    from pathlib import Path as _P
    fp = _P.home() / ".aelvoxim" / "curiosity.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        topics = json.loads(fp.read_text()) if fp.exists() else []
        existing = {t["topic"] for t in topics}
        if topic[:80] not in existing:
            topics.append({
                "topic": topic[:80],
                "asked_at": __import__("time").time(),
                "learned": False,
            })
            fp.write_text(json.dumps(topics, ensure_ascii=False, indent=2, default=str))
    except Exception:
        pass


def _pop_curiosity_topic() -> str:
    """Pop the oldest unlearned curiosity topic for autonomous learning."""
    from pathlib import Path as _P
    fp = _P.home() / ".aelvoxim" / "curiosity.json"
    if not fp.exists():
        return ""
    try:
        topics = json.loads(fp.read_text())
        unlearned = [t for t in topics if not t.get("learned")]
        if not unlearned:
            return ""
        oldest = unlearned[0]
        oldest["learned"] = True
        oldest["learned_at"] = __import__("time").time()
        fp.write_text(json.dumps(topics, ensure_ascii=False, indent=2, default=str))
        return oldest["topic"]
    except Exception:
        return ""


def _get_recently_learned_topics(hours: int = 24) -> list:
    """Get topics learned within the last N hours (for proactive sharing)."""
    from pathlib import Path as _P
    fp = _P.home() / ".aelvoxim" / "curiosity.json"
    if not fp.exists():
        return []
    try:
        topics = json.loads(fp.read_text())
        now = __import__("time").time()
        return [
            t["topic"] for t in topics
            if t.get("learned") and now - t.get("learned_at", 0) < hours * 3600
        ]
    except Exception:
        return []


# ── Self-reflection: LLM self-evaluates its own reply ──

def _self_reflect(query: str, reply: str, mc=None, user: dict = None) -> None:
    """Let the LLM evaluate its own reply quality. Runs async, doesn't block return."""
    from ..learn.llm import call_llm
    if not reply or not query:
        return
    try:
        _score_text = call_llm(
            mc,
            "You are a strict quality evaluator. Rate the response (0-100):",
            f"User: {query[:300]}\nAI: {reply[:600]}\n\nRate the AI's response quality (0-100). Return only the number:",
            temperature=0.3,
            max_tokens=10,
        )
        _score = int(''.join(c for c in _score_text if c.isdigit()) or "50")
        _score = max(0, min(100, _score))
        # Inject into quality trend (per-user)
        import time as _rt
        _user_scores = _recent_scores.get(user)
        _user_scores.append((_rt.time(), _score))
        while len(_user_scores) > _MAX_RECENT:
            _user_scores.pop(0)
        # If low score, log for curiosity learning
        if _score < 40 and query:
            _add_curiosity_topic(query[:80], user)
    except Exception:
        pass
