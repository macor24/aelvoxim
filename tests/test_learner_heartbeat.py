# -*- coding: utf-8 -*-
"""learner 心跳/计数的"不许被 continue 绕过"回归测试（U1, 2026-10-17）。

背景：master loop 里 `_loop_count += 1` 与 `_save_status()` 原本在轮末，
而轮内多个 gate 都 `continue`（cooldown / weak_pass / rate_interval / rate_daily /
learning 或 teaching 循环成功）⇒ 学习一忙，轮末就永远到不了，status.json 停更、
监控报 "learner may be dead"。修法：两者移到轮首，并在 status 里暴露 last_gate。
"""
import json

from aelvoxim.learn import loop as loop_mod


def test_save_status_carries_loop_count_and_last_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "STATUS_FILE", tmp_path / "status.json")

    learner = object.__new__(loop_mod.Learner)
    learner._running = True
    learner._directions = {}
    learner._loop_count = 42
    learner._last_gate = "cycle_learn"

    learner._save_status()

    data = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert data["running"] is True
    assert data["loop_count"] == 42
    assert data["last_gate"] == "cycle_learn"
    assert data["updated_at"], "updated_at 必须存在（监控据此判活）"
    # v2：与 iteration 解耦的年龄字段（缺它就无法区分"慢"与"卡死"）
    assert isinstance(data.get("iter_age_s"), int)


def test_loop_head_advances_counter_before_any_gate():
    """源码级契约：计数与心跳必须在 while 体内、第一个 gate 之前。

    这是"结构性"断言——防止将来有人把它们挪回轮末（本 bug 的复发形态）。
    """
    src = open(loop_mod.__file__, encoding="utf-8").read()
    head_i = src.index("while self._running:")
    count_i = src.index("self._loop_count += 1", head_i)
    save_i = src.index("self._save_status()", head_i)

    # 第一个"真正的 continue 语句"（整行只有 continue，排除注释/字符串里的同名词）
    first_continue_i = None
    off = head_i
    for line in src[head_i:].splitlines(keepends=True):
        if line.strip() == "continue":
            first_continue_i = off
            break
        off += len(line)
    assert first_continue_i is not None, "没找到任何 continue？源码结构变了，先核对"
    assert count_i < first_continue_i, "计数必须在任何 continue 之前"
    assert save_i < first_continue_i, "心跳必须在任何 continue 之前"
