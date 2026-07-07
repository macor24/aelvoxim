"""aelvoxim.learn.learner — Backward-compatible re-export

This module was the original 1969-line monolith. It has been split into:
  - loop.py       — Learner main class
  - direction.py  — LearningDirection + DirectionManager
  - report.py     — Status logging + daily brain report
  - goals.py      — Active goal system
  - cleanup.py    — Memory + KB cleanup
  - discovery.py  — Direction discovery
  - scheduler.py  — Spaced repetition + pending promotion
  - meta_cog.py   — Meta-cognition reflection + repair verification

All external imports (from aelvoxim.learn.learner import ...) still work.
"""

from .loop import Learner, get_learner, LEARNER_DIR, LOG_FILE, STATUS_FILE, CONFIG_FILE, TASK_DECOMPOSE_CATEGORIES
from .direction import LearningDirection
