#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dgmh_review.py —— 演化变更的人工判分/放行入口（无 LLM 参与）。

闸门 `gate_changes` 需要 judge_grade ≥ A 才能放行 update。没有自动判分器时，
人就是判分器：本工具把"放行"写成一次性令牌（24h、target+new 必须吻合），
并在真正应用时写历史、留可回滚记录。

用法（生产：/opt/aelvoxim/venv/bin/python3 /opt/aelvoxim/dgmh_review.py ...）：
  status                          激活等级 + 待审数量
  list                            待审队列 + 最近决策
  approve --target T [--why W] [--by NAME] [--apply]
                                  放行（--apply 立即写入配置并记账）
  reject  --target T --why W      否决（打冷却，本条不再进队列）
  revert  --id ID                 按历史把某个 target 回滚到旧值
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/opt/aelvoxim/src")
sys.dont_write_bytecode = True
os.environ.setdefault("AELVOXIM_EDITION", "enterprise")
# 注：生产 /root/.aelvoxim 是指向 /var/aelvoxim/data/.aelvoxim 的符号链接，
# 默认路径解析就是对的；不要用 AELVOXIM_DATA_DIR 去"纠正"它（会指到父目录）。

from aelvoxim.core import dgmh  # noqa: E402
from aelvoxim.utils import METACORE_DIR  # noqa: E402

UNGRADED = METACORE_DIR / "dgmh_ungraded.jsonl"
DECISIONS = Path(dgmh.DECISIONS_FILE)
HISTORY = METACORE_DIR / "dgmh_history.jsonl"
ACTIVATION = Path(dgmh.ACTIVATION_FILE)


