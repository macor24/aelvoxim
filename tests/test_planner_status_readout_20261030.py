"""P0-5 判据：planner 读数一律来自 status.json，过期就明说 stale、计数回 0。

修前：端点读 API 进程内 learner 单例的 `_directions`（上次落盘快照），
`last_heartbeat` 恒 0，且没有过期概念。

运行：.venv/bin/python -m pytest tests/test_planner_status_readout_20261030.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aelvoxim.learn import loop as loop_mod


@pytest.fixture()
def status_file(tmp_path, monkeypatch):
    p = tmp_path / "status.json"
    monkeypatch.setattr(loop_mod, "STATUS_FILE", p)
    return p


def _write(p: Path, **over):
    data = {
        "running": True,
        "direction_count": 3,
        "total_cycles": 128,
        "active_directions": 2,
        "total_entries": 46,
        "last_heartbeat": 1761800000.0,
        "loop_count": 9,
        "last_gate": "quality",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.update(over)
    p.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_fresh_status_is_read_verbatim(status_file):
    _write(status_file)

    st = loop_mod.read_learner_status()

    assert st["stale"] is False
    assert st["total_cycles"] == 128
    assert st["active_directions"] == 2
    assert st["total_entries"] == 46
    assert st["last_heartbeat"] == 1761800000.0
    assert st["running"] is True


def test_stale_status_zeroes_counters(status_file):
    old = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    _write(status_file, updated_at=old)

    st = loop_mod.read_learner_status()

    assert st["stale"] is True
    assert st["total_cycles"] == 0
    assert st["active_directions"] == 0
    assert st["total_entries"] == 0
    assert st["running"] is False
    assert st["age_s"] >= 540


def test_missing_file_is_not_a_zero_reading(status_file):
    assert not status_file.exists()

    st = loop_mod.read_learner_status()

    assert st["stale"] is True
    assert st["total_cycles"] == 0
    assert st["age_s"] is None


def test_save_status_writes_counters(status_file):
    learner = loop_mod.Learner(enable_auto_discover=False, skip_load=True)

    learner._save_status()

    data = json.loads(status_file.read_text(encoding="utf-8"))
    for k in ("total_cycles", "active_directions", "total_entries", "last_heartbeat"):
        assert k in data, f"_save_status 必须写 {k}，否则读端没真值可用"


def test_planner_endpoint_no_longer_reads_singleton():
    """静态守卫：端点不得再读进程内 learner._directions。"""
    src = Path("src/aelvoxim/server/__init__.py").read_text(encoding="utf-8")
    block = src.split('@app.get("/v1/status/planner")', 1)[1].split("@app.", 1)[0]

    assert "read_learner_status" in block
    assert "learner._directions" not in block


def test_other_readouts_use_status_file():
    """routes_system/admin/learner/status 与 routes_brain 也必须走同一读数口。"""
    sys_src = Path("src/aelvoxim/server/routes_system.py").read_text(encoding="utf-8")
    brain_src = Path("src/aelvoxim/server/routes_brain.py").read_text(encoding="utf-8")

    assert "read_learner_status" in sys_src
    assert "l.is_running()" not in sys_src
    assert "read_learner_status" in brain_src
