"""Tests for metacore.experts.sub_agent — Sub-process expert execution."""

import time
from aelvoxim.experts.sub_agent import SubAgentManager
from aelvoxim.experts.base import ExpertInput, ExpertOutput


def test_sub_agent_init():
    """SubAgentManager should initialize with default timeout."""
    sam = SubAgentManager()
    assert sam._timeout == 10


def test_sub_agent_init_custom_timeout():
    """SubAgentManager should accept custom timeout."""
    sam = SubAgentManager(timeout=5)
    assert sam._timeout == 5


def test_run_one_memory_expert():
    """_run_one should execute MemoryExpert in subprocess."""
    from aelvoxim.experts.memory import MemoryExpert
    inp = ExpertInput(query="python", user_id="test")
    result = SubAgentManager._run_one(MemoryExpert, inp, 10)
    assert result is not None
    assert result.expert_name == "memory"
    assert isinstance(result.confidence, (int, float))


def test_run_one_timeout():
    """_run_one should handle timeouts gracefully."""
    from aelvoxim.experts.ethics import EthicsExpert
    inp = ExpertInput(query="delete all files", user_id="test")
    result = SubAgentManager._run_one(EthicsExpert, inp, 3)
    assert result is not None
    # Should either complete or timeout — either is acceptable
    assert isinstance(result.confidence, (int, float))


def test_run_one_with_shared_dir():
    """_run_one should handle shared_dir in input."""
    import tempfile, os
    shared_dir = tempfile.mkdtemp(prefix="test_shared_")
    from aelvoxim.experts.memory import MemoryExpert
    inp = ExpertInput(query="python", user_id="test", shared_dir=shared_dir)
    result = SubAgentManager._run_one(MemoryExpert, inp, 10)
    assert result is not None
    assert result.expert_name == "memory"
    # Cleanup
    import shutil
    shutil.rmtree(shared_dir, ignore_errors=True)


def test_run_all_empty():
    """run_all with empty list should return empty list."""
    sam = SubAgentManager()
    inp = ExpertInput(query="test", user_id="test")
    results = sam.run_all([], inp)
    assert results == []


def test_run_all_single():
    """run_all with one expert should work."""
    from aelvoxim.experts.memory import MemoryExpert
    sam = SubAgentManager(timeout=10)
    inp = ExpertInput(query="python", user_id="test")
    results = sam.run_all([MemoryExpert], inp)
    assert len(results) == 1
    assert results[0].expert_name == "memory"


def test_run_all_multiple():
    """run_all with multiple experts should return all results."""
    from aelvoxim.experts.memory import MemoryExpert
    from aelvoxim.experts.logic import LogicExpert
    sam = SubAgentManager(timeout=10)
    inp = ExpertInput(query="python", user_id="test")
    results = sam.run_all([MemoryExpert, LogicExpert], inp)
    assert len(results) == 2
    names = [r.expert_name for r in results]
    assert "memory" in names
    assert "logic" in names


def test_run_one_returns_expert_output():
    """_run_one should return ExpertOutput instance."""
    from aelvoxim.experts.memory import MemoryExpert
    inp = ExpertInput(query="test", user_id="test")
    result = SubAgentManager._run_one(MemoryExpert, inp, 5)
    assert isinstance(result, ExpertOutput)
    assert hasattr(result, "expert_name")
    assert hasattr(result, "confidence")
    assert hasattr(result, "opinion")
    assert hasattr(result, "error")
