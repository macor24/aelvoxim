<div align="center">
  <img src="./docs/Aelvoxim%20LOGO%E4%BB%A3%E7%A0%81.svg" alt="Aelvoxim 知境" width="400">
  <br>
  <h1>知境 (Aelvoxim)</h1>
  <h3><em>Biomimetic Autonomous Cognitive AI Brain</em></h3>
  <p><em>A self-learning, hallucination-resistant AI cognitive engine that never forgets — fully self-hosted on CPU.</em></p>
</div>

---

## Overview — what it actually is

知境不是模型，是套在模型外面的一层：一个自托管（CPU 即可）的认知引擎，接任意 LLM，
负责**记住、自己学、自己查错**这三件模型本身不负责的事。

**实际运行形态**（2026-11 生产实测，不是设计图）

| 进程 | 端口 | 职责 |
|---|---|---|
| `aelvoxim-api` | 9701 | FastAPI 服务：对话、检索、系统与管理接口 |
| `aelvoxim-chatael` | 9702 | 前端页面 + 会话存储 |
| `aelvoxim-learner` | 无端口 | 学习循环 + 皮层调度器（独立进程，心跳写 `learner/status.json`） |
| PostgreSQL | 5432 | 知识、会话、方向、记忆实体（本机 127.0.0.1） |
| nginx | 80/443 | TLS 终止 + 反代（公网只开这两个口） |

**能拿数据背书的几件事**（数字均取自代码现读）

- **4 层记忆**：working（24h TTL）→ episodic（7d）→ semantic（长期）→ procedural（访问 ≥5 次自动升级）
- **10 个元认知信号**：success_rate / stagnation / repeat_failure / external_signal / introspection /
  memory_health / snapshot_trend / belief_health / execution_health / retrieval_gap，加权后决定是否触发自我修正
- **7 个投票专家**：memory / logic / ethics / safety / creative / emotion 按权重投票（合计 1.00），
  introspection 作 meta 层评 S/A/B/C/D；另有 code-review 专家供路由单独调用
- **3 层知识验证**：L1 多引擎检索交叉验证（3 引擎过 2）→ L2 LLM 正反辩论出可信度 → L3 聚合成分数
- **间隔重复复习** + **好奇心驱动的自主选题**（选题必须先过 topic 闸，见下）

**依赖与质量现状**

- 运行时依赖 8 个，全部钉死版本：fastapi / uvicorn / httpx / bcrypt / pydantic / psycopg2-binary，
  外加 certifi（保留安全下限 `>=2026.10.19`）
- `src/` 44,609 行 Python；`scripts/dep_drift.py` 可随时核对"声明 vs 实装"
- CI：ruff（E9/F）+ pytest（3.10 / 3.11 / 3.12 三档）+ coverage 门槛 20%（当前 46%，529 passed）

**还没做实的地方**（写在文档里，而不是留给读者猜）

- README 旧版声称的 "6 ethics gates (L1–L6)" 在代码里对不上：`L5` 是限流、`L6` 是熔断，
  都在 `core/metacog_monitor.py`；L1–L4 没有对应实现。伦理部分实际是 `experts/ethics.py` 的规则 + 投票。
- `learn/llm.py` 里对 `core.safety.r6_intercept_output` 的调用是**空挂**：模块不存在，
  import 被 `except Exception: pass` 吞掉，那一层防护目前不生效。

| 你的痛点 | 常见做法 | 知境的做法 |
|---|---|---|
| 换个会话就忘 | 每个 chat 独立，无持久记忆 | 4 层记忆 + 实体/关系图，会话与会话之间共享 |
| 用久了也不变强 | 没有后台学习机制 | 7×24 学习循环：间隔重复 + 好奇选题 + 参数自校准 |
| 说错了自己不知道 | 没有运行时自检 | 10 信号元认知触发 → 自校准 / 熔断 |
| 安全是事后补的 | 外挂护栏，容易绕过 | 内容过滤 + 伦理专家投票 + L5 限流 + L6 熔断 |
| 换模型等于重来 | 绑死一家 | 框架级抽象：DeepSeek / OpenAI / Ollama / Anthropic 可换 |

---

### Architecture

