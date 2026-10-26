"""Tests for learn/topic_gate + the discover.py user_chat exclusion (2026-10-26).

Run: PYTHONPATH=src python -m pytest tests/test_topic_gate.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "src" / "aelvoxim" / "learn" / "topic_gate.py",
    Path("/opt/aelvoxim/src/aelvoxim/learn/topic_gate.py"),
]
GATE_PATH = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])


def _load_gate():
    spec = importlib.util.spec_from_file_location("topic_gate_under_test", GATE_PATH)
    assert spec and spec.loader, f"cannot load {GATE_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tg = _load_gate()


JUNK = [
    ("कृपया मुझे दो पंक्तियों में एक छोटी कविता सुनाइए।", "prompt_like"),
    ("You are a content writer for Zenttop, a Chinese kitchen knife factory (OEM/ODM,", "prompt_like"),
    ("中筋面粉 是小麦粉吗？还 是什么？", "question"),
    ("你的意思是把客户资料给agent他帮我发邮件是吗？还是agent会直接帮我找到客户", "question"),
    ("那你觉得要怎么样改进这些？", "question"),
    ("我叫张伟，在阿里云工作，喜欢写Python", "chat_sentence"),
    ("+905****5179 watsap", "phone_number"),
    ("Normalize Devanagari", "denylist:devanagari"),
    ("# Welvoket AGI v0.0.1 全天状态浓缩解读\n> 运行基线：本体21537", "multiline"),
    ("我觉得有闯关的感觉会比较好", "first_second_person"),
    ("已完成计划: 深入学习 深入学习 深入学习 Learn Pyth - 进阶", "plan_title"),
    ("hi", "too_short"),
]

GOOD = [
    "Python性能分析工具与优化技巧",
    "Docker 多阶段构建与镜像体积优化",
    "DistilBERT",
    "Sigstore Rekor",
    "Learn Rust - Main Tools and Frameworks",
    "pytest fixtures and parameterized testing for database operations",
    "Neural network interpretability and mechanistic interpretability",
    "分布式系统一致性协议（如Raft或Paxos的工程实现）",
    "AI自我进化与能力扩展",
    "AI智能体自我进化方向",
    # denylist must target the Zenttop/lead-gen TASKS, not the domains:
    "Alibaba Cloud ECS 远程运维",
    "Alibaba Cloud RDS backup",
    "数控刀具选型与寿命",
    "kitchen knife factory automation",
]

DENY_CASES = [
    ("Zenttop 刀具工厂阿里文案", "denylist:zenttop"),
    ("阿里国际站产品文案撰写", "denylist:阿里国际站"),
    ("自动拓客 客户线索收集", "denylist:拓客"),
    ("cold email sequence for B2B leads", "denylist:cold email"),
]


@pytest.mark.parametrize("topic,reason", JUNK)
def test_junk_rejected(topic, reason):
    ok, why = tg.check(topic)
    assert not ok, f"should reject: {topic!r}"
    assert why == reason, f"{topic!r} -> {why}, expected {reason}"


@pytest.mark.parametrize("topic", GOOD)
def test_real_topics_kept(topic):
    ok, why = tg.check(topic)
    assert ok, f"should keep {topic!r} but got {why}"


@pytest.mark.parametrize("topic,reason", DENY_CASES)
def test_denylist_hits_the_task_not_the_domain(topic, reason):
    ok, why = tg.check(topic)
    assert not ok, f"should block {topic!r}"
    assert why == reason, f"{topic!r} -> {why}, expected {reason}"


def test_denylist_file_overrides_defaults(tmp_path, monkeypatch):
    p = tmp_path / "learning_denylist.json"
    p.write_text('["fortune telling"]', encoding="utf-8")
    monkeypatch.setattr(tg, "DENY_FILE", p)
    tg.reload_denylist()
    try:
        assert not tg.is_learnable("Fortune Telling for Beginners")
        # Defaults are replaced by the operator file, not merged silently
        assert tg.is_learnable("Kubernetes Pod 生命周期与健康检查配置")
    finally:
        tg.reload_denylist()


def test_discover_never_promotes_user_chat():
    pytest.importorskip("aelvoxim.learn.discover")
    from aelvoxim.learn.discover import suggest_directions_from_knowledge

    entries = [
        {"topic": "Kubernetes Pod 生命周期与健康检查配置", "source": "learner_task", "confidence": 0.4},
        {"topic": "我叫张伟，在阿里云工作，喜欢写Python", "source": "user_chat", "confidence": 0.9},
        {"topic": "कृपया मुझे दो पंक्तियों में एक छोटी कविता सुनाइए।", "source": "learner_task", "confidence": 0.9},
        {"topic": "Redis Persistence Modes", "source": "curiosity", "confidence": 0.3},
    ]
    out = suggest_directions_from_knowledge(set(), entries, max_suggestions=5)
    assert "我叫张伟，在阿里云工作，喜欢写Python" not in out
    assert "कृपया मुझे दो पंक्तियों में एक छोटी कविता सुनाइए।" not in out
    assert "Redis Persistence Modes" in out


def test_direction_manager_rejects_junk():
    pytest.importorskip("aelvoxim.learn.direction")
    from aelvoxim.learn.direction import DirectionManager

    assert not DirectionManager.is_valid_direction("你的意思是把客户资料给agent他帮我发邮件是吗？")
    assert not DirectionManager.is_valid_direction("You are a content writer for Zenttop, a Chinese kitchen")
    assert DirectionManager.is_valid_direction("Python性能分析工具与优化技巧")


def test_chat_topic_sanitizer():
    pytest.importorskip("aelvoxim.learn.service_chat")
    from aelvoxim.learn.service_chat import _safe_topic

    assert _safe_topic("कृपया मुझे दो पंक्तियों में एक छोटी कविता सुनाइए।") == "conversation"
    assert _safe_topic("你的意思是把客户资料给agent他帮我发邮件是吗？") == "conversation"
    assert _safe_topic("You are a content writer for Zenttop, a Chinese kitchen knife") == "conversation"
    assert _safe_topic("Kubernetes Pod 生命周期与健康检查配置") == "Kubernetes Pod 生命周期与健康检查配置"
    assert _safe_topic("") == "conversation"
