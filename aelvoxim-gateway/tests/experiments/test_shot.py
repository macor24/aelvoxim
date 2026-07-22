"""Test screenshot only"""
import sys, time, base64, io
sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import screenshot

t0 = time.time()
shot = screenshot("记事本")
print(f"screenshot: {round(time.time()-t0,1)}s", flush=True)
print(f"success: {shot.get('success')}", flush=True)
if shot.get("image_base64"):
    try:
        base64.b64decode(shot["image_base64"])
        print("base64 valid", flush=True)
    except:
        print("base64 invalid", flush=True)
else:
    print("no image", flush=True)
