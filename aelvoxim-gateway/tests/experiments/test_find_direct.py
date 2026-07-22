"""Direct test of find_window PowerShell on Windows."""
import sys, time
sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import find_window

# First open notepad
from gateway.executor._uia import _run_ps
rc, out, _ = _run_ps("Start-Process notepad.exe; Start-Sleep 2")
print(f"Open notepad: {out}")

# Try several patterns
for pattern in ["记事本", "无标题", "notepad", "Notepad"]:
    result = find_window(pattern)
    print(f"find_window('{pattern}'): {result}")
    time.sleep(0.5)
