# Aelvoxim

<p align="center">
  <img src="docs/Aelvoxim2.jpg" alt="Aelvoxim" width="600">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/aelvoxim)](https://pypi.org/project/aelvoxim/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-macor24%2Faelvoxim-181717?logo=github)](https://github.com/macor24/aelvoxim)

**Aelvoxim — Lightweight AI Cognitive Engine Framework.**

Give your AI application a "memory" — it remembers, reasons, learns, and has meta-cognition.

---

## Quick Start

```bash
# Install
pip install aelvoxim

# Start the server
aelvoxim server --port 9701

# Open in browser
open http://localhost:9701

# Or use the ChatAEL frontend (dedicated chat UI)
python serve_chatael.py
# Open http://localhost:9702
```

### From source

```bash
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

---

## Features

### 🧠 Memory System
Your AI remembers like a human — with layers, decay, and doubt.

| Capability | Description |
|------------|-------------|
| **4-layer memory** | Working / Episodic / Semantic / Procedural |
| **MemoryFusion** | Inverted index + multi-layer retrieval |
| **Forgetting curve** | Knowledge decays naturally (Ebbinghaus) |
| **Conflict detection** | Finds contradictions, alerts the user |
| **Confidence Matrix** | 5-dimensional trust score on every response |

### 🔧 Expert System
A team of specialized AI experts working together.

| Expert | Role | Community | Pro |
|--------|------|-----------|-----|
| LogicExpert | Rule reasoning, contradiction detection | ✅ | ✅ |
| MemoryExpert | Memory retrieval, fusion search | ✅ | ✅ |
| EthicsExpert | 15 ethical rules, transparent & auditable | ✅ | ✅ |
| SafetyExpert | Local keyword + regex safety checks | ✅ | ✅ |
| CodeReviewExpert | Code style, complexity, secret scanning | ✅ | ✅ |
| EmotionExpert | Sentiment analysis, empathetic response | ❌ | ✅ |
| CreativeExpert | Creative writing, content generation | ❌ | ✅ |
| IntrospectionExpert | Self-reflection, quality audit | ❌ | ✅ |
| HypothesisEngine | Root cause hypothesis & validation | ❌ | ✅ |

### 📚 Learning System

| Capability | Community | Pro |
|------------|-----------|-----|
| Manual learning (API trigger) | ✅ | ✅ |
| 7×24 auto-learning loop | ❌ | ✅ |
| Curiosity-driven discovery | ❌ | ✅ |
| Auto parameter tuning | ❌ | ✅ |
| Knowledge gap analysis | ❌ | ✅ |
| Post-validation audit | ❌ | ✅ |

### 🔌 API & Integration
- **RESTful API** — FastAPI, Swagger docs at `/docs`
- **SSE streaming** — Real-time token-by-token output
- **Multi-user auth** — Email/password registration + API Keys
- **Knowledge base CRUD** — Add, search, update, delete knowledge entries
- **Session management** — Persistent conversations with history

---

## Architecture

```
aelvoxim/
├── src/aelvoxim/
│   ├── core/         Cognitive engine (belief, metacog, reasoner, judge, DGM-H)
│   ├── learn/        Learning engine (learner, KB, validator, search, curiosity)
│   ├── memory/       Memory system (4 layers, fusion, decay, conflict, scoring)
│   ├── server/       API server (FastAPI, auth, chat pipeline, sessions)
│   ├── cortex/       Brain cortex (intent routing, scheduler)
│   ├── experts/      Expert system (orchestrator, 5+ experts, registry)
│   ├── storage/      Dual storage (SQLite local, PostgreSQL production)
│   ├── utils/        Helpers (i18n, paths, JSON)
│   └── edition.py    Edition gating (community/pro config control)
├── frontend/
│   └── chatael-v2/   ChatAEL — React + Tailwind chat UI
├── tests/            130+ test cases
├── docs/
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE (MIT)
```

---

## Community vs Pro

| Dimension | Community (free, MIT) | Pro (license key) |
|-----------|----------------------|-------------------|
| Learning | Manual trigger | 7×24 auto loop |
| Discovery | User-specified | Curiosity-driven |
| Tuning | Static defaults | Dynamic auto-tune |
| Experts | 5 core | 12 total (incl. advanced) |
| Validation | None | Scheduled auto-scan |
| Security | Local rules | SentriKit deep engine |
| License | MIT | License key |

---

## External Dependencies

Only **3 external packages** — everything else is Python standard library.

| Package | Purpose |
|---------|---------|
| `bcrypt` | Password hashing |
| `fastapi` | API framework |
| `uvicorn` | ASGI server |

---

## Why Aelvoxim?

- **Lightweight** — 3 deps, 34k lines of Python, no Docker required
- **Self-contained** — SQLite for single-user, PostgreSQL for multi-user
- **Private by design** — Your data stays on your machine, no telemetry
- **Dual storage** — SQLite for dev, PostgreSQL for production, same code
- **Built-in safety** — Ethics rules, safety checks, meta-cognition monitoring

---

## License

MIT License — Copyright (c) 2026 macor24

See [LICENSE](./LICENSE) for details.
