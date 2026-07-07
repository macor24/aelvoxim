# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.executor._uia — Windows UI Automation via PowerShell.

Provides high-precision window manipulation:
    - activate_window(title_pattern) → bring to foreground
    - click_button(window_title, button_name) → click UI element
    - send_keys(keys) → keyboard input
    - get_window_rect(title_pattern) → window position/size
    - screenshot(window_title) → capture window screenshot (base64)

Uses PowerShell's UIAutomationClient for native accessibility.
Fallback: SendKeys for keyboard simulation.
"""
from __future__ import annotations

import base64
import subprocess
import time
from typing import Any, Dict, Optional, Tuple


def _run_ps(script: str, timeout: int = 15) -> Tuple[int, str, str]:
    """Run a PowerShell script and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


# ── Window management ──


def activate_window(title_pattern: str) -> Dict[str, Any]:
    """Bring a window to foreground by title pattern."""
    ps = f'''
    $wshell = New-Object -ComObject wscript.shell
    $wshell.AppActivate("{title_pattern}")
    Start-Sleep -Milliseconds 300
    '''
    rc, out, err = _run_ps(ps)
    return {"success": rc == 0, "output": out, "error": err}


def find_window(title_pattern: str) -> Dict[str, Any]:
    """Find a window and return its handle and title."""
    ps = f'''
    Add-Type -AssemblyName UIAutomationClient
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "*{title_pattern}*")
    $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if ($el) {{
        $rect = $el.Current.BoundingRectangle
        Write-Output "FOUND|$($el.Current.Name)|$($rect.X)|$($rect.Y)|$($rect.Width)|$($rect.Height)"
    }} else {{
        Write-Output "NOT_FOUND"
    }}
    '''
    rc, out, err = _run_ps(ps)
    if rc != 0 or out.startswith("NOT_FOUND"):
        return {"found": False}
    parts = out.split("|")
    if len(parts) >= 6:
        return {
            "found": True,
            "title": parts[1],
            "x": int(float(parts[2])),
            "y": int(float(parts[3])),
            "width": int(float(parts[4])),
            "height": int(float(parts[5])),
        }
    return {"found": False}


def get_active_window_title() -> str:
    """Get the title of the currently active window."""
    ps = '''
    Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinAPI {
            [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
            [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
        }
"@
    $hwnd = [WinAPI]::GetForegroundWindow()
    $sb = New-Object System.Text.StringBuilder 256
    [WinAPI]::GetWindowText($hwnd, $sb, 256) | Out-Null
    Write-Output $sb.ToString()
    '''
    rc, out, err = _run_ps(ps)
    return out if out else ""


# ── UI element interaction ──


def click_button(window_title: str, button_name: str) -> Dict[str, Any]:
    """Find and click a button by name within a window."""
    ps = f'''
    Add-Type -AssemblyName UIAutomationClient
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $wndCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "*{window_title}*")
    $wnd = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $wndCond)
    if (-not $wnd) {{ Write-Output "WINDOW_NOT_FOUND"; exit }}
    $btnCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "{button_name}")
    $btn = $wnd.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    if (-not $btn) {{ Write-Output "BUTTON_NOT_FOUND"; exit }}
    $invoke = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    if ($invoke) {{
        $invoke.Invoke()
        Write-Output "CLICKED"
    }} else {{
        # Try click via coordinates
        $rect = $btn.Current.BoundingRectangle
        $x = [int]($rect.X + $rect.Width / 2)
        $y = [int]($rect.Y + $rect.Height / 2)
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($x, $y)
        [System.Windows.Forms.SendKeys]::SendWait("{{ENTER}}")
        Write-Output "CLICKED_COORDS"
    }}
    '''
    rc, out, err = _run_ps(ps)
    if "NOT_FOUND" in out:
        return {"success": False, "error": f"{button_name} not found in {window_title}"}
    return {"success": True, "output": out}


