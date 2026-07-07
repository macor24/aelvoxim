"""
metacore.learn.meta_learner — Meta-learning engine that learns from user conversations.

Pipeline:
  1. ingest_feedback()  — Called at end of chat turn, writes to feedback queue (JSONL)
  2. MetaLearner.tick()  — Called by Learner during idle cycles, batch processes
  3. Landing: write to knowledge base + create negative anchors + trigger directions

Zero LLM calls, pure rules + regex matching.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

_FEEDBACK_DIR = METACORE_DIR / "feedback"
_FEEDBACK_FILE = _FEEDBACK_DIR / "pending.jsonl"


# ══════════════════════════════════════════════════════════════
# Feedback queue management
# ══════════════════════════════════════════════════════════════


def _ensure_dir() -> None:
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)


def ingest_feedback(record: dict) -> None:
    """Write one feedback record to the pending queue.

    Called at the end of a chat turn. Thread-safe (append-only JSONL).
    """
    _ensure_dir()
    entry = {
        "ts": time.time(),
        "datetime": datetime.now().isoformat(),
        **(record or {}),
    }
    try:
        with open(_FEEDBACK_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_feedback() -> List[dict]:
    """Read all pending feedback records."""
    if not _FEEDBACK_FILE.exists():
        return []
    try:
        text = _FEEDBACK_FILE.read_text().strip()
        return [json.loads(line) for line in text.split("\n") if line.strip()]
    except Exception:
        return []


def _clear_feedback() -> None:
    """Clear the pending feedback file."""
    try:
        if _FEEDBACK_FILE.exists():
            _FEEDBACK_FILE.unlink()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# MetaLearner — Core learning engine
# ══════════════════════════════════════════════════════════════


class MetaLearner:
    """Process user feedback signals into knowledge landing and parameter adjustments.

    Configuration overridable via calibration.json "meta_learn" section.
    """

    # Default configuration
    MIN_INTERVAL = 600           # Process at most once every 10 minutes
    CORRECTION_CONFIDENCE = 0.7  # Confidence for correction-derived knowledge
    REPEAT_CONFIDENCE = 0.5      # Confidence for repeat-question-derived knowledge
    NEGATIVE_ANCHOR_CONF = 0.15  # Confidence for negative memory anchors
    MAX_BATCH = 50               # Max feedback records per batch

    def __init__(self, learner=None):
        self._learner = learner
        self._last_process = 0.0

        # Attempt to load overrides from calibration
        try:
            from ..core.calibration import get_calibration
            cal = get_calibration()
            mc = cal.get("meta_learn", default={})
            if mc:
                self.MIN_INTERVAL = mc.get("min_interval", self.MIN_INTERVAL)
                self.CORRECTION_CONFIDENCE = mc.get(
                    "correction_confidence", self.CORRECTION_CONFIDENCE)
                self.REPEAT_CONFIDENCE = mc.get(
                    "repeat_confidence", self.REPEAT_CONFIDENCE)
                self.MAX_BATCH = mc.get("max_feedback_batch", self.MAX_BATCH)
        except Exception:
            pass

    # ── Main entry ──

    def tick(self) -> List[str]:
        """Process backlogged feedback signals. Called by Learner during idle.

        Returns:
            Action descriptions (for log output).
        """
        now = time.time()
        if now - self._last_process < self.MIN_INTERVAL:
            return []
        self._last_process = now

        records = _load_feedback()
        if not records:
            return []

        actions: List[str] = []
        processed = 0
        for rec in records[:self.MAX_BATCH]:
            signals = rec.get("signals", {})
            if not signals:
                continue

            try:
                acts = self._process_one(rec, signals)
                actions.extend(acts)
                processed += 1
            except Exception:
                pass

        _clear_feedback()
        if processed > 0:
            actions.append(f"processed {processed} feedback record(s)")
        return actions

    # ── Per-record processing ──

    def _process_one(self, rec: dict, signals: dict) -> List[str]:
        """Process one feedback record and return action descriptions."""
        actions: List[str] = []
        query = rec.get("query", "")
        user_id = rec.get("user_id", "")

        # 1. Explicit correction signal -> store to knowledge base
        correction = signals.get("correction")
        if correction:
            old_term = correction.get("old_term", "")
            new_term = correction.get("new_term", "")
            if old_term and new_term:
                action = self._handle_correction(old_term, new_term, user_id)
                if action:
                    actions.append(action)

        # 2. Vague correction tone (no specific old/new content, but signals
        #    existing knowledge may be problematic)
        if signals.get("correction_detected") and not correction:
            raw_topic = signals.get("raw_topic", "")
            if raw_topic:
                action = self._handle_vague_correction(raw_topic, user_id)
                if action:
                    actions.append(action)

        # 3. Repeat question -> insufficient knowledge on this topic
        if signals.get("repeat_question"):
            raw_topic = signals.get("raw_topic", query[:80])
            if raw_topic:
                action = self._handle_repeat(raw_topic, user_id)
                if action:
                    actions.append(action)

        return actions

    # ── Landing actions ──

    def _handle_correction(self, old_term: str, new_term: str,
                           user_id: str) -> Optional[str]:
        """Correction landing: write to knowledge base + create negative anchor."""
        try:
            from .knowledge import KnowledgeBase

            KnowledgeBase.store(
                topic=f"correction:{old_term}",
                title=f"Corrected term: {old_term} -> {new_term}",
                summary=(
                    f"User corrected '{old_term}' to '{new_term}'. "
                    f"The correct form/term is '{new_term}', not '{old_term}'."
                ),
                source="user_feedback",
                confidence=self.CORRECTION_CONFIDENCE,
                tags=["correction", "user_feedback", old_term.lower(), new_term.lower()],
                validated=True,
            )
        except Exception:
            pass

        self._create_negative_anchor(old_term)

        # Add learning direction if learner is available
        if self._learner and hasattr(self._learner, 'add_direction'):
            try:
                self._learner.add_direction(new_term)
            except Exception:
                pass

        return f"correction: '{old_term[:20]}' -> '{new_term[:20]}'"

    def _handle_vague_correction(self, topic: str,
                                 user_id: str) -> Optional[str]:
        """Vague correction: flag the topic's knowledge for possible review."""
        self._create_negative_anchor(topic)
        return f"vague_correction: flagged '{topic[:20]}'"

    def _handle_repeat(self, topic: str, user_id: str) -> Optional[str]:
        """Repeat question: add learning direction if not already present."""
        if not self._learner:
            return None

        topic_clean = topic[:60].strip()
        if not topic_clean:
            return None

        directions = getattr(self._learner, '_directions', {})
        if topic_clean in directions:
            # Direction exists but has low saturation -> reset task queue
            d = directions[topic_clean]
            if d.saturation is not None and d.saturation < 0.5:
                d.task_queue = "[]"
                d.current_task = ""
                d.reflect_no_produce = 0
                if hasattr(self._learner, '_save_config'):
                    self._learner._save_config()
                return f"repeat: reset '{topic_clean[:20]}' queue (saturation={d.saturation})"
            return None  # Already well-learned

        # Add new direction
        if hasattr(self._learner, 'add_direction'):
            try:
                self._learner.add_direction(topic_clean)
                return f"repeat: added direction '{topic_clean[:20]}'"
            except Exception:
                pass

        # Fallback: store to knowledge base
        try:
            from .knowledge import KnowledgeBase
            KnowledgeBase.store(
                topic=topic_clean,
                title=f"Repeat inquiry: {topic_clean}",
                summary=f"User repeatedly asked about '{topic_clean}'. May need more knowledge.",
                source="user_feedback",
                confidence=self.REPEAT_CONFIDENCE,
                tags=["repeat_question", "user_feedback"],
                validated=False,
            )
            return f"repeat: stored KB entry '{topic_clean[:20]}'"
        except Exception:
            pass

        return None

    def _create_negative_anchor(self, topic: str) -> None:
        """Write a low-confidence negative anchor into procedural memory.

        Signals that "this topic may have errors / is not recommended".
        """
        try:
            from ..memory import store_entity
            eid = f"negative_anchor:{abs(hash(topic)) & 0x7FFFFFFF:x}"
            store_entity(
                eid=eid,
                etype="concept",
                attributes={
                    "name": topic,
                    "_confidence": self.NEGATIVE_ANCHOR_CONF,
                    "_is_negative_anchor": True,
                    "source": "meta_learner",
                },
                tags=["negative_anchor", topic.lower()],
            )
        except Exception:
            pass
