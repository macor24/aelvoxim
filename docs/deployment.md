# Deployment Guide

## Prerequisites

- Python 3.10+
- LLM API key (optional — engine runs without it)

## Installation

```bash
pip install aelvoxim
```

## Configuration

MetaCore stores runtime data at `~/.aelvoxim/`. All config is optional.

### LLM (optional)

```json
# ~/.aelvoxim/llm-config.json
{
  "models": [
    {"provider": "deepseek", "api_key": "sk-xxx", "base_url": "https://api.deepseek.com/v1", "name": "deepseek-chat", "priority": 1, "is_default": true}
  ]
}
```

### Learning directions (optional)

Add topics for the engine to learn:

```bash
python -m aelvoxim learn add "FastAPI async optimization"
python -m aelvoxim learn add "Python threading safety"
python -m aelvoxim learn start
```

## Integrations

### Telegram Bot (5 min)

```python
from telegram.ext import Application, MessageHandler, filters
from aelvoxim.api import submit_task

async def handle(update, context):
    reply = submit_task(update.message.text, "query") or "I'm still learning."
    await update.message.reply_text(reply)

app = Application.builder().token("YOUR_BOT_TOKEN").build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
```

### Feishu/Lark Bot

See `examples/feishu_bot.py` for a complete example with message handling,
image sending, and Webhook setup.

### AI Customer Service Demo

```bash
python demo_v2_deepseek.py
```

Advanced salesperson mode with context-aware proactive follow-up
(30-120s random interval).

## Dashboard

```bash
python -m aelvoxim ui --port 9700
```

Opens a read-only dashboard at `http://localhost:9700/` with real-time
statistics on learning progress, knowledge base, and capability profile.
Auto-refresh every 30 seconds. Language toggle in top-right corner.

## Production Considerations

- **Memory**: `~/.aelvoxim/memory.json` — grows with entity/relation/event count.
  Events are capped at 1000 entries automatically.
- **Knowledge**: `~/.aelvoxim/knowledge/` — index + per-entry JSON files.
- **Config**: All in `~/.aelvoxim/` — backup this directory for disaster recovery.
- **LLM**: Without LLM config, the engine still runs but skips LLM-dependent features
  (query answering, theme relevance check, LLM distillation).

## Data Cleanup

```bash
# Reset everything (keeps the package intact)
rm -rf ~/.aelvoxim

# Reset only knowledge base
rm -rf ~/.aelvoxim/knowledge

# Reset only learning progress
rm -f ~/.aelvoxim/learner/config.json
```
