"""aelvoxim.core.belief — Belief engine (Bayesian foundation)

Tracks quality profiles for knowledge and skills using Beta distribution.
Each BeliefUnit models expected value and uncertainty,
supports single/batch record, merge, and decay prior.

Design:
- Pure stdlib, zero external dependencies
- All thresholds read dynamically from calibration.json
- JSON serializable, cross-session persistence

Usage: 
    unit = BeliefUnit()
    unit.record_outcome(True)   # 1 success
    unit.record_outcome(False)  # 1 failure
    unit.record_batch(80, 100)  # 80 successes out of 100 batch
    print(unit.get_expected_value(), unit.get_confidence())
"""

from __future__ import annotations

import logging
_log = logging.getLogger("aelvoxim.belief")
import json
import math
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Belief unit ─────────────────────────────


@dataclass
class BeliefUnit:
    """Bayesian Belief unit. 

    Uses Beta distribution (Beta(alpha, beta)) to model knowledge/skill reliability. 
    alpha = Success count + 1 (Prior) 
    beta = Failure count + 1 (Prior) 

    Core formula: 
        Expected value E = alpha / (alpha + beta) 
        Uncertainty ≈ 1 / sqrt(alpha + beta) (high for small samples, low for large) 
        Confidence = 1 - Uncertainty 
    """

    # Beta distributionArgs
    alpha: int = 1          # Success + Prior
    beta: int = 1           # Failure + Prior
    # Metadata
    prior_alpha: int = 1    # Initial prior alpha (to distinguish prior from real evidence)
    prior_beta: int = 1     # Initial prior beta
    evidence_count: int = 0  # TotalEvidence count (excluding Prior) 
    last_updated: str = ""  # Last updated timestamp
    source: str = ""        # Evidence source ("skill_exec" / "human_feedback" / "self_heal") 

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Recording results ──

    def record_outcome(self, success: bool) -> None:
        """Record a single execution result."""
        if success:
            self.alpha += 1
        else:
            self.beta += 1
        self.evidence_count += 1
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def record_batch(self, successes: int, total: int) -> None:
        """Batch record a set of results."""
        if total <= 0:
            return
        self.alpha += successes
        self.beta += (total - successes)
        self.evidence_count += total
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Queries ──

    def get_expected_value(self) -> float:
        """Expected value = alpha / (alpha + beta)"""
        n = self.alpha + self.beta
        if n == 0:
            return 0.5
        return self.alpha / n

    def get_uncertainty(self) -> float:
        """Uncertainty ~ 1 / sqrt(alpha + beta)

        Low for large samples (>100), high for small samples (<5).
        Range: (0, 1], smaller = more certain.
        """
        n = self.alpha + self.beta
        if n <= 2:
            return 1.0
        return round(1.0 / math.sqrt(n), 4)

    def get_confidence(self) -> float:
        """Confidence = 1 - uncertainty"""
        return 1.0 - self.get_uncertainty()

    def get_total_evidence(self) -> int:
        """Total evidence count (including prior)."""
        real = self.evidence_count
        prior = self.prior_alpha + self.prior_beta
        return real + prior

    def get_summary(self, name: str = "") -> str:
        """Returns human-readable belief summary."""
        ev = self.get_expected_value()
        conf = self.get_confidence()
        total = self.evidence_count
        tag = f"[{name}] " if name else ""
        return (
            f"{tag}Expected {ev:.0%} (confidence {conf:.0%}), "
            f"based on {total} evidence"
        )

    def get_interval(self, width: float = 0.95) -> tuple:
        """Approximate Beta distribution confidence interval. 

        Uses normal approximation: mean ± z * std 
        z = 1.96 (95% CI), valid for large samples. 
        Wider interval for small samples is conservative. 
        """
        n = self.alpha + self.beta
        if n <= 2:
            return (0.0, 1.0)
        mean = self.get_expected_value()
        variance = (self.alpha * self.beta) / (n * n * (n + 1))
        std = math.sqrt(variance)
        z = {0.80: 1.28, 0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(width, 1.96)
        lo = max(0.0, mean - z * std)
        hi = min(1.0, mean + z * std)
        return (round(lo, 4), round(hi, 4))

    # ── Merge ──

    def merge_with(self, other: BeliefUnit) -> None:
        """Merge another belief unit's evidence."""
        # Accumulate real evidence (subtract each prior)
        real_alpha = (self.alpha - self.prior_alpha) + (other.alpha - other.prior_alpha)
        real_beta = (self.beta - self.prior_beta) + (other.beta - other.prior_beta)
        # Re-add prior
        self.alpha = self.prior_alpha + max(0, real_alpha)
        self.beta = self.prior_beta + max(0, real_beta)
        self.evidence_count += other.evidence_count
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Decay ──

    def decay_prior(self, days_passed: int, half_life_days: int = 90) -> None:
        """Prior weight decays over time.

        Recent evidence gets proportionally more weight (system becomes more "open").
        Less evidence required to shift belief.
        """
        if days_passed <= 0 or half_life_days <= 0:
            return
        # Exponential decay factor
        factor = 2.0 ** (-days_passed / half_life_days)
        # Old prior influence multiplied by decay factor
        new_prior_alpha = max(0.5, round(self.prior_alpha * factor, 2))
        new_prior_beta = max(0.5, round(self.prior_beta * factor, 2))
        # Keep real evidence unchanged, recalculate total
        real_alpha = self.alpha - self.prior_alpha
        real_beta = self.beta - self.prior_beta
        self.alpha = new_prior_alpha + max(0, real_alpha)
        self.beta = new_prior_beta + max(0, real_beta)
        self.prior_alpha = new_prior_alpha
        self.prior_beta = new_prior_beta

    # ── Serialization ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "prior_alpha": self.prior_alpha,
            "prior_beta": self.prior_beta,
            "evidence_count": self.evidence_count,
            "last_updated": self.last_updated,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> BeliefUnit:
        return cls(
            alpha=d.get("alpha", 1),
            beta=d.get("beta", 1),
            prior_alpha=d.get("prior_alpha", 1),
            prior_beta=d.get("prior_beta", 1),
            evidence_count=d.get("evidence_count", 0),
            last_updated=d.get("last_updated", ""),
            source=d.get("source", ""),
        )


# ── Bayesian updater ────────────────────────


def bayesian_updater(
    current: Optional[BeliefUnit],
    evidence: Dict[str, Any],
    prior_strength: float = 1.0,
) -> BeliefUnit:
    """Generic Bayesian update function. 

    Args:
        current: Current Belief (None creates new) 
        evidence: Evidence dict, must contain one of: 
            - {"success": bool}                       Single outcome
            - {"successes": int, "total": int}         Batch
            - {"success_rate": float, "count": int}   Ratio + count
            - {"belief": BeliefUnit}                   Merge with another Belief
        prior_strength: Prior strength (0~2, 1=standard) 

    Returns:
        Updated BeliefUnit
    """
    from .calibration import get_calibration
    cal = get_calibration()

    if current is None:
        default_a = cal.get("belief", "default_prior_alpha", default=1)
        default_b = cal.get("belief", "default_prior_beta", default=1)
        current = BeliefUnit(alpha=default_a, beta=default_b,
                             prior_alpha=default_a, prior_beta=default_b)

    # Choose update method based on evidence type
    if "success" in evidence:
        current.record_outcome(evidence["success"])
    elif "successes" in evidence and "total" in evidence:
        current.record_batch(evidence["successes"], evidence["total"])
    elif "success_rate" in evidence and "count" in evidence:
        sr = evidence["success_rate"]
        cnt = evidence["count"]
        succ = int(round(sr * cnt))
        current.record_batch(succ, cnt)
    elif "belief" in evidence and isinstance(evidence["belief"], BeliefUnit):
        current.merge_with(evidence["belief"])

    # Optional: tag source
    if "source" in evidence:
        current.source = evidence["source"]

    # Update timestamp
    current.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return current


# ── Belief pool (global management) ───────────────────


class BeliefPool:
    """Belief pool — manages multiple BeliefUnit creation/query/persistence. 

    Indexed by key (e.g. skill_name, knowledge_topic). 
    Persisted to ~/.metacore/belief/belief_pool.json
    """

    def __init__(self, project_dir: Optional[str] = None):
        from ..utils import get_data_dir
        self._project_dir = Path(project_dir) if project_dir else get_data_dir()
        self._units: Dict[str, BeliefUnit] = {}
        self._load()

    # ── Core operations ──

    def get(self, key: str) -> Optional[BeliefUnit]:
        return self._units.get(key)

    def get_or_create(self, key: str, prior_a: int = 1, prior_b: int = 1) -> BeliefUnit:
        if key not in self._units:
            self._units[key] = BeliefUnit(alpha=prior_a, beta=prior_b,
                                          prior_alpha=prior_a, prior_beta=prior_b)
        return self._units[key]

    def update(self, key: str, evidence: Dict[str, Any]) -> BeliefUnit:
        current = self.get(key)
        updated = bayesian_updater(current, evidence)
        self._units[key] = updated
        self._save()
        return updated

    def record_outcome(self, key: str, success: bool) -> BeliefUnit:
        return self.update(key, {"success": success, "source": "skill_exec"})

    def record_batch(self, key: str, successes: int, total: int) -> BeliefUnit:
        return self.update(key, {"successes": successes, "total": total, "source": "skill_exec"})

    def record_learner_cycle(self, topic: str, entries_created: int, cycles: int) -> BeliefUnit:
        """Record a learner cycle result into the belief pool.

        Args:
            topic: Learning direction topic.
            entries_created: Number of knowledge entries created this cycle.
            cycles: Total cycles completed for this direction.

        Returns:
            Updated BeliefUnit.
        """
        key = f"learner:{topic}"
        successes = entries_created
        total = max(cycles, 1)
        return self.update(key, {
            "successes": successes,
            "total": total,
            "source": "learner_cycle",
        })

    def remove(self, key: str) -> bool:
        if key in self._units:
            del self._units[key]
            self._save()
            return True
        return False

    def list_keys(self) -> List[str]:
        return list(self._units.keys())

    def decay(self, key: str, factor: float = 0.95) -> Optional[BeliefUnit]:
        """Apply forgetting curve decay to a belief.

        Reduces evidence count and adjusts alpha/beta to simulate
        fading memory over time. Called by cognition_tick() for
        beliefs not updated in 7+ days.

        Args:
            key: Belief key to decay.
            factor: Decay multiplier (0.95 = 5% decay per call).

        Returns:
            Updated BeliefUnit, or None if key not found.
        """
        unit = self._units.get(key)
        if not unit:
            return None
        old_ev = unit.evidence_count
        new_ev = max(1, int(old_ev * factor))
        if new_ev < old_ev:
            # Reduce evidence: scale alpha/beta proportionally
            ratio = new_ev / old_ev if old_ev > 0 else 1.0
            unit.alpha = max(1, int(unit.alpha * ratio))
            unit.beta = max(1, int(unit.beta * ratio))
            unit.evidence_count = new_ev
            self._save()
        return unit

    def get_stats(self) -> Dict[str, Any]:
        if not self._units:
            return {"count": 0, "high_confidence": 0, "low_confidence": 0}
        high = sum(1 for u in self._units.values() if u.get_confidence() >= 0.80)
        low = sum(1 for u in self._units.values() if u.get_confidence() < 0.30)
        return {
            "count": len(self._units),
            "high_confidence": high,
            "low_confidence": low,
            "total_evidence": sum(u.evidence_count for u in self._units.values()),
        }

    # ── Persistence ──

    def _file_path(self) -> str:
        """Path to the belief pool JSON file."""
        return str(self._project_dir / "belief" / "belief_pool.json")

    def _load(self) -> None:
        fp = self._file_path()
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in data.items():
                self._units[key] = BeliefUnit.from_dict(val)
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # non-critical, continue

    def _save(self) -> None:
        fp = Path(self._file_path())
        fp.parent.mkdir(parents=True, exist_ok=True)
        data = {k: u.to_dict() for k, u in self._units.items()}
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


__all__ = [
    "BeliefUnit",
    "bayesian_updater",
    "BeliefPool",
]
