"""
metacore.memory.prevalidation — Write-before-simulate validation for store_entity.

Inspects the target entity's existing knowledge graph (fusion + SQLite) before
a new value is committed, detecting semantic conflicts, relation discrepancies,
and potential knowledge drift.

Three validation stages:
  1. value_conflict — checks if the new value contradicts a high-confidence existing value
  2. relation_coherence — checks if the entity's type/tags are compatible
     with its existing relation network
  3. path_contradiction — 2-hop relation walk to find contradictory inference chains

Returns a decision dict: {action: "write"|"flag"|"block", reason, ...}
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .entry import LAYER_SEMANTIC
from .fusion import MemoryFusion

import logging
_log = logging.getLogger("aelvoxim.prevalidation")


# ── Thresholds ──

_HIGH_CONFIDENCE = 0.8
_SIMILARITY_CONFLICT = 0.7
_MAX_HOP = 2


def _char_similarity(a: str, b: str) -> float:
    """Character-level overlap (0.0-1.0)."""
    if not a or not b:
        return 0.0
    a_chars = set(a.strip().lower()[:100])
    b_chars = set(b.strip().lower()[:100])
    if not a_chars or not b_chars:
        return 0.0
    return len(a_chars & b_chars) / max(len(a_chars | b_chars), 1)


def _existing_value(
    eid: str, fusion: MemoryFusion, db=None
) -> Tuple[Optional[str], float, str]:
    """Look up existing value across fusion layers then SQLite.

    Returns (value, confidence, layer_name) or (None, 0, "").
    """
    for layer in (fusion.semantic, fusion.episodic, fusion.working):
        entry = layer._entries.get(eid)
        if entry and entry.value:
            return (str(entry.value), entry.importance, entry.layer)
    if db:
        try:
            row = db.execute(
                "SELECT value, attributes FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if row and row[0]:
                attrs: dict = json.loads(row[1] or "{}")
                return (str(row[0]), attrs.get("_confidence", 0.5), "db")
        except Exception:
            _log.exception("prevalidation error")
    return (None, 0.0, "")


def _relation_targets(
    eid: str, db=None
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Get all entities + relations connected to this key."""
    targets: List[str] = []
    rels: List[Dict[str, Any]] = []
    if not db:
        return targets, rels
    try:
        rows = db.execute(
            "SELECT source, target, rel_type, attributes FROM relations "
            "WHERE source = ? OR target = ?",
            (eid, eid),
        ).fetchall()
        for r in rows:
            other = r["target"] if r["source"] == eid else r["source"]
            targets.append(other)
            rels.append({
                "source": r["source"],
                "target": r["target"],
                "rel_type": r["rel_type"],
                "attributes": r["attributes"],
            })
    except Exception:
        _log.exception("prevalidation error")
    return targets, rels


def _walk_chain(
    seed: str, db, depth: int = 0, visited: Optional[set] = None
) -> List[Dict[str, Any]]:
    """BFS walk relation chain up to _MAX_HOP. Returns path nodes."""
    if visited is None:
        visited = set()
    if depth >= _MAX_HOP or seed in visited:
        return []
    visited.add(seed)
    chains: List[Dict[str, Any]] = []
    try:
        rows = db.execute(
            "SELECT source, target, rel_type FROM relations "
            "WHERE source = ? OR target = ?",
            (seed, seed),
        ).fetchall()
        for r in rows:
            other = r["target"] if r["source"] == seed else r["source"]
            chains.append({
                "from": seed, "to": other,
                "rel_type": r["rel_type"], "depth": depth + 1,
            })
            chains.extend(_walk_chain(other, db, depth + 1, visited))
    except Exception:
        _log.exception("prevalidation error")
    return chains


def _stage1(eid: str, new_value: str, tags: List[str],
            fusion: MemoryFusion, db=None) -> Optional[Dict[str, Any]]:
    """Check for value contradiction against high-confidence existing value."""
    old_val, conf, layer = _existing_value(eid, fusion, db)
    if not old_val:
        return None
    if layer == LAYER_SEMANTIC or conf >= _HIGH_CONFIDENCE:
        sim = _char_similarity(old_val, new_value)
        if sim < _SIMILARITY_CONFLICT:
            return {
                "stage": "value_conflict",
                "action": "flag",
                "reason": f"new '{new_value[:60]}' vs existing '{old_val[:60]}' (sim={sim:.2f}, conf={conf:.2f})",
                "existing_value": old_val[:200],
                "new_value": new_value[:200],
                "similarity": round(sim, 2),
                "confidence": conf,
            }
    return None


def _stage2(eid: str, tags: List[str], db=None) -> Optional[Dict[str, Any]]:
    """Check tag-vs-relation consistency."""
    if not tags or not db:
        return None
    _, rels = _relation_targets(eid, db)
    if not rels:
        return None

    type_indicators = set(t.lower() for t in tags)
    is_person = any(t in type_indicators for t in ("person", "people"))
    rel_types = list({r["rel_type"] for r in rels})

    if is_person and rel_types and all(t in ("is_a", "part_of") for t in rel_types):
        return {
            "stage": "relation_coherence",
            "action": "flag",
            "reason": f"tagged 'person' but only has {set(rel_types)} relations",
            "rel_types": rel_types,
        }

    return None


def _stage3(eid: str, db=None) -> Optional[Dict[str, Any]]:
    """Walk 2-hop chain for contradictory has-vs-is_a relations."""
    if not db:
        return None
    chains = _walk_chain(eid, db)
    if len(chains) < 2:
        return None

    has_targets = {c["to"] for c in chains if c["rel_type"] in ("has", "contains")}
    is_targets = {c["to"] for c in chains if c["rel_type"] in ("is_a", "part_of")}
    overlap = has_targets & is_targets
    if overlap:
        return {
            "stage": "path_contradiction",
            "action": "flag",
            "reason": f"both 'has' and 'is_a' to same targets: {overlap}",
            "overlap": list(overlap),
            "chain_depth": _MAX_HOP,
        }
    return None


def prevalidate(
    eid: str,
    new_value: str,
    tags: Optional[List[str]] = None,
    fusion: Optional[MemoryFusion] = None,
    db_connection=None,
) -> Dict[str, Any]:
    """Run all three validation stages before committing a new entity value.

    Returns a decision dict:
    - "write": no issues found, proceed normally
    - "flag": minor concern, proceed but mark attributes
    - "block": strong contradiction, defer to pending queue
    """
    tags = tags or []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    stages: list = []
    s1 = _stage1(eid, new_value, tags, fusion, db_connection) if fusion else None
    s2 = _stage2(eid, tags, db_connection)
    s3 = _stage3(eid, db_connection)

    for s in (s1, s2, s3):
        if s:
            stages.append(s)

    if not stages:
        return {"action": "write", "reason": "no conflicts", "stages": [], "validated_at": now}

    has_major = any(
        s.get("stage") == "value_conflict" and s.get("confidence", 0) >= _HIGH_CONFIDENCE
        for s in stages
    )

    if has_major:
        return {
            "action": "block",
            "reason": stages[0].get("reason", "high-confidence value conflict"),
            "stages": stages,
            "validated_at": now,
            "conflict_details": stages[0],
        }

    reasons = [s.get("reason", "") for s in stages if s.get("reason")]
    return {
        "action": "flag",
        "reason": "; ".join(reasons),
        "stages": stages,
        "validated_at": now,
    }


__all__ = ["prevalidate"]
