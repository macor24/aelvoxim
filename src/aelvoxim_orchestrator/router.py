"""
aelvoxim_orchestrator.router — Fine-grained routing engine.

Reads config/routing_rules.json and maps user input to a routing type + expert subset.
Edge case: no match → falls back to "chat" (DEFAULT_TASK).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROUTING_FILE = Path(__file__).parent.parent.parent / "config" / "routing_rules.json"


class Router:
    """Load routing rules and classify input into routing_type + expert subset."""

    def __init__(self):
        self._rules: Dict[str, Any] = self._load()
        self._task_routes: Dict[str, Any] = self._rules.get("TASK_ROUTES", {})
        self._keywords: List[List] = self._rules.get("TASK_KEYWORDS", [])
        self._default: str = self._rules.get("DEFAULT_TASK", "chat")
        self._risky: List[str] = self._rules.get("RISKY_PATTERNS", [])

    def _load(self) -> Dict[str, Any]:
        try:
            if _ROUTING_FILE.exists():
                return json.loads(_ROUTING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def classify(self, query: str) -> Dict[str, Any]:
        """Classify a user query into routing_type and expert subset.

        Returns:
            {"routing_type": "code"|"analysis"|"chat"|...,
             "experts": [...],
             "level": "simple"|"expert",
             "risky": bool}
        """
        q = query.lower().strip()
        if not q:
            return {"routing_type": self._default, "experts": self._get_experts(self._default),
                    "level": "simple", "risky": False}

        # Check risky patterns first
        risky = any(p in q for p in self._risky)

        # Match keywords → routing_type
        matched_type = None
        for entry in self._keywords:
            keywords, task_type = entry[0], entry[1]
            if any(kw in q for kw in keywords):
                matched_type = task_type
                break

        routing_type = matched_type or self._default

        # Determine level: "planning", "analysis", "code" are expert
        expert_types = {"code", "analysis", "planning", "security"}
        level = "expert" if routing_type in expert_types or risky else "simple"

        return {
            "routing_type": routing_type,
            "experts": self._get_experts(routing_type),
            "level": level,
            "risky": risky,
        }

    def _get_experts(self, routing_type: str) -> List[str]:
        route = self._task_routes.get(routing_type, {})
        return route.get("experts", ["memory"])

    def get_rules(self) -> Dict[str, Any]:
        return self._rules
