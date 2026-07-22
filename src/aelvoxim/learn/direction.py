"""aelvoxim.learn.direction — Learning direction data model + manager

Split from learner.py (1969-line monolith).
Responsibility: LearningDirection dataclass, DirectionManager CRUD, config persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR, ensure_dir
from .knowledge import KnowledgeBase

import logging
_log = logging.getLogger("aelvoxim.learn.direction")

# ── Data paths ──
LEARNER_DIR = METACORE_DIR / "learner"
CONFIG_FILE = LEARNER_DIR / "config.json"
_SENTRIKIT_CHECKED = False


def _ensure_learner_dir() -> None:
    ensure_dir(LEARNER_DIR)


# ── Direction dataclass ──


@dataclass
class LearningDirection:
    """A learning direction with its state."""
    topic: str
    status: str = "active"       # active / paused / completed / mastery
    phase_index: int = 0
    template_index: int = 0
    saturation: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    task_queue: str = ""
    current_task: str = ""
    completed_tasks: str = ""
    entries_created: int = 0
    cycles_completed: int = 0
    reflect_no_produce: int = 0
    review_history: str = ""
    last_verified: str = ""
    added_from: str = ""
    source_plan: str = ""
    source_milestone: str = ""
    last_cycle: float = 0.0


# ── Config persistence (PG dual-mode) ──


def save_config_to_file(data: Dict) -> None:
    """Save learning directions config — PG upsert or JSON file."""
    from ..storage.db import execute, use_pg as _up
    if _up():
        try:
            for key, val in data.items():
                if not isinstance(val, dict):
                    continue
                execute("""
                    INSERT INTO learning_directions (topic, status, phase_index, saturation, entries_created, config)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (topic) DO UPDATE SET
                        status = EXCLUDED.status,
                        phase_index = EXCLUDED.phase_index,
                        saturation = EXCLUDED.saturation,
                        entries_created = EXCLUDED.entries_created,
                        config = EXCLUDED.config,
                        updated_at = NOW()
                """, (
                    val.get("topic", key),
                    val.get("status", "active"),
                    val.get("phase_index", 0),
                    val.get("saturation", 0.0),
                    val.get("entries_created", 0),
                    json.dumps(val),
                ))
        except Exception as e:
            import logging
            logging.getLogger("aelvoxim.direction").warning("PG save failed: %s", e)
    _ensure_learner_dir()
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(CONFIG_FILE)


def load_config_from_file() -> Dict:
    """Load learning directions config — PG first, JSON fallback."""
    from ..storage.db import fetch_dict, use_pg as _up
    if _up():
        try:
            rows = fetch_dict("SELECT * FROM learning_directions ORDER BY started_at DESC")
            if rows:
                result = {}
                for r in rows:
                    topic = r.get("topic", "")
                    if topic:
                        cfg = r.get("config") or {}
                        if isinstance(cfg, str):
                            try:
                                cfg = json.loads(cfg)
                            except Exception:
                                cfg = {}
                        result[topic] = dict(cfg) if isinstance(cfg, dict) else {}
                        result[topic]["topic"] = topic
                        result[topic]["status"] = r.get("status", "active")
                        result[topic]["phase_index"] = r.get("phase_index", 0)
                        result[topic]["saturation"] = r.get("saturation", 0.0)
                        result[topic]["entries_created"] = r.get("entries_created", 0)
                if result:
                    return result
        except Exception:
            _log.exception("direction error")
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


# ── Direction manager ──


class DirectionManager:
    """Manages the learning direction registry: add, remove, query, save, load."""

    def __init__(self, log_func, plan_getter=None):
        self._directions: Dict[str, LearningDirection] = {}
        self._log = log_func
        self._plan_getter = plan_getter  # optional callable → plan name

    # ── Validation ──

    @staticmethod
    def is_valid_direction(topic: str) -> bool:
        """Check if a topic is a valid learning direction (not junk)."""
        import re
        t = topic.strip()
        if len(t) < 3 or len(t) > 80:
            return False
        if re.match(r'^[a-z]{1,10}-[a-f0-9]{5,}', t):
            return False
        if t.startswith('correction:'):
            return False
        if re.match(r"^\s*(DROP|ALTER|CREATE|INSERT|DELETE)[_\s]", t, re.I):
            return False
        fragment_markers = ['的吧', '吧，', '吧。', '了？', '重新生成', '白费了', '你是的']
        if any(m in t for m in fragment_markers):
            return False
        if len(t) <= 2:
            return False
        return True

    # ── SentriKit safety gate ──

    @staticmethod
    def _safety_check(topic: str) -> bool:
        """Run SentriKit safety check on a new direction (silent pass on failure)."""
        try:
            from ..client.security_gate import check_evolution as _sk_ev
            _sk_r = _sk_ev("add", topic)
            return _sk_r.get("allowed", True)
        except Exception:
            return True

    # ── CRUD ──

    def add(self, topic: str, plan: str = "community") -> bool:
        topic = topic.strip()
        if not topic:
            return False
        if not self.is_valid_direction(topic):
            self._log(f"  🚫 Rejected low-quality direction: {topic}")
            return False
        if topic in self._directions:
            return False
        # Plan limit check
        try:
            from ..server.auth import PLANS
            cfg = PLANS.get(plan, PLANS['community'])
            if len(self._directions) >= cfg['max_directions']:
                self._log(f"  🚫 Direction limit reached ({plan}: {cfg['max_directions']})")
                return False
        except Exception:
            _log.exception("direction error")
        # SentriKit safety check
        if not self._safety_check(topic):
            return False
        self._directions[topic] = LearningDirection(
            topic=topic, started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._log(f"📚 Added learning direction: {topic}")
        return True

    def remove(self, topic: str) -> bool:
        if topic not in self._directions:
            return False
        del self._directions[topic]
        self._log(f"🗑️ Removed learning direction: {topic}")
        return True

    def pause(self, topic: str) -> bool:
        if topic not in self._directions:
            return False
        self._directions[topic].status = "paused"
        return True

    def resume(self, topic: str) -> bool:
        if topic not in self._directions:
            return False
        self._directions[topic].status = "active"
        return True

    def list_all(self) -> List[dict]:
        return [asdict(d) for d in self._directions.values()]

    def get(self, topic: str) -> Optional[LearningDirection]:
        return self._directions.get(topic)

    def all_directions(self) -> Dict[str, LearningDirection]:
        return self._directions

    def active_count(self) -> int:
        return sum(1 for d in self._directions.values() if d.status == "active")

    # ── Persistence ──

    def save(self) -> None:
        """Save direction config to persistent storage."""
        from datetime import datetime as _dt
        _now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        for d in self._directions.values():
            if d.status in ("completed", "mastery") and not d.completed_at:
                d.completed_at = _now
        data = {t: asdict(d) for t, d in self._directions.items()}
        save_config_to_file(data)

    def load(self) -> None:
        """Load direction config from persistent storage."""
        data = load_config_from_file()
        for topic, cfg in data.items():
            if isinstance(cfg, dict):
                # Pop fields that are not part of LearningDirection
                cfg.pop("config", None)
                try:
                    self._directions[topic] = LearningDirection(**cfg)
                except Exception:
                    _log.exception("direction error")
