"""Tests for aelvoxim.server.edition and aelvoxim.server.license — edition gating."""

import pytest

from aelvoxim.server.edition import current, get, set_edition


class TestEditionDefaults:
    def test_default_edition(self):
        """Default edition should be community."""
        assert current() == "community"

    def test_community_values(self):
        """Community edition should have all Pro features disabled."""
        assert get("auto_learn") is False
        assert get("curiosity_enabled") is False
        assert get("auto_tune_enabled") is False
        assert get("auto_post_validation") is False
        assert get("gap_analysis_enabled") is False
        assert get("advanced_experts") is False
        assert get("max_experts") == 5
        assert get("enterprise_mode") is False

    def test_unknown_key_returns_default(self):
        assert get("nonexistent", "fallback") == "fallback"


class TestSetEdition:
    def test_set_pro(self):
        set_edition("pro")
        assert current() == "pro"
        assert get("auto_learn") is True
        assert get("curiosity_enabled") is True
        assert get("auto_tune_enabled") is True
        assert get("max_experts") == 12
        assert get("advanced_experts") is True

    def test_set_enterprise(self):
        set_edition("enterprise")
        assert current() == "enterprise"
        assert get("enterprise_mode") is True

    def test_set_invalid_edition(self):
        """Invalid edition names should be ignored."""
        set_edition("nonexistent")
        assert current() == "enterprise"

    def test_case_insensitive(self):
        set_edition("PRO")
        assert current() == "pro"

    def test_back_to_community(self):
        set_edition("community")
        assert current() == "community"
        assert get("auto_learn") is False


class TestLicenseImport:
    def test_apply_license_importable(self):
        from aelvoxim.server.license import apply_license
        assert callable(apply_license)

    def test_current_edition_importable(self):
        from aelvoxim.server.license import current_edition
        assert callable(current_edition)
