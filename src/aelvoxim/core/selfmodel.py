"""aelvoxim.core.selfmodel — SelfModel

Self-awareness: capability profile + decision log + state snapshot + bottleneck analysis.
Hot-update capabilities (evolve without changing code, only strategy params).
Standalone, no sentrikit dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import get_data_dir

import logging
_log = logging.getLogger("aelvoxim.selfmodel")



@dataclass
class CapabilityScore:
    success_rate: float = 0.0
    task_count: int = 0
    avg_latency: str = ""
    last_success: str = ""
    # Beta distribution args (evidence accumulation)
    alpha: int = 2  # Success+1 (light regularization prior)
    beta: int = 2   # Failure+1 (light regularization prior)

    def __post_init__(self):
        """Sync success_rate with alpha/beta after init."""
        if self.task_count == 0 and self.alpha == 1 and self.beta == 1:
            # Default constructor — use passed success_rate (backward compatible)
            pass
        else:
            # Derive success_rate from alpha/beta
            self.success_rate = round(self.alpha / (self.alpha + self.beta), 2)

    @property
    def beta_mean(self) -> float:
        """Beta distribution mean = alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def beta_uncertainty(self) -> float:
        """Beta distribution uncertainty approximation.
        Higher for small samples, lower for large ones.
        """
        n = self.alpha + self.beta
        if n <= 2:
            return 1.0
        return round(1.0 / (n ** 0.5), 3)

    def record_outcome(self, success: bool) -> None:
        """Record one execution outcome and update Beta distribution."""
        self.task_count += 1
        if success:
            self.alpha += 1
        else:
            self.beta += 1
        # Keep backward compatible with old success_rate
        self.success_rate = round(self.beta_mean, 2)


@dataclass
class DecisionEntry:
    timestamp: str = ""
    decision_type: str = ""
    task: str = ""
    chosen: str = ""
    rejected: str = ""
    judge_score: Optional[float] = None
    outcome: Optional[str] = None


@dataclass
class SnapshotEntry:
    timestamp: str = ""
    overall_success_rate: float = 0.0
    judge_avg_score: float = 0.0
    semantic_count: int = 0
    procedural_active: int = 0
    error_rate_7d: float = 0.0
    pending_skills: int = 0
    audit_block_rate: float = 0.0
    load: float = 0.0
    # Extended fields for cross-time comparison
    knowledge_entries: int = 0       # KB entry count
    learner_cycles: int = 0          # Total learner cycles
    active_directions: int = 0       # Active learning directions
    hypotheses_verified: int = 0     # Verified hypotheses this cycle
    # Learning effectiveness metrics (Phase 1)
    knowledge_retention_rate: float = 0.0  # Fraction of knowledge entries referenced in last 30d
    direction_saturation_speed: float = 0.0  # Avg cycles per direction from new→saturated
    validation_pass_rate: float = 0.0  # Validation pass rate over time


@dataclass
class Goal:
    """An active improvement goal set by the AGI's motivation layer.

    Set by _set_active_goals() in Learner, tracked across cognition cycles.
    """
    id: str = ""
    description: str = ""            # "Raise belief health from 44% to 60%"
    category: str = ""               # belief_health / knowledge_quality / success_rate / stagnation
    target_value: float = 0.0        # 0.60
    current_value: float = 0.0       # 0.44
    status: str = "active"           # active / completed / failed / abandoned
    created_at: str = ""
    completed_at: str = ""
    progress_note: str = ""          # "Cleaned 5 low-confidence entries, +4% improvement"

    # Focus strategy: tells cognition_tick which operations to prioritise
    focus: str = "balanced"          # balanced / cleanup / validate / search
    skip_actions: list = field(default_factory=list)  # actions to skip (e.g. ["auto_tune"])
    _focus_cycles: int = 0           # cycles spent on current focus (internal counter)


