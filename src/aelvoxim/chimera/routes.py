# SPDX-License-Identifier: MIT

"""
metacore.chimera.routes — FastAPI routes for Chimera integration.

Implements:
- POST /api/v1/metacore/intent  — Receive user input, return expression + action
- WS  /api/v1/metacore/action-stream — WebSocket for action progress
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse

from .models import (
    IntentRequest, IntentResponse, Expression, Action,
    EmotionProfile, TTSVoiceParams, ActionStreamMessage,
)
from .intent_classifier import IntentClassifier
from .emotion_engine import EmotionEngine

logger = logging.getLogger("aelvoxim.chimera.routes")

router = APIRouter(prefix="/api/v1/metacore")

# ── Singleton engines ──────────────────────────────────

_classifier = IntentClassifier()
_emotion = EmotionEngine()

# ── WebSocket connections ──────────────────────────────

_ws_connections: Dict[str, WebSocket] = {}
_pending_confirmations: Dict[str, asyncio.Event] = {}
_pending_confirmation_results: Dict[str, bool] = {}


# ── Intent endpoint ─────────────────────────────────────


@router.post("/intent")
async def handle_intent(request: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/v1/metacore/intent

    Receives user input, classifies intent, computes emotion,
    and returns expression (+ action for execute intents).

    Request:
    ```json
    {
      "content": "帮我在Chrome搜索AI最新新闻",
      "session_id": "sess_abc123",
      "language": "zh"
    }
    ```

    Response:
    ```json
    {
      "intent_id": "intent_xxx",
      "expression": {
        "response_text": "Chrome 搜索已完成。🔍",
        "filler_text": "好的",
        "tone": "efficient_friendly",
        "emotion_profile": {"primary": "helpful", "intensity": 0.7},
        "tts_params": {"speed": 1.04, "pitch": "medium", "volume": 1.0},
        "expected_delay_ms": 0
      },
      "action": {
        "task": "search_in_chrome",
        "target_app": "Chrome",
        "params": {"query": "AI最新新闻"}
      }
    }
    ```
    """
    intent_id = f"intent_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()

    # Parse request — support both flat and nested formats
    raw_content: Any = request.get("content")
    if raw_content is None:
        # Try nested format: {"input": {"content": "..."}}
        raw_content = request.get("input", {}).get("content", "")
    content: str = str(raw_content) if raw_content else ""

    session_id: str = request.get("session_id", "")
    language: str = request.get("language", "") or request.get("input", {}).get("language", "zh")
    context: Optional[Dict] = request.get("context")

    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    # Step 1: Classify intent
    intent = _classifier.classify(content, language)

    # Step 2: Determine response text and action
    response_text = ""
    action: Optional[Action] = None
    expected_delay = 0

    if intent.is_execute:
        action = intent.action
        if action:
            # Execute via Desktop Gateway (replaces legacy Serpent API)
            import urllib.request as _ur
            import urllib.error as _ue

            _win_host = "127.0.0.1"
            try:
                import subprocess as _sp
                _r = _sp.run(["ip", "route"], capture_output=True, text=True, timeout=3)
                for _line in _r.stdout.splitlines():
                    if _line.startswith("default"):
                        _parts = _line.split()
                        if len(_parts) > 2:
                            _win_host = _parts[2]
                            break
            except Exception:
                pass

            _gw_payload = json.dumps({
                "operation": {
                    "action": getattr(action, "task", ""),
                    "target": getattr(action, "target_app", ""),
                    "params": getattr(action, "params", {}),
                }
            }).encode()
            _gw_url = f"http://{_win_host}:9705/api/execute"
            try:
                _req = _ur.Request(_gw_url, data=_gw_payload,
                                    headers={"Content-Type": "application/json"}, method="POST")
                with _ur.urlopen(_req, timeout=60) as _resp:
                    _gw_result = json.loads(_resp.read().decode())
                if _gw_result.get("success"):
                    response_text = _execute_success_message(action.task, action.target_app, language)
                    expected_delay = 0
                    logger.info("Gateway execute OK: %s/%s", action.task, action.target_app)
                else:
                    response_text = _bilingual(
                        f"操作失败：{_gw_result.get('error', 'unknown error')}",
                        f"Operation failed: {_gw_result.get('error', 'unknown error')}",
                        language,
                    )
                    expected_delay = 0
                    logger.warning("Gateway execute FAIL: %s/%s → %s",
                                   action.task, action.target_app, _gw_result.get("error", ""))
            except Exception as _e:
                response_text = _bilingual(
                    f"操作失败：Gateway 连接错误 — {_e}",
                    f"Operation failed: Gateway connection error — {_e}",
                    language,
                )
                expected_delay = 0
                logger.warning("Gateway unavailable: %s", _e)
        else:
            response_text = _bilingual("收到指令", "Got it", language)
    elif intent.is_query:
        response_text = _bilingual(
            "让我查一下相关资料。",
            "Let me check the relevant information.",
            language,
        )
        expected_delay = 2000
    else:
        response_text = _bilingual(
            "好的，收到。",
            "OK, got it.",
            language,
            is_chat=True,
        )

    # Step 3: Compute expression
    expression = _emotion.compute_expression(
        response_text=response_text,
        intent_type=intent.type,
        user_input=content,
        session_depth=len(context or {}),
        action_delay_ms=expected_delay,
        language=language,
    )

    # Log
    logger.info(
        "Intent: id=%s type=%s content='%s' tone=%s action=%s",
        intent_id, intent.type, content[:40], expression.tone,
        action.task if action else "None",
    )

    # Build response
    result = IntentResponse(
        intent_id=intent_id,
        expression=expression,
        action=action,
        session_id=session_id,
    )

    return result.to_dict()


