# Aelvoxim

## Biomimetic Autonomous Cognitive AI Brain

> A self-learning, hallucination-resistant AI cognitive entity that never forgets and can control your desktop — fully self-hosted.

---

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Application Layer                                 │
│  Desktop control, file ops, browser automation     │
├─────────────────────────────────────────────────────┤
│  Tool Layer                                         │
│  Code execution, API calls, data analysis, MCP     │
├─────────────────────────────────────────────────────┤
│  Cognitive Layer                                    │
│  Reasoning, planning, decision-making, learning     │
├─────────────────────────────────────────────────────┤
│  Memory Layer                                       │
│  Working → Episodic → Semantic → Procedural        │
│  Knowledge graph, entity extraction                │
└─────────────────────────────────────────────────────┘
```

What you see, in order:

1. **Aelvoxim** — the project
2. **Biomimetic Autonomous Cognitive AI Brain** — what it is
3. **A self-learning, hallucination-resistant AI that never forgets and can control your desktop** — what it does
4. **Four-layer architecture** — how it's built

---

## How Aelvoxim Compares

A realistic comparison of Aelvoxim against major AI platforms — written from current capability, not roadmap.

| Dimension | Aelvoxim | DeepSeek | ChatGPT | Claude | Llama |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Nature** | Cognitive engine framework (plugs into any LLM) | LLM | LLM + platform | LLM | Open-source LLM |
| **Persistent memory** | ✅ 4-tier memory, cross-session knowledge graph | ❌ No built-in memory | ⚠️ Limited (ChatGPT Memory) | ❌ No built-in memory | ❌ No built-in memory |
| **Metacognition** | ✅ MetaCogMonitor + 6 ethics gates (L1-L6) | ❌ | ❌ | ⚠️ Constitutional AI (different approach) | ❌ |
| **Self-learning** | ✅ Learner loop + background knowledge extraction | ❌ | ❌ | ❌ | ❌ |
| **Expert orchestration** | ✅ 8 expert modules + orchestrator voting | ❌ | ❌ | ❌ | ❌ |
| **Code generation** | ⚠️ Depends on backend LLM (can use DeepSeek, etc.) | ✅ Excellent | ✅ Strong | ✅ Strong | ⚠️ Fine-tuned variants |
| **Reasoning depth** | ⚠️ Depends on backend LLM | ✅ Strong chain-of-thought | ✅ Strong | ✅ Strong, safety-aligned | ⚠️ Varies by size |
| **Local deployment** | ✅ CPU-only, Python 3.11+, optional PostgreSQL | ✅ Needs GPU | ❌ API-only | ❌ API-only | ✅ Needs GPU |
| **Open source** | ✅ MIT | ✅ Weights open | ❌ Closed | ❌ Closed | ✅ Weights open |
| **Multimodal** | ⚠️ Via tool integration | ❌ Text-only | ✅ GPT-4o multimodal | ✅ Multimodal | ⚠️ Partial |
| **Tool calling** | ✅ Unified orchestrator | ⚠️ Function Call | ⚠️ Function Call | ⚠️ Tool Use | ⚠️ Self-wrapped |
| **Security** | ✅ 6 ethics gates (L1-L6) + community edition gating | ⚠️ Basic content filter | ⚠️ Policy filter | ✅ Constitutional AI | ❌ None built-in |
| **Business model** | Open-source + self-hosted | Open weights + API | Closed API | Closed API | Open weights + ecosystem |

**Key takeaways:**

1. **Memory & continuous learning** — Aelvoxim's core moat. Every competitor is stateless per session. ChatGPT Memory exists but is a simple snippet store — no forgetting curve, no confidence scoring, no 4-tier architecture.
2. **Metacognition & self-learning** — No competitor has runtime self-monitoring, degradation detection, or hypothesis generation. Aelvoxim's MetaCogMonitor + 6 ethics gates are unique. Other models' "reflection" is prompt-induced text generation, not system-level self-check.
3. **Code & reasoning** — Aelvoxim's advantage is **flexibility**: it doesn't lock you into one model. Plug in DeepSeek for code, Claude for safety, or run multiple models and let the orchestrator vote.
4. **Deployment** — Aelvoxim runs on CPU, no GPU required. DeepSeek and Llama need GPU for local inference.

**Bottom line:** Aelvoxim is not competing with LLMs — it's the **operating system for LLMs**: managing memory, monitoring health, orchestrating tools, and learning continuously. You choose the brain (model), Aelvoxim gives it a body that remembers and improves.

| Product | One-liner |
| :--- | :--- |
| **Aelvoxim** | Gives any LLM persistent memory, metacognition, and self-learning |
| **DeepSeek** | Open-source code king, cost-effective reasoning |
| **ChatGPT** | Closed-source all-rounder, multimodal + plugin ecosystem |
| **Claude** | Safest closed-source model, long-document reasoning |
| **Llama** | Open-source LLM standard — powerful but needs engineering to productize |

---

## Features

### 1. Cross-Session Memory

Every conversation updates an evolving memory system. Start a new session — the AI picks up exactly where you left off. No lost context, no repeating yourself.

- Concepts, relationships, and user preferences are structured into a persistent knowledge graph
- Memory is queryable, exportable, and resettable
- Four-tier retention: working (session) → episodic (7 days) → semantic (90 days) → procedural (permanent)

### 2. Self-Learning & Evolution

The system doesn't just answer questions — it learns from them.

- Proactively initiates learning plans ("learn Rust", "study PostgreSQL indexing")
- Curiosity engine detects unfamiliar topics during conversation and schedules background learning
- Learning progress is trackable; acquired knowledge can be recalled and explained back to you

### 3. Reasoning & Planning

- Multi-step logical reasoning, causal analysis, and contradiction detection
- Complex tasks are decomposed into plans and executed step by step
- Tool-calling for code execution, API integration, and data analysis
- Metacognition layer checks output quality (factual consistency, topic drift, safety, clarity)

### 4. Desktop Control (via Windows-MCP)

Control your Windows desktop through the AI — mouse, keyboard, file system, browser.

- **Requires Windows-MCP** running on the Windows host (see [Windows-MCP/install_and_run.bat](Windows-MCP/install_and_run.bat))
- PowerShell execution, screenshots, app launching, file operations
- Suitable for test automation, data harvesting, daily office tasks

### 5. Security

- All requests filtered through SentriKit (optional security gate)
- Tool permissions are tiered — sensitive operations require confirmation
- No prompt injection, no unauthorized system modification

---

## Security

See [SECURITY.md](SECURITY.md) for the full security policy.

**Quick security checklist for users:**

| Concern | Status |
|---------|--------|
| Prompt injection guard | ✅ Built-in, enabled via `AELVOXIM_CONTENT_FILTER=1` |
| API Key authentication | ✅ Required for all endpoints |
| Rate limiting | ✅ Built into MetaCogMonitor (L5) |
| Data encryption at rest | ⚠️ JSON file storage — encrypt at filesystem level |
| PostgreSQL connection | ✅ Uses password auth, localhost-only by default |

## CI & Code Quality

| Check | Service | When |
|-------|---------|------|
| Lint (Ruff) | GitHub Actions | Every push/PR |
| Tests (3 Python versions) | GitHub Actions | Every push/PR |
| Security scan | GitHub Actions + CodeQL | Every push/PR + weekly |
| Dependency updates | Dependabot | Weekly (security only) |

All CI workflows are in [`.github/workflows/`](.github/workflows/).  
PR template is at [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

**Quick rules:**
- One feature per PR
- All code, comments, and commit messages in **English only**
- Stdlib-first — minimize external dependencies
- Type hints required for public APIs
- Run `pytest tests/ -v` before submitting
- Update README if API or config changes

---

### Port Map

| Port | Service | Description |
|------|---------|-------------|
| 9701 | API Server (FastAPI) | Core brain — chat, auth, admin, knowledge, learning |
| 9702 | Frontend (ChatAEL-v2) | Web chat interface (compiled SPA) |
| 5432 | PostgreSQL | Sessions, messages, knowledge base, users |

---

## Quickstart

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ *(optional — falls back to JSON file storage)*
- An LLM API key (OpenAI, DeepSeek, Anthropic, or any OpenAI-compatible provider)

### Installation

```bash
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim

