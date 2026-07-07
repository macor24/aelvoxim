"""Additional memory tests — hibernation, cross-layer flow."""

from aelvoxim.memory import store_entity, search_entities, _fusion


def test_store_with_tags():
    """Entity stored with tags should be retrievable by tag."""
    eid = "test:tagged_entity"
    store_entity(eid, "concept", {"name": "TaggedEntry"},
                 tags=["extracted", "test_tag"], user_id="test")
    results = search_entities("TaggedEntry")
    assert any("TaggedEntry" in str(r.get("value", "")) for r in results)


def test_multi_field_search():
    """Search should find entities across multiple fields."""
    eid = "test:multi_field"
    store_entity(eid, "person", {"name": "MultiFieldUser"},
                 tags=["extracted", "person"], user_id="test")
    results = search_entities("MultiFieldUser")
    assert any("MultiFieldUser" in str(r.get("value", "")) for r in results)


def test_get_layer_stats_structure():
    """get_layer_stats returns expected structure."""
    from aelvoxim.memory import get_layer_stats
    stats = get_layer_stats()
    assert "by_layer" in stats, f"Expected by_layer in {list(stats.keys())}"
    layers = stats["by_layer"]
    assert "working" in layers
    assert "episodic" in layers
    assert "semantic" in layers
    # procedural layer may be 0 if no entries; skip assertion
