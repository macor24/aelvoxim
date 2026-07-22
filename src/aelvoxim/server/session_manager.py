# SPDX-License-Identifier: MIT
"""
metacore.server.session_manager — Cross-session snapshot manager.

Saves structured snapshots at end of each conversation:
- pending_tasks: tasks the user mentioned (e.g. "下周面试")
- topic_focus: topics discussed in this session
- memory_pointers: entity IDs extracted during this session

New sessions can restore context from the latest snapshot.
Auto-expires snapshots older than 3 days (marked as "stale").
Maximum 30 snapshots per user.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

import logging
_log = logging.getLogger("aelvoxim.server.session_manager")

_SNAPSHOT_DIR = Path(METACORE_DIR) / "snapshots"
_MAX_SNAPSHOTS = 30
_STALE_DAYS = 3

# ── Sensitive data patterns for auto-masking ──

_SENSITIVE_PATTERNS = [
    (r'1[3-9]\d{9}', '***'),           # Chinese phone numbers
    (r'\d{17}[\dXx]', '***'),          # Chinese ID numbers
    (r'\b\d{16,19}\b', '***'),         # Credit card numbers
]


@dataclass
class SessionSnapshot:
    """Full snapshot of a single conversation session."""
    session_id: str
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0
    pending_tasks: List[str] = field(default_factory=list)
    topic_focus: List[str] = field(default_factory=list)
    memory_pointers: List[str] = field(default_factory=list)
    summary: str = ""
    stale: bool = False


def _mask_sensitive(text: str) -> str:
    """Mask phone numbers, IDs, and credit card numbers in text."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def _user_dir(user_id: str) -> Path:
    path = _SNAPSHOT_DIR / user_id.replace(":", "_")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot_path(user_id: str, session_id: str) -> Path:
    return _user_dir(user_id) / f"{session_id}.json"