# Python dependencies
pip install -e .

# Configure PostgreSQL (optional, skip if using JSON storage)
psql -U postgres -c "CREATE DATABASE aelvoxim;"
psql -U postgres -c "CREATE USER aelvoxim WITH PASSWORD 'your_password';"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE aelvoxim TO aelvoxim;"
export AELVOXIM_DATABASE_URL="host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=your_password"
```

### Configure LLM

Set one of these environment variables (see docs for full list):

```bash
# DeepSeek
export DEEPSEEK_API_KEY="sk-..."
export LLM_PROVIDER="deepseek"

# OpenAI
export OPENAI_API_KEY="sk-..."
export LLM_PROVIDER="openai"
```

### Running

```bash
# Start the brain
PYTHONPATH=src python3 src/run_server.py 9701

# (separate terminal) Start the frontend
python3 serve_chatael.py --port 9702

# (on Windows host) Start desktop control — see Windows-MCP/install_and_run.bat
```

Open `http://localhost:9702` in your browser. Register an account and start chatting.

---

## API Endpoints

All endpoints on port 9701:

| Path | Description |
|------|-------------|
| `POST /v1/auth/register` | Create a new user account |
| `POST /v1/auth/login` | Authenticate — returns API key |
| `POST /v1/llm/chat/stream` | Streaming chat (SSE) |
| `GET /v1/admin/panel` | Admin management panel |
| `GET /v1/health` | Service health check |

A full OpenAPI spec is available at `http://localhost:9701/docs`.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `deepseek` | LLM provider name |
| `DEEPSEEK_API_KEY` | — | API key for DeepSeek |
| `OPENAI_API_KEY` | — | API key for OpenAI |
| `AELVOXIM_DATABASE_URL` | (none) | PostgreSQL DSN — leave unset for JSON file storage |
| `AELVOXIM_CONTENT_FILTER` | `0` | Enable prompt injection guard |
| `AELVOXIM_LLM_CHECK` | `0` | Enable LLM-based fact contradiction check |

---

## Project Structure

```
aelvoxim/
├── src/
│   └── aelvoxim/
│       ├── server/        # API routes, auth, chat, tool execution
│       ├── cortex/        # Intent routing, expert orchestration
│       ├── chimera/       # Emotion engine, intent classification
│       ├── control/       # Metacognition, generation quality checks
│       ├── learn/         # Autonomous learning, knowledge acquisition
│       ├── memory/        # Cross-session memory, entity extraction
│       ├── proactive/     # Background proactive engine
│       ├── storage/       # Database layer (PostgreSQL + JSON fallback)
│       ├── utils/         # Utility functions
│       └── planner/       # Long-term task planning
├── frontend/              # ChatAEL-v2 SPA
├── scripts/               # CI, lint, migration helper scripts
├── tests/                 # Test suite
├── serve_chatael.py       # Frontend static server entry point
└── requirements.txt       # Locked dependencies
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Links

- **GitHub:** https://github.com/macor24/aelvoxim
- **Issues:** https://github.com/macor24/aelvoxim/issues
