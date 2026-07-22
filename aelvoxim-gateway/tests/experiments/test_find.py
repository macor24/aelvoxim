"""Test find_window"""
import sys, time
sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import find_window

t0 = time.time()
win = find_window("记事本")
print(f"find_window: {round(time.time()-t0,1)}s", flush=True)
print(win, flush=True)
