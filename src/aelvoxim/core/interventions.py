"""二阶认知的最小闭环：开实验 → 24h 后按数据判成败 → 保留或回退。

设计约束（2026-10-21）：
- 一次只允许一个进行中的实验；同一旋钮 48h 冷却
- 只允许"收紧"（方向数下降），不允许自动放宽；下限 4
- 判据用落盘窗口算「条目/轮」，提升 ≥10% 才算成功，否则回退
- 全程写 interventions.jsonl，可手工关掉（calibration: interventions.enabled=false）
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

KNOB = "learning.max_active_directions"
DEFAULT_CAP = 10
MIN_CAP = 4
TTL_HOURS = 24.0
IMPROVE_RATIO = 0.10
COOLDOWN_HOURS = 48.0


def _log_file() -> Path:
    from ..utils import METACORE_DIR
    return METACORE_DIR / "interventions.jsonl"


def _append(rec: Dict[str, Any]) -> None:
    p = _log_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _records() -> List[Dict[str, Any]]:
    p = _log_file()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def list_all() -> List[Dict[str, Any]]:
    return _records()


def active() -> Optional[Dict[str, Any]]:
    """当前进行中的实验（status=open）。"""
    for r in reversed(_records()):
        if r.get("type") == "open" and r.get("status") == "open":
            if not any(c.get("experiment") == r["id"] for c in _records()
                       if c.get("type") in ("keep", "revert")):
                return r
    return None


def _cooldown_ok(now: float) -> bool:
    for c in _records():
        if c.get("type") in ("keep", "revert") and c.get("target") == KNOB:
            if now - float(c.get("ts_epoch", 0)) < COOLDOWN_HOURS * 3600:
                return False
    return True


def current_cap() -> int:
    try:
        from .calibration import get_calibration
        return int(get_calibration().get("learning", "max_active_directions",
                                         default=DEFAULT_CAP) or DEFAULT_CAP)
    except Exception:
        return DEFAULT_CAP


def _set_cap(value: int) -> None:
    from .calibration import get_calibration
    get_calibration().apply_tuning([{"target": KNOB, "old": current_cap(), "new": int(value)}])


def open_experiment(reason: str, baseline: Optional[float] = None,
                    step: int = 1, log: Callable[[str], None] = print) -> Optional[str]:
    """开一个实验：方向数 -step。返回实验 id；不满足条件返回 None。"""
    now = time.time()
    if active() is not None:
        return None
    if not _cooldown_ok(now):
        return None
    old = current_cap()
    new = max(MIN_CAP, old - step)
    if new >= old:
        log(f"  🧪 [Experiment] 已到下限 {MIN_CAP}，不再收紧")
        return None
    eid = uuid.uuid4().hex[:10]
    _set_cap(new)
    _append({
        "type": "open", "id": eid, "status": "open", "target": KNOB,
        "old": old, "new": new, "baseline": baseline, "reason": reason,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": now,
        "due_epoch": now + TTL_HOURS * 3600,
    })
    log(f"  🧪 [Experiment] open id={eid}: {KNOB} {old} → {new} "
        f"(baseline={baseline}, reason={reason})")
    return eid


def close_experiment(eid: str, after: Optional[float], log: Callable[[str], None] = print) -> str:
    """按数据判成败：提升 ≥10% → keep；否则 revert。返回 verdict。"""
    exp = next((r for r in _records() if r.get("id") == eid and r.get("type") == "open"), None)
    if exp is None:
        return "missing"
    base = exp.get("baseline")
    improved = (after is not None and base not in (None, 0)
                and after >= float(base) * (1 + IMPROVE_RATIO))
    verdict = "keep" if improved else "revert"
    if verdict == "revert":
        _set_cap(int(exp["old"]))
    else:
        # 保留当前值，但把 baseline 更新为 after，便于下轮再降
        pass
    _append({
        "type": verdict, "experiment": eid, "target": KNOB,
        "baseline": base, "after": after, "value": exp["new"] if improved else exp["old"],
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": time.time(),
    })
    log(f"  🧪 [Experiment] {verdict} id={eid}: baseline={base} after={after} "
        f"→ {KNOB}={exp['new'] if improved else exp['old']}")
    return verdict


def tick(report, rate_fn: Callable[[int], Optional[float]],
         log: Callable[[str], None] = print) -> None:
    """每轮自省后调用：先处理到期实验，再决定是否开新实验。

    report: MetaCogReport（看 success_rate 是否触发）
    rate_fn(window_hours) -> Optional[float]：条目/轮产出率
    """
    try:
        from .calibration import get_calibration
        if not bool(get_calibration().get("interventions", "enabled", default=True)):
            return
    except Exception:
        pass

    now = time.time()
    exp = active()
    if exp and now >= float(exp.get("due_epoch", 0)):
        close_experiment(exp["id"], rate_fn(24), log=log)
        return
    if exp:
        return

    # 只有 success_rate 信号真的触发（score≥0.5）才动手
    fired = [t for t in getattr(report, "triggers", [])
             if getattr(t, "triggered", False) and getattr(t, "signal_name", "") == "success_rate"]
    if not fired or max(float(getattr(t, "score", 0)) for t in fired) < 0.5:
        return
    open_experiment(reason=getattr(fired[0], "reason", "success_rate low"),
                    baseline=rate_fn(24), log=log)
