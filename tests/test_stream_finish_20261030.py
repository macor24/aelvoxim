"""P0-1 判据：流式三条出口都必须跑收尾，且只跑一次。

三条出口：自然结束 / [WIN:] 分支提前 return / [TOOL:] 分支提前 return。
修前：两个提前 return 都在收尾块之前，工具轮次不产生快照/记忆/评估。

运行：.venv/bin/python -m pytest tests/test_stream_finish_20261030.py -q
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aelvoxim.learn.llm import ModelConfig
from aelvoxim.server import routes_chat


@pytest.fixture()
def client(monkeypatch):
    """最小 app + 依赖覆盖；收尾换成记录器，不写任何东西。"""
    finished = []

    def _recorder(user, sid, email, user_msg, collected, t0):
        finished.append({"sid": sid, "email": email, "text": "".join(collected)})

    monkeypatch.setattr(routes_chat, "_post_stream_finish", _recorder)
    monkeypatch.setattr(routes_chat, "_POST_STREAM_INLINE", True)
    routes_chat._finished_keys.clear()

    app = FastAPI()
    app.include_router(routes_chat.router)
    app.dependency_overrides[routes_chat._verify_key] = lambda: {
        "email": "p0-1@test", "id": "u1", "user_id": "u1", "plan": "pro",
    }
    return TestClient(app), finished, monkeypatch


def _stream_of(text, monkeypatch):
    def _gen(models, system, prompt):
        yield text

    monkeypatch.setattr("aelvoxim.learn.extract.call_llm_if_available",
                        lambda: (object(), ModelConfig(name="test-model", provider="deepseek")))
    monkeypatch.setattr("aelvoxim.learn.llm.call_llm_stream", _gen)


def _post(c, messages=None):
    return c.post("/llm/chat/stream", json={
        "messages": messages or [{"role": "user", "content": "你好"}],
        "session_id": "sess-p01",
    })


def test_plain_stream_finishes_once(client, monkeypatch):
    c, finished, mp = client
    _stream_of("这是普通回答，没有任何工具标记。", mp)

    r = _post(c)

    assert r.status_code == 200
    assert len(finished) == 1
    assert finished[0]["text"] == "这是普通回答，没有任何工具标记。"
    assert finished[0]["sid"] == "sess-p01"


def test_tool_branch_finishes_once(client, monkeypatch):
    c, finished, mp = client
    _stream_of('[TOOL:read_file]{"path":"/tmp/x"}', mp)
    monkeypatch.setattr("aelvoxim.server.tool_use.has_tool_calls", lambda t: True)
    monkeypatch.setattr("aelvoxim.server.tool_use.execute_tool_calls",
                        lambda t: "文件内容：hello")

    r = _post(c)

    assert r.status_code == 200
    assert len(finished) == 1, "工具分支的提前 return 必须也走收尾"
    assert finished[0]["text"] == "文件内容：hello"


def test_win_branch_finishes_once(client, monkeypatch):
    c, finished, mp = client
    _stream_of('[WIN:click]{"x":1}', mp)
    monkeypatch.delenv("WINDOWS_MCP_URL", raising=False)
    monkeypatch.delenv("WINDOWS_MCP_KEY", raising=False)

    r = _post(c)

    assert r.status_code == 200
    assert len(finished) == 1, "[WIN:] 分支的提前 return 必须也走收尾"


def test_schedule_is_idempotent_per_request(monkeypatch):
    calls = []
    monkeypatch.setattr(routes_chat, "_post_stream_finish",
                        lambda *a: calls.append(a))
    monkeypatch.setattr(routes_chat, "_POST_STREAM_INLINE", True)
    routes_chat._finished_keys.clear()

    # 同一次请求被三条出口各触发一次 → 只跑一次
    for _ in range(3):
        routes_chat._schedule_post_stream_finish(
            {"email": "x@y"}, "sid-dup", "x@y", "hi", ["ans"], 1000.0)
    # 同一会话的下一轮（同一秒内）→ 必须另跑一次，不能被 int(t0) 误判成重复
    routes_chat._schedule_post_stream_finish(
        {"email": "x@y"}, "sid-dup", "x@y", "hi", ["ans2"], 1000.4)

    assert len(calls) == 2


def test_finish_swallows_stage_errors(monkeypatch):
    """任一阶段报错都不能把收尾变成异常（已发出的流不能变错误）。"""
    def _boom(*a, **k):
        raise RuntimeError("stage down")

    monkeypatch.setattr("aelvoxim.server.session_manager.save_snapshot", _boom)
    monkeypatch.setattr("aelvoxim.storage.db.save_message_to_pg", _boom, raising=False)

    routes_chat._post_stream_finish({"email": "", "id": ""}, "sid", "", "q", ["a"], 0.0)
