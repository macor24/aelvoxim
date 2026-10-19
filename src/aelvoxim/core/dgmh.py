"""aelvoxim.core.dgmh — DGM-H meta-cognition orchestrator

Level 1-3 meta-cognition gates:
- Level 1: Create proposals (Judge>=B) auto-execute
- Level 2: Modify proposals (Judge>=A) auto-execute
- Level 3: Meta-change proposals (MetaEVOLVE, Judge>=S) requires user authorization

SafetyShield M1-M6 safety barriers.
Standalone version, no external dependencies.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR

# ── 2026-10-19：闸门配套（冷却 / ungraded 队列 / 批量过闸）────────────
COOLDOWN_HOURS = 24.0


def _cooldown_path() -> Path:
    return Path(METACORE_DIR) / "dgmh_cooldown.json"


def _ungraded_path() -> Path:
    return Path(METACORE_DIR) / "dgmh_ungraded.jsonl"


def _load_cooldown() -> dict:
    try:
        return json.loads(_cooldown_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def in_cooldown(target: str, hours: float = COOLDOWN_HOURS) -> bool:
    """同一 target 上次未通过后，冷却期内不再重试（消掉重复拦截）。"""
    if not target:
        return False
    ts = (_load_cooldown() or {}).get(target)
    try:
        return bool(ts) and (time.time() - float(ts)) < hours * 3600
    except Exception:
        return False


def mark_blocked(target: str) -> None:
    if not target:
        return
    data = _load_cooldown()
    data[target] = time.time()
    try:
        p = _cooldown_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def queue_ungraded(change: dict, reason: str = "") -> None:
    """没有判分器的改动进人工审队列 —— 不再伪造一个 B 级评分。"""
    try:
        p = _ungraded_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "target": change.get("target"), "old": change.get("old"),
               "new": change.get("new"), "reason": change.get("reason", ""),
               "why_ungraded": reason}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


ACTIVATION_FILE = METACORE_DIR / "dgmh_activation.json"
DECISIONS_FILE = METACORE_DIR / "dgmh_decisions.jsonl"


def load_activation() -> "ActivationStatus":
    """从 dgmh_activation.json 读激活等级；文件不存在 ⇒ 默认（Level 1）。

    2026-10-19：以前 gate_changes 每次 new 一个默认 ActivationStatus()，
    judgestored 恒为 False ⇒ update（Level 2）结构上永不可达，判分再好也拦。
    """
    try:
        d = json.loads(ACTIVATION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ActivationStatus()
    return ActivationStatus(
        phase1_ready=bool(d.get("phase1_ready", True)),
        phase2_ready=bool(d.get("phase2_ready", False)),
        phase3_ready=bool(d.get("phase3_ready", False)),
        judgestored=bool(d.get("judgestored", False)),
        user_authorized=bool(d.get("user_authorized", False)),
        safetyguard_stable=bool(d.get("safetyguard_stable", False)),
        success_trails_ready=bool(d.get("success_trails_ready", False)),
    )


def record_decision(kind: str, target: str, new_value, grade: str = "A",
                    by: str = "", why: str = "", decision_id: str = "",
                    ttl_hours: float = 24.0) -> str:
    """追加一条人工放行/否决记录（一次性令牌，24h 过期）。返回 id。"""
    import uuid
    did = decision_id or uuid.uuid4().hex[:12]
    rec = {
        "type": kind, "id": did, "target": target,
        "new": new_value, "grade": grade, "by": by, "why": why,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ts_epoch": time.time(), "ttl_hours": ttl_hours,
    }
    with DECISIONS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return did


def find_decision(target: str, new_value) -> Optional[str]:
    """查可用的放行令牌：一次性、24h 内、target 与 new 都要吻合。"""
    if not DECISIONS_FILE.exists():
        return None
    now = time.time()
    approves, consumed = {}, set()
    for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "approve":
            approves[d.get("id")] = d
        elif d.get("type") in ("consume", "reject"):
            consumed.add(d.get("id"))
    for did, d in approves.items():
        if did in consumed or str(d.get("target")) != target:
            continue
        try:
            if abs(float(d.get("new")) - float(new_value)) > 1e-9:
                continue
        except (TypeError, ValueError):
            continue
        ttl = float(d.get("ttl_hours", 24.0)) * 3600
        if (now - float(d.get("ts_epoch", 0))) > ttl:
            continue
        return str(d.get("grade") or "A")
    return None


def consume_decision(target: str, new_value) -> None:
    """标记令牌已用（一次性）。"""
    if not DECISIONS_FILE.exists():
        return
    for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "approve" and str(d.get("target")) == target:
            try:
                if abs(float(d.get("new")) - float(new_value)) <= 1e-9:
                    record_decision("consume", target, new_value, decision_id=d["id"],
                                    why="applied")
                    return
            except (TypeError, ValueError):
                continue


def gate_changes(changes, log=None, cooldown_hours: float = COOLDOWN_HOURS,
                 activation: Optional["ActivationStatus"] = None,
                 belief_health: Optional[Dict[str, Any]] = None):
    """逐条过闸门。返回 (passed, stats)。

    - 冷却期内：跳过（不重复报告）
    - 没有判分器（change 里没有 judge_grade）：进 ungraded 队列并冷却，**不默认 B**
    - 有判分但不达标：记 blocked 并冷却
    """
    passed: List[Dict] = []
    stats = {"blocked": 0, "cooled": 0, "ungraded": 0}
    for c in changes or []:
        target = str(c.get("target") or "")
        if in_cooldown(target, cooldown_hours):
            stats["cooled"] += 1
            continue
        judge = c.get("judge_grade") or find_decision(target, c.get("new"))
        if not judge:
            queue_ungraded(c, "no judge available")
            mark_blocked(target)
            stats["ungraded"] += 1
            if log:
                log(f"  📝 ungraded: {target} (no judge) → 人工审队列")
            continue
        ok, reason = check_gate("update", judge,
                                activation if activation is not None else load_activation(),
                                belief_health=belief_health)
        if not ok:
            stats["blocked"] += 1
            mark_blocked(target)
            if log:
                log(f"  🛡️ DGM-H blocked: {target} ({reason})")
            continue
        consume_decision(target, c.get("new"))
        passed.append(c)
    return passed, stats


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
        # M1: Do not modify safety rules — unconditional red line. The old
        # `level <= 1` guard only blocked the least-strict level, so higher
        # (stricter) levels actually allowed safety-rule modification — the
        # condition was inverted (P3, 9.txt audit).
        if "safety_guard" in target or "safety-guard" in target or "safety" in target.lower():
            return f"[M1] safety rule modification blocked: {target}"

        # M3: Do not escalate permissions
        if action in ("chmod", "sudo", "chown", "adduser", "usermod"):
            return "[M3] permission escalation not allowed: {action}"

        # M5: No new rules allowed
        if action in ("create_safety_rule", "new_rule"):
            if self.level >= 5:
                return "[M5] new rule creation blocked"

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


def check_gate(change_type: str, judge_grade: Optional[str], activation: ActivationStatus,
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

    # 2026-10-19：没有判分器时不再伪造 B 去撞 A 的门
    if not judge_grade or judge_grade not in JUDGE_ORDER:
        return (False, "ungraded: no judge available")

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

    def check_proposal_gate(self, change_type: str, judge_grade: Optional[str]) -> Tuple[bool, str]:
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
