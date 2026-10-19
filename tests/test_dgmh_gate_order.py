"""DGM-H 闸门：先审后改 / 无判分器不伪造 B / 冷却（2026-10-19）。

背景：`auto_tune` 先落盘、闸门后审 ⇒ 502 条 "blocked" 拦的是已经生效的改动；
而 `judge_grade` 全仓无写入方，代码默认 B，撞 update 要求的 A 恒失败。
"""
import json

import pytest

import aelvoxim.core.dgmh as dgmh
from aelvoxim.core.dgmh import ActivationStatus, check_gate


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dgmh, "_cooldown_path", lambda: tmp_path / "cooldown.json")
    monkeypatch.setattr(dgmh, "_ungraded_path", lambda: tmp_path / "ungraded.jsonl")
    return tmp_path


def test_check_gate_reports_ungraded_instead_of_defaulting_to_b():
    ok, reason = check_gate("update", None, ActivationStatus())
    assert ok is False and "ungraded" in reason


def test_explicit_b_still_fails_the_a_requirement():
    ok, reason = check_gate("update", "B", ActivationStatus())
    assert ok is False and "below minimum A" in reason


def test_gate_changes_queues_ungraded_and_applies_nothing(isolated):
    passed, stats = dgmh.gate_changes(
        [{"target": "metacog.evolve_threshold", "old": 0.35, "new": 0.10, "reason": "t"}]
    )
    assert passed == []
    assert stats == {"blocked": 0, "cooled": 0, "ungraded": 1}
    rec = json.loads((isolated / "ungraded.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["target"] == "metacog.evolve_threshold" and rec["why_ungraded"]


def test_second_identical_proposal_is_cooled_down(isolated):
    change = {"target": "metacog.evolve_threshold", "old": 0.35, "new": 0.10}
    dgmh.gate_changes([change])
    passed, stats = dgmh.gate_changes([change])
    assert passed == [] and stats["cooled"] == 1


def test_cooldown_expires(isolated):
    dgmh.mark_blocked("x.y")
    assert dgmh.in_cooldown("x.y") is True
    assert dgmh.in_cooldown("x.y", hours=0.0) is False


def test_graded_change_passes_when_level_two_is_active(isolated):
    """有 A 级判分且 Level 2 已激活时，闸门才真的放行。"""
    act = ActivationStatus(judgestored=True, phase2_ready=True)
    ok, _ = check_gate("update", "A", act)
    assert ok is True
    passed, stats = dgmh.gate_changes(
        [{"target": "judge.reusability_create", "old": 0.3, "new": 0.2, "judge_grade": "A"}],
        cooldown_hours=0.0,
    )
    # 注意：gate_changes 内部用默认 ActivationStatus（judgestored=False），所以仍会被拦
    assert passed == [] and stats["blocked"] == 1
