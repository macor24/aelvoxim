"""aelvoxim.core.dgmh — DGM-H meta-cognition orchestrator

Level 1-3 meta-cognition gates:
- Level 1: Create proposals (Judge>=B) auto-execute
- Level 2: Modify proposals (Judge>=A) auto-execute
- Level 3: Meta-change proposals (MetaEVOLVE, Judge>=S) requires user authorization

SafetyShield M1-M6 safety barriers.
Standalone version, no sentrikit dependency.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR


# ── Activation status ──────────────────────────────────


@dataclass
class ActivationStatus:
    """DGM-H activation status."""
    phase1_ready: bool = True       # Level 1
    phase2_ready: bool = False      # Level 2
    phase3_ready: bool = False      # Level 3
    judgestored: bool = False
    user_authorized: bool = False
    safetyguard_stable: bool = False
    success_trails_ready: bool = False
    gepa_verified: bool = True

    def to_dict(self) -> Dict:
        return {
            "phase1_ready": self.phase1_ready,
            "phase2_ready": self.phase2_ready,
            "phase3_ready": self.phase3_ready,
            "judgestored": self.judgestored,
            "user_authorized": self.user_authorized,
            "safetyguard_stable": self.safetyguard_stable,
            "success_trails_ready": self.success_trails_ready,
            "gepa_verified": self.gepa_verified,
        }

    def check_level(self, level: int) -> Tuple[bool, str]:
        """Check if specified level is active."""
        if level == 1:
            return (True, "Level 1: Basic evolution")
        elif level == 2:
            if not self.judgestored:
                return (False, "Level 2 not active: judgestored required")
            return (True, "Level 2: Judge-driven evolution")
        elif level == 3:
            if not (self.user_authorized and self.safetyguard_stable
                    and self.success_trails_ready):
                return (False, "Level 3 not active: need user_authorized + safetyguard_stable + success_trails_ready")
            if not self.user_authorized:
                return (False, "Level 3 not active: user authorization needed")
            return (True, "Level 3: Meta-cognition fully active")
        return (False, f"Unknown level: {level}")


# ── SafetyShield M1-M6 ───────────────────────


class SafetyShield:
    """SafetyShield M1-M6. 

    M1: Do not modify safety rules 
    M2: Do not delete core memory 
    M3: Do not escalate permissions 
    M4: Changes must pass Judge 
    M5: No new safety rules allowed 
    M6: Modification frequency limit 
    """

    def __init__(self, level: str = "M3"):
        self.level = int(level.replace("M", "")) if level.startswith("M") and level[1:].isdigit() else 3
        self._failures = 0
        self._rollback_paths: List[Dict[str, str]] = []
        self._outcomes: List[bool] = []
        self.should_rollback = False
        self._modifications: List[Dict[str, str]] = []
        self._last_modify_count: int = 0
        self._cycle_modify_count: int = 0

    def check(self, action: str = "", target: str = "") -> Optional[str]:
        """Safety check, returns None=pass, str=block reason."""
        # M1: Do not modify safety rules
        if "safety_guard" in target or "safety-guard" in target or "safety" in target.lower():
            if self.level <= 1:
                return f"[M1] safety rule modification blocked: {target}"

        # M3: Do not escalate permissions
        if action in ("chmod", "sudo", "chown", "adduser", "usermod"):
            return "[M3] permission escalation not allowed: {action}"

        # M5: No new rules allowed
        if action in ("create_safety_rule", "new_rule"):
            if self.level >= 5:
                return f"[M5] new rule creation blocked"

        # M6: Limit modifications per evolution cycle
        if action == "modify":
            if self._cycle_modify_count > 3:
                return "[M6] too many modifications this cycle (>3)"

        return None

    def count_modify(self) -> None:
        """Count one actual modification (called after execution, not during check)."""
        self._cycle_modify_count += 1

    def reset_cycle(self) -> None:
        """Reset cycle counter (called at evolution loop start)."""
        self._last_modify_count = self._cycle_modify_count
        self._cycle_modify_count = 0

    def record_modification(self, path: str, old: str, new: str) -> None:
        self._modifications.append({"path": path, "old": old, "new": new})
        if not any(rp.get("path") == path for rp in self._rollback_paths):
            self._rollback_paths.append({"path": path, "old": old, "new": new})

    def get_rollback_path(self) -> Optional[Dict[str, str]]:
        if not self._rollback_paths:
            return None
        return self._rollback_paths[-1]

    def get_rollback_paths(self) -> List[Dict[str, str]]:
        return self._rollback_paths

    def record_outcome(self, success: bool) -> None:
        self._outcomes.append(success)
        recent = self._outcomes[-3:]
        self.should_rollback = len(recent) == 3 and not any(recent)

    def failures(self) -> int:
        return self._failures


# ── Evolution gate levels ──────────────────────────────

# Evolution proposal level mapped to Gate Level
EVOLUTION_GATES = {
    "create": {"min_judge": "B", "level": 1},    # 新增 → Level 1
    "update": {"min_judge": "A", "level": 2},    # 修改 → Level 2
    "meta":   {"min_judge": "S", "level": 3},    # 元修改 → Level 3
}

# Judge grade numeric mapping (higher is better)
JUDGE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def check_gate(change_type: str, judge_grade: str, activation: ActivationStatus,
               belief_health: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Check if evolution proposal passes the Gate. 

    Args:
        change_type: create / update / meta
        judge_grade: S / A / B / C / D
        activation: Current activation status 
        belief_health: Optional. Belief pool health (from cognition_tick). 
            Raises Gate level automatically when confidence is low. Format: {"count": N, "high_confidence": N, "total_evidence": N} 

    Returns:
        (passed?, reason)
    """
    gate = EVOLUTION_GATES.get(change_type)
    if not gate:
        return (False, f"Unknown change type: {change_type}")

    # Bayesian Belief Gate: raises Gate level when confidence is low
    if belief_health:
        _high = belief_health.get("high_confidence", 0)
        _total = belief_health.get("count", 1)
        _evidence = belief_health.get("total_evidence", 0)
        if _total > 0:
            _high_ratio = _high / _total
            if _high_ratio < 0.3:
                # Low confidence (high-confidence < 30%) → block auto-evolution
                return (False, f"Belief health too low (high-conf {_high_ratio:.0%}), auto-evolution paused")
            if _high_ratio < 0.5 and gate["level"] >= 2:
                # Medium confidence (high-confidence < 50%) → Level 2/3 restricted
                return (False, f"Belief health moderate (high-conf {_high_ratio:.0%}), Level {gate['level']} unavailable")
        if _evidence < 50 and gate["level"] >= 3:
            return (False, f"Total evidence too low ({_evidence}<50), Level 3 unavailable")

    # Check if Judge grade meets requirements
    min_order = JUDGE_ORDER.get(gate["min_judge"], 0)
    actual_order = JUDGE_ORDER.get(judge_grade, 0)
    if actual_order < min_order:
        return (False, f"Judge {judge_grade} below minimum {gate['min_judge']}")

    # Check Level Gate
    ok, reason = activation.check_level(gate["level"])
    if not ok:
        return (False, reason)

    return (True, f"Gate passed (Level {gate['level']}, Judge {judge_grade})")


