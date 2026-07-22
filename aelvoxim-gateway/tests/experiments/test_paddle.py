"""Test PaddleOCR predict API format"""
import numpy as np, time, json, cv2
from paddleocr import PaddleOCR

t0 = time.time()
ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
print(f"init: {round(time.time()-t0,1)}s", flush=True)

# Create test image with text
img = np.zeros((200, 800, 3), dtype=np.uint8)
img[:] = (255, 255, 255)
cv2.putText(img, "Hello Aelvoxim OCR Test 你好世界", (50, 100),
            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)

t1 = time.time()
result = ocr.predict(img)
print(f"predict: {round(time.time()-t1,1)}s", flush=True)
print(f"type: {type(result)}", flush=True)
print(f"result[0] type: {type(result[0])}", flush=True)
if isinstance(result[0], dict):
    print(f"keys: {list(result[0].keys())}", flush=True)
    # Try common OCR result key
    for key in ["result", "text", "boxes", "ocr", "dt_polys", "rec_text", "dt_result"]:
        if key in result[0]:
            val = result[0][key]
            print(f"'{key}': type={type(val)} str={str(val)[:200]}", flush=True)
    # Also print full dict keys with types
    for k, v in result[0].items():
        if isinstance(v, np.ndarray):
            print(f"  {k}: ndarray {v.shape}", flush=True)
        elif isinstance(v, (list, tuple)):
            print(f"  {k}: list len={len(v)}", flush=True)
        else:
            print(f"  {k}: {type(v).__name__}", flush=True)
