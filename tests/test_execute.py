"""Tests for metacore.learn.execute"""

from aelvoxim.learn.execute import _get_template_for_task, try_execute_task


def test_template_matching_en():
    assert _get_template_for_task("route design") is not None
    assert _get_template_for_task("database setup") is not None
    assert _get_template_for_task("test coverage") is not None
    assert _get_template_for_task("deploy to production") is not None
    assert _get_template_for_task("config setup") is not None
    assert _get_template_for_task("function implementation") is not None


def test_template_matching_cn():
    assert _get_template_for_task("路由设计") is not None
    assert _get_template_for_task("依赖注入") is not None
    assert _get_template_for_task("数据库优化") is not None
    assert _get_template_for_task("测试用例") is not None
    assert _get_template_for_task("部署方案") is not None


def test_template_no_match():
    assert _get_template_for_task("unknown random thing") is None


def test_true_execution():
    content = try_execute_task("test", "route design")
    assert content is not None
    assert len(content) >= 10


def test_true_execution_no_match():
    """Non-matching task should return None (no subprocess execution for safety)."""
    content = try_execute_task("test", "some random unknown task")
    assert content is None  # safe: no subprocess for non-preset tasks
