"""
metacore.experts.safety — Safety Expert.

Local pattern-based safety check only.
Auto-degrades gracefully when the local check cannot evaluate.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register

_log = logging.getLogger("aelvoxim.experts.safety")

# ── Local fallback patterns ──
_SAFETY_BLOCK_PATTERNS = [
    "DROP TABLE", "DROP DATABASE", "TRUNCATE",
    "rm -rf", "rm -rf /", ":(){ :|:& };:", "fork bomb",
    "chmod 777", "chown root",
    "self-replicat", "self_replicat", "clone itself",
    "fork bomb", "replicate", "autonomous replicat",
]


def _local_safety_check(text: str) -> Dict:
    """Local safety check."""
    text_upper = (text or "").upper()
    for pattern in _SAFETY_BLOCK_PATTERNS:
        if pattern.upper() in text_upper:
            return {"allowed": False, "reason": f"Local block: pattern '{pattern}' detected"}
    return {"allowed": True, "reason": "Local check passed"}


_BLOCK_SUGGESTIONS: Dict[str, str] = {
    "forbidden pattern": (
        "Avoid using '~' or '..' in paths — use the full absolute path instead."
    ),
}


def _add_suggestion(reason: str) -> str:
    """Append user-friendly suggestion to block reason."""
    rl = reason.lower()
    for key, suggestion in _BLOCK_SUGGESTIONS.items():
        if key.lower() in rl:
            return f"{reason} {suggestion}"
    return reason


@register
class SafetyExpert(BaseExpert):
    """Evaluates safety via local pattern matching only."""
    _capabilities = ["safety", "security", "audit", "compliance"]

    name = "safety"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if ethics has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "local_check": {},
            "rules_triggered": [],
            "priority_chain": [],
        }

        local_result = _local_safety_check(inp.query)
        source = "local_only"

        details["local_check"] = {
            "allowed": local_result.get("allowed", True),
            "reason": local_result.get("reason", ""),
        }

        blocked = not local_result.get("allowed", True)
        if blocked:
            opinion = f"SAFETY BLOCK (via {source}): {_add_suggestion(local_result.get('reason', ''))}"
        else:
            opinion = f"Safety check passed (via {source})"

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=0.0 if blocked else 0.9,
            details=details,
            error="Safety block" if blocked else None,
        )
