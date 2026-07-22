"""Integration tests for desktop operations via Gateway HTTP API.

These tests require:
1. Gateway running on Windows (port 9705)
2. WSL can reach Windows host

Tests are skipped if Gateway is unreachable.
"""
import urllib.request, json
import pytest

GATEWAY_HOST = ""


def _gw():
    """Get Gateway base URL (lazy resolve)."""
    global GATEWAY_HOST
    if not GATEWAY_HOST:
        import subprocess
        r = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        gw_ip = r.stdout.strip().split()[2] if r.stdout.strip() else "127.0.0.1"
        GATEWAY_HOST = f"http://{gw_ip}:9705"
    return GATEWAY_HOST


def _is_gateway_available() -> bool:
    try:
        r = urllib.request.urlopen(f"{_gw()}/api/status", timeout=5)
        return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_gateway_available(),
    reason="Gateway (Windows, port 9705) not reachable",
)

def _exec(op: dict, timeout: int = 15) -> dict:
    body = json.dumps({"operation": op}).encode()
    req = urllib.request.Request(
        f"{_gw()}/api/execute", data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class TestDesktopOperations:
    """Basic desktop operations must succeed."""

    def test_open_notepad(self):
        result = _exec({"action": "open", "target": "notepad.exe"}, timeout=15)
        assert result.get("success") is True, f"open notepad failed: {result.get('error')}"

    def test_find_notepad(self):
        import time
        for _ in range(3):
            result = _exec({"action": "find_window", "target": "记事本"}, timeout=15)
            if result.get("found"):
                return
            time.sleep(2)
        assert result.get("found") is True, f"find_window notepad failed after retry: {result}"

    def test_activate_notepad(self):
        result = _exec({"action": "activate_window", "target": "记事本"}, timeout=15)
        assert result.get("success") is True, f"activate notepad failed: {result}"

    def test_type_text_to_notepad(self):
        result = _exec({
            "action": "type_text",
            "target": "Aelvoxim test input",
            "params": {"window": "记事本"},
        }, timeout=15)
        assert result.get("success") is True, f"type_text failed: {result}"

    def test_screenshot(self):
        result = _exec({"action": "screenshot", "target": ""}, timeout=30)
        assert result.get("success") is True, f"screenshot failed: {result}"
        assert "image_base64" in result, "screenshot missing image_base64"

    def test_screenshot_window(self):
        result = _exec({"action": "screenshot", "target": "记事本"}, timeout=30)
        assert result.get("success") is True, f"screenshot(notepad) failed: {result}"
        assert "image_base64" in result

    def test_focus_cache(self):
        """type_text without window param should activate last focused window."""
        _exec({"action": "activate_window", "target": "记事本"}, timeout=15)
        result = _exec({
            "action": "type_text",
            "target": "Focus cache test",
        }, timeout=15)
        assert result.get("success") is True, f"focus cache failed: {result}"