class SelfGraph:
    """GPTSwarm-style self-graph structure."""

    DEFAULT_NODES: Dict[str, Dict] = {
        "BrainCore": {"type": "router", "capabilities": ["task_routing", "priority_scheduling"],
                      "health": "good", "load": 0.3},
        "Judge": {"type": "evaluator", "capabilities": ["score", "grade"],
                  "health": "good", "load": 0.2},
        "MetaCogTrigger": {"type": "monitor", "capabilities": ["detect_degradation"],
                           "health": "good", "load": 0.1},
        "SelfModel": {"type": "self_model", "capabilities": ["capability_tracking", "decision_log"],
                      "health": "good", "load": 0.1},
    }

    DEFAULT_EDGES = [
        ("BrainCore", "Judge", "proposal_input"),
        ("MetaCogTrigger", "BrainCore", "trigger_evolve"),
        ("BrainCore", "SelfModel", "record_decision"),
    ]

    def __init__(self, nodes: Optional[Dict] = None, edges: Optional[List] = None):
        self.nodes = nodes or dict(self.DEFAULT_NODES)
        self.edges = edges or list(self.DEFAULT_EDGES)

    def find_bottlenecks(self) -> List[str]:
        return [
            name for name, attrs in self.nodes.items()
            if attrs.get("load", 0) > 0.6 and attrs.get("health", "good") != "good"
        ]

    def update_node(self, name: str, **kwargs) -> None:
        if name in self.nodes:
            self.nodes[name].update(kwargs)

    def to_dict(self) -> Dict:
        return {"nodes": self.nodes, "edges": self.edges}


