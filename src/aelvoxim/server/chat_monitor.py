# SPDX-License-Identifier: MIT
"""
metacore.server.chat_monitor — Post-chat quality evaluation.

Inspired by MetaCore's metacog_monitor.py, adapted for pure-stdlib
architecture. Evaluates each chat turn on several dimensions and stores
the result as a JSONL record for trend analysis.

Zero external dependencies. Zero LLM calls. Does NOT import any old package code.

Data stored in: ~/.metacore/chat_monitor/YYYY-MM-DD.jsonl
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import DATA_DIR as _DATA_ROOT

_DATA_DIR = _DATA_ROOT / "chat_monitor"


def _daily_path(date_str: Optional[str] = None) -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    d = date_str or datetime.now().strftime("%Y-%m-%d")
    # Guard: only allow safe date pattern to prevent path traversal
    if date_str is not None and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        raise ValueError(f"Invalid date format: {date_str}")
    return _DATA_DIR / f"{d}.jsonl"


def _append(record: dict) -> None:
    fp = _daily_path()
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_day(date_str: Optional[str] = None) -> List[dict]:
    fp = _daily_path(date_str)
    if not fp.exists():
        return []
    try:
        text = fp.read_text(encoding="utf-8").strip()
        return [json.loads(line) for line in text.split("\n") if line.strip()]
    except Exception:
        return []


def evaluate_conversation(
    query: str,
    answer: str,
    user_id: str = "",
    knowledge_results: Optional[List[dict]] = None,
    learn_result: Optional[dict] = None,
    agent_status: Optional[dict] = None,
    response_time_ms: float = 0.0,
) -> dict:
    """Evaluate a single chat turn and persist the result.

    Args:
        query: The user's message text.
        answer: The assistant's reply text.
        user_id: Optional user identifier (first 16 chars stored).
        knowledge_results: List of KnowledgeBase search results.
        learn_result: Reserved for future learning value detection.
        agent_status: Reserved for future learner status reporting.
        response_time_ms: Wall-clock time for the LLM call.

    Returns:
        The evaluation record dict (also persisted to disk).
    """
    now = datetime.now()
    report: Dict[str, Any] = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time()),
        "user_id": (user_id or "anon")[:16],
        "query_len": len(query),
        "answer_len": len(answer),
        "response_time_ms": round(response_time_ms, 1),
    }

    # 1. Knowledge hit rate
    kb_hit = bool(knowledge_results and len(knowledge_results) > 0)
    kb_topics = []
    if knowledge_results:
        kb_topics = [e.get("topic", "?") for e in knowledge_results[:3]]
    report["knowledge"] = {
        "hit": kb_hit,
        "hit_count": len(knowledge_results) if knowledge_results else 0,
        "topics": kb_topics[:3],
    }

    # 2. Answer quality signals
    answer_ok = len(answer) > len(query) * 0.3 and len(answer) >= 20
    report["quality"] = {
        "answer_to_query_ratio": round(len(answer) / max(len(query), 1), 2),
        "adequate_length": answer_ok,
    }

    # 3. Learning value (placeholder — future use)
    report["learning"] = {
        "should_learn": bool(learn_result and learn_result.get("should_learn")),
        "topic": (learn_result or {}).get("topic", ""),
        "confidence": round(float((learn_result or {}).get("confidence", 0)), 2),
    }

    # 4. Agent health (placeholder — future use)
    report["agent_health"] = {
        "alive": bool(agent_status and agent_status.get("status") == "running"),
        "loops": (agent_status or {}).get("loops_completed", 0),
    }

    # 5. Overall robustness score (0-100)
    score = _calc_robustness_score(report)
    report["robustness_score"] = score
    report["level"] = _score_to_level(score)

    _append(report)
    return report


def _calc_robustness_score(report: dict) -> float:
    """Weighted robustness score. Baseline 50, bonuses up to +50."""
    score = 50.0

    if report.get("knowledge", {}).get("hit"):
        score += 15
    if report.get("quality", {}).get("adequate_length"):
        score += 15
    ratio = report.get("quality", {}).get("answer_to_query_ratio", 0)
    if 0.3 <= ratio <= 5.0:
        score += 10
    if report.get("learning", {}).get("should_learn"):
        score += 10
    if report.get("agent_health", {}).get("alive"):
        score += 10
    if report.get("response_time_ms", 99999) < 5000:
        score += 5
    if report.get("answer_len", 0) > 200:
        score += 5
    if report.get("query_len", 0) > 0:
        score += 5

    return min(score, 100)


def _score_to_level(score: float) -> str:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 50:
        return "fair"
    return "poor"


# ── Query helpers ───────────────────────────


def get_report(date_str: Optional[str] = None) -> dict:
    """Aggregated report for a single day."""
    records = _load_day(date_str)
    if not records:
        return {
            "date": date_str or datetime.now().strftime("%Y-%m-%d"),
            "total": 0,
        }

    scores = [r.get("robustness_score", 0) for r in records]
    levels = [r.get("level", "unknown") for r in records]
    hit_count = sum(1 for r in records if r.get("knowledge", {}).get("hit"))

    return {
        "date": date_str or datetime.now().strftime("%Y-%m-%d"),
        "total": len(records),
        "avg_robustness": round(sum(scores) / len(scores), 1),
        "min_score": min(scores),
        "max_score": max(scores),
        "level_distribution": {
            "excellent": levels.count("excellent"),
            "good": levels.count("good"),
            "fair": levels.count("fair"),
            "poor": levels.count("poor"),
        },
        "knowledge_hit_rate": round(hit_count / len(records) * 100, 1),
        "conversations": records[-50:],
    }


def get_trend(days: int = 7) -> dict:
    """Trend report over the last N days."""
    points = []
    for i in range(days - 1, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        r = get_report(d)
        if r["total"] > 0:
            points.append({
                "date": d,
                "total": r["total"],
                "avg_score": r["avg_robustness"],
                "hit_rate": r["knowledge_hit_rate"],
            })
    return {"days": days, "trend": points}


def get_summary(limit_days: int = 7) -> dict:
    """Global summary across the last N days."""
    all_records: List[dict] = []
    for i in range(limit_days - 1, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        all_records.extend(_load_day(d))

    if not all_records:
        return {"total": 0}

    scores = [r.get("robustness_score", 0) for r in all_records]
    levels = [r.get("level", "unknown") for r in all_records]
    hit = sum(1 for r in all_records if r.get("knowledge", {}).get("hit"))

    return {
        "total": len(all_records),
        "period": f"last_{limit_days}_days",
        "avg_robustness": round(sum(scores) / len(scores), 1),
        "level_distribution": {
            "excellent": levels.count("excellent"),
            "good": levels.count("good"),
            "fair": levels.count("fair"),
            "poor": levels.count("poor"),
        },
        "knowledge_hit_rate": round(hit / len(all_records) * 100, 1),
    }


def cleanup_old(keep_days: int = 30) -> int:
    """Remove JSONL files older than keep_days."""
    threshold = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    removed = 0
    for fp in _DATA_DIR.glob("*.jsonl"):
        if fp.stem < threshold:
            try:
                fp.unlink()
                removed += 1
            except Exception:
                pass
    return removed


# ── Meta-cognition log ──


def generate_metacog_log(
    query: str,
    answer: str,
    user_id: str = "",
    db_path: str = "",
    previous_suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate a meta-cognition log entry after each conversation.

    Scans memory DB for entities that were created or updated during
    this conversation, compares confidence before/after, and identifies
    blind spots and contradictions.

    The log is stored as a semantic memory entry (key=metacog:ts).

    Returns the log dict.
    """
    import json as _js, sqlite3 as _sq
    from datetime import datetime as _dt
    from pathlib import Path

    if not db_path:
        from ..utils import METACORE_DIR as _md
        db_path = str(Path(_md) / "memory.db")

    now = _dt.now()
    log: Dict[str, Any] = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": f"sess_{now.strftime('%H%M%S')}",
        "new_knowledge": [],
        "updated_knowledge": [],
        "blind_spots": [],
        "contradictions": [],
        "suggested_next_actions": [],
        "prev_suggestions_fulfilled": False,
    }

    try:
        _uid = user_id if user_id else ""
        db = _sq.connect(db_path)
        db.row_factory = _sq.Row

        # Scan recent entities for this user
        rows = db.execute(
            "SELECT id, value, attributes, created_at FROM entities WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (_uid,)
        ).fetchall()

        for row in rows:
            attrs = _js.loads(row["attributes"] or "{}")
            cm = attrs.get("confidence_metadata", {})
            overall = cm.get("overall", 0.5) if isinstance(cm, dict) else 0.5
            value = (row["value"] or "")[:80]
            if not value:
                continue

            # Superseded = updated knowledge
            if attrs.get("_superseded"):
                log["updated_knowledge"].append({
                    "content": value,
                    "old_value": str(attrs["_superseded"])[:80],
                    "overall": overall,
                })
            elif overall >= 0.7 and row["created_at"]:
                # Check if created very recently
                try:
                    cts = _dt.strptime(str(row["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
                    if (_dt.now() - cts).total_seconds() < 300:  # within 5 min
                        log["new_knowledge"].append({
                            "content": value,
                            "overall": overall,
                            "source": attrs.get("source", "chat"),
                        })
                except Exception:
                    pass

            # Blind spots (low confidence extracted entities)
            if overall < 0.6 and "extracted" in (attrs.get("tags") or ""):
                log["blind_spots"].append({
                    "topic": value,
                    "overall": overall,
                })

            # Contradictions
            if attrs.get("_conflict") and not attrs.get("_conflict_resolved"):
                log["contradictions"].append({
                    "key": row["id"],
                    "old_value": str(attrs.get("_conflict_old", ""))[:80],
                    "new_value": str(attrs.get("_conflict_new", ""))[:80],
                })

        db.close()

        # Check previous suggestions
        if previous_suggestions:
            # Simplified: check if any blind spot topics were resolved
            for suggestion in previous_suggestions:
                for spot in log["blind_spots"]:
                    if suggestion.get("topic", "").lower() in spot.get("topic", "").lower():
                        if spot["overall"] >= 0.7:
                            log["prev_suggestions_fulfilled"] = True
                            break

        # Generate next actions
        if log["blind_spots"]:
            top_spot = log["blind_spots"][0]
            log["suggested_next_actions"].append(
                f"ask_about: {top_spot['topic']} (confidence {top_spot['overall']:.2f})"
            )
        if log["contradictions"]:
            c = log["contradictions"][0]
            log["suggested_next_actions"].append(
                f"resolve_conflict: {c['key']} ({c['old_value']} vs {c['new_value']})"
            )

    except Exception:
        pass

    return log


def store_metacog_log(log: Dict[str, Any], user_id: str = "") -> bool:
    """Store a meta-cognition log as a semantic memory entry."""
    try:
        from ..memory import store_entity
        key = f"metacog:{log['ts'].replace(' ', '-').replace(':', '')}"
        store_entity(
            eid=key,
            etype="metacognition",
            attributes={"log": log, "name": key},
            tags=["metacognition", "system", "log"],
            user_id=user_id,
        )
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# Meta-learning: FeedbackExtractor — extract learning signals from user chat
# ══════════════════════════════════════════════════════════════

_CORRECTION_PATTERNS = [
    re.compile(r"(?i)(不对|不是|错了|才没有|你错了|wrong|incorrect|no[,.]?|actually)"),
    re.compile(r"(?i)(应该是|正确的(?:是|应该是)|其实就是)"),
    re.compile(r"(?i)(不是吗|你确定\?|真的吗)"),
]

_REPEAT_QUESTION_PATTERNS = [
    re.compile(r"(?i)(重新|再说|再讲|又(?:是|说|问)|还是不懂|还没懂|again|repeat|retry)"),
]


def extract_feedback_signals(query: str, answer: str) -> dict:
    """Extract learning signals from conversation content. Pure rules, no LLM.

    Returns:
        dict with detected signals:
        {
            "correction_detected": bool,
            "correction": {"old_term": str, "new_term": str} | None,
            "repeat_question": bool,
            "raw_topic": str,
        }
    """
    signals: Dict[str, Any] = {}

    if not query:
        return signals

    # 1. Correction tone detection
    for pattern in _CORRECTION_PATTERNS:
        match = pattern.search(query)
        if match:
            signals["correction_detected"] = True
            signals["trigger_phrase"] = match.group()
            break

    # 2. Explicit correction extraction: "not A but B"
    correction_match = re.search(
        r"(?i)不是\s*['\u201c]?(.+?)['\u201d]?\s*而是\s*['\u201c]?(.+?)['\u201d]?(?:\s*[。，]|$)",
        query,
    )
    if correction_match:
        signals["correction"] = {
            "old_term": correction_match.group(1).strip(),
            "new_term": correction_match.group(2).strip(),
        }

    # 3. Repeat question detection
    for pattern in _REPEAT_QUESTION_PATTERNS:
        if pattern.search(query):
            signals["repeat_question"] = True
            break

    # 4. Rough topic extraction (strip question words, keep nouns)
    raw_topic = re.sub(
        r"(?i)\b(什么是|如何|怎么|怎样|为什么|哪里|哪个|多少|能否|请|帮|帮我)\b",
        "", query
    ).strip()
    if raw_topic and len(raw_topic) > 2:
        signals["raw_topic"] = raw_topic[:80]

    return signals
