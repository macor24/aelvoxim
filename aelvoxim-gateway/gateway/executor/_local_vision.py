"""
免费本地视觉模块：基于 uiautomation 定位窗口 + Windows 内置 OCR
"""
from __future__ import annotations

import numpy as np
import pyautogui
import pyperclip
import time
import logging
import cv2
import base64
import io
import subprocess
import os
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def _windows_ocr(image_b64: str) -> List[Dict[str, Any]]:
    """调用 Windows 内置 OCR 引擎（PowerShell），零依赖，支持中文"""
    ps_script = f'''
Add-Type -AssemblyName System.Drawing
$b64 = "{image_b64}"
$ms = New-Object System.IO.MemoryStream([Convert]::FromBase64String($b64))
$bmp = [System.Drawing.Bitmap]::FromStream($ms)
$ms.Close()
$tmp = [System.IO.Path]::GetTempFileName() + ".png"
$bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] > $null
$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $ocr) {{ exit 1 }}
$file = [Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp).GetResults()
$stream = [Windows.Storage.Streams.FileRandomAccessStream]::OpenAsync($tmp, 0).GetResults()
$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).GetResults()
$softwareBitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($decoder.GetSoftwareBitmapAsync().GetResults(), 0)
$result = $ocr.RecognizeAsync([Windows.Graphics.Imaging.SoftwareBitmap]::CreateCopyFromBuffer($softwareBitmap.PixelBuffer, 0, $softwareBitmap.PixelWidth, $softwareBitmap.PixelHeight)).GetResults()
foreach ($line in $result.Lines) {{
    foreach ($word in $line.Words) {{
        $r = $word.BoundingRect
        Write-Output ("WORD|$($word.Text)|$($r.X)|$($r.Y)|$($r.Width)|$($r.Height)")
    }}
}}
Remove-Item $tmp -Force
'''
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        items = []
        for line in proc.stdout.splitlines():
            if line.startswith("WORD|"):
                parts = line.split("|")
                if len(parts) >= 6:
                    items.append({
                        "text": parts[1],
                        "box": [int(float(parts[2])), int(float(parts[3])),
                                int(float(parts[2])) + int(float(parts[4])),
                                int(float(parts[3])) + int(float(parts[5]))],
                        "confidence": 1.0
                    })
        return items
    except Exception as e:
        logger.error(f"Windows OCR 失败: {e}")
        return []


def capture_wechat_window() -> Optional[np.ndarray]:
    """通过 uiautomation 获取微信窗口坐标并截图（无需激活窗口）"""
    try:
        import uiautomation as auto
        auto.SetGlobalSearchTimeout(2)
        wechat = auto.WindowControl(ClassName='Qt51514QWindowIcon')
        if not wechat.Exists():
            wechat = auto.WindowControl(Name='微信')
        if not wechat.Exists():
            logger.error("微信窗口未找到")
            return None
        rect = wechat.BoundingRectangle
        # 截图窗口区域
        img = pyautogui.screenshot(region=(rect.left, rect.top, rect.width(), rect.height()))
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.error(f"无法捕获微信窗口: {e}")
        return None


def ocr_read_text(image: np.ndarray) -> List[Dict[str, Any]]:
    """对图像进行 OCR，返回识别到的文本和位置"""
    # 将 numpy 数组转为 base64
    _, buffer = cv2.imencode('.png', image)
    img_b64 = base64.b64encode(buffer).decode()
    return _windows_ocr(img_b64)


def wechat_send_message(text: str) -> bool:
    img = capture_wechat_window()
    if img is None:
        return False

    ocr_results = ocr_read_text(img)
    input_region = None
    for item in ocr_results:
        if '输入' in item.get('text', ''):
            input_region = item['box']
            break

    if input_region:
        # box 格式: [x1, y1, x2, y2]
        x = (input_region[0] + input_region[2]) // 2
        y = input_region[3] + 20  # 输入框下方一点
        win_rect = _get_wechat_rect()
        pyautogui.click(win_rect[0] + x, win_rect[1] + y)
        time.sleep(0.2)
    else:
        logger.warning("未找到输入区域，尝试固定坐标")
        rect = _get_wechat_rect()
        pyautogui.click(rect[0] + 300, rect[1] + rect[3] - 50)

    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('backspace')
    time.sleep(0.1)
    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.2)
    pyautogui.press('enter')
    return True


def wechat_read_messages(max_lines: int = 10) -> List[str]:
    img = capture_wechat_window()
    if img is None:
        return []

    ocr_results = ocr_read_text(img)
    if not ocr_results:
        return []

    ocr_results.sort(key=lambda x: x['box'][1])
    messages = [item['text'] for item in ocr_results[-max_lines:]]
    return messages


def _get_wechat_rect():
    """获取微信窗口屏幕坐标 (left, top, width, height)"""
    try:
        import uiautomation as auto
        auto.SetGlobalSearchTimeout(2)
        wechat = auto.WindowControl(ClassName='Qt51514QWindowIcon')
        if not wechat.Exists():
            wechat = auto.WindowControl(Name='微信')
        if wechat.Exists():
            r = wechat.BoundingRectangle
            return (r.left, r.top, r.width(), r.height())
    except Exception:
        pass
    return (0, 0, 0, 0)