def _jsonl(p: Path):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append(p: Path, rec: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pending() -> list:
    """待审 = 进过 ungraded、但没有对应 approve/reject 的最新条目。"""
    decided = {(d.get("target"), d.get("new")) for d in _jsonl(DECISIONS)
               if d.get("type") in ("approve", "reject")}
    seen, out = set(), []
    for rec in reversed(_jsonl(UNGRADED)):
        key = (rec.get("target"), rec.get("new"))
        if key in decided or key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def cmd_status(_a):
    act = dgmh.load_activation()
    print("激活状态:", json.dumps(act.to_dict(), ensure_ascii=False))
    print("激活文件:", str(ACTIVATION), "存在" if ACTIVATION.exists() else "不存在（默认 Level 1）")
    print("待审:", len(pending()), "| 决策:", len(_jsonl(DECISIONS)), "| 历史:", len(_jsonl(HISTORY)))


def cmd_list(_a):
    p = pending()
    print(f"待审 {len(p)} 条：")
    for r in p:
        print(f"  {r.get('ts')}  {r.get('target')}  {r.get('old')} → {r.get('new')}"
              f"   ({r.get('reason', '')[:50]})")
    if not p:
        print("  （空）")
    ds = _jsonl(DECISIONS)[-5:]
    if ds:
        print("最近决策：")
        for d in ds:
            print(f"  [{d.get('type')}] {d.get('target')} → {d.get('new')} "
                  f"grade={d.get('grade')} by={d.get('by')} {d.get('ts')}")


def _target_entry(target: str) -> dict:
    latest = None
    for r in _jsonl(UNGRADED):
        if r.get("target") == target:
            latest = r
    if latest is None:
        raise SystemExit(f"✗ 队列里没有 target={target}")
    return latest


def cmd_approve(a):
    rec = _target_entry(a.target)
    did = dgmh.record_decision("approve", a.target, rec.get("new"), grade=a.grade,
                               by=a.by, why=a.why or rec.get("reason", ""),
                               ttl_hours=a.ttl)
    print(f"✓ 已放行 {a.target}: {rec.get('old')} → {rec.get('new')} "
          f"(grade={a.grade}, by={a.by}, id={did}, {a.ttl}h 有效)")
    if not a.apply:
        print("  未写入配置（加 --apply 才生效）")
        return
    apply_now(rec, did, a)


def apply_now(rec: dict, decision_id: str, a):
    from aelvoxim.core.calibration import get_calibration
    cal_path = METACORE_DIR / "calibration.json"
    before = hashlib.sha256(cal_path.read_bytes()).hexdigest() if cal_path.exists() else ""
    cal = get_calibration()
    n = cal.apply_tuning([{"target": rec["target"], "old": rec.get("old"), "new": rec["new"]}])
    after = hashlib.sha256(cal_path.read_bytes()).hexdigest() if cal_path.exists() else ""
    if n == 0:
        print("✗ 写入失败（apply_tuning 返回 0）")
        return
    _append(HISTORY, {
        "id": decision_id, "target": rec["target"], "old": rec.get("old"),
        "new": rec.get("new"), "by": a.by, "why": a.why or rec.get("reason", ""),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": time.time(),
        "hash_before": before[:16], "hash_after": after[:16], "type": "apply",
    })
    dgmh.consume_decision(rec["target"], rec.get("new"))
    print(f"✓ 已写入配置: {rec['target']} = {rec['new']}  (配置哈希 {before[:8]} → {after[:8]})")
    print("  回滚: dgmh_review.py revert --id " + decision_id)


def cmd_reject(a):
    rec = _target_entry(a.target)
    did = dgmh.record_decision("reject", a.target, rec.get("new"), grade="D",
                               by=a.by, why=a.why)
    print(f"✓ 已否决 {a.target} (id={did}): {a.why}")


def cmd_revert(a):
    hist = [h for h in _jsonl(HISTORY) if h.get("id") == a.id and h.get("type") == "apply"]
    if not hist:
        raise SystemExit(f"✗ 历史里没有 id={a.id}")
    h = hist[-1]
    from aelvoxim.core.calibration import get_calibration
    cal = get_calibration()
    cal.apply_tuning([{"target": h["target"], "old": h.get("new"), "new": h.get("old")}])
    _append(HISTORY, {"id": a.id, "type": "revert", "target": h["target"],
                      "old": h.get("new"), "new": h.get("old"), "by": a.by,
                      "why": "manual revert",
                      "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "ts_epoch": time.time()})
    print(f"✓ 已回滚 {h['target']}: {h.get('new')} → {h.get('old')}")


def cmd_activate(a):
    """写激活文件（明确的、留痕的开关；不是代码里的默认值）。"""
    cur = dgmh.load_activation().to_dict()
    new = dict(cur)
    for flag in ("judgestored", "user_authorized", "safetyguard_stable", "success_trails_ready"):
        if getattr(a, flag):
            new[flag] = True
    for flag in ("no_judgestored",):
        if getattr(a, flag):
            new[flag.replace("no_", "")] = False
    new["updated_by"] = a.by
    new["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new["reason"] = a.why
    ACTIVATION.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print("激活文件已写:", str(ACTIVATION))
    print("  旧:", json.dumps(cur, ensure_ascii=False))
    print("  新:", json.dumps(new, ensure_ascii=False))
    act = dgmh.load_activation()
    for lvl in (1, 2, 3):
        ok, why = act.check_level(lvl)
        print(f"  Level {lvl}: {'可用' if ok else '不可用'} — {why}")


def main():
    ap = argparse.ArgumentParser(prog="dgmh_review")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("list").set_defaults(fn=cmd_list)

    p = sub.add_parser("approve")
    p.add_argument("--target", required=True)
    p.add_argument("--grade", default="A", choices=["S", "A", "B", "C", "D"])
    p.add_argument("--by", default=os.environ.get("DG_REVIEWER", "operator"))
    p.add_argument("--why", default="")
    p.add_argument("--ttl", type=float, default=24.0)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_approve)

    r = sub.add_parser("reject")
    r.add_argument("--target", required=True)
    r.add_argument("--why", required=True)
    r.add_argument("--by", default=os.environ.get("DG_REVIEWER", "operator"))
    r.set_defaults(fn=cmd_reject)

    v = sub.add_parser("revert")
    v.add_argument("--id", required=True)
    v.add_argument("--by", default=os.environ.get("DG_REVIEWER", "operator"))
    v.set_defaults(fn=cmd_revert)

    ac = sub.add_parser("activate")
    ac.add_argument("--judgestored", action="store_true", help="判分来源已存在（Level 2 前提）")
    ac.add_argument("--no-judgestored", action="store_true", help="关掉 Level 2")
    ac.add_argument("--user-authorized", action="store_true")
    ac.add_argument("--safetyguard-stable", action="store_true")
    ac.add_argument("--success-trails-ready", action="store_true")
    ac.add_argument("--by", default=os.environ.get("DG_REVIEWER", "operator"))
    ac.add_argument("--why", required=True, help="为什么开/关（写进文件）")
    ac.set_defaults(fn=cmd_activate)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
