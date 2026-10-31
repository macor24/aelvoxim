"""P0-4 判据：learner 控制面走 control.json，不再作用于 API 进程内单例。

修前：/admin/learner/stop 调 get_learner().stop()，停的是 API 进程里那个空壳；
worker 的 30s 心跳随后把 status.json 覆写回 running=true。

运行：.venv/bin/python -m pytest tests/test_learner_control_plane_20261030.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import learn_worker  # noqa: E402
from aelvoxim.learn import loop as loop_mod  # noqa: E402
from aelvoxim.server import routes_system  # noqa: E402


@pytest.fixture()
def learner_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(loop_mod, "CONTROL_FILE", tmp_path / "control.json")
    monkeypatch.setattr(loop_mod, "CONTROL_ACK_FILE", tmp_path / "control_ack.json")
    return tmp_path


def _fresh_status(p: Path, running=True, loop_count=7):
    p.write_text(json.dumps({
        "running": running, "direction_count": 2, "total_cycles": 10,
        "active_directions": 1, "total_entries": 5, "last_heartbeat": 1.0,
        "loop_count": loop_count, "last_gate": "quality",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }), encoding="utf-8")


def _stale_status(p: Path):
    old = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps({"running": True, "updated_at": old}), encoding="utf-8")


def test_control_roundtrip_is_atomic(learner_dir):
    payload = loop_mod.write_learner_control("stop", by="admin@test")

    assert loop_mod.read_learner_control()["id"] == payload["id"]
    assert not (learner_dir / "control.tmp").exists(), "必须用 os.replace 原子落盘"


class _FakeLearner:
    def __init__(self):
        self._running = True
        self.calls = []

    def stop(self):
        self.calls.append("stop")
        self._running = False

    def start(self):
        self.calls.append("start")
        self._running = True


def test_apply_control_once_per_id():
    l = _FakeLearner()
    ctrl = {"id": "stop-1", "cmd": "stop"}

    last, res = learn_worker.apply_control(l, ctrl, "")
    assert (last, res) == ("stop-1", "stopped")

    last, res = learn_worker.apply_control(l, ctrl, last)
    assert res == "skip"
    assert l.calls == ["stop"], "同一条指令只能执行一次"


def test_apply_control_unknown_and_error():
    l = _FakeLearner()

    _, res = learn_worker.apply_control(l, {"id": "x-1", "cmd": "restart"}, "")
    assert res == "unknown"

    def _boom():
        raise RuntimeError("factory down")

    l.start = _boom
    _, res = learn_worker.apply_control(l, {"id": "x-2", "cmd": "start"}, "x-1")
    assert res.startswith("error:RuntimeError")


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(routes_system.router)
    app.dependency_overrides[routes_system._require_admin] = lambda: {"email": "admin@test"}
    return TestClient(app)


def test_stop_returns_409_when_worker_absent(learner_dir, client):
    _stale_status(learner_dir / "status.json")

    r = client.post("/admin/learner/stop")

    assert r.status_code == 409
    assert "systemctl" in r.json()["detail"]
    assert not (learner_dir / "control.json").exists(), "worker 不在时不能排指令"


def test_stop_queues_command_for_worker(learner_dir, client):
    _fresh_status(learner_dir / "status.json")

    r = client.post("/admin/learner/stop")

    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True and body["cmd"] == "stop"
    written = json.loads((learner_dir / "control.json").read_text(encoding="utf-8"))
    assert written["cmd"] == "stop" and written["id"] == body["id"]


def test_start_queues_command(learner_dir, client):
    _fresh_status(learner_dir / "status.json", running=False)

    r = client.post("/admin/learner/start")

    assert r.status_code == 200
    assert json.loads((learner_dir / "control.json").read_text(encoding="utf-8"))["cmd"] == "start"


def test_endpoints_no_longer_touch_singleton():
    """静态守卫：start/stop 不得再调 get_learner()。"""
    src = Path("src/aelvoxim/server/routes_system.py").read_text(encoding="utf-8")
    block = src.split('@router.post("/admin/learner/start")', 1)[1].split("@router.get", 1)[0]

    assert "get_learner" not in block
    assert "write_learner_control" in src
