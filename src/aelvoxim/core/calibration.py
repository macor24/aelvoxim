"""aelvoxim.core.calibration — Global calibration parameter center 

All hardcoded thresholds centralized, dynamically tunable, persistent, auto-tunable. 

Architecture: 
- Nested dicts grouped by module storing all params 
- Read from file first, use defaults if file missing 
- Auto-persist to ~/.metacore/metacog/calibration.json 
- Batch update, single-param update, version tracking supported 
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

from ..utils import get_data_dir


import logging
_log = logging.getLogger("aelvoxim.core.calibration")

# ── Default params (complete list, safe to modify values but don't delete keys) ──

DEFAULT_CALIBRATION: Dict[str, Any] = {

    # ── MetaCogTrigger SignalThresholds ──
    "metacog": {
        # Success rate (1d)
        "success_rate_1d_critical": 0.50,       # <50% -> CRITICAL
        "success_rate_3d_moderate": 0.70,       # <70% -> MODERATE
        "success_rate_7d_warning": 0.85,        # <85% -> Warning
        # Stagnation
        "stagnation_days_trigger": 3,            # >=3 days no improvement -> Trigger
        "stagnation_days_moderate": 14,          # >=14 days -> MODERATE
        "stagnation_score_rate": 14.0,           # score = min(1, days / N)
        # Repeat failure
        "repeat_failure_trigger": 1,             # >0 -> Trigger
        "repeat_failure_moderate": 3,            # >=3 -> MODERATE
        "repeat_failure_critical": 5,            # >=5 -> CRITICAL
        "repeat_failure_score_rate": 5.0,        # score = min(1, count / N)
        # External signal
        "external_signal_trigger": 0.3,          # >0.3 -> Trigger
        # Introspection
        "introspection_trigger_days": 7,         # >=7 days -> Trigger
        "introspection_score_rate": 10.0,        # score = min(1, days / N)
        # Memory health
        "memory_semantic_max_pct": 0.80,         # semantic layer >80% -> Warning
        "memory_working_min_pct": 0.01,          # working memory <1% -> Warning
        "memory_importance_inflate_pct": 0.40,   # high importance >40% -> Warning
        "memory_importance_inflate_critical": 0.70,  # >70% -> MODERATE
        "memory_backup_max_count": 20,           # backup files >20 -> Warning
        "memory_health_min_score": 0.30,         # memory health score >0.3 triggers
        # Snapshot trend
        "snapshot_trend_trigger": 2,             # consecutive decline >=2 -> Trigger
        "snapshot_trend_moderate": 3,            # >=3 -> MODERATE
        "snapshot_trend_critical": 5,            # >=5 -> CRITICAL
        "snapshot_trend_score_rate": 3.0,        # score = min(1, streak / N)
        # Belief health
        "belief_unhealthy_threshold": 0.6,       # <0.6 -> UNHEALTHY
        "belief_critical_threshold": 0.3,        # <0.3 -> CRITICAL
        # Trigger weights (8 signals)
        "trigger_weights": {
            "success_rate": 0.20,
            "stagnation": 0.15,
            "repeat_failure": 0.15,
            "external_signal": 0.05,
            "introspection": 0.05,
            "memory_health": 0.15,
            "snapshot_trend": 0.10,
            "belief_health": 0.15,
        },
        "evolve_threshold": 0.15,                # overall_score >= 0.15 triggers evolution
    },

    # ── Judge scoring args ──
    "judge": {
        "grade_thresholds": [                     # [min_score, grade]
            [0.85, "S"],
            [0.70, "A"],
            [0.50, "B"],
            [0.40, "C"],
        ],
        "requires_approval": {                    # which grades require human approval
            "S": False,
            "A": False,
            "B": False,
            "C": True,
            "D": True,
        },
        "weights": {
            "direction": 0.20,
            "efficiency": 0.15,
            "robustness": 0.15,
            "reusability": 0.10,
            "cost_risk": 0.15,
            "prediction_confidence": 0.25,
        },
        # Per-dimension rule args
        "direction_rsi_score": 0.8,              # direction score when RSI-aligned
        "direction_non_rsi_score": 0.3,          # direction score when non-RSI
        "direction_delete_multiplier": 0.5,      # delete operation multiplier
        "efficiency_step_cost": 0.1,             # score penalty per step
        "efficiency_min_score": 0.1,             # minimum efficiency score
        "efficiency_delete_multiplier": 0.5,
        "robustness_fail_cost": 0.2,             # score penalty per failure
        "robustness_min_score": 0.2,
        "reusability_create": 0.8,
        "reusability_update": 0.6,
        "reusability_other": 0.3,
        "cost_token_rate": 10000,                # score penalty per N tokens
        "cost_min_score": 0.1,
        "prediction_confidence_weight": 0.7,     # inference confidence weight
        "prediction_risk_weight": 0.3,
        "prediction_no_data": 0.5,               # default score when no inference data
        "prediction_max_score": 0.95,
    },

    # ── SelfModel 5D scoring params ──
    "selfmodel": {
        "grade_thresholds": [                     # overall grade thresholds
            [0.90, "S"],
            [0.75, "A"],
            [0.55, "B"],
            [0.30, "C"],
        ],
        "weights": {
            "direction": 0.25,
            "efficiency": 0.20,
            "robustness": 0.20,
            "reusability": 0.15,
            "cost_risk": 0.20,
        },
        # Direction
        "direction_base_boost": 10.0,            # score = min(rate*100 + N, 100)
        "direction_max": 100.0,
        # Efficiency
        "efficiency_task_rate": 5.0,             # score = min(tasks * N, 100)
        "efficiency_max": 100.0,
        # Robustness
        "robustness_sample_weight": 0.40,
        "robustness_volatility_weight": 0.30,
        "robustness_trend_weight": 0.30,
        "robustness_default_score": 50.0,
        "robustness_max": 100.0,
        # Reusability
        "reusability_type_rate": 12.0,           # score = min(type_count * N, 100)
        "reusability_max": 100.0,
        # Cost & risk
        "cost_risk_max": 100.0,
    },

    # ── BeliefEngine (Bayesian foundation) params ──
    "belief": {
        "default_prior_alpha": 1,           # uninformed prior
        "default_prior_beta": 1,
        "weak_prior_alpha": 0.5,            # weak prior (fast convergence with little data)
        "weak_prior_beta": 0.5,
        "strong_prior_alpha": 5,            # strong prior (needs lots of evidence to overturn)
        "strong_prior_beta": 5,
        "confidence_threshold": 0.80,       # >80% considered reliable
        "uncertainty_low": 0.20,            # uncertainty <0.20 = high confidence
        "uncertainty_high": 0.50,           # uncertainty >0.50 = low confidence
        "evidence_decay_half_life_days": 90, # prior decay half-life
        "max_batch_size": 1000,
    },

    # ── MetaEVOLVE self-calibration params ──
    "metaevolve": {
        "min_records_for_stat": 3,
        "hit_rate_threshold": 0.5,               # <50% -> suggest raising threshold
        "judge_error_threshold": 0.2,            # >20% -> suggest calibration
        "stale_days": 30,
        "auto_tune_enabled": True,
        # Tuning step size
        "threshold_tune_step": 0.1,              # adjustment per tick
        "threshold_max": 0.9,                    # max threshold
        "consecutive_miss_limit": 3,             # pause after N consecutive misses
    },

    # ── DGM-H gate args ──
    "dgmh": {
        "judge_order": {
            "S": 5, "A": 4, "B": 3, "C": 2, "D": 1,
        },
        "evolve_gates": {
            "create": {"min_judge": "B", "level": 1},
            "update": {"min_judge": "A", "level": 2},
            "meta":   {"min_judge": "S", "level": 3},
        },
        "m6_max_per_cycle": 3,                   # max 3 modifications per cycle
    },

    # ── Fusion memory layer ──
    "fusion": {
        "layer_priority": {
            "procedural": 1.5,
            "semantic": 1.3,
            "episodic": 1.0,
            "working": 0.8
        },
        "importance_weight": 0.3,
        "hit_weight": 0.7
    },

    # ── Meta-learning ──
    "meta_learn": {
        "min_interval": 600,
        "correction_confidence": 0.7,
        "repeat_confidence": 0.5,
        "negative_anchor_confidence": 0.15,
        "max_feedback_batch": 50
    },

    # Metadata
    "_meta": {
        "version": "1.0.0",
        "created_at": "",
        "last_updated": "",
        "auto_tune_count": 0,
    },
}


class Calibration:
    """Global calibration parameter center. 

    Usage: 
        cal = Calibration()
        threshold = cal.get("metacog", "success_rate_1d_critical")
        cal.set("judge", "weights", new_weights)
        cal.save()
    """

    def __init__(self, project_dir: Optional[Path] = None):
        self._project_dir = project_dir if project_dir else get_data_dir()
        self._data: Dict[str, Any] = deepcopy(DEFAULT_CALIBRATION)
        self._load()

    # ── Read ──

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get value by key chain. cal.get('metacog', 'success_rate_1d_critical')"""
        d = self._data
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    def get_all(self) -> Dict:
        """Return full calibration data (copy, prevents accidental modification)."""
        return deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, {})

    # ── Write ──

    def set(self, *args, value: Any) -> None:
        """Set value. cal.set('metacog', 'success_rate_1d_critical', value=0.6)"""
        keys = list(args)
        if len(keys) < 1:
            return
        d = self._data
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self._touch()

    def set_batch(self, path_prefix: str, updates: Dict[str, Any]) -> int:
        """Batch set args under same path prefix. Returns number of changes."""
        count = 0
        for key, value in updates.items():
            full_key = f"{path_prefix}.{key}"
            parts = full_key.split(".")
            d = self._data
            for p in parts[:-1]:
                if p not in d or not isinstance(d[p], dict):
                    d[p] = {}
                d = d[p]
            if d.get(parts[-1]) != value:
                d[parts[-1]] = value
                count += 1
        if count > 0:
            self._touch()
            self._save()
        return count

    def reset_to_defaults(self) -> None:
        """Reset to default values."""
        self._data = deepcopy(DEFAULT_CALIBRATION)
        self._touch()
        self._save()

    # ── Persistence ──

    def _get_file(self) -> Path:
        return self._project_dir / "calibration.json"

    def _load(self) -> None:
        fp = self._get_file()
        if not fp.exists():
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Recursively merge saved values into defaults
            # so newly added keys in defaults won't be lost
            self._deep_merge(self._data, saved)
        except Exception:
            pass  # non-critical, continue

    def save(self) -> None:
        """Explicitly save to file."""
        self._touch()
        self._save()

    def _save(self) -> None:
        fp = self._get_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _touch(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if "_meta" not in self._data:
            self._data["_meta"] = {}
        if not self._data["_meta"].get("created_at"):
            self._data["_meta"]["created_at"] = now
        self._data["_meta"]["last_updated"] = now

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> None:
        """Recursive merge: override replaces base values, new keys keep defaults."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Calibration._deep_merge(base[key], value)
            elif key in base:
                # Only overwrite keys that exist in base (prevents injection of new keys)
                base[key] = value
            # New keys in override are silently ignored (keep defaults)

    # ── Convenience methods ──

    def auto_tune(self, metaevolve_analysis: Dict) -> List[Dict]:
        """Auto-tune parameters based on MetaEVOLVE analysis results.

        Args:
            metaevolve_analysis: MetaEVOLVE.generate_strategy_advice().to_dict()

        Returns:
            changes: List of applied changes

        Edition gate: community edition disables automatic tuning.
        """
        # Edition gate
        try:
            from ..server.edition import get as _ed_get
            if not _ed_get("auto_tune_enabled", False):
                return []
        except ImportError:
            _log.exception("calibration error")

        if not self._data.get("metaevolve", {}).get("auto_tune_enabled", True):
            return []

        me = self._data.get("metaevolve", {})
        mc = self._data.get("metacog", {})
        jg = self._data.get("judge", {})

        changes: List[Dict] = []

        # 0. Self-check: if evolve_threshold drifted unreasonably high, reset
        _ev = mc.get("evolve_threshold", 0.10)
        if _ev > 0.5:
            mc["evolve_threshold"] = 0.10
            changes.append({
                "target": "metacog.evolve_threshold",
                "old": _ev, "new": 0.10,
                "reason": f"Auto-reset: drifted to {_ev}, reset to default 0.10",
            })

        # 1. If hit rate is too low, raise metacog evolve_threshold
        hit_rate = metaevolve_analysis.get("hit_rate", 0.5) or 0
        if hit_rate <= me.get("hit_rate_threshold", 0.5):
            old = mc.get("evolve_threshold", 0.10)
            step = me.get("threshold_tune_step", 0.1)
            new_val = min(me.get("threshold_max", 0.9), old + step)
            mc["evolve_threshold"] = new_val
            changes.append({
                "target": "metacog.evolve_threshold",
                "old": old, "new": new_val,
                "reason": f"Hit rate {hit_rate:.0%}<{me.get('hit_rate_threshold', 0.5):.0%}",
            })

        # 2. If Judge error rate is high, reduce prediction_confidence weight
        judge_error = metaevolve_analysis.get("judge_error_rate", 0.0) or 0
        if judge_error > me.get("judge_error_threshold", 0.2):
            jw = jg.setdefault("weights", {})
            old_pred = jw.get("prediction_confidence", 0.7)
            new_pred = max(0.15, old_pred - 0.05)
            jw["prediction_confidence"] = new_pred
            changes.append({
                "target": "judge.weights.prediction_confidence",
                "old": old_pred, "new": new_pred,
                "reason": f"Judge error {judge_error:.0%}>{me.get('judge_error_threshold', 0.2):.0%}",
            })
            # Compensate: increase direction weight proportionally
            old_dir = jw.get("direction", 0.2)
            jw["direction"] = round(old_dir + 0.03, 3)
            changes.append({
                "target": "judge.weights.direction",
                "old": old_dir, "new": jw["direction"],
                "reason": "Compensate direction weight",
            })

        # 3. If a specific change type has low hit rate, raise its Judge threshold
        by_type = metaevolve_analysis.get("by_type", {})
        for ctype, stats in by_type.items():
            if isinstance(stats, dict) and stats.get("total", 0) >= 3:
                hit = stats.get("hit_rate", 1.0) or 1.0
                if hit < 0.3:
                    key = f"reusability_{ctype}"
                    if key in jg:
                        old = jg[key]
                        jg[key] = max(0.1, old - 0.1)
                        changes.append({
                            "target": f"judge.{key}",
                            "old": old, "new": jg[key],
                            "reason": f"{ctype} hit rate {hit:.0%}<30%",
                        })

        if changes:
            self._data["_meta"]["auto_tune_count"] = self._data["_meta"].get("auto_tune_count", 0) + 1
            self._touch()
            self._save()

        return changes

    def get_snapshot(self) -> Dict:
        """Get calibration snapshot (for SelfModel recording)."""
        return {
            "version": self._data.get("_meta", {}).get("version", ""),
            "last_updated": self._data.get("_meta", {}).get("last_updated", ""),
            "auto_tune_count": self._data.get("_meta", {}).get("auto_tune_count", 0),
            "metacog_evolve_threshold": self._data.get("metacog", {}).get("evolve_threshold", 0.15),
            "judge_weights": self._data.get("judge", {}).get("weights", {}),
            "hit_rate_threshold": self._data.get("metaevolve", {}).get("hit_rate_threshold", 0.5),
        }


# Global singleton (lazy load)
_CALIBRATION: Optional[Calibration] = None


def get_calibration() -> Calibration:
    """Get global calibration instance (singleton)."""
    global _CALIBRATION
    if _CALIBRATION is None:
        _CALIBRATION = Calibration()
    return _CALIBRATION


def reset_calibration() -> None:
    """Reset global calibration instance (for testing)."""
    global _CALIBRATION
    _CALIBRATION = None


__all__ = [
    "Calibration", "get_calibration", "reset_calibration",
    "DEFAULT_CALIBRATION",
]
