"""
metacore.experts.memory — Memory Expert.

Calls existing metacore.memory API to retrieve relevant entities,
events, and confidence metadata for the user's query.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register

from aelvoxim.memory import search_events


# ── Freshness classification ─────────────────────────────────

_FRESHNESS_CUTOFFS = {
    "fresh": 3600 * 24,        # < 1 day
    "recent": 3600 * 24 * 7,   # < 7 days
    "mature": 3600 * 24 * 30,  # < 30 days
    # > 30 days = historical
}


def _get_freshness(created_at) -> str:
    """Classify entry freshness based on created_at timestamp.

    Returns one of: fresh, recent, mature, historical, unknown
    """
    if not created_at:
        return "unknown"
    try:
        import time
        now = time.time()
        age = now - float(created_at) if isinstance(created_at, (int, float)) else now
        if age < _FRESHNESS_CUTOFFS["fresh"]:
            return "fresh"
        elif age < _FRESHNESS_CUTOFFS["recent"]:
            return "recent"
        elif age < _FRESHNESS_CUTOFFS["mature"]:
            return "mature"
        return "historical"
    except (ValueError, TypeError, OSError):
        return "unknown"


def _run_fusion_search(query: str, user_id: str, limit: int = 10) -> list:
    """Run fusion search (inverted index + layer priority).

    Falls back to standard entity search if fusion is unavailable.
    """
    try:
        from aelvoxim.memory.fusion import MemoryFusion
        fusion = MemoryFusion()
        results = fusion.search(query=query, limit=limit)
        if results:
            return results
    except Exception:
        pass
    # Fallback: standard entity search with user_id
    try:
        from aelvoxim.memory import search_entities
        return search_entities(query=query, limit=limit, user_id=user_id)
    except Exception:
        return []


def _detect_entry_layer(entry: dict) -> str:
    """Detect which memory layer an entry belongs to.

    Works with both fusion results and raw entities.
    Fusion results may have a 'layer' key; raw entities use key prefixes.
    """
    # Fusion results carry layer info directly
    layer = entry.get("layer", "")
    if layer and layer in ("working", "episodic", "semantic", "procedural"):
        return layer

    # Fallback: key prefix heuristics
    key = str(entry.get("key", "") or entry.get("id", ""))
    if key.startswith(("working:", "working_")):
        return "working"
    if key.startswith(("episodic:", "episodic_")):
        return "episodic"
    if key.startswith(("semantic:", "semantic_")):
        return "semantic"
    if key.startswith(("procedural:", "procedural_")):
        return "procedural"

    # Heuristic: locked or high-confidence entities are procedural/semantic
    attrs = entry.get("attributes", {})
    if isinstance(attrs, dict) and attrs.get("locked"):
        return "procedural"
    return "semantic"


def _run_conflict_check(entities: list) -> list:
    """Check retrieved entities for mutual contradictions.

    Returns list of conflict dicts: [{e1_key, e2_key, reason, severity}]
    """
    try:
        from aelvoxim.memory.conflict import ConflictDetector
        detector = ConflictDetector()
        # ConflictDetector.check_entities returns list of conflict records
        conflicts = detector.check_entities(entities)
        return conflicts if conflicts else []
    except Exception:
        return []


@register
class MemoryExpert(BaseExpert):
    """Retrieves user-specific memory entities, events, and confidence scores."""
    _capabilities = ["memory", "retrieval", "entity", "context"]

    name = "memory"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if another expert (safety/ethics) has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "entities": [],
            "events": [],
            "blind_spots": [],
            "confidence_summary": {},
            "layers": {},
            "conflicts": [],
            "freshness": {},
        }

        # 1. Fusion search (layer-aware, inverted index)
        all_entities = _run_fusion_search(inp.query, inp.user_id, limit=12)

        # Layer breakdown
        from collections import defaultdict
        layer_buckets: Dict[str, list] = defaultdict(list)
        for e in all_entities:
            layer = _detect_entry_layer(e)
            layer_buckets[layer].append(e)
        details["layers"] = {k: len(v) for k, v in layer_buckets.items()}

        # Freshness tagging
        freshness_counts: Dict[str, int] = defaultdict(int)
        for e in all_entities:
            f = _get_freshness(e.get("created_at"))
            freshness_counts[f] += 1
            if isinstance(e, dict):
                e["_freshness"] = f
        details["freshness"] = dict(freshness_counts)

        # Conflict detection
        details["conflicts"] = _run_conflict_check(all_entities)

        # Format entities for output
        if all_entities:
            details["entities"] = [
                {
                    "key": e.get("id", "") or e.get("key", ""),
                    "value": str(e.get("value", ""))[:80],
                    "type": e.get("type", ""),
                    "layer": _detect_entry_layer(e),
                    "freshness": _get_freshness(e.get("created_at")),
                    "confidence": _extract_overall(e),
                }
                for e in all_entities
            ]

        # 2. Search events (unchanged)
        try:
            events = search_events(query=inp.query, limit=5)
            if events:
                details["events"] = [
                    {"type": e.get("type", ""), "content": str(e.get("content", ""))[:100]}
                    for e in events
                ]
        except Exception:
            pass

        # 3. Detect blind spots (low-confidence entities for this user)
        try:
            from aelvoxim.learn.gap_analysis import get_blind_spots_for_user as _gbs
            spots = _gbs(inp.user_id, min_confidence=0.6, max_items=3)
            if spots:
                details["blind_spots"] = [
                    {"topic": s["value"], "confidence": s["overall"]}
                    for s in spots
                ]
        except Exception:
            pass

        # 4. Compute confidence summary
        if details["entities"]:
            confs = [e["confidence"] for e in details["entities"] if e["confidence"] is not None]
            if confs:
                details["confidence_summary"] = {
                    "avg": round(sum(confs) / len(confs), 2),
                    "min": round(min(confs), 2),
                    "max": round(max(confs), 2),
                }

        # Build opinion text
        opinion_parts = []
        n = len(details["entities"])
        if n:
            opinion_parts.append(f"Retrieved {n} entities")
            layers = details.get("layers", {})
            if layers:
                layer_str = ", ".join(f"{k}:{v}" for k, v in sorted(layers.items()))
                opinion_parts.append(f"layers: {layer_str}")
            freshness = details.get("freshness", {})
            if freshness:
                fresh_str = ", ".join(f"{k}:{v}" for k, v in sorted(freshness.items()))
                opinion_parts.append(f"freshness: {fresh_str}")
            n_conflicts = len(details.get("conflicts", []))
            opinion_parts.append(f"conflicts: {n_conflicts}")
            cs = details.get("confidence_summary", {})
            if cs:
                opinion_parts.append(f"avg conf: {cs.get('avg', 0):.2f}")
        if details["events"]:
            opinion_parts.append(f"events: {len(details['events'])}")
        if details["blind_spots"]:
            opinion_parts.append(f"blind spots: {len(details['blind_spots'])}")
        opinion = " | ".join(opinion_parts) if opinion_parts else "No relevant memory found."

        confidence = details["confidence_summary"].get("avg", 0.5) if details["confidence_summary"] else 0.3

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=round(confidence, 2),
            details=details,
        )


def _extract_overall(entity: dict) -> Optional[float]:
    """Extract overall confidence from entity's confidence_metadata."""
    try:
        attrs = entity.get("attributes", {})
        if isinstance(attrs, dict):
            cm = attrs.get("confidence_metadata", {})
            if isinstance(cm, dict):
                return cm.get("overall")
    except Exception:
        pass
    return None
