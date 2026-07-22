"""
metacore.learn.post_validation — Post-storage knowledge audit engine.

Three independent verifiers run on stored knowledge entries that meet
trigger conditions (low confidence, high-risk topic, confidence drop,
or time since last check). All are read-only against memory — only
knowledge entry confidence + review_history are updated.

Verifiers:
  1. FactCrossVerifier      — Cross-reference facts against memory layer
  2. ConsistencyChecker     — Internal logical consistency within knowledge base
  3. SafetyComplianceFilter — Secondary security/regulatory scan
"""

from __future__ import annotations

import json
import re
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import logging
_log = logging.getLogger("aelvoxim.post_validation")



# ── Risk topic keywords ──────────────────────────────────────

HIGH_RISK_TOPICS: Set[str] = {
    # Security attacks
    "security", "hack", "exploit", "vulnerability",
    "malware", "trojan", "ransomware", "backdoor",
    "入侵", "漏洞利用", "破解", "木马", "病毒",
    # Data leaks
    "data leak", "data breach", "privacy",
    "泄露", "数据泄露", "隐私",
    # AI safety red lines
    "self-replicat", "self_replicat", "clone itself",
    "autonomous replicat", "memory poison",
    "prompt injection", "jailbreak",
    # Ethics / compliance
    "伦理", "伦理边界", "合规", "红线",
    # Destructive ops
    "destructive", "bypass", "rm -rf",
    "破坏", "删除系统",
    # PII
    "api_key", "api key", "password", "token",
    "secret key", "private key", "auth key",
}

# ── PII detection patterns ───────────────────────────────────

_PII_PATTERNS: List[Tuple[str, str, str]] = [
    ("api_key", r"(?i)\b(sk-[a-zA-Z0-9_-]{10,})\b", "API Key pattern"),
    ("long_token", r"(?i)\b([a-zA-Z0-9_-]{32,})\b", "Long alphanumeric token"),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email address"),
    ("phone_cn", r"\b1[3-9]\d{9}\b", "Chinese phone number"),
    ("password_var", r"(?i)\b(password|passwd|pwd)\s*[:=]\s*\S{6,}", "Password assignment"),
]

# ── Dangerous instruction patterns (from security_gate) ───────

_DANGEROUS_PATTERNS: List[str] = [
    "DROP TABLE", "DROP DATABASE", "TRUNCATE",
    "rm -rf", "rm -rf /", ":(){ :|:& };:", "fork bomb",
    "chmod 777", "chown root",
    "self-replicat", "self_replicat", "clone itself",
    "autonomous replicat",
]

# ── Data structures ──────────────────────────────────────────


@dataclass
class AuditIssue:
    entry_id: str
    entry_title: str
    dimension: str           # e.g. "direct_contradiction", "pii_leak"
    severity: str            # "P0" / "P1" / "P2"
    detail: str
    confidence_impact: float # -0.5 ~ 0.0
    suggestion: str          # "isolate" / "downgrade" / "flag" / "log"
    matched_content: str = ""


@dataclass
class AuditReport:
    ts: float = 0.0
    total_checked: int = 0
    total_flagged: int = 0
    issues: List[AuditIssue] = field(default_factory=list)
    entries_adjusted: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def summary(self) -> str:
        p0 = sum(1 for i in self.issues if i.severity == "P0")
        p1 = sum(1 for i in self.issues if i.severity == "P1")
        p2 = sum(1 for i in self.issues if i.severity == "P2")
        parts = [f"{self.total_flagged}/{self.total_checked} flagged"]
        if p0:
            parts.append(f"P0={p0}")
        if p1:
            parts.append(f"P1={p1}")
        if p2:
            parts.append(f"P2={p2}")
        return " | ".join(parts)


# ── Trigger condition ────────────────────────────────────────


