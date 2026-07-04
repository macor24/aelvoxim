"""aelvoxim.learn.report — Status logging, daily brain report

Split from learner.py (1969-line monolith).
Responsibility: learner log rotation, daily brain markdown report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import METACORE_DIR, DATA_DIR

# ── Data paths ──
LOG_FILE = METACORE_DIR / "learner" / "learner.log"
_REPORTS_DIR = DATA_DIR / "reports" / "daily"


def log(msg: str):
    """Write timestamped log to file + stdout. Auto-rotate at 5MB."""
    log_line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
                bak = LOG_FILE.with_suffix(".log.1")
                if bak.exists():
                    bak.unlink()
                LOG_FILE.rename(bak)
        except Exception:
            pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
    except Exception:
        pass
    print(log_line)


def update_daily_brain_report(cognition_report, direction_manager, learner_ref) -> None:
    """Write or update the daily brain report markdown file.

    Called from cognition_tick. Produces a concise status snapshot
    that can be served via /v1/brain/report.
    """
    try:
        from ..core.selfmodel import SelfModel

        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        report_file = _REPORTS_DIR / f"{today}.md"

        sm = SelfModel()
        caps = sm._capabilities

        # Gather data
        bc = caps.get("belief_health")
        belief_rate = f"{bc.success_rate:.0%}" if bc else "N/A"
        grade_result = sm.overall_grade()
        grade_label = grade_result.get("grade", "N/A") if isinstance(grade_result, dict) else str(grade_result)

        active_dirs = direction_manager.active_count()
        trig_count = sum(
            1 for t in (cognition_report.get("triggers", []) or [])
            if getattr(t, "triggered", False)
        )

        goals = getattr(learner_ref, "_active_goals", [])
        active_goals = [g for g in goals if g.status == "active"]
        goal_lines = "\n".join(
            f"- {g.description} ({g.current_value:.0%}/{g.target_value:.0%})"
            for g in active_goals[:5]
        )

        content = (
            f"# Daily Brain Report — {today}\n\n"
            f"## Self State\n"
            f"- Reasoning quality: Grade {grade_label}\n"
            f"- Belief health: {belief_rate}\n"
            f"- Active learning directions: {active_dirs}\n"
            f"- Cognitive signals triggered: {trig_count}\n\n"
            f"## Goal Tracking\n"
            f"{goal_lines or 'No active goals'}\n\n"
            f"## Security\n"
            f"- SentriKit: offline (local mode active)\n\n"
            f"*Auto-generated at {datetime.now().strftime('%H:%M')}*\n"
        )

        tmp = report_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(report_file)
    except Exception:
        pass
