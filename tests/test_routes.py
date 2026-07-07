"""Tests for metacore.server.routes — health, log endpoints, mask function."""

from aelvoxim.server.routes import _mask_api_key as _m
from aelvoxim.core.metacog_monitor import get_ethics_gate, set_ethics_gate


def test_mask_api_key_routes():
    """API key masking in routes should work."""
    assert "***" in _m("Bearer ***"), "API key should be masked"
    assert _m("Bearer ***") == "Bearer ***", "Short non-key unchanged"


def test_ethics_gate_access():
    """ETHICS_GATES can be accessed via get/set functions."""
    set_ethics_gate("L5_rate_limit", True, "restored")
    assert get_ethics_gate("L5_rate_limit") is True
