# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.executor._vlm — Visual Language Model fallback.

Pro feature. When UIA accessibility is unavailable (e.g. non-standard UI),
this module takes a screenshot, detects UI elements via VLM, and returns
click coordinates for mouse simulation.

Community edition: returns stub with upgrade prompt.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def detect_ui_elements(screenshot_base64: str, description: str = "") -> Dict[str, Any]:
    """Detect UI elements in a screenshot using VLM.

    Args:
        screenshot_base64: Base64-encoded PNG screenshot.
        description: Optional text description of what to find.

    Returns:
        Community edition: {"success": False, "error": "VLM not available (Pro feature)"}
        Pro edition (future): {"success": True, "elements": [...], "coords": [...]}
    """
    return {
        "success": False,
        "error": "VLM not available (Pro feature)",
        "elements": [],
    }


def find_element_by_text(screenshot_base64: str, text: str) -> Dict[str, Any]:
    """Find a UI element containing specific text.

    Returns click coordinates if found.
    """
    return {
        "success": False,
        "error": "VLM not available (Pro feature)",
        "coords": None,
    }


def compare_screenshots(before_b64: str, after_b64: str) -> Dict[str, Any]:
    """Compare two screenshots to detect visual changes.

    Pro edition (future): returns changed regions with similarity score.
    """
    return {
        "success": False,
        "error": "VLM not available (Pro feature)",
        "changed_regions": [],
        "similarity": 1.0,
    }
