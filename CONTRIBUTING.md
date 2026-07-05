# Contributing to Aelvoxim

Thank you for your interest in Aelvoxim!

## Quick Start

```bash
git clone https://github.com/gmxchz/aelvoxim
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

## Development

```bash
pip install -e .[dev]  # pytest
python -m pytest tests/ -v
```

## Code Style

- **Stdlib-first** — Only bcrypt, fastapi, uvicorn as external deps
- **English-only** — Identifiers, comments, and docstrings in English
- **Type hints** — Required for public APIs
- **Docstrings** — Every public function needs one
- **Exception handling** — Every `except` must be justified

## Architecture

```
aelvoxim/
├── core/         Cognitive engine (belief/metacog/reasoner/judge/calibration)
├── learn/        Learning engine (learner/knowledge base/validator/search)
├── memory/       Memory system (4 layers/fusion/decay/conflict/scoring)
├── server/       API server (FastAPI routes/auth/chat pipeline/sessions)
├── cortex/       Brain cortex (router/scheduler)
├── experts/      Expert system (7+ experts/orchestrator/sub-process mgr)
├── storage/      Storage (SQLite/PostgreSQL dual-mode)
├── utils/        Helpers (i18n, paths, JSON)
└── edition.py    Edition gating (community/pro config control)
```

## Adding a New Expert

1. Create `aelvoxim/experts/my_expert.py`
2. Inherit `BaseExpert`, implement `run(inp) -> ExpertOutput`
3. Add `@register` decorator and `_capabilities` list
4. Add import in `experts/__init__.py`
5. Expert auto-registers — no orchestrator changes needed

```python
from .base import BaseExpert, ExpertInput, ExpertOutput, register

@register
class MyExpert(BaseExpert):
    _capabilities = ["my_domain", "specialized_skill"]
    name = "my"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Your logic here
        return ExpertOutput(
            expert_name=self.name,
            opinion="...",
            confidence=0.8,
        )
```

## Edition Gating

Aelvoxim uses a single codebase with edition gating:

- **Community**: `edition="community"` — manual learning, 5 core experts
- **Pro**: `edition="pro"` — auto learning, all 12 experts

Set via environment variable `AELVOXIM_EDITION` or license key.

## Pull Request Process

1. One feature per PR
2. New features must include tests
3. Update README if API changes
4. Run `pytest tests/ -v` before submitting
5. Pass `python -c "from aelvoxim import __version__"` import check