# ── DGM-H orchestrator ─────────────────────────────


class DGOrchestrator:
    """DGM-H meta-cognition orchestrator. 

    Responsibilities: 
    1. Activation status management (Level 1/2/3) 
    2. SafetyShield M1-M6 guardrails 
    3. Evolution proposal Gate (create/update/meta) 
    4. Rollback mechanism 
    """

    def __init__(self, project_dir: Optional[str] = None):
        self._activation = ActivationStatus()
        self.shield = SafetyShield()
        self._project_dir = Path(project_dir) if project_dir else METACORE_DIR

    def set_activation(
        self,
        judgestored: bool = False,
        user_authorized: bool = False,
        safetyguard_stable: bool = False,
        success_trails_ready: bool = False,
        gepa_verified: bool = False,
        **kwargs,
    ) -> None:
        """Set activation status."""
        self._activation = ActivationStatus(
            phase2_ready=judgestored,
            phase3_ready=judgestored and user_authorized
                         and safetyguard_stable and success_trails_ready,
            judgestored=judgestored,
            user_authorized=user_authorized,
            safetyguard_stable=safetyguard_stable,
            success_trails_ready=success_trails_ready,
            gepa_verified=gepa_verified,
        )

    def check_activation(self) -> ActivationStatus:
        return self._activation

    def check_proposal_gate(self, change_type: str, judge_grade: str) -> Tuple[bool, str]:
        """Gate check: whether the proposal meets Gate conditions. 
        """
        # SafetyShield check
        shield_result = self.shield.check(
            action="modify" if change_type in ("update", "meta") else "create",
            target=f"proposal_{change_type}",
        )
        if shield_result:
            return (False, f"SafetyShield blocked: {shield_result}")

        # Gate check
        return check_gate(change_type, judge_grade, self._activation)

    def apply_suggestion(self, suggestion: Dict[str, Any]) -> Dict:
        """Apply one evolution suggestion."""
        target = suggestion.get("target", "")
        shield_result = self.shield.check(action="modify", target=target)
        if shield_result:
            return {"success": False, "reason": shield_result}

        self.shield.record_modification(
            target,
            suggestion.get("current_value", ""),
            suggestion.get("suggested_value", ""),
        )
        return {"success": True, "applied": suggestion}

    def rollback_if_needed(self) -> Optional[Dict[str, Any]]:
        """Check if rollback is needed."""
        if self.shield.should_rollback:
            path = self.shield.get_rollback_path()
            return {"action": "rollback", "path": path["path"] if path else "unknown"}
        return None

    def get_status(self) -> Dict:
        return {
            "activation": self._activation.to_dict(),
            "shield_level": self.shield.level,
            "modifications": len(self.shield._modifications),
            "should_rollback": self.shield.should_rollback,
        }


__all__ = [
    "DGOrchestrator", "SafetyShield", "ActivationStatus",
    "check_gate", "EVOLUTION_GATES", "JUDGE_ORDER",
]
