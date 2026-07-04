"""
metacore.experts.base — BaseExpert interface.

All experts inherit from BaseExpert and implement run().
The orchestrator calls each expert via this uniform interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExpertInput:
    """Input to any expert."""
    query: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    session_id: str = ""
    shared_dir: Optional[str] = None


@dataclass
class ExpertOutput:
    """Output from any expert."""
    expert_name: str = ""
    opinion: str = ""
    confidence: float = 0.5
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    skipped: bool = False


class BaseExpert:
    """Base class for all experts. Subclasses must implement run()."""

    name: str = "base"

    def __init__(self):
        self.name = self.__class__.__name__.lower().replace("expert", "")

    def run(self, inp: ExpertInput) -> ExpertOutput:
        """Process input and return expert opinion."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Expert: {self.name}>"

    @staticmethod
    def _check_shared_block(inp: ExpertInput) -> Optional[ExpertOutput]:
        """Check if safety or ethics has already blocked this query."""
        shared = (inp.context or {}).get("_shared_context", {})
        if not shared:
            return None

        for block_key, label in (("safety", "SAFETY"), ("ethics", "ETHICAL")):
            block = shared.get(block_key, {})
            if block.get("error") and label in str(block.get("error", "")).upper():
                return ExpertOutput(
                    expert_name="",
                    opinion=f"Skipped: {label} block — {block.get('opinion', '')[:100]}",
                    confidence=0.0,
                    error=f"skipped_by_{block_key}",
                    skipped=True,
                )
        return None


def register(cls):
    """Decorator: auto-register an expert class on import."""
    from . import register_expert
    register_expert(cls)
    return cls
