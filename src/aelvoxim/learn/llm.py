"""aelvoxim.learn.llm — LLM adapter layer

Supports multiple models: DeepSeek / OpenAI / Ollama / Anthropic
Pure stdlib (urllib), zero external dependencies.
Auto failover: primary model failure -> degrade to fallback model.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError


# ── Model config ────────────────────────────────────


@dataclass
class ModelConfig:
    """Configuration for a single model."""
    name: str                # Model name (deepseek-chat, gpt-4o, qwen2.5:7b, etc.)
    provider: str            # deepseek / openai / ollama / anthropic
    api_key: str = ""        # API key (not needed for Ollama)
    base_url: str = ""       # API base URL
    timeout: int = 30        # Timeout in seconds
    temperature: float = 0.7
    max_tokens: int = 4096
    priority: int = 1        # Priority, lower = higher priority

    def is_available(self) -> bool:
        if self.provider == "ollama":
            return True  # Ollama does not need an API key
        return bool(self.api_key) and len(self.api_key) > 8


# ── Preset model config ───────────────────────────

_ollama_cache: Optional[str] = None
_ollama_cache_time: float = 0
_OLLAMA_CACHE_TTL = 60  # Cache 60 seconds


def auto_detect_ollama() -> Optional[str]:
    """Auto-detect local Ollama service address (result cached for 60 seconds)."""
    global _ollama_cache, _ollama_cache_time
    now = time.time()
    if _ollama_cache is not None and (now - _ollama_cache_time) < _OLLAMA_CACHE_TTL:
        return _ollama_cache
    candidates = [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://172.19.240.1:11434",
        "http://host.docker.internal:11434",
    ]

    # Auto-detect Windows host IP (default gateway)
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        # Gateway IP = last octet to 1
        parts = local_ip.rsplit(".", 1)
        if len(parts) == 2:
            gw = f"{parts[0]}.1"
            candidates.insert(0, f"http://{gw}:11434")
    except Exception:
        pass  # non-critical: gateway detection failure, continue with defaults
    for url in candidates:
        try:
            req = Request(f"{url}/api/tags", method="GET")
            resp = urlopen(req, timeout=2)
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                models = data.get("models", [])
                if models:
                    _ollama_cache = url
                    _ollama_cache_time = time.time()
                    return url
        except Exception:
            continue
    _ollama_cache = None
    _ollama_cache_time = time.time()
    return None


def default_models() -> List[ModelConfig]:
    """Get default model config list (sorted by priority)."""
    models = []

    # 1. DeepSeek (highest priority)
    deepseek_key = (os.environ.get("DEEPSEEK_API_KEY") or
                    os.environ.get("LLM_API_KEY") or "")
    if deepseek_key:
        models.append(ModelConfig(
            name="deepseek-chat",
            provider="deepseek",
            api_key=deepseek_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            priority=1,
        ))

    # 2. Qwen — supports qwen-max / qwen-plus / qwen-turbo
    qwen_key = os.environ.get("QWEN_API_KEY", "")
    if qwen_key:
        qwen_model = os.environ.get("QWEN_MODEL", "qwen-max")
        models.append(ModelConfig(
            name=qwen_model,
            provider="openai",  # Qwen API is OpenAI-compatible
            api_key=qwen_key,
            base_url=os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            priority=2,
        ))

    # 3. OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        models.append(ModelConfig(
            name="gpt-4o",
            provider="openai",
            api_key=openai_key,
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            priority=3,
        ))

    # 4. Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        models.append(ModelConfig(
            name="claude-sonnet-4",
            provider="anthropic",
            api_key=anthropic_key,
            base_url="https://api.anthropic.com/v1",
            priority=4,
        ))

    # 6. Saved config file fallback
    try:
        from ..utils import read_json, LLM_CONFIG_FILE
        saved = read_json(LLM_CONFIG_FILE) or {}
        saved_key = saved.get("api_key", "")
        saved_provider = saved.get("provider", "deepseek")
        if saved_key and not any(m.api_key == saved_key for m in models):
            models.append(ModelConfig(
                name=saved.get("model_name", "deepseek-chat"),
                provider=saved_provider,
                api_key=saved_key,
                base_url=saved.get("base_url", "https://api.deepseek.com/v1"),
                priority=1 if saved_provider == "deepseek" else 5,
            ))
    except Exception:
        pass

    # 5. Ollama local
    ollama_url = auto_detect_ollama()
    if ollama_url:
        models.append(ModelConfig(
            name="qwen2.5:3b",
            provider="ollama",
            base_url=ollama_url,
            priority=5,
        ))

    return models


# ── Fallback wrapper ──


def call_llm_with_fallback(
    models: List[ModelConfig],
    system_prompt: str,
    user_message: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """Call LLM with automatic fallback across all available models.

    Tries models in priority order. If one fails, logs and tries the next.
    Only raises LLMError if ALL models fail.

    Returns:
        Model reply text.

    Raises:
        LLMError: All models failed.
    """
    last_error = ""
    for i, model in enumerate(models):
        try:
            text = call_llm(
                model, system_prompt, user_message,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if i > 0:
                pass  # fallback succeeded silently
            return text
        except Exception as e:
            last_error = str(e)
            continue
    raise LLMError(f"All models failed. Last error: {_mask_api_key(last_error)}")


def _mask_api_key(text: str) -> str:
    """Mask API keys in error messages."""
    import re
    return re.sub(r'(sk-[a-zA-Z0-9]{8})[a-zA-Z0-9]+', r'\1...', text)


# ── LLM caller ──────────────────────────────────


class LLMError(Exception):
    pass


def call_llm(
    model: ModelConfig,
    system_prompt: str,
    user_message: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """Call the specified LLM model.

    Args:
        model: Model configuration
        system_prompt: System prompt
        user_message: User message
        temperature: Temperature (overrides config)
        max_tokens: Max tokens (overrides config)

    Returns:
        Model reply text

    Raises:
        LLMError: Raised on call failure
    """
    temp = temperature if temperature is not None else model.temperature
    mt = max_tokens if max_tokens is not None else model.max_tokens
    to = timeout if timeout is not None else model.timeout

    try:
        if model.provider == "ollama":
            text = _call_ollama(model, user_message, temp, mt)
        elif model.provider == "deepseek":
            text = _call_openai_compat(model, system_prompt, user_message, temp, mt, to)
        elif model.provider == "openai":
            text = _call_openai_compat(model, system_prompt, user_message, temp, mt, to)
        elif model.provider == "anthropic":
            text = _call_anthropic(model, system_prompt, user_message, temp, mt)
        else:
            raise LLMError(f"Unsupported provider: {model.provider}")

        # SafetyGuard R6 content security (always active)
        try:
            from ..core.safety import r6_intercept_output
            text = r6_intercept_output(text)
        except Exception:
            pass  # non-critical: safety module unavailable, continue without intercept

        # Content safety filter (only checked when enabled via env var AELVOXIM_CONTENT_FILTER=true)
        if os.environ.get("AELVOXIM_CONTENT_FILTER", "").lower() in ("true", "1", "yes"):
            from ..core.content_filter import filter_output, sanitize_output
            verdict = filter_output(text, check_pii=True)
            if not verdict.passed:
                raise LLMError(f"Output blocked by content safety filter: {verdict.reason}")
            text = sanitize_output(text)

        return text
    except Exception as e:
        raise LLMError(f"[{model.provider}/{model.name}] {e}")


def call_with_fallback(
    models: List[ModelConfig],
    system_prompt: str,
    user_message: str,
) -> str:
    """Auto failover: try each model by priority until success.

    Args:
        models: List of model configs (sorted by priority)
        system_prompt: System prompt
        user_message: User message

    Returns:
        Reply from the first successful model

    Raises:
        LLMError: All models failed
    """
    errors = []
    for model in models:
        if not model.is_available():
            errors.append(f"{model.name}: not configured")
            continue
        try:
            return call_llm(model, system_prompt, user_message)
        except LLMError as e:
            errors.append(str(e))
            continue

    raise LLMError(f"All models failed: {'; '.join(errors)}")


def call_llm_stream(
    models: List[ModelConfig],
    system_prompt: str,
    user_message: str,
) -> str:
    """Streaming LLM call, yields text chunk by chunk.

    Currently only supports OpenAI-compatible APIs (DeepSeek/OpenAI).
    Ollama/Anthropic degrade to non-streaming.
    """
    errors = []
    for model in models:
        if not model.is_available():
            errors.append(f"{model.name}: not configured")
            continue
        try:
            if model.provider in ("deepseek", "openai"):
                yield from _call_openai_compat_stream(model, system_prompt, user_message)
            else:
                # Non-streaming provider — degrading
                yield call_llm([model], system_prompt, user_message)
            return
        except LLMError as e:
            errors.append(str(e))
            continue

    raise LLMError(f"All models failed: {'; '.join(errors)}")


def _call_openai_compat_stream(
    model: ModelConfig,
    system_prompt: str,
    user_message: str,
) -> str:
    """Streaming call for OpenAI-compatible API (SSE), yields chunk by chunk."""
    import http.client
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    payload = json.dumps({
        "model": model.name,
        "messages": messages,
        "temperature": model.temperature,
        "max_tokens": model.max_tokens,
        "stream": True,
    })

    import urllib.parse
    parsed = urllib.parse.urlparse(model.base_url)
    host = parsed.hostname
    port = parsed.port
    path = parsed.path.rstrip("/") + "/chat/completions" if parsed.path else "/chat/completions"
    use_ssl = parsed.scheme == "https"

    if use_ssl:
        conn = http.client.HTTPSConnection(host, port, timeout=model.timeout)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=model.timeout)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {model.api_key}",
    }

    try:
        conn.request("POST", path, body=payload, headers=headers)
        resp = conn.getresponse()
        
        while True:
            line = resp.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            
            # SSE format: data: {"choices":[{"delta":{"content":"..."}}]}
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    pass
    finally:
        conn.close()


def call_with_fallback_or_fallback(
    models: List[ModelConfig],
    system_prompt: str,
    user_message: str,
) -> str:
    """Failover with keyword fallback.

    Prioritizes LLM call; if all fail, falls back to keyword fallback engine.
    Never raises.
    """
    try:
        return call_with_fallback(models, system_prompt, user_message)
    except LLMError:
        return FALLBACK_ENGINE.reply(user_message, system_prompt)


# ── Fallback engine ────────────────────────────────


class FallbackEngine:
    """Keyword fallback engine when LLM is unavailable.

    Provides keyword matching, template replies, and other fallback capabilities.
    No external dependencies, does not affect any functionality.
    """

    def reply(self, user_message: str, system_prompt: str = "") -> str:
        """Keyword-matched fallback reply."""
        msg_lower = user_message.lower()

        # 1. Greetings
        greetings = ["你好", "hello", "hi", "在吗", "hey"]
        if any(g in msg_lower for g in greetings):
            return "Hello! Please configure an LLM API Key first. Enter your DeepSeek/OpenAI/Anthropic key in settings."

        # 2. Identity query
        who_am_i = ["你是谁", "你是什么", "who are you", "what are you"]
        if any(q in msg_lower for q in who_am_i):
            return (
                "I am Aelvoxim Agent — the self-evolving AI brain. "
                "I have a complete evolution loop: metacognitive detection → self-scoring → evolution engine → memory consolidation. "
                "Currently running locally, LLM service not configured, using keyword fallback mode."
            )

        # 3. Status inquiry
        status_q = ["状态", "status", "怎么样", "how are you"]
        if any(q in msg_lower for q in status_q):
            return "All good. LLM service not configured, running in local degraded mode."

        # 4. Help
        help_q = ["帮助", "help", "能做什么", "功能"]
        if any(q in msg_lower for q in help_q):
            return (
                "I can do:\n"
                "• Chat (with cross-session memory)\n"
                "• Self-evolution (MetaCog → Judge → Evolution → SelfModel)\n"
                "• Data flywheel (trend analysis + pattern discovery)\n"
                "• Memory management (4-layer memory architecture)\n"
                "Configure a DeepSeek/OpenAI Key in settings to enable full AI capabilities."
            )

        # 5. Thanks
        thanks = ["谢谢", "感谢", "thank", "thanks", "多谢"]
        if any(t in msg_lower for t in thanks):
            return "You're welcome! Feel free to ask anytime."

        # 6. Default reply
        return (
            f"LLM is not configured, running in fallback mode.\n"
            f"Please configure a DeepSeek/OpenAI/Anthropic API Key in settings.\n\n"
            f"You sent: {user_message[:100]}"
        )


FALLBACK_ENGINE = FallbackEngine()


# ── Provider implementations ────────────────────────


def _call_ollama(
    model: ModelConfig,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call Ollama local model."""
    payload = json.dumps({
        "model": model.name,
        "prompt": user_message,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }).encode("utf-8")

    req = Request(
        f"{model.base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urlopen(req, timeout=model.timeout)
    result = json.loads(resp.read().decode("utf-8"))

    if "error" in result:
        raise LLMError(result["error"])

    return result.get("response", "")


def _call_openai_compat(
    model: ModelConfig,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    timeout: int = 30,
) -> str:
    """Call OpenAI-compatible API (DeepSeek / OpenAI / other)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    payload = json.dumps({
        "model": model.name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = Request(
        f"{model.base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {model.api_key}",
        },
        method="POST",
    )
    resp = urlopen(req, timeout=timeout)
    result = json.loads(resp.read().decode("utf-8"))

    choices = result.get("choices", [])
    if not choices:
        raise LLMError("API returned empty choices")

    return choices[0].get("message", {}).get("content", "")


def _call_anthropic(
    model: ModelConfig,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call Anthropic Claude API."""
    payload = json.dumps({
        "model": model.name,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = Request(
        f"{model.base_url}/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": model.api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    resp = urlopen(req, timeout=model.timeout)
    result = json.loads(resp.read().decode("utf-8"))

    content = result.get("content", [])
    if not content:
        raise LLMError("API returned empty content")

    return "".join(block.get("text", "") for block in content if block.get("type") == "text")


# ── Convenience functions ───────────────────────────


def get_available_models() -> List[Dict]:
    """Get currently available model list (for display)."""
    models = default_models()
    return [
        {
            "name": m.name,
            "provider": m.provider,
            "available": m.is_available(),
            "priority": m.priority,
            "base_url": m.base_url,
        }
        for m in models
    ]


def test_model(model: ModelConfig) -> Dict:
    """Test whether a model is available."""
    try:
        resp = call_llm(model, "You are an assistant", "Reply OK", max_tokens=10)
        return {"success": True, "response": resp[:50]}
    except Exception as e:
        return {"success": False, "error": _mask_api_key(str(e))}


# ── SmartOrchestrator ──────────────────────────
#
# Three-stage pipeline:
#   0. Front-end enhancement — search KnowledgeBase, high confidence → direct return
#   1. Decomposition stage — use DeepSeek to decompose into sub-questions + reasoning paths (CoT)
#   2. Execution stage — call LLM independently for each sub-question (with reasoning path injection)
#   3. Synthesis stage — merge sub-answers into coherent reply, incorporating context and user preferences
#


def _has_deepseek(models: List[ModelConfig]) -> Optional[ModelConfig]:
    """Check if any DeepSeek model is available in the list."""
    for m in models:
        if m.provider == "deepseek" and m.is_available():
            return m
    return None


def _detect_need_decomposition(user_message: str) -> bool:
    """Detect whether decomposition is needed.

    Returns True when the message involves complex logic, math, algorithms, or analysis.
    """
    msg = user_message.lower()
    triggers = [
        "分析", "比较", "对比", "推理", "论证",
        "数学", "公式", "计算", "方程", "算法", "复杂度",
        "逻辑", "推导", "证明", "因果", "归因",
        "策略", "方案", "建议", "评估", "权衡",
        "拆解", "分解", "步骤", "流程",
        "code", "代码", "debug", "Debug",
        "优化", "性能", "设计模式",
    ]
    return any(t in msg for t in triggers)


# ── Front-end knowledge enhancement layer ───────────


def _query_knowledge(
    user_message: str,
    min_confidence: float = 0.7,
) -> Optional[Dict]:
    """Search KnowledgeBase, return the highest-matching knowledge entry (if confidence threshold is met).

    Args:
        user_message: User message
        min_confidence: Minimum confidence threshold (0-1)

    Returns:
        {title, summary, content, confidence, topic} or None
    """
    try:
        from aelvoxim.learn.knowledge import KnowledgeBase
        results = KnowledgeBase.search(
            query=user_message,
            min_confidence=min_confidence,
            limit=3,
        )
        if results:
            best = results[0]
            return {
                "title": best.get("title", ""),
                "summary": best.get("summary", ""),
                "content": best.get("content", ""),
                "confidence": best.get("confidence", 0),
                "topic": best.get("topic", ""),
            }
    except Exception:
        pass  # non-critical: knowledge search failure, fall through to None return
    return None


def _format_knowledge_as_context(knowledge: Dict) -> str:
    """Format a knowledge entry as injectable context text."""
    parts = ["[Knowledge Base Reference]"]
    parts.append(f"Topic: {knowledge.get('topic', '')}")
    parts.append(f"Title: {knowledge.get('title', '')}")
    summary = knowledge.get("summary", "")
    if summary:
        parts.append(f"Summary: {summary[:200]}")
    content = knowledge.get("content", "")
    if content:
        parts.append(f"Detail: {content[:800]}")
    parts.append(f"Confidence: {knowledge.get('confidence', 0):.0%}")
    return "\n".join(parts)


# ── Lightweight CoT decomposition ──────────────────


@dataclass
class DecompositionResult:
    """Decomposition result: sub-question list + corresponding reasoning paths."""
    sub_questions: List[str]
    reasoning_paths: List[str]  # Reasoning path for each sub-question


def _decompose_with_cot(
    ds: ModelConfig,
    system_prompt: str,
    user_message: str,
    context: Optional[str] = None,
    user_preferences: Optional[str] = None,
    knowledge_context: Optional[str] = None,
) -> DecompositionResult:
    """Decompose the question using DeepSeek, producing sub-questions + reasoning chain paths.

    Returns DecompositionResult, containing the sub-question list and reasoning path for each sub-question.
    """
    lines = [f"User question: {user_message}"]
    if context:
        lines.append(f"\nConversation context:\n{context[:1500]}")
    if user_preferences:
        lines.append(f"\nUser preferences:\n{user_preferences[:500]}")
    if knowledge_context:
        lines.append(f"\nKnowledge base reference:\n{knowledge_context[:1000]}")

    sys_prompt = (
        "You are a question decomposition and analysis path planning expert. Your responsibilities:\n"
        "1. Analyze the user's question, break it down into 3-5 independent sub-questions covering different dimensions\n"
        "2. For each sub-question, provide an analysis approach (reasoning path) — what angle to start from, what to compare, what to derive\n\n"
        "Return a JSON object only, structured as follows:\n"
        "{\n"
        '  "sub_questions": ["Sub-question 1", "Sub-question 2", ...],\n'
        '  "reasoning_paths": ["Analysis approach for sub-question 1", "Analysis approach for sub-question 2", ...]\n'
        "}\n\n"
        "Requirements:\n"
        "- Each sub-question should be independently answerable\n"
        "- Reasoning paths should be concise (30-80 chars), guiding the model on which angle to answer from\n"
        "- If the question is simple and does not need decomposition, Return {\"sub_questions\": [\"Original question\"], \"reasoning_paths\": [\"Direct answer\"]}\n"
        "- Do not output anything else"
    )

    try:
        text = call_llm(ds, sys_prompt, "\n".join(lines),
                       temperature=0.3, max_tokens=1536)
        import json
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            sq = result.get("sub_questions", [])
            rp = result.get("reasoning_paths", [])
            if isinstance(sq, list) and len(sq) > 0:
                sq = [str(q).strip() for q in sq if str(q).strip()]
                if isinstance(rp, list):
                    rp = [str(p).strip() for p in rp if str(p).strip()]
                # Pad: if reasoning paths are fewer than sub-questions, fill in
                while len(rp) < len(sq):
                    rp.append("Comprehensive analysis")
                return DecompositionResult(
                    sub_questions=sq[:5],
                    reasoning_paths=rp[:5],
                )
    except Exception:
        pass  # non-critical: decomposition LLM call failed, fall back to single-question default
    return DecompositionResult(
        sub_questions=[user_message],
        reasoning_paths=["Direct answer"],
    )


# ── Entry function ──────────────────────────


def orchestrate(
    models: List[ModelConfig],
    system_prompt: str,
    user_message: str,
    context: Optional[str] = None,
    user_preferences: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Smart invocation orchestration entry point.

    Four-stage pipeline:
    0. Front-end knowledge enhancement — search KnowledgeBase, high confidence → direct return
    1. Decomposition + CoT — use DeepSeek to decompose into sub-questions + analysis paths
    2. Execution — inject reasoning paths into each sub-question and call LLM independently
    3. Synthesis — merge into coherent reply, injecting context and preferences

    Args:
        models: Available model list (sorted by priority)
        system_prompt: System prompt
        user_message: User message
        context: Conversation context (history)
        user_preferences: User preference description
        temperature: Temperature
        max_tokens: Max tokens

    Returns:
        Final reply text
    """
    ds = _has_deepseek(models)

    # ── Stage 0: Front-end knowledge enhancement ───────
    knowledge = _query_knowledge(user_message)

    # If knowledge entry confidence >= 0.85, use knowledge + one LLM call (skip decomposition)
    if knowledge and knowledge.get("confidence", 0) >= 0.85:
        knowledge_text = _format_knowledge_as_context(knowledge)
        enhanced_system = (
            f"{system_prompt}\n\n"
            f"The following is relevant information retrieved from the knowledge base. Please answer the user's question based on this:\n"
            f"{knowledge_text}"
        )
        try:
            if ds:
                return call_llm(ds, enhanced_system, user_message,
                               temperature=temperature, max_tokens=max_tokens)
            return call_with_fallback(models, enhanced_system, user_message)
        except LLMError:
            # Knowledge enhancement failed, degrade to non-enhanced flow
            pass
        except Exception:
            pass  # non-critical: knowledge-enhanced call failed, continue with normal flow

    # Low-confidence knowledge (0.7-0.85) or no match: use as reference in decomposition stage
    knowledge_context = None
    if knowledge and knowledge.get("confidence", 0) >= 0.7:
        knowledge_context = _format_knowledge_as_context(knowledge)

    # ── Stage 1: Decomposition + CoT ───────────
    decomposition = DecompositionResult(
        sub_questions=[user_message],
        reasoning_paths=["Direct answer"],
    )

    if ds and _detect_need_decomposition(user_message):
        try:
            decomposition = _decompose_with_cot(
                ds, system_prompt, user_message,
                context, user_preferences,
                knowledge_context,
            )
        except Exception:
            pass  # non-critical: decomposition call failed, fall back to default single-question
    sq = decomposition.sub_questions
    rp = decomposition.reasoning_paths

    # ── Stage 2: Execution (with reasoning path injection) ─
    raw_answers = {}
    for i, (sub_q, reason_path) in enumerate(
        zip(sq, rp + [""] * (len(sq) - len(rp)))
    ):
        raw = None
        err = None
        try:
            sub_system = system_prompt
            if context:
                sub_system = f"{sub_system}\n\n[Context]\n{context[:2000]}"
            if knowledge_context:
                sub_system = f"{sub_system}\n\n{knowledge_context}"
            # Inject reasoning chain
            if reason_path and reason_path != "Direct answer":
                sub_system = (
                    f"{sub_system}\n\n"
                    f"[Analysis approach]\n{reason_path}\n\n"
                    f"Please reason step by step according to the above approach, then give the final answer."
                )

            if ds and (len(sq) > 1 or i > 0):
                raw = call_llm(ds, sub_system, sub_q,
                              temperature=temperature, max_tokens=max_tokens)
            else:
                raw = call_with_fallback(models, sub_system, sub_q)
        except LLMError as e:
            err = str(e)
            try:
                raw = FALLBACK_ENGINE.reply(sub_q, system_prompt)
            except Exception:
                raw = f"(Unable to answer this question: {err})"
        except Exception as e:
            err = str(e)
            raw = f"(Exception: {err})"

        raw_answers[f"q{i}"] = {
            "question": sub_q,
            "answer": raw,
            "error": err,
            "reasoning_path": reason_path,
        }

    # ── Stage 3: Synthesis ─────────────────────
    if len(sq) == 1:
        return raw_answers["q0"]["answer"]

    if ds:
        try:
            return _synthesize(
                ds, system_prompt, user_message,
                raw_answers, context, user_preferences,
            )
        except Exception:
            pass  # non-critical: synthesis LLM call failed, fall back to concatenation

    # Degrade: concatenate
    parts = []
    for i in range(len(sq)):
        ans = raw_answers.get(f"q{i}", {}).get("answer", "")
        parts.append(ans)
    return "\n\n".join(parts)


def _synthesize(
    ds: ModelConfig,
    system_prompt: str,
    original_question: str,
    raw_answers: Dict[str, Dict],
    context: Optional[str] = None,
    user_preferences: Optional[str] = None,
) -> str:
    """Use DeepSeek to recompose sub-answers into a coherent reply."""
    parts = []
    for i in sorted(int(k[1:]) for k in raw_answers.keys()):
        data = raw_answers.get(f"q{i}", {})
        q = data.get("question", "")
        a = data.get("answer", "")
        rp = data.get("reasoning_path", "")
        parts.append(f"Sub-question {i+1}: {q}\nAnalysis approach: {rp}\nAnalysis result: {a}")

    input_text = "\n\n".join(parts)

    sys_prompt = (
        "You are an answer integration expert. Your task is to merge the analysis results of multiple sub-questions into a coherent, complete reply.\n"
        "Requirements:\n"
        "1. Organize in natural language, as if a single person is answering completely\n"
        "2. Do not show markers like 'Sub-question' or 'Analysis result'\n"
        "3. Add context and logical transition words\n"
        "4. If the user has preference descriptions, output in the preferred style\n"
        "5. Be accurate, do not fabricate information"
    )

    inputs = [f"Original question: {original_question}"]
    if user_preferences:
        inputs.append(f"User preferences: {user_preferences[:500]}")
    if context:
        inputs.append(f"Conversation context: {context[:1000]}")
    inputs.append(f"\nMaterials to integrate:\n{input_text}")

    try:
        return call_llm(ds, sys_prompt, "\n".join(inputs),
                       temperature=0.4, max_tokens=4096)
    except Exception:
        return "\n\n".join(
            raw_answers[f"q{i}"]["answer"]
            for i in sorted(int(k[1:]) for k in raw_answers.keys())
        )


# ── Exports ─────────────────────────────────────


__all__ = [
    "ModelConfig", "LLMError", "default_models",
    "call_llm", "call_with_fallback",
    "call_with_fallback_or_fallback",
    "get_available_models", "test_model",
    "auto_detect_ollama",
    "orchestrate",
]
