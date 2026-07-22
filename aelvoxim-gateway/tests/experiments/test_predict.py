"""Test PaddleOCR predict() API"""
import sys, time, base64, io, numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot, find_window

shot = screenshot("记事本")
img_data = base64.b64decode(shot["image_base64"])
pil_img = Image.open(io.BytesIO(img_data))

win = find_window("记事本")
if win.get("found"):
    x, y, w, h = win["x"], win["y"], win["width"], win["height"]
    pil_img = pil_img.crop((max(0,x), max(0,y), min(w, pil_img.width-x), min(h, pil_img.height-y)))
img = np.array(pil_img.convert("RGB"))
print(f"Image: {img.shape}", flush=True)

t0 = time.time()
from paddleocr import PaddleOCR

t1 = time.time()
ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
print(f"init: {round(time.time()-t1,1)}s", flush=True)

t2 = time.time()
result = ocr.predict(img)
print(f"predict: {round(time.time()-t2,1)}s", flush=True)

if result and len(result) > 0:
    page = result[0]
    if isinstance(page, dict):
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        boxes = page.get("rec_boxes", [])
        for i in range(min(len(texts), 5)):
            txt = texts[i]
            conf = scores[i] if i < len(scores) else 0
            print(f"  [{conf:.2f}] {txt}", flush=True)
    else:
        print(f"  unexpected type: {type(page)}", flush=True)
else:
    print("no result", flush=True)

print(f"total: {round(time.time()-t0,1)}s", flush=True)