def _should_recheck(entry: dict, last_check: Optional[dict] = None) -> bool:
    """Determine if a knowledge entry needs post-validation.

    Four trigger conditions (OR):
      1. Confidence < 0.7
      2. High-risk topic match in title/content
      3. Confidence dropped > 0.2 since last check
      4. More than 7 days since last check
    """
    # Minimum interval: 30 minutes
    if last_check and (time.time() - last_check.get("ts", 0)) < 1800:
        return False

    conf = entry.get("confidence", 0.5)
    topic = (entry.get("topic", "") or "").lower()
    title = (entry.get("title", "") or "").lower()
    content = (entry.get("content", "") or "")[:500].lower()
    text = f"{topic} {title} {content}"

    # 1. Low confidence
    if conf < 0.7:
        return True

    # 2. High-risk topic
    if any(kw.lower() in text for kw in HIGH_RISK_TOPICS):
        return True

    # 3. Confidence drop > 0.2
    prev_conf = last_check.get("confidence_before", conf) if last_check else None
    if prev_conf is not None and (prev_conf - conf) > 0.2:
        return True

    # 4. No check in 7+ days
    if last_check is None:
        return True  # never checked
    days_since = (time.time() - last_check.get("ts", 0)) / 86400
    if days_since > 7:
        return True

    return False


# ── Helper: text similarity ──────────────────────────────────


def _text_similarity(a: str, b: str) -> float:
    """Character-level Jaccard similarity over first 100 chars."""
    if not a or not b:
        return 0.0
    a_chars = set(a.strip().lower()[:100])
    b_chars = set(b.strip().lower()[:100])
    intersection = len(a_chars & b_chars)
    union = len(a_chars | b_chars)
    return intersection / max(union, 1)


# ══════════════════════════════════════════════════════════════
# FactCrossVerifier — Cross-reference against memory layer
# ══════════════════════════════════════════════════════════════