class SelfModel:
    """SelfModel。

    Answers three questions:
    - Current state? → snapshot()
    - Strengths? → capabilities()
    - Weaknesses? → limits()
    """

    def __init__(self, project_dir: Optional[Path] = None):
        self.project_dir = Path(project_dir) if project_dir else get_data_dir()
        self.graph = SelfGraph()
        self._capabilities: Dict[str, CapabilityScore] = {}
        self._decisions: List[DecisionEntry] = []
        self._snapshots: List[SnapshotEntry] = []
        self.weights: Dict[str, float] = {
            "direction": 0.25, "efficiency": 0.20,
            "robustness": 0.20, "reusability": 0.15, "cost_risk": 0.20,
        }
        self._load()

    # ── Capability profile ───────────────────────────────

    def capabilities(self) -> Dict[str, Dict]:
        return {tt: asdict(score) for tt, score in self._capabilities.items()}

    def update_capability(self, task_type: str, score: CapabilityScore) -> None:
        self._capabilities[task_type] = score
        self._save()

    def update_capabilities_from_history(self, history: List[Dict]) -> None:
        from collections import defaultdict
        tasks_by_type: Dict[str, List[Dict]] = defaultdict(list)
        for task in history:
            tt = task.get("task_type", "query")
            tasks_by_type[tt].append(task)

        for tt, tasks in tasks_by_type.items():
            success = sum(1 for t in tasks if t.get("status") == "done")
            total = len(tasks)
            rate = success / total if total > 0 else 0.0
            last = max((t.get("timestamp", "") for t in tasks), default="")
            self._capabilities[tt] = CapabilityScore(
                success_rate=round(rate, 2),
                task_count=total,
                avg_latency=f"{total * 0.5:.0f}s" if total > 0 else "",
                last_success=last,
                alpha=success + 1,
                beta=(total - success) + 1,
            )
        self._save()

    # ── Decision log ───────────────────────────────

    def record_decision(self, entry: DecisionEntry) -> None:
        self._decisions.append(entry)
        self._save()

    def get_decisions(self, limit: int = 100, decision_type: str = "") -> List[Dict]:
        entries = self._decisions
        if decision_type:
            entries = [e for e in entries if e.decision_type == decision_type]
        return [asdict(e) for e in entries[-limit:]]

    # ── State snapshot ───────────────────────────────

    def get_snapshots(self, limit: int = 90) -> List[Dict]:
        return [asdict(s) for s in self._snapshots[-limit:]]

    def take_snapshot(self) -> SnapshotEntry:
        snap = SnapshotEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            overall_success_rate=self._calc_overall_success_rate(),
            judge_avg_score=self._calc_judge_avg_score(),
            semantic_count=len(self._capabilities),
            procedural_active=len(self._decisions),
            error_rate_7d=self._calc_error_rate_7d(),
            pending_skills=0,
            audit_block_rate=0.0,
            load=min(1.0, len(self._decisions) / 100),
            # Extended fields
            knowledge_entries=len(self._capabilities),
            learner_cycles=sum(c.task_count for c in self._capabilities.values()),
            active_directions=len([c for c in self._capabilities.values() if c.task_count > 0]),
            hypotheses_verified=0,
        )
        self._snapshots.append(snap)
        self._save()
        return snap

    def inject_learner_stats(self, learner_stats: dict, belief_stats: dict) -> None:
        """Inject learner cycle results and belief pool stats into SelfModel.

        Updates capability scores, takes a snapshot, and recalculates grade.
        Called after each learner cycle by _cognition_tick().
        """
        total_cycles = learner_stats.get("total_cycles", 0)
        active_dirs = learner_stats.get("active_directions", 0)
        total_entries = learner_stats.get("total_entries", 0)

        # Update capability score for "learning" task type
        old = self._capabilities.get("learning", CapabilityScore())
        self._capabilities["learning"] = CapabilityScore(
            success_rate=round(total_entries / max(total_cycles, 1), 2),
            task_count=total_cycles,
            avg_latency=f"{active_dirs}s" if active_dirs else "",
            last_success=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            alpha=total_entries + 1,
            beta=max(total_cycles - total_entries, 0) + 1,
        )

        # Update belief health capability
        high_conf = belief_stats.get("high_confidence", 0)
        total_belief = belief_stats.get("count", 0)
        self._capabilities["belief_health"] = CapabilityScore(
            success_rate=round(high_conf / max(total_belief, 1), 2),
            task_count=total_belief,
            avg_latency="",
            last_success=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            alpha=high_conf + 1,
            beta=max(total_belief - high_conf, 0) + 1,
        )

        self._save()
        self.take_snapshot()


    def compute_learning_effectiveness(self) -> Dict[str, float]:
        """Compute learning effectiveness metrics from existing data.

        Returns:
            {
                "knowledge_retention_rate": 0.75,  # Fraction of entries referenced in last 30d
                "direction_saturation_speed": 5.2,  # Avg cycles per saturated direction
                "validation_pass_rate": 0.83,       # Validation success rate
            }
        """
        result = {
            "knowledge_retention_rate": 0.0,
            "direction_saturation_speed": 0.0,
            "validation_pass_rate": 0.0,
        }

        # 1. Knowledge retention rate
        # Checks recent KnowledgeBase entries for access count
        try:
            from ..learn.knowledge import KnowledgeBase
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=30)
            all_entries = list(KnowledgeBase.get_all_active())
            if all_entries:
                recent = [
                    e for e in all_entries
                    if e.get("last_accessed") or e.get("created_at", "")
                ]
                accessed = sum(1 for e in recent if e.get("last_accessed"))
                result["knowledge_retention_rate"] = round(
                    accessed / max(len(recent), 1), 2
                )
        except Exception:
            _log.exception("selfmodel error")

        # 2. Direction saturation speed
        # From direction config: avg cycles from active → completed/saturated
        try:
            from ..learn.direction import load_config_from_file
            cfg = load_config_from_file()
            speeds = []
            for topic, d in cfg.items():
                if isinstance(d, dict):
                    cycles = d.get("cycles_completed", 0)
                    sat = d.get("saturation", 0)
                    if sat >= 0.8 and cycles > 0:
                        speeds.append(cycles)
            if speeds:
                result["direction_saturation_speed"] = round(
                    sum(speeds) / len(speeds), 1
                )
        except Exception:
            _log.exception("selfmodel error")

        # 3. Validation pass rate
        # From Learner logs or SelfModel decision history
        try:
            decisions = getattr(self, "_decisions", [])
            if decisions:
                verified = sum(
                    1 for d in decisions
                    if getattr(d, "decision_type", "") == "knowledge_verify"
                    and getattr(d, "outcome", "") == "success"
                )
                total = sum(
                    1 for d in decisions
                    if getattr(d, "decision_type", "") == "knowledge_verify"
                )
                if total > 0:
                    result["validation_pass_rate"] = round(verified / total, 2)
        except Exception:
            _log.exception("selfmodel error")

        return result


    # ── Hot-update capabilities (evolve without code changes) ──

    def hot_update_weights(self, new_weights: Dict[str, float]) -> Dict:
        """Hot-update strategy parameters — takes effect immediately, no restart needed."""
        if not new_weights:
            return {"success": False, "reason": "Empty weights"}
        self.weights = new_weights
        self._save()
        return {"success": True, "applied": new_weights}

    def get_history(self, limit: int = 20) -> Dict:
        return {
            "capabilities": self.capabilities(),
            "recent_decisions": self.get_decisions(limit=10),
            "latest_snapshot": asdict(self._snapshots[-1]) if self._snapshots else {},
            "bottlenecks": self.graph.find_bottlenecks(),
        }

    # ── Persistence ─────────────────────────────────

    def _load(self) -> None:
        fp = self.project_dir / "selfmodel.json"
        if not fp.exists():
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            dec_field_names = set(DecisionEntry.__dataclass_fields__.keys())
            snap_field_names = set(SnapshotEntry.__dataclass_fields__.keys())
            cap_field_names = set(CapabilityScore.__dataclass_fields__.keys())
            self._decisions = [DecisionEntry(**{k: v for k, v in d.items() if k in dec_field_names})
                               for d in data.get("decisions", [])]
            self._snapshots = [SnapshotEntry(**{k: v for k, v in s.items() if k in snap_field_names})
                               for s in data.get("snapshots", [])]
            self._capabilities = {k: CapabilityScore(**{k2: v2 for k2, v2 in v.items() if k2 in cap_field_names})
                                  for k, v in data.get("capabilities", {}).items()}
            saved_weights = data.get("weights")
            if saved_weights and isinstance(saved_weights, dict):
                self.weights = saved_weights
        except Exception:
            pass  # non-critical, continue

    def _save(self) -> None:
        fp = self.project_dir / "selfmodel.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "decisions": [asdict(d) for d in self._decisions],
            "snapshots": [asdict(s) for s in self._snapshots],
            "capabilities": {k: asdict(v) for k, v in self._capabilities.items()},
            "weights": self.weights,
        }
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _calc_overall_success_rate(self) -> float:
        scores = [s.success_rate for s in self._capabilities.values()]
        base = sum(scores) / len(scores) if scores else 0.0
        # Evidence penalty: lower grade if total evidence is too low
        _total_ev = sum(s.task_count for s in self._capabilities.values())
        if _total_ev < 10:
            base *= 0.6  # very few decisions → penalize
        elif _total_ev < 50:
            base *= 0.8  # moderate decisions → slight penalty
        return round(base, 2)

    def _calc_judge_avg_score(self) -> float:
        scores = [d.judge_score for d in self._decisions if d.judge_score is not None]
        return round(sum(scores) / len(scores), 2) if scores else 0.0

    def _calc_error_rate_7d(self) -> float:
        """Calculate error rate for the last 7 days."""
        try:
            now = datetime.now()
            recent = [
                d for d in self._decisions
                if d.outcome == "MISS"
                and d.timestamp
                and (now - datetime.strptime(d.timestamp[:19], "%Y-%m-%d %H:%M:%S")).days < 7
            ]
            total_7d = [
                d for d in self._decisions
                if d.timestamp
                and (now - datetime.strptime(d.timestamp[:19], "%Y-%m-%d %H:%M:%S")).days < 7
            ]
            return round(len(recent) / max(len(total_7d), 1), 2)
        except Exception:
            return 0.0

    # ── Overall grade (S/A/B/C/D) ────────────────

    def overall_grade(self) -> Dict:
        """Return overall grade and aggregate score (threshold from calibration)."""
        rate = self._calc_overall_success_rate()
        from .calibration import get_calibration
        thresholds = get_calibration().get("selfmodel", "grade_thresholds",
            default=[[0.90, "S"], [0.75, "A"], [0.55, "B"], [0.30, "C"]])
        grade = "D"
        for thresh, g in thresholds:
            if rate >= thresh:
                grade = g
                break
        return {"grade": grade, "score": round(rate * 100), "label": self._grade_label(grade)}

    @staticmethod
    def _grade_label(grade: str) -> str:
        return {"S": "excellent", "A": "good", "B": "average", "C": "needs_improvement", "D": "beginner"}.get(grade, grade)

    # ── 5D Score ──────────────────────────────

    def dimension_scores(self) -> Dict[str, float]:
        """Return weighted scores for five dimensions (0-100)."""
        rate = self._calc_overall_success_rate()
        # Extract dimension data from capabilities
        caps = self._capabilities
        total_tasks = sum(c.task_count for c in caps.values())
        # 5D base score: success rate + task count + decisions + calibration + risk
        dims = {
            "方向性": self._calc_direction_score(),
            "效率": self._calc_efficiency_score(),
            "鲁棒性": self._calc_robustness_score(),
            "可复用性": self._calc_reusability_score(),
            "成本风险": self._calc_cost_risk_score(),
        }
        return dims

    def _calc_direction_score(self) -> float:
        """Direction score: higher success rate = better direction alignment."""
        rate = self._calc_overall_success_rate()
        return round(min(rate * 100 + 10, 100), 1)

    def _calc_efficiency_score(self) -> float:
        """Efficiency score: more tasks = higher efficiency."""
        total = sum(c.task_count for c in self._capabilities.values())
        return round(min(total * 5, 100), 1)

    def _calc_robustness_score(self) -> float:
        """Robustness score: sample size + volatility + recent trend.
        - Sample: lower beta_uncertainty = more stable (weight 40%)
        - Volatility: further from 50% = more stable (weight 30%)
        - Trend: recent success rate decline penalized (weight 30%)
        """
        caps = list(self._capabilities.values())
        if not caps:
            return 0.0

        # 1. Sample size dimension (40%)
        avg_uncertainty = sum(c.beta_uncertainty for c in caps) / len(caps)
        sample_score = max(0, (1 - avg_uncertainty) * 100)

        # 2. Volatility dimension (30%): more extreme success rate = more stable
        # 50% = completely random → 0 pts; 0% or 100% → 100 pts
        rates = [c.success_rate for c in caps]
        avg_rate = sum(rates) / len(rates) if rates else 0.5
        volatility_score = (1 - abs(avg_rate - 0.5) * 2) * 100  # 50%时0分, 0%/100%时100分
        volatility_score = 100 - volatility_score  # 反转：离50%越远分越高

        # 3. Recent trend dimension (30%): last decisions success rate
        trend_score = self._calc_recent_trend_score()

        score = sample_score * 0.40 + volatility_score * 0.30 + trend_score * 0.30
        return round(max(0, min(score, 100)), 1)

    def _calc_recent_trend_score(self) -> float:
        """Calculate recent trend from decision log."""
        decisions = self._decisions
        if len(decisions) < 3:
            return 50.0  # 数据不足，给中间分

        # Sort by time, take last 20
        sorted_decisions = sorted(decisions, key=lambda d: d.timestamp or "", reverse=True)[:20]
        total = len(sorted_decisions)
        if total == 0:
            return 50.0

        successes = sum(1 for d in sorted_decisions if d.outcome != "MISS")
        recent_rate = successes / total

        # Compare against overall success rate
        overall_successes = sum(1 for d in decisions if d.outcome != "MISS")
        overall_rate = overall_successes / len(decisions) if decisions else 0.5

        if recent_rate >= overall_rate:
            # Recent performance >= average → bonus
            return min(50 + (recent_rate - overall_rate) * 100, 100)
        else:
            # Recent decline → penalty
            return max(0, 50 - (overall_rate - recent_rate) * 100)

    def _calc_reusability_score(self) -> float:
        """Reusability: experience reuse across task types."""
        # Measure breadth by number of task types
        return round(min(len(self._capabilities) * 12, 100), 1)

    def _calc_cost_risk_score(self) -> float:
        """Cost/risk: lower error rate = higher score."""
        err = self._calc_error_rate_7d()
        return round(max(0, min((1 - err) * 100, 100)), 1)

    # ── Beta distribution chart data ─────────────────

    def beta_distributions(self) -> Dict[str, Dict]:
        """Returns每个能力的Beta distributionArgs，用于前端画分布图。"""
        result = {}
        for tt, score in self._capabilities.items():
            result[tt] = {
                "alpha": score.alpha,
                "beta": score.beta,
                "mean": score.beta_mean,
                "uncertainty": score.beta_uncertainty,
                "task_count": score.task_count,
            }
        return result

    def weekly_summary(self) -> str:
        """Produce a readable one-line weekly summary.

        Example: 'Grade B (stable) | success rate 72% (up 5% vs last week) | 2 bottlenecks'
        """
        trend = TrendAnalyzer.analyze(self._snapshots)
        grade_info = f"Grade {trend.grade} ({trend.grade_trend})"
        rate_info = (f"success rate {trend.success_rate['current']:.0%}"
                     f" ({'up' if trend.improvement_rate > 0 else 'down'} "
                     f"{abs(trend.improvement_rate)*100:.0f}% vs last week)"
                     if trend.velocity != "insufficient_data"
                     else f"success rate {trend.success_rate['current']:.0%}")
        bottle_info = ""
        bottlenecks = self.graph.find_bottlenecks()
        if bottlenecks:
            bottle_info = f" | {len(bottlenecks)} bottleneck(s): {', '.join(bottlenecks[:3])}"
        vol_info = f" | volatility {trend.volatility:.2f}" if trend.volatility > 0.05 else ""
        return f"{grade_info} | {rate_info}{bottle_info}{vol_info}"

    def weekly_comparison(self) -> dict:
        """Compare this week vs last week. Returns structured diff."""
        from collections import defaultdict
        now = datetime.now()
        recent_entries = []
        older_entries = []
        for s in self._snapshots:
            s_ts = _parse_ts(s.timestamp)
            d = (now - s_ts).days
            if d <= 7:
                recent_entries.append(s)
            elif 7 < d <= 14:
                older_entries.append(s)

        def _safe_avg(entries, attr: str) -> float:
            vals = [getattr(e, attr, 0) for e in entries]
            return round(sum(vals) / len(vals), 3) if vals else 0.0

        trend = TrendAnalyzer.analyze(self._snapshots)

        # Capability-level comparison
        caps_current = {k: v.success_rate for k, v in self._capabilities.items()}

        return {
            "grade": trend.grade,
            "grade_trend": trend.grade_trend,
            "success_rate": {
                "current": trend.success_rate["current"],
                "week_ago": trend.success_rate["week_ago"],
                "change": trend.improvement_rate,
                "velocity": trend.velocity,
            },
            "error_rate": {
                "this_week": _safe_avg(recent_entries, "error_rate_7d"),
                "last_week": _safe_avg(older_entries, "error_rate_7d"),
            },
            "load": {
                "this_week": _safe_avg(recent_entries, "load"),
                "last_week": _safe_avg(older_entries, "load"),
            },
            "knowledge_growth": {
                "entries_then": _safe_avg(older_entries, "knowledge_entries"),
                "entries_now": _safe_avg(recent_entries, "knowledge_entries"),
            },
            "stability": {
                "stagnation_days": trend.stagnation_days,
                "volatility": trend.volatility,
            },
            "improvement_index": self.improvement_index(),
            "capabilities": caps_current,
        }

    def capability_trends(self) -> dict:
        """Return per-capability change over observation window."""
        result = {}
        for name, cap in self._capabilities.items():
            result[name] = {
                "success_rate": cap.success_rate,
                "task_count": cap.task_count,
                "uncertainty": cap.beta_uncertainty,
                "n": cap.alpha + cap.beta,
            }
        return result

    def improvement_index(self) -> float:
        """Single composite score: -1.0 (declining) to +1.0 (improving).

        Combines:
        - success rate trend velocity
        - stagnation penalty
        - volatility penalty
        """
        trend = TrendAnalyzer.analyze(self._snapshots)
        idx = trend.improvement_rate  # base: -0.05 to +0.05 typical
        # Stagnation penalty
        if trend.stagnation_days > 3:
            idx -= 0.1 * min(trend.stagnation_days / 7, 1.0)
        # Volatility penalty
        if trend.volatility > 0.05:
            idx -= 0.05
        return round(max(-1.0, min(1.0, idx)), 3)


