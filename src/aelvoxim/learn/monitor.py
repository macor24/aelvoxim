"""aelvoxim.learn.monitor — Health monitor and self-healing engine

Three layers:
1. Metric collection - from Learner, KnowledgeBase, SelfModel, search config
2. Diagnosis - rule engine matching anomaly patterns
3. Healing - execute fixes, only modify runtime config/data, never source code

Safety boundary:
  HealthMonitor only writes: search-config.json, learner/config.json, heal_log
  HealthMonitor never writes: any .py file, system files, import paths

Design principles:
  - Each method has isolated try/except, one crash won't affect others
  - Fix operations have cooldown period (default 30min)
  - All fixes are logged for rollback
  - Zero third-party dependencies, pure stdlib
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aelvoxim.monitor")

# ── Data paths (runtime config and data, not source code) ───────────

METACORE_DATA_DIR = Path.home() / ".aelvoxim"
_SEARCH_CONFIG_PATH = METACORE_DATA_DIR / "search-config.json"
_LEARNER_CONFIG_PATH = METACORE_DATA_DIR / "learner" / "config.json"
_LEARNER_LOG_PATH = METACORE_DATA_DIR / "learner" / "learner.log"
_LEARNER_STATUS_PATH = METACORE_DATA_DIR / "learner" / "status.json"
_HEAL_LOG_PATH = METACORE_DATA_DIR / "heal_log.jsonl"


# ── Severity levels ─────────────────────────────────

SEVERITY_INFO = 0        # Log only
SEVERITY_LOW = 1         # Warning
SEVERITY_MEDIUM = 2      # Self-heal
SEVERITY_HIGH = 3        # Self-heal + urgent report


# ── Data structures ─────────────────────────────────


@dataclass
class MetricSnapshot:
    """A single health check snapshot. All metrics collected from runtime state, no external dependencies."""
    timestamp: float = 0.0

    # Direction status
    active_directions: int = 0
    completed_directions: int = 0
    total_directions: int = 0

    # Knowledge output
    total_entries: int = 0
    pending_entries: int = 0
    entries_last_24h: int = 0
    entries_last_7d: int = 0

    # Search status
    search_engine: str = "unknown"
    search_is_mock: bool = False
    auto_discover_errors_last_hour: int = 0

    # Learner running status
    learner_running: bool = False
    learner_idle_hours: float = 0.0

    # Review status
    overdue_reviews: int = 0
    mastery_count: int = 0

    # SelfModel
    decision_count: int = 0
    capability_count: int = 0
    snapshot_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Diagnosis:
    """A single diagnosis result."""
    symptom: str = ""
    severity: int = SEVERITY_INFO
    detail: str = ""
    fix_action: str = ""
    fix_params: Dict = field(default_factory=dict)


# ── Utility methods ─────────────────────────────────


def _read_json(path: Path) -> Optional[Dict]:
    """Safely read a JSON file. Returns None if missing or corrupted."""
    try:
        if path and path.exists():
            raw = path.read_text(encoding="utf-8")
            if len(raw) > 50 * 1024 * 1024:  # 50MB safety limit
                logger.warning("File too large, skipping: %s (%d bytes)", path, len(raw))
                return None
            return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, PermissionError, OSError) as e:
        logger.debug("Read file failed %s: %s", path, e)
    return None


def _write_json(path: Path, data: Dict) -> bool:
    """Safely write a JSON file. Atomic write (tmp → rename)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except (OSError, PermissionError) as e:
        logger.warning("Write to file failed %s: %s", path, e)
    return False


def _count_recent_log_errors(pattern: str, hours: float = 1.0) -> int:
    """Count lines matching pattern in logs from the last N hours."""
    try:
        if not _LEARNER_LOG_PATH.exists():
            return 0
        logs = _LEARNER_LOG_PATH.read_text(encoding="utf-8").split("\n")
        cutoff = time.time() - hours * 3600
        count = 0
        for line in logs:
            if pattern not in line:
                continue
            if line.startswith("["):
                try:
                    ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S").timestamp()
                    if ts < cutoff:
                        continue
                except (ValueError, IndexError):
                    pass
            count += 1
        return count
    except (OSError, PermissionError) as e:
        logger.debug("Read log failed: %s", e)
    return 0