class FactCrossVerifier:
    """Check knowledge entry facts against semantic memory.

    Reads from SQLite entities table + in-memory fusion layers.
    Never writes to memory — read-only audit.
    """

    def __init__(self, log_func=None):
        self._log = log_func or (lambda msg: None)
        self._db_conn = None

    def _get_db(self):
        if self._db_conn is None:
            try:
                import sqlite3
                from ..utils import METACORE_DIR
                db_path = str(METACORE_DIR / "memory.db")
                self._db_conn = sqlite3.connect(db_path)
                self._db_conn.row_factory = sqlite3.Row
            except Exception:
                self._db_conn = None
        return self._db_conn

    # Known technical concepts that should NOT trigger unverified_claim
    _TECH_CONCEPT_PATTERNS = [
        "^SQLite", "^JSON$", "^TOOL_CALL", "^LLM", "^API",
        "^HTTP", "^HTTPS", "^REST", "^CLI", "^SDK", "^IDE",
        "^CSS$", "^HTML$", "^XML$", "^YAML$", "^TLS$", "^SSL$",
        "^Docker", "^Kubernetes", "^K8s", "^Git$", "^GitHub", "^GitLab",
        "^Python", "^Java", "^Rust$", "^Go$", "^Node",
        "^React$", "^Vue$", "^Angular", "^FastAPI", "^Flask", "^Django",
        "^Redis$", "^MongoDB", "^PostgreSQL", "^MySQL$", "^SQL$",
        "^MemorySystem", "^Memory$",
        "^TCP$", "^UDP$", "^OAuth", "^JWT$", "^CORS$",
        "^AI$", "^ML$", "^LLM$", "^NLP$",
        "^Linux", "^Windows", "^MacOS", "^Unix$",
        "^Aelvoxim", "^MetaCore", "^SentriKit", "^CodeNova",
        "^DeepSeek", "^ChatGPT", "^OpenAI",
        "^v\\d", "^V\\d",
    ]

    _TECH_CONCEPT_RE = re.compile(
        "|".join(f"({p})" for p in _TECH_CONCEPT_PATTERNS)
    )

    def _extract_entity_names(self, entry: dict) -> List[str]:
        """Extract likely entity names from entry content."""
        text = f"{entry.get('topic', '')} {entry.get('title', '')}"
        # Look for capitalized technical terms and proper nouns
        entities = re.findall(r'\b[A-Z][A-Za-z0-9+#./_-]{2,}\b', text)
        # Also check content for entity patterns
        content = entry.get("content", "") or ""
        entities += re.findall(r'\b[A-Z][A-Za-z0-9+#./_-]{2,}\b', content[:200])
        # Remove duplicates, short items, and pure numbers
        seen: Set[str] = set()
        result: List[str] = []
        for e in entities:
            low = e.lower()
            if len(e) < 3 or e.isdigit() or low in seen:
                continue
            # Skip known technical concepts
            if self._TECH_CONCEPT_RE.match(e):
                continue
            seen.add(low)
            result.append(e)
        # Also extract topic as an entity if it looks like a known concept
        topic = entry.get("topic", "").strip()
        if topic and topic.lower() not in seen and len(topic) >= 3:
            # Skip if it's a known tech concept
            if not self._TECH_CONCEPT_RE.match(topic):
                result.append(topic)
        return result

    def _query_memory(self, entity_name: str) -> Optional[dict]:
        """Search memory for an entity by name. Returns first match or None."""
        db = self._get_db()
        if db is None:
            return None
        try:
            # Search in entities table by id or value
            name_lower = entity_name.lower().strip()
            row = db.execute(
                "SELECT id, type, value, tags, attributes FROM entities "
                "WHERE (LOWER(id) = ? OR LOWER(value) = ?) AND type != 'event' "
                "ORDER BY created_at DESC LIMIT 1",
                (name_lower, name_lower),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "value": row["value"],
                    "type": row["type"],
                    "tags": json.loads(row["tags"] or "[]"),
                    "attributes": json.loads(row["attributes"] or "{}"),
                }
            # Fallback: LIKE search
            row = db.execute(
                "SELECT id, type, value, tags, attributes FROM entities "
                "WHERE (LOWER(id) LIKE ? OR LOWER(value) LIKE ?) AND type != 'event' "
                "ORDER BY created_at DESC LIMIT 1",
                (f"%{name_lower}%", f"%{name_lower}%"),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "value": row["value"],
                    "type": row["type"],
                    "tags": json.loads(row["tags"] or "[]"),
                    "attributes": json.loads(row["attributes"] or "{}"),
                }
        except Exception:
            _log.exception("post_validation error")
        return None

    def verify(self, entry: dict) -> List[AuditIssue]:
        """Run fact cross-verification on a single entry."""
        issues: List[AuditIssue] = []
        entity_names = self._extract_entity_names(entry)
        if not entity_names:
            return issues

        eid = entry.get("id", "unknown")
        title = entry.get("title", "")[:60]
        content_snippet = (entry.get("content", "") or entry.get("summary", ""))[:200]

        for name in entity_names:
            mem_entity = self._query_memory(name)
            if not mem_entity:
                # Unverified claim: entity referenced but not in memory
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="unverified_claim", severity="P2",
                    detail=f"Entity '{name}' not found in memory — unverified claim",
                    confidence_impact=-0.1,
                    suggestion="flag",
                    matched_content=name,
                ))
                continue

            mem_value = mem_entity.get("value", "")
            mem_attrs = mem_entity.get("attributes", {})

            # 1. Direct contradiction: entry content vs memory value
            if mem_value:
                sim = _text_similarity(content_snippet, mem_value)
                if sim < 0.3 and len(mem_value) > 10:
                    issues.append(AuditIssue(
                        entry_id=eid, entry_title=title,
                        dimension="direct_contradiction", severity="P0",
                        detail=f"'{name}': entry content contradicts memory value ('{mem_value[:60]}')",
                        confidence_impact=-0.4,
                        suggestion="isolate",
                        matched_content=mem_value[:80],
                    ))

            # 2. Confidence inversion: entry confident but memory says low
            mem_conf = mem_attrs.get("_confidence", 0.5)
            entry_conf = entry.get("confidence", 0.5)
            if entry_conf > 0.8 and mem_conf < 0.3:
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="confidence_inversion", severity="P1",
                    detail=f"'{name}': entry conf={entry_conf:.1f} but memory conf={mem_conf:.1f}",
                    confidence_impact=-0.2,
                    suggestion="downgrade",
                    matched_content=name,
                ))

            # 3. Stale claim: memory has been superseded
            superseded = mem_attrs.get("_superseded", "")
            if superseded:
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="stale_claim", severity="P1",
                    detail=f"'{name}' was superseded in memory (old='{superseded[:40]}')",
                    confidence_impact=-0.2,
                    suggestion="downgrade",
                    matched_content=superseded[:80],
                ))

            # 4. Unresolved conflict in memory
            if mem_attrs.get("_conflict") and not mem_attrs.get("_conflict_resolved"):
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="unresolved_conflict", severity="P2",
                    detail=f"'{name}' has unresolved conflict in memory layer",
                    confidence_impact=-0.1,
                    suggestion="flag",
                    matched_content=name,
                ))

        return issues


