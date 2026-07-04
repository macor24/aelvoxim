"""aelvoxim.utils — Common utilities for MetaCore

Provides data directory paths, time helpers, and i18n support.
Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


# ── Paths ──

DATA_DIR = Path.home() / ".aelvoxim"
CONFIG_FILE = DATA_DIR / "config.json"
LLM_CONFIG_FILE = DATA_DIR / "llm-config.json"
SEARCH_CONFIG_FILE = DATA_DIR / "search-config.json"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
ENTRIES_DIR = KNOWLEDGE_DIR / "entries"
INDEX_FILE = KNOWLEDGE_DIR / "index.json"
PENDING_FILE = KNOWLEDGE_DIR / "pending.json"
MEMORY_FILE = DATA_DIR / "memory.json"
SELFMODEL_FILE = DATA_DIR / "selfmodel.json"
LEARNER_DIR = DATA_DIR / "learner"
HEAL_LOG = DATA_DIR / "heal_log.jsonl"
CACHE_DIR = DATA_DIR / "cache"
LEARNER_CONFIG = LEARNER_DIR / "config.json"
LEARNER_STATUS = LEARNER_DIR / "status.json"

# ── Compatibility alias ──
METACORE_DIR = DATA_DIR

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_str(fmt: str = DATETIME_FMT) -> str:
    """Get current time string."""
    return datetime.now().strftime(fmt)


def parse_dt(s: str, fmt: str = DATETIME_FMT) -> Optional[datetime]:
    """Parse datetime string, return None on failure."""
    try:
        return datetime.strptime(s[:19], fmt)
    except (ValueError, TypeError):
        return None


def hours_ago(dt_str: str) -> Optional[float]:
    """Calculate hours since given datetime string. Returns None on failure."""
    dt = parse_dt(dt_str)
    if dt is None:
        return None
    return (datetime.now() - dt).total_seconds() / 3600


def read_json(path: Path) -> Optional[Dict]:
    """Safely read a JSON file. Returns None if missing or corrupt."""
    try:
        if path and path.exists():
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return {}
            if len(raw) > 50 * 1024 * 1024:
                return {}
            return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def write_json(path: Path, data: Dict, indent: int = 2) -> bool:
    """Safely write JSON with atomic tmp → rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def get_data_dir() -> Path:
    """Return the aelvoxim data directory root."""
    return DATA_DIR