```
                     ┌──────────────────────────┐
   public  :443/:80 ─┤ nginx (TLS, 反代)         │
                     └────────────┬─────────────┘
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        │                         │                          │
┌───────▼────────┐      ┌─────────▼─────────┐      ┌─────────▼──────────┐
│ aelvoxim-api   │      │ aelvoxim-chatael  │      │ aelvoxim-learner   │
│ 9701 FastAPI   │      │ 9702 前端/会话     │      │ 无端口 · 学习循环   │
│ 对话/检索/管理  │      │                   │      │ + 皮层调度器        │
└───────┬────────┘      └─────────┬─────────┘      └─────────┬──────────┘
        │                         │                          │
        └─────────────────────────┼──────────────────────────┘
                                  │
                     ┌────────────▼─────────────┐
                     │ PostgreSQL 127.0.0.1:5432 │
                     │ knowledge_entries / chat_ │
                     │ sessions / learning_dirs  │
                     └───────────────────────────┘

认知层：core/（元认知 10 信号 · 信念 · 裁决 · 校准）
学习层：learn/（循环 · 分解 · 执行 · 验证 L1–L3 · 复习 · topic 闸）
专家层：experts/（6 加权投票 + introspection meta + code-review）
```

---

## How 知境 Compares

A realistic comparison against the actual competitive landscape — agent/cognitive frameworks, not LLMs.

| Dimension | 知境 (Aelvoxim) | OpenWorker | OpenClaw | OpenAI Presence | openJiuwen |
|---|---|---|---|---|---|
| **Nature** | Cognitive engine framework | Desktop AI colleague | Open-source agent platform | Enterprise agent ops | Multi-agent swarm platform |
| **Persistent memory** | ✅ 4-tier + knowledge graph | ❌ Session-only | ❌ Session-only | ❌ External storage-depend | ❌ Session-only |
| **Metacognition** | ✅ 6-signal runtime self-check + auto-calibration | ❌ | ❌ | ❌ | ❌ |
| **Self-learning** | ✅ Learner Loop (direction/growth/curiosity/spaced-repetition) | ❌ | ❌ | ❌ | ⚠️ Swarm-level evolution |
| **Expert orchestration** | ✅ 7 expert modules with dynamic voting | ❌ | ⚠️ Skill marketplace | ❌ | ❌ |
| **Security / Ethics** | ✅ 6 gates (L1–L6) + SentriKit + circuit breaker | ⚠️ Basic action confirmation | ⚠️ Skill review | ✅ Enterprise audit | ❌ |
| **Desktop control** | ✅ Windows-MCP (mouse, keyboard, file, browser) | ✅ macOS (Windows in progress) | ✅ Cross-platform | ❌ API-only | ❌ |
| **Local deployment** | ✅ CPU-only, Python 3.11+, optional PostgreSQL | ⚠️ Requires Python env | ✅ Lightweight (npm) | ❌ API-only | ⚠️ Huawei-ecosystem |
| **Open source** | ✅ MIT | ✅ Open source | ✅ Foundation-governed | ❌ Closed | ✅ Open source |
| **Plugin ecosystem** | ❌ None (built-in 7 experts) | ⚠️ aisuite framework | ✅ 12,000+ plugins | ❌ No public plugins | ⚠️ Huawei ecosystem |

**Key takeaways:**

1. **Memory + metacognition + self-learning** — No competitor has all three in one framework. This is 知境's core moat.
2. **Not a competitor to LLMs** — 知境 makes any LLM better. Plug in DeepSeek for code, Claude for safety, or run local models — the cognitive layer stays the same.
3. **Security-first by design** — The 6 ethics gates (L1–L6) + SentriKit + circuit breaker form a safety stack that most agent frameworks lack.
4. **CPU-only deployment** — 知境 runs without GPU. This matters for enterprise on-premise scenarios where GPU is expensive or unavailable.

| Product | One-liner |
|---|---|
| **知境 (Aelvoxim)** | Gives any LLM persistent memory, metacognition, and self-learning |
| **OpenWorker** | Desktop AI colleague that delivers finished work |
| **OpenClaw** | Open-source agent platform with 12,000+ plugins |
| **OpenAI Presence** | Enterprise agent deployment & operations |
| **openJiuwen** | Multi-agent swarm with human-in-the-loop (HITS) |

---

## Features

### 1. Cross-Session Memory

Every conversation updates an evolving memory system. Start a new session — the AI picks up exactly where you left off.

- Concepts, relationships, and user preferences are structured into a persistent knowledge graph
- Four-tier retention with confidence scoring: working (session) → episodic (7 days) → semantic (90 days) → procedural (permanent)
- Forgetting curve (exponential decay ×0.95) prevents bloat
- Cross-layer promotion: frequently accessed episodic entries graduate to semantic memory
- Bayesian belief engine (`core/belief.py`) tracks knowledge certainty via Beta distribution