# ══════════════════════════════════════════════════════════════
# ConsistencyChecker — Internal knowledge base logical consistency
# ══════════════════════════════════════════════════════════════


class ConsistencyChecker:
    """Check logical consistency of a knowledge entry against the rest
    of the knowledge base. Read-only.
    """

    def __init__(self, log_func=None):
        self._log = log_func or (lambda msg: None)

    # Sentiment keyword lists (mirrored from validator.py)
    _POSITIVE_KW = ["优势", "好处", "有效", "提高", "增长", "Success",
                    "正确", "推荐", "支持", "促进"]
    _NEGATIVE_KW = ["风险", "问题", "缺陷", "限制", "不足", "Failure",
                    "Error", "反对", "危害", "降低"]

    @staticmethod
    def _detect_sentiment(text: str) -> str:
        t = text.lower()
        pos = sum(1 for kw in ConsistencyChecker._POSITIVE_KW if kw in t)
        neg = sum(1 for kw in ConsistencyChecker._NEGATIVE_KW if kw in t)
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "neutral"

    def verify(self, entry: dict) -> List[AuditIssue]:
        """Run consistency checks against other entries in the KB."""
        issues: List[AuditIssue] = []
        try:
            from .knowledge import KnowledgeBase
        except Exception:
            return issues

        eid = entry.get("id", "unknown")
        title = entry.get("title", "")[:60]
        topic = entry.get("topic", "")
        source = entry.get("source", "")
        content = entry.get("content", "") or entry.get("summary", "")
        entry_conf = entry.get("confidence", 0.5)
        entry_sentiment = self._detect_sentiment(content)

        all_active = list(KnowledgeBase.get_all_active())

        # 1. Same-topic contradiction
        same_topic = [e for e in all_active
                      if e.get("id") != eid
                      and e.get("topic") == topic
                      and e.get("title", "").lower() == title.lower()]

        from difflib import SequenceMatcher
        for other in same_topic:
            other_content = other.get("content", "") or other.get("summary", "")
            if not other_content:
                continue
            # Check sentiment opposition
            other_sent = self._detect_sentiment(other_content)
            if (entry_sentiment == "positive" and other_sent == "negative") or \
               (entry_sentiment == "negative" and other_sent == "positive"):
                sim = SequenceMatcher(None, content[:100].lower(),
                                      other_content[:100].lower()).ratio()
                if sim > 0.3:
                    issues.append(AuditIssue(
                        entry_id=eid, entry_title=title,
                        dimension="topic_contradiction", severity="P1",
                        detail=f"Same-topic '{topic}': opposite sentiment vs '{other.get('id','')[:8]}'",
                        confidence_impact=-0.2,
                        suggestion="flag",
                        matched_content=other_content[:80],
                    ))

        # 2. Cross-source consistency: same title, different source, large conf gap
        same_title = [e for e in all_active
                      if e.get("id") != eid
                      and e.get("title", "").lower() == title.lower()
                      and e.get("source", "") != source]
        for other in same_title:
            other_conf = other.get("confidence", 0.5)
            if abs(entry_conf - other_conf) > 0.4:
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="cross_source_inconsistency", severity="P2",
                    detail=f"Source '{source[:15]}' conf={entry_conf:.1f} vs "
                           f"'{other.get('source','')[:15]}' conf={other_conf:.1f}",
                    confidence_impact=-0.1,
                    suggestion="flag",
                    matched_content=f"conf gap: {abs(entry_conf - other_conf):.1f}",
                ))

        # 3. Entity naming pollution in same topic
        if topic:
            same_topic_entries = [e for e in all_active
                                  if e.get("topic") == topic and e.get("id") != eid]
            # Normalize entity names: extract all capitalized terms
            tech_terms: Dict[str, Set[str]] = {}
            for te in same_topic_entries:
                t_text = f"{te.get('title','')} {te.get('content','')}"
                t_terms = set(re.findall(r'\b[A-Z][A-Za-z0-9+#./_-]{2,}\b', t_text))
                for term in t_terms:
                    key = term.lower()
                    tech_terms.setdefault(key, set()).add(term)
            # Check if current entry uses a variant
            my_terms = set(re.findall(r'\b[A-Z][A-Za-z0-9+#./_-]{2,}\b',
                                      f"{title} {content}"))
            for my_term in my_terms:
                my_low = my_term.lower()
                if my_low in tech_terms:
                    variants = tech_terms[my_low]
                    if len(variants) > 1 and my_term not in variants:
                        issues.append(AuditIssue(
                            entry_id=eid, entry_title=title,
                            dimension="naming_pollution", severity="P2",
                            detail=f"Term '{my_term}' has variants: {', '.join(sorted(variants))}",
                            confidence_impact=-0.05,
                            suggestion="log",
                            matched_content=my_term,
                        ))

        return issues