def _log_heal(description: str) -> None:
    """Log a heal action to heal_log.jsonl."""
    try:
        _HEAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": description,
        }
        with open(_HEAL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, PermissionError) as e:
        logger.warning("Write heal log failed: %s", e)


# ── Safety boundary verification ────────────────────────────

_PYTHON_SOURCE_EXTENSIONS = (".py", ".pyc", ".pyx", ".so", ".pyd", ".pxd")
_BLOCKED_DIR_PREFIXES = (
    "src/",
    "site-packages/",
    "dist-packages/",
    "/usr/lib/",
    "/usr/local/lib/",
    "/etc/",
)


def _is_safe_write_path(path: Path) -> bool:
    """Safety check: confirm path is a runtime data file, not source code or system file.

    Safety rules:
    1. Must start with ~/.metacore/ (after resolving)
    2. Must not have .py / .so / .pyc or other source extensions
    3. Must not be a system path like /usr/lib / /etc
    """
    resolved = str(path.resolve())
    # Must be under .metacore directory
    metacore_str = str(METACORE_DATA_DIR.resolve())
    if not resolved.startswith(metacore_str + "/") and resolved != metacore_str:
        logger.warning("Security block: path outside .metacore directory: %s", resolved)
        return False
    # Disallow unresolved path traversal (defense in depth)
    if "/../" in resolved or "/.." == resolved[-3:]:
        logger.warning("Security block: path traversal detected: %s", resolved)
        return False
    # Must not be a Python source file
    if path.suffix.lower() in _PYTHON_SOURCE_EXTENSIONS:
        logger.warning("Security block: cannot write to source file: %s", resolved)
        return False
    return True


def _safe_write_json(path: Path, data: Dict) -> bool:
    """JSON write with safety boundary check."""
    if not _is_safe_write_path(path):
        return False
    return _write_json(path, data)


# ── Health monitor class ─────────────────────────────────


