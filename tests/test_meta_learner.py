"""Tests for metacore.learn.meta_learner — Meta-learning from user feedback."""

from aelvoxim.learn.meta_learner import (
    MetaLearner, ingest_feedback, _load_feedback, _clear_feedback,
    _FEEDBACK_FILE,
)


def setup_method():
    """Clean feedback queue before each test."""
    _clear_feedback()


def test_ingest_feedback():
    """ingest_feedback should write to the feedback file."""
    _clear_feedback()
    ingest_feedback({"user_id": "test", "query": "hello", "signals": {}})
    records = _load_feedback()
    assert len(records) == 1
    assert records[0]["user_id"] == "test"


def test_ingest_feedback_empty_signals():
    """ingest_feedback should accept records with no signals."""
    _clear_feedback()
    ingest_feedback({"user_id": "test", "query": "not A but B"})
    records = _load_feedback()
    assert len(records) == 1


def test_load_feedback_empty():
    """_load_feedback should return empty list when no file exists."""
    _clear_feedback()
    records = _load_feedback()
    assert records == []


def test_meta_learner_tick_no_feedback():
    """MetaLearner.tick should return empty list when no feedback."""
    ml = MetaLearner()
    ml.MIN_INTERVAL = 0
    actions = ml.tick()
    assert actions == []


def test_meta_learner_tick_correction():
    """MetaLearner.tick should process correction feedback."""
    _clear_feedback()
    ingest_feedback({
        "user_id": "test",
        "query": "not Flask but FastAPI",
        "signals": {
            "correction_detected": True,
            "correction": {"old_term": "Flask", "new_term": "FastAPI"},
        },
    })

    class MockLearner:
        _directions = {}
        def add_direction(self, topic):
            self._directions[topic] = None
        def _save_config(self):
            pass

    ml = MetaLearner(learner=MockLearner())
    ml.MIN_INTERVAL = 0
    actions = ml.tick()
    assert any("correction" in a for a in actions)


def test_meta_learner_tick_repeat_question():
    """MetaLearner.tick should add direction for repeat questions."""
    _clear_feedback()
    ingest_feedback({
        "user_id": "test",
        "query": "tell me about python decorators again",
        "signals": {
            "repeat_question": True,
            "raw_topic": "python decorators",
        },
    })

    class MockLearner:
        _directions = {}
        def add_direction(self, topic):
            self._directions[topic] = None
        def _save_config(self):
            pass

    ml = MetaLearner(learner=MockLearner())
    ml.MIN_INTERVAL = 0
    actions = ml.tick()
    assert actions  # should have at least 1 action


def test_meta_learner_tick_empty_learner():
    """MetaLearner.tick should handle None learner gracefully."""
    _clear_feedback()
    ingest_feedback({
        "user_id": "test",
        "query": "not A but B",
        "signals": {
            "correction_detected": True,
            "correction": {"old_term": "A", "new_term": "B"},
        },
    })

    ml = MetaLearner(learner=None)
    ml.MIN_INTERVAL = 0
    actions = ml.tick()
    # Should not crash, correction handling may fail but should not raise


def test_meta_learner_negative_anchor():
    """_create_negative_anchor should not raise."""
    ml = MetaLearner()
    ml._create_negative_anchor("test_topic")
    # No assertion needed — just verify no exception
    assert True


def test_meta_learner_config_override():
    """MetaLearner should load config from calibration."""
    ml = MetaLearner()
    assert ml.MIN_INTERVAL > 0
    assert ml.CORRECTION_CONFIDENCE > 0
