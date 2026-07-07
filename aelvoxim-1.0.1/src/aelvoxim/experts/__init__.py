"""
metacore.experts — Expert modules for MetaCore brain + plugin registry.

Each expert is a Python class implementing BaseExpert.
Experts auto-register via the @register_expert decorator.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from .base import BaseExpert, ExpertInput, ExpertOutput

# ── Plugin registry ──

_EXPERT_REGISTRY: Dict[str, Type[BaseExpert]] = {}
_COMMUNITY_ONLY = {"logic", "memory", "ethics", "safety", "code_review"}  # 5 core experts for community edition


def register_expert(cls: Type[BaseExpert]) -> Type[BaseExpert]:
    """Register an expert class by its lowercased name (sans 'expert' suffix)."""
    name = cls.__name__.lower().replace("expert", "")
    _EXPERT_REGISTRY[name] = cls
    return cls


def discover_experts(capability: str = None, edition: str = "") -> List[Type[BaseExpert]]:
    """Get registered expert classes, optionally filtered by capability or edition.

    edition='community' limits to {logic, memory, ethics, safety, code_review}.
    Uses aelvoxim.server.edition if no explicit edition passed.
    """
    if not edition:
        try:
            from ..server.edition import current as _ed_cur
            edition = _ed_cur()
        except ImportError:
            edition = "community"
    if edition == "community":
        max_n = 5
        from ..server.edition import get as _ed_get
        max_n = _ed_get("max_experts", 5)
        experts = [cls for cls in _EXPERT_REGISTRY.values()
                   if cls.__name__.lower().replace("expert", "") in _COMMUNITY_ONLY]
        return experts[:max_n]
    return list(_EXPERT_REGISTRY.values())


def get_expert_names() -> List[str]:
    """Return names of all registered experts."""
    return list(_EXPERT_REGISTRY.keys())


# ── Auto-import expert modules so @register_expert decorators fire ──

from . import memory       # noqa: F401, E402
from . import logic        # noqa: F401, E402
from . import ethics       # noqa: F401, E402
from . import emotion      # noqa: F401, E402
from . import creative     # noqa: F401, E402
from . import safety       # noqa: F401, E402
from . import introspection  # noqa: F401, E402
from . import code_review    # noqa: F401, E402


__all__ = [
    "BaseExpert", "ExpertInput", "ExpertOutput",
    "register_expert", "discover_experts", "get_expert_names",
]
