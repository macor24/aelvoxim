"""Test full pipeline: open -> screenshot -> crop -> find -> OCR"""
import sys, time, base64, io, numpy as np
from PIL import Image

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot, find_window
import subprocess

# 1. Open notepad
subprocess.Popen(["notepad.exe"])
time.sleep(2)

# 2. Screenshot
t0 = time.time()
shot = screenshot("记事本")
print(f"screenshot: {round(time.time()-t0,1)}s", flush=True)
print(f"  success: {shot.get('success')}", flush=True)

img_data = base64.b64decode(shot["image_base64"])

# 3. Find window
t1 = time.time()
win = find_window("记事本")
print(f"find_window: {round(time.time()-t1,1)}s", flush=True)
print(f"  {win}", flush=True)

if win.get("found"):
    pil_img = Image.open(io.BytesIO(img_data))
    x, y, w, h = win["x"], win["y"], win["width"], win["height"]
    pil_img = pil_img.crop((max(0,x), max(0,y), min(w, pil_img.width-x), min(h, pil_img.height-y)))
    img = np.array(pil_img.convert("RGB"))
    print(f"cropped: {img.shape}", flush=True)
    
    # 4. PaddleOCR predict
    from paddleocr import PaddleOCR
    t2 = time.time()
    ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
    print(f"paddle init: {round(time.time()-t2,1)}s", flush=True)
    
    t3 = time.time()
    result = ocr.predict(img)
    print(f"predict: {round(time.time()-t3,1)}s", flush=True)
    
    if result and len(result) > 0:
        page = result[0]
        if isinstance(page, dict):
            texts = page.get("rec_texts", [])
            print(f"  texts: {len(texts)}", flush=True)
            for i in range(min(len(texts), 5)):
                print(f"  [{page.get('rec_scores',[0])[i]:.2f}] {texts[i]}", flush=True)
else:
    print("window not found", flush=True)
    
print(f"total: {round(time.time()-t0,1)}s", flush=True)
