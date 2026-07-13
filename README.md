# Aelvoxim

**Lightweight AI Cognitive Engine Framework — give your AI a brain that learns.**

Aelvoxim is a self-hosted AI decision layer that combines multi-agent orchestration, persistent memory, autonomous learning, and desktop automation. It provides a unified API (port 9701) for chat, knowledge management, and system administration, with a separate Gateway (port 9705) for Windows desktop control.

> **Why Aelvoxim?** Most LLM frameworks treat the model as the whole system. Aelvoxim treats the model as one component in a larger cognitive architecture: safety filters, knowledge retrieval, memory persistence, multi-expert orchestration, metacognition checks, and desktop operation tools all work together *before and after* the LLM call.

---

## Architecture

```
User → Frontend (9702) → API Server (9701) → LLM
                            ↓
                    Expert Modules (safety, knowledge, memory, security, creativity, logic, emotion)
                            ↓
                    Metacognition Check (fact-conflict, drift, safety, clarity, repetition)
                            ↓
                    Tool Execution (read_file, write_file, gateway, OCR, HTTP requests)
                            ↓
                    Response → Frontend
```

### Port Map

| Port | Service | Description |
|------|---------|-------------|
| 9701 | API Server (FastAPI) | Brain — chat, auth, admin, knowledge, learning |
| 9702 | Frontend (ChatAEL-v2) | Web chat interface (static SPA) |
| 9705 | Desktop Gateway (FastAPI) | Windows desktop control (UIA, OCR, screenshots) |
| 5432 | PostgreSQL | Sessions, messages, knowledge base, users |

---

## Quickstart

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (optional — falls back to JSON file storage)
- Windows 10+ (for desktop Gateway functionality)

### Installation

```bash
# Clone the repository
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim

# Install dependencies
pip install -r requirements.txt

# Set up PostgreSQL (optional, recommended)
# Create database and user
psql -U postgres -c "CREATE DATABASE aelvoxim;"
psql -U postgres -c "CREATE USER aelvoxim WITH PASSWORD 'your_password';"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE aelvoxim TO aelvoxim;"

# If using PG, set connection string
export AELVOXIM_DATABASE_URL="host=localhost port=5432 dbname=aelvoxim user=aelvoxim password=your_password"
```

### Running

```bash
# Start the API server (brain)
cd aelvoxim
PYTHONPATH=src python3 src/run_server.py 9701

# Start the frontend (separate terminal)
cd frontend/chatael-v2
python3 -m http.server 9702

# Start the Gateway (on Windows host, separate terminal)
cd aelvoxim-gateway
python3 start_gateway.py
```

Once running, open `http://localhost:9702` in your browser. Register an account and start chatting.

---

## Features

### Core Capabilities

- **Multi-Expert Orchestration** — 8 expert modules (safety, knowledge, memory, security, creativity, logic, emotion, introspection) collaborate to produce high-quality responses
- **Persistent Memory** — Cross-session memory with entity extraction, relation graphs, and forgetting curves
- **Autonomous Learning** — Scheduled knowledge acquisition from web searches, code analysis, and conversation history
- **Metacognition** — Post-generation quality checks (factual consistency, topic drift, safety, clarity, repetition avoidance)
- **Multi-Tenant** — Session isolation by user ID; knowledge base intentionally shared across users

### Desktop Automation (Windows Gateway)

| Operation | Description |
|-----------|-------------|
| `open` | Launch an app by name (Windows PATH) or known alias (Photoshop, WeChat, Chrome) |
| `activate_window` | Bring a window to foreground by title match |
| `find_window` | Locate a window — returns position and dimensions |
| `type_text` | Type text into the active window; auto-reactivates last focused window |
| `send_keys` | Send keyboard shortcuts (e.g. `^s` for Ctrl+S, `{ENTER}`) |
| `mouse_click` | Click at screen coordinates |
| `screenshot` | Capture the entire screen or a specific window |
| `ocr_screenshot` | Screenshot + OCR (via PaddleOCR subprocess) — returns text blocks |
| `open_app` | Special command to launch known applications (alias for open) |

> **Note:** Only system applications (notepad, calc, mspaint) can be launched directly by name. Third-party applications (WeChat, Photoshop) require the path to be configured in `_KNOWN_APPS` or launched manually.

### API Endpoints

All API endpoints are served on port 9701:

| Path | Description |
|------|-------------|
| `POST /v1/auth/register` | Create a new user account |
| `POST /v1/auth/login` | Authenticate — returns API key |
| `POST /v1/llm/chat/stream` | Streaming chat (SSE) |
| `GET /v1/admin/panel` | Admin management panel HTML |
| `GET /v1/admin/data` | System statistics dashboard |
| `GET /v1/health` | Service health check |

---

## Configuration

Configuration is primarily through environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AELVOXIM_DATABASE_URL` | (none) | PostgreSQL DSN — leave unset for JSON file storage |
| `AELVOXIM_EDITION` | `enterprise` | Feature edition gate (community/pro/enterprise) |
| `AELVOXIM_GATEWAY_HOST` | (auto-detected) | Windows Gateway host IP for desktop operations |
| `AELVOXIM_LLM_CHECK` | `0` | Enable LLM-based fact contradiction check (R1) |

---

## Testing

```bash
cd aelvoxim
PYTHONPATH=src python3 -m pytest tests/ -v

# Run only specific test suites
PYTHONPATH=src python3 -m pytest tests/test_tool_use.py -v
PYTHONPATH=src python3 -m pytest tests/test_database.py -v
```

---

## Project Structure

```
aelvoxim/
├── src/
│   └── aelvoxim/
│       ├── server/        # API routes, auth, chat, tool execution
│       ├── cortex/        # Intent routing, expert orchestration
│       ├── experts/       # 8 expert modules (safety, logic, creativity, etc.)
│       ├── control/       # Metacognition checks (post-generation quality)
│       ├── learn/         # Autonomous learning, knowledge acquisition
│       ├── memory/        # Cross-session memory, entity extraction
│       ├── storage/       # Database layer (PostgreSQL + JSON fallback)
│       ├── utils/         # Utility functions (JSON, datetime, i18n)
│       └── chimera/       # Intent classification, emotion engine
├── aelvoxim-gateway/      # Windows desktop Gateway (UIA, OCR)
├── frontend/              # ChatAEL-v2 SPA
├── tests/                 # Test suite (~60 tests)
├── requirements.txt       # Locked dependencies
└── README.md
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Links

- **GitHub:** https://github.com/macor24/aelvoxim
- **Issue Tracker:** https://github.com/macor24/aelvoxim/issues
