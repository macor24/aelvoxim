"""
metacore.memory.conf_matrix — 5-dimension confidence metadata for memory entries.

Replaces the single-score confidence with a structured 5-dimension evaluation:

  source_reliability  0.0-1.0  How reliable is the information source
  recency             0.0-1.0  How fresh is the information
  consistency         0.0-1.0  Does it agree with historical records
  granularity         0.0-1.0  How detailed is the information
  cross_validation    0.0-1.0  How many times has it been confirmed

Overall = weighted combination:
  0.30*source + 0.20*recency + 0.20*consistency + 0.15*granularity + 0.15*cross_validation

Stored as confidence_metadata dict inside entity attributes JSON.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ── Weights ──

OVERALL_WEIGHTS = {
    "source_reliability": 0.30,
    "recency": 0.20,
    "consistency": 0.20,
    "granularity": 0.15,
    "cross_validation": 0.15,
}

# Source reliability scores
SOURCE_SCORES = {
    "user_direct": 0.95,       # User explicitly stated it
    "user_indirect": 0.70,     # User mentioned indirectly
    "user_corrected": 0.90,    # User corrected previous info → high trust
    "ai_inferred": 0.50,       # AI deduced from context
    "kb": 0.80,                # Knowledge base entry
    "migration": 0.50,         # Migrated from legacy storage
    "chat": 0.60,              # Extracted from chat context
    "system": 0.40,            # Auto-generated system record
}

# Granularity: auto-scored based on content characteristics
_GRANULARITY_HIGH_KEYWORDS = {"具体", "详细", "精确", "exact", "specific", "full", "完整"}
_GRANULARITY_LOW_KEYWORDS = {"大概", "可能", "也许", "maybe", "some", "一点点"}


def _source_classify(tags: List[str], source: str) -> str:
    """Classify the source type for scoring."""
    if "user_corrected" in tags or "corrected" in tags:
        return "user_corrected"
    if "kb" in tags or source == "manual":
        return "kb"
    if "ai_inferred" in tags or source == "agent":
        return "ai_inferred"
    if source in ("migration", "db_reload"):
        return "migration"
    if source in ("system", "auto"):
        return "system"
    if "extracted" in tags or source == "chat":
        return "chat"
    return "chat"


def _score_recency(timestamp_str: str) -> float:
    """Score recency based on timestamp. Today=1.0, >365d=0.1."""
    if not timestamp_str:
        return 0.5
    try:
        ts = datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
        days_old = (datetime.now() - ts).total_seconds() / 86400
    except Exception:
        return 0.5
    if days_old <= 1:
        return 1.0
    if days_old <= 7:
        return 0.9
    if days_old <= 30:
        return 0.7
    if days_old <= 90:
        return 0.5
    if days_old <= 365:
        return 0.3
    return 0.1


def _score_granularity(value: str) -> float:
    """Score how detailed the information is."""
    if not value:
        return 0.3
    v = str(value)
    length = len(v)
    # Very detailed
    if length >= 50:
        base = 0.9
    elif length >= 20:
        base = 0.7
    elif length >= 8:
        base = 0.6
    elif length >= 3:
        base = 0.5
    else:
        base = 0.3
    # Bonus for high-granularity keywords
    for kw in _GRANULARITY_HIGH_KEYWORDS:
        if kw in v:
            base = min(1.0, base + 0.1)
            break
    # Penalty for vague keywords
    for kw in _GRANULARITY_LOW_KEYWORDS:
        if kw in v:
            base = max(0.2, base - 0.15)
            break
    return base


def _score_cross_validation(mention_count: int, source: str = "") -> float:
    """Score based on how many times the info has been confirmed.
    User-direct confirmed info gets a boost to quickly separate known from unsure."""
    base = 0.1
    if mention_count >= 5:
        base = 1.0
    elif mention_count >= 3:
        base = 0.8
    elif mention_count >= 2:
        base = 0.6
    elif mention_count >= 1:
        base = 0.3
    # User-direct source: boost to quickly separate known vs unsure
    if source == "user_direct":
        base = max(base, 0.6)
    return base


# ── Public API ──


def compute_5d(
    tags: List[str],
    source: str = "",
    value: str = "",
    timestamp_str: str = "",
    mention_count: int = 1,
    has_conflict: bool = False,
) -> Dict[str, float]:
    """Compute 5-dimension confidence metadata for a memory entry.

    Args:
        tags: Entity tags (person, location, extracted, etc.)
        source: Source string (chat, manual, system, etc.)
        value: Entity value text.
        timestamp_str: ISO timestamp of when this was created.
        mention_count: How many times confirmed.
        has_conflict: Whether this entity has unresolved conflict.

    Returns:
        Dict with all 5 dimensions + overall score.
    """
    source_class = _source_classify(tags, source)
    sr = SOURCE_SCORES.get(source_class, 0.5)
    rec = _score_recency(timestamp_str)
    con = 0.3 if has_conflict else 0.9  # Conflict = low consistency
    gran = _score_granularity(value)
    cv = _score_cross_validation(mention_count, source=source_class)

    overall = (
        OVERALL_WEIGHTS["source_reliability"] * sr
        + OVERALL_WEIGHTS["recency"] * rec
        + OVERALL_WEIGHTS["consistency"] * con
        + OVERALL_WEIGHTS["granularity"] * gran
        + OVERALL_WEIGHTS["cross_validation"] * cv
    )

    return {
        "source_reliability": round(sr, 2),
        "recency": round(rec, 2),
        "consistency": round(con, 2),
        "granularity": round(gran, 2),
        "cross_validation": round(cv, 2),
        "overall": spread_confidence(overall),
    }


def spread_confidence(overall: float) -> float:
    """Apply adaptive scaling to prevent score collapse into a narrow band.
    
    The raw weighted-average formula tends to cluster all entities in 0.60-0.70.
    This spreads scores toward the ends so blind-spot and known detection can work.
    
    Strategy: if overall is in the middle band (0.5-0.8), pull it toward the
    extremes by scaling the distance from 0.5:
      below 0.5: keep as-is (already clearly low)
      0.50-0.65: pull down toward 0.50
      0.65-0.80: pull up toward 0.80
      above 0.80: keep as-is (already clearly high)
    """
    if overall < 0.50:
        return overall  # already clearly low
    if overall > 0.80:
        return overall  # already clearly high
    # Middle band: spread from center (0.65)
    center = 0.65
    spread = 0.15  # how far to spread
    if overall <= center:
        # Pull toward 0.50
        ratio = (overall - 0.50) / (center - 0.50) if center > 0.50 else 0
        return round(0.50 + ratio * 0.05, 2)  # 0.50 → 0.55 range
    else:
        # Pull toward 0.80
        ratio = (overall - center) / (0.80 - center) if 0.80 > center else 0
        return round(0.75 + ratio * 0.05, 2)  # 0.75 → 0.80 range


def overall_from_5d(meta: Dict[str, float]) -> float:
    """Recalculate overall from existing metadata dict."""
    if not meta:
        return 0.5
    raw = round(
        sum(OVERALL_WEIGHTS.get(k, 0.1) * v for k, v in meta.items() if k != "overall"),
        2,
    )
    return spread_confidence(raw)


def confidence_label(overall: float) -> str:
    """Map overall score to a human label."""
    if overall >= 0.9:
        return "very_high"
    if overall >= 0.7:
        return "high"
    if overall >= 0.5:
        return "medium"
    if overall >= 0.3:
        return "low"
    return "very_low"


__all__ = [
    "compute_5d", "overall_from_5d", "confidence_label",
    "OVERALL_WEIGHTS", "SOURCE_SCORES",
]