def save_snapshot(
    user_id: str,
    session_id: str,
    messages: List[Dict[str, str]],
    entities: List[str],
    task_keywords: Optional[List[str]] = None,
) -> SessionSnapshot:
    """Save a snapshot for the current session.

    Args:
        user_id: User identifier.
        session_id: Unique session ID.
        messages: List of {'role': ..., 'content': ...} from this session.
        entities: List of entity IDs extracted during this session.
        task_keywords: Optional list of task-related keywords detected.

    Returns:
        The saved SessionSnapshot.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Extract topic focus from user messages
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    topic_focus = _extract_topics(" ".join(user_msgs))
    # Extract pending tasks from user messages
    pending_tasks = task_keywords or _extract_tasks(" ".join(user_msgs))
    # Build summary — combine last user msg with AI response prefix
    last = user_msgs[-1][:80] if user_msgs else ""
    last_ai = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            last_ai = m["content"][:60].strip()
            break
    summary = _mask_sensitive(last)
    if last_ai:
        summary += f"... resp: {last_ai}"

    snap = SessionSnapshot(
        session_id=session_id,
        user_id=user_id,
        created_at=now,
        updated_at=now,
        message_count=len(messages),
        pending_tasks=pending_tasks,
        topic_focus=topic_focus[:5],
        memory_pointers=entities[:10],
        summary=summary,
        stale=False,
    )

    # Prune old snapshots
    _prune(user_id)

    # Save to file
    path = _snapshot_path(user_id, session_id)
    with open(str(path), "w") as f:
        json.dump(asdict(snap), f, ensure_ascii=False, indent=2)

    return snap


def load_latest_snapshot(user_id: str) -> Optional[SessionSnapshot]:
    """Load the most recent non-stale snapshot for this user.

    Returns None if no valid snapshot exists.
    """
    user_dir = _user_dir(user_id)
    if not user_dir.exists():
        return None

    files = sorted(user_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text())
            snap = SessionSnapshot(**data)
            # Mark as stale if older than STALE_DAYS
            created = datetime.strptime(snap.created_at[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - created).days > _STALE_DAYS:
                snap.stale = True
                # Update file
                with open(str(f), "w") as fw:
                    json.dump(asdict(snap), fw, ensure_ascii=False, indent=2)
                continue  # skip stale snapshots
            return snap
        except Exception:
            continue
    return None


def restore_context(user_id: str) -> str:
    """Build a context string from the latest snapshot for injection.

    Returns:
        Empty string if no valid snapshot, or a formatted context string
        like "[SessionRestore: you were discussing interview preparation]"
    """
    snap = load_latest_snapshot(user_id)
    if not snap:
        return ""
    parts = []
    if snap.summary:
        parts.append(f"previous: {snap.summary[:120]}")
    if snap.pending_tasks:
        tasks = ", ".join(snap.pending_tasks[:3])
        parts.append(f"you had pending: {tasks}")
    if snap.topic_focus:
        topics = ", ".join(snap.topic_focus[:3])
        parts.append(f"topics: {topics}")
    if not parts:
        return ""
    return "[SessionRestore: {}] ".format("; ".join(parts))


def get_pending_reminders(user_id: str) -> List[str]:
    """Get pending task reminders for active notifications.

    Returns list of reminder strings (empty if no pending tasks or no authorization).
    """
    snap = load_latest_snapshot(user_id)
    if not snap or not snap.pending_tasks:
        return []
    # Only remind if snapshot is at least 1 day old (not the same session)
    updated = datetime.strptime(snap.updated_at[:19], "%Y-%m-%d %H:%M:%S")
    if (datetime.now() - updated).days < 1:
        return []
    return [f"[Reminder: you mentioned \"{t}\" — any updates?] " for t in snap.pending_tasks[:2]]


def _extract_topics(text: str) -> List[str]:
    """Extract topic keywords from conversation text (rule-based)."""
    if not text:
        return []
    words = text.lower().split()
    # Simple frequency-based extraction
    from collections import Counter
    stopwords = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
                 "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
                 "没有", "看", "好", "自己", "这", "the", "a", "an", "is", "are",
                 "was", "were", "be", "been", "have", "has", "had", "do", "does",
                 "did", "will", "would", "can", "could", "may", "might", "shall",
                 "should", "to", "of", "in", "for", "on", "with", "at", "by", "from"}
    # Also filter very short tokens
    filtered = [w for w in words if len(w) > 2 and w not in stopwords]
    common = Counter(filtered).most_common(10)
    return [w for w, c in common if c >= 2][:5]


def _extract_tasks(text: str) -> List[str]:
    """Extract task-like phrases (rule-based, no LLM)."""
    if not text:
        return []
    tasks = []
    # Chinese patterns
    patterns = [
        r'(?:下周|明天|下个月|月底|下周五|下周六)\s*\S{2,10}',
        r'(?:要|需要|准备|打算|想)\s*\S{2,10}(?:面试|考试|会议|报告|作业|任务|出差)',
        r'(?:记住|别忘了|别忘了做).{2,30}',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            task = m.group().strip()
            if task and len(task) <= 50:
                tasks.append(_mask_sensitive(task))
    return tasks[:5]


def _prefs_path(user_id: str) -> Path:
    return _user_dir(user_id) / "_preferences.json"


def get_user_preferences(user_id: str) -> Dict[str, Any]:
    """Get proactive reminder preferences for a user.

    Returns dict with keys: proactive_enabled, frequency, accepted, ignored, last_reminder.
    Default values returned if no saved preferences exist.
    """
    default: Dict[str, Any] = {
        "proactive_enabled": False,
        "frequency": "low",
        "accepted": 0,
        "ignored": 0,
        "last_reminder": "",
    }
    path = _prefs_path(user_id)
    if not path.exists():
        return default
    try:
        saved = json.loads(path.read_text())
        default.update(saved)
    except Exception:
        _log.exception("session_manager error")
    return default


def update_user_preference(user_id: str, key: str, value: Any) -> None:
    """Update a single preference key for a user.

    Supported keys: proactive_enabled, frequency, accepted, ignored, last_reminder.
    """
    prefs = get_user_preferences(user_id)
    prefs[key] = value
    path = _prefs_path(user_id)
    path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2))


def adjust_preference_by_response(user_id: str, responded: bool) -> None:
    """Dynamically adjust reminder frequency based on user response.

    - 3 consecutive accepts → upgrade frequency
    - 3 consecutive ignores → downgrade frequency
    - Downgrade to 'off' means proactive mode disabled
    """
    prefs = get_user_preferences(user_id)
    if responded:
        prefs["accepted"] = prefs.get("accepted", 0) + 1
        prefs["ignored"] = 0
    else:
        prefs["ignored"] = prefs.get("ignored", 0) + 1
        prefs["accepted"] = 0
    # Adjust frequency on streaks
    freq_order = ["off", "low", "medium", "high"]
    current_idx = freq_order.index(prefs.get("frequency", "low"))
    if prefs.get("accepted", 0) >= 3 and current_idx < len(freq_order) - 1:
        prefs["frequency"] = freq_order[current_idx + 1]
        prefs["accepted"] = 0
        _audit("frequency_upgrade", {"user": user_id, "new_freq": prefs["frequency"]})
    elif prefs.get("ignored", 0) >= 3 and current_idx > 0:
        prefs["frequency"] = freq_order[current_idx - 1]
        prefs["ignored"] = 0
        _audit("frequency_downgrade", {"user": user_id, "new_freq": prefs["frequency"]})
    path = _prefs_path(user_id)
    path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2))


def _audit(event: str, data: Dict[str, Any]) -> None:
    """Write an audit log entry."""
    entry = json.dumps({
        "ts": datetime.now().isoformat(),
        "event": event,
        **data,
    }, ensure_ascii=False)
    log_path = _SNAPSHOT_DIR.parent / "ethics" / "audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(log_path), "a") as f:
        f.write(entry + "\n")


def _prune(user_id: str) -> None:
    """Keep at most _MAX_SNAPSHOTS snapshots per user, and delete any older than 30 days."""
    user_dir = _user_dir(user_id)
    files = sorted(user_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
    now = datetime.now()
    for f in files:
        # Delete files older than 30 days
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            if (now - mtime).days > 30:
                f.unlink()
                continue
        except Exception:
            _log.exception("session_manager error")
        # Also cap count
    files = sorted(user_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
    for f in files[_MAX_SNAPSHOTS:]:
        f.unlink()
