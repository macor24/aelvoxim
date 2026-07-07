"""Tests for metacore.learn.decompose"""

from aelvoxim.learn.decompose import detect_lang, decompose_direction


def test_detect_lang_en():
    assert detect_lang("FastAPI async optimization") == "en"
    assert detect_lang("Python threading") == "en"


def test_detect_lang_zh():
    assert detect_lang("异步优化与并发模型") == "zh"


def test_detect_lang_ja():
    assert detect_lang("FastAPI タスク処理") == "ja"


def test_detect_lang_kr():
    assert detect_lang("비동기 프로그래밍") == "kr"


def test_detect_lang_other():
    assert detect_lang("") == "other"


def test_decompose_preset():
    tasks = decompose_direction("FastAPI optimization")
    assert len(tasks) >= 6, f"Expected >=6 tasks, got {len(tasks)}"
    assert any("async" in t.lower() or "route" in t.lower() for t in tasks), \
        f"Expected async/route task, got {tasks}"


def test_decompose_fallback():
    # Force fallback by using a topic unlikely to match search
    tasks = decompose_direction("Very unique zx9kq topic")
    assert len(tasks) >= 3, f"Expected >=3 tasks, got {len(tasks)}"
    assert all(isinstance(t, str) for t in tasks)
