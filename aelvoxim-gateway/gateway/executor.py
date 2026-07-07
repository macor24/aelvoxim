# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.executor — Dual-mode execution engine.

Mode A (UIA / Accessibility): High-precision native control.
Mode B (VLM): Screenshot → visual detection → click simulation.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional


class Executor:
    """Execute operations on desktop software."""

    def __init__(self, mode: str = "uia"):
        self.mode = mode

    # ── Public API ──

    def execute(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single operation.

        Operation format:
            {"action": "click"|"input"|"hotkey"|"select"|"run_script"|"wait",
             "target": "layer_name"|"menu_item"|"coords",
             "params": {...},
             "mode": "uia"|"vlm"}   # optional, overrides default mode

        Returns:
            {"success": bool, "output": str, "error": str}
        """
        op_mode = operation.get("mode", self.mode)
        if op_mode == "vlm":
            return self._exec_vlm(operation)
        return self._exec_uia(operation)

    def run_script(self, script_path: str, app_name: str = "photoshop") -> Dict[str, Any]:
        """Execute a software script file (JSX, LISP, VBA)."""
        ext = Path(script_path).suffix.lower()
        if ext == ".jsx":
            return self._run_photoshop_jsx(script_path)
        return {"success": False, "error": f"Unsupported script type: {ext}"}

    # ── UIA mode ──

    def _exec_uia(self, op: Dict[str, Any]) -> Dict[str, Any]:
        action = op.get("action", "")
        target = op.get("target", "")
        params = op.get("params", {})

        if action == "run_script":
            return self.run_script(params.get("path", ""), params.get("app", ""))
        if action == "wait":
            time.sleep(float(params.get("seconds", 1)))
            return {"success": True, "output": f"Waited {params.get('seconds',1)}s"}
        if action == "activate_window":
            return self._uia_activate_window(target)
        if action == "click":
            return self._uia_click(target)
        if action == "hotkey":
            return self._send_hotkey(target)
        if action == "input":
            return self._uia_input(target, params.get("text", ""))

        return {"success": False, "error": f"Unknown action: {action}"}

    def _uia_activate_window(self, title_pattern: str) -> Dict:
        """Bring a window to foreground by title."""
        ps_script = f'''
        $wshell = New-Object -ComObject wscript.shell
        $wshell.AppActivate("{title_pattern}")
        Start-Sleep -Milliseconds 200
        '''
        r = subprocess.run(["powershell", "-Command", ps_script],
                           capture_output=True, text=True, timeout=15)
        return {"success": True, "output": r.stdout.strip()}

    def _uia_click(self, target: str) -> Dict:
        """Send hotkeys to activate a tool or menu in Photoshop."""
        return self._send_hotkey(target)

    def _uia_input(self, target: str, text: str) -> Dict:
        """Type text using SendKeys."""
        # Escape special characters for SendKeys
        escaped = text.replace("{", "{{}").replace("}", "{}}")
        ps_script = f'''
        $wshell = New-Object -ComObject wscript.shell
        $wshell.SendKeys("{escaped}")
        '''
        subprocess.run(["powershell", "-Command", ps_script],
                       capture_output=True, timeout=15)
        return {"success": True, "output": f"Typed: {text[:30]}"}

    def _send_hotkey(self, keys: str) -> Dict:
        """Send keyboard shortcut."""
        from ..config import get
        # Allow override via config
        ps_script = f'''
        $wshell = New-Object -ComObject wscript.shell
        Start-Sleep -Milliseconds 100
        $wshell.SendKeys("{keys}")
        '''
        subprocess.run(["powershell", "-Command", ps_script],
                       capture_output=True, timeout=15)
        return {"success": True, "output": f"Sent hotkey: {keys}"}

    def _run_photoshop_jsx(self, script_path: str) -> Dict:
        """Execute a JSX script in Photoshop."""
        ps_paths = [
            r"C:\Program Files\Adobe\Adobe Photoshop 2025\Photoshop.exe",
            r"C:\Program Files\Adobe\Adobe Photoshop 2024\Photoshop.exe",
            r"C:\Program Files\Adobe\Adobe Photoshop 2023\Photoshop.exe",
            r"C:\Program Files\Adobe\Photoshop 2025\Photoshop.exe",
        ]
        ps_exe = None
        for p in ps_paths:
            if Path(p).exists():
                ps_exe = p
                break
        if not ps_exe:
            # Try to find it via registry or where
            try:
                r = subprocess.run(["where", "Photoshop.exe"],
                                   capture_output=True, text=True, timeout=10)
                ps_exe = r.stdout.strip().split("\n")[0]
            except Exception:
                pass
        if not ps_exe:
            return {"success": False, "error": "Photoshop not found"}

        try:
            r = subprocess.run(
                [ps_exe, "-r", str(Path(script_path).resolve())],
                capture_output=True, timeout=120,
            )
            return {"success": r.returncode == 0,
                    "output": r.stdout.decode("utf-8", errors="replace")[:500]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Photoshop script timed out (120s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── VLM mode (Pro feature) ──

    def _exec_vlm(self, op: Dict[str, Any]) -> Dict[str, Any]:
        """VLM fallback — stub, to be implemented as Pro feature."""
        return {"success": False,
                "error": "VLM execution not available (Pro feature)"}
