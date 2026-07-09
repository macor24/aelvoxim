# Aelvoxim — More than a chatbot. A brain that learns.

<p align="center">
  <img src="docs/Aelvoxim2.jpg" alt="Aelvoxim" width="600">
</p>

<p align="center">
  A self-hosted AI decision layer with multi-agent orchestration, anti-hallucination, 
  memory, tool calling, desktop control, and autonomous learning.
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/aelvoxim)](https://pypi.org/project/aelvoxim/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-macor24%2Faelvoxim-181717?logo=github)](https://github.com/macor24/aelvoxim)

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim
export LLM_PROVIDER=openai LLM_API_KEY=sk-xxxxx
docker compose up -d
# Open http://localhost:9701
```

### Pip

```bash
pip install aelvoxim

# Start the API server
aelvoxim server --port 9701
```

### Windows

```powershell
pip install aelvoxim
aelvoxim server --port 9701
# Open browser at http://localhost:9701
```

### From source

```bash
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

---

## Use Cases

Aelvoxim's layered memory, autonomous learning, and meta-cognition unlock scenarios that ordinary LLM wrappers and RAG pipelines cannot handle.

### 🧑‍🤝‍🧑 Long-Living Virtual Character
Most AI agents lose everything on restart — Aelvoxim doesn't.

- Conversations are stored in **episodic memory** and **never reset**
- User preferences and speaking style are extracted into **semantic memory** (persist across sessions)
- The **forgetting curve** naturally prioritizes recent interactions while preserving core facts
- After weeks of use, the character grows to genuinely know the user, instead of starting from scratch every session

### 📚 Living Code Documentation
Hook Aelvoxim into your Git workflow and let it maintain your project docs automatically.

- Connect via **GitHub/GitLab webhook** — every PR merge triggers re-learning
- Aelvoxim scans new code, extracts module APIs, and updates its knowledge base
- **Conflict detection** flags when an interface changed but docs weren't updated
- Team members ask "how does this function work?" and get answers grounded in actual code, not stale docs

### 🔍 Continuous Compliance Monitoring (Medical / Legal)
Meta-cognition + auto-validation as a regulatory watchdog.

- Store regulations, guidelines, or protocols in the knowledge base
- **Curiosity-driven discovery** periodically searches for new versions or updates
- **Conflict detection** catches contradictions between new and old knowledge automatically
- Output validation ensures every answer stays within compliance boundaries
- No manual review cycles needed — the system watches itself

### 🧠 Personal Learning Companion
Unlike ChatGPT where every conversation is isolated, Aelvoxim builds a persistent model of what you know.

- Tracks what you've learned and which concepts you find confusing
- **Forgetting curve** schedules review reminders at optimal intervals
- Extracts knowledge triples from your notes automatically (knowledge distillation)
- **Gap analysis** identifies blind spots and suggests what to study next
- Over time it becomes a second brain that knows your knowledge landscape

### 📊 Automated Business Intelligence
Turn curiosity-driven learning into a competitive radar.

- Define interest directions (competitor moves, industry news, tech trends)
- **Curiosity engine** autonomously discovers and fetches relevant content
- Extracted insights are stored as structured knowledge
- **Conflict alerts** fire when new information contradicts existing beliefs (e.g. a competitor changed pricing)
- No manual keyword rules — the AI decides what's worth learning

### ⚙️ Self-Tuning Service Bot
Deploy a support bot that tunes itself — no DevOps babysitting required.

- Out of the box it works with conservative defaults
- **Auto calibration** monitors real response quality and adjusts parameters (temperature, expert weights, memory thresholds) based on what actually works
- When traffic patterns shift (e.g. more technical questions at certain hours), the system adapts without a config push
- **Knowledge gap analysis** spots missing answers and automatically initiates learning
- Over weeks the bot silently improves — your only job is to keep it running

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
