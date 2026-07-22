"""Test OCR on Windows"""
import time, numpy as np
from PIL import Image
from paddleocr import PaddleOCR

t0 = time.time()
ocr = PaddleOCR(use_textline_orientation=True, lang='ch')
print("Init:", round(time.time()-t0, 1), "s")

# Create test image with text
img = np.zeros((200, 800, 3), dtype=np.uint8)
img[:] = (255, 255, 255)
try:
    import cv2
    cv2.putText(img, "Hello Aelvoxim Test OCR 你好世界", (50, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
except ImportError:
    # Fallback: draw with PIL
    from PIL import ImageDraw, ImageFont
    pil_img = Image.fromarray(img)
    draw = ImageDraw.Draw(pil_img)
    draw.text((50, 100), "Hello Aelvoxim Test OCR 你好世界", fill=(0, 0, 0))
    img = np.array(pil_img)

t1 = time.time()
result = ocr.ocr(img)
print("OCR:", round(time.time()-t1, 1), "s")

if result and result[0]:
    for line in result[0]:
        print("  Text:", line[1][0], "Conf:", line[1][1])
else:
    print("  No text found or empty result")