@router.post("/intent/mock")
async def handle_intent_mock(request: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/v1/metacore/intent/mock

    Simplified mock version that returns predefined expressions
    based on keyword matching — no LLM needed.

    Used for development/testing when no real LLM is configured.
    """
    content: str = request.get("content", "")
    session_id: str = request.get("session_id", "")
    language: str = request.get("language", "zh")
    intent_id = f"intent_{uuid.uuid4().hex[:12]}"

    # Simplified keyword → response mapping
    if any(kw in content for kw in ["发消息", "发送", "发微信", "tell", "send"]):
        action = Action(
            task="send_message_in_wechat",
            target_app="WeChat",
            params=_extract_text_params(content),
        )
        text = "好的，正在帮你发消息。🚀"
        tone = "efficient_friendly"
    elif any(kw in content for kw in ["搜索", "搜一下", "search", "find"]):
        action = Action(
            task="search_in_chrome",
            target_app="Chrome",
            params=_extract_text_params(content),
        )
        text = "好的，正在帮你搜索。🔍"
        tone = "efficient_friendly"
    elif any(kw in content for kw in ["你好", "hi", "hello", "嗨"]):
        action = None
        text = "嗨！有什么需要帮忙的吗？😊"
        tone = "enthusiastic"
    elif any(kw in content for kw in ["谢谢", "感谢", "thanks", "thank"]):
        action = None
        text = "不客气！随时找我。👍"
        tone = "playful"
    else:
        action = None
        text = f"好的，收到了。你在说：{content[:50]}..."
        tone = "efficient_neutral"

    expression = Expression(
        response_text=text,
        filler_text="好的" if action else "嗯",
        tone=tone,
        emotion_profile=EmotionProfile(primary="helpful", intensity=0.6),
        tts_params=TTSVoiceParams(speed=1.0, pitch="medium", volume=1.0),
        expected_delay_ms=500 if action else 0,
    )

    return IntentResponse(
        intent_id=intent_id,
        expression=expression,
        action=action,
        session_id=session_id,
    ).to_dict()


# ── WebSocket: Action Stream ────────────────────────────


@router.websocket("/action-stream")
async def action_stream(websocket: WebSocket):
    """WebSocket /api/v1/metacore/action-stream

    Serpent connects here to receive action commands.
    MetaCore sends:
    - action: New action to execute
    - confirmation: Ask user to confirm a high-risk action
    - cancel: Cancel a pending action

    Serpent sends:
    - progress: {intent_id, progress_pct, status}
    - confirm_result: {intent_id, approved: bool}
    - result: {intent_id, success, error, duration_ms}
    """
    await websocket.accept()
    logger.info("WebSocket connected: action-stream")

    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    _ws_connections[ws_id] = websocket

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error", "data": {"message": "Invalid JSON"},
                }))
                continue

            msg_type = msg.get("type", "")
            intent_id = msg.get("intent_id", "")
            data = msg.get("data", {})

            if msg_type == "progress":
                logger.info(
                    "Action progress: intent=%s progress=%s status=%s",
                    intent_id, data.get("progress_pct"), data.get("status"),
                )

            elif msg_type == "confirm_result":
                approved = data.get("approved", False)
                if intent_id in _pending_confirmations:
                    _pending_confirmation_results[intent_id] = approved
                    _pending_confirmations[intent_id].set()

            elif msg_type == "result":
                logger.info(
                    "Action result: intent=%s success=%s error=%s",
                    intent_id, data.get("success"), data.get("error", ""),
                )

            # Echo back as acknowledgment
            await websocket.send_text(json.dumps({
                "type": "ack",
                "intent_id": intent_id,
                "data": {"received": msg_type},
            }))

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: action-stream")
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
    finally:
        _ws_connections.pop(ws_id, None)


# ── Send action via WebSocket ───────────────────────────


async def send_action(action: Action, intent_id: str) -> bool:
    """Send an action to all connected Serpent instances via WebSocket.

    Returns True if sent successfully.
    """
    if not _ws_connections:
        logger.warning("No WebSocket connections to send action")
        return False

    msg = ActionStreamMessage(
        type="action",
        intent_id=intent_id,
        action=action,
        data={"timestamp": datetime.now().isoformat()},
    )

    dead_connections = []
    sent = False
    for ws_id, ws in _ws_connections.items():
        try:
            await ws.send_text(json.dumps(msg.to_dict()))
            sent = True
        except Exception:
            dead_connections.append(ws_id)

    for ws_id in dead_connections:
        _ws_connections.pop(ws_id, None)

    return sent


# ── Helpers ─────────────────────────────────────────────


def _execute_success_message(task: str, app: str, language: str) -> str:
    """Generate success message for execute intents."""
    zh = {
        "send_message_in_wechat": "好的，正在帮你发消息。🚀",
        "send_message": "好的，正在发送。",
        "search_in_chrome": "好的，正在帮你搜索。🔍",
        "navigate_to_url": "好的，正在打开网页。",
        "screenshot": "好的，正在截图。📸",
        "open_file": "好的，正在打开文件。",
        "save_file": "好的，正在保存文件。",
        "delete_file": "好的，正在删除文件。",
    }
    en = {
        "send_message_in_wechat": "Sending your message. 🚀",
        "send_message": "Sending message.",
        "search_in_chrome": "Searching. 🔍",
        "navigate_to_url": "Opening page.",
        "screenshot": "Taking a screenshot. 📸",
        "open_file": "Opening file.",
        "save_file": "Saving file.",
        "delete_file": "Deleting file.",
    }
    fallback_zh = f"好的，正在处理：{app}。"
    fallback_en = f"Processing: {app}."
    if language == "zh":
        return zh.get(task, fallback_zh)
    return en.get(task, fallback_en)


def _bilingual(zh: str, en: str, language: str, is_chat: bool = False) -> str:
    """Return text in user's language."""
    return zh if language == "zh" else en


def _extract_text_params(content: str) -> Dict[str, str]:
    """Extract text content from user message."""
    import re
    for prefix in ["说：", "说:", "发：", "发:", "说 ", "发 ", "发送消息：", "发送消息:"]:
        if prefix in content:
            parts = content.rsplit(prefix, 1)
            if len(parts) > 1 and parts[1].strip():
                return {"text": parts[1].strip()}
    m = re.search(r"给(.+?)(说|发|发送)", content)
    if m:
        recipient = m.group(1).strip()
        return {"recipient": recipient}
    return {}


__all__ = ["router", "send_action"]
