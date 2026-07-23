"""aelvoxim.learn.meta_cog — Meta-cognition reflection + repair verification

Split from learner.py (1969-line monolith).
Responsibility: analyze triggers, execute reflection actions, verify repair effectiveness.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import logging
_log = logging.getLogger("aelvoxim.meta_cog")



def analyze_triggers(report, directions: dict) -> Optional[dict]:
    """Analyze triggered signals and return root cause + suggested action."""
    for t in report.get("triggers", []):
        if not getattr(t, "triggered", False) or getattr(t, "score", 0) < 0.05:
            continue
        sig_name = getattr(t, "signal_name", "")
        t_score = getattr(t, "score", 0)
        if sig_name == "stagnation":
            stale = None
            _max_age = 0
            for d in directions.values():
                _age = d.cycles_completed or 0
                if _age > _max_age:
                    _max_age = _age
                    stale = d
            if stale:
                return {
                    "level": "P1",
                    "cause": "stagnation",
                    "target": stale.topic,
                    "action": "reset_task_queue",
                    "detail": f"{stale.topic} stagnated ({stale.entries_created}e/{_max_age}c)",
                }
        elif sig_name == "repeat_failure":
            return {
                "level": "P1",
                "cause": "repeat_failure",
                "action": "switch_engine",
                "detail": f"{int(t_score * 10)} recent validation failures",
            }
        elif sig_name == "success_rate":
            return {
                "level": "P2",
                "cause": "low_success_rate",
                "action": "tighten_gate",
                "detail": f"success_rate score={t_score:.2f}",
            }
        elif sig_name == "belief_health":
            return {
                "level": "P2",
                "cause": "belief_degradation",
                "action": "cleanup_kb",
                "detail": f"belief_health score={t_score:.2f}",
            }
    return None


def analyze_with_hypotheses(report, directions: dict, log_func: Callable, learner_ref=None) -> Optional[dict]:
    """Run analyze_triggers and attach generated hypotheses."""
    analysis = analyze_triggers(report, directions)
    if analysis:
        from dataclasses import asdict
        from ..learn.hypothesis import HypothesisGenerator, HypothesisVerifier
        topics = [d.topic for d in directions.values()]
        hypotheses = HypothesisGenerator.generate(analysis, topics)
        if hypotheses:
            # Dedup: skip hypotheses already confirmed recently
            from ..learn.hypothesis import _load_hypotheses
            recent = _load_hypotheses(limit=20)
            recent_confirmed = {h.cause for h in recent if h.status == "confirmed"}
            new_hypotheses = [h for h in hypotheses if h.cause not in recent_confirmed]
            if not new_hypotheses:
                log_func("  ⏭️ All hypotheses already confirmed recently, skipping")
                # Try generating new seeds from curiosity engine
                try:
                    from ..learn.curiosity import pick_next_topic
                    fallback = pick_next_topic(topics)
                    if fallback:
                        log_func(f"  🌱 Curiosity seed: {fallback[:80]}")
                        analysis["fallback_seed"] = fallback
                        return analysis
                except Exception:
                    _log.exception("meta_cog error")
                return None
            hypotheses = new_hypotheses
            analysis["hypotheses"] = [asdict(h) for h in hypotheses]
            hypo_str = ", ".join(f'"{h.cause[:40]}..."' for h in hypotheses)
            log_func(f"  🧠 Generated {len(hypotheses)} hypothesis(es): {hypo_str}")
            # Verify hypotheses against actual learner state
            verified = HypothesisVerifier.verify_all(hypotheses, learner=learner_ref)
            analysis["verified_hypotheses"] = verified
            v_confirmed = sum(1 for v in verified if "confirmed" in v.lower())
            log_func(f"  ✅ Verified hypotheses: {v_confirmed}/{len(verified)} confirmed")
            # Write confirmed hypotheses to BeliefPool
            from ..core.belief import BeliefPool
            bp = BeliefPool()
            for h, v in zip(hypotheses, verified or []):
                if "confirmed" in v.lower():
                    try:
                        key = f"hypothesis:{h.id}"
                        bp.get_or_create(key, prior_a=3, prior_b=1)
                        bp.record_outcome(key, True)  # confirmed = success
                    except Exception:
                        _log.exception("meta_cog error")
    return analysis


def execute_reflection(
    analysis: dict,
    directions: dict,
    save_config: Callable,
    log_func: Callable,
    learner_ref=None,
) -> dict | None:
    """Execute the action determined by root cause analysis.

    Returns _last_repair dict if a repair was performed.
    """
    if not analysis:
        return None
    action = analysis.get("action", "")
    topic = analysis.get("target", "")
    if action == "reset_task_queue":
        if topic and topic in directions:
            d = directions[topic]
            d.task_queue = "[]"
            d.current_task = ""
            d.reflect_no_produce = 0
            save_config()
            log_func(f"  🧠 Reflected: reset '{topic}' queue — {analysis.get('detail','')}")
    elif action == "switch_engine":
        import os as _os
        _engines = ["bing_cn", "duckduckgo", "so"]
        _current = _os.environ.get("AELVOXIM_SEARCH_ENGINE", _os.environ.get("METACORE_SEARCH_ENGINE", "bing_cn"))
        # Pre-check with cache (avoid SelfModel query on every engine switch)
        _cache_key = f"engine_precheck_{_current}"
        _cached = getattr(execute_reflection, _cache_key, None)
        _next_engine = _engines[(_engines.index(_current) + 1) % len(_engines)] if _current in _engines else _engines[0]
        _skip = False
        if _cached is not None:
            _skip = _cached
            if _skip:
                log_func(f"  ⏭️ [Precheck] (cached) All alternate engines have poor history, keeping '{_current}'")
        else:
            try:
                from ..core.selfmodel import SelfModel
                _sm = SelfModel()
                for _eng in _engines:
                    if _eng == _current:
                        continue
                    _cap_name = f"search_engine_{_eng}"
                    _cap = _sm._capabilities.get(_cap_name) if hasattr(_sm, '_capabilities') else None
                    if _cap and _cap.task_count >= 3 and (_cap.success_rate or 0) < 0.2:
                        log_func(f"  ⏭️ [Precheck] Engine '{_eng}' historical success {_cap.success_rate:.0%} (<20%, skipping)")
                        if _eng == _next_engine:
                            _skip = True
            except Exception:
                _log.exception("meta_cog error")
            # Cache result for next switch (reset on process restart)
            setattr(execute_reflection, _cache_key, _skip)
        if _skip:
            log_func(f"  ⏭️ [Precheck] All alternate engines have poor history, keeping '{_current}'")
        else:
            _os.environ["AELVOXIM_SEARCH_ENGINE"] = _next_engine
            log_func(f"  🧠 Reflected: switch search {_current}→{_next_engine} — {analysis.get('detail','')}")
    elif action == "tighten_gate":
        log_func(f"  🧠 Reflected: tightening quality gates — {analysis.get('detail','')}")
    elif action == "cleanup_kb":
        try:
            from ..learn.knowledge import KnowledgeBase
            from datetime import datetime as _dt2
            _now = _dt2.now().timestamp()
            _cutoff = _now - 3 * 86400
            _all = list(KnowledgeBase.get_all_active())
            _old = [e for e in _all if (e.get('created_at', _now) if isinstance(e.get('created_at'), (int, float)) else 0) < _cutoff]
            log_func(f"  🧠 Reflected: {len(_all)} entries, {len(_old)} older than 3d — {analysis.get('detail','')}")
        except Exception:
            _log.exception("meta_cog error")

    # Record this repair for later verification
    repair = None
    if action:
        repair = {
            "action": action,
            "target": topic,
            "signal": analysis.get("cause", ""),
            "ts": time.time(),
            "verified": False,
        }
        # Attach to learner_ref so verify_repair can find it
        if learner_ref is not None:
            learner_ref._last_repair = repair
            learner_ref._cycles_since_repair = 0

    # Verify any pending hypotheses
    try:
        from dataclasses import asdict
        hypotheses_dicts = analysis.get("hypotheses", [])
        if hypotheses_dicts:
            from ..learn.hypothesis import Hypothesis, HypothesisVerifier
            hypotheses = [Hypothesis(**h) for h in hypotheses_dicts]
            results = HypothesisVerifier.verify_all(hypotheses, learner_ref)
            for r in results:
                log_func(f"  🧪 Hypothesis result: {r}")
    except Exception:
        _log.exception("meta_cog error")

    return repair


def verify_repair(learner_ref) -> Optional[dict]:
    """Check if the last repair action was effective.

    Waits at least 3 cycles after a repair before checking.
    Returns {"status": "resolved" / "worse", ...} or None.
    """
    last = getattr(learner_ref, '_last_repair', None)
    if not last or last.get("verified"):
        return None

    cycles_since = getattr(learner_ref, '_cycles_since_repair', 0) + 1
    learner_ref._cycles_since_repair = cycles_since
    if cycles_since < 3:
        return None

    try:
        from ..core.metacog_monitor import MetaCogMonitor
        from ..core.selfmodel import SelfModel
        sm = SelfModel()
        mon = MetaCogMonitor()
        learner_stats = {"total_cycles": 0, "active_directions": 0, "total_entries": 0}
        belief_stats = {"count": 0, "high_confidence": 0, "low_confidence": 0, "total_evidence": 0}
        if hasattr(sm, '_capabilities'):
            bc = sm._capabilities.get("belief_health", None)
            if bc:
                belief_stats = {
                    "count": bc.task_count,
                    "high_confidence": int(bc.success_rate * bc.task_count),
                    "low_confidence": int((1 - bc.success_rate) * bc.task_count),
                    "total_evidence": bc.alpha + bc.beta,
                }
        new_report = mon.evaluate(learner_stats=learner_stats, belief_stats=belief_stats)

        signal = last.get("signal", "")
        triggers = new_report.get("triggers") or []
        still_triggered = any(
            getattr(t, "signal_name", "") == signal and getattr(t, "triggered", False)
            for t in triggers
        )
        if not still_triggered:
            last["verified"] = True
            last["outcome"] = "success"
            return {"status": "resolved", "signal": signal, "detail": "Signal no longer triggered"}
        else:
            last["verified"] = True
            last["outcome"] = "failed"
            return {"status": "worse", "signal": signal, "detail": "Signal still triggered after repair"}
    except Exception:
        return None


def update_selfmodel_from_repair(repair_result: dict) -> None:
    """Record repair outcome in SelfModel for long-term performance tracking."""
    try:
        from ..core.selfmodel import SelfModel, CapabilityScore
        sm = SelfModel()
        if "repair" not in sm._capabilities:
            sm._capabilities["repair"] = CapabilityScore()
        sm._capabilities["repair"].record_outcome(success=(repair_result["status"] == "resolved"))
        sm._save()
    except Exception:
        _log.exception("meta_cog error")
