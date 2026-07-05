"""Aelvoxim edition/version gating module.

Controls which features are available based on edition (community/pro/enterprise).
Same codebase, different edition = different automation level.

Usage:
    from aelvoxim.server.edition import get, current, apply_license
    
    if get("auto_learn"):
        # Pro feature: auto learning loop
        start_auto_loop()
    
    edition = current()  # "community" | "pro" | "enterprise"
"""

from __future__ import annotations

import os
from typing import Any

_EDITION = os.environ.get("AELVOXIM_EDITION", "community").lower()

_EDITION_CONFIG = {
    "community": {
        "auto_learn": False,           # Manual learn only
        "curiosity_enabled": False,    # No curiosity-driven discovery
        "auto_tune_enabled": False,    # Static parameters
        "auto_post_validation": False, # No periodic knowledge audit
        "gap_analysis_enabled": False, # No knowledge gap detection
        "meta_learner_enabled": False, # No meta-learning
        "sub_agent_isolation": False,  # In-process serial execution
        "max_experts": 5,              # 5 core experts only
        "advanced_experts": False,     # No .pyd advanced experts
        "enterprise_mode": False,       # Single-user
    },
    "pro": {
        "auto_learn": True,
        "curiosity_enabled": True,
        "auto_tune_enabled": True,
        "auto_post_validation": True,
        "gap_analysis_enabled": True,
        "meta_learner_enabled": True,
        "sub_agent_isolation": True,
        "max_experts": 12,
        "advanced_experts": True,
        "enterprise_mode": False,
    },
    "enterprise": {
        "auto_learn": True,
        "curiosity_enabled": True,
        "auto_tune_enabled": True,
        "auto_post_validation": True,
        "gap_analysis_enabled": True,
        "meta_learner_enabled": True,
        "sub_agent_isolation": True,
        "max_experts": 12,
        "advanced_experts": True,
        "enterprise_mode": True,
    },
}


def current() -> str:
    """Get current edition string."""
    return _EDITION


def get(key: str, default: Any = None) -> Any:
    """Get a configuration value for the current edition."""
    return _EDITION_CONFIG.get(_EDITION, {}).get(key, default)


def set_edition(edition: str) -> None:
    """Set edition at runtime (called by license verification)."""
    global _EDITION
    edition = edition.lower()
    if edition in _EDITION_CONFIG:
        _EDITION = edition
