#!/usr/bin/env python3
"""
ocr_worker.py — Standalone OCR worker for Aelvoxim Gateway.

Called as a subprocess by the Gateway to avoid PaddleOCR deadlock inside uvicorn.
Usage:
    python ocr_worker.py --input <image.png> --output <result.json> [--lang ch]
"""
import sys, json, base64, io, os, time
import numpy as np
from PIL import Image

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to input PNG image")
    parser.add_argument("--output", required=True, help="Path to write JSON result")
    parser.add_argument("--lang", default="ch", help="OCR language")
    args = parser.parse_args()

    t0 = time.time()

    # 1. Load image
    try:
        img = np.array(Image.open(args.input).convert("RGB"))
    except Exception as e:
        _fail(args.output, f"Image load failed: {e}")
        return

    # 2. Init PaddleOCR (loads models here, one-time cost per subprocess)
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_textline_orientation=True, lang=args.lang)
    except Exception as e:
        _fail(args.output, f"PaddleOCR init failed: {e}")
        return

    # 3. OCR
    try:
        result = ocr.predict(img)
    except Exception as e:
        _fail(args.output, f"OCR failed: {e}")
        return

    # 4. Parse result
    text_blocks = []
    full_text_parts = []
    if result and len(result) > 0:
        page = result[0]
        if isinstance(page, dict):
            texts = page.get("rec_texts", [])
            scores = page.get("rec_scores", [])
            boxes = page.get("rec_boxes", [])
            polys = page.get("dt_polys", [])
            for i in range(len(texts)):
                txt = texts[i]
                conf = scores[i] if i < len(scores) else 0.5
                x, y, w, h = 0, 0, 0, 0
                if i < len(boxes):
                    b = boxes[i]
                    if hasattr(b, "__iter__") and len(b) >= 4:
                        x, y, w, h = int(b[0]), int(b[1]), int(b[2]) - int(b[0]), int(b[3]) - int(b[1])
                elif i < len(polys):
                    pts = polys[i]
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    x, y, w, h = int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys))
                if txt:
                    text_blocks.append({
                        "text": txt, "x": x, "y": y, "w": w, "h": h,
                        "confidence": round(float(conf), 3),
                    })
                    full_text_parts.append(txt)

    elapsed = round(time.time() - t0, 1)
    output = {
        "success": True,
        "text_blocks": text_blocks,
        "full_text": "\n".join(full_text_parts),
        "block_count": len(text_blocks),
        "elapsed": elapsed,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)
    # Cleanup input file
    try:
        os.remove(args.input)
    except Exception:
        pass


def _fail(output_path: str, error: str):
    """Write error result and exit."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"success": False, "error": error, "text_blocks": [], "full_text": "", "block_count": 0}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
