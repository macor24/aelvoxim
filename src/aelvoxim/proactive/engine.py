"""
metacore.proactive.engine — ProactiveEngine main loop.

Background thread that ticks every N minutes, checks all users,
and pushes proactive messages when appropriate.
"""

from __future__ import annotations

import logging
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

_log = logging.getLogger("aelvoxim.proactive.engine")

from ..storage.db import fetch_dict, execute, use_pg

class ProactiveEngine:
    """Background engine that pushes proactive messages to users."""

    def __init__(self, tick_interval: int = 300):
        """
        Args:
            tick_interval: seconds between ticks (default 5 min)
        """
        self._tick_interval = tick_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self):
        """Start the background loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        _log.info("ProactiveEngine started (tick=%ss)", self._tick_interval)

    def stop(self):
        """Stop the background loop."""
        self._running = False
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        """Check if the engine is running."""
        return self._running

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                _log.warning("ProactiveEngine tick error: %s", e)
            self._stop_event.wait(self._tick_interval)

    def _tick(self):
        """One tick: check all users and push if needed."""
        if not use_pg():
            return

        from .detector import find_silent_users
        from .gate import FrequencyGate
        from .predictor import TopicPredictor
        from .selector import BehaviorSelector
        from .dispatcher import ChannelDispatcher
        from .feedback import FeedbackLearner

        gate = FrequencyGate()
        predictor = TopicPredictor()
        selector = BehaviorSelector()
        dispatcher = ChannelDispatcher()
        feedback = FeedbackLearner()

        silent_users = find_silent_users(min_hours=24)
        for user in silent_users:
            user_id = user.get("id")
            email = user.get("email", "?")
            config = user.get("proactive_config")
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except Exception:
                    config = {}

            if not gate.should_push(user_id, config):
                continue

            predicted_topics = predictor.predict(user_id)
            push_type, content, topic = selector.choose(
                user_id, predicted_topics, config
            )
            if not content:
                continue

            success = dispatcher.dispatch(user_id, email, push_type, content, topic)
            if success:
                gate.record_push(user_id)
                feedback.record_push(user_id, push_type, topic)
