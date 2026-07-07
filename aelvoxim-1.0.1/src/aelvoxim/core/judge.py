"""aelvoxim.core.judge — Self-scoring engine

Agent-as-a-Judge: Score evolution proposals across 6 dimensions.
Dual-mode: Rule engine (offline) and LLM evaluation (online).
All thresholds read dynamically from calibration.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class JudgeGrade(Enum):
    S = "S"  # Excellent — execute with priority
    A = "A"  # Good — normal execution
    B = "B"  # Average — automatic execution
    C = "C"  # Poor — blocked
    D = "D"  # Very poor — blocked


# Fallback thresholds (used when calibration loading fails)
FALLBACK_GRADE_THRESHOLDS: List[Tuple[float, JudgeGrade]] = [
    (0.85, JudgeGrade.S),
    (0.70, JudgeGrade.A),
    (0.50, JudgeGrade.B),
    (0.40, JudgeGrade.C),
]

FALLBACK_REQUIRES_APPROVAL: Dict[str, bool] = {
    "S": False, "A": False, "B": False, "C": True, "D": True,
}

FALLBACK_WEIGHTS = {
    "direction": 0.20,
    "efficiency": 0.15,
    "robustness": 0.15,
    "reusability": 0.10,
    "cost_risk": 0.15,
    "prediction_confidence": 0.25,
}


def _get_cal():
    from .calibration import get_calibration
    return get_calibration()


def score_to_grade(score: float) -> JudgeGrade:
    cal = _get_cal()
    thresholds = cal.get("judge", "grade_thresholds", default=FALLBACK_GRADE_THRESHOLDS)
    for threshold, grade_name in thresholds:
        grade = JudgeGrade(grade_name)
        if score >= threshold:
            return grade
    return JudgeGrade.D


def _requires_approval(grade: JudgeGrade) -> bool:
    cal = _get_cal()
    app = cal.get("judge", "requires_approval", default=FALLBACK_REQUIRES_APPROVAL)
    return app.get(grade.value, True)


def _weights() -> Dict[str, float]:
    cal = _get_cal()
    return cal.get("judge", "weights", default=FALLBACK_WEIGHTS)


@dataclass
class DimensionScore:
    name: str
    score: float
    weight: float
    reason: str = ""

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass
class JudgeResult:
    proposal_id: str
    proposal_summary: str
    dimensions: List[DimensionScore]
    total_score: float = 0.0
    grade: JudgeGrade = JudgeGrade.B
    timestamp: str = ""
    details: Optional[Dict] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = sum(d.weighted for d in self.dimensions)
        if abs(self.total_score - total) < 0.001 or self.total_score == 0.0:
            self.total_score = total
        self.grade = score_to_grade(self.total_score)

    def passed(self) -> bool:
        return self.grade in (JudgeGrade.S, JudgeGrade.A, JudgeGrade.B)

    def requires_approval(self) -> bool:
        return _requires_approval(self.grade)

    def to_dict(self) -> Dict:
        return {
            "proposal_id": self.proposal_id,
            "proposal_summary": self.proposal_summary,
            "total_score": round(self.total_score, 3),
            "grade": self.grade.value,
            "passed": self.passed(),
            "requires_approval": self.requires_approval(),
            "timestamp": self.timestamp,
            "dimensions": [
                {"name": d.name, "score": d.score, "weight": d.weight,
                 "weighted": round(d.weighted, 3), "reason": d.reason}
                for d in self.dimensions
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class Proposal:
    id: str
    summary: str
    description: str = ""
    change_type: str = "update"
    target: str = ""
    estimated_steps: int = 5
    estimated_tokens: int = 2000
    previous_failures: int = 0
    is_rsi_aligned: bool = True


class JudgeEngine:
    """Evaluation engine base class."""
    def evaluate(self, proposal: Proposal) -> JudgeResult:
        raise NotImplementedError

    def _build_result(self, proposal: Proposal, scores: Dict[str, Tuple[float, str]]) -> JudgeResult:
        w = _weights()
        dimensions = []
        for dim_name, (score, reason) in scores.items():
            weight = w.get(dim_name, 0.2)
            dimensions.append(DimensionScore(
                name=dim_name,
                score=max(0.0, min(1.0, score)),
                weight=weight,
                reason=reason,
            ))
        return JudgeResult(
            proposal_id=proposal.id,
            proposal_summary=proposal.summary,
            dimensions=dimensions,
        )


class RuleBasedJudge(JudgeEngine):
    """Rule-based Judge engine, fully local scoring with no LLM needed.

    Supports injection of PredictiveReasoner's Prediction results.
    All scoring params read dynamically from calibration.json.
    """

    def __init__(self, prediction: Optional[Any] = None):
        super().__init__()
        self._prediction = prediction

    def set_prediction(self, prediction: Any) -> None:
        self._prediction = prediction

    def evaluate(self, proposal: Proposal) -> JudgeResult:
        cal = _get_cal()
        jc = cal.get("judge", default={})
        scores: Dict[str, Tuple[float, str]] = {}

        # 1. Direction
        rsi_score = jc.get("direction_rsi_score", 0.8)
        non_rsi = jc.get("direction_non_rsi_score", 0.3)
        del_mult = jc.get("direction_delete_multiplier", 0.5)
        dir_score = rsi_score if proposal.is_rsi_aligned else non_rsi
        if "delete" in proposal.change_type:
            dir_score *= del_mult
        scores["direction"] = (dir_score, self._direction_reason(proposal))

        # 2. Efficiency
        step_cost = jc.get("efficiency_step_cost", 0.1)
        eff_min = jc.get("efficiency_min_score", 0.1)
        eff_del = jc.get("efficiency_delete_multiplier", 0.5)
        eff_score = max(eff_min, 1.0 - proposal.estimated_steps * step_cost)
        if "delete" in proposal.change_type:
            eff_score *= eff_del
        scores["efficiency"] = (eff_score, f"Estimated {proposal.estimated_steps} steps")

        # 3. Robustness
        fail_cost = jc.get("robustness_fail_cost", 0.2)
        rob_min = jc.get("robustness_min_score", 0.2)
        rob_score = max(rob_min, 1.0 - proposal.previous_failures * fail_cost)
        scores["robustness"] = (rob_score, f"Previous failures: {proposal.previous_failures}")

        # 4. Reusability
        reuse_create = jc.get("reusability_create", 0.8)
        reuse_update = jc.get("reusability_update", 0.6)
        reuse_other = jc.get("reusability_other", 0.3)
        if proposal.change_type in ("create",):
            reuse_score = reuse_create
        elif proposal.change_type in ("update",):
            reuse_score = reuse_update
        else:
            reuse_score = reuse_other
        scores["reusability"] = (reuse_score, f"Change type: {proposal.change_type}")

        # 5. Cost & risk
        token_rate = jc.get("cost_token_rate", 10000)
        cost_min = jc.get("cost_min_score", 0.1)
        cost_score = max(cost_min, 1.0 - proposal.estimated_tokens / token_rate)
        scores["cost_risk"] = (cost_score, f"Estimated {proposal.estimated_tokens} tokens")

        # 6. Prediction confidence
        pred_conf_weight = jc.get("prediction_confidence_weight", 0.7)
        pred_risk_weight = jc.get("prediction_risk_weight", 0.3)
        pred_no_data = jc.get("prediction_no_data", 0.5)
        pred_max = jc.get("prediction_max_score", 0.95)
        if self._prediction is not None:
            adjusted = self._prediction.confidence * pred_conf_weight + (1 - self._prediction.risk_score) * pred_risk_weight
            outcome_label = "Predicted Success" if self._prediction.expected_outcome == "success" else "Predicted Neutral/Failure"
            reason = (
                f"Inference: confidence={self._prediction.confidence:.0%} risk={self._prediction.risk_score:.0%} "
                f"-> {adjusted:.0%} | {outcome_label}"
            )
            scores["prediction_confidence"] = (min(pred_max, adjusted), reason)
        else:
            scores["prediction_confidence"] = (pred_no_data, "No prediction data, neutral score")

        return self._build_result(proposal, scores)

    @staticmethod
    def _direction_reason(proposal: Proposal) -> str:
        if not proposal.is_rsi_aligned:
            return "Non-RSI direction"
        if "delete" in proposal.change_type:
            return "RSI direction with delete operation"
        return f"RSI direction: {proposal.summary[:60]}"



# ── Knowledge entry scoring ────────────


@dataclass
class KnowledgeProposal:
    """Judge proposal for knowledge entry validation."""
    topic: str
    content: str
    source: str
    confidence: float
    content_length: int
    has_execution: bool = False


def score_knowledge_entry(entry: KnowledgeProposal) -> "JudgeResult":
    """Score a knowledge entry before storage.

    Dimensions:
    1. Topic relevance — based on source type + confidence
    2. Content richness — length + structural indicators
    3. Trustworthiness — execution > LLM > search
    4. Information density — code vs text ratio
    """
    from .calibration import get_calibration
    jc = {}
    try:
        cal = get_calibration()
        jc = cal.get("judge", default={}) or {}
    except Exception:
        pass  # non-critical, continue

    scores: Dict[str, Tuple[float, str]] = {}

    # 1. Topic relevance
    src_mult = {"execution_result": 0.95, "learner_task": 0.7, "file_import": 0.8, "manual": 0.6}
    base = src_mult.get(entry.source, 0.5)
    rel_score = min(1.0, base * (0.5 + entry.confidence * 0.5))
    scores["topic_relevance"] = (rel_score, f"source={entry.source} conf={entry.confidence:.2f}")

    # 2. Content richness
    if entry.content_length < 40:
        richness = 0.1
    elif entry.content_length < 100:
        richness = 0.3
    elif entry.content_length < 200:
        richness = 0.6
    elif entry.content_length < 500:
        richness = 0.8
    else:
        richness = 0.95
    # Bonus for structured content (code blocks, lists)
    if "```" in entry.content or "\n- " in entry.content or "\n1." in entry.content:
        richness = min(1.0, richness + 0.15)
    scores["content_richness"] = (richness, f"{entry.content_length} chars")

    # 3. Trustworthiness
    trust = 1.0 if entry.has_execution else (0.7 if entry.confidence > 0.7 else 0.4)
    scores["trustworthiness"] = (trust, "execution_result" if entry.has_execution else "non-execution")

    # 4. Information density
    has_explanation = any(w in entry.content.lower() for w in
                         ["enables", "allows", "because", "creates", "groups",
                          "manages", "specifies", "controls", "purpose", "用于",
                          "实现", "提供", "支持", "通过"])
    density = 0.95 if (entry.content_length > 200 and has_explanation) else 0.5
    scores["info_density"] = (density, "has explanation" if has_explanation else "no explanation")

    # Build result
    weights = {"topic_relevance": 0.30, "content_richness": 0.25,
               "trustworthiness": 0.30, "info_density": 0.15}
    weighted_sum = sum(scores[k][0] * weights[k] for k in weights if k in scores)
    total_weight = sum(weights[k] for k in weights if k in scores)

    final_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    dims = [DimensionScore(name=k, score=v[0], weight=weights.get(k, 0.25), reason=v[1]) for k, v in scores.items()]

    thresholds = {"S": 0.85, "A": 0.7, "B": 0.5, "C": 0.4}
    if final_score >= 0.85:
        grade = JudgeGrade.S
    elif final_score >= 0.7:
        grade = JudgeGrade.A
    elif final_score >= 0.5:
        grade = JudgeGrade.B
    elif final_score >= 0.4:
        grade = JudgeGrade.C
    else:
        grade = JudgeGrade.D

    return JudgeResult(grade=grade, total_score=final_score,
                       proposal_id=f"kb_{entry.topic[:20]}_{entry.source}",
                       proposal_summary=entry.topic,
                       dimensions=dims, timestamp=datetime.now().isoformat())


__all__ = [
    "JudgeGrade", "JudgeResult", "JudgeEngine", "RuleBasedJudge", "Proposal",
    "KnowledgeProposal", "score_knowledge_entry",
    "DimensionScore", "score_to_grade", "FALLBACK_GRADE_THRESHOLDS", "FALLBACK_WEIGHTS",
]
