# Aelvoxim

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Aelvoxim — Lightweight AI Cognitive Engine Framework.**

Give your AI application a "memory" — it remembers, reasons, learns, and has meta-cognition.

---

## Positioning

| Edition | Positioning | Tagline |
|---------|-------------|---------|
| **Community** | A living memory | Forgets, overloads, doubts itself, learns from conversations |
| **Pro** | Self-evolving production brain | 7×24 auto-learning, self-healing, dynamic optimization |

## Features

### 🧠 Memory System
- **4-layer memory architecture**: Working / Episodic / Semantic / Procedural
- **MemoryFusion**: Inverted index + layer-prioritized retrieval
- **Forgetting curve**: Knowledge decays naturally over time (Ebbinghaus)
- **Conflict detection**: Automatically finds contradictions, alerts users
- **Confidence Matrix**: 5-dimensional scoring, marks trustworthiness on every response

### 🔧 Expert System
| Expert | Community | Pro |
|--------|-----------|-----|
| LogicExpert | ✅ | ✅ |
| MemoryExpert | ✅ | ✅ |
| EthicsExpert | ✅ | ✅ |
| SafetyExpert | ✅ | ✅ |
| CodeReviewExpert | ✅ | ✅ |
| EmotionExpert | ❌ | ✅ |
| CreativeExpert | ❌ | ✅ |
| IntrospectionExpert | ❌ | ✅ |
| HypothesisEngine | ❌ | ✅ |

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
- RESTful API (FastAPI + Swagger)
- SSE streaming
- Multi-user auth (email/password, API Key)
- Knowledge base CRUD + search
- Session management

## Quick Start

```bash
# Install
pip install aelvoxim

# Start server
aelvoxim server --port 9701

# Open in browser
open http://localhost:9701
```

### From source

```bash
git clone https://github.com/gmxchz/aelvoxim.git
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

## Project Structure

```
aelvoxim/
├── src/aelvoxim/
│   ├── core/         Cognitive engine (belief/metacog/reasoner/judge/DGM-H)
│   ├── learn/        Learning engine (learner/knowledge base/validator/search)
│   ├── memory/       Memory system (4 layers/fusion/decay/conflict)
│   ├── server/       API server (FastAPI routes/auth/chat pipeline)
│   ├── cortex/       Brain cortex (router/scheduler)
│   ├── experts/      Expert system (7+ experts/orchestrator/registry)
│   ├── storage/      Storage layer (SQLite/PostgreSQL dual-mode)
│   ├── utils/        Helpers (i18n, paths, JSON)
│   └── edition.py    Edition gating (community/pro config control)
├── tests/            130+ test cases
└── docs/             Documentation
```

## Community vs Pro

| Dimension | Community (free, MIT) | Pro (¥99/month) |
|-----------|----------------------|-----------------|
| Learning | Manual trigger | 7×24 auto loop |
| Discovery | User-specified | Curiosity-driven |
| Tuning | Static defaults | Dynamic auto-tune |
| Experts | 5 core | 12 total (incl. advanced) |
| Validation | None | Scheduled auto-scan |
| Security | Local rules | SentriKit deep engine |
| License | MIT open source | License key |

## External Dependencies

- **bcrypt** — Password hashing
- **fastapi** — API framework
- **uvicorn** — ASGI server

Everything else is Python standard library.

## License

MIT License

Copyright (c) 2026 gmxchz
