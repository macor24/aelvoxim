#!/usr/bin/env python3
"""Aelvoxim 一键只读体检（2026-11-04）。只读，不写任何东西。

判据（全绿才 exit 0）：
  C1 三服务 active + /v1/health 200
  C2 知识库 topic 全部过 topic_gate（kb_rows_failing_gate == 0，含 plan_title payload 豁免）
  C3 curiosity 队列里的 topic 全部过闸（脏题不许再进队列）
  C4 LLM 空回复率 = 0 或 < 5%（读 status.json 的 llm 段；调用 < 20 次时只报数不判）
  C5 learner 心跳新鲜（updated_at < 180min）且 loop_count 在动
用法:
  python3 readout.py            # 打印六行结论
  python3 readout.py --verbose  # 附带详情
"""
import json
import re
import subprocess
import sys
import importlib.util
from datetime import datetime
from pathlib import Path

V = "--verbose" in sys.argv
DATA = Path("/var/aelvoxim/data/.aelvoxim")
STATUS = DATA / "learner" / "status.json"
CUR = DATA / "curiosity.json"
K = DATA / "knowledge"

spec = importlib.util.spec_from_file_location("tg", "/opt/aelvoxim/src/aelvoxim/learn/topic_gate.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)
_PLAN = re.compile(r"^(已完成计划|完成计划)\s*[:：]?\s*")

fails = []


def passes(topic):
    ok, reason = tg.check(topic or "")
    if ok:
        return True, reason
    if reason == "plan_title":
        return tg.check(_PLAN.sub("", topic or ""))[0], "plan_title_with_payload"
    return False, reason


def say(tag, ok, text):
    print(f"{'✅' if ok else '❌'} {tag}  {text}")
    if not ok:
        fails.append(tag)


def main():
    # C1
    bad = []
    for s in ("aelvoxim-api", "aelvoxim-chatael", "aelvoxim-learner"):
        st = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True).stdout.strip()
        if st != "active":
            bad.append(f"{s}={st}")
    code = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                           "http://127.0.0.1:9701/v1/health"],
                          capture_output=True, text=True).stdout.strip()
    say("C1", not bad and code == "200", f"services={'ok' if not bad else bad} health={code}")

    # C2
    out = subprocess.run(["su", "postgres", "-c",
                          'psql -d aelvoxim -t -A -F"|" -c "SELECT topic, count(*) FROM knowledge_entries GROUP BY 1;"'],
                         capture_output=True, text=True).stdout
    rows = [ln.rsplit("|", 1) for ln in out.splitlines() if ln.strip()]
    dirty = []
    for row in rows:
        if len(row) != 2:
            continue
        t, n = row[0], row[1]
        ok, _reason = passes(t)
        if not ok:
            dirty.append((t, int(n)))
    ndirty = sum(n for _, n in dirty)
    say("C2", ndirty == 0, f"kb_rows_failing_gate={ndirty} topics={len(dirty)}")
    if V:
        for t, n in dirty[:8]:
            print("    ", n, repr(t[:50]))

    # C3
    try:
        cur = json.loads(CUR.read_text(encoding="utf-8")) if CUR.exists() else []
        badc = [it.get("topic", "") for it in cur if isinstance(it, dict) and not passes(it.get("topic", ""))[0]]
        say("C3", not badc, f"curiosity_queue={len(cur)} dirty={len(badc)}")
        if V:
            for t in badc[:5]:
                print("    ", repr(t[:50]))
    except Exception as e:
        say("C3", False, f"curiosity 读失败: {e}")

    # C4
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
        llm = st.get("llm") or {}
        calls, empty = int(llm.get("calls") or 0), int(llm.get("empty") or 0)
        rate = (empty / calls) if calls else 0.0
        ok = (empty == 0) or (rate < 0.05)
        say("C4", ok, f"llm calls={calls} empty={empty} rate={rate:.1%}"
                      + ("" if calls >= 20 else " (样本<20，仅报数)"))
        if V and llm.get("by_site"):
            for k, v in sorted(llm["by_site"].items(), key=lambda kv: -kv[1])[:5]:
                print("    ", v, k)
    except Exception as e:
        say("C4", False, f"status.json 读失败: {e}")

    # C5
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
        ts = datetime.strptime(st["updated_at"][:19], "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - ts).total_seconds() / 60
        say("C5", age < 180, f"heartbeat={age:.0f}min loops={st.get('loop_count')} "
                             f"dirs={st.get('direction_count')} gate={st.get('kb_topic_gate')}")
    except Exception as e:
        say("C5", False, f"心跳读失败: {e}")

    # C6 依赖漂移（声明里钉住的版本 vs 实际装的版本）
    try:
        _r = subprocess.run([sys.executable, str(Path(__file__).with_name("dep_drift.py"))],
                            capture_output=True, text=True, timeout=90)
        _lines = [ln for ln in _r.stdout.strip().splitlines() if ln.strip()]
        say("C6", _r.returncode == 0, _lines[-1] if _lines else f"exit={_r.returncode}")
        if V:
            print(_r.stdout.strip())
    except Exception as e:
        say("C6", False, f"dep_drift 调用失败: {e}")

    print()
    print("结论:", "全绿" if not fails else f"{len(fails)} 项不通过 -> {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
