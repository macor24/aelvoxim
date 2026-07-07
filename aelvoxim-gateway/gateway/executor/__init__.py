# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.executor — Dual-mode desktop execution engine.

Mode A: UIA / Accessibility — Windows UI Automation (primary).
Mode B: VLM — Visual Language Model fallback (Pro feature).
"""
from . import _uia
from typing import Any, Dict

# ── VLM mode (Pro feature) ──


def _exec_vlm(operation: Dict[str, Any]) -> Dict[str, Any]:
    """VLM fallback — stub."""
    return {"success": False, "error": "VLM not available (Pro feature)"}


# ── Public API ──


ACTIONS = {
    "activate_window": lambda op: _uia.activate_window(op.get("target", "")),
    "find_window": lambda op: _uia.find_window(op.get("target", "")),
    "click_button": lambda op: _uia.click_button(
        op.get("params", {}).get("window_title", ""),
        op.get("target", "")),
    "get_uia_children": lambda op: _uia.get_uia_children(op.get("target", "")),
    "send_keys": lambda op: _uia.send_keys(op.get("target", "")),
    "type_text": lambda op: _uia.type_text(op.get("target", "")),
    "screenshot": lambda op: _uia.screenshot(op.get("target", "")),
    "mouse_click": lambda op: _uia.mouse_click(
        op.get("params", {}).get("x", 0),
        op.get("params", {}).get("y", 0),
        op.get("params", {}).get("button", "left")),
    "mouse_drag": lambda op: _uia.mouse_drag(
        op.get("params", {}).get("x1", 0),
        op.get("params", {}).get("y1", 0),
        op.get("params", {}).get("x2", 0),
        op.get("params", {}).get("y2", 0)),
    "wait": lambda op: _uia.send_keys("", delay_ms=int(op.get("params", {}).get("seconds", 1) * 1000)),
    "run_script": lambda op: _uia.send_keys(""),  # handled by executor.py
}


def execute(operation: Dict[str, Any], mode: str = "uia") -> Dict[str, Any]:
    """Execute a single operation.

    Operation format:
        {"action": "activate_window"|"click_button"|"send_keys"|...,
         "target": "...",
         "params": {...},
         "mode": "uia"|"vlm"}

    Returns:
        {"success": bool, "output": str, "error": str}
    """
    action = operation.get("action", "")
    op_mode = operation.get("mode", mode)

    if op_mode == "vlm":
        # Special Photoshop shortcut handling
        if action.startswith("ps_"):
            return _exec_photoshop_shortcut(operation)
        return _exec_vlm(operation)

    handler = ACTIONS.get(action)
    if handler:
        return handler(operation)

    if action == "exec":
        import subprocess
        _path = operation.get("params", {}).get("path", operation.get("target", ""))
        if not _path:
            return {"success": False, "error": "exec requires path param"}
        try:
            _r = subprocess.Popen(_path, shell=True)
            return {"success": True, "output": f"Started: {_path} (PID {_r.pid})"}
        except Exception as _e:
            return {"success": False, "error": str(_e)}
    if action == "open_notepad":
        import subprocess
        _r = subprocess.Popen("notepad.exe", shell=True)
        return {"success": True, "output": f"Notepad started (PID {_r.pid})"}
    if action == "open":
        _path = op.get("params", {}).get("path", op.get("target", ""))
        if not _path:
            return {"success": False, "error": "open requires path or target"}
        import subprocess
        _r = subprocess.Popen(_path, shell=True)
        return {"success": True, "output": f"Started: {_path} (PID {_r.pid})"}
    return {"success": False, "error": f"Unknown UIA action: {action}"}


# ── Photoshop shortcuts ──


def _exec_photoshop_shortcut(op: Dict[str, Any]) -> Dict[str, Any]:
    """Execute Photoshop-specific shortcuts via keyboard."""
    action = op.get("action", "")
    shortcuts = {
        "ps_new_layer": "^+n",         # Ctrl+Shift+N
        "ps_save": "^s",               # Ctrl+S
        "ps_save_as": "^+s",           # Ctrl+Shift+S
        "ps_undo": "^z",               # Ctrl+Z
        "ps_redo": "^+z",              # Ctrl+Shift+Z
        "ps_fill_fg": "^%n",          # Alt+Backspace (foreground fill)
        "ps_fill_bg": "^%n",          # Ctrl+Backspace (background fill)
        "ps_deselect": "^d",           # Ctrl+D
        "ps_select_all": "^a",         # Ctrl+A
        "ps_copy": "^c",               # Ctrl+C
        "ps_paste": "^v",              # Ctrl+V
        "ps_cut": "^x",                # Ctrl+X
        "ps_delete": "{DEL}",          # Delete
        "ps_merge_down": "^e",         # Ctrl+E
        "ps_merge_visible": "+^e",     # Ctrl+Shift+E
        "ps_free_transform": "^t",     # Ctrl+T
        "ps_lasso": "l",               # L key
        "ps_brush": "b",               # B key
        "ps_eraser": "e",              # E key
        "ps_move": "v",                # V key
        "ps_marquee": "m",             # M key
        "ps_zoom": "z",                # Z key
        "ps_eyedropper": "i",          # I key
        "ps_save_png": "^+a",          # Ctrl+Shift+A → choose PNG
    }
    keys = shortcuts.get(action)
    if keys:
        return _uia.send_keys(keys)
    return {"success": False, "error": f"Unknown PS shortcut: {action}"}