# ══════════════════════════════════════════════════════════════
# SafetyComplianceFilter — Secondary security/regulatory scan
# ══════════════════════════════════════════════════════════════


class SafetyComplianceFilter:
    """Re-scan stored knowledge for security compliance issues.

    Checks: PII leaks, dangerous instructions, SentriKit re-verification,
    memory poisoning, expired secrets.
    """

    def __init__(self, log_func=None):
        self._log = log_func or (lambda msg: None)
        self._sentrikit_available: Optional[bool] = None

    @staticmethod
    def _scan_text(text: str, patterns: List[str]) -> List[str]:
        """Return matched patterns from text."""
        lower = text.lower()
        return [p for p in patterns if p.lower() in lower]

    @staticmethod
    def _find_pii(text: str) -> List[Tuple[str, str, str]]:
        """Find PII patterns in text. Returns list of (category, match, label)."""
        results: List[Tuple[str, str, str]] = []
        for category, pattern, label in _PII_PATTERNS:
            matches = re.findall(pattern, text)
            for m in matches:
                m_str = str(m)
                # Skip very short and obviously non-secret strings
                if len(m_str) < 5:
                    continue
                # Skip dates and version numbers
                if re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', m_str):
                    continue
                if re.match(r'^v?\d+\.\d+', m_str, re.I):
                    continue
                results.append((category, m_str[:40], label))
        return results

    def verify(self, entry: dict) -> List[AuditIssue]:
        """Run all compliance checks on a single entry."""
        issues: List[AuditIssue] = []
        eid = entry.get("id", "unknown")
        title = entry.get("title", "")[:60]
        text = f"{entry.get('title', '')} {entry.get('summary', '')} {entry.get('content', '')}"
        created_at = entry.get("created_at", "")

        # 1. PII detection
        pii_finds = self._find_pii(text)
        for category, match, label in pii_finds:
            severity = "P0" if category in ("api_key", "password_var") else "P1"
            issues.append(AuditIssue(
                entry_id=eid, entry_title=title,
                dimension=f"pii_leak:{category}", severity=severity,
                detail=f"{label} detected: '{match}'",
                confidence_impact=-0.5 if severity == "P0" else -0.3,
                suggestion="isolate" if severity == "P0" else "downgrade",
                matched_content=match,
            ))

        # 2. Dangerous instruction patterns
        danger_matches = self._scan_text(text, _DANGEROUS_PATTERNS)
        for dm in danger_matches:
            issues.append(AuditIssue(
                entry_id=eid, entry_title=title,
                dimension="dangerous_pattern", severity="P0",
                detail=f"Dangerous instruction pattern: '{dm}'",
                confidence_impact=-0.4,
                suggestion="isolate",
                matched_content=dm[:60],
            ))

        # 3. Memory poisoning patterns (high-risk subset)
        poison_kw = ["self-replicat", "self_replicat", "clone itself",
                     "autonomous replicat", "memory poison"]
        poison_matches = self._scan_text(text, poison_kw)
        for pm in poison_matches:
            issues.append(AuditIssue(
                entry_id=eid, entry_title=title,
                dimension="memory_poison", severity="P0",
                detail=f"Memory poisoning pattern: '{pm}'",
                confidence_impact=-0.5,
                suggestion="isolate",
                matched_content=pm[:60],
            ))

        # 4. Expired secret: API-key-like content created > 30 days ago
        if created_at:
            try:
                created_dt = datetime.strptime(str(created_at)[:10], "%Y-%m-%d")
                age_days = (datetime.now() - created_dt).days
                if age_days > 30:
                    # Check if the entry contains something secret-like
                    if re.search(r'(?i)\b(sk-[a-zA-Z0-9]+|key|token|secret)\b', text):
                        issues.append(AuditIssue(
                            entry_id=eid, entry_title=title,
                            dimension="expired_secret", severity="P1",
                            detail=f"Entry has secret-like content but is {age_days}d old",
                            confidence_impact=-0.2,
                            suggestion="downgrade",
                            matched_content=f"age={age_days}d",
                        ))
            except Exception:
                _log.exception("post_validation error")

        # 5. SentriKit re-check (only for high-confidence entries)
        if entry.get("confidence", 0.5) >= 0.8:
            sk_result = self._call_sentrikit(entry)
            if sk_result and not sk_result.get("allowed", True):
                issues.append(AuditIssue(
                    entry_id=eid, entry_title=title,
                    dimension="sentrikit_blocked", severity="P0",
                    detail=f"SentriKit re-check blocked: {sk_result.get('reason', '')[:80]}",
                    confidence_impact=-0.4,
                    suggestion="isolate",
                    matched_content=str(sk_result.get("reason", ""))[:80],
                ))

        return issues

    def _call_sentrikit(self, entry: dict) -> Optional[dict]:
        """Call SentriKit safety check for a stored entry."""
        try:
            from ..client.security_gate import check_evolution as _sk_check
            text = f"{entry.get('title','')}: {entry.get('summary','')}"
            result = _sk_check("post_audit", text[:500])
            return result
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════
# PostValidationEngine — Main audit orchestrator
# ══════════════════════════════════════════════════════════════


