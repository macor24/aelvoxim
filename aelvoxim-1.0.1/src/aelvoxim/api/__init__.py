"""aelvoxim.api — Standard public API

Four core interfaces:
- Task submission: submit a learning direction or query
- Result query: check status of submitted tasks
- Memory read/write: store and retrieve entities, relations, events
- Config: get/set runtime configuration

Hides internal inference details. Only exposes call entry points.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils import read_json, write_json, CONFIG_FILE


# ── Config API ────────────────────────────


def get_config(key: str, default: Any = None) -> Any:
    """Get a configuration value."""
    cfg = read_json(CONFIG_FILE) or {}
    return cfg.get(key, default)


def set_config(key: str, value: Any) -> bool:
    """Set a configuration value."""
    cfg = read_json(CONFIG_FILE) or {}
    cfg[key] = value
    return write_json(CONFIG_FILE, cfg)


def list_config() -> Dict:
    """List all configuration."""
    return read_json(CONFIG_FILE) or {}


# ── Task API ──────────────────────────────


def submit_task(goal: str, task_type: str = "learn", **kwargs) -> Optional[str]:
    """Submit a learning task or query.

    Args:
        goal: What to learn or query (e.g. '退货政策', 'FastAPI async')
        task_type: 'learn' (add direction), 'query' (ask AI), 'search' (knowledge search)
        **kwargs: Additional parameters (confidence, source, etc.)
                  Can include 'plan_id' and 'milestone_id' for planner integration.

    Returns:
        task_id (str) on success, None on failure.
    """
    from ..learn.learner import get_learner
    from ..learn.knowledge import KnowledgeBase
    from ..learn.llm import call_llm
    from ..utils import read_json, LLM_CONFIG_FILE

    if task_type == "learn":
        learner = get_learner()
        if learner.add_direction(goal):
            return f"direction:{goal[:40]}"
        return None

    elif task_type == "search":
        kb = KnowledgeBase()
        results = list(kb.search(query=goal, limit=kwargs.get("limit", 5)))
        if results:
            return f"search:{goal[:20]}({len(results)} results)"
        return None

    elif task_type == "query":
        config = read_json(LLM_CONFIG_FILE) or {}
        models = config.get("models", [])
        if not models:
            return None
        text = call_llm(
            model=models[0],
            user_message=goal,
            system_prompt="You are a helpful AI assistant",
            max_tokens=kwargs.get("max_tokens", 500),
        )
        return text or None

    return None


def get_task_status(task_id: str) -> Optional[Dict]:
    """Get the status of a submitted task.

    Args:
        task_id: The task_id returned by submit_task()

    Returns:
        Dict with status info, or None if not found.
    """
    from pathlib import Path
    import json

    if task_id.startswith("direction:"):
        topic = task_id[len("direction:"):]
        from ..utils import LEARNER_CONFIG
        cfg_file = LEARNER_CONFIG
        try:
            cfg = json.loads(cfg_file.read_text())
            d = cfg.get(topic)
            if d:
                return {
                    "type": "direction",
                    "topic": topic,
                    "status": d.get("status", "unknown"),
                    "entries": d.get("entries_created", 0),
                    "cycles": d.get("cycles_completed", 0),
                }
        except Exception:
            pass

    elif task_id.startswith("search:"):
        return {"type": "search", "status": "completed"}

    return None


# ── Memory API ────────────────────────────


def memory_store(key: str, value: Any, tags: Optional[List[str]] = None,
                 etype: str = "legacy", relations: Optional[List[Dict]] = None) -> bool:
    """Store something in memory.

    Args:
        key: Unique identifier
        value: Arbitrary data
        tags: Optional search tags
        etype: Entity type ('legacy', 'user', 'product', 'topic', 'event')
        relations: Optional list of {target, type, attributes} to link
    """
    # Fast path: direct SQLite write with timeout guard
    from ..memory import _get_db
    import json, threading, time
    _done = threading.Event()
    _ok = [False]
    def _do_store():
        try:
            from ..memory import store_entity, store_relation
            r = store_entity(key, etype, {"value": value}, tags=tags)
            _ok[0] = r
            if r and relations:
                import uuid
                for rel in relations:
                    rid = f"{rel.get('type', 'rel')}:{key}:{rel.get('target', uuid.uuid4().hex[:8])}"
                    store_relation(rid, key, rel["target"], rel.get("type", "related"),
                                   attributes=rel.get("attributes", {}))
        except Exception:
            pass
        finally:
            _done.set()
    _t = threading.Thread(target=_do_store, daemon=True)
    _t.start()
    _completed = _done.wait(timeout=3)
    if _completed and _ok[0]:
        return True
    # Fallback: direct SQLite INSERT
    try:
        db = _get_db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        import json as _j
        db.execute(
            "INSERT OR REPLACE INTO entities (id, type, value, tags, attributes, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM entities WHERE id = ?), ?))",
            (key, etype, str(value)[:500], _j.dumps(tags or []), _j.dumps({"value": value}), "", key, now)
        )
        db.commit()
        return True
    except Exception:
        return False


def memory_read(key: str) -> Optional[Dict]:
    """Read something from memory by key."""
    from ..memory import memory_read as _read
    return _read(key)


def memory_search(query: str, limit: int = 10) -> List[Dict]:
    """Search memory entries."""
    from ..memory import memory_search as _search
    return _search(query, limit=limit)


def memory_timeline(entity_id: str, limit: int = 30) -> List:
    """Get timeline of events and relations for an entity."""
    from ..memory import get_timeline
    return get_timeline(entity_id, limit=limit)
