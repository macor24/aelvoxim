"""
metacore.experts.creative — Creative Expert.

Generates alternative scenarios, solution combinations, and novel ideas.
Uses LLM when available (via metacore.learn.extract.call_llm_if_available)
with graceful fallback to rule-based template generation.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseExpert, ExpertInput, ExpertOutput, register

import logging
_log = logging.getLogger("aelvoxim.creative")


# ── Creative type detection ─────────────────────────────────

_CREATIVE_TYPES: Dict[str, Dict] = {
    "story": {
        "keywords": ["story", "novel", "narrative", "tale", "fiction",
                     "故事", "小说", "叙事", "虚构"],
        "structure": "character > setting > conflict > resolution",
    },
    "poem": {
        "keywords": ["poem", "poetry", "verse", "lyric", "rhyme",
                     "诗", "诗歌", "词", "韵文"],
        "structure": "imagery > emotion > rhythm > form",
    },
    "code_example": {
        "keywords": ["code", "example", "snippet", "function", "class",
                     "示例", "代码", "函数", "类"],
        "structure": "problem > approach > code > walkthrough",
    },
    "explanation": {
        "keywords": ["explain", "what is", "how does", "why does",
                     "explanation", "解释", "什么是", "原理"],
        "structure": "concept > example > edge-cases > summary",
    },
    "idea": {
        "keywords": ["idea", "brainstorm", "suggestion", "propose",
                     "建议", "想法", "点子", "头脑风暴"],
        "structure": "problem > options > trade-offs > recommendation",
    },
}


def _detect_creative_type(query: str) -> Optional[str]:
    """Detect the user's creative request type based on keywords."""
    q_lower = query.lower()
    for ctype, info in _CREATIVE_TYPES.items():
        if any(kw in q_lower for kw in info["keywords"]):
            return ctype
    return None


def _get_structure_guidance(ctype: str) -> str:
    """Get structure guidance for a creative type, or default."""
    info = _CREATIVE_TYPES.get(ctype)
    if info:
        return info["structure"]
    return "overview > details > examples"


# ── Fallback templates (used when LLM is unavailable) ──

_SCENARIO_TEMPLATES = [
    "What if we approach this from {perspective}?",
    "Consider the opposite: instead of {current}, try {alternative}.",
    "Combine {a} with {b} — would that yield a better outcome?",
    "What's the simplest possible solution that could work?",
    "What would happen if we removed {constraint} entirely?",
]


def _build_context(inp: ExpertInput) -> str:
    """Build memory context string for LLM prompt.

    Uses shared context from MemoryExpert when available,
    avoiding duplicate search_entities/search_events calls.
    """
    parts = []
    # Step 0: Try shared context from MemoryExpert
    shared_entities = []
    try:
        shared = (inp.context or {}).get("_shared_context", {})
        memory_result = shared.get("memory", {})
        if isinstance(memory_result, dict):
            memory_details = memory_result.get("details", {})
            if isinstance(memory_details, dict):
                shared_entities = memory_details.get("entities", [])
    except Exception:
        _log.exception("creative error")

    if shared_entities:
        vals = [e.get("value", "")[:80] or str(e.get("content", ""))[:80]
                for e in shared_entities if e.get("value") or e.get("content")]
        if vals:
            parts.append("Related known entities: " + "; ".join(vals[:5]))
            return "\n".join(parts)

    # Fallback: own search (original logic)
    try:
        from aelvoxim.memory import search_entities
        entities = search_entities(query=inp.query, limit=5, user_id=inp.user_id)
        if entities:
            vals = [str(e.get("value", ""))[:80] for e in entities if e.get("value")]
            if vals:
                parts.append("Related known entities: " + "; ".join(vals))
    except Exception:
        _log.exception("creative error")
    try:
        from aelvoxim.memory import search_events
        events = search_events(query=inp.query, limit=3)
        if events:
            ev = [str(e.get("content", ""))[:120] for e in events if e.get("content")]
            if ev:
                parts.append("Related events: " + "; ".join(ev))
    except Exception:
        _log.exception("creative error")
    return "\n".join(parts)


_CREATIVE_PROMPT = """You are a creative thinking expert. Given a user's question or problem, generate alternative perspectives, unconventional approaches, and novel ideas.

User query: {query}

{context}

Generate exactly 3 creative alternatives. Each must:
1. Be substantively different from the others
2. Include a concrete direction (not just "think differently")
3. Be actionable and grounded in reality

Format your response as:
- Alternative 1: [title]
  [2-3 sentences of concrete description]
- Alternative 2: [title]
  [2-3 sentences of concrete description]
- Alternative 3: [title]
  [2-3 sentences of concrete description]

Also rate the novelty of each idea on a scale of 0.0-1.0 in format: [Novelty: 0.XX]"""


