"""Calibration 调参拆分：compute 只算、apply 才落盘（2026-10-19）。

背景：auto_tune 边算边改并立即 _save()，DGM-H 闸门在它之后才检查 ⇒ 拦截是假的。
拆开后"未通过闸门 ⇒ 配置不变"才是可验证的。

注意：compute_tuning 有 edition 闸（community 版禁用 auto_tune），
所以测试里显式放行 edition，否则拿到的是空列表、测不到东西。
"""
import json

import pytest

from aelvoxim.core.calibration import Calibration


@pytest.fixture()
def auto_tune_on(monkeypatch):
    import aelvoxim.server.edition as ed
    monkeypatch.setattr(ed, "get", lambda *a, **k: True)


def test_compute_tuning_does_not_touch_config(tmp_path, auto_tune_on):
    cal = Calibration(project_dir=tmp_path)
    before = cal.get_all()
    changes = cal.compute_tuning({"hit_rate": 0.0, "judge_error_rate": 0.9, "by_type": {}})
    assert changes, "低命中率应产生建议"
    assert cal.get_all() == before, "compute 阶段不许改配置"
    assert not (tmp_path / "calibration.json").exists(), "compute 阶段不许落盘"


def test_apply_tuning_writes_and_persists(tmp_path):
    cal = Calibration(project_dir=tmp_path)
    n = cal.apply_tuning([{"target": "metacog.evolve_threshold", "old": 0.35, "new": 0.10}])
    assert n == 1
    assert cal.get("metacog", "evolve_threshold") == 0.10
    on_disk = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert on_disk["metacog"]["evolve_threshold"] == 0.10


def test_apply_tuning_handles_nested_targets(tmp_path):
    cal = Calibration(project_dir=tmp_path)
    cal.apply_tuning([{"target": "judge.weights.direction", "old": 0.2, "new": 0.23}])
    assert cal.get("judge", "weights", "direction") == 0.23


def test_raise_is_capped_by_reachable_score(tmp_path, monkeypatch, auto_tune_on):
    """门槛不许抬到实际达不到的高度（此前棘轮到 0.35 而最高分 0.065）。"""
    cal = Calibration(project_dir=tmp_path)
    cal._data.setdefault("metacog", {})["evolve_threshold"] = 0.10
    monkeypatch.setattr(Calibration, "_recent_max_overall", lambda self, n=20: 0.065)
    changes = cal.compute_tuning({"hit_rate": 0.0, "judge_error_rate": 0.0, "by_type": {}})
    raised = [c for c in changes if c["target"] == "metacog.evolve_threshold"]
    assert raised == [], f"已到可达上限就不该再抬高，实际产出 {raised}"
    assert cal.get("metacog", "evolve_threshold") == 0.10


def test_raise_still_happens_when_headroom_exists(tmp_path, monkeypatch, auto_tune_on):
    """可达分高于当前门槛时，仍应正常抬高（但不超过可达+0.02）。"""
    cal = Calibration(project_dir=tmp_path)
    cal._data.setdefault("metacog", {})["evolve_threshold"] = 0.05
    monkeypatch.setattr(Calibration, "_recent_max_overall", lambda self, n=20: 0.12)
    changes = cal.compute_tuning({"hit_rate": 0.0, "judge_error_rate": 0.0, "by_type": {}})
    raised = [c for c in changes if c["target"] == "metacog.evolve_threshold"]
    assert raised and raised[0]["new"] <= 0.14 + 1e-9


def test_auto_tune_wrapper_still_applies(tmp_path, auto_tune_on):
    """兼容包装：老调用点仍能拿到"算并应用"的行为。"""
    cal = Calibration(project_dir=tmp_path)
    changes = cal.auto_tune({"hit_rate": 0.0, "judge_error_rate": 0.0, "by_type": {}})
    if changes:
        assert (tmp_path / "calibration.json").exists()
