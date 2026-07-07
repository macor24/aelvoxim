# SPDX-License-Identifier: MIT

"""
metacore.chimera.emotion_engine — Emotion and tone computation for Chimera.

Determines the appropriate tone, emotion profile, and TTS parameters
based on:
- Intent type (execute, chat, query)
- User input content (urgency, sentiment)
- Session context (conversation history depth)
- User profile from memory (when available)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .models import EmotionProfile, TTSVoiceParams, Expression


# ── Tone profiles ─────────────────────────────────────

TONE_PROFILES = {
    "efficient_friendly": {
        "description": "Concise, lively with mild colloquialism",
        "emoji": "high",
        "exclamation": True,
        "speed": 1.04,
    },
    "efficient_neutral": {
        "description": "Clear, direct, professional",
        "emoji": "low",
        "exclamation": False,
        "speed": 1.0,
    },
    "empathetic": {
        "description": "Soft, warm, understanding",
        "emoji": "medium",
        "exclamation": False,
        "speed": 0.85,
    },
    "apologetic": {
        "description": "Slightly slower, humble",
        "emoji": "low",
        "exclamation": False,
        "speed": 0.9,
    },
    "enthusiastic": {
        "description": "Energetic, fast, lots of emoji",
        "emoji": "high",
        "exclamation": True,
        "speed": 1.15,
    },
    "professional": {
        "description": "Formal, precise, no emoji",
        "emoji": "none",
        "exclamation": False,
        "speed": 1.0,
    },
    "playful": {
        "description": "Light, humorous, emoji-rich",
        "emoji": "high",
        "exclamation": True,
        "speed": 1.1,
    },
    "thinking": {
        "description": "Slow, pensive, with pauses",
        "emoji": "low",
        "exclamation": False,
        "speed": 0.8,
    },
}


class EmotionEngine:
    """Computes emotional expression parameters for MetaCore responses.

    Usage:
        engine = EmotionEngine()
        expr = engine.compute_expression(
            response_text="Message sent.",
            intent_type="execute",
            user_input="帮我在Chrome搜索AI新闻",
            session_depth=5,
            user_profile={},
        )
    """

    def __init__(self):
        self._tone_profiles = TONE_PROFILES

    def compute_expression(
        self,
        response_text: str,
        intent_type: str = "chat",
        user_input: str = "",
        session_depth: int = 0,
        user_profile: Optional[Dict[str, Any]] = None,
        action_delay_ms: int = 0,
        language: str = "zh",
    ) -> Expression:
        """Compute full Expression with tone, emotion, and TTS params.

        Args:
            response_text: The text content to express.
            intent_type: 'execute', 'chat', or 'query'.
            user_input: Original user message for sentiment analysis.
            session_depth: Number of previous turns in this session.
            user_profile: Optional user personality profile from memory.
            action_delay_ms: Expected delay for action execution.
            language: User language.

        Returns:
            Expression with tone, emotion, TTS params.
        """
        # 1. Select tone
        tone = self._select_tone(intent_type, user_input, session_depth, user_profile)

        # 2. Compute emotion profile
        emotion = self._compute_emotion(intent_type, user_input, tone)

        # 3. Compute TTS parameters
        tts_params = self._compute_tts_params(tone, emotion)

        # 4. Select filler text
        filler = self._select_filler(tone, intent_type, language)

        return Expression(
            response_text=response_text,
            filler_text=filler,
            tone=tone,
            emotion_profile=emotion,
            tts_params=tts_params,
            expected_delay_ms=action_delay_ms,
        )

    def _select_tone(
        self,
        intent_type: str,
        user_input: str,
        session_depth: int,
        user_profile: Optional[Dict[str, Any]],
    ) -> str:
        """Select tone based on intent type and context."""
        user_lower = user_input.lower()

        if intent_type == "execute":
            # Execute intents → efficient and friendly
            return "efficient_friendly"

        elif intent_type == "query":
            # Knowledge queries → informative but warm
            if self._detect_urgency(user_lower):
                return "efficient_friendly"
            return "efficient_neutral"

        else:
            # Chat intents: vary tone based on content
            if self._detect_negative_sentiment(user_lower):
                return "empathetic"
            if self._detect_positive_sentiment(user_lower):
                return "enthusiastic"
            if self._detect_question_mark(user_lower):
                return "efficient_friendly"

            # Default: vary based on session depth
            if session_depth < 3:
                return "efficient_friendly"
            elif session_depth < 10:
                return "efficient_neutral"
            else:
                return "playful"

    def _compute_emotion(
        self,
        intent_type: str,
        user_input: str,
        tone: str,
    ) -> EmotionProfile:
        """Compute emotion profile from context."""
        user_lower = user_input.lower()

        # Map tone to primary emotion
        tone_to_emotion = {
            "efficient_friendly": "helpful",
            "efficient_neutral": "neutral",
            "empathetic": "empathetic",
            "apologetic": "apologetic",
            "enthusiastic": "excited",
            "professional": "neutral",
            "playful": "playful",
            "thinking": "neutral",
        }
        primary = tone_to_emotion.get(tone, "neutral")

        # Compute intensity
        if intent_type == "execute":
            intensity = 0.7
        elif self._detect_urgency(user_lower):
            intensity = 0.8
        elif self._detect_negative_sentiment(user_lower):
            intensity = 0.6
        else:
            intensity = 0.5

        return EmotionProfile(primary=primary, intensity=round(intensity, 2))

    def _compute_tts_params(self, tone: str, emotion: EmotionProfile) -> TTSVoiceParams:
        """Compute TTS parameters from tone and emotion."""
        profile = self._tone_profiles.get(tone, self._tone_profiles["efficient_neutral"])

        speed = profile.get("speed", 1.0)
        # Adjust speed by emotion intensity
        speed += (emotion.intensity - 0.5) * 0.1
        speed = round(max(0.6, min(2.0, speed)), 2)

        # Pitch
        if emotion.intensity > 0.7:
            pitch = "high"
        elif emotion.intensity < 0.3:
            pitch = "low"
        else:
            pitch = "medium"

        return TTSVoiceParams(
            speed=speed,
            pitch=pitch,
            volume=1.0,
            emotion_name=emotion.primary,
        )

    def _select_filler(self, tone: str, intent_type: str, language: str) -> str:
        """Select appropriate filler text."""
        if intent_type == "execute":
            return "好的" if language == "zh" else "OK"
        if tone == "apologetic":
            return "嗯" if language == "zh" else "Um"
        if tone == "empathetic":
            return "嗯嗯" if language == "zh" else "I see"
        return "好的" if language == "zh" else "OK"

    # ── Sentiment helpers ─────────────────────────────

    @staticmethod
    def _detect_urgency(text: str) -> bool:
        urgency = ["快", "马上", "立刻", "赶紧", "urgent", "quickly", "now", "hurry", "asap"]
        return any(w in text for w in urgency)

    @staticmethod
    def _detect_negative_sentiment(text: str) -> bool:
        negative = ["烦", "气", "不行", "问题", "bug", "error", "fail", "wrong", "bad", "sad", "angry"]
        return any(w in text for w in negative)

    @staticmethod
    def _detect_positive_sentiment(text: str) -> bool:
        positive = ["好", "棒", "厉害", "谢谢", "感谢", "nice", "great", "good", "thanks", "awesome", "love"]
        return any(w in text for w in positive)

    @staticmethod
    def _detect_question_mark(text: str) -> bool:
        return "?" in text or "？" in text or "吗" in text


__all__ = ["EmotionEngine", "TONE_PROFILES"]
