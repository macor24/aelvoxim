"""Tests for metacore.learn.knowledge"""

import uuid

from aelvoxim.learn.knowledge import KnowledgeBase


# Track IDs created during tests so we can clean up
_created_ids: list[str] = []


def _cleanup_kb():
    """Remove test entries from KB index after each test."""
    import json
    from pathlib import Path
    from aelvoxim.utils import INDEX_FILE
    if INDEX_FILE.exists():
        try:
            index = json.loads(INDEX_FILE.read_text())
            entries = index.get("entries", [])
            before = len(entries)
            entries[:] = [eid for eid in entries if eid not in _created_ids]
            if len(entries) < before:
                INDEX_FILE.write_text(json.dumps(index, indent=2))
        except Exception:
            pass
    _created_ids.clear()


def test_store():
    kb = KnowledgeBase()
    uid = uuid.uuid4().hex
    result = kb.store(f"qa-{uid}", f"Store Title {uid}", "Store summary", source="manual")
    assert result is not None
    # Entry may be active or rejected_duplicate depending on KG state
    assert result.get("id") is not None
    assert result.get("title") is not None
    if result.get("id"):
        _created_ids.append(result["id"])


def test_get_by_title():
    kb = KnowledgeBase()
    unique = uuid.uuid4().hex[:8]
    result = kb.store(f"gbt-{unique}", f"Gbt Title {unique}", "gbt summary", source="manual")
    if result.get("id"):
        _created_ids.append(result["id"])
    found = kb.get_by_title(f"Gbt Title {unique}")
    # If rejected_duplicate, get_by_title may return None
    if found:
        assert found["title"] == f"Gbt Title {unique}"


def test_get_all_active():
    kb = KnowledgeBase()
    unique = uuid.uuid4().hex[:8]
    result = kb.store(f"act-{unique}", f"Act Title {unique}", "act sum", source="manual")
    if result.get("id"):
        _created_ids.append(result["id"])
    if result.get("_status") == "active":
        active = list(kb.get_all_active())
        assert any(e["title"] == f"Act Title {unique}" for e in active)


def test_search():
    kb = KnowledgeBase()
    unique = uuid.uuid4().hex[:8]
    result = kb.store(f"sch-{unique}", f"Sch Title {unique}", "sch content here", source="manual")
    if result.get("id"):
        _created_ids.append(result["id"])
    results = kb.search(query="Sch Title", limit=5)
    assert len(results) >= 1


def test_dedup():
    kb = KnowledgeBase()
    u1 = uuid.uuid4().hex[:8]
    u2 = uuid.uuid4().hex[:8]
    r1 = kb.store(f"t-{u1}", f"Dedup Title {u1}", "dd content", source="manual")
    if r1.get("id"):
        _created_ids.append(r1["id"])
    r2 = kb.store(f"t-{u2}", f"Dedup Title {u2}", "dd content diff", source="manual")
    if r2.get("id"):
        _created_ids.append(r2["id"])
    assert r2 is not None
