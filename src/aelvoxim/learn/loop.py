"""aelvoxim.learn.loop — Learner main class (orchestration loop)

Split from learner.py (1969-line monolith). All business logic delegated
to specialized modules: direction, report, goals, cleanup, discovery, scheduler, meta_cog.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR, ensure_dir, read_json, write_json, LLM_CONFIG_FILE

_RATE_LIMIT_MAX = 5  # Max cognition ticks per 24 hours (per direction)
_RATE_LIMIT_MIN_INTERVAL = 300  # Min seconds between cognition ticks per direction (5 min — was 60s; fewer, slower ticks)
_LOOP_ROUND_SLEEP = 600       # Rest between full loop rounds (10 min — was ~8-15s per direction)
_MAX_ACTIVE_DIRECTIONS = 22  # Max concurrently active learning directions; beyond this, auto-discovery pauses

# Paths
LEARNER_DIR = METACORE_DIR / "learner"
LOG_FILE = LEARNER_DIR / "learner.log"
STATUS_FILE = LEARNER_DIR / "status.json"
CONFIG_FILE = LEARNER_DIR / "config.json"
ensure_dir(LEARNER_DIR)

# Task categories
TASK_DECOMPOSE_CATEGORIES = [
    "Core Concepts",
    "Main Tools and Frameworks",
    "Implementation Steps",
    "Common Issues and Solutions",
    "Best Practices",
    "Performance Optimization",
]

# ── Imports from split modules ──
from .direction import LearningDirection, DirectionManager, load_config_from_file
from .knowledge import KnowledgeBase
from .search import search as _search
from .decompose import decompose_direction, detect_lang
from .validate import execute_and_validate
from .discover import suggest_directions_from_knowledge
from .extract import call_llm_if_available as _call_llm_if_available
from .report import log as _report_log, update_daily_brain_report as _update_daily_report
from .goals import search_and_learn as _goals_search_and_learn
from .goals import set_active_goals as _goals_set_active
from .goals import progress_goals as _goals_progress
from .cleanup import memory_layer_cleanup as _memory_cleanup
from .cleanup import cleanup_knowledge_base as _kb_cleanup
from .discovery import try_discover_new_directions as _try_discover
from .discovery import auto_add_direction as _auto_add
from .scheduler import submit_verification_task, schedule_review, check_reviews
from .scheduler import check_pending_promotions, llm_verify_practice
from .meta_cog import analyze_triggers, analyze_with_hypotheses
from .meta_cog import execute_reflection, verify_repair, update_selfmodel_from_repair

import logging
_log = logging.getLogger("aelvoxim.loop")



# ── Singleton pattern ──
_learner_instance = None
_learner_lock = threading.Lock()


def get_learner():
    global _learner_instance
    if _learner_instance is None:
        with _learner_lock:
            if _learner_instance is None:
                _learner_instance = Learner(enable_auto_discover=True)
    return _learner_instance


class Learner:
    """Persistent self-learning agent. Runs a background loop.

    Orchestrates learning directions via delegation to sub-modules.
    """

    def __init__(self, enable_auto_discover: bool = False, skip_load: bool = False):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._health_alive = False          # health daemon lifecycle (independent of _running)
        self._start_lock = threading.Lock()   # prevent duplicate start() spawns
        self._restart_lock = threading.Lock() # serialize health-daemon respawns
        self._last_restart_ts = 0.0          # anti-flap: min 60s between respawns
        self._rapid_restarts = 0             # rapid-death counter → 10min cooldown
        self._knowledge = KnowledgeBase()
        self._last_auto_discover = 0
        self._enable_auto_discover = enable_auto_discover
        self._last_heartbeat = 0.0
        self._lock = threading.Lock()
        self._watchdog_event = threading.Event()
        self._cognition_time = 0.0
        self._active_goals = []
        self._last_repair = None
        self._cycles_since_repair = 0
        self._cognition_cycle_count = 0
        self._search_rr = 0
        self._last_discovery = 0
        self._teach_consecutive = 0  # consecutive teach-mode cycles before fallback
        self._last_llm_check = 0.0  # timestamp of last LLM availability check
        self._llm_status = "unknown"  # "available" / "degraded" / "unavailable"
        self._search_mock = False    # true if search engine is mock/unavailable

        # DirectionManager — CRUD + persistence
        self._dir_mgr = DirectionManager(log_func=self._log)
        self._directions = self._dir_mgr._directions  # pointer to same dict

        if not skip_load:
            self._dir_mgr.load()
        try:
            from .monitor import HealthMonitor
            self._monitor = HealthMonitor()
        except Exception:
            self._monitor = None

        self._log("Learner initialized")
        # Initial LLM status detection
        self._detect_llm_status()

    # ── Logging (delegated to report.py) ──

    def _log(self, msg: str):
        _report_log(msg)

    # ── Direction management (delegated to DirectionManager) ──

    @staticmethod
    def _is_valid_direction(topic: str) -> bool:
        return DirectionManager.is_valid_direction(topic)

    def add_direction(self, topic: str) -> bool:
        # Pending queue backpressure: block new topics if queue is full
        try:
            from ..learn.knowledge import _read_pending
            _pending = _read_pending()
            if len(_pending.get("pending", [])) >= 100:
                self._log(f"  🚫 [Backpressure] Pending queue full ({len(_pending['pending'])}), blocking new topic: {topic}")
                return False
        except Exception:
            pass
        import os
        _ed = os.environ.get("AELVOXIM_EDITION", "community")
        _plan_map = {"enterprise": "enterprise", "pro": "pro", "trial": "enterprise"}
        plan = _plan_map.get(_ed, "community")
        result = self._dir_mgr.add(topic, plan=plan)
        if result:
            self._dir_mgr.save()
        return result

    def remove_direction(self, topic: str) -> bool:
        result = self._dir_mgr.remove(topic)
        if result:
            self._dir_mgr.save()
        return result

    def pause_direction(self, topic: str) -> bool:
        result = self._dir_mgr.pause(topic)
        if result:
            self._dir_mgr.save()
        return result

    def resume_direction(self, topic: str) -> bool:
        result = self._dir_mgr.resume(topic)
        if result:
            self._dir_mgr.save()
        return result

    def list_directions(self) -> List[dict]:
        return self._dir_mgr.list_all()

    # ── Main loop ──

    # ── LLM status detection ──

    def _detect_llm_status(self) -> str:
        """Detect LLM availability and search mock status.

        Sets self._llm_status and self._search_mock.
        Checks LLM config file existence + valid API key.
        Also checks if search engine is mock.

        Returns: current _llm_status string.
        """
        now = time.time()
        # Re-check every 60 seconds max
        if now - self._last_llm_check < 60:
            return self._llm_status
        self._last_llm_check = now

        # 1. LLM availability
        try:
            from .extract import call_llm_if_available
            llm = call_llm_if_available()
            if llm:
                self._llm_status = "available"
            else:
                self._llm_status = "degraded"
        except Exception:
            self._llm_status = "degraded"

        # 2. Search mock status
        try:
            from .extract import is_search_mock
            self._search_mock = is_search_mock()
        except Exception:
            self._search_mock = False

        if self._llm_status == "degraded":
            if self._search_mock:
                self._log(f"  📡 LLM degraded + search mock → teach mode")
            else:
                self._log(f"  📡 LLM degraded, search available → rule-based fallback")

        return self._llm_status

    # ── Teach-mode learning (no LLM, no real search) ──

    def _teach_one_cycle(self, direction: LearningDirection) -> bool:
        """Teach-mode learning cycle: use preset knowledge when LLM+search unavailable.

        Falls back to presets library. Each cycle produces one knowledge entry.
        After TEACH_MAX_CONSECUTIVE cycles with no new directions, pauses.
        """
        topic = direction.topic
        try:
            from .teach import TeachEngine
            if not hasattr(self, '_teach_engine') or self._teach_engine is None:
                self._teach_engine = TeachEngine()

            if not self._teach_engine.can_teach(topic):
                self._log(f"  📕 [{topic}] No teach presets available, marking direction complete")
                direction.status = "completed"
                self._dir_mgr.save()
                return True

            # Get current task to pass to teach engine for content matching
            task = direction.current_task or topic

            entry = self._teach_engine.learn_one_cycle(topic, task, log_func=self._log)
            if not entry:
                self._teach_consecutive += 1
                if self._teach_consecutive >= 3:
                    self._log(f"  📕 [{topic}] Teach exhausted, marking direction complete")
                    direction.status = "completed"
                    self._dir_mgr.save()
                return True

            # Store the knowledge entry
            self._teach_consecutive = 0
            try:
                kb_entry = KnowledgeBase.store_pending(
                    topic=topic,
                    title=entry["title"],
                    summary=entry["summary"],
                    content=entry["content"],
                    source=entry.get("source", "teach"),
                    tags=entry.get("tags", [topic]),
                    confidence=entry.get("confidence", 0.5),
                    depth=entry.get("depth", 2),
                    validated=entry.get("validated", False),
                )
                if kb_entry:
                    direction.entries_created += 1
                    direction.cycles_completed += 1
                    direction.current_task = ""
                    self._dir_mgr.save()
                    self._log(f"  📗 [{topic}] Teach stored: {entry['title'][:50]}")
                return True
            except Exception as e:
                self._log(f"  ⚠️ [{topic}] Teach store failed: {e}")
                return True
        except ImportError:
            self._log(f"  ⚠️ [{topic}] Teach engine not available")
            direction.status = "completed"
            self._dir_mgr.save()
            return True

    def _learn_one_cycle(self, direction: LearningDirection) -> bool:
        """Process one learning direction: decompose → execute → validate → review."""
        topic = direction.topic

        # ── Validation cooldown: skip topics that recently failed validation
        # repeatedly, so a stuck topic cannot dominate the loop or burn LLM
        # quota. Cooldown is set in validate.execute_and_validate().
        try:
            from .validate import is_cooldown
            if is_cooldown(topic):
                self._log(f"  ⏸️ [{topic}] In validation cooldown, skipping cycle")
                return False
        except Exception:
            pass

        # ── Pre-execution checkpoint: skip completed tasks ──
        if direction.current_task:
            _done = json.loads(direction.completed_tasks or "[]")
            if direction.current_task in _done:
                self._log(f"  ⏭️ [{topic}] Task already completed, skipping: {direction.current_task[:50]}")
                direction.current_task = ""
                self._dir_mgr.save()
                # Try to fetch the next unfinished task
                if direction.task_queue and direction.task_queue != "[]":
                    q = json.loads(direction.task_queue)
                    while q:
                        t = q.pop(0)
                        if t not in _done:
                            direction.current_task = t
                            direction.task_queue = json.dumps(q)
                            self._dir_mgr.save()
                            break
                    if not direction.current_task:
                        return self._reflect_generate_tasks(direction)
                else:
                    return self._reflect_generate_tasks(direction)

        if not direction.task_queue or direction.task_queue == "[]":
            direction_meta = {
                "saturation": direction.saturation,
                "entries_created": direction.entries_created,
            }
            tasks = decompose_direction(topic, self._log, direction_meta=direction_meta)
            direction.task_queue = json.dumps(tasks)
            direction.current_task = ""
            self._dir_mgr.save()
            self._log(f"  🎯 [{topic}] First decomposition: {len(tasks)} tasks")
            return True

        if not direction.current_task:
            q = json.loads(direction.task_queue)
            while q:
                t = q.pop(0)
                # Skip tasks in 10min cooldown (3x qskip)
                _skip_until = json.loads(getattr(direction, '_task_skip_until', '{}'))
                if t in _skip_until and time.time() < _skip_until[t]:
                    continue
                # Cooldown expired — enrich task with keyword supplement for retry
                if t in _skip_until:
                    _enriched = t + " (with implementation details, code examples, configuration, deployment guide, best practices, troubleshooting)"
                    if _enriched not in json.loads(direction.completed_tasks or "[]"):
                        t = _enriched
                        # Clear cooldown record so retry is tracked as fresh
                        del _skip_until[t]
                        direction._task_skip_until = json.dumps(_skip_until)
                if t not in json.loads(direction.completed_tasks or "[]"):
                    direction.current_task = t
                    direction.task_queue = json.dumps(q)
                    self._dir_mgr.save()
                    break
            if not direction.current_task:
                return self._reflect_generate_tasks(direction)

        task = direction.current_task

        # Determine value level from topic name
        _vl = 2  # default: long-term
        _low = ("报错", "错误", "故障", "Error", "错误信息", "临时", "test", "temp", "示例", "demo")
        _high = ("偏好", "喜欢", "用", "习惯", "常用", "推荐", "首选", "配置", "技巧")
        _combined = (topic + " " + task).lower()
        if any(kw.lower() in _combined for kw in _low):
            _vl = 1
        elif any(kw in _combined for kw in _high):
            _vl = 3

        def on_store(topic, title, score):
            direction.entries_created += 1
            direction.cycles_completed += 1
            direction.fail_streak = 0  # reset fail streak on any success
            direction._cooldown_count = 0  # reset cooldown backoff on success
            direction._quality_skip_streak = 0  # reset quality skip counter on success
            done = json.loads(direction.completed_tasks or "[]")
            done.append(task)
            direction.completed_tasks = json.dumps(done)
            direction.current_task = ""
            self._dir_mgr.save()
            # Record tracking outcome
            try:
                from ..hooks.tracker import list_active as _la
                for _at in _la():
                    from ..hooks.tracker import record_outcome as _ro
                    _ro(_at["id"], True)
            except Exception:
                _log.exception("loop error")
            # Sync to knowledge graph
            try:
                from ..server.knowledge_graph import add_knowledge_entry
                add_knowledge_entry(topic, title)
            except Exception:
                _log.exception("loop error")

        result = execute_and_validate(
            topic=topic,
            task=task,
            log_func=self._log,
            on_store=on_store,
            value_level=_vl,
        )

        if not result:
            direction.cycles_completed += 1
            # ── Failure tracking by category ──
            direction.fail_streak += 1
            _fail_streak = direction.fail_streak
            # Categorize failure reason
            _task_lower = task.lower()
            _reason_cat = "validation"
            _timeout_kw = ["timeout", "too long", "timed out", "no response"]
            _quality_kw = ["generic", "template", "irrelevant", "empty", "no content"]
            _search_kw = ["search empty", "no result", "not found", "no data"]
            if any(k in _task_lower for k in _timeout_kw):
                _reason_cat = "timeout"
            elif any(k in _task_lower for k in _quality_kw):
                _reason_cat = "quality"
            elif any(k in _task_lower for k in _search_kw):
                _reason_cat = "search_empty"
            # Quality and validation skips don't trigger circuit breaker, but cap consecutive attempts
            _is_quality_skip = _reason_cat in ('quality', 'validation')
            if _is_quality_skip:
                direction.fail_streak = max(0, direction.fail_streak - 1)
                # Per-task quality skip tracking: 3 skips for the same task → 10min cooldown
                _task_qskip = json.loads(getattr(direction, '_task_quality_skip', '{}'))
                _task_qskip[task] = _task_qskip.get(task, 0) + 1
                direction._task_quality_skip = json.dumps(_task_qskip)
                # Check if this task hit 3 qskips
                if _task_qskip[task] >= 3:
                    _task_skip_until = json.loads(getattr(direction, '_task_skip_until', '{}'))
                    _task_skip_until[task] = time.time() + 600  # 10min cooldown for this task
                    direction._task_skip_until = json.dumps(_task_skip_until)
                    self._log(f"  🛑 [{topic}] Task '{task[:30]}' hit {_task_qskip[task]}x qskip — 10min cooldown")
                else:
                    self._log(f"  ⏭️ [{topic}] Skip ({direction.fail_streak}/3 task_qskip={_task_qskip[task]}, {_reason_cat}): {task}")
                # Also track direction-level qskip for overall pause
                _q_skip = getattr(direction, '_quality_skip_streak', 0) + 1
                direction._quality_skip_streak = _q_skip
                if _q_skip >= 5:
                    direction.status = "paused"
                    direction._circuit_state = "OPEN"
                    _prev = getattr(direction, '_cooldown_count', 0)
                    direction._cooldown_count = _prev + 1
                    direction.fail_cooldown_until = time.time() + 600 * (2 ** min(_prev, 4))
                    self._dir_mgr.save()
                    self._log(f"  🛑 [{topic}] {_q_skip}x quality skips — pausing (cooldown={600 * (2 ** min(_prev, 4))}s)")
                return result

            # Update fail_by_reason dict
            _reasons = {}
            if direction.fail_by_reason:
                try:
                    _reasons = json.loads(direction.fail_by_reason)
                except (json.JSONDecodeError, ValueError):
                    _reasons = {}
            _reasons[_reason_cat] = _reasons.get(_reason_cat, 0) + 1
            direction.fail_by_reason = json.dumps(_reasons)
            done = json.loads(direction.completed_tasks or "[]")
            done.append(task)
            direction.completed_tasks = json.dumps(done)
            direction.current_task = ""
            self._dir_mgr.save()

            # ── Circuit breaker: 3 fails → OPEN, 10-min cooldown; HALF_OPEN fail → permanent freeze ──
            _threshold = 3

            if _fail_streak >= _threshold:
                direction.status = "paused"
                direction._circuit_state = getattr(direction, '_circuit_state', '')
                # If already HALF_OPEN and still failing → permanent freeze
                if direction._circuit_state == "HALF_OPEN":
                    direction.disable_auto_resume = True
                    direction._circuit_state = "FROZEN"
                    self._dir_mgr.save()
                    self._log(f'  🧊 [{topic}] HALF_OPEN failed — permanently frozen')
                    return result
                direction._circuit_state = "OPEN"
                # Exponential backoff: 10min, 20min, 40min, 80min, 160min...
                _prev_cooldowns = getattr(direction, '_cooldown_count', 0)
                direction._cooldown_count = _prev_cooldowns + 1
                _backoff = 600 * (2 ** min(_prev_cooldowns, 4))  # 10min × 2^n
                direction.fail_cooldown_until = time.time() + _backoff
                # Keyword supplementation: enrich search terms to reduce future rejections
                if 'missing_domain_keyword' in str(_reasons):
                    _existing = json.loads(getattr(direction, 'keywords', '[]') or '[]')
                    _new_kw = ['algorithm', 'implementation', 'analysis',
                               'architecture', 'performance', 'optimization',
                               'method', 'framework', 'design pattern']
                    direction.keywords = json.dumps(list(set(_existing + _new_kw)))
                self._dir_mgr.save()
                # Log with reason breakdown
                _reason_detail = "; ".join(f"{k}={v}" for k, v in sorted(_reasons.items()))
                self._log(f"  🛑 [{topic}] {_fail_streak}x consecutive fails — pausing "
                          f"(reasons: {_reason_detail})")
                # Fault classification tag
                _fault_tag = ""
                if _fail_streak >= _threshold:
                    _fault_tag = "[fault:excessive_failures]"
                self._log(f"  🧠 [{topic}] Reflection: "
                          f"1) check approach 2) prerequisite knowledge 3) problem definition {_fault_tag}")
                # Record to metacog
                try:
                    from ..core.metacog import MetaCogTrigger
                    _tr = MetaCogTrigger()
                    _tr.evaluate(
                        success_rate_7d=0.0, success_rate_3d=0.0, success_rate_1d=0.0,
                        days_since_last_improvement=0,
                        repeat_error_count=_fail_streak,
                        external_signal_strength=0.0,
                    )
                except Exception:
                    _log.exception("loop error")
                return True

            self._log(f"  ⏭️ [{topic}] Skip ({_fail_streak}/{_threshold}, {_reason_cat}): {task}")

            try:
                from ..hooks.analyzer import analyze
                report = analyze(lookback=20)
                if report.should_trigger_tuning(0.3):
                    self._log(f"  📊 [Auto-tune] Failure rate {report.failure_rate:.0%}, analyzing patterns...")
                    for p in report.top_patterns[:3]:
                        self._log(f"    - {p.pattern}: {p.count}x ({p.frequency:.0%}), suggest: {p.suggestion}")
                    from ..core.calibration import Calibration
                    from ..core.selfmodel import SelfModel
                    cal = Calibration()
                    sm = SelfModel()
                    snap = sm.take_snapshot() if hasattr(sm, 'take_snapshot') else None
                    analysis = {
                        "hit_rate": snap.overall_success_rate if snap else 0.5,
                        "judge_error_rate": 0,
                        "by_type": {},
                        "total_proposals": 0,
                        "total_applied": 0,
                        "failure_report": report.to_dict(),
                    }
                    changes = cal.auto_tune(analysis)
                    if changes:
                        self._log(f"  🔧 Auto-tune applied: {len(changes)} changes")
            except Exception as e:
                self._log(f"  ⚠️ [Auto-tune] Error: {e}")

            return True

        self._cognition_tick()
        return result

    def _reflect_generate_tasks(self, direction: LearningDirection) -> bool:
        """Reflect on completed tasks and generate new ones if possible."""
        topic = direction.topic
        direction.reflect_no_produce += 1
        entries = list(KnowledgeBase.search(query=topic, limit=5))
        if not entries:
            self._log(f"  ⏳ [{topic}] Queue empty, no knowledge yet ({direction.reflect_no_produce}/3)")
            if direction.reflect_no_produce >= 3:
                direction.status = "completed"
                self._log(f"  ✅ [{topic}] No progress after 3 rounds, marking complete")
                submit_verification_task(topic, is_review=False, directions=self._directions,
                                        save_config_fn=self._dir_mgr.save, log_func=self._log)
                schedule_review(topic, directions=self._directions,
                               save_config_fn=self._dir_mgr.save, log_func=self._log)
                self._dir_mgr.save()
            return True
        # ── Saturation estimate (v3): weighted validation quality + task completion + difficulty ──
        conf_avg = sum(e.get("confidence", 0.5) for e in entries) / max(len(entries), 1)
        entry_ratio = min(direction.entries_created / 5.0, 1.0)
        # validation pass rate
        done_tasks = json.loads(direction.completed_tasks or "[]")
        total_tasks = len(done_tasks) + (len(json.loads(direction.task_queue or "[]")) if direction.task_queue and direction.task_queue != "[]" else 0)
        high_conf = sum(1 for e in entries if e.get("confidence", 0) >= 0.6)
        # Also count entries stored via code/metric direct path (not in KnowledgeBase.search)
        high_conf = max(high_conf, direction.entries_created)
        verify_pass_rate = high_conf / max(len(entries) + direction.entries_created, 1)
        task_complete_rate = len(done_tasks) / max(total_tasks, 1) if total_tasks > 0 else 0
        # difficulty: more tasks + more cycles consumed -> higher difficulty
        # saturation threshold should drop as difficulty rises
        difficulty = min(1.0, total_tasks / 10.0) if total_tasks > 0 else 0.3
        # w_quality > w_progress > w_difficulty
        direction.saturation = round(
            min(1.0, verify_pass_rate * 0.50 + task_complete_rate * 0.30 + (1.0 - difficulty) * 0.20), 2
        )
        # ── Progress vs expected check ──
        expected_min = task_complete_rate * 0.5  # expect at least 50% completion rate by now
        if direction.cycles_completed > 5 and verify_pass_rate < expected_min:
            self._log(f"  ⚠️ [{topic}] Progress check: verify_rate={verify_pass_rate:.2f} < expected={expected_min:.2f} "
                      f"after {direction.cycles_completed} cycles — flagging for review")
            direction.status = "paused"
            direction._circuit_state = "OPEN"
            _prev_cooldowns = getattr(direction, '_cooldown_count', 0)
            direction._cooldown_count = _prev_cooldowns + 1
            direction.fail_cooldown_until = time.time() + 600 * (2 ** min(_prev_cooldowns, 4))
            self._dir_mgr.save()
            return True
        if direction.cycles_completed > 10 and direction.saturation < 0.3:
            # Cost-benefit check before termination
            _cost_invested = direction.cycles_completed
            _cost_remaining = max(1, total_tasks - len(done_tasks)) * 3  # estimate 3 cycles per remaining task
            if _cost_invested > _cost_remaining:
                self._log(f"  ⚠️ [{topic}] Cost check: invested {_cost_invested}cyc > remaining ~{_cost_remaining}cyc, terminating")
                direction.status = "completed"
                self._dir_mgr.save()
            else:
                self._log(f"  ⚠️ [{topic}] Cost check: invested {_cost_invested}cyc <= remaining ~{_cost_remaining}cyc, continuing")
        self._log(f"  📊 [{topic}] saturation={direction.saturation:.2f} "
                  f"(verify_rate={verify_pass_rate:.2f}*0.6 + task_rate={task_complete_rate:.2f}*0.4, "
                  f"conf_avg={conf_avg:.2f}, entries={direction.entries_created}/5)")
        if direction.reflect_no_produce >= 3 or direction.saturation >= 0.9 or direction.entries_created >= 5:
            direction.status = "completed"
            self._log(f"  ✅ [{topic}] Task completed, saturation={direction.saturation:.2f}")
            submit_verification_task(topic, is_review=False, directions=self._directions,
                                    save_config_fn=self._dir_mgr.save, log_func=self._log)
            schedule_review(topic, directions=self._directions,
                           save_config_fn=self._dir_mgr.save, log_func=self._log)
            # Notify planner
            try:
                from aelvoxim.planner import LongTermPlanner
                _ltp = LongTermPlanner()
                _st = {"directions": {topic: {"status": "completed",
                                             "entries_created": direction.entries_created,
                                             "source_plan": direction.source_plan,
                                             "source_milestone": direction.source_milestone}}}
                _ltp.update_from_learner(_st)
            except Exception:
                _log.exception("loop error")
        return True

    def _report_learning_quality(self, topic: str, direction: LearningDirection) -> None:
        """Report learning quality statistics. Not used in core loop."""
        try:
            entries = list(KnowledgeBase.search(query=topic, limit=30))
            total = len(entries)
            if total == 0:
                return
            verified_high = sum(1 for e in entries if e.get("confidence", 0) >= 0.7)
        except Exception:
            _log.exception("loop error")

    # ── Cognition tick (delegated to sub-modules) ──

    def _cognition_tick(self) -> None:
        """Run meta-cognition checks and self-improvement actions.

        Orchestrates memory cleanup, auto-tune, reflection, goals, reports.
        """
        t0 = time.time()
        try:
            from ..core.metacog_monitor import MetaCogMonitor
            from ..core.metacog import MetaCogTrigger
            from ..core.selfmodel import SelfModel, CapabilityScore
            from ..core.dgmh import DGOrchestrator as _DGOrch
            # Optional: patches module may not exist in all editions
            _cached_sm = None
            try:
                from ..patches.learner_cache import get_selfmodel as _cached_sm
            except (ImportError, ModuleNotFoundError):
                pass  # optional: enterprise only

            sm = _cached_sm() if _cached_sm else SelfModel()
            # Persistent MetaCogMonitor: keeps tick history across cognition cycles
            mon = getattr(self, '_mon', None)
            if mon is None:
                mon = MetaCogMonitor()
                self._mon = mon
            learner_stats = {
                "total_cycles": sum(d.cycles_completed for d in self._directions.values()),
                "active_directions": sum(1 for d in self._directions.values() if d.status == "active"),
                "total_entries": sum(d.entries_created for d in self._directions.values()),
            }
            belief_stats = {"count": 0, "high_confidence": 0, "low_confidence": 0, "total_evidence": 0}

            # Gap analysis → auto-create plans
            try:
                from ..server.edition import get as _ed_get_gap
                if _ed_get_gap("gap_analysis_enabled", False):
                    from ..learn.gap_analysis import analyze_knowledge_gaps
                _kb_topics = {}
                try:
                    from ..learn.knowledge import KnowledgeBase
                    for e in KnowledgeBase.get_all_active():
                        t = e.get("topic", "")
                        if t:
                            _kb_topics[t] = _kb_topics.get(t, 0) + 1
                except Exception:
                    _log.exception("loop error")
                _gap_result = analyze_knowledge_gaps(
                    self._directions, _kb_topics, [],
                    min_entries_threshold=5, saturation_threshold=0.8,
                )
                if _gap_result.get("recommendations"):
                    for rec in _gap_result["recommendations"][:2]:
                        self._log(f"  🧩 Gap: {rec[:80]}")
                    # Auto-create plan for top recommendation
                    recs = _gap_result["recommendations"]
                    if recs and len(self._directions) < 10:
                        _goal = recs[0].replace("Create direction from user demand: ", "").replace("Re-activate direction: ", "")
                        from aelvoxim.planner import LongTermPlanner
                        _ltp = LongTermPlanner()
                        if _goal and not any(d.topic == _goal for d in self._directions.values()):
                            _plan = _ltp.create_plan(_goal, source="gap_analysis")
                            self._log(f"  🗺️ Plan created: {_plan.id} -> {_goal[:50]}")
            except Exception:
                _log.exception("loop error")
            if hasattr(sm, '_capabilities'):
                bc = sm._capabilities.get("belief_health", None)
                if bc:
                    belief_stats = {
                        "count": bc.task_count,
                        "high_confidence": int(bc.success_rate * bc.task_count),
                        "low_confidence": int((1 - bc.success_rate) * bc.task_count),
                        "total_evidence": bc.alpha + bc.beta,
                    }
            # Run MetaCogTrigger — 8-signal degradation analysis
            try:
                _trigger = MetaCogTrigger(self_model=sm)
                # Compute real metrics from learner state
                _total_cycles = sum(d.cycles_completed for d in self._directions.values())
                _total_entries = sum(d.entries_created for d in self._directions.values())
                _overall_success = _total_entries / max(_total_cycles, 1)
                # Days since last direction update (stagnation estimate)
                _now_ts = time.time()
                _max_age_days = 0
                for d in self._directions.values():
                    _updated = getattr(d, "updated_at", 0) or 0
                    if _updated:
                        try:
                            _parsed = datetime.strptime(str(_updated)[:10], "%Y-%m-%d") if isinstance(_updated, str) else datetime.fromtimestamp(float(_updated))
                            _days = (_now_ts - _parsed.timestamp()) / 86400
                            _max_age_days = max(_max_age_days, _days)
                        except Exception:
                            _log.exception("loop error")
                # Repeat failure count from log analysis
                _repeat_fail = 0
                try:
                    from ..hooks.analyzer import analyze as _hook_analyze
                    _a_report = _hook_analyze(lookback=5)
                    _repeat_fail = _a_report.failure_count if hasattr(_a_report, 'failure_count') else 0
                except Exception:
                    _log.exception("loop error")
                # Also check SelfModel for failure tracking
                try:
                    _learner_cap = sm._capabilities.get("learner")
                    if _learner_cap and _learner_cap.task_count > 0:
                        _failures = _learner_cap.beta  # Beta distribution's failure count
                        if _failures > _repeat_fail:
                            _repeat_fail = _failures
                except Exception:
                    _log.exception("loop error")
                # ── Per-timeframe success rate tracking ──
                _tick_snapshots = getattr(self, '_tick_snapshots', [])
                _tick_snapshots.append((time.time(), _total_entries, _total_cycles))
                if len(_tick_snapshots) > 100:
                    _tick_snapshots = _tick_snapshots[-100:]
                self._tick_snapshots = _tick_snapshots
                _now = time.time()

                def _window_rate(window_hours: int) -> float:
                    """Compute success rate over last N hours from snapshots."""
                    cutoff = _now - window_hours * 3600
                    recent = [(e, c) for ts, e, c in _tick_snapshots if ts >= cutoff]
                    if not recent or len(recent) < 2:
                        return _overall_success  # fall back to lifetime
                    e0, c0 = recent[0]
                    e1, c1 = recent[-1]
                    de = e1 - e0
                    dc = c1 - c0
                    return de / max(dc, 1)

                _sr_1d = _window_rate(24)
                _sr_3d = _window_rate(72)
                _sr_7d = _window_rate(168)

                _mc_report = _trigger.evaluate(
                    success_rate_7d=_sr_7d,
                    success_rate_3d=_sr_3d,
                    success_rate_1d=_sr_1d,
                    days_since_last_improvement=int(_max_age_days),
                    repeat_error_count=_repeat_fail,
                    external_signal_strength=0.0,
                    belief_health=belief_stats,
                )
                # Debug: log trigger stats
                _tr_all = len(getattr(_mc_report, 'triggers', []))
                _tr_hit = sum(1 for t in getattr(_mc_report, 'triggers', []) if getattr(t, 'triggered', False))
                if _tr_hit > 0:
                    _names = [getattr(t, 'signal_name', '?') for t in getattr(_mc_report, 'triggers', []) if getattr(t, 'triggered', False)]
                    self._log(f"  🔔 MetaCogTrigger: {_tr_hit}/{_tr_all} signals triggered — {_names}")
                # Sync SelfModel capabilities from actual BeliefPool data
                try:
                    sm.update_capabilities_from_history(
                        [r.to_dict() for r in getattr(_trigger, '_history', [])]
                    )
                except Exception:
                    _log.exception("loop error")
            except Exception:
                # Diagnose the silent metacog chain break (2026-08-24): the
                # evaluate() call above stopped persisting metacog_history and
                # Cognition degraded to triggers=1/1; log the real exception
                # instead of swallowing it.
                _log.exception("loop error (metacog trigger chain)")
                _mc_report = None
            # ── Memory maintenance (every cognition tick) ──
            try:
                from aelvoxim.memory.decay import batch_decay
                _mcm = getattr(self, '_mem_tick', 0) + 1
                self._mem_tick = _mcm
                if _mcm % 5 == 0:
                    from aelvoxim.memory import _fusion as _mem_fusion
                    _result = batch_decay(_mem_fusion)
                    if _result.get('decayed', 0) > 0 or _result.get('archived', 0) > 0 or _result.get('dormant', 0) > 0:
                        self._log(f"  🧹 Memory maintenance: {_result}")
            except Exception:
                _log.exception("loop error")

            # ── Unknown discovery (every tick) ──
            try:
                from aelvoxim.learn.unknown_discovery import scan_unknowns, pop_unknown_candidates
                if scan_unknowns(self._directions, self._log):
                    self._log("  🔍 UnknownDiscovery: new candidate(s) queued")
                # Consume candidates: try to add as directions (respects hard limit of 22)
                _ud_topic = pop_unknown_candidates(max_count=1)
                if _ud_topic:
                    _candidate = _ud_topic[0][:180] if isinstance(_ud_topic[0], str) else str(_ud_topic[0])[:180]
                    if self.add_direction(_candidate):
                        self._log(f"  🧠 [UnknownDiscovery] Started learning: {_candidate}")
                    else:
                        self._log(f"  ⏭️ [UnknownDiscovery] Failed to add: {_candidate}")
            except Exception:
                _log.exception("loop error")

            report = mon.evaluate(
                learner_stats=learner_stats,
                belief_stats=belief_stats,
                metacog_report=_mc_report,
            )

            # ── Memory consolidation (every 10 cognition ticks) ──
            try:
                _mct = getattr(self, '_consolidation_tick', 0) + 1
                self._consolidation_tick = _mct
                if _mct % 10 == 0:
                    from ..memory.consolidator import run_consolidation
                    _cr = run_consolidation()
                    if _cr.get("merged_count", 0) > 0 or _cr.get("groups_found", 0) > 0:
                        self._log(f"  🧩 Consolidation: merged {_cr['merged_count']} from {_cr['groups_found']} groups")
            except Exception:
                _log.exception("loop error")

            # 2b. Feed learner results back into SelfModel
            try:
                sm.inject_learner_stats(learner_stats, belief_stats)
            except Exception:
                _log.exception("loop error")

            # ── Meta-review: self-audit of metacog logs (every 10 ticks) ──
            try:
                _mrv = getattr(self, '_meta_review_tick', 0) + 1
                self._meta_review_tick = _mrv
                if _mrv % 10 == 0:
                    from ..learn.meta_reviewer import MetaReviewer
                    _reviewer = MetaReviewer()
                    _result = _reviewer.review()
                    if _result and _result.get("suggestions"):
                        self._log(f"  🧪 Meta-review: {len(_result['suggestions'])} suggestion(s), "
                                  f"analyzed {_result['reports_analyzed']} reports")
            except Exception:
                _log.exception("loop error")

            # ── Bayesian belief update for all directions with outcomes ──
            try:
                from ..core.belief import BeliefPool
                _bp = BeliefPool()
                for _t, _d in getattr(self, '_directions', {}).items():
                    if _d.cycles_completed > 0:
                        _bp.record_learner_cycle(_t, _d.entries_created, _d.cycles_completed)
            except Exception:
                _log.exception("loop error")

            # ── Determine current focus from active goals ──
            _current_focus = "balanced"
            _current_skip = set()
            for _g in getattr(self, "_active_goals", []):
                if _g.status == "active":
                    _current_focus = _g.focus
                    _current_skip.update(_g.skip_actions)
                    break

            # 3. Memory layer cleanup (focus-sensitive intensity)
            if _current_focus == "cleanup":
                _memory_cleanup()
                try:
                    _cleaned = KnowledgeBase.cleanup_low_value_knowledge(max_age_days=3, min_access=0)
                    if _cleaned:
                        self._log(f"  🧹 [Focus:cleanup] Cleaned {_cleaned} low-value entries")
                except Exception:
                    _log.exception("loop error")
            else:
                _memory_cleanup()
            # KB quality cleanup: archive low-confidence entries (every 60 cycles ≈ 30min)
            if getattr(self, '_kb_cleanup_tick', 0) % 60 == 0:
                _kb_cleanup(self._log)
            self._kb_cleanup_tick = getattr(self, '_kb_cleanup_tick', 0) + 1

            # 4. Auto-tune + reflection (skippable via focus)
            if "auto_tune" not in _current_skip and report.get("score", 0) > 0:
                if report.get("should_evolve", False):
                    from ..core.calibration import Calibration
                    cal = Calibration()
                    _auto_data = {
                        "hit_rate": report.get("score", 0.5),
                        "judge_error_rate": 0.0,
                        "by_type": {},
                        "total_proposals": 0,
                        "total_applied": 0,
                        "failure_report": {
                            "top_patterns": [],
                            "failure_rate": 0.0,
                        },
                    }
                    changes = cal.auto_tune(_auto_data)
                    if changes:
                        # DGM-H Gate: check each change through SafetyShield
                        _dgmh = _DGOrch()
                        _shielded_changes = []
                        for _c in changes[:5]:
                            _target = _c.get("target", "")
                            _judge = _c.get("judge_grade", "B")
                            _ok, _reason = _dgmh.check_proposal_gate("update", _judge)
                            if not _ok:
                                self._log(f"  🛡️ DGM-H blocked: {_target} ({_reason})")
                                continue
                            _sr = _dgmh.shield.check(action="modify", target=_target)
                            if _sr:
                                self._log(f"  🛡️ SafetyShield blocked: {_target} ({_sr})")
                                continue
                            _dgmh.shield.count_modify()
                            _shielded_changes.append(_c)
                        if _shielded_changes:
                            self._log(f"  🧠 Cognition: auto-tuned {len(_shielded_changes)} params (DGM-H gated)")
                            # Start tracking effectiveness
                            try:
                                from ..hooks.tracker import start_tracking as _st
                                _tid = _st({"changes": _shielded_changes, "trigger": "cognition_tick"})
                                self._log(f"  📊 Tracker started: {_tid}")
                            except Exception:
                                _log.exception("loop error")
                        for c in _shielded_changes[:3]:
                            self._log(f"    {c.get('target','?')}: {c.get('old','?')} -> {c.get('new','?')} ({c.get('reason','')})")
                _analysis = analyze_with_hypotheses(report, self._directions, self._log, learner_ref=self)
                if _analysis:
                    execute_reflection(_analysis, self._directions, self._dir_mgr.save, self._log, self)

            # 4b. Direction-level auto-tune (every cognition tick)
            try:
                from ..learn.autotune import tune as _autotune
                _at_changes = _autotune(self)
                if _at_changes:
                    self._log(f"  🔧 Direction auto-tune: {len(_at_changes)} changes")
                    for _c in _at_changes[:3]:
                        self._log(f"    {_c.get('target','?')}: {_c.get('action','?')} ({_c.get('reason','')})")
            except Exception:
                _log.exception("loop error")

            # 5a. Active search and learn (skippable via focus)
            if "search" not in _current_skip:
                try:
                    self._search_rr = _goals_search_and_learn(self._directions, self._log, self._search_rr)
                except Exception:
                    _log.exception("loop error")
            else:
                self._log(f"  ⏸️ [Focus:{_current_focus}] Search paused")

            # 5b. Consume suggested actions (filtered by focus skip list)
            for action in report.get("suggested_actions", []):
                if action in _current_skip:
                    continue
                if action == "switch_search_engine":
                    current = os.environ.get("AELVOXIM_SEARCH_ENGINE", "bing_cn")
                    engines = ["bing_cn", "duckduckgo", "so"]
                    if current in engines:
                        idx = engines.index(current)
                        new_engine = engines[(idx + 1) % len(engines)]
                        os.environ["AELVOXIM_SEARCH_ENGINE"] = new_engine
                        self._log(f"  🔄 Cognition: switched search engine {current} → {new_engine}")
                elif action == "cleanup_low_confidence_kb":
                    if _current_focus == "cleanup":
                        _days = 3
                    else:
                        _days = 7
                    try:
                        cleaned = KnowledgeBase.cleanup_low_value_knowledge(max_age_days=_days, min_access=1)
                        if cleaned:
                            self._log(f"  🧹 Cognition: cleaned up {cleaned} low-value entries")
                    except Exception:
                        _log.exception("loop error")

            # 6. Verify last repair
            try:
                _repair_result = verify_repair(self)
                if _repair_result:
                    update_selfmodel_from_repair(_repair_result)
                    self._log(f"  🔄 Repair verification: {_repair_result['status']} — {_repair_result.get('detail', '')}")
            except Exception:
                _log.exception("loop error")

            # 7. Progress goals
            try:
                self._active_goals = _goals_progress(self._active_goals, self._log)
            except Exception:
                _log.exception("loop error")

            # 8. Set new goals (every 30 cycles)
            try:
                self._cognition_cycle_count += 1
                if self._cognition_cycle_count % 30 == 0:
                    self._active_goals = _goals_set_active(self._active_goals, self._log)
            except Exception:
                _log.exception("loop error")

            # 9. Review scheduler (every 10 cycles)
            try:
                if getattr(self, '_review_tick', 0) % 10 == 0:
                    from ..learn.review_scheduler import run_review_cycle
                    _rr = run_review_cycle(log_func=self._log)
                self._review_tick = getattr(self, '_review_tick', 0) + 1
            except Exception:
                _log.exception("loop error")

            # 9. Daily brain report
            try:
                _update_daily_report(report, self._dir_mgr, self)
            except Exception:
                _log.exception("loop error")

            # 10. Curiosity-driven autonomous learning (every 15 cycles)
            try:
                if getattr(self, '_curiosity_tick', 0) % 15 == 0:
                    from ..server.service_chat import _pop_curiosity_topic, _get_recently_learned_topics
                    _topic = _pop_curiosity_topic()
                    if _topic:
                        self._log(f"  🔍 Curiosity: Auto-learning about '{_topic}'")
                        try:
                            from ..learn.search import search as _search
                            _results = _search(_topic, max_results=5)
                            if _results:
                                from ..learn.knowledge import KnowledgeBase
                                _snippets = [
                                    (r.get("snippet") or r.get("content") or "")[:200]
                                    for r in _results[:3]
                                ]
                                KnowledgeBase.store_pending(
                                    topic=_topic[:80],
                                    title=f"主动学习: {_topic[:40]}",
                                    content="\n".join(_snippets),
                                    source="curiosity",
                                )
                                self._log(f"  🔍 Curiosity: Learned '{_topic}' ({len(_results)} results)")
                        except Exception:
                            self._log(f"  ⚠️ Curiosity: Search failed for '{_topic}'")
                self._curiosity_tick = getattr(self, '_curiosity_tick', 0) + 1
            except Exception:
                _log.exception("loop error")

            # Update running average
            elapsed = time.time() - t0
            self._cognition_time = self._cognition_time * 0.7 + elapsed * 0.3
            _trig_list = report.get("triggers", [])
            _trig_count = sum(1 for t in _trig_list if getattr(t, "triggered", False))
            _sug_actions = report.get("suggested_actions", [])
            if _sug_actions or report.get("score", 0) > 0.1 or _trig_count > 0:
                self._log(f"  🧠 Cognition: score={report.get('score', 0):.3f} "
                          f"actions={len(_sug_actions)} "
                          f"triggers={_trig_count}/{len(_trig_list)} "
                          f"belief={belief_stats.get('count',0)}/{belief_stats.get('total_evidence',0)}")
        except Exception as e:
            import traceback as _tb
            self._log(f"  ⚠️ Cognition tick error: {e}")
            self._log("  Traceback: " + _tb.format_exc()[:300].replace("\n", " | "))

    # ── Main loop ──

    def _main_loop(self):
        """Main learning loop. Runs in a background thread."""
        self._dir_mgr.load()
        self._log("🔄 Learning loop started")
        self._last_heartbeat = time.time()
        self._loop_count = 0
        while self._running:
            try:
                self._last_heartbeat = time.time()
                # Check PG availability — warn but don't block (JSON fallback works)
                try:
                    from ..storage.db import use_pg as _pg_ok
                    _pg_available = _pg_ok()
                    if not _pg_available:
                        try:
                            from ..storage.db import get_pool as _get_pool
                            _pg_available = _get_pool() is not None
                        except Exception:
                            _pg_available = False
                except Exception:
                    _pg_available = False

                # Refresh LLM + search status
                self._detect_llm_status()

                any_active = False
                # Priority ordering: near-saturation first, suppress brand-new starts
                _SAT_NEAR = 0.65  # saturation threshold for "near graduation"
                _SAT_LOW = 0.30   # below this = just started
                _now_ts = time.time()

                def _priority(item):
                    _, d = item
                    sat = getattr(d, 'saturation', 0.0) or 0.0
                    entries = d.entries_created
                    cycles = d.cycles_completed or 1
                    # Efficiency: higher = better signal
                    efficiency = entries / max(cycles, 1)
                    # Distance from near-saturation sweet spot (0.65-0.80)
                    # Peaked reward around 0.72, steep falloff below 0.4
                    if sat >= _SAT_NEAR:
                        # Nearly done — highest priority, push to graduation
                        return (0, -sat, -efficiency)
                    elif sat <= _SAT_LOW and entries <= 3:
                        # Brand new — lowest priority, suppress starting
                        return (2, sat, cycles)
                    else:
                        # Mid-learning — moderate priority
                        return (1, -efficiency, -entries)

                _sorted = sorted(
                    self._directions.items(),
                    key=_priority,
                )
                for topic, direction in _sorted:
                    if not self._running:
                        break
                    if direction.status != "active":
                        # HALF_OPEN circuit breaker: test paused directions after cooldown
                        if direction.status == "paused" and getattr(direction, '_circuit_state', '') == "OPEN":
                            _cooldown = getattr(direction, "fail_cooldown_until", 0)
                            if _cooldown <= time.time():
                                direction._circuit_state = "HALF_OPEN"
                                direction.status = "active"
                                self._dir_mgr.save()
                                self._log(f'  🔓 [{topic}] HALF_OPEN — testing with lightweight task')
                        continue
                    # Cooldown check: skip if within cooldown
                    _cooldown = getattr(direction, "fail_cooldown_until", 0)
                    if _cooldown > time.time():
                        continue
                    # Weak pass backpressure: ≥3 consecutive → skip 2 rounds
                    _wps = getattr(direction, "weak_pass_streak", 0)
                    if _wps >= 3 and _wps % 2 != 0:
                        continue
                    # Rate limit: respect _RATE_LIMIT_MIN_INTERVAL per direction
                    _last_tick = getattr(direction, '_last_tick_time', 0)
                    _since_last = time.time() - _last_tick
                    if _since_last < _RATE_LIMIT_MIN_INTERVAL:
                        self._sleep(min(_RATE_LIMIT_MIN_INTERVAL - _since_last, 10))
                        continue
                    # Rate limit: respect _RATE_LIMIT_MAX per 24h per direction
                    _cycles_today = getattr(direction, 'cycles_completed', 0)
                    _started = getattr(direction, 'started_at', '')
                    if _started:
                        try:
                            from datetime import datetime as _dt
                            _start_dt = _dt.strptime(_started, '%Y-%m-%d %H:%M:%S')
                            _hours_alive = max(1, (time.time() - _start_dt.timestamp()) / 3600)
                            if _cycles_today / _hours_alive > _RATE_LIMIT_MAX / 24:
                                self._sleep(10)
                                continue
                        except (ValueError, TypeError):
                            pass
                    any_active = True

                    # LLM degraded AND search mock → teach mode
                    if self._llm_status != "available" and self._search_mock:
                        if self._teach_one_cycle(direction):
                            self._last_heartbeat = time.time()
                            direction._last_tick_time = time.time()
                            self._sleep(60)
                            continue
                    else:
                        if self._learn_one_cycle(direction):
                            self._last_heartbeat = time.time()
                            direction._last_tick_time = time.time()
                            self._sleep(60)
                            continue
                    self._sleep(30)
                    self._last_heartbeat = time.time()

                # ── Active health scan (every 30 min) ──
                try:
                    from ..learn.active_scan import should_scan as _should, run_scan as _run_scan
                    if _should():
                        _report = _run_scan(log_fn=self._log)
                        if _report.get("knowledge", {}).get("total", 0) > 0:
                            _kb = _report["knowledge"]
                            self._log(f"  📊 Health: {_kb['total']} entries, avg conf {_kb.get('avg_confidence', 0):.2f}, {_report['directions'].get('active', 0)} active directions")
                        self._last_heartbeat = time.time()
                except Exception:
                    _log.exception("loop error")

                # ── Periodic zero-score cleanup (every 30 min) ──
                if self._loop_count % 60 == 0:
                    try:
                        from ..learn.knowledge import cleanup_low_value_knowledge
                        cleanup_low_value_knowledge()
                    except Exception:
                        pass

                # ── Stale direction cleanup ──
                if len(self._directions) >= 12:
                    stale = [t for t, d in self._directions.items()
                             if d.status in ('paused', 'pending') and
                             getattr(d, 'cycles_completed', 0) == 0 and
                             getattr(d, 'entries_created', 0) == 0 and
                             getattr(d, 'fail_streak', 0) == 0]
                    for t in stale[:max(0, len(self._directions) - 10)]:
                        # Passive (capacity) cleanup → soft ban (15 min): core tech
                        # domains should not be repeatedly hard-blocked.
                        self._dir_mgr.mark_cleanup_removed(t, cooldown=900)
                        self._directions.pop(t, None)
                        self._log(f'  🗑️ Removed stale direction (cap) + soft-blacklisted (15min): {t}')
                    if stale:
                        self._dir_mgr.save()
                        self._log(f'  💾 Direction config saved after cleanup')

                # Check reviews
                if check_reviews(self._directions, self._dir_mgr.save, self._log):
                    continue

                # Meta-learning: process accumulated feedback signals from chat
                # (tick was previously never called, so the feedback queue
                # grew forever — B15, 9.txt audit).
                try:
                    from ..learn.meta_learner import MetaLearner
                    _acts = MetaLearner().tick()
                    for _a in _acts:
                        self._log(f"  📈 {_a}")
                except Exception:
                    _log.exception("loop error")

                # Check pending promotions
                pmt_state = {
                    "_pending_streak": getattr(self, '_pending_streak', 0),
                    "_last_pending_eid": getattr(self, '_last_pending_eid', ""),
                }
                if check_pending_promotions(self._directions, self._dir_mgr.save, self._log, pmt_state):
                    setattr(self, '_pending_streak', pmt_state.get('_pending_streak', 0))
                    setattr(self, '_last_pending_eid', pmt_state.get('_last_pending_eid', ""))
                    continue

                # ── Stale direction cleanup ──
                if len(self._directions) >= 12:
                    stale = [t for t, d in self._directions.items()
                             if d.status in ('paused', 'pending') and
                             getattr(d, 'cycles_completed', 0) == 0 and
                             getattr(d, 'entries_created', 0) == 0]
                    # Paused directions stuck with cycles but zero entries
                    stuck_paused = [t for t, d in self._directions.items()
                                    if d.status == 'paused' and
                                    getattr(d, 'cycles_completed', 0) >= 5 and
                                    getattr(d, 'entries_created', 0) == 0]
                    for t in stale[:max(0, len(self._directions) - 10)]:
                        # Passive (capacity) cleanup → soft ban (15 min): core tech
                        # domains should not be repeatedly hard-blocked.
                        self._dir_mgr.mark_cleanup_removed(t, cooldown=900)
                        self._directions.pop(t, None)
                        self._log(f'  🗑️ Removed stale direction (cap) + soft-blacklisted (15min): {t}')
                    for t in stuck_paused[:max(0, len(self._directions) - 10)]:
                        # Active (failure-driven) cleanup → hard ban (1h): strict.
                        self._dir_mgr.mark_cleanup_removed(t)
                        self._directions.pop(t, None)
                        self._log(f'  🗑️ Removed stuck paused direction + blacklisted (1h): {t}')
                    if stale or stuck_paused:
                        self._dir_mgr.save()
                        self._log(f'  💾 Direction config saved after cleanup')

                # ── Archive high-saturation completed directions ──
                if len(self._directions) >= 12:
                    _high_sat = [(t, d) for t, d in self._directions.items()
                                 if d.status == 'completed' and d.started_at and
                                 d.saturation >= 0.75 and
                                 getattr(d, 'current_task', '') == '']
                    if _high_sat:
                        _to_archive = _high_sat[:max(1, len(self._directions) - 10)]
                        for t, d in _to_archive:
                            d.status = 'archived'  # keep record, prevent re-creation
                            self._dir_mgr._creation_lock[t.lower()] = time.time()  # 1h cooldown on re-creation
                        self._dir_mgr.save()
                        self._log(f'  🗂️ Archived {len(_to_archive)} high-sat completed directions')

                # No active directions — review mode, curiosity, or auto-discover
                if not any_active:

                    # Curiosity engine: pick next topic from seeds or derive
                    try:
                        from ..learn.curiosity import activate_curiosity as _curiosity
                        if _curiosity(self._directions, self.add_direction, self._log):
                            self._sleep(5)
                            continue
                    except Exception:
                        _log.exception("loop error")

                    try:
                        if self._review_mode():
                            self._sleep(5)
                            continue
                    except Exception:
                        _log.exception("loop error")

                    if len(self._directions) < 20:
                        try:
                            if self._monitor:
                                fixes = self._monitor.tick()
                                for f in fixes:
                                    self._log('🩺 Self-heal: {}'.format(f))
                        except Exception:
                            _log.exception("loop error")

                    # Auto-discovery: only when active count is below the max threshold
                    _active_cnt = sum(1 for d in self._directions.values() if d.status == "active")
                    if _active_cnt >= _MAX_ACTIVE_DIRECTIONS:
                        if self._loop_count % 10 == 0:
                            self._log(f"  ⏸️ Auto-discovery paused: {_active_cnt} active >= {_MAX_ACTIVE_DIRECTIONS} max")
                    else:
                        if self._enable_auto_discover:
                            found, new_ts = _auto_add(
                                self._directions, self._last_auto_discover, self._log,
                                self.add_direction, KnowledgeBase.get_all_active,
                                suggest_directions_from_knowledge, _search,
                            )
                            self._last_auto_discover = new_ts
                            if found:
                                self._log("🔄 Auto-discovered new direction")
                                self._sleep(5)
                                continue

                        self._last_discovery = _try_discover(
                            self._directions, self._last_discovery, self._log,
                            suggest_directions_from_knowledge, self.add_direction,
                        )

                    # Post-validation audit (every 60 min, Pro feature)
                    try:
                        from ..server.edition import get as _ed_get_pv
                        if _ed_get_pv("auto_post_validation", False):
                            _now_pv = time.time()
                            if not hasattr(self, '_last_post_audit') or _now_pv - self._last_post_audit > 3600:
                                self._last_post_audit = _now_pv
                                from ..learn.post_validation import PostValidationEngine
                                _pve = PostValidationEngine(log_func=self._log)
                                _preport = _pve.run_audit(max_entries=30, max_issues=10)
                                if _preport and _preport.total_flagged > 0:
                                    self._log("  🔍 Post-audit: {}".format(_preport.summary))
                                    for _fi in _preport.issues[:3]:
                                        self._log("    [{}] {}: {} — {}".format(
                                            _fi.severity, _fi.dimension, _fi.entry_title[:30], _fi.detail[:50]))
                    except Exception:
                        _log.exception("loop error")

                # ── Loop summary (every iteration) ──
                self._loop_count += 1
                if self._loop_count % 5 == 0:
                    _active = sum(1 for d in self._directions.values() if d.status == "active")
                    _completed = sum(1 for d in self._directions.values() if d.status == "completed")
                    _paused = sum(1 for d in self._directions.values() if d.status == "paused")
                    self._log(f"  📊 Loop#{self._loop_count}: {_active}active/{_completed}completed/{_paused}paused llm={self._llm_status}")

                # Heartbeat: refresh status.json every round so monitors and
                # the admin panel see a live learner (previously only written
                # on start/stop → appeared dead while running).
                try:
                    self._save_status()
                except Exception:
                    pass

                # ── Round rest: pause before the next full pass so the
                # background loop stays out of the model channel for most of
                # the time (chat requests run in a separate process anyway).
                self._sleep(_LOOP_ROUND_SLEEP)

            except Exception as e:
                self._log(f"  ⚠️ Main loop error: {e}")
                self._sleep(30)

    def _sleep(self, seconds: int):
        """Sleep with interrupt support (watchdog or stop).

        Long sleeps are chunked so _last_heartbeat stays fresh — the
        watchdog threshold (900s) must never fire during a legitimate
        round rest (600s) or direction sleep (60s).
        """
        _remaining = seconds
        while _remaining > 0 and self._running:
            _chunk = min(_remaining, 60)
            self._watchdog_event.wait(timeout=_chunk)
            self._watchdog_event.clear()
            self._last_heartbeat = time.time()
            _remaining -= _chunk
        # Re-check running flag after sleep
        if not self._running:
            raise SystemExit("Stopped")

    # ── Start / Stop ──

    def start(self):
        with self._start_lock:
            if self._running and self._thread and self._thread.is_alive():
                self._log("⚠️ Already running")
                return
            self._dir_mgr.load()
            if not self._directions:
                self._log("⚠️ No directions, use add_direction() first")
                return

            # Edition gate: community edition does not run auto-learning loop
            try:
                from ..server.edition import get as _ed_get
                if not _ed_get("auto_learn", False):
                    self._log("  ℹ️ Community edition: auto-learning disabled (start Pro for 7×24 loop)")
                    return
            except ImportError:
                _log.exception("loop error")

            self._running = True
            self._thread = threading.Thread(target=self._main_loop_safe, daemon=True, name="learner-main")
            self._thread.start()
            self._log("🚀 Learning loop started in background thread")
            self._save_status()

            # Health daemon (single instance)
            try:
                self._health_alive = True
                if not self._health_thread or not self._health_thread.is_alive():
                    self._health_thread = threading.Thread(target=self._health_daemon, daemon=True)
                    self._health_thread.start()
            except Exception:
                _log.exception("loop error")

            # Watchdog
            self._start_watchdog()

    def _main_loop_safe(self):
        """Wrap main loop with exception boundary."""
        try:
            self._main_loop()
        except SystemExit:
            _log.exception("loop error")
        except Exception as e:
            self._log(f"🚨 Learning loop crashed: {e}")
            import traceback as _tb
            self._log("  Traceback: " + _tb.format_exc()[:500].replace("\n", " | "))
            self._running = False

    def _health_daemon(self):
        """Background health check: restart main loop if it dies (anti-flap backoff).

        Uses an independent _health_alive flag so a main-loop crash (which sets
        _running=False) does not kill the daemon that is supposed to revive it.
        """
        while self._health_alive:
            time.sleep(10)
            if not self._health_alive:
                break
            if self._thread and not self._thread.is_alive():
                _now = time.time()
                # Anti-flap: min 60s between respawns; 3 rapid deaths → 10min cooldown
                if _now - self._last_restart_ts < 60:
                    self._rapid_restarts += 1
                    if self._rapid_restarts >= 3:
                        self._log(f"🩺 Main loop died {self._rapid_restarts}x within 60s — 10min cooldown")
                        time.sleep(600)
                        self._rapid_restarts = 0
                    continue
                self._last_restart_ts = _now
                self._rapid_restarts = 0
                with self._restart_lock:
                    if self._thread and not self._thread.is_alive():
                        self._log("🩺 Main loop died, restarting...")
                        self._running = True
                        self._thread = threading.Thread(target=self._main_loop_safe, daemon=True)
                        self._thread.start()

    def _run_health_scan(self):
        """Run a health scan and log results."""
        from ..core.selfmodel import SelfModel
        sm = SelfModel()
        caps = sm._capabilities
        bc = caps.get("belief_health")
        health_str = f"belief={bc.success_rate:.2f}" if bc else "belief=N/A"
        self._log(f"  🔍 Health: {health_str}, directions={len(self._directions)}, running={self._running}")

    def _review_mode(self) -> bool:
        """Review mode when no active directions: verify pending entries."""
        try:
            pending = KnowledgeBase.get_pending()
            if pending:
                return True
        except Exception:
            _log.exception("loop error")
        return False

    def single_learn(self, topic: str) -> dict:
        """Run a single learning cycle for a specific direction (no background loop)."""
        if topic not in self._directions:
            self.add_direction(topic)
        direction = self._directions.get(topic)
        if not direction:
            return {"status": "error", "detail": "Direction not found"}
        self._learn_one_cycle(direction)
        return {"status": "ok", "direction": direction.topic}

    def stop(self):
        self._running = False
        self._health_alive = False
        if self._thread:
            self._watchdog_event.set()
        self._save_status()
        self._log("🛑 Learning loop stopped")

    def is_running(self) -> bool:
        return self._running

    def _start_watchdog(self):
        """Start watchdog thread to detect and report hangs."""
        def _watch():
            while self._running:
                time.sleep(30)
                if not self._running:
                    break
                # Threshold must exceed the longest legitimate quiet period:
                # round rest (600s) + direction sleep (60s) + slow LLM call.
                if time.time() - self._last_heartbeat > 900:
                    self._log("⚠️ Watchdog: no heartbeat for 900s")
                    self._last_heartbeat = time.time()
        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    # ── Persistence ──

    def _save_config(self):
        """Save direction config via DirectionManager."""
        self._dir_mgr.save()

    def _load_config(self):
        """Load direction config via DirectionManager."""
        self._dir_mgr.load()

    def _save_status(self):
        """Save learner status to JSON file."""
        status = {
            "running": self._running,
            "direction_count": len(self._directions),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2))
        except Exception:
            _log.exception("loop error")

    def get_status(self) -> dict:
        """Get learner status dict."""
        return {
            "running": self._running,
            "directions": {t: d.status for t, d in self._directions.items()},
            "direction_count": len(self._directions),
            "enable_auto_discover": self._enable_auto_discover,
            "last_heartbeat": self._last_heartbeat,
        }

    def get_logs(self, n: int = 50) -> List[str]:
        """Return last N log lines."""
        try:
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text().strip().split("\n")
                return lines[-n:]
        except Exception:
            _log.exception("loop error")
        return []
