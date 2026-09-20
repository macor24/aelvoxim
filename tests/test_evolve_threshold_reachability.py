"""The evolve gate must stay inside the range the score can actually reach.

2026-09-19: auto-tune walked metacog.evolve_threshold up to exactly 0.5 while
the highest overall_score ever recorded was 0.371, so the evolution gate could
never open. The old self-check tested `> 0.5` and therefore never caught a value
sitting on the boundary. These tests pin the reachability rule.
"""
import pytest

CEILING = 0.35  # AELVOXIM_EVOLVE_CEILING default


@pytest.fixture()
def cal(monkeypatch):
    from aelvoxim.core.calibration import Calibration
    # auto_tune is edition-gated; enable it for the test and never let a test
    # touch the real calibration file
    monkeypatch.setattr("aelvoxim.server.edition.get",
                        lambda key, default=False: True, raising=False)
    c = Calibration()
    monkeypatch.setattr(c, "save", lambda: None)
    return c


def _analysis(hit_rate=0.0):
    return {"hit_rate": hit_rate, "judge_error_rate": 0.0, "by_type": {},
            "total_proposals": 0, "total_applied": 0, "failure_report": {}}


def test_drifted_threshold_is_pulled_back_into_reachable_band(cal):
    cal._data.setdefault("metacog", {})["evolve_threshold"] = 0.5
    cal.auto_tune(_analysis())
    assert cal._data["metacog"]["evolve_threshold"] <= CEILING


def test_low_hit_rate_cannot_raise_threshold_out_of_reach(cal):
    cal._data.setdefault("metacog", {})["evolve_threshold"] = 0.10
    for _ in range(10):  # auto-tune keeps raising while the hit rate is low
        cal.auto_tune(_analysis(hit_rate=0.0))
        assert cal._data["metacog"]["evolve_threshold"] <= CEILING


def test_threshold_at_the_old_boundary_is_no_longer_sticky(cal):
    """0.5 is the exact value the old `> 0.5` check could never catch."""
    cal._data.setdefault("metacog", {})["evolve_threshold"] = 0.5
    changes = cal.auto_tune(_analysis())
    assert cal._data["metacog"]["evolve_threshold"] != 0.5, f"stuck at 0.5 ({changes})"
