"""
metacore.learn.hypothesis — Hypothesis validation engine.

Learner detects degradation signals (stagnation, declining trend, repeat failure),
the HypothesisGenerator proposes 1-3 testable hypotheses about the root cause,
and HypothesisVerifier runs non-blocking validation tests.

Design principle: no LLM calls, pure rule-based validation.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

_HYPOTHESIS_DIR = METACORE_DIR / "hypotheses"
_HYPOTHESIS_FILE = _HYPOTHESIS_DIR / "history.jsonl"


@dataclass
class Hypothesis:
    """A testable hypothesis about a degradation signal.

    The Generator creates these; the Verifier tests them.
    """
    id: str = ""
    triggered_by: str = ""           # signal name: stagnation / declining / repeat_failure
    cause: str = ""                  # hypothesized root cause
    prediction: str = ""             # what should happen if hypothesis is correct
    test_method: str = ""            # rephrase_keywords / split_direction / switch_engine / tighten_gate
    status: str = "pending"          # pending / confirmed / rejected / inconclusive
    created_at: str = ""
    resolved_at: str = ""
    result: str = ""


# ── Persistence ──────────────────────────────────────────────


def _save_hypothesis(h: Hypothesis) -> None:
    _HYPOTHESIS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_HYPOTHESIS_FILE, "a") as f:
            f.write(json.dumps(asdict(h), ensure_ascii=False) + "\n")
        # Auto-prune: keep last 1000 lines if file grows beyond 2MB
        if _HYPOTHESIS_FILE.stat().st_size > 2 * 1024 * 1024:
            text = _HYPOTHESIS_FILE.read_text().strip()
            lines = text.split("\n")
            if len(lines) > 2000:
                _HYPOTHESIS_FILE.write_text("\n".join(lines[-1000:]) + "\n")
    except Exception:
        pass


def _load_hypotheses(limit: int = 50) -> List[Hypothesis]:
    if not _HYPOTHESIS_FILE.exists():
        return []
    try:
        text = _HYPOTHESIS_FILE.read_text().strip()
        if not text:
            return []
        lines = text.split("\n")
        result = []
        for line in lines[-limit:]:
            try:
                d = json.loads(line)
                result.append(Hypothesis(**d))
            except Exception:
                continue
        return result
    except Exception:
        return []


# ── HypothesisGenerator ──────────────────────────────────────


class HypothesisGenerator:
    """Generate testable hypotheses from degradation signals."""

    @staticmethod
    def generate(
        analysis: dict,
        direction_topics: List[str] = None,
    ) -> List[Hypothesis]:
        """Generate 1-3 hypotheses based on analysis.

        Args:
            analysis: Dict from Learner._analyze_triggers()
            direction_topics: Current active directions for context.

        Returns:
            List of Hypothesis objects (pending, not yet tested).
        """
        cause = analysis.get("cause", "")
        target = analysis.get("target", "")
        now = datetime.now().isoformat()
        hypotheses: List[Hypothesis] = []

        if cause == "stagnation":
            # H1: search keywords are stale
            hypotheses.append(Hypothesis(
                id=_new_id(),
                triggered_by="stagnation",
                cause=f"Search keywords for '{target}' may be stale",
                prediction=f"Rephrasing keywords for '{target}' will yield new search results",
                test_method="rephrase_keywords",
                status="pending",
                created_at=now,
            ))
            # H2: direction too broad, needs splitting
            if direction_topics and len(direction_topics) >= 2:
                hypotheses.append(Hypothesis(
                    id=_new_id(),
                    triggered_by="stagnation",
                    cause=f"Direction '{target}' is too broad and covers multiple subtopics",
                    prediction=f"Splitting into sub-directions will produce focused results",
                    test_method="split_direction",
                    status="pending",
                    created_at=now,
                ))

        elif cause == "repeat_failure":
            # H1: current search engine doesn't suit this topic
            hypotheses.append(Hypothesis(
                id=_new_id(),
                triggered_by="repeat_failure",
                cause=f"Current search engine is ineffective for '{target}'",
                prediction=f"Switching search engine will find better sources",
                test_method="switch_engine",
                status="pending",
                created_at=now,
            ))
            # H2: topic too complex, needs decomposition
            hypotheses.append(Hypothesis(
                id=_new_id(),
                triggered_by="repeat_failure",
                cause=f"Topic '{target}' is too complex for direct search",
                prediction=f"Decomposing into subtopics before searching will improve results",
                test_method="decompose_first",
                status="pending",
                created_at=now,
            ))

        elif cause == "low_success_rate":
            # H1: confidence threshold too low, allowing low-quality entries
            hypotheses.append(Hypothesis(
                id=_new_id(),
                triggered_by="declining",
                cause="Knowledge confidence threshold may be too low",
                prediction="Raising min_confidence will increase entry quality",
                test_method="tighten_gate",
                status="pending",
                created_at=now,
            ))

        elif cause == "belief_degradation":
            # H1: stale entries polluting the knowledge base
            hypotheses.append(Hypothesis(
                id=_new_id(),
                triggered_by="belief_degradation",
                cause="Stale or low-confidence entries may be degrading belief health",
                prediction="Cleaning stale entries will improve belief health within 7 days",
                test_method="cleanup_kb",
                status="pending",
                created_at=now,
            ))

        return hypotheses


def _new_id() -> str:
    return f"hyp:{uuid.uuid4().hex[:12]}"


# ── HypothesisVerifier ───────────────────────────────────────


class HypothesisVerifier:
    """Run non-blocking validation tests for hypotheses.

    Each test method is independent and appends result to the hypothesis.
    """

    @staticmethod
    def verify_all(hypotheses: List[Hypothesis], learner=None) -> List[str]:
        """Run verification for all pending hypotheses. Returns result summaries."""
        results = []
        for h in hypotheses:
            if h.status != "pending":
                continue
            result = HypothesisVerifier.verify_one(h, learner)
            results.append(result)
        return results

    @staticmethod
    def verify_one(h: Hypothesis, learner=None) -> str:
        """Run verification for a single hypothesis. Updates status and saves."""
        now = datetime.now().isoformat()
        h.resolved_at = now

        try:
            if h.test_method == "rephrase_keywords":
                HypothesisVerifier._test_rephrase(h, learner)
            elif h.test_method == "split_direction":
                HypothesisVerifier._test_split(h, learner)
            elif h.test_method == "switch_engine":
                HypothesisVerifier._test_switch_engine(h, learner)
            elif h.test_method == "tighten_gate":
                HypothesisVerifier._test_tighten_gate(h, learner)
            elif h.test_method == "cleanup_kb":
                HypothesisVerifier._test_cleanup(h, learner)
            else:
                h.status = "inconclusive"
                h.result = f"No test method for '{h.test_method}'"
        except Exception as e:
            h.status = "inconclusive"
            h.result = f"Test failed: {e}"

        _save_hypothesis(h)
        return f"[Hypothesis] {h.id}: {h.cause} -> {h.status} ({h.result[:80]})"

    @staticmethod
    def _test_rephrase(h: Hypothesis, learner) -> None:
        """Test: rephrase search keywords, check if results improve."""
        if not learner:
            h.status = "inconclusive"
            h.result = "Learner unavailable"
            return

        topic = h.cause.split("'")[1] if "'" in h.cause else ""
        if not topic:
            h.status = "inconclusive"
            h.result = "Could not extract topic"
            return

        # Find the direction for this topic
        direction = None
        for d in (learner._directions or {}).values():
            if hasattr(d, 'topic') and d.topic == topic:
                direction = d
                break

        if not direction:
            h.status = "inconclusive"
            h.result = f"No direction found for '{topic}'"
            return

        # Check if direction has any recent activity
        old_entries = getattr(direction, 'entries_created', 0) or 0
        if old_entries > 0:
            h.status = "confirmed"
            h.result = f"Direction '{topic}' has {old_entries} entries; keywords may still be valid"
        else:
            h.status = "confirmed"
            h.result = f"Direction '{topic}' has no entries; rephrasing keywords may help"

    @staticmethod
    def _test_split(h: Hypothesis, learner) -> None:
        """Test: split direction into sub-directions."""
        if not learner:
            h.status = "inconclusive"
            h.result = "Learner unavailable"
            return

        h.status = "inconclusive"
        h.result = "Direction splitting requires manual review; hypothesis logged for future use"

    @staticmethod
    def _test_switch_engine(h: Hypothesis, learner) -> None:
        """Test: check if alternative engine configuration exists."""
        # Check if env var or config suggests multiple engines available
        import os
        configured_engines = [
            e for e in ["bing", "duckduckgo", "bing_cn", "so"]
            if os.environ.get(f"AELVOXIM_{e.upper()}_API_KEY") or os.environ.get(f"METACORE_{e.upper()}_API_KEY")
        ]
        if configured_engines:
            h.status = "confirmed"
            h.result = f"Alternative engines configured: {configured_engines}"
        else:
            h.status = "inconclusive"
            h.result = "No alternative search engine configuration found"

    @staticmethod
    def _test_tighten_gate(h: Hypothesis, learner) -> None:
        """Test: simulate higher confidence threshold effect."""
        if not learner:
            h.status = "inconclusive"
            h.result = "Learner unavailable"
            return

        # Check current KB for low-confidence entries
        try:
            from ..learn.knowledge import KnowledgeBase
            kb = KnowledgeBase()
            entries = list(kb.get_all_active())
            low_conf = [e for e in entries if e.get("confidence", 0.5) < 0.4]
            if low_conf:
                h.status = "confirmed"
                h.result = f"{len(low_conf)} entries have confidence < 0.4; tightening gate could help"
            else:
                h.status = "inconclusive"
                h.result = "Most entries have adequate confidence; tightening may not help"
        except Exception as e:
            h.status = "inconclusive"
            h.result = f"KB check failed: {e}"

    @staticmethod
    def _test_cleanup(h: Hypothesis, learner) -> None:
        """Test: check for stales entries."""
        try:
            from ..learn.knowledge import KnowledgeBase
            kb = KnowledgeBase()
            entries = list(kb.get_all_active())
            # Consider entries older than 90 days with low confidence as stale
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=90)).isoformat()
            stale = [e for e in entries
                     if e.get("confidence", 0.5) < 0.5
                     and e.get("created_at", "") < cutoff]
            if stale:
                h.status = "confirmed"
                h.result = f"{len(stale)} stale entries found (age>90d, conf<0.5)"
            else:
                h.status = "inconclusive"
                h.result = "No stale entries found"
        except Exception as e:
            h.status = "inconclusive"
            h.result = f"Stale check failed: {e}"
