"""
Aelvoxim learner worker — standalone process for the background learning loop.

Runs the Learner (cognitive cycle) in its own process, fully isolated from the
9701 API process so background LLM calls can never compete with user chat
requests for the GIL, network, or model channel.

Deployed as systemd unit aelvoxim-learner.service:
    ExecStart=/opt/aelvoxim/venv/bin/python3 -B /opt/aelvoxim/learn_worker.py

Logs go to the learner log (LEARNER_DIR/learner.log) plus stderr
(captured by systemd journal).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    from aelvoxim.learn.loop import get_learner, LEARNER_DIR, LOG_FILE

    LEARNER_DIR.mkdir(parents=True, exist_ok=True)
    (LEARNER_DIR / "enabled.flag").touch()

    learner = get_learner()
    if not learner:
        print("learner unavailable", file=sys.stderr)
        return 1

    if learner._directions is None or not learner._directions:
        learner._log("⚠️ No directions yet — worker idle-waits for directions")

    learner.start()
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
    while True:
        time.sleep(30)
        if not learner._thread or not learner._thread.is_alive():
            learner._log("🚨 Learner thread died — worker exiting for systemd restart")
            return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("learn worker stopped", file=sys.stderr)
        sys.exit(0)
