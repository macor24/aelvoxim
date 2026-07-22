"""Test ocr_worker subprocess directly"""
import sys, os, time, json, subprocess

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot, find_window
import base64, io
from PIL import Image
import numpy as np

# Screenshot + crop
shot = screenshot("记事本")
img_data = base64.b64decode(shot["image_base64"])
pil_img = Image.open(io.BytesIO(img_data))

win = find_window("记事本")
if win.get("found"):
    x, y, w, h = win["x"], win["y"], win["width"], win["height"]
    pil_img = pil_img.crop((max(0,x), max(0,y), min(w, pil_img.width-x), min(h, pil_img.height-y)))

tmp_img = os.environ.get("TEMP", ".") + "\\ael_test_input.png"
tmp_out = os.environ.get("TEMP", ".") + "\\ael_test_output.json"
pil_img.save(tmp_img)
print(f"Saved: {tmp_img}", flush=True)

t0 = time.time()
worker = r"C:\Aelvoxim\aelvoxim-gateway\ocr_worker.py"
r = subprocess.run(
    [sys.executable, worker, "--input", tmp_img, "--output", tmp_out, "--lang", "ch"],
    timeout=120, capture_output=True,
)
print(f"Worker: rc={r.returncode}, stderr={r.stderr.decode()[:200]}", flush=True)

if os.path.exists(tmp_out):
    with open(tmp_out, "r", encoding="utf-8") as f:
        result = json.load(f)
    print(f"Result: success={result.get('success')}", flush=True)
    print(f"Blocks: {result.get('block_count')}", flush=True)
    if result.get("full_text"):
        print(f"Text: {result['full_text'][:200]}", flush=True)
    if result.get("error"):
        print(f"Error: {result['error']}", flush=True)
    print(f"Elapsed: {result.get('elapsed')}s", flush=True)
    os.remove(tmp_out)
else:
    print("No output file", flush=True)

os.remove(tmp_img)
print(f"Total: {round(time.time()-t0,1)}s", flush=True)
