"""Test OCR pipeline on Windows"""
import sys, json, time, base64, io, numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot

# 1. 截图
t0 = time.time()
shot = screenshot("记事本")
print(f"screenshot: success={shot.get('success')}", flush=True)
if not shot.get("success"):
    print(f"  error: {shot.get('error')}", flush=True)
    exit()

img_b64 = shot.get("image_base64", "")
print(f"  base64 len: {len(img_b64)}", flush=True)
print(f"  starts with: {repr(img_b64[:30])}", flush=True)

# 2. Decode to numpy
try:
    img_data = base64.b64decode(img_b64)
    img = Image.open(io.BytesIO(img_data))
    img_np = np.array(img.convert("RGB"))
    print(f"  decoded: {img_np.shape}", flush=True)
except Exception as e:
    print(f"  decode failed: {e}", flush=True)
    exit()

# 3. Try PaddleOCR directly
t1 = time.time()
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
    print(f"  PaddleOCR init: {round(time.time()-t1,1)}s", flush=True)
    t2 = time.time()
    result = ocr.ocr(img_np)
    print(f"  OCR run: {round(time.time()-t2,1)}s", flush=True)
    if result and result[0]:
        for line in result[0][:5]:
            print(f"  text: {line[1][0]} conf: {line[1][1]}", flush=True)
    else:
        print("  no text found", flush=True)
except Exception as e:
    print(f"  PaddleOCR error: {e}", flush=True)
    # Fallback: try pyteesract
    try:
        import pytesseract
        t3 = time.time()
        _pil = Image.fromarray(img_np)
        text = pytesseract.image_to_string(_pil, lang="chi_sim+eng")
        print(f"  Tesseract fallback ({round(time.time()-t3,1)}s): {text[:200]}", flush=True)
    except Exception as e2:
        print(f"  Tesseract also failed: {e2}", flush=True)
