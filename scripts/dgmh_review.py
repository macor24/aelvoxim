#!/usr/bin/env python3
"""DGM-H 人工评审 CLI（2026-11-04）

背景：`core/dgmh.py` 里 `record_decision()` 负责发放"人工放行令牌"，但全仓没有任何调用方，
所以 `find_decision()` 永远返回 None → 每条 auto-tune 提案都进 ungraded 队列 + 冷却，
evolve 分支被结构性卡住。这个脚本就是缺的那个入口。

用法（在生产上用 venv 的 python 跑）：
  dgmh_review.py list                      # 只读：列队列 + 当前值 + 冷却 + 该给什么等级
  dgmh_review.py approve 2 --grade A --by gmxchz --why "hit_rate 长期 9%，抬阈值合理"
  dgmh_review.py reject  3 --by gmxchz --why "阈值应保持 0.1，先修指标"
  dgmh_review.py status                    # 只读：激活等级 / 阈值 / 冷却

设计约束：
- 默认只读；approve/reject 必须显式给编号，且带 --id/--by/--why（留痕）。
- 令牌是一次性 + 24h 有效（沿用 dgmh 的既有语义），所以 approve 会顺带清掉该 target 的冷却，
  否则下一轮 auto-tune 仍在冷却期内、根本走不到闸门。
- 不改任何阈值本身：阈值只在闸门放行后由 auto-tune 自己写，符合"阈值必须脚本产出 + 人确认"。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

from aelvoxim.core import dgmh  # noqa: E402


def _queue_path() -> Path:
    return dgmh._ungraded_path()


def _read_queue():
    p = _queue_path()
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        out.append((i, d))
    return out


def _voided(records):
    """返回被作废的提案键集合。

    作废要按 ref_ts 精确指向某一条，而不是按 (target, new) —— 10-19 那条 void 是针对
    "探针用合成 hit_rate=0.0 算出的伪建议"，它的 target/new 与后来基于真实 hit_rate 的
    提案完全相同；按 (target,new) 去重会把后来 5 条真实提案一起误杀（第一版就是这个 bug）。
    没有 ref_ts 的旧 void 记录才退化为按 (target, new) 处理。
    """
    dead_refs = set()
    dead_pairs = set()
    for _, d in records:
        if not (d.get("void") or d.get("supersede")):
            continue
        if d.get("ref_ts"):
            dead_refs.add(str(d.get("ref_ts")))
        else:
            dead_pairs.add((str(d.get("target")), str(d.get("new"))))
    return dead_refs, dead_pairs


def _is_dead(d, dead):
    dead_refs, dead_pairs = dead
    return (str(d.get("ts")) in dead_refs
            or (str(d.get("target")), str(d.get("new"))) in dead_pairs)


def _live_value(target: str):
    """读 calibration.json 里该 target 的当前值（target 形如 metacog.evolve_threshold）。"""
    try:
        from aelvoxim.core.calibration import get_calibration
        cal = get_calibration()
        sec, _, key = target.partition(".")
        if not key:
            return None
        cur = cal.get(sec, key)
        return cur
    except Exception as e:
        return f"<读取失败: {type(e).__name__}>"


def _cooldown_left(target: str) -> str:
    try:
        ts = (dgmh._load_cooldown() or {}).get(target)
        if not ts:
            return "无"
        left = dgmh.COOLDOWN_HOURS * 3600 - (time.time() - float(ts))
        if left <= 0:
            return "已过"
        return f"{int(left // 60)} 分钟后到期"
    except Exception:
        return "?"


def _activation_line():
    try:
        a = dgmh.load_activation()
        return (f"L1={a.phase1_ready} L2(judgestored)={a.judgestored} "
                f"L3(user_authorized+safetyguard_stable+success_trails)="
                f"{a.user_authorized and a.safetyguard_stable and a.success_trails_ready}")
    except Exception as e:
        return f"<读取失败: {type(e).__name__}>"


def cmd_list(args) -> int:
    recs = _read_queue()
    dead = _voided(recs)
    print(f"队列文件: {_queue_path()}  （{len(recs)} 条记录）")
    print(f"激活状态: {_activation_line()}")
    print()
    live_props = []
    for lineno, d in recs:
        if d.get("void") or d.get("supersede"):
            continue                      # 标记记录本身不是提案
        if _is_dead(d, dead):
            continue
        live_props.append((lineno, d))
    if not live_props:
        print("没有待评审的提案。")
        return 0
    print(f"{'#':>3}  {'时间':<20} {'target':<26} {'old → new':<16} {'当前值':<10} 冷却")
    for lineno, d in live_props:
        cur = _live_value(str(d.get("target") or ""))
        print(f"{lineno:>3}  {str(d.get('ts'))[:19]:<20} {str(d.get('target'))[:26]:<26} "
              f"{str(d.get('old'))} → {str(d.get('new')):<8} {str(cur):<10} {_cooldown_left(str(d.get('target') or ''))}")
        print(f"      原因: {d.get('reason')}   未分级原因: {d.get('why_ungraded')}")
        # 预估这条提案要什么条件才能过闸（update 类最低 A，故先试 A 再试 S）
        try:
            need = None
            last = ""
            for g in ("A", "S"):
                ok, why = dgmh.check_gate("update", g, dgmh.load_activation())
                if ok:
                    need = g
                    print(f"      最低可用等级: {g}（{why}）")
                    break
                last = why
            if need is None:
                print(f"      任何等级都过不去: {last}")
        except Exception as e:
            print(f"      闸门预估失败: {type(e).__name__}: {e}")
        print()
    return 0


def _clear_cooldown(target: str) -> None:
    try:
        p = dgmh._cooldown_path()
        data = dgmh._load_cooldown() or {}
        if target in data:
            data.pop(target)
            p.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ 清冷却失败: {type(e).__name__}: {e}")


def _pick(lineno: int, want: str):
    for n, d in _read_queue():
        if n == lineno:
            if d.get("void") or d.get("supersede"):
                sys.exit(f"编号 {lineno} 是作废标记，不是提案")
            new = d.get("new")
            if want and str(new) != str(want):
                sys.exit(f"编号 {lineno} 的 new 是 {new}，与 --new {want} 不符，拒绝执行")
            return d
    sys.exit(f"队列里没有编号 {lineno}")


def cmd_approve(args) -> int:
    d = _pick(args.index, args.new)
    target, new = str(d.get("target") or ""), d.get("new")
    if args.grade not in ("S", "A"):
        sys.exit("update 类变更最低要求 A（EVOLUTION_GATES），--grade 只能是 A 或 S")
    ok, why = dgmh.check_gate("update", args.grade, dgmh.load_activation())
    if not ok:
        print(f"⚠ 现在放行也不会生效：{why}")
        if not args.force:
            sys.exit("（加 --force 仍写令牌，但闸门会继续拦）")
    if args.dry_run:
        print(f"[dry-run] 会写入 approve: {target} {new} grade={args.grade} by={args.by}")
        return 0
    did = dgmh.record_decision("approve", target, new, grade=args.grade,
                               by=args.by, why=args.why)
    print(f"✅ 已写入放行令牌 id={did}  target={target} new={new} grade={args.grade}")
    _clear_cooldown(target)
    print(f"   已清掉 {target} 的冷却 → 下一轮 auto-tune 尝试时放行（令牌一次性、24h 内有效）")
    print("   阈值本身不在这里改：由 auto-tune 在闸门放行后自己写。")
    return 0


def cmd_reject(args) -> int:
    d = _pick(args.index, args.new)
    target, new = str(d.get("target") or ""), d.get("new")
    if args.dry_run:
        print(f"[dry-run] 会写入 reject + 作废标记: {target} {new}")
        return 0
    did = dgmh.record_decision("reject", target, new, grade="D", by=args.by, why=args.why)
    # 作废标记沿用 2026-10-19 那条记录的格式，便于 list 跳过
    rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "void": True,
           "target": target, "new": new, "ref_ts": d.get("ts"),
           "note": f"人工否决（{args.by}）：{args.why}"}
    with _queue_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _clear_cooldown(target)
    print(f"✅ 已否决 id={did}  target={target} new={new}；队列已标 void，冷却已清")
    return 0


def cmd_status(args) -> int:
    print("激活状态:", _activation_line())
    print("冷却:", _cooldown_left("metacog.evolve_threshold"))
    print("当前 evolve_threshold:", _live_value("metacog.evolve_threshold"))
    print("队列有效提案数:", len([1 for n, d in _read_queue()
                                  if not _is_dead(d, _voided(_read_queue()))]))
    p = dgmh.DECISIONS_FILE
    print("决策令牌文件:", p, "存在" if p.exists() else "不存在（从未写过任何放行/否决）")
    if p.exists():
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        print(f"  共 {len(lines)} 条，最近 3 条:")
        for ln in lines[-3:]:
            try:
                dd = json.loads(ln)
                print("   ", dd.get("type"), dd.get("target"), dd.get("new"),
                      dd.get("grade"), dd.get("ts"))
            except Exception:
                pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DGM-H 人工评审（默认只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出待评审提案（只读）")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("status", help="激活/阈值/令盘状态（只读）")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("approve", help="给某条提案写放行令牌")
    p.add_argument("index", type=int)
    p.add_argument("--grade", default="A", choices=["S", "A"])
    p.add_argument("--by", required=True)
    p.add_argument("--why", default="")
    p.add_argument("--new", default="", help="校验用：期望的 new 值")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("reject", help="否决某条提案并作废")
    p.add_argument("index", type=int)
    p.add_argument("--by", required=True)
    p.add_argument("--why", default="")
    p.add_argument("--new", default="")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_reject)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
