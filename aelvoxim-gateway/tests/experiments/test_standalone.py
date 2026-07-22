"""Test PaddleOCR init + ocr in Windows, outside Gateway"""
import sys, time, base64, io, numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot, find_window

# 1. Screenshot
t0 = time.time()
shot = screenshot("记事本")
img_data = base64.b64decode(shot["image_base64"])
pil_img = Image.open(io.BytesIO(img_data))

# 2. Crop
win = find_window("记事本")
if win.get("found"):
    x, y, w, h = win["x"], win["y"], win["width"], win["height"]
    pil_img = pil_img.crop((max(0,x), max(0,y), min(w, pil_img.width-x), min(h, pil_img.height-y)))
img = np.array(pil_img.convert("RGB"))
print(f"Image: {img.shape}", flush=True)

# 3. PaddleOCR init
t1 = time.time()
from paddleocr import PaddleOCR
print(f"import: {round(time.time()-t1,1)}s", flush=True)

t2 = time.time()
ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
print(f"init: {round(time.time()-t2,1)}s", flush=True)

# 4. ocr.ocr()
t3 = time.time()
result = ocr.ocr(img)
print(f"ocr: {round(time.time()-t3,1)}s", flush=True)

if result and result[0]:
    for line in result[0][:5]:
        print(f"  text: {line[1][0]}", flush=True)
else:
    print("no text", flush=True)

print(f"total: {round(time.time()-t0,1)}s", flush=True)
