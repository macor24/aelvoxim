# SPDX-License-Identifier: MIT

"""
metacore.chimera.models — Data models for Chimera integration contracts.

These models define the Intent/Expression/Action format exchanged between
MetaCore (brain), Siren (expression layer), and Serpent (execution layer).

Compatible with chimera-api-v1.0.yaml contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ── Emotion & Tone ────────────────────────────────────

@dataclass
class EmotionProfile:
    """Emotional state for the current response."""
    primary: str = "neutral"         # neutral, helpful, friendly, apologetic, confused, excited, calm, playful
    intensity: float = 0.5           # 0.0 - 1.0
    secondary: str = ""              # Optional secondary emotion

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TTSVoiceParams:
    """TTS voice parameters derived from emotion."""
    speed: float = 1.0               # 0.5 - 2.0
    pitch: str = "medium"            # low, medium, high
    volume: float = 1.0              # 0.0 - 1.0
    emotion_name: str = ""           # Provider-specific emotion tag

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Expression (returned to Siren) ────────────────────

@dataclass
class Expression:
    """The expressive part of an intent response.

    Returned synchronously to Siren so it can start speaking immediately.
    """
    response_text: str = ""
    filler_text: str = ""
    tone: str = "efficient_neutral"      # See ToneProfile in tone_adjuster.py
    emotion_profile: Optional[EmotionProfile] = None
    tts_params: Optional[TTSVoiceParams] = None
    expected_delay_ms: int = 0           # Expected processing delay for action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "response_text": self.response_text,
            "filler_text": self.filler_text,
            "tone": self.tone,
            "emotion_profile": self.emotion_profile.to_dict() if self.emotion_profile else None,
            "tts_params": self.tts_params.to_dict() if self.tts_params else None,
            "expected_delay_ms": self.expected_delay_ms,
        }


# ── Action (sent to Serpent) ──────────────────────────

@dataclass
class Action:
    """An executable action for Serpent."""
    task: str = ""                     # e.g. "send_message_in_wechat"
    target_app: str = ""
    target_element: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    confirmation_prompt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Intent Request / Response ─────────────────────────

@dataclass
class IntentRequest:
    """Incoming request to /api/v1/metacore/intent."""
    content: str = ""
    session_id: str = ""
    user_id: str = ""
    language: str = "zh"               # "zh" or "en"
    context: Optional[Dict[str, Any]] = None  # Previous turn context

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentRequest":
        return cls(
            content=data.get("content", ""),
            session_id=data.get("session_id", ""),
            user_id=data.get("user_id", ""),
            language=data.get("language", "zh"),
            context=data.get("context"),
        )


@dataclass
class IntentResponse:
    """Response from /api/v1/metacore/intent."""
    intent_id: str = ""
    expression: Optional[Expression] = None
    action: Optional[Action] = None       # None for pure chat
    session_id: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "expression": self.expression.to_dict() if self.expression else None,
            "action": self.action.to_dict() if self.action else None,
            "session_id": self.session_id,
            "error": self.error,
        }


# ── Action Stream Messages (WebSocket) ─────────────────

@dataclass
class ActionStreamMessage:
    """Message sent via /api/v1/metacore/action-stream WebSocket."""
    type: str = ""      # "action" | "progress" | "confirmation" | "result" | "error"
    intent_id: str = ""
    action: Optional[Action] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "intent_id": self.intent_id,
            "action": self.action.to_dict() if self.action else None,
            "data": self.data,
        }


__all__ = [
    "EmotionProfile", "TTSVoiceParams",
    "Expression", "Action",
    "IntentRequest", "IntentResponse",
    "ActionStreamMessage",
]
