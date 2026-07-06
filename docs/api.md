# API Reference

## Command Line

```bash
python -m aelvoxim --help
python -m aelvoxim --lang zh           # Chinese interface
python -m aelvoxim learn add "Topic"   # Add learning direction
python -m aelvoxim learn start         # Start learning loop
python -m aelvoxim learn stop          # Stop learning loop
python -m aelvoxim learn status        # Show learner status
python -m aelvoxim ui --port 9700      # Start dashboard
python -m aelvoxim status              # Show system overview
```

## Python API

### Core

```python
from aelvoxim.core.selfmodel import SelfModel, DecisionEntry
sm = SelfModel()
sm.record_decision(DecisionEntry(decision_type="verify", task="test", outcome="pass"))
snap = sm.take_snapshot()  # SelfGraph with overall_success_rate

from aelvoxim.core.judge import RuleBasedJudge, Proposal, JudgeGrade
judge = RuleBasedJudge()
result = judge.evaluate(Proposal(id="p1", summary="test", change_type="update"))
print(result.grade, result.total_score)

from aelvoxim.core.dgmh import check_gate, ActivationStatus
valid, reason = check_gate("create", "B", ActivationStatus(judgestored=True))

from aelvoxim.core.metacog import MetaCogTrigger
trigger = MetaCogTrigger()
report = trigger.evaluate(success_rate_7d=0.8)
print(report.triggered, report.score)
```

### Learn

```python
from aelvoxim.learn.learner import Learner, get_learner

learner = get_learner()
learner.add_direction("FastAPI async optimization")
learner.start()
print(learner.is_running)
print(learner.get_status())
learner.stop()

from aelvoxim.learn.decompose import decompose_direction, detect_lang
print(detect_lang("FastAPI async"))  # "en"
tasks = decompose_direction("FastAPI async")  # 8 preset sub-tasks

from aelvoxim.learn.knowledge import KnowledgeBase
kb = KnowledgeBase()
entry = kb.store("topic", "title", "summary", source="manual")
found = kb.get_by_title("title")
all_active = list(kb.get_all_active())

from aelvoxim.learn.execute import try_execute_task
output = try_execute_task("test", "route design")  # Runs subprocess

from aelvoxim.learn.monitor import HealthMonitor
hm = HealthMonitor()
fixed = hm.tick()  # Returns list of fixes applied
```

### Memory

```python
from aelvoxim.memory import store, get, search, delete, list_keys
store("key", "value", ["tag1", "tag2"])
entry = get("key")
results = search("query", limit=5)
```

### Config

```python
from aelvoxim.api import get_config, set_config, list_config
set_config("llm.provider", "deepseek")
provider = get_config("llm.provider")
```

### Dashboard

```python
from aelvoxim.ui import serve
serve(port=9700)  # Starts HTTP server at http://127.0.0.1:9700
```
