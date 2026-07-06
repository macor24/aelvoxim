"""aelvoxim.server.audit — Structured operation audit logging.

Logs all security-relevant operations to ~/.aelvoxim/audit/audit.jsonl.
Each entry has timestamp, user, action, and optional detail.

Pure stdlib, no external deps.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Audit log directory
_AUDIT_DIR = Path(os.environ.get("AELVOXIM_DATA_DIR", str(Path.home() / ".aelvoxim"))) / "audit"
_AUDIT_LOG = _AUDIT_DIR / "audit.jsonl"


def _ensure_dir() -> None:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def log(
    action: str,
    user: str = "",
    detail: Optional[dict] = None,
    status: str = "success",
) -> None:
    """Write an audit log entry.

    Args:
        action: Operation name (e.g. 'user.login', 'user.create', 'config.update').
        user: User email or API key suffix.
        detail: Optional dict with extra context.
        status: 'success' | 'failure' | 'blocked'.
    """
    try:
        _ensure_dir()
        entry = {
            "ts": datetime.now().isoformat(),
            "action": action,
            "user": user or "",
            "status": status,
        }
        if detail:
            # Mask sensitive fields
            safe = dict(detail)
            for sensitive in ("password", "api_key", "token", "secret", "key"):
                if sensitive in safe:
                    safe[sensitive] = "***"
            entry["detail"] = safe
        with open(str(_AUDIT_LOG), "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Non-critical: audit failure should not crash the app


def get_recent(limit: int = 50) -> list[dict]:
    """Get the most recent audit log entries."""
    if not _AUDIT_LOG.exists():
        return []
    try:
        lines = _AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in lines[-limit:]:
            if line.strip():
                entries.append(json.loads(line))
        return entries[::-1]  # newest first
    except Exception:
        return []
