"""
metacore.cortex.scheduler — Background scheduling thread for cortex (formerly 9703 Orchestrator).

Runs independently of request cycles. Every 5 minutes:
1. Reads LongTermPlanner's next action
2. Fetches Learner status
3. If planner has a pending milestone → submits task
4. Updates planner state

No HTTP calls — runs inside 9701 process, calls functions directly.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("aelvoxim.cortex.scheduler")

_TICK_INTERVAL = 900  # 15 minutes


def _submit_task(goal: str, task_type: str = "learn", plan_id: str = "", milestone_id: str = "") -> bool:
    """Submit a task to the MetaCore learner (internal call, no HTTP).

    Returns True if the task was accepted OR the direction already exists
    (both count as progress for planner milestone tracking).
    """
    from aelvoxim.api import submit_task
    from aelvoxim.learn.loop import get_learner

    try:
        task_id = submit_task(goal=goal, task_type=task_type,
                              plan_id=plan_id, milestone_id=milestone_id)
        if task_id:
            log.info("Dispatched %s task: %s", task_type, goal[:50])
            return True
        # If add_direction failed because direction already exists,
        # that's still progress — milestone should advance.
        learner = get_learner()
        if task_type == "learn" and goal in learner._directions:
            log.info("Direction already exists (milestone progress): %s", goal[:50])
            return True
        log.warning("Dispatch %s failed (no task_id): %s", task_type, goal[:50])
        return False
    except Exception as e:
        log.warning("Dispatch %s failed: %s: %s", task_type, goal[:50], e)
        return False


def _get_learner_status() -> dict:
    """Get learner status from internal state (same as GET /v1/status/planner)."""
    try:
        from aelvoxim.learn.loop import get_learner

        learner = get_learner()
        result = {
            "total_cycles": sum(
                d.cycles_completed for d in learner._directions.values()
            ) if hasattr(learner, "_directions") else 0,
            "active_directions": sum(
                1 for d in learner._directions.values() if d.status == "active"
            ) if hasattr(learner, "_directions") else 0,
            "total_entries": sum(
                d.entries_created for d in learner._directions.values()
            ) if hasattr(learner, "_directions") else 0,
            "last_heartbeat": getattr(learner, "_last_heartbeat", 0.0),
            "directions": {},
        }
        if hasattr(learner, "_directions"):
            for topic, d in learner._directions.items():
                result["directions"][topic] = {
                    "status": d.status,
                    "entries_created": d.entries_created,
                    "source_plan": getattr(d, "source_plan", ""),
                    "source_milestone": getattr(d, "source_milestone", ""),
                    "plan_ids": getattr(d, "plan_ids", []),
                }
        return result
    except Exception:
        return {}


class Scheduler:
    """Background scheduler that ticks planner and dispatches actions internally."""

    def __init__(self, planner=None):
        self._planner = planner  # LongTermPlanner instance (set externally)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running and self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cortex-scheduler")
        self._thread.start()
        log.info("Cortex scheduler started (tick every 5min)")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.warning("Scheduler error: %s", e)
            for _ in range(_TICK_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    def _tick(self):
        """One scheduler tick."""
        # 1. Get planner status
        if self._planner:
            action = self._planner.next_action()
            if action:
                log.info("Tick: dispatching action type=%s goal=%.50s ms=%s",
                         action.get("type","?"), action.get("goal","")[:50], action.get("milestone_id",""))
                self._dispatch(action)
                self._planner.mark_dispatched(action.get("id", ""))
            else:
                log.info("Tick: no pending action (all milestones dispatched or plans completed)")

        # 2. Refresh learner status — now includes directions dict for plan tracking
        status = _get_learner_status()
        if status and self._planner:
            self._planner.update_from_learner(status)
            log.info("Tick: learner status active=%d total=%d",
                     len([d for d in status.get("directions",{}).values() if d.get("status")=="active"]),
                     len(status.get("directions",{})))

    def _dispatch(self, action: dict):
        """Dispatch a planner action internally."""
        action_type = action.get("type", "")
        goal = action.get("goal", "")
        plan_id = action.get("plan_id", "")
        milestone_id = action.get("milestone_id", "")

        if action_type in ("learn", "search", "review") and goal:
            _submit_task(goal, task_type=action_type,
                         plan_id=plan_id, milestone_id=milestone_id)
