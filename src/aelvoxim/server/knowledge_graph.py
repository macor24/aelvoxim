# SPDX-License-Identifier: MIT
"""
metacore.server.knowledge_graph — Entity-relation knowledge graph builder.

Generates ECharts force-graph compatible JSON data with {nodes, links}.

Sources:
- SQLite entities (type=person/location/technology, tagged as extracted)
- SQLite relations (directed edges)
- Co-occurrence: entities mentioned in the same event

No LLM used — pure rule-based graph construction.
No dependency on old package code.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import METACORE_DIR

import logging
_log = logging.getLogger("aelvoxim.server.knowledge_graph")

_DB_PATH = str(Path(METACORE_DIR) / "memory.db")


# ── Node colors by type ──

_SKIP_CATEGORIES = {'person', 'location', 'organization', 'preference'}


TYPE_COLORS = {
    "person": "#5470c6",       # blue
    "location": "#91cc75",     # green
    "technology": "#fac858",   # yellow
    "preference": "#ee6666",   # red
    "job": "#73c0de",          # cyan
    "concept": "#3ba272",      # teal
    "topic": "#fc8452",        # orange
    "user": "#9a60b4",         # purple
    "knowledge": "#fc8452",    # orange (same as topic)
}

DEFAULT_COLOR = "#999999"


def _get_db() -> sqlite3.Connection:
    return sqlite3.connect(_DB_PATH)


def get_graph(user_id: str = "", limit: int = 50) -> Dict[str, list]:
    """Build a force-graph from memory data.

    Args:
        user_id: Filter by user_id (optional).
        limit: Maximum number of nodes.

    Returns:
        {"nodes": [...], "links": [...]} compatible with ECharts force-graph.
    """
    db = _get_db()
    nodes: Dict[str, dict] = {}
    links: List[dict] = []
    added: set = set()

    # 1. Entities with 'extracted' tag
    query = "SELECT id, type, value FROM entities WHERE tags LIKE '%extracted%'"
    params: List[str] = []
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    query += f" LIMIT {limit}"
    rows = db.execute(query, params).fetchall()

    for row in rows:
        eid = row[0]
        etype = row[1]
        value = row[2] or eid
        if eid not in added and etype not in _SKIP_CATEGORIES:
            nodes[eid] = {
                "id": eid,
                "name": value[:30],
                "category": etype,
                "itemStyle": {"color": TYPE_COLORS.get(etype, DEFAULT_COLOR)},
                "symbolSize": _node_size(etype, eid),
            }
            added.add(eid)

    # 2. Relations
    rel_rows = db.execute(
        "SELECT source, target, rel_type FROM relations LIMIT 200"
    ).fetchall()
    # Rewrite properly
    return _build_graph(db, nodes, added, user_id, limit)


def _build_graph(
    db: sqlite3.Connection,
    nodes: Dict[str, dict],
    added: set,
    user_id: str,
    limit: int,
) -> Dict[str, list]:
    """Build the graph structure from entities and relations."""
    links: List[dict] = []

    # Relations
    rows = db.execute("SELECT source, target, rel_type FROM relations LIMIT 500").fetchall()
    for row in rows:
        src, tgt, rtype = row[0], row[1], row[2]
        if src not in added:
            # Add source node if exists
            en = db.execute("SELECT id, type, value FROM entities WHERE id = ?", (src,)).fetchone()
            if en and len(nodes) < limit + 50 and en[1] not in _SKIP_CATEGORIES:
                nodes[en[0]] = {
                    "id": en[0],
                    "name": (en[2] or en[0])[:30],
                    "category": en[1],
                    "itemStyle": {"color": TYPE_COLORS.get(en[1], DEFAULT_COLOR)},
                    "symbolSize": 20,
                }
                added.add(en[0])
        if tgt not in added:
            en = db.execute("SELECT id, type, value FROM entities WHERE id = ?", (tgt,)).fetchone()
            if en and len(nodes) < limit + 50 and en[1] not in _SKIP_CATEGORIES:
                nodes[en[0]] = {
                    "id": en[0],
                    "name": (en[2] or en[0])[:30],
                    "category": en[1],
                    "itemStyle": {"color": TYPE_COLORS.get(en[1], DEFAULT_COLOR)},
                    "symbolSize": 20,
                }
                added.add(en[0])
        if src in nodes and tgt in nodes:
            links.append({
                "source": src,
                "target": tgt,
                "value": rtype,
                "lineStyle": {"curveness": 0.2},
            })

    # Co-occurrence: entities in the same event
    event_rows = db.execute(
        "SELECT type, participants FROM events WHERE type = 'chat_inquiry' ORDER BY RANDOM() LIMIT 100"
    ).fetchall()
    for row in event_rows:
        try:
            parts = json.loads(row[1])
        except (json.JSONDecodeError, IndexError):
            continue
        ent_ids = [p for p in parts if p.startswith("entity:") or p.startswith("user:")]
        for i in range(len(ent_ids)):
            for j in range(i + 1, len(ent_ids)):
                s, t = ent_ids[i], ent_ids[j]
                if s in nodes and t in nodes:
                    links.append({
                        "source": s,
                        "target": t,
                        "value": "co-occur",
                        "lineStyle": {"curveness": 0.2, "opacity": 0.3},
                    })

    # Deduplicate links
    seen_links = set()
    deduped = []
    for link in links:
        pair = (link["source"], link["target"], link.get("value", ""))
        if pair not in seen_links:
            seen_links.add(pair)
            deduped.append(link)

    return {"nodes": list(nodes.values())[:limit], "links": deduped[:limit * 3]}


def _node_size(etype: str, eid: str) -> int:
    """Determine symbol size based on type and relation count."""
    base = {"person": 30, "location": 25, "technology": 25, "preference": 20}
    return base.get(etype, 18)


def graph_stats() -> Dict:
    """Get graph statistics."""
    db = _get_db()
    entities = db.execute("SELECT COUNT(*) FROM entities WHERE tags LIKE '%extracted%'").fetchone()[0]
    relations = db.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    topics = db.execute("SELECT COUNT(*) FROM entities WHERE type='topic'").fetchone()[0]
    return {
        "extracted_entities": entities,
        "relations": relations,
        "topics": topics,
    }


def add_knowledge_entry(topic: str, title: str) -> None:
    """Add a knowledge entry as a graph node with relation to its topic.

    Called by Learner.on_store() when a new knowledge entry is created.
    """
    if not topic or not title:
        return
    import json
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    node_id = f"kb:{topic[:20]}:{title[:20]}"
    try:
        db = _get_db()
        # Add topic node if not exists
        topic_id = f"topic:{topic[:40]}"
        db.execute(
            "INSERT OR IGNORE INTO entities (id, type, value, tags, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, "topic", topic[:200], '["extracted","knowledge"]', json.dumps({"source": "learner"}), now),
        )
        # Add knowledge entry node
        db.execute(
            "INSERT OR IGNORE INTO entities (id, type, value, tags, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, "knowledge", title[:200], '["extracted","knowledge_entry"]', json.dumps({"topic": topic}), now),
        )
        # Add relation: entry -> belongs_to -> topic
        rel_id = f"rel:kb:{topic[:20]}:{title[:20]}"
        db.execute(
            "INSERT OR IGNORE INTO relations (source, target, rel_type, attributes, created_at) VALUES (?, ?, ?, ?, ?)",
            (node_id, topic_id, "belongs_to", json.dumps({"source": "learner"}), now),
        )
        db.commit()
    except Exception:
        _log.exception("knowledge_graph error")


def sync_all_entries_to_graph() -> dict:
    """Sync all knowledge base entries to the graph. Returns {added_entities, added_relations}."""
    import json
    from datetime import datetime
    from pathlib import Path
    entries_dir = Path.home() / ".aelvoxim" / "knowledge" / "entries"
    if not entries_dir.exists():
        return {"added_entities": 0, "added_relations": 0}
    db = _get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added_e = 0
    added_r = 0
    for f in sorted(entries_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text())
            topic = d.get("topic", "") or d.get("title", "")[:40]
            title = d.get("title", topic)
            # topic node
            topic_id = f"topic:{topic[:40]}"
            db.execute(
                "INSERT OR IGNORE INTO entities (id, type, value, tags, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (topic_id, "topic", topic[:200], '["extracted","knowledge"]', json.dumps({"source": "kb_sync"}), now),
            )
            # knowledge entry node
            node_id = f"kb:{topic[:20]}:{title[:20]}"
            db.execute(
                "INSERT OR IGNORE INTO entities (id, type, value, tags, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (node_id, "knowledge", title[:200], '["extracted","knowledge_entry"]', json.dumps({"topic": topic}), now),
            )
            # relation
            rel_id = f"rel:kb:{topic[:20]}:{title[:20]}"
            db.execute(
                "INSERT OR IGNORE INTO relations (source, target, rel_type, attributes, created_at) VALUES (?, ?, ?, ?, ?)",
                (node_id, topic_id, "belongs_to", json.dumps({"source": "kb_sync"}), now),
            )
            added_e += 1
            added_r += 1
        except Exception:
            _log.exception("knowledge_graph error")
    try:
        db.commit()
    except Exception:
        _log.exception("knowledge_graph error")
    return {"added_entities": added_e, "added_relations": added_r}
