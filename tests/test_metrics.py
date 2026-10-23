"""闭环指标：置信度加权产出、A/B 分组、U 检验（2026-10-22）。

旧指标"条目/轮"被每方向每天 5 轮的限制锁死；W=Σconfidence 才量得出质量差异。
"""
from aelvoxim.core import metrics as m


def _rows():
    return [
        {"topic": "alpha", "day": "2026-10-21", "n": 5, "w": 3.5, "validated": 2},
        {"topic": "alpha", "day": "2026-10-22", "n": 4, "w": 3.0, "validated": 3},
        {"topic": "beta", "day": "2026-10-21", "n": 2, "w": 0.8, "validated": 0},
    ]


def test_summarize_totals_and_pass_rate():
    s = m.summarize(_rows())
    assert s["n_total"] == 11
    assert s["w_total"] == 7.3
    assert s["pass_rate"] == round(5 / 11, 3)
    assert s["by_day"]["2026-10-22"]["n"] == 4
    assert s["by_direction"]["alpha"]["n"] == 9


def test_empty_rows_are_safe():
    s = m.summarize([])
    assert s["n_total"] == 0 and s["pass_rate"] == 0.0 and s["days"] == []


def test_arm_assignment_is_stable():
    a1 = m.arm_of("alpha")
    a2 = m.arm_of("alpha")
    assert a1 == a2 and a1 in ("A", "B")
    # 不同方向不保证不同组，但同方向必须稳定
    assert {m.arm_of(t) for t in ("alpha", "beta", "gamma", "delta")} <= {"A", "B"}


def test_by_arm_sums():
    r = m.by_arm(_rows())
    assert r["A"]["n"] + r["B"]["n"] == 11


def test_mann_whitney_detects_separation():
    low = [1.0, 1.2, 0.9, 1.1, 1.0]
    high = [2.0, 2.2, 1.9, 2.1, 2.3]
    p = m.mann_whitney_p(low, high)
    assert p is not None and p < 0.05


def test_mann_whitney_needs_samples():
    assert m.mann_whitney_p([1.0], [2.0, 2.1, 2.2]) is None
