"""
metacore.experts.orchestrator — Expert Orchestrator.

Collects opinions from registered experts via dynamic discovery,
selects relevant experts via RouteSelector, executes them via
SubAgentManager (subprocess isolation), votes, resolves conflicts,
and produces a unified output. Acts as the brain's "executive function".

Enhancement: When experts disagree (confidence gap > 0.3 or ethics block),
the orchestrator calls LLM for final arbitration, with fallback to
weighted-average voting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from .base import BaseExpert, ExpertInput, ExpertOutput
from . import discover_experts
import logging
_log = logging.getLogger("aelvoxim.experts.orchestrator")

# ── Voting weights — updated dynamically by WeightManager ──

EXPERT_WEIGHTS = {
    "memory": 0.20,
    "logic": 0.20,
    "ethics": 0.15,
    "emotion": 0.10,
    "creative": 0.15,
    "safety": 0.20,
}

# Task-type specific base weights
_TASK_WEIGHTS = {
    "code":     {"logic": 0.30, "memory": 0.25, "safety": 0.20, "creative": 0.05, "emotion": 0.05, "ethics": 0.15},
    "analysis": {"logic": 0.30, "memory": 0.25, "creative": 0.20, "emotion": 0.05, "safety": 0.10, "ethics": 0.10},
    "creative": {"creative": 0.30, "emotion": 0.25, "memory": 0.20, "logic": 0.10, "safety": 0.10, "ethics": 0.05},
    "security": {"safety": 0.35, "ethics": 0.25, "logic": 0.20, "memory": 0.10, "creative": 0.05, "emotion": 0.05},
    "planning": {"logic": 0.25, "memory": 0.20, "creative": 0.20, "ethics": 0.15, "safety": 0.10, "emotion": 0.10},
    "chat":     {"emotion": 0.25, "memory": 0.25, "ethics": 0.20, "logic": 0.10, "safety": 0.10, "creative": 0.10},
}


# ── WeightManager — dynamic expert weight adjustment ──


@dataclass
class ExpertPerformance:
    """Recent performance record for one expert."""
    scores: List[float] = field(default_factory=list)
    max_records: int = 50

    @property
    def avg_score(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.5


class WeightManager:
    """Dynamic weight adjustment based on recent expert performance."""

    def __init__(self):
        self._history: Dict[str, ExpertPerformance] = {}

    def record_task(self, expert_name: str, task_type: str,
                    confidence: float, had_findings: bool, was_blocked: bool) -> None:
        """Record one expert's performance and compute a quality score.

        Score:
          0.0 blocked/skipped/error
          0.3 low confidence + no findings
          0.6 findings without high confidence
          0.8 high confidence + findings
          1.0 high confidence + findings + not blocked
        """
        if expert_name not in self._history:
            self._history[expert_name] = ExpertPerformance()

        if was_blocked:
            score = 0.0
        elif confidence >= 0.7 and had_findings:
            score = 0.8
        elif confidence >= 0.5:
            score = 0.6
        elif had_findings:
            score = 0.4
        else:
            score = 0.3

        self._history[expert_name].scores.append(score)

    def get_weights(self, task_type: str) -> Dict[str, float]:
        """Get dynamic weights for a task type, adjusted by recent history.

        1. Start with task-type base weights
        2. Adjust by recent performance (avg > 0.7 → boost, avg < 0.3 → reduce)
        3. Normalize to sum 1.0
        """
        base = _TASK_WEIGHTS.get(task_type, EXPERT_WEIGHTS)
        weights = dict(base)

        for name in weights:
            perf = self._history.get(name)
            if perf and len(perf.scores) >= 3:
                avg = perf.avg_score
                if avg > 0.7:
                    weights[name] *= 1.2
                elif avg < 0.3:
                    weights[name] *= 0.8

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 2) for k, v in weights.items()}
        return weights


# ── Arbitration helpers ──

# If the gap between top and bottom confidence exceeds this, trigger LLM arbitration
_CONFIDENCE_GAP_THRESHOLD = 0.35

# Select at most this many experts per think() call
_MAX_EXPERTS = 6


def _needs_arbitration(results: List[ExpertOutput]) -> bool:
    """Check if experts disagree enough to need LLM arbitration."""
    confidences = [
        r.confidence for r in results
        if r.error is None and r.expert_name != "creative" and r.expert_name != "safety"
    ]
    if len(confidences) < 2:
        return False
    return (max(confidences) - min(confidences)) >= _CONFIDENCE_GAP_THRESHOLD


def _arbitrate(results: List[ExpertOutput], inp: ExpertInput) -> Tuple[float, str]:
    """Call LLM to arbitrate between conflicting expert opinions."""
    summary = ""
    for r in results:
        status = "OK" if r.error is None else f"FAILED({r.error})"
        summary += f"  [{r.expert_name}] confidence={r.confidence} status={status}\n"
        summary += f"    Opinion: {r.opinion[:200]}\n"
        if r.details:
            raw = str(r.details)[:300]
            summary += f"    Details: {raw}\n"
    try:
        from aelvoxim.learn.extract import call_llm_if_available
        llm = call_llm_if_available()
        if not llm:
            return _weighted_vote(results), "LLM unavailable, using weighted vote"
        call_fn, model = llm
        prompt = (
            f"You are the final arbiter for an AI brain's multi-expert system. "
            f"Given the user query: '{inp.query}'\n\n"
            f"Below are opinions from {len(results)} experts:\n{summary}\n\n"
            f"The experts disagree. Decide which expert's opinion is most reliable.\n"
            f"Respond with JUST a confidence score 0.0-1.0 and a 1-sentence explanation.\n"
            f"Format: CONFIDENCE: 0.XX\n"
            f"REASON: <your reason>"
        )
        text = call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=256,
        )
        if text:
            conf = _parse_arbitration(text)
            if conf is not None:
                ethics_result = next(
                    (r for r in results if r.expert_name == "ethics"),
                    None,
                )
                if ethics_result and ethics_result.error and "ETHICAL BLOCK" in str(ethics_result.error):
                    return min(conf, 0.2), f"LLM arbitration overridden by ethics block: {_extract_reason(text)}"
                return conf, _extract_reason(text)
        return _weighted_vote(results), "LLM arbitration failed to parse, using weighted vote"
    except Exception as e:
        return _weighted_vote(results), f"LLM arbitration error: {e}"


def _needs_debate(results: List[ExpertOutput]) -> bool:
    """Check if expert conflict warrants a debate round rather than simple vote.

    Triggers when safety/ethics blocks AND logic/memory allows,
    creating a genuine disagreement that needs LLM judgment.
    """
    blocked_names = set()
    allowed_high_conf = []
    for r in results:
        if r.skipped:
            continue
        if r.expert_name in ("safety", "ethics") and r.error:
            err = str(r.error).upper()
            if "BLOCK" in err or "SAFETY" in err or "ETHICAL" in err:
                blocked_names.add(r.expert_name)
        if r.error is None and r.confidence > 0.5:
            if r.expert_name not in ("creative", "emotion"):
                allowed_high_conf.append(r.expert_name)
    # Debate if at least one block AND at least one allow with high confidence
    return bool(blocked_names) and bool(allowed_high_conf)


def _debate(results: List[ExpertOutput], inp: ExpertInput) -> Tuple[float, str]:
    """Run an LLM debate round between conflicting experts.

    Presents blocking vs allowing arguments and asks LLM to judge
    which side is more compelling. Safety/ethics rules get priority.
    """
    debate_text = ""
    for r in results:
        if r.skipped:
            continue
        if r.error and ("BLOCK" in str(r.error).upper()
                        or "SAFETY" in str(r.error).upper()
                        or "ETHICAL" in str(r.error).upper()):
            debate_text += f"[BLOCK - {r.expert_name.upper()}] {r.opinion[:200]}\n"
        elif r.error is None and r.confidence > 0.5:
            debate_text += f"[ALLOW - {r.expert_name}] {r.opinion[:200]}\n"

    try:
        from aelvoxim.learn.extract import call_llm_if_available
        llm = call_llm_if_available()
        if not llm:
            return _weighted_vote(results), "LLM unavailable for debate, using weighted vote"

        call_fn, model = llm
        prompt = (
            f"Two groups of experts disagree on: '{inp.query}'\n\n"
            f"BLOCK side (safety/ethics concerns):\n"
            f"{debate_text}\n"
            f"Safety rules and ethical guidelines are HIGHEST priority.\n"
            f"Only ALLOW if you are certain the operation is safe.\n\n"
            f"Decide: DECISION: block/allow\n"
            f"REASON: <1 sentence>"
        )
        text = call_fn(
            model=model,
            system_prompt="",
            user_message=prompt,
            max_tokens=256,
        )
        if text:
            import re
            m = re.search(r'DECISION:\s*(block|allow)', text, re.IGNORECASE)
            if m:
                decision = m.group(1).lower()
                reason = _extract_reason(text)
                if decision == "block":
                    return 0.05, f"Debate: block — {reason}"
                else:
                    return 0.6, f"Debate: allow — {reason}"
        return _weighted_vote(results), "Debate failed to parse, using weighted vote"
    except Exception as e:
        return _weighted_vote(results), f"Debate error: {e}"


def _weighted_vote(results: List[ExpertOutput], weights: Dict[str, float] = None) -> float:
    """Standard weighted-average vote. Uses provided weights or defaults."""
    if weights is None:
        weights = EXPERT_WEIGHTS
    total_weight = 0.0
    weighted_sum = 0.0
    for r in results:
        weight = EXPERT_WEIGHTS.get(r.expert_name, 0.15)
        if r.error:
            weight *= 0.1
        else:
            total_weight += weight
            weighted_sum += r.confidence * weight
    return round(weighted_sum / max(total_weight, 0.01), 2)


def _parse_arbitration(text: str) -> Optional[float]:
    """Extract confidence score from LLM arbitration response."""
    try:
        import re
        m = re.search(r'CONFIDENCE:\s*([\d.]+)', text)
        if m:
            conf = float(m.group(1))
            return max(0.0, min(1.0, conf))
    except Exception:
        _log.exception("orchestrator error")
    return None


def _extract_reason(text: str) -> str:
    """Extract reason line from LLM arbitration response."""
    try:
        import re
        m = re.search(r'REASON:\s*(.+)', text)
        if m:
            return m.group(1).strip()
    except Exception:
        _log.exception("orchestrator error")
    return "LLM arbitration completed"


class ExpertOrchestrator:
    """Orchestrates registered experts via dynamic discovery + route-based selection.

    On init, discovers all registered experts via the plugin registry.
    On think(), selects relevant experts via RouteSelector, executes them
    via SubAgentManager (subprocess isolation with timeout), then votes.

    Usage:
        orch = ExpertOrchestrator()
        result = orch.think(inp)
    """

    def __init__(self, edition: str = ""):
        if not edition:
            from aelvoxim import _EDITION
            edition = _EDITION
        # Discover all registered expert classes
        self._expert_classes: List[Type[BaseExpert]] = discover_experts(edition=edition)
        self._expert_names: Set[str] = {
            cls.__name__.lower().replace("expert", "")
            for cls in self._expert_classes
        }
        self._router = None  # lazy init
        self._weight_manager = WeightManager()

    def _get_router(self):
        if self._router is None:
            from .router import RouteSelector
            self._router = RouteSelector()
        return self._router

    def _select_experts(self, query: str) -> List[Type[BaseExpert]]:
        """Use RouteSelector to pick which experts to run."""
        selected_names = self._get_router().select(query, self._expert_names)
        # Map names back to classes (preserve registration order within selection)
        selected = []
        for cls in self._expert_classes:
            name = cls.__name__.lower().replace("expert", "")
            if name in selected_names and len(selected) < _MAX_EXPERTS:
                selected.append(cls)
        # Fallback: if selection is empty, use first 6
        if not selected:
            return self._expert_classes[:_MAX_EXPERTS]
        # Ensure at least 5 experts participate for better arbitration
        if len(selected) < 5:
            _extra_needed = 5 - len(selected)
            for cls in self._expert_classes:
                name = cls.__name__.lower().replace("expert", "")
                if name not in selected_names and len(selected) < 5:
                    selected.append(cls)
        return selected

    def _run_introspection_for(self, inp: ExpertInput, results: list) -> Optional[dict]:
        """Run IntrospectionExpert on a set of results and return structured output."""
        try:
            from .introspection import IntrospectionExpert
            # Build shared context from results
            shared_ctx = {}
            for r in results:
                shared_ctx[r.expert_name] = {
                    "opinion": r.opinion,
                    "confidence": r.confidence,
                    "error": r.error,
                    "skipped": r.skipped,
                    "details": r.details,
                }
            inp.context["_shared_context"] = shared_ctx
            _ie = IntrospectionExpert()
            _out = _ie.run(inp)
            if _out:
                return {
                    "grade": _out.details.get("overall_grade", "N/A"),
                    "quality": _out.confidence,
                    "issues": _out.details.get("issues", []),
                    "opinion": _out.opinion,
                }
        except Exception:
            _log.exception("orchestrator error")
        return None

    def think(self, inp: ExpertInput, expert_filter: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run selected experts via subprocess isolation, collect opinions,
        vote, and return a unified result.

        When experts disagree significantly, uses LLM arbitration.
        Falls back to in-process serial execution if subprocess fails.
        """
        start = time.time()
        results: List[ExpertOutput] = []
        errors: List[str] = []

        # Select which experts to run based on query
        selected_classes = self._select_experts(inp.query)
        # Apply filter from chat_pipeline routing (expert_subset from routing_rules.json)
        if expert_filter:
            filtered = [cls for cls in selected_classes
                        if cls.__name__.lower().replace("expert", "") in [e.lower() for e in expert_filter]]
            if filtered:
                selected_classes = filtered

        # Phase 1: Run selected experts via subprocess (with fallback)
        try:
            from .sub_agent import SubAgentManager
            _sam = SubAgentManager(timeout=8)
            results = _sam.run_all(selected_classes, inp)
        except Exception:
            # Fallback: in-process serial execution (no shared context)
            for cls in selected_classes:
                try:
                    expert = cls()
                    out = expert.run(inp)
                    results.append(out)
                    if out.error:
                        errors.append(f"{expert.name}: {out.error}")
                except Exception as e:
                    results.append(ExpertOutput(
                        expert_name=cls.__name__.lower().replace("expert", ""),
                        opinion=f"{cls.__name__} failed",
                        confidence=0.0,
                        error=str(e),
                    ))
                    errors.append(f"{cls.__name__}: {e}")

        # Phase 2: Detect divergence among experts
        _confidences = [r.confidence for r in results if r and not r.error]
        _divergence = 0.0
        if _confidences:
            _divergence = max(_confidences) - min(_confidences)
        _has_divergence = _divergence > 0.3 or len(results) < 3

        # Build shared context from results for introspection
        _shared_ctx = {}
        for r in results:
            _shared_ctx[r.expert_name] = {
                "opinion": r.opinion,
                "confidence": r.confidence,
                "error": r.error,
                "skipped": r.skipped,
                "details": r.details,
            }
        if inp.context is None:
            inp.context = {}
        inp.context["_shared_context"] = _shared_ctx

        # Phase 2: Check if ethics or safety blocked; filter skipped experts
        blocked = False
        active_results = []
        for r in results:
            if r.skipped:
                continue  # exclude skipped experts from arbitration
            if r.expert_name == "ethics" and r.error and "ETHICAL BLOCK" in str(r.error):
                blocked = True
            if r.expert_name == "safety" and r.error and "SAFETY BLOCK" in str(r.error):
                blocked = True
            active_results.append(r)

        # Pre-compute weighted vote for reuse
        # Use dynamic weights if WeightManager is available
        _dynamic_weights = None
        if hasattr(self, '_weight_manager'):
            from .router import TaskClassifier
            _task_type = TaskClassifier.classify(inp.query)
            _dynamic_weights = self._weight_manager.get_weights(_task_type)
            # Record performance for each expert
            for r in results:
                self._weight_manager.record_task(
                    expert_name=r.expert_name,
                    task_type=_task_type,
                    confidence=r.confidence,
                    had_findings=bool(r.details and len(str(r.details)) > 20),
                    was_blocked=r.skipped or bool(r.error),
                )

        avg_conf_from_phase1 = _weighted_vote(results, _dynamic_weights)

        # Phase 2b: Check if debate is needed (safety/ethics vs logic conflict)
        debate_result = None
        if not blocked and _needs_debate(results):
            debate_conf, debate_reason = _debate(results, inp)
            debate_result = {"confidence": debate_conf, "reason": debate_reason}
            if debate_conf < 0.1:
                blocked = True

        # Phase 3: Arbitrate or vote
        if blocked:
            avg_conf = min(avg_conf_from_phase1, 0.1)
            arbitration_reason = "Ethics/Safety block: overriding all expert opinions"
        elif _needs_arbitration(results):
            avg_conf, arbitration_reason = _arbitrate(results, inp)
        else:
            avg_conf = avg_conf_from_phase1
            arbitration_reason = "No arbitration needed — experts agree"

        # Phase 4: Build opinion
        opinion_parts = []
        for r in results:
            if r.error:
                opinion_parts.append(f"{r.expert_name}: unavailable ({r.error})")
            else:
                opinion_parts.append(f"{r.expert_name}: {r.opinion[:100]}")

        if blocked:
            _blocked_by = next(
                (r for r in results if r.expert_name == "safety" and r.error and "Safety block" in str(r.error)),
                next(
                    (r for r in results if r.expert_name == "ethics" and r.error and "ETHICAL BLOCK" in str(r.error)),
                    None,
                ),
            )
            if _blocked_by:
                opinion = f"BLOCKED by {_blocked_by.expert_name}: {_blocked_by.opinion}"
            else:
                opinion = "BLOCKED"
        else:
            opinion = " | ".join(opinion_parts)
            opinion += f"\n[Arbitration: {arbitration_reason}]"

        elapsed_ms = round((time.time() - start) * 1000, 1)

        # Phase 5: Append introspection result
        introspection_result = self._run_introspection_for(inp, results)

        return {
            "opinion": opinion,
            "confidence": avg_conf,
            "blocked": blocked,
            "expert_results": [r for r in results],
            "arbitration": arbitration_reason,
            "debate": debate_result,
            "introspection": introspection_result,
            "experts_selected": [cls.__name__ for cls in selected_classes],
            "vote": {
                "experts_voted": len([r for r in results if not r.error]),
                "experts_failed": len(errors),
                "weighted_confidence": avg_conf,
            },
            "timing_ms": elapsed_ms,
            "errors": errors[:3],
        }

    def think_fast(self, inp: ExpertInput) -> Dict[str, Any]:
        """Quick mode: run a minimal set in-process (no subprocess overhead).

        Selects 2-3 experts via router for the fastest possible response.
        """
        # Pick the top 2-3 experts from routing
        selected_names = self._get_router().select(inp.query, self._expert_names)
        fast_names = list(selected_names)[:3]
        fast_classes = [cls for cls in self._expert_classes
                        if cls.__name__.lower().replace("expert", "") in fast_names]

        start = time.time()
        results = []
        for cls in fast_classes:
            try:
                expert = cls()
                results.append(expert.run(inp))
            except Exception as e:
                results.append(ExpertOutput(
                    expert_name=cls.__name__.lower().replace("expert", ""),
                    opinion="failed", confidence=0.0, error=str(e),
                ))

        # Compute weight sum for the selected subset
        total_w = sum(EXPERT_WEIGHTS.get(r.expert_name, 0.2) for r in results)
        conf = round(
            sum(r.confidence * EXPERT_WEIGHTS.get(r.expert_name, 0.2) for r in results)
            / max(total_w, 0.01), 2
        )

        return {
            "opinion": " | ".join(f"{r.expert_name}: {r.opinion[:100]}" for r in results),
            "confidence": conf,
            "blocked": any(
                (r.expert_name == "ethics" and r.error and "ETHICAL BLOCK" in str(r.error))
                or (r.expert_name == "safety" and r.error and "SAFETY BLOCK" in str(r.error))
                for r in results
            ),
            "mode": "fast",
            "introspection": self._run_introspection_for(inp, results),
            "expert_results": results,
            "experts_selected": [cls.__name__ for cls in fast_classes],
            "timing_ms": round((time.time() - start) * 1000, 1),
        }
