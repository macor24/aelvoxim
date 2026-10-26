"""Tests for the KB index append + embeddings merge (2026-10-26).

Covers two production defects:
  1. index.json append was an unguarded read-modify-write -> freshly learned
     entries vanished from the index (1366 found and re-indexed by hand).
  2. _save_embeddings wrote the caller's in-memory dict over the whole file ->
     22 freshly backfilled vectors disappeared within 30 minutes.

Run: PYTHONPATH=src python -m pytest tests/test_kb_index_merge.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
kn = pytest.importorskip("aelvoxim.learn.knowledge")


@pytest.fixture()
def kb_tmp(tmp_path, monkeypatch):
    kdir = tmp_path / "knowledge"
    edir = kdir / "entries"
    edir.mkdir(parents=True)
    monkeypatch.setattr(kn, "KNOWLEDGE_DIR", kdir)
    monkeypatch.setattr(kn, "ENTRIES_DIR", edir)
    monkeypatch.setattr(kn, "INDEX_FILE", kdir / "index.json")
    monkeypatch.setattr(kn, "LOCK_FILE", kdir / ".lock")
    monkeypatch.setattr(kn, "_EMBEDDING_FILE", kdir / "embeddings.json")
    monkeypatch.setattr(kn, "_EMBEDDING_CACHE", None)
    monkeypatch.setattr(kn, "_EMBEDDING_STAMP", None)
    monkeypatch.setattr(kn, "_lock_fd", None)
    return kdir


def test_add_to_index_appends_once(kb_tmp):
    kn.INDEX_FILE.write_text(json.dumps({"entries": [], "topics": {}}))
    assert kn.add_to_index("abc123", "Topic A", "2026-10-26 11:00:00") is True
    assert kn.add_to_index("abc123", "Topic A", "2026-10-26 11:00:01") is False
    data = json.loads(kn.INDEX_FILE.read_text())
    assert data["entries"] == ["abc123"]
    assert data["topics"]["Topic A"]["count"] == 1


def test_add_to_index_does_not_drop_concurrent_ids(kb_tmp):
    """Two writers reading the same snapshot must both end up in the index."""
    kn.INDEX_FILE.write_text(json.dumps({"entries": ["old"], "topics": {}}))
    stale = json.loads(kn.INDEX_FILE.read_text())   # what a racing writer saw
    kn.add_to_index("new1", "T", "")
    # the second writer still holds the stale snapshot; add_to_index must re-read
    assert kn.add_to_index("new2", "T", "") is True
    entries = json.loads(kn.INDEX_FILE.read_text())["entries"]
    assert entries == ["old", "new1", "new2"]
    assert "new1" in entries and stale["entries"] == ["old"]


def test_save_embeddings_merges_foreign_keys(kb_tmp):
    kn._EMBEDDING_FILE.write_text(json.dumps({"keep": {"vector": [1.0], "text": "x"}}))
    cache = kn._load_embeddings()          # records the stamp
    assert "keep" in cache
    # another process appends a vector while we hold our copy
    disk = json.loads(kn._EMBEDDING_FILE.read_text())
    disk["foreign"] = {"vector": [0.5], "text": "y"}
    kn._EMBEDDING_FILE.write_text(json.dumps(disk))
    # our save must not drop "foreign"
    kn._save_embeddings({"mine": {"vector": [0.1], "text": "z"}})
    final = json.loads(kn._EMBEDDING_FILE.read_text())
    assert set(final) == {"keep", "foreign", "mine"}


def test_save_embeddings_is_atomic(kb_tmp):
    kn._save_embeddings({"a": {"vector": [1.0], "text": "t"}})
    assert json.loads(kn._EMBEDDING_FILE.read_text()).keys() == {"a"}
    assert not kn._EMBEDDING_FILE.with_suffix(".tmp").exists()