# ══════════════════════════════════════════════════════════════
# TrendAnalyzer — cross-time trend detection for SelfModel snapshots
# ══════════════════════════════════════════════════════════════


@dataclass
class TrendReport:
    """Cross-time trend report produced by TrendAnalyzer."""
    success_rate: Dict = field(default_factory=lambda: {"current": 0.0, "week_ago": 0.0, "month_ago": 0.0})
    velocity: str = "stable"       # "improving", "stable", "declining"
    improvement_rate: float = 0.0  # weekly delta
    stagnation_days: int = 0
    volatility: float = 0.0        # stddev of last 14 snapshots
    grade: str = "N/A"
    grade_trend: str = "stable"    # "upgrading", "stable", "downgrading"


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.min


def _avg_rate(entries: List[SnapshotEntry]) -> float:
    return sum(s.overall_success_rate for s in entries) / len(entries) if entries else 0.0


class TrendAnalyzer:
    """Analyze SelfModel snapshot sequence for cross-time trends.

    Produces TrendReport with velocity, stagnation detection, volatility,
    and grade trajectory.
    """

    @staticmethod
    def analyze(snapshots: List[SnapshotEntry]) -> TrendReport:
        if not snapshots:
            return TrendReport()
        if len(snapshots) < 2:
            latest = snapshots[-1]
            return TrendReport(
                success_rate={"current": latest.overall_success_rate,
                              "week_ago": 0.0, "month_ago": 0.0},
                velocity="insufficient_data",
                grade="N/A",
            )

        now = datetime.now()

        # Group snapshots by time windows
        recent = [s for s in snapshots
                  if (now - _parse_ts(s.timestamp)).days <= 7]
        last_week = [s for s in snapshots
                     if 7 < (now - _parse_ts(s.timestamp)).days <= 14]
        last_month = [s for s in snapshots
                      if 14 < (now - _parse_ts(s.timestamp)).days <= 30]

        current = _avg_rate(recent) if recent else (
            snapshots[-1].overall_success_rate if snapshots else 0.0)
        week_ago = _avg_rate(last_week) if last_week else current
        month_ago = _avg_rate(last_month) if last_month else current

        # Improvement velocity: compare last 3 vs second-to-last 3
        if len(snapshots) >= 6:
            first_half = snapshots[-6:-3] if len(snapshots) >= 6 else snapshots[:2]
            second_half = snapshots[-3:]
            improvement = _avg_rate(second_half) - _avg_rate(first_half)
        else:
            improvement = current - week_ago

        velocity = "improving" if improvement > 0.02 else (
            "declining" if improvement < -0.02 else "stable")

        # Stagnation: count consecutive snapshots with no improvement
        stagnation = 0
        latest_rate = snapshots[-1].overall_success_rate if snapshots else 0.0
        for s in reversed(snapshots[-14:]):
            if s.overall_success_rate <= latest_rate:
                stagnation += 1
            else:
                break

        # Volatility: stddev of last 14 rates
        rates = [s.overall_success_rate for s in snapshots[-14:]]
        vol = TrendAnalyzer._stddev(rates) if len(rates) >= 2 else 0.0

        # Grade trajectory
        grades = []
        for s in snapshots[-7:]:
            r = s.overall_success_rate
            if r >= 0.90:
                grades.append("S")
            elif r >= 0.75:
                grades.append("A")
            elif r >= 0.55:
                grades.append("B")
            elif r >= 0.30:
                grades.append("C")
            else:
                grades.append("D")
        current_grade = grades[-1] if grades else "N/A"
        # Compare first vs last in the window for grade trend
        if len(grades) >= 2:
            grade_order = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
            delta = grade_order.get(grades[-1], 0) - grade_order.get(grades[0], 0)
            grade_trend = "upgrading" if delta > 0 else (
                "downgrading" if delta < 0 else "stable")
        else:
            grade_trend = "stable"

        return TrendReport(
            success_rate={"current": round(current, 3),
                          "week_ago": round(week_ago, 3),
                          "month_ago": round(month_ago, 3)},
            velocity=velocity,
            improvement_rate=round(improvement, 3),
            stagnation_days=stagnation,
            volatility=round(vol, 3),
            grade=current_grade,
            grade_trend=grade_trend,
        )

    @staticmethod
    def _stddev(values: List[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        return variance ** 0.5


__all__ = [
    "SelfModel", "SelfGraph", "CapabilityScore",
    "DecisionEntry", "SnapshotEntry",
    "TrendAnalyzer", "TrendReport",
]
