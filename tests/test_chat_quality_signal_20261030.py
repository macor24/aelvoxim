"""P0-7 · L1：对话质量信号（只读派生）判据。

口径：
- 数据源＝evaluate_conversation 已落盘的 chat_monitor/YYYY-MM-DD.jsonl
- 纯读函数：不新增文件、不写 memory entities、不改 calibration 阈值
- 样本不足必须 available=False，不得填默认值冒充读数

运行：.venv/bin/python -m pytest tests/test_chat_quality_signal_20261030.py -q
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from aelvoxim.server import chat_monitor as cm


def _write_day(dirpath, records, date_str=None):
    d = date_str or datetime.now().strftime("%Y-%m-%d")
    fp = dirpath / f"{d}.jsonl"
    with open(fp, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n")
    return fp


def _rec(score, level, kb_hit=False, ts="2026-10-30 10:00:00", qh="aabbccddeeff"):
    return {
        "ts": ts,
        "robustness_score": score,
        "level": level,
        "knowledge": {"hit": kb_hit, "query_hash": qh, "query_head": "不该出现在信号里"},
        "response_time_ms": 120.0,
    }


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_DATA_DIR", tmp_path)
    return tmp_path


def test_low_rate_and_worst_level(data_dir):
    recs = [_rec(40, "poor") for _ in range(6)] + [_rec(90, "excellent", True) for _ in range(4)]
    _write_day(data_dir, recs)

    sig = cm.chat_quality_signal(window_days=1, min_samples=5)

    assert sig["available"] is True
    assert sig["samples"] == 10
    assert sig["low_rate"] == 0.6
    assert sig["worst_level"] == "poor"
    assert sig["avg_score"] == 60.0
    assert len(sig["recent_low"]) == 5
    # 只带匿名标识，不带正文
    assert set(sig["recent_low"][0]) == {"ts", "score", "level", "kb_hit", "query_hash"}
    assert "query_head" not in json.dumps(sig["recent_low"], ensure_ascii=False)


def test_insufficient_samples_is_explicit(data_dir):
    _write_day(data_dir, [_rec(80, "good") for _ in range(3)])

    sig = cm.chat_quality_signal(window_days=1, min_samples=5)

    assert sig["available"] is False
    assert sig["samples"] == 3
    assert sig["low_rate"] is None
    assert sig["avg_score"] is None
    assert sig["recent_low"] == []


def test_no_data_is_not_zero(data_dir):
    sig = cm.chat_quality_signal(window_days=2, min_samples=1)

    assert sig["available"] is False
    assert sig["samples"] == 0
    assert sig["worst_level"] is None


def test_malformed_lines_tolerated(data_dir):
    fp = data_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    good = json.dumps(_rec(55, "fair"), ensure_ascii=False)
    fp.write_text(good + "\n{not json}\n" + good + "\n", encoding="utf-8")

    sig = cm.chat_quality_signal(window_days=1, min_samples=1)

    assert sig["available"] is True
    assert sig["samples"] == 2


def test_empty_answer_floor_counts_as_low(data_dir):
    """空回答实测 60 分（底分 50 + 两个白给加分）⇒ 阈值必须 >60 才会触发。"""
    r = cm.evaluate_conversation(query="怎么配置 TLS 证书", answer="", user_id="fire@test")

    assert r["robustness_score"] == 60.0
    assert r["level"] == "fair"

    sig = cm.chat_quality_signal(window_days=1, min_samples=1)
    assert sig["available"] is True
    assert sig["low_rate"] == 1.0
    assert sig["worst_level"] == "fair"


def test_admin_panel_exposes_signal(data_dir):
    from aelvoxim.server import admin_panel

    _write_day(data_dir, [_rec(30, "poor") for _ in range(6)])

    got = admin_panel._chat_quality_status()

    assert "available" in got
    assert got["available"] is True
    assert got["samples"] == 6
