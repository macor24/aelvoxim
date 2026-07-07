# SPDX-License-Identifier: MIT
"""
metacore.learn.active_scan — Periodic health scan for knowledge and memory.

Runs every 30 minutes (triggered by cognition_tick).
Checks:
1. Memory layer cleanup (expired/low-value entries)
2. Knowledge base health (entries, confidence, coverage)
3. Direction health (active/completed, saturation)
4. Semantic enhancement for low-quality entries

Writes health reports to ~/.metacore/health/<date>.jsonl
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR

# ── Constants ──

_HEALTH_DIR = Path(METACORE_DIR) / "health"
_SCAN_INTERVAL = 1800  # 30 minutes
_last_scan: float = 0.0


def should_scan() -> bool:
    """Check if 30 minutes have passed since last scan."""
    global _last_scan
    now = time.time()
    if now - _last_scan >= _SCAN_INTERVAL:
        return True
    return False


def _mark_scanned() -> None:
    global _last_scan
    _last_scan = time.time()


def run_scan(log_fn: Optional[callable] = None) -> Dict[str, Any]:
    """Run a full health scan and return the report.

    Args:
        log_fn: Optional logging function (e.g., self._log from Learner).

    Returns:
        Dict with 'memory', 'knowledge', 'directions', 'ts' keys.
    """
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    report: Dict[str, Any] = {"ts": ts}

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    memory_stats = {}
    try:
        # L3 rollback: backup memory.db before cleanup
        try:
            import shutil
            from ..utils import METACORE_DIR as _md
            _src = str(_md / "memory.db")
            _dst = str(_md / "memory.db.rollback")
            shutil.copy2(_src, _dst)
        except Exception:
            pass
        from ..memory import cleanup_all as _cl
        from ..memory import _fusion as _fus
        _cl({"working": _fus.working, "episodic": _fus.episodic, "semantic": _fus.semantic})
        memory_stats = {"cleaned": 0}
        # Time-based decay: re-score entities from DB
        try:
            import json as _js, sqlite3 as _sq
            from datetime import datetime as _dt, timedelta
            from ..memory.scorer import apply_decay
            from ..utils import METACORE_DIR as _md
            _db = _sq.connect(str(_md / "memory.db"))
            _rows = _db.execute(
                "SELECT id, type, value, attributes, created_at FROM entities ORDER BY created_at DESC"
            ).fetchall()
            _now = _dt.now()
            _cleaned = 0
            _pending_forget = 0
            for _r in _rows:
                _attrs = _js.loads(_r[3]) if isinstance(_r[3], str) else (_r[3] or {})
                if isinstance(_r[3], str) and _r[3]:
                    _attrs = _js.loads(_r[3])
                else:
                    _attrs = _r[3] or {}
                _ttl = _attrs.get("_ttl")
                _created = str(_r[4] or _now)[:10]
                _days = (_now - _dt.strptime(_created, "%Y-%m-%d")).days if _created else 0
                # Re-score: apply decay based on last access days
                _conf = _attrs.get("_confidence", 0.6)
                _decayed = apply_decay(_conf, _days, _ttl)
                # Tag for pending forget if confidence is low
                if _decayed < 0.40 and _decayed >= 0.20 and _ttl != -1:
                    if not _attrs.get("_pending_forget"):
                        _attrs["_pending_forget"] = 1
                        _attrs["_conf_decayed"] = round(_decayed, 2)
                        _db.execute("UPDATE entities SET attributes = ? WHERE id = ?",
                                    (_js.dumps(_attrs, ensure_ascii=False), _r[0]))
                        _pending_forget += 1
                # Check TTL expiry
                if _ttl is not None and _ttl > 0:
                    expires = _attrs.get("_expires_at", "")
                    if expires:
                        exp_dt = _dt.strptime(str(expires)[:10], "%Y-%m-%d")
                        if exp_dt < _now:
                            _db.execute("DELETE FROM entities WHERE id = ?", (_r[0],))
                            _cleaned += 1
                else:
                    # Untagged entry: heavy decay for 90+ day old
                    if _days > 90 and _decayed < 0.20:
                        _db.execute("DELETE FROM entities WHERE id = ? AND tags NOT LIKE '%person%' AND tags NOT LIKE '%location%'", (_r[0],))
                        _cleaned += 1
            _db.commit()
            _db.close()
            memory_stats["cleaned"] = _cleaned
            memory_stats["pending_forget"] = _pending_forget
        except Exception:
            pass
    except Exception:
        pass
    report["memory"] = memory_stats or {"cleaned": 0}

    # ── 2. Knowledge base health ──
    try:
        from ..learn.knowledge import KnowledgeBase
        all_kb = list(KnowledgeBase.get_all_active())
        total = len(all_kb)
        if total > 0:
            confs = [k.get("confidence", 0) for k in all_kb if isinstance(k, dict)]
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            low_conf = sum(1 for c in confs if c < 0.3)
            topics: Dict[str, int] = {}
            for k in all_kb:
                t = k.get("topic", "unknown") if isinstance(k, dict) else "unknown"
                topics[t] = topics.get(t, 0) + 1
            top_topics = sorted(topics.items(), key=lambda x: -x[1])[:10]
            report["knowledge"] = {
                "total": total,
                "avg_confidence": round(avg_conf, 3),
                "low_conf_ratio": round(low_conf / total, 3) if total > 0 else 0,
                "topic_count": len(topics),
                "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
            }
        else:
            report["knowledge"] = {"total": 0}
    except Exception:
        report["knowledge"] = {"error": "knowledge check failed"}

    # ── 3. Direction health ──
    try:
        from ..learn.learner import get_learner
        l = get_learner()
        directions = list(l._directions.values()) if hasattr(l, '_directions') else []
        active = sum(1 for d in directions if d.status == "active")
        completed = sum(1 for d in directions if d.status == "completed")
        saturated = sum(1 for d in directions if hasattr(d, 'saturation') and d.status == "completed" and d.saturation >= 0.8)
        avg_sat = 0.0
        if completed > 0:
            sats = [d.saturation for d in directions if d.status == "completed" and hasattr(d, 'saturation')]
            avg_sat = sum(sats) / len(sats) if sats else 0.0
        report["directions"] = {
            "total": len(directions),
            "active": active,
            "completed": completed,
            "saturated": saturated,
            "avg_saturation": round(avg_sat, 3),
        }
        if saturated > 0:
            _log(f"  📊 {saturated} directions saturated, consider resetting for new content")
    except Exception:
        report["directions"] = {"error": "direction check failed"}

    # ── Write report ──
    _HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    today = _dt.now().strftime("%Y-%m-%d")
    log_path = _HEALTH_DIR / f"{today}.jsonl"
    try:
        with open(str(log_path), "a") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    except Exception:
        pass

    _mark_scanned()
    return report


def get_latest_report() -> Optional[Dict]:
    """Get the most recent health report from disk."""
    files = sorted(_HEALTH_DIR.glob("*.jsonl"), reverse=True)
    if not files:
        return None
    try:
        lines = files[0].read_text().strip().split("\n")
        return json.loads(lines[-1])
    except Exception:
        return None


def get_trend(days: int = 7) -> List[Dict]:
    """Get health report trend for the last N days."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    results = []
    for f in sorted(_HEALTH_DIR.glob("*.jsonl")):
        if f.stem < cutoff:
            continue
        try:
            for line in f.read_text().strip().split("\n"):
                if line:
                    results.append(json.loads(line))
        except Exception:
            pass
    return results
