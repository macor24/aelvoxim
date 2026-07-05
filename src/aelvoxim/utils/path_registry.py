"""path_registry — Centralized path registration and lookup.

All .py files should obtain paths through this module instead of hardcoding.

Usage:
    from path_registry import registry
    path = registry.get("data:llm_config")   # -> ~/.metacore/llm-config.json
    path = registry.get("module:auth")        # -> /abs/path/to/metacore/server/auth.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

# -- Config --

# Default config path: project root / config / path_registry.json
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent.parent  # utils/ -> metacore/ -> src/ -> Aelvoxim/
_SRC_ROOT = _PROJECT_ROOT / "src"
_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "path_registry.json"


# -- Registry --


class PathRegistry:
    """Path registry — loaded at startup, supports dynamic registration at runtime.

    >>> registry = PathRegistry()
    >>> registry.get("data:user_data")   # expands ~/ and joins root
    """

    def __init__(self, config_path: str | Path = ""):
        self._config_path = Path(config_path or _DEFAULT_CONFIG)
        self._registry: Dict = self._load()

    # -- Public API --

    def get(self, key: str, default: str = "") -> str:
        """Get full path by 'category:name' format.

        Examples:
            registry.get("data:llm_config")
            registry.get("module:auth")
        """
        if ":" not in key:
            return default

        category, name = key.split(":", 1)
        # Compat: singular -> plural, e.g. "module" -> "modules"
        _plural_map = {"module": "modules", "tool": "tools", "config": "configs", "data": "data"}
        lookup = _plural_map.get(category, category)
        relative = self._registry.get(lookup, {}).get(name)
        if not relative:
            return default

        return self._resolve(relative)

    def register(self, category: str, name: str, relative_path: str) -> None:
        """Register or update a path at runtime."""
        if category not in self._registry:
            self._registry[category] = {}
        self._registry[category][name] = relative_path
        self._save()

    def list_all(self) -> Dict[str, str]:
        """List all registered paths, keyed as 'category:name'."""
        result: Dict[str, str] = {}
        for category, items in self._registry.items():
            if isinstance(items, dict):
                for name, rel in items.items():
                    result[f"{category}:{name}"] = self._resolve(rel)
            elif category != "root":
                result[category] = self._resolve(str(items))
        return result

    # -- Internal --

    def _load(self) -> Dict:
        if self._config_path.exists():
            try:
                return json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {"root": "", "modules": {}, "tools": {}, "data": {}}
        return {"root": "", "modules": {}, "tools": {}, "data": {}}

    def _save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _resolve(self, relative: str) -> str:
        """Expand relative path to absolute path.

        Rules:
        - ~/ -> user home directory
        - If root is set, join with root
        - Absolute paths returned as-is
        """
        raw = os.path.expanduser(relative)
        if raw.startswith("/"):
            return os.path.abspath(raw)
        root = self._registry.get("root", "")
        if root:
            return os.path.abspath(os.path.join(root, raw))
        return os.path.abspath(os.path.join(str(_PROJECT_ROOT), raw))


# -- Module-level singleton --

_registry: Optional[PathRegistry] = None


def get_registry(config_path: str | Path = "") -> PathRegistry:
    """Get or initialize the global path registry."""
    global _registry
    if _registry is None:
        _registry = PathRegistry(config_path)
    # Ensure config is loaded (file may be updated after first import)
    if not _registry._registry.get("modules"):
        _registry._registry = _registry._load()
    return _registry


# Quick reference
registry = get_registry()
