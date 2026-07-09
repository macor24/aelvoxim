## Docker

### Prerequisites
- Docker & Docker Compose installed

### Quick start

```bash
# Clone the repo
git clone https://github.com/macor24/aelvoxim.git
cd aelvoxim

# Set your LLM provider (required)
export LLM_PROVIDER=openai
export LLM_API_KEY=sk-xxxxx

# Start
docker compose up -d

# Open in browser
open http://localhost:9701
```

### Configuration

Copy `.env.example` to `.env` and fill in your values, then:

```bash
docker compose --env-file .env up -d
```

### Data persistence

Data (memory, knowledge, plans) is stored in a Docker volume `aelvoxim_data` at `~/.aelvoxim/` inside the container.

### Desktop Gateway

The Docker container runs the Aelvoxim brain only. For Windows desktop control, you still need to run the Gateway on Windows:

```bash
cd aelvoxim-gateway
pip install -r requirements.txt
python main.py
```

See [aelvoxim-gateway/README.md](aelvoxim-gateway/README.md) for details.
