"""Tests for aelvoxim_gateway.gateway.executor — desktop operation engine.

These tests verify:
- Action handler routing
- Action alias mapping
- _exec_app path resolution

NOTE: These tests require running on Windows (gateway code is Windows-only).
They are skipped on Linux/WSL.
"""
import sys
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Gateway code is Windows-only (PowerShell UIA, Win32 API)",
)


class TestAppResolution:
    """_resolve_known_app must find apps by name match (case-insensitive)."""

    def test_photoshop_found(self):
        """Photoshop must be in known apps"""
        from gateway.executor.__init__ import _KNOWN_APPS, _resolve_known_app
        assert "photoshop" in _KNOWN_APPS
        # Don't assert _resolve_known_app returns a path (depends on installation)

    def test_wechat_found(self):
        """WeChat must be in known apps"""
        from gateway.executor.__init__ import _KNOWN_APPS
        assert "微信" in _KNOWN_APPS

    def test_case_insensitive_lookup(self):
        """'PHOTOSHOP', 'PhotoShop', 'photoshop' should all resolve"""
        from gateway.executor.__init__ import _resolve_known_app
        # These should not crash (may return None if not installed, but shouldn't error)
        for name in ["PHOTOSHOP", "PhotoShop", "photoshop", "PS"]:
            try:
                _resolve_known_app(name)
            except Exception as e:
                pytest.fail(f"resolve_known_app('{name}') raised {e}")

    def test_unknown_app_returns_none(self):
        """Unknown app names should return None, not raise"""
        from gateway.executor.__init__ import _resolve_known_app
        assert _resolve_known_app("nonexistent_app_xyz") is None


class TestActionAliases:
    """Common LLM-generated action names must map to real actions."""

    def test_aliases_map_to_open(self):
        from gateway.executor.__init__ import ACTIONS
        # These are handled by tool_use.py's _ACTION_ALIASES, not ACTIONS directly
        # But ACTIONS should have "open" and "run"
        assert "open" in ACTIONS
        assert "run" in ACTIONS


class TestActionHandlers:
    """Each action must have a valid handler."""

    def test_all_actions_have_callables(self):
        from gateway.executor.__init__ import ACTIONS
        for name, handler in ACTIONS.items():
            assert callable(handler), f"Action '{name}' handler is not callable"
