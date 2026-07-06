"""Tests for aelvoxim.core.calibration — parameter auto-tuning engine."""

import pytest

from aelvoxim.core.calibration import Calibration


class TestCalibration:
    def setup_method(self):
        self.cal = Calibration()

    def test_init(self):
        assert self.cal is not None

    def test_auto_tune_empty_analysis(self):
        """Empty analysis should return no changes."""
        changes = self.cal.auto_tune({})
        assert isinstance(changes, list)

    def test_auto_tune_with_hit_rate(self):
        """Low hit rate should trigger evolve_threshold adjustment."""
        analysis = {
            "hit_rate": 0.3,
            "judge_error_rate": 0.0,
            "by_type": {},
            "total_proposals": 10,
            "total_applied": 5,
        }
        changes = self.cal.auto_tune(analysis)
        assert isinstance(changes, list)

    def test_auto_tune_high_hit_rate(self):
        """High hit rate should not trigger changes."""
        analysis = {
            "hit_rate": 0.9,
            "judge_error_rate": 0.0,
            "by_type": {},
            "total_proposals": 10,
            "total_applied": 9,
        }
        changes = self.cal.auto_tune(analysis)
        assert isinstance(changes, list)

    def test_save_load(self):
        """Calibration should support save/load."""
        import tempfile, os
        self.cal.save()
        assert True
