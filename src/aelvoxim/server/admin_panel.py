"""
aelvoxim.server.admin_panel — Cognitive dashboard backend.

Aggregates live status from Learner, SelfModel, MetaCogMonitor, and
expert orchestrator for the admin UI dashboard (tab: Cognitive Engine).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("aelvoxim.admin_panel")

# The HOMEDIR where persistent data lives
_HOMEDIR = Path(os.environ.get("AELVOXIM_HOME", Path.home() / ".aelvoxim"))


def get_cognitive_status() -> dict[str, Any]:
    """Aggregate learner, self-model, and metacognition status into one dict.

    Returns a dict with keys:
        learner      — direction overview, running state, cycles
        selfmodel    — capability scores per domain (5D), trends, grades
        metacog      — trigger log summary (last N events)
        health       — online/offline summary of subsystems
        edition      — current edition string
    """
    result: dict[str, Any] = {
        "learner": _learner_status(),
        "selfmodel": _selfmodel_status(),
        "metacog": _metacog_status(),
        "health": _subsystem_health(),
        "edition": _get_edition(),
    }
    return result


def _learner_status() -> dict[str, Any]:
    """Return learner loop + direction overview."""
    out: dict[str, Any] = {
        "running": False,
        "uptime_hours": 0,
        "total_directions": 0,
        "active_directions": 0,
        "completed_directions": 0,
        "paused_directions": 0,
        "total_cycles": 0,
        "total_entries": 0,
        "directions": [],
    }

    try:
        from aelvoxim.learn.learner import get_learner

        learner = get_learner()
        if learner is None:
            return out

        # Running state comes from the standalone learner process heartbeat
        # (status.json written by learn_worker). The in-process instance here
        # is a file-backed mirror and never runs its own loop, so
        # learner.is_running() is always False in the API process.
        try:
            import json as _json

            from aelvoxim.learn.loop import STATUS_FILE as _L_STATUS
            if _L_STATUS.exists():
                _st = _json.loads(_L_STATUS.read_text(encoding="utf-8"))
                out["running"] = bool(_st.get("running", False))
                _st_ts = _st.get("updated_at", "")
                if _st_ts:
                    try:
                        from datetime import datetime as _dt
                        _last = _dt.strptime(str(_st_ts), "%Y-%m-%d %H:%M:%S")
                        out["uptime_hours"] = round(max(0, (time.time() - _last.timestamp()) / 3600), 1)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            _log.exception("learner_status error")
        if not out["running"]:
            out["running"] = learner.is_running()

        # Compute uptime from first direction heartbeat, or current status
        directions = getattr(learner, "_directions", {}) or {}

        out["total_directions"] = len(directions)
        active_c = completed_c = paused_c = 0
        total_cycles = 0
        total_entries = 0

        for topic, d in directions.items():
            status = getattr(d, "status", "unknown")
            if status == "active":
                active_c += 1
            elif status in ("completed", "mastery"):
                completed_c += 1
            elif status in ("paused", "pending"):
                paused_c += 1

            total_cycles += getattr(d, "cycles_completed", 0)
            total_entries += getattr(d, "entries_created", 0)

            dir_entry: dict[str, Any] = {
                "topic": topic,
                "status": status,
                "entries": getattr(d, "entries_created", 0),
                "cycles": getattr(d, "cycles_completed", 0),
                "saturation": getattr(d, "saturation", 0.0),
                "confidence": getattr(d, "confidence", 0.0),
            }
            # Optional timestamp
            if hasattr(d, "completed_at") and d.completed_at:
                dir_entry["completed_at"] = str(d.completed_at)
            out["directions"].append(dir_entry)

        out["active_directions"] = active_c
        out["completed_directions"] = completed_c
        out["paused_directions"] = paused_c
        out["total_cycles"] = total_cycles
        out["total_entries"] = total_entries

        # Uptime approximation
        last_hb = getattr(learner, "_last_heartbeat", None)
        if last_hb and isinstance(last_hb, (int, float)) and last_hb > 0:
            out["uptime_hours"] = round((time.time() - last_hb) / 3600, 1)
    except Exception:
        _log.exception("learner_status error")

    return out


def _selfmodel_status() -> dict[str, Any]:
    """Read SelfModel capability scores and trends."""
    out: dict[str, Any] = {
        "available": False,
        "capabilities": [],
        "overall_grade": "N/A",
        "improvement_index": 0.0,
        "weekly_comparison": {},
        "curiosity": {},  # curiosity diversity metrics
    }

    try:
        from aelvoxim.core.selfmodel import SelfModel

        sm = SelfModel()
        caps = sm.capabilities() if hasattr(sm, "capabilities") else {}
        if not caps:
            return out

        out["available"] = True
        cap_list = []
        for domain, info in caps.items():
            cap_list.append(
                {
                    "domain": domain,
                    "score": round(info.get("success_rate", 0) * 100, 1),
                    "confidence": round(info.get("success_rate", 0) * 100, 1),
                    "grade": "A" if info.get("success_rate", 0) >= 0.9 else "B" if info.get("success_rate", 0) >= 0.7 else "C" if info.get("success_rate", 0) >= 0.5 else "D",
                    "trend": "+" if info.get("beta", 2) <= 2 else "-" if info.get("beta", 0) / max(info.get("alpha", 1), 1) > 0.5 else "→",
                }
            )
        out["capabilities"] = cap_list

        # Load curiosity diversity metrics
        try:
            from aelvoxim.learn.curiosity import get_curiosity_stats
            out["curiosity"] = get_curiosity_stats()
        except Exception:
            pass

        try:
            _grade = sm.overall_grade()
            if isinstance(_grade, dict):
                out["overall_grade"] = _grade.get("grade", "N/A")
                out["improvement_index"] = _grade.get("score", 0)
            else:
                out["overall_grade"] = str(_grade)
        except Exception:
            pass

        weekly = getattr(sm, "weekly_comparison", None)
        if callable(weekly):
            try:
                out["weekly_comparison"] = weekly()
            except Exception:
                pass
        elif isinstance(weekly, dict):
            out["weekly_comparison"] = weekly
    except Exception:
        _log.exception("selfmodel_status error")

    return out


def _metacog_status() -> dict[str, Any]:
    """Read recent metacognition trigger events from JSONL log."""
    out: dict[str, Any] = {
        "available": False,
        "recent_triggers": [],
        "total_trigger_count": 0,
        "triggers_by_type": {},
    }

    # The metacog history file — path depends on HOMEDIR
    metacog_file = _HOMEDIR / "metacog_history.jsonl"
    if not metacog_file.exists():
        return out

    try:
        triggers = []
        with open(str(metacog_file), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        # Each line is a MetaCogReport record containing a
                        # triggers[] array — flatten to the individual
                        # triggered events (records have no signal_name, which
                        # made every event show as "unknown").
                        _rec = json.loads(line)
                        _ts = _rec.get("timestamp", "")
                        for _t in _rec.get("triggers", []):
                            if _t.get("triggered"):
                                _t = dict(_t)
                                _t.setdefault("timestamp", _ts)
                                triggers.append(_t)
                    except json.JSONDecodeError:
                        pass

        out["available"] = True
        out["total_trigger_count"] = len(triggers)

        # Count by trigger type
        type_counts: dict[str, int] = {}
        for t in triggers:
            ttype = t.get("trigger_type", t.get("type", t.get("signal_name", "unknown")))
            type_counts[ttype] = type_counts.get(ttype, 0) + 1
        out["triggers_by_type"] = type_counts

        # Last 20 triggers, newest first
        recent = list(reversed(triggers))[:20]
        out["recent_triggers"] = [
            {
                "timestamp": t.get("timestamp", ""),
                "type": t.get("trigger_type", t.get("type", t.get("signal_name", "unknown"))),
                "reason": t.get("reason", t.get("summary", "")),
            }
            for t in recent
        ]
    except Exception:
        _log.exception("metacog_status error")

    return out


def _subsystem_health() -> list[dict[str, Any]]:
    """Report health status of key subsystems."""
    checks: list[dict[str, Any]] = []

    # Learner health — running state comes from the standalone learner
    # process heartbeat (status.json), not the in-process mirror instance
    # (which never runs its own loop in the API process).
    try:
        _l_running = False
        try:
            from aelvoxim.learn.loop import STATUS_FILE as _L_STATUS
            if _L_STATUS.exists():
                import json as _json
                _l_running = bool(_json.loads(_L_STATUS.read_text(encoding="utf-8")).get("running", False))
        except Exception:
            _log.exception("learner_status error")
        if not _l_running:
            from aelvoxim.learn.learner import get_learner
            _l = get_learner()
            _l_running = bool(_l and _l.is_running())
        if _l_running:
            checks.append({"name": "Learner Loop", "status": "online", "detail": "Running"})
        else:
            checks.append({"name": "Learner Loop", "status": "offline", "detail": "Stopped"})
    except Exception:
        checks.append({"name": "Learner Loop", "status": "error", "detail": "Import failed"})

    # MetaCog health: check if metacog module can be imported
    try:

        checks.append({"name": "MetaCognition", "status": "online", "detail": "Module loaded"})
    except Exception:
        checks.append({"name": "MetaCognition", "status": "offline", "detail": "Not available"})

    # PostgreSQL health
    try:
        from aelvoxim.core.health import get_pg_status
        pg = get_pg_status()
        checks.append({"name": "PostgreSQL", "status": "online" if pg.get("up") else "offline",
                        "detail": pg.get("version", pg.get("error", "Unknown"))})
    except Exception:
        checks.append({"name": "PostgreSQL", "status": "offline", "detail": "Check failed"})

    # SelfModel health — capability data lives in _capabilities (a dict of
    # CapabilityScore), not a "scores" attribute. Empty dict = degraded.
    try:
        from aelvoxim.core.selfmodel import SelfModel

        sm = SelfModel()
        caps = getattr(sm, "_capabilities", {}) or {}
        ok = bool(caps)
        detail = f"{len(caps)} capabilities loaded"
        if ok:
            _top = max(caps.values(), key=lambda c: c.task_count)
            detail += f" (best: {_top.task_count} tasks, {_top.success_rate} rate)"
        checks.append(
            {
                "name": "SelfModel",
                "status": "online" if ok else "degraded",
                "detail": detail,
            }
        )
    except Exception:
        checks.append({"name": "SelfModel", "status": "offline", "detail": "Not available"})

    # Expert orchestrator
    try:
        from aelvoxim.experts.orchestrator import ExpertOrchestrator

        eo = ExpertOrchestrator()
        n = len(getattr(eo, "_expert_classes", []))
        health = getattr(eo, "_expert_health", {})
        ok_count = sum(1 for h in health.values()
                       if h["runs"] == 0 or h["failures"] < h["runs"] * 0.5)
        detail = f"{n} experts registered, {ok_count}/{len(health) or n} healthy"
        checks.append({"name": "Expert Orchestrator", "status": "online", "detail": detail,
                       "experts": {name: dict(h) for name, h in health.items()}})
    except Exception:
        checks.append({"name": "Expert Orchestrator", "status": "offline", "detail": "Not available"})

    return checks


def _get_edition() -> str:
    """Return the current edition string."""
    try:
        from aelvoxim.server.edition import current

        return current()
    except Exception:
        return "community"
