# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.config — Configuration management for Desktop Gateway.

Loads config.yaml, provides typed access to settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_CONFIG: Optional[Dict[str, Any]] = None
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Load config from YAML file (or JSON fallback)."""
    global _CONFIG
    config_path = Path(path) if path else _CONFIG_PATH

    if config_path.suffix in (".yaml", ".yml"):
        # Minimal YAML parser for our simple config
        data = _parse_yaml(config_path.read_text(encoding="utf-8"))
    else:
        data = json.loads(config_path.read_text(encoding="utf-8"))

    _CONFIG = data
    return data


def get(key: str, default: Any = None) -> Any:
    """Get a config value by dot-separated key."""
    global _CONFIG
    if _CONFIG is None:
        load()
    keys = key.split(".")
    val = _CONFIG
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


def _parse_yaml(text: str) -> Dict:
    """Minimal YAML parser — supports our flat config format only."""
    result: Dict = {}
    current: Dict = result
    stack: List[Dict] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        # Adjust nesting level
        while stack and stack[-1][1] >= indent:
            stack.pop()
        # Key: value or Key:
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                # Simple value
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                elif val.isdigit():
                    val = int(val)
                elif val.replace(".", "", 1).isdigit():
                    val = float(val)
                elif val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                parent = stack[-1][0] if stack else result
                parent[key] = val
            else:
                # Nested key — start new dict
                new_dict = {}
                if stack:
                    stack[-1][0][key] = new_dict
                else:
                    # Root level
                    pass
                # Handle array items starting with "-"
                # ... for now just treat as simple dict
                stack.append((new_dict, indent))
                # Special case for list items
                if stripped.startswith("- "):
                    val = stripped[2:].strip()
                    if stack:
                        # Should be added to parent list
                        pass
        elif stripped.startswith("- "):
            # List item — add to the last parent dict key
            val = stripped[2:].strip()
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            elif val.isdigit():
                val = int(val)
            parent = stack[-1][0] if stack else result
            if not stack:
                continue
            # Find a key in the parent that doesn't have a value yet
            for k, v in list(parent.items()):
                if isinstance(v, list):
                    parent[k].append(val)
                    break
            else:
                # If no list found, add to a default list
                pass
    return result


# ── Quick access ──

def gateway_port() -> int:
    return int(get("gateway.port", 9705))


def gateway_host() -> str:
    return get("gateway.host", "127.0.0.1")


def refresh_interval() -> int:
    return int(get("gateway.refresh_interval_sec", 3))


def temp_dir() -> Path:
    return Path(get("gateway.temp_dir", "/tmp/aelvoxim_gateway"))


def app_configs() -> List[Dict]:
    return get("apps", [])
