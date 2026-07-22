"""Test RapidOCR on Windows"""
import sys, time, json, base64, io, numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot

# 截图
t0 = time.time()
shot = screenshot("记事本")
print(f"screenshot: success={shot.get('success')}", flush=True)

img_b64 = shot.get("image_base64", "")
img_data = base64.b64decode(img_b64)
img = np.array(Image.open(io.BytesIO(img_data)).convert("RGB"))
print(f"image shape: {img.shape}", flush=True)

# RapidOCR
t1 = time.time()
engine = RapidOCR()
print(f"init: {round(time.time()-t1,1)}s", flush=True)

t2 = time.time()
result = engine(img)
print(f"ocr: {round(time.time()-t2,1)}s", flush=True)

if result:
    boxes, texts, scores = result
    print(f"boxes: {len(boxes)}, texts: {len(texts)}, scores: {len(scores)}", flush=True)
    for i in range(min(len(texts), 10)):
        print(f"  [{scores[i]:.2f}] {texts[i]}", flush=True)
else:
    print("no result", flush=True)
print(f"total: {round(time.time()-t0,1)}s", flush=True)