class HealthMonitor:
    """Health monitor + self-healing engine.

    Called via tick() during Learner's _main_loop idle time, automatically:
    1. Collects 14 health metrics
    2. Rule engine diagnoses error patterns
    3. Executes heal actions (runtime config/data only)

    Safety constraints:
    - Never modifies any .py / .so files
    - All writes go through _safe_write_json (path whitelist check)
    - Fix operations have cooldown period (default 30 min)
    - Each step has independent try/except, single crash won't affect the whole
    """

    TICK_INTERVAL = 300       # Collect every 5 minutes
    METRICS_HISTORY_LIMIT = 100
    FIX_COOLDOWN = 1800       # At least 30 minutes between fixes

    def __init__(self, learner=None):
        self._learner = learner          # Learner instance reference (optional)
        self._last_tick = 0.0
        self._metrics: List[MetricSnapshot] = []
        self._last_fix_time: float = 0.0

    # ── Main entry ───────────────────────────────

    def tick(self) -> List[str]:
        """Main entry. Called periodically in the Learner main loop.

        Returns list of heal action descriptions triggered this round (empty = all good).
        Safe: all exceptions are caught, never propagate outward.
        """
        now = time.time()
        if now - self._last_tick < self.TICK_INTERVAL:
            return []
        self._last_tick = now

        try:
            snapshot = self._collect_metrics()
        except Exception as e:
            logger.warning("Metric collection error: %s", e)
            return []

        self._metrics.append(snapshot)
        if len(self._metrics) > self.METRICS_HISTORY_LIMIT:
            self._metrics = self._metrics[-self.METRICS_HISTORY_LIMIT:]

        try:
            issues = self._diagnose(snapshot)
        except Exception as e:
            logger.warning("Diagnostic error: %s", e)
            return []

        fixes = []
        for issue in issues:
            try:
                result = self._heal(issue)
                if result:
                    fixes.append(result)
            except Exception as e:
                logger.warning("Heal action error [%s]: %s", issue.fix_action, e)
        return fixes

    # ── Metric collection ─────────────────────────────

    def _collect_metrics(self) -> MetricSnapshot:
        """Collect all current health metrics.

        Each collection item has independent try/except, single failure uses default values,
        one metric error won't cause total collection failure.
        """
        snap = MetricSnapshot(timestamp=time.time())

        # 1. Direction status
        try:
            if self._learner and hasattr(self._learner, "_directions"):
                dirs = self._learner._directions
                snap.total_directions = len(dirs)
                snap.active_directions = sum(
                    1 for d in dirs.values() if d.status == "active"
                )
                snap.completed_directions = sum(
                    1 for d in dirs.values() if d.status == "completed"
                )
            else:
                cfg = _read_json(_LEARNER_CONFIG_PATH) or {}
                snap.total_directions = len(cfg)
                snap.active_directions = sum(
                    1 for d in cfg.values()
                    if isinstance(d, dict) and d.get("status") == "active"
                )
                snap.completed_directions = sum(
                    1 for d in cfg.values()
                    if isinstance(d, dict) and d.get("status") == "completed"
                )
        except Exception as e:
            logger.debug("Collect direction status failed: %s", e)

        # 2. Knowledge base
        try:
            from aelvoxim.learn.knowledge import KnowledgeBase

            active = list(KnowledgeBase.get_all_active())
            pending = list(KnowledgeBase.get_pending())
            snap.total_entries = len(active)
            snap.pending_entries = len(pending)
            now_dt = datetime.now()
            for e in active:
                ts = e.get("created_at", "") or e.get("updated_at", "")
                if ts:
                    try:
                        dt = datetime.strptime(ts[:10], "%Y-%m-%d")
                        if (now_dt - dt).days < 1:
                            snap.entries_last_24h += 1
                        if (now_dt - dt).days < 7:
                            snap.entries_last_7d += 1
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.debug("Collect knowledge base failed: %s", e)

        # 3. Search config
        try:
            sc = _read_json(_SEARCH_CONFIG_PATH) or {}
            snap.search_engine = sc.get("engine", "unknown")
            snap.search_is_mock = (sc.get("engine") == "mock")
        except Exception as e:
            logger.debug("Collect search config failed: %s", e)

        # 4. Auto-discovery error rate
        try:
            snap.auto_discover_errors_last_hour = _count_recent_log_errors(
                "自动发现Raises", hours=1.0
            )
        except Exception as e:
            logger.debug("Collect auto-discovery error rate failed: %s", e)

        # 5. Learner running status
        try:
            st = _read_json(_LEARNER_STATUS_PATH) or {}
            snap.learner_running = st.get("running", False)
        except Exception as e:
            logger.debug("Collect Learner status failed: %s", e)

        # 6. Review / mastery status
        try:
            if self._learner and hasattr(self._learner, "_directions"):
                cfg_data_v2 = self._learner._directions
            else:
                cfg_data_v2 = _read_json(_LEARNER_CONFIG_PATH) or {}
            now_ts = time.time()
            for d in cfg_data_v2.values():
                if not isinstance(d, dict):
                    continue
                nra = d.get("next_review_at", "")
                if nra:
                    try:
                        review_dt = datetime.strptime(nra[:19], "%Y-%m-%d %H:%M:%S")
                        if review_dt.timestamp() < now_ts:
                            snap.overdue_reviews += 1
                    except (ValueError, TypeError):
                        pass
                if d.get("status") == "mastery":
                    snap.mastery_count += 1
        except Exception as e:
            logger.debug("Collect review status failed: %s", e)

        # 7. SelfModel
        try:
            from aelvoxim.core.selfmodel import SelfModel

            sm = SelfModel()
            snap.decision_count = len(sm._decisions)
            snap.capability_count = len(sm._capabilities)
            snap.snapshot_count = len(sm._snapshots)
        except Exception as e:
            logger.debug("Collect SelfModel failed: %s", e)

        # 8. Idle time estimate (only when Learner is running and no active directions)
        if snap.learner_running and snap.active_directions == 0:
            try:
                if _LEARNER_LOG_PATH.exists():
                    logs = _LEARNER_LOG_PATH.read_text(
                        encoding="utf-8"
                    ).split("\n")
                    for line in reversed(logs):
                        if "无活跃学习方向" in line and line.startswith("["):
                            try:
                                ts = datetime.strptime(
                                    line[1:20], "%Y-%m-%d %H:%M:%S"
                                )
                                snap.learner_idle_hours = (
                                    datetime.now() - ts
                                ).total_seconds() / 3600.0
                            except (ValueError, IndexError):
                                pass
                            break
            except (OSError, PermissionError) as e:
                logger.debug("Collect idle time failed: %s", e)

        return snap

    # ── Diagnostic engine ─────────────────────────────

    def _diagnose(self, snap: MetricSnapshot) -> List[Diagnosis]:
        """Analyze metrics and return a list of diagnoses.

        Rule-engine style - each if block is a rule.
        No rule engine library, pure if/elif.
        Each rule is independent and does not affect others.
        """
        issues: List[Diagnosis] = []

        # Rule 1: Search engine is mock -> high priority
        if snap.search_is_mock:
            issues.append(Diagnosis(
                symptom="search_engine_is_mock",
                severity=SEVERITY_MEDIUM,
                detail=f"Search is configured as mock, auto-discovery will return fake data",
                fix_action="fix_search_engine",
                fix_params={"target_engine": "bing_cn"},
            ))

        # Rule 2: No active directions and Learner is running
        if snap.active_directions == 0 and snap.learner_running:
            issues.append(Diagnosis(
                symptom="no_active_directions",
                severity=SEVERITY_MEDIUM,
                detail=f"All {snap.completed_directions} directions completed, Learner idling",
                fix_action="add_directions_from_knowledge",
                fix_params={"max_directions": 5},
            ))

        # Rule 3: Auto-discovery keeps raising errors (>= 3 in past hour)
        if snap.auto_discover_errors_last_hour >= 3:
            issues.append(Diagnosis(
                symptom="auto_discover_failing",
                severity=SEVERITY_MEDIUM,
                detail=f"{snap.auto_discover_errors_last_hour} auto-discovery errors in the past hour",
                fix_action="log_auto_discover_diagnosis",
                fix_params={},
            ))

        # Rule 4: Active directions but no knowledge output in 24 hours
        if snap.entries_last_24h == 0 and snap.active_directions > 0:
            issues.append(Diagnosis(
                symptom="no_knowledge_growth",
                severity=SEVERITY_LOW,
                detail=f"{snap.active_directions} active directions but no output in 24h",
                fix_action="restart_stuck_directions",
                fix_params={},
            ))

        # Rule 5: Learner has been idle for more than 24 hours
        if snap.learner_idle_hours > 24:
            issues.append(Diagnosis(
                symptom="learner_idle_too_long",
                severity=SEVERITY_MEDIUM,
                detail=f"Learner has been idle for {snap.learner_idle_hours:.1f} hours",
                fix_action="add_directions_from_knowledge",
                fix_params={"max_directions": 3},
            ))

        # Rule 6: Pending knowledge backlog exceeds 20 items
        if snap.pending_entries > 20:
            issues.append(Diagnosis(
                symptom="pending_knowledge_backlog",
                severity=SEVERITY_LOW,
                detail=f"{snap.pending_entries} pending knowledge items not processed",
                fix_action="approve_pending_knowledge",
                fix_params={},
            ))

        # Rule 7: SelfModel has decisions but no snapshots
        if snap.decision_count > 0 and snap.snapshot_count == 0:
            issues.append(Diagnosis(
                symptom="selfmodel_no_snapshots",
                severity=SEVERITY_LOW,
                detail=f"SelfModel has {snap.decision_count} decisions but no snapshots",
                fix_action="force_selfmodel_snapshot",
                fix_params={},
            ))

        return issues

    # ── Fix dispatch ─────────────────────────────

    def _heal(self, diagnosis: Diagnosis) -> Optional[str]:
        """Execute a heal action. Returns heal description, returns None on failure.

        Safety: checks cooldown to prevent frequent fixes.
        """
        now = time.time()
        if now - self._last_fix_time < self.FIX_COOLDOWN:
            return None  # In cooldown, skip

        action = diagnosis.fix_action
        params = diagnosis.fix_params
        handler = getattr(self, f"_heal_{action}", None)
        if not handler:
            logger.debug("Unknown heal action: %s", action)
            return None

        try:
            result = handler(**params)
            if result:
                self._last_fix_time = now
                _log_heal(result)
                return result
        except Exception as e:
            logger.warning("Heal action execution failed [%s]: %s", action, e)
        return None

    # ── Heal actions ─────────────────────────────

    # Safety: All heal actions only write runtime config files, never .py source code

    def _heal_fix_search_engine(self, target_engine: str = "bing_cn") -> Optional[str]:
        """Fix: Change search engine from mock to real.

        Writes: ~/.metacore/search-config.json (runtime config)
        Safe: no subprocess, no eval, no exec
        """
        cfg = _read_json(_SEARCH_CONFIG_PATH)
        if not cfg:
            return None
        if cfg.get("engine") != "mock":
            return None  # Already real
        cfg["engine"] = target_engine
        cfg["bing_api_key"] = ""
        if _safe_write_json(_SEARCH_CONFIG_PATH, cfg):
            return f"Search engine fixed from mock to {target_engine}"
        return None

    def _heal_add_directions_from_knowledge(self, max_directions: int = 5) -> Optional[str]:
        """Fix: Add new learning directions from knowledge base.

        Writes: ~/.metacore/learner/config.json (runtime config)
        Safe: no subprocess, no eval, no exec
        """
        try:
            from aelvoxim.learn.knowledge import KnowledgeBase

            active = list(KnowledgeBase.get_all_active())
            topic_counter: Counter = Counter(
                e.get("topic", "") for e in active if e.get("topic")
            )

            # Get existing directions
            if self._learner and hasattr(self._learner, "_directions"):
                existing = set(self._learner._directions.keys())
            else:
                cfg = _read_json(_LEARNER_CONFIG_PATH) or {}
                existing = set(cfg.keys())

            added = 0
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for topic, _ in topic_counter.most_common(20):
                if not topic or len(topic) < 4:
                    continue
                if topic in existing:
                    continue

                # Prefer adding via Learner API
                if self._learner and hasattr(self._learner, "add_direction"):
                    if self._learner.add_direction(topic):
                        added += 1
                else:
                    # Fallback: directly write config.json
                    new_entry = {
                        "topic": topic,
                        "status": "active",
                        "phase_index": 0,
                        "template_index": 0,
                        "saturation": 0.0,
                        "started_at": now_str,
                        "last_cycle": "",
                        "cycles_completed": 0,
                        "entries_created": 0,
                        "review_history": "[]",
                        "next_review_at": "",
                        "review_streak": 0,
                        "task_queue": "[]",
                        "current_task": "",
                        "completed_tasks": "[]",
                        "reflect_no_produce": 0,
                    }
                    cfg[topic] = new_entry
                    existing.add(topic)
                    added += 1

                if added >= max_directions:
                    break

            # If using the direct config.json write approach, write the file
            if not (self._learner and hasattr(self._learner, "add_direction")):
                if added > 0:
                    _safe_write_json(_LEARNER_CONFIG_PATH, cfg)

            if added > 0:
                return f"Automatically added {added} new directions from knowledge base"
        except Exception as e:
            logger.warning("Add direction failed: %s", e)
        return None

    def _heal_restart_stuck_directions(self) -> Optional[str]:
        """Fix 3: Reset task queue of stuck directions.

        Only modifies Learner memory state + writes config.json (runtime config).
        Safe: no subprocess, no eval of code.
        """
        if not (self._learner and hasattr(self._learner, "_directions")):
            return None
        dirs = self._learner._directions
        now = datetime.now()
        reset_count = 0
        for topic, d in list(dirs.items()):
            if d.status != "active":
                continue
            if d.entries_created > 0:
                continue  # Has output, so not stuck
            lc = d.last_cycle
            if not lc:
                continue
            try:
                lc_dt = datetime.strptime(lc[:19], "%Y-%m-%d %H:%M:%S")
                if (now - lc_dt).total_seconds() < 7200:
                    continue  # Less than 2 hours, not considered stuck
            except (ValueError, TypeError):
                continue
            # Reset task queue
            d.task_queue = "[]"
            d.completed_tasks = "[]"
            d.current_task = ""
            d.reflect_no_produce = 0
            reset_count += 1

        if reset_count > 0:
            self._learner._save_config()
            return f"Reset task queues for {reset_count} stuck directions"
        return None

    def _heal_approve_pending_knowledge(self) -> Optional[str]:
        """Fix 4: Batch approve/cleanup pending knowledge.

        Rules (based on AutoValidator result and confidence):
        - combined_score >= 0.4 -> approve (search validation passed)
        - conf >= 0.3 -> approve (weak pass but searched+LLM, has actual content)
        - score < 0.2 -> reject (very low search validation, might be fake data)
        - remainder stay pending

        Only operates KnowledgeBase API (data layer), never writes .py source code.
        """
        try:
            from aelvoxim.learn.knowledge import KnowledgeBase

            pending = list(KnowledgeBase.get_pending())
            approved = 0
            rejected = 0
            for p in pending:
                conf = p.get("confidence", 0)
                score = p.get("_last_auto_result", {}).get("combined_score", conf)
                eid = p.get("id", "")
                if not eid:
                    continue
                if score >= 0.6 and conf >= 0.5:
                    if approved >= 20:  # Max 20 approvals per batch
                        break
                    result = KnowledgeBase.approve(eid)
                    if isinstance(result, dict) and result.get("approved"):
                        approved += 1
                elif score < 0.2:
                    if rejected >= 20:  # Max 20 rejections per batch
                        break
                    result = KnowledgeBase.reject(eid)
                    if result:
                        rejected += 1
            if approved > 0 or rejected > 0:
                parts = []
                if approved:
                    parts.append(f"Approved {approved}")
                if rejected:
                    parts.append(f"Rejected {rejected}")
                return f"Auto-processed pending knowledge: {', '.join(parts)}"
        except Exception as e:
            logger.warning("Approve pending failed: %s", e)
        return None

    def _heal_log_auto_discover_diagnosis(self) -> Optional[str]:
        """Fix 5: Log auto-discovery error diagnosis info.

        Only writes healer_log (runtime log), does not modify any config or data.
        Code bugs cannot self-heal, but recording the diagnosis helps user troubleshooting.
        """
        try:
            count = _count_recent_log_errors("自动发现Raises", hours=24.0)
            if count > 0:
                _log_heal(
                    f"Auto-discovery error diagnosis: {count} errors in the past 24 hours. "
                    "Possible search() argument error or network issue. Manual code review needed."
                )
                return f"Auto-discovery error diagnosis recorded ({count} errors in 24h)"
        except Exception as e:
            logger.warning("Record auto-discovery diagnosis failed: %s", e)
        return None

    def _heal_force_selfmodel_snapshot(self) -> Optional[str]:
        """Fix 6: Force SelfModel to generate a snapshot.

        Only operates SelfModel API (data layer), never writes .py source code.
        """
        try:
            from aelvoxim.core.selfmodel import SelfModel

            sm = SelfModel()
            sm.take_snapshot()
            return "Forced SelfModel to generate a snapshot"
        except Exception as e:
            logger.warning("Force SelfModel snapshot failed: %s", e)
        return None
