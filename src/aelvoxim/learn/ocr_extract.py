"""ocr_extract — OCR 图片/PDF 文字提取

支持 PaddleOCR 和 EasyOCR 作为后端。
使用子进程方式避免 GPU 内存占用问题。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("aelvoxim.ocr_extract")


def ocr_extract(path: Path) -> Optional[str]:
    """Extract text from an image or PDF using OCR.

    Tries PaddleOCR first, then EasyOCR.
    Returns extracted text or None if both fail.
    """
    result = _try_paddleocr(path)
    if result:
        return result
    result = _try_easyocr(path)
    if result:
        return result
    return None


def _try_paddleocr(path: Path) -> Optional[str]:
    """Try PaddleOCR via subprocess."""
    try:
        code = (
            "import sys; import json; "
            "from paddleocr import PaddleOCR; "
            f"ocr = PaddleOCR(use_angle_cls=True, lang='ch', use_gpu=False, show_log=False); "
            f"result = ocr.ocr('{path}', cls=True); "
            "texts = []; "
            "if result and result[0]: "
            "    for line in result[0]: "
            "        texts.append(line[1][0]); "
            "print(json.dumps({'text': '\\n'.join(texts)}, ensure_ascii=False))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip())
            text = data.get("text", "").strip()
            if len(text) > 5:
                return text
    except Exception as e:
        log.debug("PaddleOCR failed: %s", e)
    return None


def _try_easyocr(path: Path) -> Optional[str]:
    """Try EasyOCR via subprocess."""
    try:
        code = (
            "import sys; import json; "
            "import easyocr; "
            f"reader = easyocr.Reader(['ch_sim', 'en'], gpu=False); "
            f"result = reader.readtext('{path}', detail=0); "
            "print(json.dumps({'text': '\\n'.join(result)}, ensure_ascii=False))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip())
            text = data.get("text", "").strip()
            if len(text) > 5:
                return text
    except Exception as e:
        log.debug("EasyOCR failed: %s", e)
    return None
