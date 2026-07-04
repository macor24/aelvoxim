# -*- coding: utf-8 -*-
"""
metacore.hooks.analyzer — Failure analysis for data feedback loop

Reads from outcomes.jsonl, groups by error pattern,
cross-references with SelfModel snapshots, produces FailureReport.

Zero dependencies, pure stdlib.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR

_OUTCOME_FILE = METACORE_DIR / "hooks" / "outcomes.jsonl"
_DEFAULT_LOOKBACK = 50


class FailurePattern:
    """A single failure pattern with frequency and suggestion."""

    def __init__(self, pattern: str, count: int, total: int,
                 examples: List[str], suggestion: str = ""):
        self.pattern = pattern
        self.count = count
        self.frequency = round(count / total, 4) if total else 0
        self.examples = examples[:3]
        self.suggestion = suggestion

    def to_dict(self) -> Dict:
        return {
            "pattern": self.pattern,
            "count": self.count,
            "frequency": self.frequency,
            "examples": self.examples,
            "suggestion": self.suggestion,
        }


class FailureReport:
    """Aggregated failure analysis result."""

    def __init__(self, total: int, failures: int,
                 patterns: List[FailurePattern],
                 time_window: Tuple[float, float]):
        self.total_outcomes = total
        self.failure_count = failures
        self.failure_rate = round(failures / total, 4) if total else 0.0
        self.top_patterns = patterns[:5]
        self.time_window_start = time_window[0]
        self.time_window_end = time_window[1]

    def to_dict(self) -> Dict:
        return {
            "total_outcomes": self.total_outcomes,
            "failure_count": self.failure_count,
            "failure_rate": self.failure_rate,
            "top_patterns": [p.to_dict() for p in self.top_patterns],
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
        }

    def should_trigger_tuning(self, threshold: float = 0.3) -> bool:
        """Return True if failure rate exceeds threshold."""
        return self.failure_rate > threshold


# ── Built-in error pattern matchers ─────────


def _match_error_pattern(detail: str) -> Optional[str]:
    """Extract error pattern from a task outcome detail string."""
    if not detail:
        return None
    detail_lower = detail.lower()

    patterns = [
        (r"llm.*timeout|timeout.*llm|llm.*connect|connect.*llm", "llm_connection"),
        (r"api.*limit|rate.*limit|429|too many", "rate_limited"),
        (r"timeout|timed out", "timeout_generic"),
        (r"not found|404", "not_found"),
        (r"auth|401|403|unauthorized|forbidden", "authentication"),
        (r"empty|no content|null|none", "empty_response"),
        (r"invalid|bad request|400", "invalid_request"),
        (r"parse|json.*error|decode", "parsing_error"),
        (r"search.*fail|search.*error|engine.*down", "search_failure"),
        (r"knowledge.*empty|no.*knowledge|kb.*miss", "knowledge_miss"),
        (r"extract.*fail|extract.*error|no.*content", "extraction_failure"),
        (r"validate.*fail|.*reject|.*score.*low", "validation_failure"),
        (r"memory.*full|disk.*full|no.*space", "resource_exhaustion"),
    ]
    for regex, label in patterns:
        if re.search(regex, detail_lower):
            return label
    return "unknown"


def _generate_suggestion(pattern: str) -> str:
    """Generate a tuning suggestion for a known pattern."""
    suggestions = {
        "llm_connection": "Increase LLM timeout or add retry with fallback provider",
        "rate_limited": "Reduce request frequency or add exponential backoff",
        "timeout_generic": "Increase operation timeout or split into smaller tasks",
        "not_found": "Verify resource path before access",
        "authentication": "Check API key validity and expiration",
        "empty_response": "Add retry with different query or fallback to cached data",
        "invalid_request": "Validate input parameters before request",
        "parsing_error": "Add input validation and sanitization",
        "search_failure": "Fallback to alternate search engine in chain",
        "knowledge_miss": "Expand search scope or add auto-discover for new topics",
        "extraction_failure": "Use more specific query or add LLM re-prompt",
        "validation_failure": "Lower validation threshold or add human review step",
        "resource_exhaustion": "Increase storage limit or enable auto-cleanup",
    }
    return suggestions.get(pattern, "Investigate and fix manually")


# ── Core analysis function ────────────────


def analyze(lookback: int = _DEFAULT_LOOKBACK) -> FailureReport:
    """Analyze recent outcomes and produce a FailureReport.

    Args:
        lookback: Number of most recent outcomes to analyze.

    Returns:
        FailureReport with top failure patterns.
    """
    outcomes = _read_outcomes(lookback)
    if not outcomes:
        return FailureReport(0, 0, [], (0, 0))

    total = len(outcomes)
    failures = [o for o in outcomes if not o.get("success", True)]
    failure_count = len(failures)
    time_window = (outcomes[-1]["timestamp"], outcomes[0]["timestamp"]) if len(outcomes) > 1 else (0, 0)

    # Group failures by error pattern
    pattern_counter: Counter = Counter()
    pattern_examples: Dict[str, List[str]] = {}
    for f in failures:
        detail = f.get("detail", "")
        pattern = _match_error_pattern(detail) or "unknown"
        pattern_counter[pattern] += 1
        if pattern not in pattern_examples:
            pattern_examples[pattern] = []
        if len(pattern_examples[pattern]) < 3:
            example = detail[:120] if detail else "(no detail)"
            pattern_examples[pattern].append(example)

    patterns = [
        FailurePattern(
            pattern=p,
            count=c,
            total=total,
            examples=pattern_examples.get(p, []),
            suggestion=_generate_suggestion(p),
        )
        for p, c in pattern_counter.most_common(10)
    ]

    return FailureReport(total, failure_count, patterns, time_window)


def analyze_since(timestamp: float, lookback: int = 200) -> FailureReport:
    """Analyze outcomes since a specific timestamp."""
    outcomes = _read_outcomes(lookback)
    outcomes = [o for o in outcomes if o.get("timestamp", 0) >= timestamp]
    if not outcomes:
        return FailureReport(0, 0, [], (0, 0))
    total = len(outcomes)
    failures = [o for o in outcomes if not o.get("success", True)]
    failure_count = len(failures)
    # simplified for timestamp-filtered case
    patterns = []
    if failure_count > 0:
        counter: Counter = Counter()
        for f in failures:
            detail = f.get("detail", "")
            p = _match_error_pattern(detail) or "unknown"
            counter[p] += 1
        patterns = [
            FailurePattern(p, c, total, [], _generate_suggestion(p))
            for p, c in counter.most_common(5)
        ]
    return FailureReport(total, failure_count, patterns,
                         (outcomes[-1]["timestamp"], outcomes[0]["timestamp"]))


def _read_outcomes(lookback: int) -> List[Dict]:
    """Read the last N outcome records from the JSONL file."""
    if not _OUTCOME_FILE.exists():
        return []
    try:
        with open(_OUTCOME_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        outcomes = []
        for line in lines[-lookback:]:
            line = line.strip()
            if line:
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return outcomes
    except (OSError, IOError):
        return []
