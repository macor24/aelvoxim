"""Minimal PaddleOCR init test"""
import time
from paddleocr import PaddleOCR
t0 = time.time()
ocr = PaddleOCR(use_textline_orientation=True, lang="ch")
print(f"init: {round(time.time()-t0,1)}s", flush=True)
print("OK", flush=True)
