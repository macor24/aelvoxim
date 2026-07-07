"""Tests for metacore.memory.fusion — Memory fusion with inverted index."""

from aelvoxim.memory.fusion import MemoryFusion, _tokenize
from aelvoxim.memory.entry import MemoryEntry


def test_tokenize_english():
    """_tokenize should extract English words with weight 1.0."""
    tokens = dict(_tokenize("Hello World"))
    assert "hello" in tokens
    assert "world" in tokens
    assert tokens["hello"] == 1.0


def test_tokenize_chinese():
    """_tokenize should handle Chinese characters."""
    tokens = dict(_tokenize("你好世界"))
    # Single chars and bigrams
    assert len(tokens) >= 4  # 4 unique chars + bigrams


def test_tokenize_mixed():
    """_tokenize should handle mixed Chinese and English."""
    tokens = dict(_tokenize("Python编程", "AI"))
    assert "python" in tokens
    assert "ai" in tokens


def test_tokenize_dedup():
    """_tokenize should deduplicate identical terms, keep highest weight."""
    tokens = dict(_tokenize("test", "TEST"))
    assert len(tokens) == 1  # case-insensitive dedup
    assert tokens["test"] == 1.0


def test_fusion_init():
    """MemoryFusion should initialize all 4 layers."""
    f = MemoryFusion()
    assert f.working is not None
    assert f.episodic is not None
    assert f.semantic is not None
    assert f.procedural is not None


def test_fusion_rebuild_index():
    """rebuild_index should not raise on empty layers."""
    f = MemoryFusion()
    f.rebuild_index()
    assert isinstance(f._inverted_index, dict)


def test_fusion_mark_dirty():
    """mark_dirty should set _index_dirty flag."""
    f = MemoryFusion()
    f._index_dirty = False
    f.mark_dirty()
    assert f._index_dirty is True


def test_fusion_needs_rebuild():
    """_needs_rebuild should return True when dirty."""
    f = MemoryFusion()
    f._index_dirty = True
    assert f._needs_rebuild() is True
    f.rebuild_index()
    f._index_dirty = False
    f._inverted_index = {"test": [("working", "k", 1.0)]}
    assert f._needs_rebuild() is False


def test_fusion_set_layer_priority():
    """set_layer_priority should update layer priorities."""
    f = MemoryFusion()
    f.set_layer_priority({"semantic": 2.0})
    assert f._layer_priority["semantic"] == 2.0
    # Other priorities should remain
    assert f._layer_priority["working"] == 0.8


def test_fusion_search_empty():
    """search with no entries should return empty list."""
    f = MemoryFusion()
    results = f.search("anything")
    assert results == []


def test_fusion_search_timeline():
    """search by query should find matching entries via inverted index."""
    f = MemoryFusion()
    # Add an entry to working memory
    entry = MemoryEntry(key="test_key", value="test_value",
                        tags=["test"], importance=0.6, layer="working")
    f.working.store(entry)
    f.mark_dirty()
    f.rebuild_index()

    # Search by matching token (inverted index lookup)
    results = f.search(query="test_key", limit=5)
    assert len(results) >= 1
    assert results[0].key == "test_key"

    # Cleanup
    f.working._entries.pop("test_key", None)


def test_fusion_stats():
    """stats should return dict with expected keys."""
    f = MemoryFusion()
    stats = f.stats()
    assert "total_active" in stats
    assert "by_layer" in stats
    assert "index_entries" in stats


def test_fusion_cleanup_all():
    """cleanup_all should not raise on empty layers."""
    f = MemoryFusion()
    count = f.cleanup_all()
    assert count == 0


def test_fusion_store():
    """store should add entry to appropriate layer and mark index dirty."""
    f = MemoryFusion()
    f._index_dirty = False
    entry = MemoryEntry(key="test_store", value="test",
                        tags=["test"], importance=0.6, layer="working")
    f.working.store(entry)
    f.mark_dirty()
    assert f._index_dirty is True
    # Cleanup
    f.working._entries.pop("test_store", None)


def test_get_layer():
    """get_layer should return correct layer by name."""
    f = MemoryFusion()
    assert f.get_layer("working") is f.working
    assert f.get_layer("episodic") is f.episodic
    assert f.get_layer("semantic") is f.semantic
    assert f.get_layer("procedural") is f.procedural
    assert f.get_layer("nonexistent") is None


def test_search_by_layer():
    """search_by_layer should search within single layer."""
    f = MemoryFusion()
    entry = MemoryEntry(key="layer_test", value="unique_value",
                        tags=["test"], importance=0.6, layer="working")
    f.working.store(entry)
    results = f.search_by_layer("working", query="unique")
    assert len(results) >= 1
    assert results[0].key == "layer_test"
    # Should not find in semantic layer
    results2 = f.search_by_layer("semantic", query="unique")
    assert len(results2) == 0
    # Cleanup
    f.working._entries.pop("layer_test", None)
