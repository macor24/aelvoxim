"""Tests for metacore.learn.learner"""

from aelvoxim.learn.learner import Learner, LearningDirection


def test_add_direction():
    l = Learner(skip_load=True)
    l._current_plan = "enterprise"
    assert l.add_direction("FastAPI Optimization")
    assert "FastAPI Optimization" in l._directions


def test_remove_direction():
    l = Learner(skip_load=True)
    l._current_plan = "enterprise"
    l.add_direction("FastAPI Optimization")
    assert l.remove_direction("FastAPI Optimization")
    assert "FastAPI Optimization" not in l._directions


def test_pause_resume():
    l = Learner(skip_load=True)
    l._current_plan = "enterprise"
    l.add_direction("FastAPI Optimization")
    assert l.pause_direction("FastAPI Optimization")
    assert l._directions["FastAPI Optimization"].status == "paused"
    assert l.resume_direction("FastAPI Optimization")
    assert l._directions["FastAPI Optimization"].status == "active"


def test_list_directions(monkeypatch):
    monkeypatch.setenv("AELVOXIM_EDITION", "enterprise")
    l = Learner(skip_load=True)
    l.add_direction("FastAPI Optimization")
    l.add_direction("Docker Deployment")
    dirs = l.list_directions()
    assert len(dirs) == 2
    topics = [d["topic"] for d in dirs]
    assert "FastAPI Optimization" in topics
    assert "Docker Deployment" in topics
