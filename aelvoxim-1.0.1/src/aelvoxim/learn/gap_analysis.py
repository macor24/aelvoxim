# SPDX-License-Identifier: MIT
"""
metacore.learn.gap_analysis — Identify knowledge gaps in the knowledge base.

Pure statistical analysis (no LLM). Detects:
1. Directions that are saturated (saturation > 0.8) but have few entries
2. Topics users frequently ask about that have zero KB coverage
3. Active directions that haven't produced new entries in 7+ days
4. Topics with too few entries (< 5) suggesting shallow coverage

Outputs gap recommendations that the Learner can act on.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR


def analyze_knowledge_gaps(
    directions: Dict[str, Any],
    knowledge_topics: Dict[str, int],
    query_topics: List[str],
    min_entries_threshold: int = 5,
    saturation_threshold: float = 0.8,
) -> Dict[str, Any]:
    """Analyze knowledge gaps and return recommendations.

    Args:
        directions: Dict of {topic: direction_object}
        knowledge_topics: Dict of {topic: entry_count}
        query_topics: List of recent user query topic strings
        min_entries_threshold: Minimum entries per topic before considered adequate
        saturation_threshold: Saturation level above which a direction is considered done

    Returns:
        {
            "gaps": [{"topic": str, "reason": str, "detail": str}, ...],
            "recommendations": [str, ...],  # suggested new directions
            "stale_directions": [str, ...],  # active but stalled
            "shallow_topics": [str, ...],  # have entries but < threshold
        }
    """
    gaps: List[Dict] = []
    recommendations: List[str] = []
    stale_directions: List[str] = []
    shallow_topics: List[str] = []

    # 1. Check completed directions with high saturation but low entries
    for topic, d in directions.items():
        entry_count = knowledge_topics.get(topic, 0)
        saturation = getattr(d, 'saturation', 0) if hasattr(d, 'saturation') else 0
        status = getattr(d, 'status', '') if hasattr(d, 'status') else ''

        if status == "completed" and saturation >= saturation_threshold and entry_count < min_entries_threshold:
            gaps.append({
                "topic": topic,
                "reason": "saturated_low_entries",
                "detail": f"{entry_count} entries, saturation {saturation:.0%}",
            })
            recommendations.append(f"Re-activate direction: {topic} (saturated at {entry_count} entries)")

        # 2. Active directions that stalled (no entries)
        if status == "active" and entry_count == 0:
            # Check if started long ago by examining if entries ever existed
            started = getattr(d, 'started_at', '') if hasattr(d, 'started_at') else ''
            if started:
                try:
                    started_dt = datetime.strptime(str(started)[:19], "%Y-%m-%d %H:%M:%S")
                    days_idle = (datetime.now() - started_dt).days
                    if days_idle >= 7:
                        stale_directions.append(topic)
                        gaps.append({
                            "topic": topic,
                            "reason": "stalled_active",
                            "detail": f"active {days_idle}d with 0 entries, suggest engine switch",
                        })
                except Exception:
                    pass

    # 3. User query topics with no KB coverage
    query_counts = Counter(query_topics)
    for topic, count in query_counts.most_common(20):
        if count >= 3:  # At least 3 queries on this topic
            # Check if any KB topic contains this query topic
            matched = False
            topic_lower = topic.lower()
            for kb_topic in knowledge_topics:
                if topic_lower in kb_topic.lower() or kb_topic.lower() in topic_lower:
                    matched = True
                    break
            if not matched:
                gaps.append({
                    "topic": topic,
                    "reason": "user_demand_no_coverage",
                    "detail": f"queried {count} times, 0 KB entries",
                })
                recommendations.append(f"Create direction from user demand: {topic}")

    # 4. Shallow topics (< threshold entries)
    for topic, count in knowledge_topics.items():
        if count < min_entries_threshold and count > 0:
            shallow_topics.append(topic)
            gaps.append({
                "topic": topic,
                "reason": "shallow_coverage",
                "detail": f"{count} entries below {min_entries_threshold} threshold",
            })

    return {
        "gaps": gaps[:20],
        "recommendations": list(set(recommendations))[:10],
        "stale_directions": stale_directions,
        "shallow_topics": shallow_topics[:10],
        "gap_count": len(gaps),
    }


def get_blind_spots_for_user(
    user_id: str,
    db_path: str = "",
    min_confidence: float = 0.65,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """Get low-confidence entities and missing knowledge for a user.

    Scans memory DB for user entities with confidence < min_confidence
    or missing confidence_metadata, returning them as blind spots
    suitable for injection into identity_prefix.

    Args:
        user_id: User identifier (e.g. 'user:xxx').
        db_path: Path to memory.db. Auto-detected if empty.
        min_confidence: Entities below this are considered blind spots.
        max_items: Max items to return.

    Returns:
        List of dicts: [{key, value, overall, label}, ...]
    """
    import json as _js, sqlite3 as _sq
    from pathlib import Path

    if not db_path:
        from ..utils import METACORE_DIR as _md
        db_path = str(Path(_md) / "memory.db")

    results: List[Dict] = []
    try:
        db = _sq.connect(db_path)
        db.row_factory = _sq.Row
        _uid = user_id if user_id else ""
        rows = db.execute(
            "SELECT id, value, attributes FROM entities WHERE user_id = ? AND tags LIKE ? ORDER BY created_at DESC LIMIT 50",
            (_uid, "%extracted%")
        ).fetchall()
        db.close()

        for row in rows:
            attrs = _js.loads(row["attributes"] or "{}")
            cm = attrs.get("confidence_metadata", {})
            overall = cm.get("overall", 0.5) if isinstance(cm, dict) else 0.5
            if overall < min_confidence:
                from ..memory.conf_matrix import confidence_label as _cl
                results.append({
                    "key": row["id"],
                    "value": (row["value"] or "")[:80],
                    "overall": overall,
                    "label": _cl(overall),
                })
                if len(results) >= max_items:
                    break
    except Exception:
        pass
    return results
