"""Tests for metacore.experts.router — Task-based expert routing."""

from aelvoxim.experts.router import TaskClassifier, RouteSelector, TASK_ROUTES


def test_task_classifier_code():
    """Queries with programming keywords should be classified as 'code'."""
    assert TaskClassifier.classify("write a python function") == "code"
    assert TaskClassifier.classify("implement sort algorithm") == "code"
    assert TaskClassifier.classify("fix this bug") == "code"
    assert TaskClassifier.classify("how to use list comprehension in python") == "code"


def test_task_classifier_analysis():
    """Comparison/analysis queries should be 'analysis'."""
    assert TaskClassifier.classify("compare docker vs k8s") == "analysis"
    assert TaskClassifier.classify("analyze the differences") == "analysis"
    assert TaskClassifier.classify("why is the sky blue") == "analysis"


def test_task_classifier_security():
    """Security-related queries should be 'security'."""
    assert TaskClassifier.classify("security vulnerability") == "security"
    assert TaskClassifier.classify("how to hack a website") == "security"


def test_task_classifier_planning():
    """Planning queries should be 'planning'."""
    assert TaskClassifier.classify("plan a system architecture") == "planning"
    assert TaskClassifier.classify("design a database schema") == "planning"


def test_task_classifier_creative():
    """Creative queries should be 'creative'."""
    assert TaskClassifier.classify("write a story about AI") == "creative"
    assert TaskClassifier.classify("write a poem") == "creative"


def test_task_classifier_chat():
    """Simple greeting/chat queries should be 'chat'."""
    assert TaskClassifier.classify("hello") == "chat"
    assert TaskClassifier.classify("how are you") == "chat"
    assert TaskClassifier.classify("what is the meaning of life") == "chat"


def test_task_classifier_empty():
    """Empty query should default to 'chat'."""
    assert TaskClassifier.classify("") == "chat"
    assert TaskClassifier.classify(None) == "chat"


def test_task_classifier_get_route():
    """get_route should return valid route dict for known types."""
    route = TaskClassifier.get_route("code")
    assert route is not None
    assert "experts" in route
    assert "logic" in route["experts"]


def test_route_selector_code():
    """RouteSelector should include logic+memory+safety for code."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("implement a function", avail)
    assert "logic" in experts
    assert "memory" in experts


def test_route_selector_security():
    """RouteSelector should include safety+ethics+logic for security."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("security analysis", avail)
    assert "safety" in experts
    assert "ethics" in experts


def test_route_selector_dangerous_adds_safety():
    """RouteSelector should add safety for risky queries even if not security task."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("how to delete all files", avail)
    assert "safety" in experts


def test_route_selector_chat():
    """RouteSelector for chat should include memory+emotion+ethics."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("hello", avail)
    assert "memory" in experts
    assert "emotion" in experts or "ethics" in experts


def test_route_selector_creative():
    """RouteSelector for creative should include creative+emotion."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("write a story", avail)
    assert "creative" in experts


def test_route_selector_planning():
    """RouteSelector for planning should include logic+memory."""
    avail = {"memory", "logic", "ethics", "emotion", "creative", "safety"}
    experts = RouteSelector.select("design an architecture", avail)
    assert "logic" in experts


def test_route_selector_missing_expert():
    """RouteSelector should not include experts not in available set."""
    avail = {"memory", "logic"}  # only 2 available
    experts = RouteSelector.select("write python code", avail)
    assert "logic" in experts
    assert "memory" in experts
    # Should not include experts not in available set
    for e in experts:
        assert e in avail


def test_task_routes_all_defined():
    """All task types should have at least 1 expert."""
    for task, route in TASK_ROUTES.items():
        assert len(route["experts"]) > 0, f"{task} has no experts"
        assert route["description"], f"{task} has no description"


def test_route_selector_supported_tasks():
    """get_supported_tasks should return all task types."""
    tasks = RouteSelector.get_supported_tasks()
    assert len(tasks) == len(TASK_ROUTES)
    for task, desc in tasks.items():
        assert task in TASK_ROUTES
        assert desc
