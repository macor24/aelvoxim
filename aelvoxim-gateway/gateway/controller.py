# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.controller — Task execution controller.

Receives commands from Aelvoxim brain, executes multi-step operations,
handles retries, result collection, screenshot verification and
human-in-the-loop intervention.

Community edition: basic single-step + plan execution without retries.
Pro edition (stub): auto-retry, VLM screenshot verification.
Enterprise edition (stub): audit log, full intervention panel.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from .executor import execute as _exec_op

# ── Intervention state ──
_paused = False
_aborted = False
_confirm_next = False
_on_progress: Optional[Callable] = None
_on_screenshot: Optional[Callable] = None


# ── Intervention controls ──


def pause():
    global _paused
    _paused = True


def resume():
    global _paused
    _paused = False


def abort():
    global _aborted
    _aborted = True


def confirm_next(value: bool = True):
    global _confirm_next
    _confirm_next = value


def set_progress_callback(cb: Callable):
    global _on_progress
    _on_progress = cb


def set_screenshot_callback(cb: Callable):
    global _on_screenshot
    _on_screenshot = cb


def get_state() -> Dict[str, Any]:
    return {
        "paused": _paused,
        "aborted": _aborted,
        "confirm_next_step": _confirm_next,
    }


# ── Snapshot / Rollback support ──


def save_snapshot(label: str = "auto") -> str:
    """Save a state snapshot for rollback. Returns snapshot ID."""
    snap_id = f"snap_{int(time.time())}_{label}"
    # In future: save canvas JSON + screenshot
    snap_dir = Path("/tmp/aelvoxim_gateway/snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    info = {"id": snap_id, "label": label, "timestamp": time.time()}
    (snap_dir / f"{snap_id}.json").write_text(json.dumps(info))
    return snap_id


# ── Task Controller ──


class TaskController:
    """Execute multi-step task plans with optional retry and verification."""

    def __init__(self):
        self._history: List[Dict] = []
        self._max_retries = 3  # TODO[Pro]: Pro=3, Enterprise=5
        self._snapshots: List[str] = []

    # ── Plan execution ──

    def execute_plan(self, plan: List[Dict],
                     edition: str = "community",
                     auto_verify: bool = False) -> Dict[str, Any]:
        """Execute a multi-step plan.

        Community edition: no retry, no auto-verify.
        Pro edition (stub): auto-retry + VLM verify.

        Args:
            plan: List of operation dicts.
            edition: "community" | "pro" | "enterprise"
            auto_verify: If True, take screenshot after each step.

        Returns:
            {"success": bool, "results": [...], "failed_step": int,
             "snapshot_id": str, "intervention": bool}
        """
        global _paused, _aborted, _confirm_next
        _aborted = False
        _confirm_next = False

        results = []
        # Save pre-execution snapshot
        snap_id = save_snapshot("pre_plan")

        for i, step in enumerate(plan):
            # Check abort
            if _aborted:
                return {
                    "success": False,
                    "results": results,
                    "failed_step": i,
                    "aborted": True,
                    "snapshot_id": snap_id,
                }

            # Check pause
            while _paused and not _aborted:
                time.sleep(0.5)

            # Check confirmation (if enabled)
            if _confirm_next:
                # Wait for user to confirm
                _paused = True
                _confirm_next = False
                # User must call resume() + confirm_next(True) to continue
                while _paused and not _aborted:
                    time.sleep(0.5)

            # Progress callback
            if _on_progress:
                _on_progress({"step": i, "total": len(plan), "action": step.get("action", "")})

            # Execute step
            result = self.execute_step(step, i, edition)
            results.append(result)

            if not result.get("success"):
                return {
                    "success": False,
                    "results": results,
                    "failed_step": i,
                    "error": result.get("error", "Step failed"),
                    "snapshot_id": snap_id,
                }

            # Auto-verify (Pro feature stub)
            if auto_verify and edition != "community":
                verify = self._verify_step(step, result)
                if not verify.get("ok"):
                    return self._fail(results, i, verify.get("error", "Verification failed"), snap_id)

            time.sleep(0.3)

        return {"success": True, "results": results, "snapshot_id": snap_id}

    def execute_step(self, operation: Dict[str, Any],
                     step_index: int = 0,
                     edition: str = "community") -> Dict[str, Any]:
        """Execute a single step.

        Community edition: single attempt, no retry.
        Pro edition: retry up to 3 times with VLM fallback.
        """
        if edition == "community":
            result = _exec_op(operation)
            self._log_result(step_index, operation, result, 1)
            return result

        # Pro: retry logic with VLM fallback [TODO:Pro] — currently same as community
        # Future: capture screenshot after each attempt → VLM analyzer detects if operation landed correctly
        last_error = ""
        for attempt in range(self._max_retries):
            result = _exec_op(operation)
            if result.get("success"):
                self._log_result(step_index, operation, result, attempt + 1)
                return result
            last_error = result.get("error", "Unknown error")
            if attempt < self._max_retries - 1:
                time.sleep(1)
        return {"success": False, "error": last_error, "attempts": self._max_retries}

    def _verify_step(self, step: Dict, result: Dict) -> Dict:
        """Verify step result via screenshot comparison. [TODO:Pro] — currently returns stub."""
        return {"ok": True}

    def _log_result(self, step: int, op: Dict, result: Dict, attempts: int):
        self._history.append({
            "step": step,
            "action": op.get("action", ""),
            "target": op.get("target", ""),
            "result": result,
            "attempts": attempts,
            "timestamp": time.time(),
        })

    def _fail(self, results: List, step: int, error: str, snap_id: str) -> Dict:
        return {
            "success": False,
            "results": results,
            "failed_step": step,
            "error": error,
            "snapshot_id": snap_id,
        }

    # ── History ──

    def get_history(self, limit: int = 50) -> List[Dict]:
        return self._history[-limit:]

    def clear_history(self):
        self._history.clear()

    # ── Current canvas context (sent to Aelvoxim brain) ──

    def get_canvas_context(self) -> str:
        """Build a text summary of what's currently on screen."""
        from .context import scan_snapshots, normalize
        snapshots = scan_snapshots()
        if not snapshots:
            return "[Gateway] No software context available."
        parts = []
        for sw, snap in snapshots.items():
            parts.append(normalize(snap))
        return "\n\n".join(parts)
