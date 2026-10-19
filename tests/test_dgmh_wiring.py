"""闸门接线回归测试（2026-10-19）。

修的四条断线：
1. gate_changes 每次 new 默认 ActivationStatus() ⇒ judgestored 恒 False ⇒ update 永不可达
2. judge_grade 无来源 ⇒ 全部进 ungraded
3. check_gate 的 belief_health 没被传 ⇒ 贝叶斯门在自动路径上等于不存在
4. 令牌没有消费 ⇒ 一次放行永久有效
"""
import json
import time

import pytest

from aelvoxim.core import dgmh


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dgmh, "ACTIVATION_FILE", tmp_path / "act.json")
    monkeypatch.setattr(dgmh, "DECISIONS_FILE", tmp_path / "dec.jsonl")
    monkeypatch.setattr(dgmh, "queue_ungraded", lambda c, why="": None)
    monkeypatch.setattr(dgmh, "mark_blocked", lambda t: None)
    monkeypatch.setattr(dgmh, "in_cooldown", lambda t, h: False)
    return tmp_path


def _change(new=0.2):
    return {"target": "metacog.evolve_threshold", "old": 0.1, "new": new}


def test_default_activation_blocks_update(isolated):
    """没有激活文件 ⇒ Level 1 ⇒ update 拦（如实报告，不是永远不通）。"""
    assert dgmh.load_activation().judgestored is False
    passed, stats = dgmh.gate_changes([dict(_change(), judge_grade="A")])
    assert passed == [] and stats["blocked"] == 1


def test_activation_file_opens_level2(isolated):
    (isolated / "act.json").write_text(json.dumps({"judgestored": True}), encoding="utf-8")
    passed, stats = dgmh.gate_changes([dict(_change(), judge_grade="A")])
    assert len(passed) == 1, stats


def test_grade_b_still_blocked_on_update(isolated):
    (isolated / "act.json").write_text(json.dumps({"judgestored": True}), encoding="utf-8")
    passed, _ = dgmh.gate_changes([dict(_change(), judge_grade="B")])
    assert passed == []


def test_human_token_acts_as_judge(isolated):
    """人工放行令牌 = 判分来源；不消费就还能用，消费后失效。"""
    (isolated / "act.json").write_text(json.dumps({"judgestored": True}), encoding="utf-8")
    did = dgmh.record_decision("approve", "metacog.evolve_threshold", 0.2,
                               grade="A", by="tester", why="unit")
    assert dgmh.find_decision("metacog.evolve_threshold", 0.2) == "A"

    passed, _ = dgmh.gate_changes([_change()])
    assert len(passed) == 1
    assert dgmh.find_decision("metacog.evolve_threshold", 0.2) is None, "令牌应已消费"


def test_token_expires(isolated):
    did = dgmh.record_decision("approve", "metacog.evolve_threshold", 0.2, ttl_hours=0)
    assert dgmh.find_decision("metacog.evolve_threshold", 0.2) is None
    assert did


def test_token_must_match_value(isolated):
    dgmh.record_decision("approve", "metacog.evolve_threshold", 0.2)
    assert dgmh.find_decision("metacog.evolve_threshold", 0.9) is None


def test_belief_health_now_blocks(isolated):
    """贝叶斯门：高置信占比 <30% ⇒ 暂停自动演化（此前在自动路径上没传）。"""
    (isolated / "act.json").write_text(json.dumps({"judgestored": True}), encoding="utf-8")
    passed, stats = dgmh.gate_changes(
        [dict(_change(), judge_grade="A")],
        belief_health={"count": 100, "high_confidence": 10, "total_evidence": 200},
    )
    assert passed == [] and stats["blocked"] == 1
