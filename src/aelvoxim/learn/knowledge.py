"""
metacore.learn.knowledge — Knowledge base engine

Knowledge entry storage, retrieval, statistics.
Pure stdlib, data stored as JSON files under ~/.metacore/knowledge/.
"""

from __future__ import annotations

import json
import os
import threading
import time
import hashlib
import secrets
try:
    import fcntl
    HAVE_FCNTL = True
except ImportError:
    HAVE_FCNTL = False
    try:
        import msvcrt
        HAVE_MSVCRT = True
    except ImportError:
        HAVE_MSVCRT = False
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("aelvoxim.knowledge")

KNOWLEDGE_DIR = Path.home() / ".aelvoxim" / "knowledge"
INDEX_FILE = KNOWLEDGE_DIR / "index.json"
ENTRIES_DIR = KNOWLEDGE_DIR / "entries"
PENDING_FILE = KNOWLEDGE_DIR / "pending.json"
REJECTED_FILE = KNOWLEDGE_DIR / "rejected.json"
LOCK_FILE = KNOWLEDGE_DIR / ".lock"  # process-level file lock

# Thread lock (intra-process serialization) + Process lock (cross-process serialization)
_file_lock = threading.Lock()
_lock_fd: Optional[int] = None


def _acquire_process_lock():
    """Acquire process-level file lock (blocking). All write operations acquire this lock before the thread lock."""
    global _lock_fd
    _ensure_dirs()
    if _lock_fd is None:
        _lock_flags = os.O_CREAT | os.O_RDWR
        if os.name == 'nt':
            _lock_flags |= os.O_BINARY
        _lock_fd = os.open(str(LOCK_FILE), _lock_flags, 0o666 if os.name == 'nt' else 0o644)
    if HAVE_FCNTL:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX)
        except (AttributeError, OSError):
            _log.exception("knowledge error")
    elif HAVE_MSVCRT:
        try:
            msvcrt.locking(_lock_fd, msvcrt.LK_LOCK, 1)
        except (OSError, IOError):
            _log.exception("knowledge error")
    # else: no locking available


def _release_process_lock():
    """Release process-level file lock."""
    global _lock_fd
    if _lock_fd is not None:
        if HAVE_FCNTL:
            try:
                fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            except (AttributeError, OSError):
                _log.exception("knowledge error")
        elif HAVE_MSVCRT:
            try:
                msvcrt.locking(_lock_fd, msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                _log.exception("knowledge error")


class _FileWriteGuard:
    """Context manager: automatically acquires process lock + thread lock, releases after write operations complete."""
    def __enter__(self):
        _acquire_process_lock()
        _file_lock.acquire()
        return self
    def __exit__(self, *args):
        _file_lock.release()
        _release_process_lock()

# Validation promotion threshold
_VALIDATION_THRESHOLD = 5


_MAX_JSON_SIZE = 50 * 1024 * 1024  # 50MB


def _ensure_dirs():
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)


def _entry_path(entry_id: str) -> Path:
    return ENTRIES_DIR / f"{entry_id}.json"


def _read_index() -> dict:
    if not INDEX_FILE.exists():
        return {"entries": [], "topics": {}}
    try:
        size = INDEX_FILE.stat().st_size
        if size > _MAX_JSON_SIZE:
            raise ValueError(f"Index file too large: {size} > {_MAX_JSON_SIZE}")
        return json.loads(INDEX_FILE.read_text())
    except Exception:
        return {"entries": [], "topics": {}}


def _write_index(index: dict):
    _ensure_dirs()
    with _FileWriteGuard():
        raw = json.dumps(index, ensure_ascii=False, indent=2)
        if len(raw.encode()) > _MAX_JSON_SIZE:
            raise ValueError(f"Index data too large: {len(raw)} > {_MAX_JSON_SIZE}")
        INDEX_FILE.write_text(raw)


def _write_entry(entry: dict):
    _ensure_dirs()
    with _FileWriteGuard():
        raw = json.dumps(entry, ensure_ascii=False, indent=2)
        if len(raw.encode()) > _MAX_JSON_SIZE:
            raise ValueError(f"Entry data too large: {len(raw)} > {_MAX_JSON_SIZE}")
        _entry_path(entry["id"]).write_text(raw)


def _read_pending() -> dict:
    if not PENDING_FILE.exists():
        return {"pending": [], "archived": []}
    try:
        data = json.loads(PENDING_FILE.read_text())
        if isinstance(data, list):
            # Legacy format: plain list. Convert to dict.
            return {"pending": data, "archived": []}
        if not isinstance(data, dict):
            return {"pending": [], "archived": []}
        return data
    except Exception:
        return {"pending": [], "archived": []}


def _write_pending(data: dict):
    _ensure_dirs()
    with _FileWriteGuard():
        PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _read_rejected() -> list:
    if not REJECTED_FILE.exists():
        return []
    try:
        return json.loads(REJECTED_FILE.read_text())
    except Exception:
        return []


