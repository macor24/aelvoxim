"""
metacore.control.retry_queue — Interrupt queue for generation controller.

Tracks chunks that failed metacognition checks and their correction attempts.
Supports SEVERE (retry loop) and MINOR (post-generation supplement) entries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RetryEntry:
    """A single interrupted chunk and its correction state."""
    id: str
    chunk: str
    issues: List[Dict[str, str]]
    attempt: int = 1
    max_attempts: int = 3
    resolved: bool = False
    failed: bool = False
    is_minor: bool = False
    created_at: float = field(default_factory=time.time)


class RetryQueue:
    """Manages interrupted chunks and generates correction prompts/supplements."""

    def __init__(self, max_attempts: int = 3):
        self.entries: List[RetryEntry] = []
        self.max_attempts = max_attempts

    def push(self, chunk: str, issues: List[Dict[str, str]],
             entry_id: str = "") -> RetryEntry:
        """Push a SEVERE interrupt — will be retried."""
        entry = RetryEntry(
            id=entry_id or _gen_id(),
            chunk=chunk,
            issues=issues,
            attempt=1,
            max_attempts=self.max_attempts,
        )
        self.entries.append(entry)
        return entry

    def push_minor(self, chunk: str, issues: List[Dict[str, str]]) -> None:
        """Push a MINOR issue — recorded for post-generation supplement."""
        entry = RetryEntry(
            id=_gen_id(),
            chunk=chunk,
            issues=issues,
            is_minor=True,
        )
        self.entries.append(entry)

    def resolve(self, entry_id: str) -> None:
        """Mark a retry entry as resolved."""
        for e in self.entries:
            if e.id == entry_id:
                e.resolved = True
                break

    def fail(self, entry_id: str) -> None:
        """Mark a retry entry as failed (exhausted retries)."""
        for e in self.entries:
            if e.id == entry_id:
                e.failed = True
                break

    def increment(self, entry_id: str) -> int:
        """Increment attempt counter; returns new attempt number."""
        for e in self.entries:
            if e.id == entry_id:
                e.attempt += 1
                return e.attempt
        return 1

    def can_retry(self, entry_id: str) -> bool:
        """Check if entry still has retries left."""
        for e in self.entries:
            if e.id == entry_id:
                return e.attempt < e.max_attempts and not e.resolved
        return False

    def correction_prompt(self, entry: RetryEntry) -> str:
        """Build a correction prompt for an LLM retry call."""
        issues_text = "\n".join(
            f"- [{i.get('type', 'issue')}] {i.get('detail', '')}"
            for i in entry.issues
        )
        return (
            "你之前写了一段话，但存在以下问题：\n"
            f"{issues_text}\n\n"
            "请仅修正以下文本，保持上下文的连贯性：\n"
            f"{entry.chunk}\n\n"
            "修正版本："
        )

    def build_supplement(self) -> str:
        """Build post-generation supplement from MINOR entries."""
        minors = [e for e in self.entries if e.is_minor and not e.resolved]
        if not minors:
            return ""
        parts = []
        for e in minors:
            for issue in e.issues:
                t = issue.get("type", "")
                d = issue.get("detail", "")
                if t == "clarity":
                    parts.append(f"* 表达已优化：{d}")
                elif t == "unverified_fact":
                    parts.append(f"* 数据说明：{d}")
                elif t == "drift":
                    parts.append(f"* 补充说明：{d}")
        return "\n".join(parts) if parts else ""

    def has_unresolved(self) -> bool:
        """Check if any SEVERE entries are still unresolved."""
        return any(
            not e.is_minor and not e.resolved and not e.failed
            for e in self.entries
        )

    def reset(self) -> None:
        """Clear all entries."""
        self.entries = []


def _gen_id() -> str:
    return f"rq_{int(time.time() * 1000)}_{id({})}"
