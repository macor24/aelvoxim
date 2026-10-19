"""memory_health 打分口径：年龄不计分，不可用条目才计分（2026-10-19）。

旧口径：entities 中 ">30 天未锁" 超过 30% ⇒ 0.5 分。生产上 1828/2558 = 71%，
知识库从 6 月长到现在必然如此 ⇒ 这个信号等于长期假报警，并惩罚"积累"。
"""
from aelvoxim.core.metacog import _unusable_ratio_score


def test_no_unusable_entries():
    assert _unusable_ratio_score(1000, 0) == 0.0


def test_small_number_of_empty_entries_is_tolerated():
    assert _unusable_ratio_score(1000, 40) == 0.0   # 4% 以下不报


def test_over_20_percent_is_flagged():
    assert _unusable_ratio_score(1000, 250) == 0.4


def test_over_half_is_severe():
    assert _unusable_ratio_score(1000, 600) == 0.5


def test_empty_store_is_neutral():
    assert _unusable_ratio_score(0, 0) == 0.0
