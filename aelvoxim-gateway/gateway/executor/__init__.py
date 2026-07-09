# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.executor — Dual-mode desktop execution engine.

Mode A: UIA / Accessibility — Windows UI Automation (primary).
Mode B: VLM — Visual Language Model fallback (Pro feature).
"""
from . import _uia
from typing import Any, Dict
import subprocess
import shlex

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
    "run_script": lambda op: _uia.send_keys(""),
    "run": lambda op: _exec_app(op.get("target", "")),
    "open": lambda op: _exec_app(op.get("params", {}).get("path", op.get("target", ""))),
    "ocr_screenshot": lambda op: _ocr_screenshot(op.get("target", "")),
}


# ── Helper: launch app / executable ──


def _exec_app(path: str) -> Dict[str, Any]:
    """Launch an app or executable. Uses shlex.split + shell=False to prevent injection."""
    if not path:
        return {"success": False, "error": "exec requires path or target"}
    try:
        parts = shlex.split(path)
        r = subprocess.Popen(parts)
        return {"success": True, "output": f"Started: {path} (PID {r.pid})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Windows built-in OCR (no dependencies required) ──


def _ocr_windows_fallback(image_base64: str) -> Dict[str, Any]:
    """OCR via Windows built-in Windows.Media.Ocr (PowerShell).

    Zero dependencies — uses Windows 10+ built-in OCR engine.
    Supports Chinese (Simplified) and English.
    """
    ps = (
        # Save base64 image to temp file
        'Add-Type -AssemblyName System.Drawing'
        '\n$b64 = """' + image_base64 + '"""'
        '\n$ms = New-Object System.IO.MemoryStream([Convert]::FromBase64String($b64))'
        '\n$bmp = [System.Drawing.Bitmap]::FromStream($ms)'
        '\n$ms.Close()'
        # Save to temp PNG
        '\n$tmp = [System.IO.Path]::GetTempFileName() + ".png"'
        '\n$bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)'
        '\n$bmp.Dispose()'
        # Windows.Media.Ocr
        '\nAdd-Type -AssemblyName Windows.Foundation.UniversalApiContract'
        '\n$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()'
        '\nif (-not $ocr) { Write-Output "ERR_NO_OCR"; exit }'
        '\n$img = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync('
        '  [Windows.Storage.Streams.RandomAccessStream]::CreateAsync('
        '    [System.IO.File]::OpenRead($tmp)))'
        '\n$result = $ocr.RecognizeAsync($img).GetResults()'
        '\nforeach ($line in $result.Lines) {'
        '  foreach ($word in $line.Words) {'
        '    $r = $word.BoundingRect'
        '    Write-Output ("WORD|$($word.Text)|$($r.X)|$($r.Y)|$($r.Width)|$($r.Height)|$($word.Text)")'
        '  }'
        '}'
        # Cleanup
        '\nRemove-Item $tmp -Force'
    )
    rc, out, err = _uia._run_ps(ps, timeout=60)
    if rc != 0 or not out or out == "ERR_NO_OCR":
        raise RuntimeError(err or "Windows OCR failed")

    text_blocks = []
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("WORD|"):
            parts = line.split("|")
            if len(parts) >= 6:
                text_blocks.append({
                    "text": parts[1],
                    "x": int(float(parts[2])),
                    "y": int(float(parts[3])),
                    "w": int(float(parts[4])),
                    "h": int(float(parts[5])),
                    "confidence": 1.0,
                })
    return {"text_blocks": text_blocks, "full_text": ""}


# ── OCR screenshot (PaddleOCR/EasyOCR/Windows built-in) ──


_OCR_AVAILABLE = None


def _ocr_screenshot(window_title: str = "") -> Dict[str, Any]:
    """Take a screenshot and run OCR on it. Returns text blocks with bounding boxes.

    Args:
        window_title: Window title to capture (empty = fullscreen).

    Returns:
        {"success": bool, "text_blocks": [{"text": str, "x": int, "y": int, "w": int, "h": int}],
         "full_text": str, "error": str}
    """
    import base64, io

    # 1. Take screenshot
    shot = _uia.screenshot(window_title)
    if not shot.get("success"):
        return {"success": False, "error": shot.get("error", "Screenshot failed")}

    # 2. Decode base64 to numpy array
    try:
        import numpy as np
        from PIL import Image
        img_data = base64.b64decode(shot["image_base64"])
        img = Image.open(io.BytesIO(img_data))
        img = np.array(img.convert("RGB"))  # PaddleOCR needs numpy RGB
    except ImportError:
        return {"success": False, "error": "PIL not available"}
    except Exception as e:
        return {"success": False, "error": f"Image decode failed: {e}"}

    # 3. OCR — try multiple engines
    global _OCR_AVAILABLE
    text_blocks = []
    full_text_parts = []
    ocr_error = ""

    # Try PaddleOCR first (models cached locally)
    try:
        from paddleocr import PaddleOCR
        _OCR_AVAILABLE = True
        ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        result = ocr.ocr(img)
        if result and result[0]:
            for line in result[0]:
                box = line[0]
                text = line[1][0]
                confidence = line[1][1]
                x = int(min(p[0] for p in box))
                y = int(min(p[1] for p in box))
                w = int(max(p[0] for p in box) - x)
                h = int(max(p[1] for p in box) - y)
                text_blocks.append({
                    "text": text, "x": x, "y": y, "w": w, "h": h,
                    "confidence": round(float(confidence), 3),
                })
                full_text_parts.append(text)
    except Exception:
        pass

    # Fallback: pytesseract (Tesseract OCR, lightweight)
    if not text_blocks:
        try:
            import pytesseract
            _OCR_AVAILABLE = True
            from PIL import Image as _PIL
            _pil_img = _PIL.fromarray(img)
            _data = pytesseract.image_to_data(_pil_img, lang="chi_sim+eng", output_type=pytesseract.Output.DICT)
            for i in range(len(_data["text"])):
                txt = _data["text"][i].strip()
                if not txt:
                    continue
                text_blocks.append({
                    "text": txt,
                    "x": _data["left"][i], "y": _data["top"][i],
                    "w": _data["width"][i], "h": _data["height"][i],
                    "confidence": float(_data["conf"][i]) / 100.0 if _data["conf"][i] != -1 else 0.5,
                })
                full_text_parts.append(txt)
        except ImportError:
            ocr_error = "No OCR engine (install: pip install pytesseract)"
        except Exception as e:
            ocr_error = f"Tesseract failed: {e}"

    # Last resort: EasyOCR if installed
    if not text_blocks:
        try:
            import easyocr
            _OCR_AVAILABLE = True
            reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
            result = reader.readtext(img)
            for box, text, confidence in result:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                text_blocks.append({
                    "text": text,
                    "x": int(min(xs)), "y": int(min(ys)),
                    "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys)),
                    "confidence": round(float(confidence), 3),
                })
                full_text_parts.append(text)
        except Exception:
            pass

    if not text_blocks and ocr_error:
        return {
            "success": True,
            "ocr_unavailable": True,
            "text_blocks": [],
            "full_text": "",
            "image_base64": shot["image_base64"],
            "error": ocr_error,
        }

    return {
        "success": True,
        "text_blocks": text_blocks,
        "full_text": "\n".join(full_text_parts),
        "block_count": len(text_blocks),
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
        return _exec_app(operation.get("params", {}).get("path", operation.get("target", "")))
    if action == "open_notepad":
        return _exec_app("notepad.exe")
    if action == "open":
        return _exec_app(operation.get("params", {}).get("path", operation.get("target", "")))
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
