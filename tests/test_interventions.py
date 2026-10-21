"""最小闭环（interventions）：开实验 → 24h 判成败 → 保留或回退。

约束：单实验、48h 冷却、只收紧、下限 4、提升 ≥10% 才算成功。
"""
import json
import time

import pytest

from aelvoxim.core import interventions as iv


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import aelvoxim.utils as u
    monkeypatch.setattr(u, "METACORE_DIR", tmp_path)
    # 用内存里的假 calibration 代替真实配置文件
    class FakeCal:
        def __init__(self):
            self.data = {10: 10}
            self.value = 10

        def get(self, *keys, default=None):
            if keys and keys[-1] == "max_active_directions":
                return self.value
            if keys and keys[0] == "interventions":
                return default
            return default

        def apply_tuning(self, changes):
            self.value = int(changes[0]["new"])
            return 1

    fake = FakeCal()
    monkeypatch.setattr(iv, "current_cap", lambda: fake.value)
    monkeypatch.setattr(iv, "_set_cap", lambda v: setattr(fake, "value", int(v)))
    return fake, tmp_path


def test_open_lowers_cap_and_logs(env):
    fake, tmp = env
    eid = iv.open_experiment("success_rate low", baseline=2.0, log=lambda s: None)
    assert eid and fake.value == 9
    recs = [json.loads(l) for l in (tmp / "interventions.jsonl").read_text().splitlines()]
    assert recs[0]["type"] == "open" and recs[0]["old"] == 10 and recs[0]["new"] == 9


def test_only_one_experiment_at_a_time(env):
    fake, _ = env
    assert iv.open_experiment("a", 2.0, log=lambda s: None)
    assert iv.open_experiment("b", 2.0, log=lambda s: None) is None
    assert fake.value == 9


def test_keep_when_improved(env):
    fake, tmp = env
    eid = iv.open_experiment("low", baseline=2.0, log=lambda s: None)
    v = iv.close_experiment(eid, after=2.5, log=lambda s: None)   # +25% > 10%
    assert v == "keep" and fake.value == 9


def test_revert_when_not_improved(env):
    fake, _ = env
    eid = iv.open_experiment("low", baseline=2.0, log=lambda s: None)
    v = iv.close_experiment(eid, after=2.05, log=lambda s: None)  # +2.5%
    assert v == "revert" and fake.value == 10


def test_floor_and_cooldown(env):
    fake, _ = env
    for _ in range(10):
        eid = iv.open_experiment("low", baseline=1.0, log=lambda s: None)
        if eid is None:
            break
        iv.close_experiment(eid, after=0.1, log=lambda s: None)  # 回退，但触发冷却
        # 跳过冷却以便继续降
        import aelvoxim.core.interventions as m
        m.COOLDOWN_HOURS = 0
    assert fake.value >= iv.MIN_CAP
