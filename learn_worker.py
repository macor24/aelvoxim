"""
Aelvoxim learner worker — standalone process for the background learning loop.

Runs the Learner (cognitive cycle) in its own process, fully isolated from the
9701 API process so background LLM calls can never compete with user chat
requests for the GIL, network, or model channel.

Deployed as systemd unit aelvoxim-learner.service:
    ExecStart=/opt/aelvoxim/venv/bin/python3 -B /opt/aelvoxim/learn_worker.py

Logs go to the learner log (LEARNER_DIR/learner.log) plus stderr
(captured by systemd journal).

P0-4（2026-10-30）：管理面的 start/stop 不再作用于 API 进程内的 Learner 单例，
改为写 LEARNER_DIR/control.json；本进程每 5s 轮询一次并执行，执行完写
control_ack.json。进程本身不退出，保证还能再 start。启动时 flock learner.pid，
抢不到锁直接退出——防止两个 Learner 同时写同一批 JSON。
"""

import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONTROL_POLL_S = 5


def apply_control(learner, ctrl: dict, last_id: str):
    """执行一条控制指令。返回 (已处理 id, 结果字符串)。

    结果取值：skip（没有新指令）/ stopped / started / start_rejected / unknown / error:…
    幂等：同一条 id 只执行一次（last_id 由调用方保存）。
    """
    cmd_id = str((ctrl or {}).get("id") or "")
    if not cmd_id or cmd_id == last_id:
        return last_id, "skip"
    cmd = (ctrl or {}).get("cmd")
    try:
        if cmd == "stop":
            learner.stop()
            return cmd_id, "stopped"
        if cmd == "start":
            learner.start()
            running = bool(getattr(learner, "_running", False))
            return cmd_id, ("started" if running else "start_rejected")
        return cmd_id, "unknown"
    except Exception as exc:  # 指令执行失败不能弄死 worker
        return cmd_id, f"error:{type(exc).__name__}:{exc}"[:200]


def main() -> int:
    from aelvoxim.learn.loop import (
        get_learner, LEARNER_DIR,
        read_learner_control, write_learner_control_ack,
    )

    LEARNER_DIR.mkdir(parents=True, exist_ok=True)
    (LEARNER_DIR / "enabled.flag").touch()

    # 单实例锁：两个 Learner 同时写同一批 JSON 会把状态写花
    _lock_path = LEARNER_DIR / "learner.pid"
    _lock_fh = open(_lock_path, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"another learner worker holds {_lock_path} — exiting", file=sys.stderr)
        return 3
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()

    learner = get_learner()
    if not learner:
        print("learner unavailable", file=sys.stderr)
        return 1

    if learner._directions is None or not learner._directions:
        learner._log("⚠️ No directions yet — worker idle-waits for directions")

    learner.start()
    if not getattr(learner, "_running", False):
        learner._log("⚠️ learner.start() 未能启动（版本开关或没有方向）")
    learner._log("🔄 Learn worker started (standalone process)")

    # Cortex scheduler belongs here too — its _submit_task() needs the
    # in-process learner instance to dispatch planner actions.
    try:
        from aelvoxim.cortex.scheduler import Scheduler
        from aelvoxim.planner import LongTermPlanner
        _cortex_scheduler = Scheduler(planner=LongTermPlanner())
        _cortex_scheduler.start()
        learner._log("🔄 Cortex scheduler started in learn worker")
    except Exception as e:
        learner._log(f"⚠️ Cortex scheduler failed to start: {e}")

    # Keep process alive; the learner runs its own thread. If the loop thread
    # dies permanently, exit so systemd can restart us cleanly.
    _last_cmd_id = ""
    while True:
        time.sleep(CONTROL_POLL_S)

        ctrl = read_learner_control()
        if ctrl:
            _last_cmd_id, _result = apply_control(learner, ctrl, _last_cmd_id)
            if _result != "skip":
                write_learner_control_ack(str(ctrl.get("id") or ""),
                                          str(ctrl.get("cmd") or ""), _result)
                learner._log(f"  🎛️ control {ctrl.get('cmd')} → {_result}")

        try:
            learner._save_status()
        except Exception:
            pass

        if not learner._thread or not learner._thread.is_alive():
            learner._log("🚨 Learner thread died — worker exiting for systemd restart")
            return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("learn worker stopped", file=sys.stderr)
        sys.exit(0)
