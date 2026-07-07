"""
metacore.experts.logic — Logic Expert with structured reasoning chain.

Extracts claims from user input, checks each against memory for
contradictions, and builds a structured reasoning chain.

Pure rule-based, no LLM calls.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register


# ── Claim extraction patterns ─────────────────────────────────


_EXTRACT_PATTERNS = [
    # "X is Y" / "X 是 Y" / "X 叫 Y"
    (r"(\w[\w\s]{1,30}) (?:is|are|was|were|be|是|叫|属于|为|叫作|称为) (\w[\w\s]{1,30})", "is_a"),
    # "X 比 Y" comparison
    (r"(\w[\w\s]{1,30}) 比 (\w[\w\s]{1,30})", "comparison"),
    # "X vs Y"
    (r"(\w[\w\s]{1,30})\s+vs\.?\s+(\w[\w\s]{1,30})", "comparison"),
    # "because X, Y" / "因为 X，所以 Y"
    (r"(?:because|since|as|因为) (.{2,50})[,，]?\s*(?:so|所以|then|则) (.{2,50})", "causation"),
    # "X causes Y" / "X 导致 Y"
    (r"(\w[\w\s]{1,30}) (?:causes?|leads? to|results? in|导致|引起|造成) (\w[\w\s]{1,30})", "causation"),
    # "X should Y" / "X Should Y" — a claim/opinion
    (r"(\w[\w\s]{1,30}) (?:should|must|need to|应该|必须|需要) (\w[\w\s]{1,30})", "claim"),
]


def _extract_claims(query: str) -> List[Dict]:
    """Extract structured claims from user query.

    Returns list of dicts with type, text, and premises.
    Falls back to treating the entire query as one claim when no
    structured patterns match.
    """
    # Skip pattern matching for interrogative sentences — they are questions, not claims
    _q = query.strip().lower()
    _question_words = ("what", "why", "how", "when", "where", "who", "which",
                       "does", "do", "is", "are", "can", "could", "would", "should",
                       "will", "did", "was", "were", "have", "has", "had")
    if any(_q.startswith(w) for w in _question_words):
        return [{"type": "general", "text": query.strip()[:80], "premises": [query.strip()[:80]]}]
    claims = []
    seen_texts = set()
    for pattern, claim_type in _EXTRACT_PATTERNS:
        for match in re.finditer(pattern, query, re.IGNORECASE):
            text = match.group(0).strip()
            if text and text not in seen_texts:
                seen_texts.add(text)
                claims.append({
                    "type": claim_type,
                    "text": text[:80],
                    "premises": [g[:40] for g in match.groups()],
                })
    # Fallback: entire query as a single general claim
    if not claims and len(query.strip()) > 5:
        claims.append({
            "type": "general",
            "text": query.strip()[:80],
            "premises": [query.strip()[:80]],
        })
    return claims


_INTERNAL_CONTRADICTION_PAIRS = [
    ("always", "never"),
    ("all", "none"),
    ("must", "optional"),
    ("required", "unnecessary"),
    ("increase", "decrease"),
    ("start", "stop"),
    ("enable", "disable"),
    ("true", "false"),
    ("yes", "no"),
    ("allow", "forbid"),
    ("create", "delete"),
    ("open", "close"),
    ("on", "off"),
    ("begin", "end"),
    ("up", "down"),
    ("add", "remove"),
    ("grant", "revoke"),
]


def _check_internal_contradiction(text: str) -> List[str]:
    """Detect internal contradictions within the query itself."""
    text_lower = text.lower()
    hits = []
    for a, b in _INTERNAL_CONTRADICTION_PAIRS:
        if a in text_lower and b in text_lower:
            hits.append(f"'{a}' vs '{b}'")
    return hits


def _check_memory_contradiction(claim_text: str, user_id: str = "",
                                 entities_from_shared: list = None) -> Dict:
    """Check if the claim contradicts existing memory entities.

    Uses pre-searched entities (from MemoryExpert) when available,
    avoiding duplicate search_entities() calls.

    Args:
        claim_text: Text to check
        user_id: User identifier
        entities_from_shared: Optional list of entities from MemoryExpert's shared context

    Returns dict with found, count, detail, and source.
    """
    if entities_from_shared:
        # Use already-searched entities — no duplicate query
        conflicting = []
        for e in entities_from_shared[:5]:
            val = str(e.get("value", "") or e.get("content", ""))[:60]
            if val:
                conflicting.append(val)
        return {
            "found": bool(conflicting),
            "count": len(conflicting),
            "detail": conflicting[0][:60] if conflicting else "",
            "source": "shared_context",
        }
    # Fallback: own search (original logic)
    try:
        from aelvoxim.memory import search_entities
        results = search_entities(query=claim_text[:50], limit=5)
        if results:
            # Check for conflicting values
            conflicting = []
            for r in results[:3]:
                val = str(r.get("value", "") or r.get("content", ""))[:60]
                if val:
                    conflicting.append(val)
            if conflicting:
                return {"found": True, "count": len(conflicting), "detail": conflicting[0][:60]}
        return {"found": False, "count": 0, "detail": ""}
    except Exception as e:
        return {"found": False, "count": 0, "detail": f"memory_search failed: {e}"}


@register
class LogicExpert(BaseExpert):
    """Evaluates logical consistency via structured reasoning chain.

    Pipeline:
    1. Extract claims from user query
    2. Check each claim for internal contradictions
    3. Cross-reference each claim against memory for conflicts
    4. Build confidence score from reasoning chain quality
    """
    _capabilities = ["logic", "reasoning", "conflict", "deduction"]

    name = "logic"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if another expert (safety/ethics) has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "reasoning_chain": [],
            "conflicts": [],
            "confidence_evaluation": {},
        }

        query = inp.query or ""
        user_id = inp.user_id or ""

        # Step 0: Read shared context for Memory expert results
        shared_entities = []
        shared = (inp.context or {}).get("_shared_context", {})
        memory_result = shared.get("memory", {})
        if isinstance(memory_result, dict):
            memory_details = memory_result.get("details", {})
            if isinstance(memory_details, dict):
                shared_entities = memory_details.get("entities", [])

        # Step 1: Extract claims
        claims = _extract_claims(query)
        details["reasoning_chain"].append({
            "step": 1,
            "action": "extract_claims",
            "result": f"{len(claims)} claim(s)",
            "claims": claims,
        })

        # Step 2: Check each claim for contradictions
        total_contradictions = 0
        for claim in claims:
            # Skip contradiction checks for fallback "general" claims (entire query)
            if claim.get("type") == "general":
                continue
            # Internal contradiction check
            internal_hits = _check_internal_contradiction(claim["text"])
            if internal_hits:
                total_contradictions += len(internal_hits)
                details["conflicts"].extend([
                    f"Internal: {h}" for h in internal_hits
                ])
            # Memory contradiction check
            mem_check = _check_memory_contradiction(claim["text"], user_id,
                                                     entities_from_shared=shared_entities)
            if mem_check["found"]:
                total_contradictions += 1
                details["conflicts"].append(
                    f"Memory conflict: {mem_check['detail'][:50]}"
                )

        details["reasoning_chain"].append({
            "step": 2,
            "action": "check_contradictions",
            "result": f"{total_contradictions} contradiction(s)",
        })

        # Step 3: Build confidence score
        confidence = 0.3  # base
        reasons = []

        # More claims = more data for reasoning
        if len(claims) >= 1:
            confidence += 0.15
            reasons.append(f"{len(claims)} claim(s) extracted")
        if len(claims) >= 3:
            confidence += 0.1
            reasons.append("multiple claims = richer reasoning")

        # Query length bonus
        if len(query) > 10:
            confidence += 0.1
            reasons.append("sufficient query length")
        if len(query) > 50:
            confidence += 0.05
            reasons.append("detailed query")

        # Memory support
        if details.get("conflicts"):
            # Conflicts = more information available → slightly more confident
            confidence += 0.05
            reasons.append("memory cross-reference available")

        # Contradiction penalty
        if total_contradictions > 0:
            confidence -= 0.25 * min(total_contradictions / 2, 1.0)
            reasons.append(f"{total_contradictions} contradiction(s) detected")

        details["confidence_evaluation"] = {
            "score": round(confidence, 2),
            "reasons": reasons[:5],  # keep concise
        }

        # Build opinion
        parts = []
        parts.append(f"Claims: {len(claims)}")
        if total_contradictions:
            parts.append(f"Contradictions: {total_contradictions}")
        parts.append(f"Confidence: {round(max(0.1, confidence), 2)}")
        opinion = " | ".join(parts)

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=round(max(0.1, min(1.0, confidence)), 2),
            details=details,
            error=None,
        )
