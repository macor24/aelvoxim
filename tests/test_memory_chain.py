"""Memory-chain criteria (2026-09-18 audit).

Three links, one assertion each, so a regression fails CI instead of surfacing
as "cross-session memory is broken" from the user:

  1. recording  — facts and preferences are actually extracted (zh AND en)
  2. injection  — previous sessions reach the prompt, bounded, current excluded
  3. retrieval  — the fetchers are called with the CALLER's own user id

The 2026-09-18 audit found: the fact extractor only matched Chinese verbs (so
English conversations recorded nothing), preferences were read but never
written, and nothing ever read previous sessions at all.

NOTE ON LINEAGES: the repo and the deployed tree have drifted. The deployed
lineage gets cross-session recall from PG session messages (this file); the repo
lineage ships a richer implementation via `server/session_manager.py`
(snapshots + PII masking + expiry) that is NOT deployed yet. The injection/
retrieval criteria below therefore apply to the deployed lines and skip on the
repo-only lineage — they are skipped with a reason, never silently passed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aelvoxim.storage.db as sdb  # noqa: E402
from aelvoxim.server import service_chat as sc  # noqa: E402

ZH_ALWAYS = "Docker Compose 是一个用于定义和运行多容器 Docker 应用的工具。它使用 YAML 文件来配置服务。"
ZH_PLAIN = "这个用法需要先安装依赖，然后运行命令即可完成部署。"
EN_TECH = "Python is a dynamically typed language. FastAPI supports async endpoints."

_PREF = getattr(sc, "extract_preference", None)
_CROSS = getattr(sc, "inject_cross_session_context", None)


def _call(fn, *args):
    """Call a function that only exists on the deployed lineage (tests are skipped otherwise)."""
    assert fn is not None
    return fn(*args)

deployed_lineage = pytest.mark.skipif(
    _PREF is None or _CROSS is None,
    reason="该分支是仓库线: 跨会话记忆由 session_manager 快照实现(未部署); 本判据针对已部署线",
)


def test_facts_extracted_in_both_languages():
    """Recording must not silently work in one language only (original bug)."""
    assert sc._extract_facts_from_reply(ZH_ALWAYS, "q"), "中文模式未提取到要点"
    assert sc._extract_facts_from_reply(EN_TECH, "q"), "英文回复未提取到要点 — 回归(原缺陷)"


def test_plain_text_yields_no_facts():
    """The extractor must stay conservative: no facts from ordinary chatter."""
    assert sc._extract_facts_from_reply(ZH_PLAIN, "q") == []


@deployed_lineage
def test_preference_statements_are_detected():
    assert _call(_PREF, "记住我用 vim 编辑代码")
    assert _call(_PREF, "I prefer dark mode in the UI")
    assert _call(_PREF, "这个接口怎么调用？") == ""


def _patch_db(monkeypatch, sessions, messages):
    calls = {}

    def fake_sessions(user_id="", email="", limit=50):
        calls["user_id"] = user_id
        calls["limit"] = limit
        return sessions

    monkeypatch.setattr(sdb, "get_sessions_from_pg", fake_sessions)
    monkeypatch.setattr(sdb, "get_messages_from_pg", lambda sid: messages(sid))
    return calls


@deployed_lineage
def test_cross_session_is_scoped_bounded_and_excludes_current(monkeypatch):
    calls = _patch_db(
        monkeypatch,
        sessions=[{"id": "cur"}, {"id": "prev1"}],
        messages=lambda sid: [{"role": "user", "content": f"q-{sid}-" + "x" * 80},
                              {"role": "assistant", "content": f"a-{sid}"}],
    )
    out = _call(_CROSS, {"id": "u-1", "email": "a@b.c"}, "cur", "")
    assert calls["user_id"] == "u-1", "检索没有按调用者 user_id 过滤(租户泄漏!)"
    assert "prev1" in out and "cur-" not in out, "当前会话未被排除"
    assert "[Previous conversations" in out, "缺少明确的分区标记"
    assert len(out) < 4000


@deployed_lineage
def test_cross_session_returns_nothing_without_user_id(monkeypatch):
    _patch_db(monkeypatch, sessions=[{"id": "x"}], messages=lambda sid: [{"role": "user", "content": "y"}])
    assert _call(_CROSS, {"email": "a@b.c"}, "cur", "") == "", "缺少 user_id 时不得回落为全局检索"


@deployed_lineage
def test_cross_session_can_be_disabled_by_budget(monkeypatch):
    _patch_db(monkeypatch, sessions=[{"id": "p"}], messages=lambda sid: [{"role": "user", "content": "z"}])
    monkeypatch.setenv("AELVOXIM_CROSS_SESSION_CHARS", "0")
    assert _call(_CROSS, {"id": "u-1", "email": "a@b.c"}, "cur", "") == ""


@deployed_lineage
def test_record_conversation_memory_wires_prefs_and_facts(monkeypatch):
    """The recording link: one call must store preferences AND the extracted
    facts for that user (the deployed lineage's "记记忆" half)."""
    rec = getattr(sc, "record_conversation_memory", None)
    assert rec is not None, "已部署线缺少 record_conversation_memory"
    seen = {}
    monkeypatch.setattr(sc, "store_preferences", lambda u, m: seen.__setitem__("prefs", m))
    monkeypatch.setattr(sc, "_store_user_facts", lambda u, f: seen.__setitem__("facts", list(f)))
    facts = _call(rec, {"id": "u-1", "email": "a@b.c"}, "记住我用 vim 编辑代码", EN_TECH)
    assert seen.get("prefs") == "记住我用 vim 编辑代码", "偏好未写入"
    assert facts and seen.get("facts") == facts, "抽取出的事实未落库"


@deployed_lineage
def test_cross_session_only_injected_on_fresh_sessions(monkeypatch):
    """Mid-session the block would duplicate context the conversation already
    carries, at ~3k chars per turn — so it must be fresh-session-only."""
    _patch_db(monkeypatch, sessions=[{"id": "prev"}], messages=lambda sid: [{"role": "user", "content": "z"}])
    user = {"id": "u-1", "email": "a@b.c"}
    fresh = [{"role": "user", "content": "hi"}]
    continuing = [{"role": "user", "content": "hi"},
                  {"role": "assistant", "content": "hello"},
                  {"role": "user", "content": "and?"}]
    assert "[Previous conversations" in _call(_CROSS, user, "cur", "", fresh)
    assert _call(_CROSS, user, "cur", "", continuing) == ""
    monkeypatch.setenv("AELVOXIM_CROSS_SESSION_MODE", "always")
    assert "[Previous conversations" in _call(_CROSS, user, "cur", "", continuing)
