"""aelvoxim.hooks — Lightweight data feedback hooks

Embedded within the engine, not standalone modules.
No full audit trail, no reporting, no automated training.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

# ── Outcome tracking ───────────────────────

_OUTCOME_FILE = METACORE_DIR / "hooks" / "outcomes.jsonl"
_FAILURE_CACHE: List[Dict] = []
_MAX_FAILURES = 100


def record_outcome(task_id: str, success: bool, task_type: str = "learn",
                   detail: str = "") -> None:
    """Record task success/failure to persistent log."""
    entry = {
        "task_id": task_id,
        "task_type": task_type,
        "success": success,
        "timestamp": time.time(),
        "detail": detail,
    }
    try:
        _OUTCOME_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_OUTCOME_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # non-critical, continue
    if not success:
        _FAILURE_CACHE.append(entry)
        if len(_FAILURE_CACHE) > _MAX_FAILURES:
            _FAILURE_CACHE.pop(0)


def get_recent_failures(n: int = 10) -> List[Dict]:
    """Get most recent N failures."""
    return _FAILURE_CACHE[-n:]


# ── Metrics ───────────────────────────────


def emit_metric(name: str, value: float, tags: Optional[Dict] = None) -> None:
    """Emit a metric. Current implementation: print to stderr."""
    tags_str = f" {tags}" if tags else ""
    _log.info("[metric] %s=%s%s", name, value, tags_str)


# ── Learning trigger switch ───────────────

_TRIGGER_ENABLED = True


def enable_learning() -> None:
    global _TRIGGER_ENABLED
    _TRIGGER_ENABLED = True


def disable_learning() -> None:
    global _TRIGGER_ENABLED
    _TRIGGER_ENABLED = False


def is_learning_enabled() -> bool:
    return _TRIGGER_ENABLED
