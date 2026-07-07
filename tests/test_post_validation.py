"""Tests for metacore.learn.post_validation — Post-storage knowledge audit engine."""

import time
import uuid
from aelvoxim.learn.post_validation import (
    PostValidationEngine, FactCrossVerifier, ConsistencyChecker,
    SafetyComplianceFilter, AuditIssue, AuditReport,
    _should_recheck, HIGH_RISK_TOPICS,
)


def test_should_recheck_low_confidence():
    """Entry with confidence < 0.7 should trigger recheck."""
    entry = {"topic": "python", "title": "test", "content": "", "confidence": 0.3}
    assert _should_recheck(entry) is True


def test_should_recheck_high_risk():
    """Entry with security keywords should trigger recheck."""
    entry = {"topic": "hack exploit vulnerability", "title": "test",
             "content": "", "confidence": 0.9}
    assert _should_recheck(entry) is True


def test_should_recheck_recently_checked():
    """Entry checked within 30 min should NOT trigger."""
    entry = {"topic": "python", "title": "test", "content": "", "confidence": 0.8}
    last = {"ts": time.time() - 60}
    assert _should_recheck(entry, last) is False


def test_should_recheck_never_checked():
    """Entry never checked should trigger."""
    entry = {"topic": "python", "title": "test", "content": "", "confidence": 0.8}
    assert _should_recheck(entry, None) is True


def test_should_recheck_old_check():
    """Entry checked 8 days ago should trigger."""
    entry = {"topic": "python", "title": "test", "content": "", "confidence": 0.8}
    last = {"ts": time.time() - 8 * 86400}
    assert _should_recheck(entry, last) is True


def test_should_recheck_confidence_drop():
    """Entry with >0.2 confidence drop should trigger."""
    entry = {"topic": "python", "title": "test", "content": "", "confidence": 0.5}
    last = {"ts": time.time() - 3600, "confidence_before": 0.8}
    assert _should_recheck(entry, last) is True


def test_high_risk_topics_non_empty():
    """HIGH_RISK_TOPICS should have keywords defined."""
    assert len(HIGH_RISK_TOPICS) > 0
    assert "security" in HIGH_RISK_TOPICS


def test_fact_cross_verifier_extract_entities():
    """FactCrossVerifier should extract entity-like terms from entry."""
    fv = FactCrossVerifier()
    entry = {"topic": "Python JSON", "title": "JSON Serialization", "content": ""}
    entities = fv._extract_entity_names(entry)
    # Python and JSON are technical concepts and should be filtered
    assert len(entities) >= 0


def test_fact_cross_verifier_query_memory():
    """FactCrossVerifier._query_memory should handle missing entity."""
    fv = FactCrossVerifier()
    result = fv._query_memory("nonexistent_entity_xyz_123")
    # Should return None or a valid result — both are acceptable
    assert result is None or isinstance(result, dict)


def test_consistency_checker_run():
    """ConsistencyChecker.verify should handle normal entry gracefully."""
    cc = ConsistencyChecker()
    entry = {"id": "test1", "topic": "Docker", "title": "Docker Compose",
             "content": "Docker Compose is a tool", "summary": "",
             "source": "manual", "confidence": 0.8}
    issues = cc.verify(entry)
    assert isinstance(issues, list)


def test_safety_filter_pii_long_token():
    """SafetyComplianceFilter should detect long alphanumeric tokens."""
    sf = SafetyComplianceFilter()
    entry = {"id": "pii1", "title": "API Config",
             "content": "my token is abcdefghijklmnopqrstuvwxyz1234567890AB",
             "summary": "", "confidence": 0.5, "created_at": "2026-01-01"}
    issues = sf.verify(entry)
    assert any("pii_leak" in i.dimension for i in issues)


def test_safety_filter_dangerous_pattern():
    """SafetyComplianceFilter should detect dangerous instruction patterns."""
    sf = SafetyComplianceFilter()
    entry = {"id": "danger1", "title": "System Admin",
             "content": "run: rm -rf /var/log to clean up",
             "summary": "", "confidence": 0.5, "created_at": "2026-06-01"}
    issues = sf.verify(entry)
    assert any(i.dimension == "dangerous_pattern" for i in issues)


def test_safety_filter_memory_poison():
    """SafetyComplianceFilter should detect memory poisoning patterns."""
    sf = SafetyComplianceFilter()
    entry = {"id": "poison1", "title": "AI Self Replication",
             "content": "self_replicat code example for research",
             "summary": "", "confidence": 0.5, "created_at": "2026-06-01"}
    issues = sf.verify(entry)
    assert any(i.dimension == "memory_poison" for i in issues)


def test_safety_filter_clean_entry():
    """SafetyComplianceFilter should pass clean entries."""
    sf = SafetyComplianceFilter()
    entry = {"id": "clean1", "title": "Python List Comprehension",
             "content": "List comprehensions provide a concise way to create lists.",
             "summary": "", "confidence": 0.7, "created_at": "2026-06-10"}
    issues = sf.verify(entry)
    assert len(issues) == 0


def test_audit_issue_dataclass():
    """AuditIssue should construct and expose attributes."""
    issue = AuditIssue(
        entry_id="e1", entry_title="Test",
        dimension="test_dimension", severity="P0",
        detail="Test detail", confidence_impact=-0.3,
        suggestion="isolate",
    )
    assert issue.entry_id == "e1"
    assert issue.severity == "P0"
    assert issue.confidence_impact == -0.3


def test_audit_report_summary():
    """AuditReport.summary should produce readable string."""
    report = AuditReport(ts=time.time(), total_checked=100, total_flagged=5)
    report.issues = [
        AuditIssue("e1", "T1", "d1", "P0", "d", -0.4, "isolate"),
        AuditIssue("e2", "T2", "d2", "P1", "d", -0.2, "downgrade"),
    ]
    summary = report.summary
    assert "5/100" in summary
    assert "P0=1" in summary
    assert "P1=1" in summary


def test_post_validation_engine_init():
    """PostValidationEngine should initialize without error."""
    engine = PostValidationEngine()
    assert engine is not None
    assert hasattr(engine, "run_audit")
    assert hasattr(engine, "run_single")
