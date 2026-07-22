"""Test find_window with Chinese window title remotely."""
import sys, urllib.request, json

sys.path.insert(0, r"C:\Aelvoxim\aelvoxim-gateway")
from gateway.executor._uia import find_window, screenshot

# Test find_window directly on Windows
result = find_window("记事本")
print(f"find_window('记事本'): {result}")

result2 = find_window("无标题")
print(f"find_window('无标题'): {result2}")

result3 = find_window("notepad")
print(f"find_window('notepad'): {result3}")
