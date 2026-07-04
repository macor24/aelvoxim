"""
metacore.experts.introspection — Introspection Expert.

Meta-expert: evaluates the quality of the reasoning process itself.
Runs AFTER all other experts, analyzes their outputs from the shared
context, and produces a self-assessment of the reasoning quality.

Does NOT participate in voting or arbitration — purely diagnostic.
Pure rule-based, no LLM calls.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register


def _score_to_grade(score: float) -> str:
    """Convert a numeric score to a letter grade."""
    if score >= 0.9:
        return "S"
    if score >= 0.75:
        return "A"
    if score >= 0.55:
        return "B"
    if score >= 0.30:
        return "C"
    return "D"


@register
class IntrospectionExpert(BaseExpert):
    """Meta-expert: evaluates reasoning quality after all experts complete.

    Pipeline:
    1. Read all expert outputs from shared context
    2. Score each expert on contribution quality
    3. Compute overall grade for this reasoning round
    4. Detect issues (too many skipped, all low-conf, etc.)
    """
    _capabilities = ["introspection", "meta", "quality", "audit"]
    name = "introspection"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        shared = (inp.context or {}).get("_shared_context", {})

        details: Dict[str, Any] = {
            "evaluations": {},
            "overall_grade": "N/A",
            "issues": [],
            "experts_evaluated": 0,
        }

        if not shared:
            return ExpertOutput(
                expert_name=self.name,
                opinion="Introspection: no shared context available",
                confidence=0.3,
                details=details,
            )

        evaluations: Dict[str, Dict] = {}
        total_score = 0.0
        expert_count = 0
        skipped_count = 0
        error_count = 0

        for name, output in shared.items():
            if not isinstance(output, dict):
                continue

            opinion = output.get("opinion", "")
            confidence = output.get("confidence", 0.0)
            error = output.get("error")
            details_output = output.get("details", {})
            skipped = output.get("skipped", False)

            # Evaluate contribution quality
            score = 0.5  # base

            if skipped:
                score = 0.0
                skipped_count += 1
            elif error:
                score = 0.1
                error_count += 1
            elif confidence >= 0.7 and len(opinion or "") > 20:
                score = 0.85  # strong contribution
            elif confidence >= 0.5:
                score = 0.65  # moderate contribution
            elif len(opinion or "") > 10:
                score = 0.4   # some content
            else:
                score = 0.2   # weak

            # Bonus for structured details
            if isinstance(details_output, dict) and len(details_output) >= 2:
                score = min(1.0, score + 0.1)

            evaluations[name] = {
                "quality_score": round(score, 2),
                "confidence": confidence,
                "skipped": skipped,
                "has_error": bool(error),
            }

            if not skipped and not error:
                total_score += score
                expert_count += 1

        details["evaluations"] = evaluations
        details["experts_evaluated"] = expert_count
        details["skipped_count"] = skipped_count
        details["error_count"] = error_count

        # Compute overall grade
        avg_score = total_score / max(expert_count, 1)
        grade = _score_to_grade(avg_score)
        details["overall_grade"] = grade

        # Detect issues
        issues = []
        if skipped_count >= 3:
            issues.append("high_skip_rate")
        if error_count >= 2:
            issues.append("multiple_errors")
        if avg_score < 0.4 and expert_count >= 2:
            issues.append("low_overall_quality")
        if not shared:
            issues.append("no_expert_output")
        details["issues"] = issues

        # Build opinion
        parts = [f"Introspection: grade {grade} (avg {avg_score:.2f})"]
        if expert_count:
            parts.append(f"{expert_count} experts evaluated")
        if issues:
            parts.append(f"issues: {', '.join(issues)}")
        opinion = " | ".join(parts)

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=round(avg_score, 2),
            details=details,
        )
