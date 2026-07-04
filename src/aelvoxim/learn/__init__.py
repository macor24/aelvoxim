"""aelvoxim.learn — Self-learning agent package

Re-exports from split modules for backward compatibility.
The original learner.py (1969 lines) has been split into:
  - loop.py       — Learner main class (orchestration)
  - direction.py  — LearningDirection dataclass + DirectionManager
  - report.py     — Status logging + daily brain report
  - goals.py      — Active goal system
  - cleanup.py    — Memory + KB cleanup
  - discovery.py  — Direction discovery
  - scheduler.py  — Spaced repetition + pending promotion
  - meta_cog.py   — Meta-cognition reflection + repair verification
"""

from .loop import Learner, get_learner, LEARNER_DIR
from .direction import LearningDirection, DirectionManager, load_config_from_file, save_config_to_file
from .report import log
from .knowledge import KnowledgeBase