class PostValidationEngine:
    """Orchestrate post-storage knowledge audit.

    Usage:
        engine = PostValidationEngine()
        report = engine.run_audit(max_entries=50)
    """

    def __init__(self, log_func=None):
        self._log = log_func or (lambda msg: None)
        self._fact_verifier: Optional[FactCrossVerifier] = None
        self._consistency_checker: Optional[ConsistencyChecker] = None
        self._safety_filter: Optional[SafetyComplianceFilter] = None

    def _lazy_init(self):
        if self._fact_verifier is None:
            self._fact_verifier = FactCrossVerifier(self._log)
        if self._consistency_checker is None:
            self._consistency_checker = ConsistencyChecker(self._log)
        if self._safety_filter is None:
            self._safety_filter = SafetyComplianceFilter(self._log)

    def run_audit(self, max_entries: int = 50, max_issues: int = 20) -> AuditReport:
        """Scan all active knowledge entries and run post-validation.

        Args:
            max_entries: Max entries to audit per call (limit CPU usage).
            max_issues: Stop scanning entries after this many total issues.

        Returns:
            AuditReport with all flagged issues.
        """
        t0 = time.time()
        self._lazy_init()

        try:
            from .knowledge import KnowledgeBase
        except ImportError:
            return AuditReport(ts=t0)

        all_entries = list(KnowledgeBase.get_all_active())
        report = AuditReport(ts=t0, total_checked=len(all_entries))

        checked = 0
        for entry in all_entries:
            if checked >= max_entries:
                break
            if len(report.issues) >= max_issues:
                break

            # Check trigger condition
            review_history = entry.get("review_history", []) or []
            last_check = review_history[-1] if review_history else None
            if not _should_recheck(entry, last_check):
                continue

            checked += 1

            # Run all three verifiers
            issues: List[AuditIssue] = []

            try:
                issues.extend(self._fact_verifier.verify(entry))
            except Exception as e:
                self._log(f"  ⚠️ FactCrossVerifier error: {e}")

            try:
                issues.extend(self._consistency_checker.verify(entry))
            except Exception as e:
                self._log(f"  ⚠️ ConsistencyChecker error: {e}")

            try:
                issues.extend(self._safety_filter.verify(entry))
            except Exception as e:
                self._log(f"  ⚠️ SafetyComplianceFilter error: {e}")

            if not issues:
                # Record a clean check in review_history anyway
                self._record_check(entry, [])
                continue

            # Record and adjust
            self._record_check(entry, issues)
            self._adjust_confidence(entry, issues, report)

            report.issues.extend(issues)
            report.total_flagged += 1

        report.duration_ms = round((time.time() - t0) * 1000)
        return report

    def run_single(self, entry: dict) -> List[AuditIssue]:
        """Run all verifiers on a single entry (for testing)."""
        self._lazy_init()
        issues: List[AuditIssue] = []
        try:
            issues.extend(self._fact_verifier.verify(entry))
        except Exception:
            _log.exception("post_validation error")
        try:
            issues.extend(self._consistency_checker.verify(entry))
        except Exception:
            _log.exception("post_validation error")
        try:
            issues.extend(self._safety_filter.verify(entry))
        except Exception:
            _log.exception("post_validation error")
        return issues

    # ── Entry mutations ──

    @staticmethod
    def _record_check(entry: dict, issues: List[AuditIssue]) -> None:
        """Append audit result to entry's review_history."""
        review_history = entry.get("review_history", []) or []
        max_severity = "P2"
        for i in issues:
            if i.severity == "P0":
                max_severity = "P0"
                break
            if i.severity == "P1":
                max_severity = "P1"

        review_history.append({
            "ts": time.time(),
            "check": "post_validation",
            "issues": len(issues),
            "top_severity": max_severity,
            "confidence_before": entry.get("confidence", 0.5),
        })
        entry["review_history"] = review_history

        # Write back to file
        try:
            from .knowledge import _write_entry
            _write_entry(entry)
        except Exception:
            _log.exception("post_validation error")

    @staticmethod
    def _adjust_confidence(entry: dict, issues: List[AuditIssue],
                           report: AuditReport) -> None:
        """Apply the most severe confidence impact and write back."""
        if not issues:
            return
        min_impact = min(i.confidence_impact for i in issues)
        old_conf = entry.get("confidence", 0.5)
        new_conf = max(0.05, old_conf + min_impact)
        entry["confidence"] = new_conf

        # Mark for manual review on P0
        if any(i.severity == "P0" for i in issues):
            flags = entry.get("_flags", []) or []
            if "requires_manual_review" not in flags:
                flags.append("requires_manual_review")
            entry["_flags"] = flags

        # Write back
        try:
            from .knowledge import _write_entry
            _write_entry(entry)
        except Exception:
            _log.exception("post_validation error")

        report.entries_adjusted.append(entry.get("id", "unknown"))
