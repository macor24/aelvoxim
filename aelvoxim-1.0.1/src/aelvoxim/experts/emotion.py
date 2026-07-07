"""
metacore.experts.emotion — Emotion Expert with cross-turn sentiment tracking.

Analyzes user sentiment from the current message AND from recent
conversation history, detecting emotional trajectories, escalation,
and mood shifts. Persists emotion snapshots to ~/.metacore/emotion/.

Pure rule-based, no LLM calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register

# ── Emotion persistence ──────────────────────────────────────

from ..utils import DATA_DIR

_EMOTION_DIR = DATA_DIR / "emotion"

# Tone mapping based on sentiment
_TONE_SUGGESTIONS = {
    "positive": "warm and encouraging",
    "negative": "empathetic and supportive",
    "angry": "calm and de-escalating",
    "sad": "gentle and understanding",
    "neutral": "neutral and informative",
    "frustrated": "patient and solution-oriented",
    "anxious": "reassuring and clear",
}


def _load_emotion_history(user_id: str, limit: int = 10) -> List[Dict]:
    """Load recent emotion snapshots for a user from JSONL history."""
    if not user_id:
        return []
    path = _EMOTION_DIR / f"{user_id}.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(l) for l in lines[-limit:] if l.strip()]
    except Exception:
        return []


def _save_emotion_snapshot(user_id: str, snapshot: Dict) -> None:
    """Persist a single emotion snapshot to JSONL."""
    if not user_id:
        return
    _EMOTION_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(str(_EMOTION_DIR / f"{user_id}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Trend detection ──────────────────────────────────────────


_NEGATIVE_LABELS = {"negative", "angry", "sad", "frustrated", "anxious"}
_POSITIVE_LABELS = {"positive"}


def _detect_trend(history: List[Dict]) -> str:
    """Detect emotional trajectory from recent snapshots.

    Returns one of: negative_escalation, improving, high_activation, stable, insufficient_data
    """
    if len(history) < 2:
        return "insufficient_data"
    recent = history[-3:] if len(history) >= 3 else history
    labels = [h.get("label", "neutral") for h in recent]
    scores = [h.get("score", 0.5) for h in recent]

    # 3 consecutive negative = escalation
    if all(l in _NEGATIVE_LABELS for l in labels):
        return "negative_escalation"

    # Last is positive, but earlier were negative = improving
    if labels[-1] in _POSITIVE_LABELS and any(l in _NEGATIVE_LABELS for l in labels[:-1]):
        return "improving"

    # High intensity (>0.8) = high activation
    if max(scores) > 0.8:
        return "high_activation"

    # Check direction: compare first vs last in window
    if len(scores) >= 3:
        if scores[-1] > scores[0] + 0.15:
            return "intensifying"
        if scores[-1] < scores[0] - 0.15:
            return "cooling"

    return "stable"


_EMOTION_KEYWORDS: Dict[str, List[str]] = {
    "positive": ["happy", "great", "thank", "love", "amazing", "good",
                 "wonderful", "excellent", "perfect", "nice", "cool"],
    "negative": ["sad", "bad", "terrible", "awful", "hate", "upset",
                 "annoying", "disappointed", "regret", "sorry"],
    "angry": ["angry", "furious", "annoyed", "mad", "rage",
              "frustrated", "irritated"],
    "sad": ["sad", "depressed", "lonely", "hurt", "cry", "grief",
            "miss", "loss"],
    "anxious": ["anxious", "worried", "nervous", "scared", "fear",
                "panic", "stress", "overwhelmed"],
    "frustrated": ["frustrated", "stuck", "confused", "hopeless",
                   "can't", "cannot", "failed"],
}

_EMOTION_STRENGTH: Dict[str, float] = {
    "positive": 0.35,
    "negative": 0.40,
    "angry": 0.70,
    "sad": 0.55,
    "anxious": 0.60,
    "frustrated": 0.50,
    "neutral": 0.10,
}


def _detect_sentiment_keyword(query: str) -> Dict:
    """Detect sentiment via keyword matching. Returns {label, score, triggered}."""
    q_lower = query.lower()
    best_label = "neutral"
    best_score = 0.0
    triggered = []
    for label, words in _EMOTION_KEYWORDS.items():
        count = sum(1 for w in words if w in q_lower)
        if count:
            base = _EMOTION_STRENGTH.get(label, 0.3)
            score = min(1.0, base + count * 0.1)
            if score > best_score:
                best_score = score
                best_label = label
                triggered.append(label)
    return {"label": best_label, "score": round(best_score, 2), "triggered": triggered}


@register
class EmotionExpert(BaseExpert):
    """Analyzes user sentiment with cross-turn tracking.

    Pipeline:
    1. Detect sentiment from current message
    2. Load recent emotion history for user
    3. Detect trajectory (escalation / improving / high_activation)
    4. Persist snapshot for future turns
    5. Suggest tone and empathy mode
    """
    _capabilities = ["emotion", "sentiment", "tone", "empathy"]

    name = "emotion"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if another expert (safety/ethics) has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        query = inp.query or ""
        user_id = inp.user_id or ""

        details: Dict[str, Any] = {
            "sentiment": "neutral",
            "empathy_mode": False,
            "tone_suggestion": "neutral and informative",
            "emotion_profile": {},
            "trajectory": {},
            "history_length": 0,
        }

        # Step 1: Detect sentiment from current message
        try:
            from aelvoxim.memory.scorer import detect_signal
            signal = detect_signal(query)
            if signal and isinstance(signal, (list, tuple)) and len(signal) >= 2:
                _sig_type, _sig_conf = signal[0], signal[1]
                details["emotion_profile"] = {"sentiment": _sig_type, "confidence": _sig_conf}
        except Exception:
            pass

        current = _detect_sentiment_keyword(query)
        sentiment = current["label"]
        score = current["score"]

        if sentiment != "neutral":
            details["sentiment"] = sentiment
        elif details["emotion_profile"]:
            prof_sent = details["emotion_profile"].get("sentiment", "neutral")
            if prof_sent != "neutral":
                details["sentiment"] = prof_sent

        # Step 2: Load emotion history
        history = _load_emotion_history(user_id)
        details["history_length"] = len(history)

        # Step 3: Detect trajectory
        trend = _detect_trend(history)
        details["trajectory"] = {
            "trend": trend,
            "history_count": len(history),
        }
        if len(history) >= 2:
            latest_from_history = history[-1]
            details["trajectory"]["previous_label"] = latest_from_history.get("label", "")
            details["trajectory"]["previous_score"] = latest_from_history.get("score", 0.0)

        # Step 4: Persist snapshot
        snapshot = {
            "ts": datetime.now().isoformat(),
            "label": sentiment,
            "score": score,
            "topic": inp.context.get("topic", "") if inp.context else "",
        }
        _save_emotion_snapshot(user_id, snapshot)

        # Step 5: Tone suggestion
        details["tone_suggestion"] = _TONE_SUGGESTIONS.get(
            details["sentiment"], "neutral and informative"
        )

        # Step 6: Empathy mode
        if sentiment in ("negative", "sad", "angry", "frustrated", "anxious"):
            details["empathy_mode"] = True
        if trend == "negative_escalation":
            details["empathy_mode"] = True

        # Step 7: Confidence
        confidence = 0.5
        if sentiment != "neutral":
            confidence = 0.6 + score * 0.2
        if details["emotion_profile"]:
            confidence += 0.1
        if trend != "insufficient_data" and trend != "stable":
            confidence += 0.1  # trajectory info adds confidence

        # Build opinion
        parts = [f"Emotion: {sentiment}"]
        if trend != "insufficient_data":
            parts.append(f"Trend: {trend}")
            if len(history) >= 2:
                shift = score - history[-1].get("score", 0.5)
                parts.append(f"Shift: {shift:+.2f}")
        parts.append(f"Tone: {details['tone_suggestion']}")

        if details["empathy_mode"]:
            parts.append("[Empathy Mode]")

        return ExpertOutput(
            expert_name=self.name,
            opinion=" | ".join(parts),
            confidence=round(min(confidence, 1.0), 2),
            details=details,
            error=None,
        )