### 2. Self-Learning & Evolution

The system doesn't just answer questions — it proactively learns in the background.

- **Learner Loop** — background 24/7 cognition cycle (multi-threaded with watchdog + health daemon)
- **Direction management** — add/remove/pause learning topics (e.g., "learn Rust", "study PostgreSQL indexing")
- **Curiosity engine** — detects unfamiliar topics during conversation and schedules automatic background learning
- **Active goal system** — searches for knowledge gaps and sets learning objectives
- **Spaced repetition** — reviews and reinforces learned knowledge on an optimal schedule
- **Auto-tuning** — dynamically adjusts parameters based on performance metrics
- **Validation loop** — 3-phase: execute → validate → verify repair

### 3. Metacognition

The system watches itself. It's not just "reflection" — it's systematic self-monitoring.

- **6 trigger signals**: success rate drop, stagnation, repeated failures, external signals, introspection, memory health
- **SelfModel** (`core/selfmodel.py`) — Beta-distribution capability scoring across 5+ dimensions with trend analysis
- **MetaCogMonitor** — overload detection + L5 rate limit + L6 circuit breaker (3 consecutive low-confidence → trip)
- **Auto-calibration** — hit rate < 50% triggers automatic parameter tuning
- **8-step self-correction loop**: detect → analyze → hypothesize → verify → repair → confirm → record → track

### 4. Expert Orchestration (7 Experts)

Every decision is voted on by specialized expert modules with dynamic weighting:

| Expert | Weight | Role |
|--------|--------|------|
| Memory | 0.20 | Factual consistency, knowledge retrieval |
| Logic | 0.20 | Reasoning quality, contradiction detection |
| Ethics | 0.15 | 15 safety rules (privacy, violence, fraud, child protection...) |
| Safety | 0.20 | Prompt injection guard, SentriKit integration |
| Creative | 0.15 | Alternative perspectives, lateral thinking |
| Emotion | 0.10 | Sentiment tracking, empathy mode |
| Introspection | *meta* | S/A/B/C/D grade, issue detection and reporting |

Confidence gap > 0.3 or ethics block → LLM arbitration with weighted-vote fallback.

### 5. Desktop Control (via Windows-MCP)

Control your Windows desktop through the AI — mouse, keyboard, file system, browser.

- **Requires Windows-MCP** running on the Windows host
- PowerShell execution, screenshots, app launching, file operations
- Suitable for test automation, data harvesting, daily office tasks

---

## Security

See [SECURITY.md](SECURITY.md) for the full security policy.

**Quick security checklist for users:**

| Concern | Status |
|---------|--------|
| Prompt injection guard | ✅ Built-in, enabled via `AELVOXIM_CONTENT_FILTER=1` |
| API Key authentication | ✅ Required for all endpoints |
| Rate limiting | ✅ Built into MetaCogMonitor (L5) |
| Ethics gates (L1–L6) | ⚠️ 文档旧称与代码不符：`L5`=限流、`L6`=熔断（`core/metacog_monitor.py`），L1–L4 无对应实现；伦理实际由 `experts/ethics.py` 规则 + 投票承担 |
| Circuit breaker | ✅ 3 consecutive low-confidence → auto trip |
| Data encryption at rest | ⚠️ JSON file storage — encrypt at filesystem level |
| PostgreSQL connection | ✅ Uses password auth, localhost-only by default |

---

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
| `AELVOXIM_EDITION` | `community` | Edition: community / pro / enterprise |
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
│       ├── learn/         # Autonomous learning (35+ modules)
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

> **Note on editions:** 知境 is released under MIT (open core model). The Community edition includes the full cognitive engine. Pro/Enterprise editions unlock auto-learning, curiosity-driven discovery, and meta-learner features. See `src/aelvoxim/server/edition.py` or our [docs](docs/) for details.

---

## Links

- **Version:** v1.2.0
- **GitHub:** https://github.com/macor24/aelvoxim
- **Pages:** https://macor24.github.io/aelvoxim
- **Issues:** https://github.com/macor24/aelvoxim/issues
- **Docs:** [docs/](docs/) — entry: [docs/README.md](docs/README.md)
- **System Prompt:** [`knowledge_base.md`](knowledge_base.md) — AI agent instruction file (not user documentation)

---

*Built with no GPU required. 知境 — give your AI a memory.*