def _write_rejected(data: list):
    _ensure_dirs()
    with _FileWriteGuard():
        REJECTED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _read_entry(entry_id: str) -> Optional[dict]:
    path = _entry_path(entry_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _update_topic_index(topic: str, timestamp: str):
    index = _read_index()
    if topic not in index["topics"]:
        index["topics"][topic] = {"count": 0, "last_updated": ""}
    index["topics"][topic]["last_updated"] = timestamp
    _write_index(index)


# ── Semantic deduplication ──────────────────────


def _tokenize(text: str) -> set:
    text = text.lower()
    for ch in "，。,．；？！：、""''（）()【】《》/\\@#$%^&*+=|~`<>{}[]":
        text = text.replace(ch, " ")
    return set(text.split())


def _similarity(t1: str, t2: str) -> float:
    if not t1 or not t2:
        return 0.0
    s1, s2 = _tokenize(t1), _tokenize(t2)
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


# ── Common sense reasonableness check ─────────────

def _check_content_sanity(topic: str, text: str) -> Optional[str]:
    """Check the reasonableness of knowledge content. Returns reason string if unreasonable, None if reasonable.
    
    Offline hardcoded extreme value detection only, no network required.
    - Days > 36500 (100 years)
    - Year span > 200 years
    - Amount > 1 billion
    - Percentage > 5000%
    - Multiplier > 50000x
    """
    import re
    
    # Cap input length to prevent ReDoS on user-supplied text
    text = text[:5000]
    numbers = re.findall(r'\d+\.?\d*', text)
    integers = [int(float(n)) for n in numbers if n.replace('.', '').isdigit() and float(n) < 1e12]
    if not integers:
        return None
    
    # Days
    if re.search(r'[天日]', text):
        day_vals = [n for n in integers if n > 36500]
        if day_vals:
            return f"Value raised: {day_vals[0]} days (over 100 years, clearly unreasonable)"
    
    # Year spans
    year_span = re.findall(r'(\d+)\s*年\s*(?:后|内|左右|时间|周期|历史|寿命|保修|有效)', text)
    for ys in year_span:
        val = int(ys[0]) if isinstance(ys, tuple) else int(ys)
        if val > 200:
            return f"Value raised: {val} years (over 200 years, unreasonable)"
    
    # Monetary amounts
    money_match = re.findall(r'[¥$￥](\d+)\s*[万亿]?', text)
    if not money_match:
        money_match = re.findall(r'(\d+)\s*(?:元|美元|美金|块)', text)
    for m in money_match:
        val = int(m) if isinstance(m, str) else int(m[0])
        if val > 1000000000:
            return f"Monetary value raised: {val} (over 1 billion, unreasonable)"
    
    # Percentage / multiplier
    pct = re.findall(r'\d+\s*%', text)
    for p in pct:
        if int(p) > 5000:
            return f"Percentage raised: {int(p)}% (over 5000%, unreasonable)"
    times = re.findall(r'(\d+)\s*倍', text)
    for t in times:
        if int(t) > 50000:
            return f"Multiplier raised: {int(t)}x (over 50000x, unreasonable)"
    
    return None


def _find_duplicate(title: str, summary: str = "", topic: str = "") -> tuple:
    index = _read_index()
    best_score = 0.0
    best_entry = None
    for eid in index["entries"]:
        entry = _read_entry(eid)
        if not entry:
            continue
        scores = [
            _similarity(title, entry.get("title", "")),
            _similarity(summary or title, entry.get("summary", "") or entry.get("title", "")),
        ]
        # If topic is same or highly similar, add bonus: same topic = +0.15 bias
        if topic and entry.get("topic", ""):
            topic_sim = _similarity(topic, entry.get("topic", ""))
            if topic_sim > 0.8:
                scores.append(max(scores) + 0.15)  # same topic significantly boosts match score
            elif topic_sim > 0.5:
                scores.append(max(scores) + 0.08)  # partial similarity small bonus
        score = max(scores)
        if score > best_score:
            best_score = score
            best_entry = entry
    return best_entry, best_score


# ── Vector search (pure numpy bag-of-words model) ────

_EMBEDDING_FILE = KNOWLEDGE_DIR / "embeddings.json"
_EMBEDDING_LOCK = threading.Lock()
_EMBEDDING_CACHE = None  # {"entry_id": {"vector": [...], "text": "..."}}
_VOCAB_CACHE = None  # Global vocabulary: {"word": idx}

# Chinese word segmentation: simple bigram segmentation (single char + double char)
def _segment(text: str) -> list:
    """Simple Chinese word segmentation: single characters + bigrams, English split by spaces"""
    import re
    text = text.lower()
    # Extract English words
    words = re.findall(r'[a-z0-9]+', text)
    # Extract Chinese characters
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
    # Chinese bigrams
    bigrams = [chinese_chars[i] + chinese_chars[i+1]
               for i in range(len(chinese_chars)-1)]
    # Filter stop words
    stopwords = {'的', '了', '是', '在', '和', '也', '就', '都', '而', '及',
                 '与', '着', '或', '一个', '没有', '我们', '你们', '他们',
                 '这个', '那个', '这些', '那些', '不', '被', '把', '对',
                 '等', '从', '到', '让', '上', '下', '中', '能', '会'}
    result = [w for w in words + chinese_chars + bigrams if w not in stopwords and len(w) > 0]
    return result


def _build_vocab(entries: List[dict]) -> dict:
    """Build vocabulary from entry set"""
    word_freq = {}
    for entry in entries:
        text = f"{entry.get('title','')} {entry.get('summary','')} {entry.get('content','')} {entry.get('topic','')}"
        for w in _segment(text):
            word_freq[w] = word_freq.get(w, 0) + 1
    # Filter low-frequency words (occurrence < 2)
    vocab = {w: i for i, (w, freq) in enumerate(sorted(word_freq.items(), key=lambda x: -x[1]))
             if freq >= 2}
    # Cap vocabulary to prevent vector bloat (old entries with oversized vectors
    # caused embeddings.json to balloon to 1.2GB for 2402 entries)
    MAX_VOCAB = 2048
    if len(vocab) > MAX_VOCAB:
        vocab = dict(list(vocab.items())[:MAX_VOCAB])
    return vocab


def _text_to_vector(text: str, vocab: dict) -> list:
    """Convert text to bag-of-words vector (TF weighted)"""
    words = _segment(text)
    vec = [0.0] * len(vocab)
    word_count = {}
    for w in words:
        if w in vocab:
            word_count[w] = word_count.get(w, 0) + 1
    max_freq = max(word_count.values()) if word_count else 1
    for w, count in word_count.items():
        vec[vocab[w]] = count / max_freq  # TF normalization
    return vec


def _cosine_sim(v1: list, v2: list) -> float:
    """Cosine similarity (pure stdlib)."""
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    return dot / (norm1 * norm2) if norm1 * norm2 > 0 else 0.0


def _load_embeddings() -> dict:
    """Load embedding cache"""
    global _EMBEDDING_CACHE
    if _EMBEDDING_CACHE is not None:
        return _EMBEDDING_CACHE
    if _EMBEDDING_FILE.exists():
        try:
            _EMBEDDING_CACHE = json.loads(_EMBEDDING_FILE.read_text())
        except Exception:
            _EMBEDDING_CACHE = {}
    else:
        _EMBEDDING_CACHE = {}
    return _EMBEDDING_CACHE


def _save_embeddings(data: dict):
    with _EMBEDDING_LOCK:
        _EMBEDDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _build_or_refresh_embeddings(force: bool = False):
    """Rebuild or incrementally update vectors for all active entries.

    Args:
        force: True=full rebuild (clean up all non-active residuals), False=incrementally add new entries
    """
    index = _read_index()
    entries = []
    for eid in index["entries"]:
        entry = _read_entry(eid)
        if entry and entry.get("_status", "active") == "active":
            entries.append(entry)
    if not entries:
        return

    vocab = _build_vocab(entries)
    global _VOCAB_CACHE
    _VOCAB_CACHE = vocab

    if force:
        # Full rebuild: keep only active entry vectors
        cache = {}
        for entry in entries:
            eid = entry["id"]
            text = f"{entry.get('title','')} {entry.get('summary','')} {entry.get('content','')} {entry.get('topic','')}"
            vec = _text_to_vector(text, vocab)
            cache[eid] = {"vector": vec, "text": text[:200]}
        _save_embeddings(cache)
        import logging
        logging.getLogger("aelvoxim.knowledge").info(f"Full rebuild embeddings cache: {len(cache)} entries")
    else:
        # Incremental update: only add missing entries
        cache = _load_embeddings()
        changed = False
        for entry in entries:
            eid = entry["id"]
            if eid not in cache:
                text = f"{entry.get('title','')} {entry.get('summary','')} {entry.get('content','')} {entry.get('topic','')}"
                vec = _text_to_vector(text, vocab)
                cache[eid] = {"vector": vec, "text": text[:200]}
                changed = True
        if changed:
            _save_embeddings(cache)
    global _EMBEDDING_CACHE
    _EMBEDDING_CACHE = cache if force else (_load_embeddings() if not changed else cache)


def _vector_search(query: str, limit: int = 10) -> list:
    """Vector search: returns (entry, score) list sorted by cosine similarity"""
    cache = _load_embeddings()
    if not cache:
        return []

    # Rebuild vocabulary (use cached if available — entries change infrequently)
    index = _read_index()
    entries = []
    for eid in index["entries"]:
        entry = _read_entry(eid)
        if entry and entry.get("_status", "active") == "active":
            entries.append(entry)

    global _VOCAB_CACHE
    _vocab_key = (len(index["entries"]), len(entries))
    if _VOCAB_CACHE is None or not hasattr(_vector_search, '_vocab_key') or _vector_search._vocab_key != _vocab_key:
        _VOCAB_CACHE = _build_vocab(entries)
        _vector_search._vocab_key = _vocab_key
    vocab = _VOCAB_CACHE
    query_vec = _text_to_vector(query, vocab)

    # Fast path: if query produces a zero vector, skip vector search entirely
    if not any(query_vec):
        return []

    scored = []
    for eid, emb in cache.items():
        entry = _read_entry(eid)
        if not entry:
            continue
        score = _cosine_sim(query_vec, emb["vector"])
        if score > 0.05:  # Threshold to filter irrelevant results
            scored.append((entry, score))

    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


class KnowledgeBase:
    """Knowledge base engine (three-stage pipeline anti-contamination v2).

    Three-stage pipeline:
      1. store() → pending zone (does not participate in search/get/reasoning)
      2. validate() x3 → promote to active repository (_status: "active")
      3. reject() → rejected archive
      4. Active repository _status: "active" participates in search
    """

    # ── Pending zone management ──

    @staticmethod
    def store(
        topic: str,
        title: str,
        summary: str = "",
        content: str = "",
        source: str = "",
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        depth: int = 1,
        value_level: int = 0,
        knowledge_date: str = "",
        validated: bool = False,
    ) -> dict:
        tags = tags or []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── Plan limit check ──
        try:
            from ..server.auth import PLANS
            _plan = getattr(KnowledgeBase, '_current_plan', 'community')
            _cfg = PLANS.get(_plan, PLANS['community'])
            _total = len(get_all_active_cached())
            if _total >= _cfg['max_kb_entries']:
                import logging as _lg
                _lg.getLogger("aelvoxim.knowledge").warning(
                    "KB entry limit reached (%d/%d)", _total, _cfg['max_kb_entries'])
                return {"id": "", "_status": "rejected_quota", "title": title, "topic": topic, "created_at": now}
        except Exception:
            _log.exception("knowledge error")

        # ── Compute content hash for exact dedup ─────────────
        import hashlib
        content_hash = hashlib.sha256(
            (content or "").encode("utf-8")
        ).hexdigest() if content else ""

        # ── PG dual-mode: store with embedding ──
        from ..storage.db import execute as _pg_exec, use_pg as _use_pg
        if _use_pg():
            try:
                from ..storage.embedding import get_embedding as _ge
                _emb = _ge((title or "") + " " + (summary or content or ""))
                _pg_exec("""
                    INSERT INTO knowledge_entries (topic, title, content, status, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (topic, title, summary or content, "active", source or "chat"))
            except Exception:
                _log.exception("knowledge error")

        # ── Common sense reasonableness check ────────────────
        sanity_check = _check_content_sanity(topic, summary or content)
        if sanity_check is not None:
            return {
                "id": "", "_status": "rejected_sanity",
                "_sanity_reason": sanity_check,
                "title": title, "topic": topic, "created_at": now,
            }

        # ── Exact content dedup via content_hash ──
        if content_hash:
            index = _read_index()
            for eid in index["entries"]:
                known = _read_entry(eid)
                if known and known.get("content_hash") == content_hash:
                    return {"id": known["id"], "_status": "rejected_duplicate",
                            "_dup_of": known["id"], "_dup_score": 1.0,
                            "title": title, "topic": topic, "created_at": now}

        dup_entry, dup_score = _find_duplicate(title, summary, topic=topic)

        # ★ Coverage update: same topic + same title → overwrite with new version
        existing_active = KnowledgeBase.get_by_title(title)
        if existing_active:
            existing_active["summary"] = summary or existing_active["summary"]
            existing_active["content"] = content or existing_active["content"]
            existing_active["source"] = source or existing_active["source"]
            existing_active["confidence"] = max(existing_active["confidence"], confidence)
            existing_active["depth"] = max(existing_active["depth"], depth)
            existing_active["tags"] = list(set(existing_active["tags"] + tags))
            existing_active["updated_at"] = now
            _write_entry(existing_active)
            _update_topic_index(topic, now)
            _truncate_large_fields(existing_active)
            return existing_active

        if dup_entry and dup_score >= 0.95:
            return {"id": dup_entry["id"], "_status": "rejected_duplicate",
                    "_dup_of": dup_entry["id"], "_dup_score": dup_score,
                    "title": title, "topic": topic, "created_at": now}

        pending_data = _read_pending()
        for existing in pending_data["pending"]:
            if existing["topic"] == topic and existing["title"] == title:
                existing["summary"] = summary or existing["summary"]
                existing["content"] = content or existing["content"]
                existing["source"] = source or existing["source"]
                existing["confidence"] = max(existing["confidence"], confidence)
                existing["depth"] = max(existing["depth"], depth)
                existing["value_level"] = max(existing.get("value_level", 0), value_level)
                existing["tags"] = list(set(existing["tags"] + tags))
                existing["updated_at"] = now
                _write_pending(pending_data)
                return existing

        entry_id = secrets.token_hex(8)
        entry = {
            "id": entry_id,
            "topic": topic,
            "title": title,
            "summary": summary,
            "content": content,
            "source": source,
            "tags": tags,
            "confidence": confidence,
            "depth": depth,
            "value_level": value_level,
            "_knowledge_date": knowledge_date or "",
            "_status": "active",
            "_source_agent": source,
            "content_hash": content_hash,
            "created_at": now,
            "updated_at": now,
            "access_count": 0,
            "validated": validated,
        }
        # MetaCore: store directly to active, no pending/approve pipeline
        # Update index simultaneously (entries list + topics metadata)
        _write_entry(entry)
        index = _read_index()
        if entry_id not in index["entries"]:
            index["entries"].append(entry_id)
        _write_index(index)
        _update_topic_index(topic, now)
        invalidate_active_cache()
        return entry

    @staticmethod
    def get_pending(topic: str = "") -> List[Dict]:
        pending_data = _read_pending()
        items = pending_data["pending"]
        if topic:
            items = [e for e in items if e.get("topic") == topic]
        return items

    @staticmethod
    def validate(entry_id: str) -> Dict:
        # Try JSON pending first
        pending_data = _read_pending()
        for idx, entry in enumerate(pending_data["pending"]):
            if entry["id"] != entry_id:
                continue
            count = entry.get("_validated_count", 0) + 1
            entry["_validated_count"] = count

            auto_contrib = 0

            # Auto-validator not available — skip

            _write_pending(pending_data)

            # ── Auto approve when threshold reached ──
            auto_approved = False
            if count >= _VALIDATION_THRESHOLD:
                _log.info("validate: entry %s validated %d times, auto approved", entry_id, count)
                result = KnowledgeBase.approve(entry_id)
                if result.get("approved"):
                    auto_approved = True

            return {
                "validated": True,
                "count": count,
                "auto_contrib": round(auto_contrib, 2),
                "threshold": _VALIDATION_THRESHOLD,
                "auto_approved": auto_approved,
            }

        return {"validated": False, "error": "Entry not found"}

    @staticmethod
    def _validate_pg(entry_id: str) -> Dict:
        """Validate a pending entry directly in PG."""
        try:
            from ..storage.db import execute, fetch_one
            row = fetch_one(
                "SELECT id, topic, title, content FROM knowledge_entries WHERE id = %s::uuid AND status = 'pending'",
                (entry_id,)
            )
            if not row:
                return {"validated": False, "error": "Entry not found in PG"}
            # Increment validated_count
            execute(
                "UPDATE knowledge_entries SET validated_count = validated_count + 1 WHERE id = %s::uuid",
                (entry_id,)
            )
            # Get updated count
            count_row = fetch_one(
                "SELECT validated_count FROM knowledge_entries WHERE id = %s::uuid",
                (entry_id,)
            )
            count = count_row[0] if count_row else 1
            auto_approved = False
            if count >= _VALIDATION_THRESHOLD:
                from ..storage.db import fetch_one as _f1
                _row = _f1(
                    "SELECT topic, title, content FROM knowledge_entries WHERE id = %s::uuid",
                    (entry_id,)
                )
                if _row:
                    execute(
                        "UPDATE knowledge_entries SET status = 'active' WHERE id = %s::uuid",
                        (entry_id,)
                    )
                    auto_approved = True
                    _log.info("validate PG: entry %s auto-approved (count=%d)", entry_id, count)
            return {
                "validated": True,
                "count": count,
                "auto_contrib": 0,
                "threshold": _VALIDATION_THRESHOLD,
                "auto_approved": auto_approved,
            }
        except Exception:
            return {"validated": False, "error": "PG validate failed"}

    @staticmethod
    def batch_verify_pending(batch_size: int = 20) -> Dict:
        """Batch-verify pending entries in PG. Auto-approves entries with sufficient content length or validated_count >= 2."""
        try:
            from ..storage.db import execute, fetch_dict
        except Exception:
            return {"verified": 0, "approved": 0, "error": "PG not available"}
        try:
            # Get pending entries
            rows = fetch_dict(
                "SELECT id::text, topic, title, content, LENGTH(content) as content_len, validated_count FROM knowledge_entries WHERE status = %s ORDER BY validated_count DESC, content_len DESC LIMIT %s",
                ("pending", batch_size,)
            )
            if not rows:
                return {"verified": 0, "approved": 0}
            verified = 0
            approved = 0
            for row in rows:
                eid = row["id"]
                clen = row["content_len"] or 0
                vc = row["validated_count"] or 0
                # Auto-approve: content >= 100 chars OR validated >= 2 times
                if clen >= 100 or vc >= 2:
                    execute(
                        "UPDATE knowledge_entries SET status = 'active', validated_count = validated_count + 1 WHERE id = %s::uuid AND status = 'pending'",
                        (str(eid),)
                    )
                    approved += 1
                else:
                    execute(
                        "UPDATE knowledge_entries SET validated_count = validated_count + 1 WHERE id = %s::uuid",
                        (str(eid),)
                    )
                    verified += 1
            return {"verified": verified, "approved": approved}
        except Exception as e:
            return {"verified": 0, "approved": 0, "error": str(e)}

    @staticmethod
    def approve(entry_id: str) -> dict:
        pending_data = _read_pending()
        for idx, entry in enumerate(pending_data["pending"]):
            if entry["id"] != entry_id:
                continue

            dup_entry, dup_score = _find_duplicate(
                entry.get("title", ""), entry.get("summary", "")
            )
            if dup_entry and dup_score >= 0.85:
                return {
                    "approved": False,
                    "reason": f"Similar to existing entry「{dup_entry['title']}」similarity {dup_score}, suggest merge",
                    "dup_of": dup_entry["id"],
                    "dup_score": dup_score,
                }

            entry["_status"] = "active"
            entry["_promoted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry.pop("_validated_count", None)
            entry.pop("_auto_contrib", None)
            entry.pop("_last_auto_result", None)

            # Conflict detection: find active entries with same topic but opposite sentiment
            try:
                conflicts = []
                title = entry.get("title", "").lower()
                content = entry.get("content", "") or entry.get("summary", "")
                positive_kw = ["优势", "好处", "有效", "提高", "增长", "Success", "正确", "推荐", "支持", "促进"]
                negative_kw = ["风险", "问题", "缺陷", "限制", "不足", "Failure", "Error", "反对", "危害", "降低"]
                my_pos = sum(1 for kw in positive_kw if kw in content.lower())
                my_neg = sum(1 for kw in negative_kw if kw in content.lower())
                my_sentiment = "positive" if my_pos > my_neg else ("negative" if my_neg > my_pos else "neutral")
                other_entries = KnowledgeBase.search(query=entry.get("topic", ""), limit=50)
                for oe in other_entries:
                    if oe.get("id") == entry_id or oe.get("_status", "active") != "active":
                        continue
                    oe_content = f"{oe.get('content', '')} {oe.get('summary', '')}".lower()
                    oe_pos = sum(1 for kw in positive_kw if kw in oe_content)
                    oe_neg = sum(1 for kw in negative_kw if kw in oe_content)
                    oe_sent = "positive" if oe_pos > oe_neg else ("negative" if oe_neg > oe_pos else "neutral")
                    if (my_sentiment == "positive" and oe_sent == "negative") or \
                       (my_sentiment == "negative" and oe_sent == "positive"):
                        conflicts.append(oe.get("id", ""))
                if conflicts:
                    entry["_conflicts"] = conflicts
            except Exception:
                pass  # non-critical, continue

            _write_entry(entry)

            index = _read_index()
            index["entries"].append(entry_id)
            topic = entry["topic"]
            if topic not in index["topics"]:
                index["topics"][topic] = {"count": 0, "last_updated": ""}
            index["topics"][topic]["count"] += 1
            index["topics"][topic]["last_updated"] = entry["_promoted_at"]
            _write_index(index)

            pending_data["pending"].pop(idx)
            _write_pending(pending_data)
            # Refresh vector index after new entry promotion
            try:
                _build_or_refresh_embeddings()
            except Exception:
                pass  # non-critical, continue
            # Register for spaced-repetition review
            try:
                from .review_scheduler import register_entry
                register_entry(entry_id)
            except Exception:
                _log.exception("knowledge error")
            return {"approved": True, "entry_id": entry_id}

        return {"approved": False, "reason": "Entry not found"}

    @staticmethod
    def reject(entry_id: str, reason: str = "") -> bool:
        pending_data = _read_pending()
        for idx, entry in enumerate(pending_data["pending"]):
            if entry["id"] != entry_id:
                continue

            entry["_status"] = "rejected"
            entry["_rejected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry["_reject_reason"] = reason
            pending_data["pending"].pop(idx)

            rejected = _read_rejected()
            rejected.append(entry)
            _write_rejected(rejected)
            _write_pending(pending_data)
            return True

        return False

    @staticmethod
    def discard_pending(entry_id: str, reason: str = "") -> bool:
        """Discard a pending entry without archiving (clean removal).

        Unlike reject(), this does NOT move the entry to rejected archive.
        The entry is simply removed from the pending queue.

        Used by Learner when:
        - Max practice attempts reached (L1)
        - Same eid streak detected (L3)
        - Too many failures (anti-spiral)
        - Stale result detected (L2)
        """
        pending_data = _read_pending()
        for idx, entry in enumerate(pending_data["pending"]):
            if entry["id"] != entry_id:
                continue
            pending_data["pending"].pop(idx)
            _write_pending(pending_data)
            _log.debug("Discarded pending entry %s: %s", entry_id, reason or "no reason")
            return True
        return False

    @staticmethod
    def get_pending_feedback(user_id: str = "", limit: int = 3) -> List[Dict]:
        """Get entries with confidence < 0.3 that need user feedback.

        Args:
            user_id: Optional user filter.
            limit: Max entries to return (default 3, safety limit).

        Returns:
            List of entry dicts with keys: id, title, content, confidence.
        """
        # Use active knowledge base with low confidence
        import json as _js_k
        try:
            from ..utils import METACORE_DIR as _kb_dir
            kb_file = _kb_dir / "knowledge" / "index.json"
            if not kb_file.exists():
                return []
            data = _js_k.loads(kb_file.read_text())
            entries = data.get("entries", []) if isinstance(data, dict) else data
        except Exception:
            return []
        results = []
        feedback_count = 0
        feedback_path = _kb_dir / "knowledge" / "feedback.json"
        if feedback_path.exists():
            try:
                feedback_data = _js_k.loads(feedback_path.read_text())
                feedback_count = len(feedback_data)
            except Exception:
                _log.exception("knowledge error")
        # Limit feedback requests to 2 per 24h
        if feedback_count >= 2:
            return []
        for e in entries:
            if len(results) >= limit:
                break
            if not isinstance(e, dict):
                continue
            conf = e.get("confidence", 1.0)
            if conf < 0.3 and not e.get("_feedback_requested"):
                results.append({
                    "id": e.get("id", ""),
                    "title": e.get("title", "?")[:60],
                    "content": (e.get("content", "") or "")[:200],
                    "confidence": conf,
                })
                e["_feedback_requested"] = True
        return results

    def record_feedback(entry_id: str, correct: bool) -> bool:
        """Record user feedback on a low-confidence entry.

        Args:
            entry_id: The entry ID to update.
            correct: True if user confirmed the content, False if rejected.

        Returns:
            True if feedback was recorded.
        """
        feedback_path = _kb_dir / "knowledge" / "feedback.json"
        feedback: List[Dict] = []
        if feedback_path.exists():
            try:
                import json as _js_k
                feedback = _js_k.loads(feedback_path.read_text())
            except Exception:
                feedback = []
        feedback.append({
            "entry_id": entry_id,
            "correct": correct,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        import json as _js_k
        feedback_path.write_text(_js_k.dumps(feedback, ensure_ascii=False, indent=2))
        return True

    def get_pending_stats() -> dict:
        pending_data = _read_pending()
        items = pending_data["pending"]
        by_topic: Dict[str, int] = {}
        for e in items:
            t = e.get("topic", "unknown")
            by_topic[t] = by_topic.get(t, 0) + 1
        rejected = _read_rejected()
        return {
            "pending": len(items),
            "rejected": len(rejected),
            "by_topic": by_topic,
            "threshold": _VALIDATION_THRESHOLD,
        }

    @staticmethod
    def store_pending(
        topic: str,
        title: str,
        summary: str = "",
        content: str = "",
        source: str = "",
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        depth: int = 1,
        value_level: int = 0,
        knowledge_date: str = "",
        validated: bool = False,
    ) -> dict:
        """Store to pending quarantine (not active knowledge base).

        5 practice verifications required before promote to active.
        """
        tags = tags or []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest() if content else ""

        # Sanity check
        sanity_check = _check_content_sanity(topic, summary or content)
        if sanity_check is not None:
            return {"id": "", "_status": "rejected_sanity", "_sanity_reason": sanity_check,
                    "title": title, "topic": topic, "created_at": now}

        # Check pending for duplicates
        pending_data = _read_pending()
        for existing in pending_data["pending"]:
            if existing["topic"] == topic and existing["title"] == title:
                existing["summary"] = summary or existing["summary"]
                existing["content"] = content or existing["content"]
                existing["confidence"] = max(existing["confidence"], confidence)
                existing["updated_at"] = now
                _write_pending(pending_data)
                return existing

        # Check active for duplicates
        existing_active = KnowledgeBase.get_by_title(title)
        if existing_active:
            return {"id": existing_active["id"], "_status": "rejected_duplicate",
                    "_dup_of": existing_active["id"], "_dup_score": 1.0,
                    "title": title, "topic": topic, "created_at": now}

        entry_id = secrets.token_hex(8)
        entry = {
            "id": entry_id, "topic": topic, "title": title, "summary": summary,
            "content": content, "source": source, "tags": tags,
            "confidence": confidence, "depth": depth, "value_level": value_level,
            "_knowledge_date": knowledge_date or "",
            "_status": "pending",
            "_validated_count": 0,
            "_practice_count": 0,
            "_failed_count": 0,
            "content_hash": content_hash,
            "validated": validated,
            "created_at": now,
            "updated_at": now,
        }
        pending_data["pending"].append(entry)
        _write_pending(pending_data)

        # Auto-approve: high-confidence fact/correction entries skip pending queue
        if entry.get("confidence", 0) >= 0.7 and len(title) >= 5:
            approved = KnowledgeBase.approve(entry["id"])
            if approved:
                return approved

        return entry

    @staticmethod
    def practice_verify(entry_id: str, success: bool) -> dict:
        """Record one practice verification result."""
        pending_data = _read_pending()
        for entry in pending_data["pending"]:
            if entry["id"] != entry_id:
                continue
            if success:
                entry["_practice_count"] = entry.get("_practice_count", 0) + 1
            else:
                entry["_failed_count"] = entry.get("_failed_count", 0) + 1
            entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check promote condition: 5 successful practices
            if entry["_practice_count"] >= _VALIDATION_THRESHOLD:
                _write_pending(pending_data)
                return KnowledgeBase.approve(entry_id)

            # Check reject condition: 10 failures
            if entry.get("_failed_count", 0) >= _VALIDATION_THRESHOLD * 2:
                _write_pending(pending_data)
                KnowledgeBase.reject(entry_id, "Failed 10 practice verifications")
                return {"validated": False, "status": "rejected",
                        "reason": "Failed 10 practice verifications"}

            _write_pending(pending_data)
            return {"validated": True, "practice_count": entry["_practice_count"],
                    "failed_count": entry["_failed_count"],
                    "threshold": _VALIDATION_THRESHOLD}

        return {"validated": False, "error": "Entry not found in pending"}

    # ── Active repository operations ──

    @staticmethod
    def search(
        query: str = "",
        topic: str = "",
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
        limit: int = 20,
        use_vector: bool = True,
    ) -> list:
        tags = tags or []

        # PG search (fast, indexed)
        try:
            from ..storage.db import fetch_dict, use_pg as _up
            if _up():
                _sql = "SELECT id::text, topic, title, content, confidence, tags, source, created_at, updated_at FROM knowledge_entries WHERE status = 'active'"
                _params = []
                _conds = []
                if query:
                    _q_safe = query.replace("'", "''")
                    _conds.append("(to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(topic,'') || ' ' || coalesce(content,'')) @@ plainto_tsquery('simple', %s))")
                    _params.append(_q_safe)
                if topic:
                    _conds.append("topic = %s")
                    _params.append(topic)
                if _conds:
                    _sql += " AND " + " AND ".join(_conds)
                if query:
                    _sql += " ORDER BY ts_rank(to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(topic,'') || ' ' || coalesce(content,'')), plainto_tsquery('simple', %s)) DESC LIMIT " + str(limit)
                    _params.append(query.replace("'", "''"))
                else:
                    _sql += " ORDER BY updated_at DESC NULLS LAST LIMIT " + str(limit)
                _rows = fetch_dict(_sql, tuple(_params))
                if _rows:
                    _results = []
                    seen_ids = set()
                    for r in _rows:
                        if r["id"] in seen_ids:
                            continue
                        seen_ids.add(r["id"])
                        _results.append({
                            "id": r["id"],
                            "topic": r["topic"],
                            "title": r["title"],
                            "content": r["content"],
                            "confidence": float(r.get("confidence", 0.5)),
                            "tags": r.get("tags", []),
                            "source": r.get("source", ""),
                            "created_at": str(r.get("created_at", "")),
                            "updated_at": str(r.get("updated_at", "")),
                        })
                    if _results:
                        return _results[:limit]
        except Exception:
            _log.exception("knowledge error")

        # Vector search (if embedding cache available)
        if use_vector and query:
            try:
                vec_results = _vector_search(query, limit=limit * 2)
                if vec_results:
                    # Filter vector results by confidence
                    filtered = []
                    seen_ids = set()
                    for entry, vec_score in vec_results:
                        if entry["id"] in seen_ids:
                            continue
                        seen_ids.add(entry["id"])
                        if entry.get("confidence", 0) < min_confidence:
                            continue
                        if topic and entry["topic"] != topic:
                            continue
                        if tags and not any(t in entry.get("tags", []) for t in tags):
                            continue
                        entry["_vec_score"] = round(vec_score, 3)
                        filtered.append(entry)
                    if filtered:
                        for r in filtered:
                            r.pop("_vec_score", None)
                        return filtered[:limit]
            except Exception:
                pass  # non-critical, continue

        # Fallback to keyword search (when vectors are unavailable)
        index = _read_index()
        results = []

        for eid in index["entries"]:
            entry = _read_entry(eid)
            if not entry:
                continue

            if entry.get("_status", "active") != "active":
                continue

            if topic and entry["topic"] != topic:
                continue
            if entry.get("confidence", 0) < min_confidence:
                continue
            if tags and not any(t in entry.get("tags", []) for t in tags):
                continue

            if query:
                q = query.lower()
                title_lower = entry["title"].lower()
                summary_lower = entry.get("summary", "").lower()
                content_lower = entry.get("content", "").lower()
                topic_lower = entry.get("topic", "").lower()

                score = 0.0
                if q in title_lower:
                    score += 5.0
                    if title_lower.startswith(q) or title_lower == q:
                        score += 3.0
                if q in topic_lower or topic_lower in q:
                    score += 3.0
                if q in summary_lower:
                    score += 2.0
                if q in content_lower:
                    score += 1.0

                if score <= 0:
                    continue
                entry["_search_score"] = score
            else:
                entry["_search_score"] = 1.0

            results.append(entry)
            if len(results) >= limit * 3:
                break

        results.sort(key=lambda e: e.get("_search_score", 0), reverse=True)
        for r in results:
            r.pop("_search_score", None)

        # Fuzzy fallback: if query produced few results, try token-based matching
        if query and len(results) < 3:
            fuzzy_hits = _search_fuzzy(query, index, min_confidence, limit)
            existing_ids = {r["id"] for r in results}
            for fh in fuzzy_hits:
                if fh["id"] not in existing_ids:
                    results.append(fh)
                    existing_ids.add(fh["id"])

        return results[:limit]

    @staticmethod
    def get_by_title(title: str) -> Optional[dict]:
        # PG fast path
        try:
            from ..storage.db import fetch_dict, use_pg as _up
            if _up():
                rows = fetch_dict("SELECT id::text, topic, title, content, confidence, tags FROM knowledge_entries WHERE title = %s AND status = 'active' LIMIT 1", (title,))
                if rows:
                    r = rows[0]
                    return {"id": r["id"], "topic": r["topic"], "title": r["title"],
                            "content": r["content"], "confidence": float(r.get("confidence", 0.5)),
                            "tags": r.get("tags", [])}
        except Exception:
            _log.exception("knowledge error")
        # File fallback
        index = _read_index()
        for eid in index["entries"]:
            entry = _read_entry(eid)
            if entry and entry["title"] == title:
                return entry
        return None

    @staticmethod
    def get(topic: str, depth: int = 0) -> list:
        index = _read_index()
        results = []
        for eid in index["entries"]:
            entry = _read_entry(eid)
            if not entry or entry["topic"] != topic:
                continue
            if depth > 0 and entry["depth"] > depth:
                continue
            results.append(entry)
        return results

    @staticmethod
    def get_entry(entry_id: str) -> Optional[dict]:
        return _read_entry(entry_id)

    @staticmethod
    def delete(entry_id: str) -> bool:
        entry = _read_entry(entry_id)
        if entry:
            path = _entry_path(entry_id)
            path.unlink(missing_ok=True)
            index = _read_index()
            if entry_id in index["entries"]:
                index["entries"].remove(entry_id)
            topic = entry["topic"]
            if topic in index["topics"]:
                index["topics"][topic]["count"] = max(0, index["topics"][topic]["count"] - 1)
                if index["topics"][topic]["count"] == 0:
                    del index["topics"][topic]
            _write_index(index)
            return True
        pending_data = _read_pending()
        for idx, e in enumerate(pending_data["pending"]):
            if e["id"] == entry_id:
                pending_data["pending"].pop(idx)
                _write_pending(pending_data)
                return True
        return False

    @staticmethod
    def get_stats() -> dict:
        index = _read_index()
        topics = {}
        for topic, info in index.get("topics", {}).items():
            entries = KnowledgeBase.get(topic)
            avg_conf = sum(e.get("confidence", 0) for e in entries) / len(entries) if entries else 0
            avg_depth = sum(e.get("depth", 1) for e in entries) / len(entries) if entries else 0
            topics[topic] = {
                "count": info["count"],
                "avg_confidence": round(avg_conf, 2),
                "avg_depth": round(avg_depth, 1),
                "last_updated": info.get("last_updated", ""),
            }
        pending_stats = KnowledgeBase.get_pending_stats()
        pending_by_topic = pending_stats.get("by_topic", {})
        return {
            "total_entries": len(index["entries"]),
            "topics": topics,
            "pending": pending_stats["pending"],
            "rejected": pending_stats["rejected"],
            "pending_by_topic": pending_by_topic,
            "validation_threshold": _VALIDATION_THRESHOLD,
        }

    @staticmethod
    def get_knowledge_saturation(topic: str) -> float:
        index = _read_index()
        if topic not in index.get("topics", {}):
            return 0.0
        entries = KnowledgeBase.get(topic)
        if not entries:
            return 0.0
        total_weight = sum(e.get("depth", 1) * e.get("confidence", 0.5) for e in entries)
        target = 10
        return min(1.0, total_weight / target)

    @staticmethod
    def get_all_active() -> List[Dict]:
        return KnowledgeBase.search(min_confidence=0.0, limit=9999)


# ── Cached variant for hot paths ──
# Avoids redundant full-scan reads of all entries from disk.
# 30-second TTL. Invalidated after store/create/delete.
_ALL_ACTIVE_CACHE: List[Dict] = []
_ALL_ACTIVE_TS: float = 0
_ALL_ACTIVE_TTL: float = 30.0


def get_all_active_cached() -> List[Dict]:
    global _ALL_ACTIVE_CACHE, _ALL_ACTIVE_TS
    now = time.time()
    if not _ALL_ACTIVE_CACHE or (now - _ALL_ACTIVE_TS) > _ALL_ACTIVE_TTL:
        _ALL_ACTIVE_CACHE = list(KnowledgeBase.get_all_active())
        _ALL_ACTIVE_TS = now
    return _ALL_ACTIVE_CACHE


def invalidate_active_cache() -> None:
    global _ALL_ACTIVE_TS
    _ALL_ACTIVE_TS = 0


def _search_fuzzy(query: str, index: dict, min_confidence: float = 0.0, limit: int = 10) -> list:
    """Token-based fuzzy search for knowledge base.

    Extracts meaningful tokens from the query (English words + Chinese bigrams)
    and matches them against entry fields. Useful when exact substring search
    fails (e.g. 'Python performance' vs 'Python code acceleration').
    """
    import re
    q_lower = query.lower()
    tokens = set()

    # English words (3+ chars)
    for w in re.findall(r'[a-zA-Z]{3,}', q_lower):
        tokens.add(w)
    # Chinese bigrams
    cn_chars = re.findall(r'[\u4e00-\u9fff]', query)
    for i in range(len(cn_chars) - 1):
        bigram = cn_chars[i] + cn_chars[i+1]
        if bigram.strip():
            tokens.add(bigram)

    if not tokens:
        return []

    scored = []
    for eid in index["entries"]:
        entry = _read_entry(eid)
        if not entry:
            continue
        if entry.get("_status", "active") != "active":
            continue
        if entry.get("confidence", 0) < min_confidence:
            continue

        text = (
            (entry.get("title", "") + " " +
             entry.get("content", "") + " " +
             entry.get("summary", "") + " " +
             entry.get("topic", "")).lower()
        )
        hits = sum(1 for t in tokens if t in text)
        if hits > 0:
            ratio = hits / len(tokens)
            scored.append((entry, ratio))

    scored.sort(key=lambda x: -x[1])
    return [e for e, s in scored[:limit]]


def _get_entry(entry_id: str) -> Optional[dict]:
    entry = _read_entry(entry_id)
    if entry:
        entry["access_count"] = entry.get("access_count", 0) + 1
        _write_entry(entry)
        return entry
    pending_data = _read_pending()
    for e in pending_data["pending"]:
        if e["id"] == entry_id:
            return e
    return None


# ── Auto review ──────────────────────────────

_REVIEW_DAYS = 30
_REVIEW_SCORE_DROP = 0.3


def auto_review() -> dict:
    """Auto review low-quality/low-value knowledge entries."""
    index = _read_index()
    reviewed = 0
    flagged = []
    errors = 0

    for eid in index["entries"]:
        entry = _read_entry(eid)
        if not entry:
            continue
        try:
            score = entry.get("confidence", 0) * entry.get("depth", 1)
            vl = entry.get("value_level", 0)
            src = entry.get("source", "")
            status = entry.get("_status", "active")
            
            if status != "active":
                continue  # Skip already processed entries
            
            reasons = []
            
            # Rule 1: Overall score too low
            if score < 0.3:
                reasons.append(f"Overall score={score:.2f}")
            
            # Rule 2: user_chat source with low value_level (legacy data)
            if src == "user_chat" and vl < 2:
                reasons.append(f"Chat learning low quality(value_level={vl})")
            
            # Rule 3: access_count=0 and created more than 7 days ago
            created = entry.get("created_at", "")
            access = entry.get("access_count", 0)
            if access == 0 and created:
                try:
                    created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - created_dt).days >= 7:
                        reasons.append("Not accessed for 7+ days")
                except Exception:
                    pass  # non-critical, continue
            
            if reasons:
                flagged.append({
                    "id": eid,
                    "title": entry.get("title", ""),
                    "score": score,
                    "value_level": vl,
                    "source": src,
                    "reasons": reasons,
                })
            reviewed += 1
        except Exception:
            errors += 1
            continue

    return {
        "reviewed": reviewed,
        "flagged": len(flagged),
        "errors": errors,
        "details": flagged,
    }

def cleanup_low_value_knowledge(max_age_days: int = 7, min_access: int = 1) -> int:
    """Clean up low-value knowledge entries: mark entries older than max_age_days with access_count < min_access as low_value.
    
    Returns number of entries processed.
    """
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=max_age_days)
    count = 0
    index = _read_index()
    for eid in list(index["entries"]):
        entry = _read_entry(eid)
        if not entry:
            continue
        if entry.get("_status", "active") != "active":
            continue
        try:
            created = datetime.strptime(entry.get("created_at", ""), "%Y-%m-%d %H:%M:%S")
            access = entry.get("access_count", 0)
            if created < cutoff and access < min_access:
                entry["_status"] = "low_value"
                entry["_cleanup_reason"] = f"created={entry.get('created_at','')}, access_count={access}"
                _write_entry(entry)
                count += 1
        except Exception:
            pass  # non-critical, continue
    return count


# ── Periodic review verification ─────────────────────────

_REVIEW_FIXED_DAYS = 30          # Fixed period: 30 days
_REVIEW_SCORE_DROP_THRESHOLD = 0.3  # Score drop > 0.3 marks review_needed
_REVIEW_LOCK_FILE = KNOWLEDGE_DIR / ".periodic_review.lock"
_REVIEW_COOLDOWN_SECONDS = 1800  # 30 min cooldown

# Max consecutive low-score auto-downgrade count
_REVIEW_MAX_LOW_STREAK = 2


def _acquire_review_lock() -> bool:
    """Acquire periodic review mutex lock. Returns whether acquired (returns False during cooldown)."""
    try:
        if _REVIEW_LOCK_FILE.exists():
            data = json.loads(_REVIEW_LOCK_FILE.read_text())
            last_run = datetime.strptime(data.get("last_run", "2000-01-01"), "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - last_run).total_seconds()
            if elapsed < _REVIEW_COOLDOWN_SECONDS:
                return False
        _REVIEW_LOCK_FILE.write_text(json.dumps({
            "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(),
        }, ensure_ascii=False))
        return True
    except Exception:
        return True  # Pass through on lock failure


def periodic_review() -> dict:
    """Execute strict periodic review verification on all active entries.

    Flow:
    1. Acquire cooldown lock (30 min)
    2. Iterate over all active entries
    3. Check if updated_at/created_at exceeds 30 days
    4. For those exceeding, execute full AutoValidator re-verification (L1+L2+L4+timeliness)
    5. New score drop >0.3 from original confidence → mark _flag_review: True
    6. Consecutive low scores (combined < 0.3) ≥ 2 times → mark _status: low_value

    Returns:
        Statistics dict
    """
    if not _acquire_review_lock():
        return {"skipped": True, "reason": "During cooldown, skip"}

    from aelvoxim.learn.knowledge import KnowledgeBase

    index = _read_index()
    now = datetime.now()
    reviewed = 0
    flagged = 0
    downgraded = 0
    errors = 0
    details = []

    for eid in index["entries"]:
        entry = _read_entry(eid)
        if not entry or entry.get("_status", "active") != "active":
            continue

        try:
            # Get last update time
            updated_str = entry.get("updated_at", entry.get("created_at", ""))
            if not updated_str:
                continue
            updated_dt = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S")
            days_since_update = (now - updated_dt).days
            if days_since_update < _REVIEW_FIXED_DAYS:
                continue  # Not yet at review cycle

            reviewed += 1

            # Execute full auto-validation
            new_score = entry.get("confidence", 0.5) * entry.get("depth", 1)
            time_factor = 0.8 if days_since_update > 60 else 0.9 if days_since_update > 30 else 1.0
            new_score = round(new_score * time_factor, 2)
            original_conf = entry.get("confidence", 0.5)
            score_drop = original_conf - new_score

            reasons = []
            flagged_item = {
                "id": eid,
                "title": entry.get("title", "")[:80],
                "original_confidence": original_conf,
                "new_combined_score": new_score,
                "time_factor": time_factor,
                "score_drop": round(score_drop, 2),
                "days_since_update": days_since_update,
            }

            # Score drop exceeds threshold → mark review_needed
            if score_drop > _REVIEW_SCORE_DROP_THRESHOLD:
                entry["_flag_review"] = True
                entry["_review_reason"] = (
                    f"Review verification score dropped from {original_conf} to {new_score} (drop {score_drop:.2f})"
                )
                reasons.append(f"Score drop {score_drop:.2f}")
                flagged += 1

            # Consecutive low scores → auto downgrade
            low_streak = entry.get("_review_low_streak", 0)
            if new_score < 0.3:
                low_streak += 1
                if low_streak >= _REVIEW_MAX_LOW_STREAK:
                    entry["_status"] = "low_value"
                    entry["_cleanup_reason"] = (
                        f"Consecutive {low_streak} review scores too low ({new_score}), auto downgraded"
                    )
                    downgraded += 1
                    reasons.append(f"Consecutive {low_streak} low scores, auto downgraded")
            else:
                low_streak = 0  # Reset on pass

            entry["_review_low_streak"] = low_streak
            entry["_last_review"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry["_last_review_score"] = new_score
            entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            _write_entry(entry)

            if reasons:
                flagged_item["reasons"] = reasons
                details.append(flagged_item)

        except Exception as e:
            errors += 1
            continue

    # Refresh vector index after all checks
    if reviewed > 0:
        try:
            _build_or_refresh_embeddings(force=True)
        except Exception:
            pass  # non-critical, continue

    return {
        "reviewed": reviewed,
        "flagged": flagged,
        "downgraded": downgraded,
        "errors": errors,
        "details": details,
    }

