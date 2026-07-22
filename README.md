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
2. **Self-Learning AI Agent with Persistent Memory & Desktop Control** — what it does
3. **Four-layer architecture** — how it's built

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
