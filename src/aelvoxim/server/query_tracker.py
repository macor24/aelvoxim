# SPDX-License-Identifier: MIT
"""
metacore.server.query_tracker — Track user query topics for behavior prediction.

Records each /v1/llm/chat query topic, analyzes patterns over time.
Predicts what the user might need next based on:
1. Topic repetition (same topic 3+ times in a row)
2. Topic co-occurrence (A often followed by B)
3. Periodic patterns (same topic at similar times)

Data stored in ~/.metacore/query_tracker/<date>.jsonl
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR

_TRACKER_DIR = Path(METACORE_DIR) / "query_tracker"
_COOLDOWN_MINUTES = 30  # Within this window, same-topic repeats are deduplicated


def _ensure_dir() -> Path:
    _TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    return _TRACKER_DIR


def _today_path() -> Path:
    return _ensure_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def _extract_topic(text: str) -> str:
    """Extract a rough topic from a user query.

    Uses the first meaningful English/Chinese keyword.
    Falls back to 'general' for very short queries.
    """
    if not text:
        return "general"
    text = text.strip().lower()
    if len(text) < 5:
        return "general"
    # Try first 2-3 words for English
    words = text.split()
    if words and all(c.isascii() for c in words[0]):
        topic = " ".join(words[:3])
        return topic[:50]
    # Chinese: take first meaningful 8 chars
    cn = [c for c in text if '\u4e00' <= c <= '\u9fff']
    if cn:
        return "".join(cn[:6])
    return text[:30]


def record_query(query: str, user_id: str = "") -> str:
    """Record a user query for behavior analysis.

    Args:
        query: The user's message text.
        user_id: Optional user identifier.

    Returns:
        The extracted topic string.
    """
    topic = _extract_topic(query)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        path = _today_path()
        # Dedup: skip if same topic within cooldown
        if path.exists():
            lines = path.read_text().strip().split("\n")
            if lines:
                last = json.loads(lines[-1])
                last_ts = datetime.strptime(last.get("ts", "1970-01-01")[:19], "%Y-%m-%d %H:%M:%S")
                if last.get("topic") == topic and (datetime.now() - last_ts).total_seconds() < _COOLDOWN_MINUTES * 60:
                    return topic
        with open(str(path), "a") as f:
            f.write(json.dumps({
                "ts": now,
                "topic": topic,
                "user_id": user_id,
                "raw_prefix": query[:50],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return topic


def get_recent_topics(hours: int = 24, limit: int = 50) -> List[Dict]:
    """Get topics from the last N hours."""
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    results = []
    for f in sorted(_ensure_dir().glob("*.jsonl"), reverse=True):
        try:
            for line in f.read_text().strip().split("\n"):
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("ts", "") >= cutoff:
                    results.append(entry)
                if len(results) >= limit:
                    break
        except Exception:
            pass
        if len(results) >= limit:
            break
    return results


def predict_next_topics(hours: int = 48) -> Dict[str, Any]:
    """Predict topics the user might need next.

    Returns:
        {
            "hot_topics": [{"topic": "...", "count": N}, ...],
            "co_occurrences": [{"if": "A", "then": "B", "probability": 0.7}, ...],
            "predictions": ["topic1", "topic2", ...]  # most likely next
        }
    """
    entries = get_recent_topics(hours=hours, limit=200)

    # 1. Count topic frequency
    topic_counts: Dict[str, int] = Counter(e.get("topic", "general") for e in entries)
    hot_topics = [{"topic": t, "count": c} for t, c in topic_counts.most_common(10)]

    # 2. Co-occurrence: if A appears, what B appears within 5 entries after A?
    topics_seq = [e.get("topic", "general") for e in entries]
    co_oc: Dict[str, Counter] = defaultdict(Counter)
    for i, t in enumerate(topics_seq):
        for j in range(i + 1, min(i + 5, len(topics_seq))):
            nt = topics_seq[j]
            if nt != t:
                co_oc[t][nt] += 1
    co_occurrences = []
    for t, followers in co_oc.items():
        total = topic_counts.get(t, 1)
        for nt, cnt in followers.most_common(3):
            if cnt >= 2:  # Only meaningful patterns
                co_occurrences.append({
                    "if": t,
                    "then": nt,
                    "probability": round(cnt / total, 2),
                })

    # 3. Predictions: hot topics + co-occurrence from last query
    predictions: List[str] = []
    for ht in hot_topics[:3]:
        predictions.append(ht["topic"])
    if topics_seq:
        last = topics_seq[-1]
        for co in co_occurrences:
            if co["if"] == last:
                predictions.append(co["then"])
    # Deduplicate
    seen = set()
    deduped = []
    for p in predictions:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return {
        "hot_topics": hot_topics,
        "co_occurrences": co_occurrences[:5],
        "predictions": deduped[:5],
        "total_queries": len(entries),
    }


def get_trend(days: int = 7) -> List[Dict]:
    """Get daily topic count trend for the last N days."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    daily: Dict[str, Counter] = defaultdict(Counter)
    for f in sorted(_ensure_dir().glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        try:
            for line in f.read_text().strip().split("\n"):
                if not line:
                    continue
                e = json.loads(line)
                day = e.get("ts", "")[:10]
                daily[day][e.get("topic", "general")] += 1
        except Exception:
            pass
    return [{"date": d, "topics": dict(c)} for d, c in sorted(daily.items())]