def _call_llm(prompt: str) -> Optional[str]:
    """Try to call LLM, returning None if not available."""
    try:
        from aelvoxim.learn.extract import call_llm_if_available
        llm = call_llm_if_available()
        if not llm:
            return None
        call_fn, model = llm
        return call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=1024,
        )
    except Exception:
        return None


@register
class CreativeExpert(BaseExpert):
    """Generates creative alternatives using LLM when available, with rule-based fallback."""
    _capabilities = ["creative", "generation", "idea", "scenario"]

    name = "creative"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if another expert (safety/ethics) has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "scenarios": [],
            "alternatives": [],
            "novelty_score": 0.0,
            "llm_generated": False,
        }

        q = inp.query
        context = _build_context(inp)

        # Detect creative type for structure guidance
        ctype = _detect_creative_type(q)
        if ctype:
            guidance = _get_structure_guidance(ctype)
            context += f"\nCreative type: {ctype}\nStructure: {guidance}\n"
            details["creative_type"] = ctype
            details["structure_guidance"] = guidance

        # Phase 1: Try LLM generation
        llm_text = _call_llm(_CREATIVE_PROMPT.format(query=q, context=context))
        if llm_text:
            details["llm_generated"] = True
            # Parse LLM response
            lines = llm_text.strip().split("\n")
            current_alt = ""
            for line in lines:
                line = line.strip()
                if line.startswith("- Alternative") or line.startswith("Alternative"):
                    if current_alt:
                        details["scenarios"].append(current_alt.strip())
                    current_alt = line
                elif current_alt:
                    current_alt += " " + line
                    # Check for novelty tag
                    if "[Novelty:" in line or "[Novelty:" in current_alt:
                        try:
                            import re
                            m = re.search(r'\[Novelty:\s*([\d.]+)\]', current_alt)
                            if m:
                                details["novelty_score"] = max(
                                    details["novelty_score"],
                                    float(m.group(1)),
                                )
                        except Exception:
                            _log.exception("creative error")
            if current_alt:
                details["scenarios"].append(current_alt.strip())
            # Fill alternatives list
            details["alternatives"] = [s.split("\n")[0][:80] for s in details["scenarios"] if s]
            # Fallback novelty if not parsed
            if details["novelty_score"] == 0.0:
                details["novelty_score"] = round(0.5 + random.random() * 0.4, 2)
        else:
            # Phase 2: Rule-based fallback
            details["llm_generated"] = False
            details = _fallback_generate(q, details)

        # Build opinion
        n = len(details["scenarios"])
        opinion = (
            f"Generated {n} creative scenarios"
            + (["", f" (LLM-generated, novelty: {details['novelty_score']})"][details['llm_generated']]
                if details['llm_generated']
                else " (rule-based)")
        )

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=round(min(0.9, details["novelty_score"] + 0.2), 2),
            details=details,
        )


def _fallback_generate(q: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based fallback when LLM is unavailable."""
    perspectives = [
        "first principles", "user experience", "long-term impact",
        "cost efficiency", "simplicity", "scalability",
    ]
    alternatives_list = [
        "the opposite approach", "a simplified version",
        "an incremental change", "a complete redesign",
    ]
    words = [w for w in q.split() if len(w) > 3][:5]
    key_terms = words if words else ["current approach"]

    for i, template in enumerate(_SCENARIO_TEMPLATES[:3]):
        try:
            scenario = template.format(
                perspective=random.choice(perspectives),
                current=key_terms[0] if key_terms else "current",
                alternative=random.choice(alternatives_list),
                a=key_terms[0] if len(key_terms) > 0 else "A",
                b=key_terms[min(1, len(key_terms) - 1)] if len(key_terms) > 1 else "B",
                constraint=key_terms[0] if key_terms else "constraint",
            )
            details["scenarios"].append(scenario)
        except Exception:
            _log.exception("creative error")

    _alt_prefixes = [
        "Try reversing", "Consider scaling down",
        "Apply the opposite", "Mix with",
        "Subtract", "Multiply by",
    ]
    for i in range(min(3, len(_alt_prefixes))):
        alt = f"{_alt_prefixes[i]} {key_terms[0] if key_terms else 'the problem'}"
        details["alternatives"].append(alt)

    novelty = min(1.0, len(q) / 200 + len(key_terms) / 10)
    details["novelty_score"] = round(novelty, 2)

    return details
