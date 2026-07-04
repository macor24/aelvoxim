"""aelvoxim.learn.autotune — Direction-level self-tuning for Learner.

Reads SelfModel metrics per-direction and adjusts:
- Direction priority (pause low-success directions, prioritize high-uncertainty ones)
- Cycle frequency (slow down on stagnation, speed up on progress)
- Auto-replacement (add new direction when one stagnates)

Called from Learner._main_loop() every 5 minutes alongside Calibration.auto_tune().
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR

TUNE_LOG = METACORE_DIR / "learner" / "autotune.log"


def _log(msg: str) -> None:
    TUNE_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(TUNE_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


# ── Tunable thresholds (can be hot-updated via SelfModel) ──

CONFIG: Dict[str, Any] = {
    # If a direction's success_rate < this, consider it struggling
    "struggle_threshold": 0.3,
    # If uncertainty > this, the direction needs more attention
    "curiosity_threshold": 0.4,
    # If a direction has cycles > this with zero entries, it's stalled
    "stall_cycles": 5,
    # Max active directions (edition gated, but we check anyway)
    "max_active": 10,
    # Cooldown cycles before auto-pausing a struggling direction
    "pause_cooldown_cycles": 5,
}


def _get_direction_metrics(learner) -> List[Dict]:
    """Collect per-direction metrics from SelfModel capability scores."""
    try:
        from ..core.selfmodel import SelfModel

        sm = SelfModel()
        caps = sm._capabilities
    except Exception:
        return []

    results: List[Dict] = []
    for topic, direction in learner._directions.items():
        if direction.status != "active":
            continue
        # Per-direction metrics: derive from direction's own stats
        cycles = direction.cycles_completed or 0
        entries = direction.entries_created or 0
        # success_rate: proportion of cycles that produced entries
        # (entries created out of total cycles, capped at 1.0)
        success_rate = min(entries / max(cycles, 1), 1.0)
        # uncertainty: high when few cycles have been run
        uncertainty = round(1.0 / max(cycles ** 0.5, 1), 3)

        results.append({
            "topic": topic,
            "status": direction.status,
            "success_rate": success_rate,
            "uncertainty": uncertainty,
            "entries_created": direction.entries_created,
            "cycles_completed": direction.cycles_completed,
            "phase_index": direction.phase_index,
            "saturation": direction.saturation,
        })

    return results


def _decide_adjustments(metrics: List[Dict], learner,
                        struggle_threshold: float = CONFIG["struggle_threshold"],
                        stall_cycles: int = CONFIG["stall_cycles"],
                        pause_cooldown: int = CONFIG["pause_cooldown_cycles"]) -> List[Dict]:
    """Run rules against metrics and return a list of adjustment decisions."""
    changes: List[Dict] = []

    # Get current active count
    active = [d for d in learner._directions.values() if d.status == "active"]
    active_count = len(active)

    for m in metrics:
        topic = m["topic"]
        direction = learner._directions.get(topic)
        if not direction:
            continue

        # Rule 1: Stalled direction — many cycles but no entries
        if m["cycles_completed"] >= stall_cycles and m["entries_created"] == 0:
            if direction.status == "active":
                # Check cooldown: have we paused this recently?
                last_pause_key = f"_last_pause:{topic}"
                last_pause = getattr(learner, last_pause_key, 0)
                if learner._cognition_cycle_count - last_pause >= pause_cooldown:
                    direction.status = "paused"
                    setattr(learner, last_pause_key, learner._cognition_cycle_count)
                    changes.append({
                        "target": topic,
                        "action": "paused",
                        "reason": f"Stalled: {m['cycles_completed']} cycles, 0 entries",
                    })
                    _log(f"⏸️  Paused {topic}: stalled ({m['cycles_completed']}c, 0 entries)")

        # Rule 2: High uncertainty — needs more exploration
        if m["uncertainty"] > CONFIG["curiosity_threshold"] and m["entries_created"] > 0:
            # Already active, no additional action needed — uncertainty will
            # naturally decrease as more entries are created
            pass

        # Rule 3: Low success rate (struggling)
        if m["success_rate"] < struggle_threshold and m["entries_created"] > 3:
            last_pause_key = f"_last_pause:{topic}"
            last_pause = getattr(learner, last_pause_key, 0)
            if learner._cognition_cycle_count - last_pause >= pause_cooldown:
                direction.status = "paused"
                setattr(learner, last_pause_key, learner._cognition_cycle_count)
                changes.append({
                    "target": topic,
                    "action": "paused",
                    "reason": f"Low success rate: {m['success_rate']:.0%}",
                })
                _log(f"⏸️  Paused {topic}: low success rate ({m['success_rate']:.0%})")

        # Rule 4: Saturation — direction has enough entries, mark for review
        if m["saturation"] >= 0.8 and m["entries_created"] >= 20:
            if direction.status == "active":
                direction.status = "completed"
                from datetime import datetime as _dt
                direction.completed_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                changes.append({
                    "target": topic,
                    "action": "completed",
                    "reason": f"Saturated: {m['saturation']:.0%} ({m['entries_created']} entries)",
                })
                _log(f"✅ Completed {topic}: saturated ({m['saturation']:.0%})")

    # Rule 5: Evict stale paused directions — 2+ days old with 0 entries
    from datetime import datetime as _dt
    _now = _dt.now()
    _stale_cutoff = 2 * 86400  # 2 days in seconds
    _paused_dirs = [(t, d) for t, d in learner._directions.items() if d.status == "paused"]
    for topic, direction in _paused_dirs:
        if direction.entries_created > 0:
            continue
        try:
            _created = _dt.fromisoformat(direction.started_at) if direction.started_at else _now
        except Exception:
            _created = _now
        if (_now - _created).total_seconds() >= _stale_cutoff:
            del learner._directions[topic]
            changes.append({
                "target": topic,
                "action": "evicted",
                "reason": f"Stale paused: 0 entries, {( _now - _created).days}d old",
            })
            _log(f"🗑️  Evicted {topic}: stale paused (0 entries, {(_now - _created).days}d old)")

    # Rule 6: If too many paused/completed, auto-add a fresh direction
    paused_count = len([d for d in learner._directions.values() if d.status == "paused"])
    completed_count = len([d for d in learner._directions.values() if d.status == "completed"])
    if paused_count >= 3 and active_count < 3:
        # Try to resume a paused direction with most entries (most promising)
        paused = sorted(
            [(t, d) for t, d in learner._directions.items() if d.status == "paused"],
            key=lambda x: x[1].entries_created,
            reverse=True,
        )
        for topic, direction in paused[:1]:
            direction.status = "active"
            changes.append({
                "target": topic,
                "action": "resumed",
                "reason": f"Auto-resumed (paused={paused_count}, active={active_count})",
            })
            _log(f"▶️  Resumed {topic}: auto-resumed (paused={paused_count}, active<3)")

    # Rule 6: SelfModel weight hot-update based on overall trends
    try:
        from ..core.selfmodel import SelfModel

        sm = SelfModel()
        overall = sm._calc_overall_success_rate() if hasattr(sm, "_calc_overall_success_rate") else 0.5

        if overall < 0.3 and active_count > 5:
            # Too many active directions with low overall success — tighten focus
            new_weights = sm.weights.copy()
            new_weights["direction"] = min(new_weights.get("direction", 0.25) + 0.05, 0.5)
            new_weights["efficiency"] = max(new_weights.get("efficiency", 0.20) - 0.05, 0.1)
            sm.hot_update_weights(new_weights)
            changes.append({
                "target": "selfmodel.weights",
                "action": "updated",
                "reason": f"Overall success {overall:.0%}<30% with {active_count} active: tightened focus",
            })
            _log(f"⚖️  Updated weights: direction={new_weights['direction']:.2f} efficiency={new_weights['efficiency']:.2f}")
    except Exception:
        pass

    return changes


def _apply_adjustments(learner, changes: List[Dict]) -> None:
    """Persist decisions: save config, record in SelfModel."""
    if not changes:
        return

    # Save learner config (writes to PG + JSON)
    learner._save_config()

    # Record adjustments as SelfModel decisions
    try:
        from ..core.selfmodel import SelfModel, DecisionEntry
        from datetime import datetime

        sm = SelfModel()
        for c in changes:
            sm.record_decision(DecisionEntry(
                timestamp=datetime.now().isoformat(),
                decision_type="autotune",
                task=f"{c['action']}: {c['target']}",
                chosen=c["action"],
                outcome=c["reason"],
            ))
    except Exception:
        pass

    # Write a brief changelog
    METACORE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = METACORE_DIR / "learner" / "autotune_changes.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            for c in changes:
                line = {**c, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass

    _log(f"Applied {len(changes)} changes: {[c['action'] for c in changes]}")


def tune(learner) -> List[Dict]:
    """Main entry point. Collect metrics, decide, apply, return changes."""
    # Read dynamic thresholds from calibration (falls back to CONFIG defaults)
    try:
        from ..core.calibration import get_calibration
        cal = get_calibration()
        struggle_threshold = cal.get("autotune", "struggle_threshold", default=CONFIG["struggle_threshold"])
        stall_cycles = cal.get("autotune", "stall_cycles", default=CONFIG["stall_cycles"])
        pause_cooldown = cal.get("autotune", "pause_cooldown_cycles", default=CONFIG["pause_cooldown_cycles"])
    except Exception:
        struggle_threshold = CONFIG["struggle_threshold"]
        stall_cycles = CONFIG["stall_cycles"]
        pause_cooldown = CONFIG["pause_cooldown_cycles"]

    metrics = _get_direction_metrics(learner)
    if not metrics:
        return []

    changes = _decide_adjustments(metrics, learner,
                                  struggle_threshold=struggle_threshold,
                                  stall_cycles=stall_cycles,
                                  pause_cooldown=pause_cooldown)
    if changes:
        _apply_adjustments(learner, changes)

    return changes