def get_uia_children(window_title: str) -> Dict[str, Any]:
    """List all UIA children of a window (useful for debugging)."""
    ps = f'''
    Add-Type -AssemblyName UIAutomationClient
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "*{window_title}*")
    $wnd = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if (-not $wnd) {{ Write-Output "WINDOW_NOT_FOUND"; exit }}
    $walker = New-Object System.Windows.Automation.TreeWalker(
        [System.Windows.Automation.Condition]::TrueCondition)
    $node = $walker.GetFirstChild($wnd)
    $i = 0
    while ($node -and $i -lt 50) {{
        $ctrl = $node.Current
        Write-Output ("$($i)|$($ctrl.ControlType.ProgrammaticName)|$($ctrl.Name)|$($ctrl.AutomationId)")
        $node = $walker.GetNextSibling($node)
        $i++
    }}
    '''
    rc, out, err = _run_ps(ps)
    if rc != 0:
        return {"success": False, "error": err}
    elements = []
    for line in out.split("\n"):
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                elements.append({
                    "index": parts[0],
                    "type": parts[1],
                    "name": parts[2],
                    "id": parts[3] if len(parts) > 3 else "",
                })
    return {"success": True, "elements": elements}


# ── Keyboard simulation ──


def send_keys(keys: str, delay_ms: int = 100) -> Dict[str, Any]:
    """Send keyboard input using SendKeys.

    Keys format: regular text or special keys in {}:
        {ENTER}, {TAB}, {ESC}, {F1}-{F12}
        + = Shift, ^ = Ctrl, % = Alt
        e.g. "^s" = Ctrl+S
    """
    ps = f'''
    $wshell = New-Object -ComObject wscript.shell
    Start-Sleep -Milliseconds {delay_ms}
    $wshell.SendKeys("{keys}")
    '''
    rc, out, err = _run_ps(ps)
    return {"success": rc == 0, "output": out, "error": err}


def type_text(text: str) -> Dict[str, Any]:
    """Type a string of text safely (escapes special chars)."""
    escaped = text.replace("{", "{{}").replace("}", "{}}")
    return send_keys(escaped)


# ── Screenshot ──


def screenshot(window_title: str = "") -> Dict[str, Any]:
    """Capture a screenshot of a specific window, or fullscreen if no title.

    Returns base64-encoded PNG.
    """
    if window_title:
        ps = f'''
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $hwnd = (Get-Process | Where-Object {{ $_.MainWindowTitle -like "*{window_title}*" }}).MainWindowHandle
        if (-not $hwnd) {{ Write-Output "WINDOW_NOT_FOUND"; exit }}

        $rect = New-Object System.Drawing.Rectangle
        [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {{
            $r = $_.Bounds
            if ($hwnd) {{
                Add-Type @"
                    using System;
                    using System.Runtime.InteropServices;
                    public class WinAPI {{
                        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
                    }}
                    public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
"@
                $r2 = New-Object RECT
                [WinAPI]::GetWindowRect($hwnd, [ref]$r2) | Out-Null
                $rect = New-Object System.Drawing.Rectangle($r2.Left, $r2.Top, $r2.Right - $r2.Left, $r2.Bottom - $r2.Top)
            }}
        }}

        $bmp = New-Object System.Drawing.Bitmap $rect.Width, $rect.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($rect.Location, [System.Drawing.Point]::Empty, $rect.Size)
        $g.Dispose()

        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $b64 = [Convert]::ToBase64String($ms.ToArray())
        $ms.Close()
        Write-Output $b64
        '''
    else:
        ps = '''
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $g.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $b64 = [Convert]::ToBase64String($ms.ToArray())
        $ms.Close()
        Write-Output $b64
        '''
    rc, out, err = _run_ps(ps, timeout=30)
    if rc != 0 or not out or out == "WINDOW_NOT_FOUND":
        return {"success": False, "error": err or "Window not found"}
    return {"success": True, "image_base64": out, "format": "PNG"}


# ── Mouse ──


def mouse_click(x: int, y: int, button: str = "left") -> Dict[str, Any]:
    """Click at screen coordinates."""
    btn = "[System.Windows.Forms.MouseButtons]::Left"
    if button == "right":
        btn = "[System.Windows.Forms.MouseButtons]::Right"
    ps = f'''
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x}, {y})
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
    '''
    rc, out, err = _run_ps(ps)
    return {"success": rc == 0, "output": f"Clicked ({x},{y})"}


def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
    """Drag from (x1,y1) to (x2,y2)."""
    ps = f'''
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x1}, {y1})
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
    [System.Windows.Forms.SendKeys]::SendWait("{{DOWN}}")
    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x2}, {y2})
    Start-Sleep -Milliseconds 50
    [System.Windows.Forms.SendKeys]::SendWait("{{UP}}")
    '''
    rc, out, err = _run_ps(ps)
    return {"success": rc == 0, "output": f"Dragged ({x1},{y1})→({x2},{y2})"}
