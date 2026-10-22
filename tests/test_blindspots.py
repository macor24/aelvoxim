"""盲点统计与 retrieval_gap 信号（2026-10-21）。

原则：样本不足不出分（此前用 7 条数据算 100%，把假健康写进了读数）。
"""
import json
import time

import pytest

from aelvoxim.core import blindspots as bs
from aelvoxim.core.metacog import MetaCogTrigger


def _write(tmp, records):
    d = tmp / "chat_monitor"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "2026-10-21.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture()
def mon(tmp_path, monkeypatch):
    import aelvoxim.utils as u
    monkeypatch.setattr(u, "METACORE_DIR", tmp_path)
    return tmp_path


def test_empty_dir_returns_zeros(mon):
    s = bs.stats(24)
    assert s["total"] == 0 and s["miss"] == 0 and s["miss_ratio"] == 0.0


def test_miss_ratio_and_topics(mon):
    now = time.time()
    recs = []
    for i in range(4):
        recs.append({"timestamp": now - i * 60, "query_head": "docker volume 挂载失败",
                     "knowledge": {"hit": False, "hit_count": 0, "topics": []}})
    recs.append({"timestamp": now - 300, "query_head": "k8s 探针",
                 "knowledge": {"hit": True, "hit_count": 2, "topics": ["Kubernetes"]}})
    _write(mon, recs)
    s = bs.stats(24)
    assert s["total"] == 5 and s["miss"] == 4
    assert s["miss_ratio"] == 0.8
    assert s["top_queries"][0].startswith("docker")
    assert s["topics"]


def test_old_records_excluded(mon):
    now = time.time()
    _write(mon, [
        {"timestamp": now - 3 * 86400, "query_head": "旧问题",
         "knowledge": {"hit": False, "hit_count": 0, "topics": []}},
        {"timestamp": now - 60, "query_head": "新问题",
         "knowledge": {"hit": True, "hit_count": 1, "topics": ["X"]}},
    ])
    s = bs.stats(24)
    assert s["total"] == 1 and s["miss"] == 0


def test_signal_needs_enough_samples(mon):
    now = time.time()
    _write(mon, [{"timestamp": now - 60, "knowledge": {"hit": False, "hit_count": 0}}] * 3)
    r = MetaCogTrigger()._check_retrieval_gap()
    assert r.triggered is False
    assert "样本不足" in r.reason


def test_signal_moderate_and_critical(mon):
    now = time.time()
    recs = [{"timestamp": now - i, "query_head": "q",
             "knowledge": {"hit": False, "hit_count": 0}} for i in range(10)]
    # 10 条里 6 条空手 → 0.6
    recs[0]["knowledge"] = {"hit": True, "hit_count": 2}
    recs[1]["knowledge"] = {"hit": True, "hit_count": 2}
    recs[2]["knowledge"] = {"hit": True, "hit_count": 2}
    recs[3]["knowledge"] = {"hit": True, "hit_count": 2}
    _write(mon, recs)
    r = MetaCogTrigger()._check_retrieval_gap()
    assert r.triggered is True and abs(r.score - 0.6) < 1e-9

    for i in range(10):
        recs[i]["knowledge"] = {"hit": False, "hit_count": 0}
    _write(mon, recs)
    r = MetaCogTrigger()._check_retrieval_gap()
    assert r.score == 0.9


def test_weight_registered():
    from aelvoxim.core.metacog import FALLBACK_WEIGHTS
    assert FALLBACK_WEIGHTS.get("retrieval_gap") == 0.10
