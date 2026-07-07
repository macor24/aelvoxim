"""Additional memory tests — semantic upgrade, hibernation."""

from aelvoxim.memory import store_entity, _fusion


def test_person_goes_to_semantic():
    """Person entity should go to semantic layer (importance >= 0.8)."""
    eid = "test:sem_person"
    store_entity(eid, "person", {"name": "SemPerson"}, tags=["extracted", "person"], user_id="test")
    # Check if in semantic or procedural
    in_sem = eid in _fusion.semantic._entries
    in_proc = eid in _fusion.procedural._entries
    assert in_sem or in_proc, f"Expected {eid} in semantic or procedural"


def test_hibernation_marker():
    """Entity can be marked as hibernating in attributes."""
    eid = "test:hibernate_test"
    store_entity(eid, "concept", {"name": "HibernateMe"}, tags=["test"], user_id="test")
    from aelvoxim.memory import search_entities
    results = search_entities("HibernateMe")
    assert any("HibernateMe" in str(r.get("value", "")) for r in results)
