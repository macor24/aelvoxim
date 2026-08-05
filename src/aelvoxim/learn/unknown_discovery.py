"""
metacore.learn.unknown_discovery — Passive unknown term discovery.

Scans recent learner outputs for terms not yet in the entity graph,
and injects promising candidates into the curiosity engine's queue.

Runs inside _cognition_tick — background only, no user interaction.
"""

from __future__ import annotations
import hashlib
import logging
import re
import time
from typing import Callable, Dict, List, Set

from .knowledge import KnowledgeBase

import logging
_log = logging.getLogger("aelvoxim.learn.unknown_discovery")

# ── English / Chinese stop lists ──

_EN_STOP: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "from",
    "up", "down", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "as", "until", "while",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "it", "its", "i", "you", "he", "she", "they", "we", "me", "him",
    "her", "them", "us", "my", "your", "his", "its", "our", "their",
    "tell", "told", "ask", "asked", "help", "need", "want", "know",
    "make", "made", "take", "took", "give", "gave", "find", "found",
    "show", "use", "used", "using", "get", "got", "let", "set", "put",
    "say", "said", "see", "look", "call", "come", "go", "went", "run",
    "try", "work", "think", "like", "tell me", "let me", "based", "also",
    "well", "much", "many", "new", "one", "two", "first", "last",
}

_CN_STOP: Set[str] = {
    "的", "了", "是", "在", "与", "和", "或", "有", "对", "以",
    "被", "从", "为", "由", "于", "向", "要", "能", "会", "这",
    "那", "它", "并", "也", "还", "但", "可", "帮", "助", "用",
    "户", "我", "你", "他", "她", "们", "都", "就", "而", "且",
    "其", "中", "上", "下", "大", "小", "多", "少", "没", "很",
    "最", "不", "好", "让", "给", "把", "将", "做", "成", "能",
    "该", "这", "哪", "什", "么", "怎", "样", "已", "经",
}

# ── Constants ──

_MAX_PENDING = 20
_SCAN_INTERVAL = 7200  # 2h base between scans (was 1h) — fewer polls, less CPU churn when idle/full
_DEDUP_WINDOW = 7200  # 2-hour dedup for re-queued candidates
_SHORT_DEDUP = 1200   # 20-min short-term hash cache
_SHORT_CACHE: Set[str] = set()  # hashes in current 20-min window
_LAST_SHORT_CLEAN: float = 0.0
_MAX_QUEUE_HISTORY = 5  # track how many times each term was queued
_queue_count: Dict[str, int] = {}  # md5_hash → queue count, for exponential backoff
_MAX_CANDIDATE_AGE = 86400  # 24h: candidates longer than this are dropped
_pending_queued_at: Dict[str, float] = {}  # candidate → timestamp when added to pending
_MAX_RETRY = 6  # max times a candidate can be re-queued before permanent discard

# ── Anti-noise / anti-polling state ──
_empty_scans: int = 0  # consecutive scans with no output → exponential backoff
_skip_log_at: Dict[str, float] = {}  # skip-reason → last log timestamp (rate-limit noisy Skipped logs)

# Core AI domains (boosted priority)
_CORE_AI_PATTERNS = [
    r'\blarge language model', r'\bLLM\b', r'\btransformer',
    r'\bRAG\b', r'\bretrieval aug',
    r'\bRLHF\b', r'\breinforcement learn',
    r'\bmulti.agent', r'\bmultiagent',
    r'\bBayesian', r'\bbayes',
    r'\battention\b', r'\bself.attention',
    r'\bprompt\b', r'\bfew.shot', r'\bchain.of.thought',
    r'\bfine.tun', r'\blora\b', r'\bqlora\b',
    r'\bembedding\b', r'\bsemantic search',
]

# Cold/low-priority domains (penalized)
_COLD_DOMAIN_PATTERNS = [
    r'\bquantum\b',
    r'\bcrypto\b', r'\bblockchain\b',
    r'\binstallation\b', r'\bsetup\b', r'\bprerequisites\b',
    # Backend/programming language topics (non-AI core)
    r'\brust\b', r'\bnode\.?js\b', r'\bdjango\b', r'\bflask\b',
    r'\bspring\b', r'\bjavascript\b', r'\btypescript\b',
    r'\breact\b', r'\bvue\b', r'\bangular\b',
    r'\bsql\b', r'\bpostgresql\b', r'\bmongodb\b', r'\bredis\b',
    r'\bdocker\b', r'\bkubernetes\b', r'\bk8s\b',
    r'\bgit\b', r'\bgithub\b', r'\bci/cd\b',
    r'\bhtml\b', r'\bcss\b', r'\bfrontend\b', r'\bbackend\b',
    r'\bapi\b', r'\brest\b', r'\bgraphql\b',
    r'\bmultithread\b', r'\basync\b', r'\bconcurrency\b',
    r'\b编译\b', r'\b部署\b', r'\b配置\b', r'\b安装\b',
]

# ── Module-level state ──

_pending_unknowns: List[str] = []
_last_scan_ts: float = 0.0
_recently_queued: Dict[str, float] = {}  # md5_hash → timestamp, 20-min dedup
_DEDUP_FILE = None  # lazy init

def _dedup_path() -> str:
    global _DEDUP_FILE
    if _DEDUP_FILE is None:
        try:
            from pathlib import Path
            from ..utils import DATA_DIR
            _DEDUP_FILE = str(DATA_DIR / "unknown_dedup_cache.json")
        except Exception:
            _DEDUP_FILE = ""
    return _DEDUP_FILE

def _load_dedup() -> None:
    global _recently_queued, _queue_count
    fp = _dedup_path()
    if not fp:
        return
    try:
        import json
        from pathlib import Path
        p = Path(fp)
        if p.exists():
            data = json.loads(p.read_text())
            now = time.time()
            cutoff = now - _DEDUP_WINDOW
            if isinstance(data, dict) and "queue" in data:
                _recently_queued = {k: v for k, v in data["queue"].items() if v >= cutoff}
                _queue_count = data.get("counts", {})
            else:
                # legacy format: dict of hash→timestamp
                _recently_queued = {k: v for k, v in data.items() if isinstance(v, (int, float)) and v >= cutoff}
                _queue_count = {}
    except Exception:
        _recently_queued = {}
        _queue_count = {}

def _save_dedup() -> None:
    fp = _dedup_path()
    if not fp or not _recently_queued:
        return
    try:
        import json
        from pathlib import Path
        Path(fp).write_text(json.dumps({
            "queue": _recently_queued,
            "counts": dict(list(_queue_count.items())[-500:]),  # keep last 500
        }))
    except Exception:
        pass

def _md5(term: str) -> str:
    return hashlib.md5(term.lower().encode('utf-8')).hexdigest()


# ══════════════════════════════════════════════
# Candidate extraction
# ══════════════════════════════════════════════


def _extract(text: str) -> List[str]:
    """Pull candidate noun phrases / CJK chunks from text."""
    if not text or len(text) < 8:
        return []

    candidates: List[str] = []
    seen: Set[str] = set()
    clean = text.strip()

    def _add(t: str) -> None:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            candidates.append(t)

    # 2-4 word Title Case phrases  e.g. "Quantum Computing"
    for m in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b', clean):
        phrase = m.group()
        if len(phrase) >= 6 and phrase.lower() not in _EN_STOP:
            _add(phrase)

    # 4-20 char lowercase words (not stopwords)
    for m in re.finditer(r'\b[a-zA-Z]{4,20}\b', clean):
        w = m.group()
        if w.lower() not in _EN_STOP:
            _add(w)

    # 4-12 CJK chunks
    for m in re.finditer(r'[\u4e00-\u9fff]{4,12}', clean):
        chunk = m.group()
        stop_ratio = sum(1 for c in chunk if c in _CN_STOP) / max(len(chunk), 1)
        if stop_ratio < 0.5:
            _add(chunk)

    return candidates


def _score(term: str) -> float:
    """Priority score 0.0-1.0 — higher = more likely worth learning.
    Shallow/basic concepts and error keywords are penalized.
    """
    _SHALLOW_TERMS = {
        "installation", "prerequisites", "understanding", "conversations",
        "introduction", "getting started", "overview", "basics", "fundamentals",
        "quick start", "setup", "configuration", "tutorial", "guide",
        "welcome", "hello world", "example", "demo", "syntax",
    }
    # Reject error-related and template terms outright
    _BLACKLIST = {
        "unboundlocalerror", "valueerror", "typeerror", "keyerror",
        "attributeerror", "importerror", "modulenotfounderror", "runtimeerror",
        "characteristics", "concrete example", "tabular", "overview of",
        "introduction to", "what is", "guide to",
        "pgpassword", "aelvoxim", "localhost", "password",
        "username", "api_key", "apikey", "secret", "token",
    }
    if term.lower() in _BLACKLIST:
        return 0.0
    score = 0.5
    if len(term) >= 8:
        score += 0.15
    if len(term) >= 12:
        score += 0.1
    if re.match(r'^[A-Z]', term):
        score += 0.15
    if re.search(r'[\u4e00-\u9fff]', term) and re.search(r'[a-zA-Z]', term):
        score += 0.1
    if re.search(r'\d', term):
        score += 0.05
    # Shallow term penalty: reduce by 70%
    if term.lower() in _SHALLOW_TERMS:
        score *= 0.3
    # Domain weighting: boost core AI, penalize cold domains
    if any(re.search(p, term, re.I) for p in _CORE_AI_PATTERNS):
        score = min(1.0, score + 0.2)
    elif any(re.search(p, term, re.I) for p in _COLD_DOMAIN_PATTERNS):
        score *= 0.3
    return min(1.0, score)


# ══════════════════════════════════════════════
# Known-state check
# ══════════════════════════════════════════════


def _is_known(term: str) -> bool:
    """Check memory entities + knowledge base for the term."""
    from aelvoxim.memory import search_entities  # lazy to avoid circular import
    if search_entities(term, limit=1):
        return True
    try:
        for entry in KnowledgeBase.get_all_active():
            title = entry.get("title") or entry.get("topic") or ""
            if title and term.lower() in title.lower():
                return True
    except Exception:
        _log.exception("unknown_discovery error")
    return False


# ══════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════


def scan_unknowns(directions: Dict[str, object], log_func: Callable) -> bool:
    """Scan knowledge base + direction topics for unknown concepts.

    Called from _cognition_tick.  Rate-limited to once per _SCAN_INTERVAL.
    Returns True if at least one candidate was queued.
    """
    global _pending_unknowns, _last_scan_ts, _LAST_SHORT_CLEAN, _empty_scans

    now = time.time()
    # Near-capacity bonus: when active directions are almost at the cap, widen
    # the interval aggressively. Pure noise reduction — capacity checks below
    # are untouched, so no free slots are created by this change.
    _active = sum(1 for d in directions.values() if d.status == "active")
    _near_cap = 2 if _active >= 20 else 0
    # Dynamic interval: base 1h, ×2 per consecutive empty scan (up to 4h);
    # near capacity the +2 factor jumps straight to the 4h ceiling.
    _effective_interval = _SCAN_INTERVAL * (2 ** min(_empty_scans + _near_cap, 3))
    if now - _last_scan_ts < _effective_interval:
        return False
    _last_scan_ts = now

    # Capacity gate: skip scanning if too many directions already exist or are pending.
    _total = len(directions)
    if _active >= 22:
        _empty_scans += 1
        if now - _skip_log_at.get("cap", 0.0) > 7200:
            _skip_log_at["cap"] = now
            log_func(f"  ⏸️ [UnknownDiscovery] Skipped: {_active} active — no capacity for new directions")
        return False
    if len(_pending_unknowns) >= _MAX_PENDING:
        _empty_scans += 1
        if now - _skip_log_at.get("full", 0.0) > 7200:
            _skip_log_at["full"] = now
            log_func(f"  ⏸️ [UnknownDiscovery] Skipped: {len(_pending_unknowns)} pending already queued")
        return False

    # 1. Collect source text
    sources: List[str] = []
    try:
        for entry in list(KnowledgeBase.get_all_active())[:50]:
            t = entry.get("title") or entry.get("topic") or ""
            c = entry.get("content") or entry.get("summary") or ""
            sources.append(f"{t} {c}")
    except Exception:
        _log.exception("unknown_discovery error")
    sources.extend(directions.keys())

    # 2. Extract candidates in one pass
    candidates: List[str] = []
    for s in sources:
        candidates.extend(_extract(s))
    if not candidates:
        _empty_scans += 1
        return False

    # Load persisted dedup cache (survives restarts)
    _load_dedup()

    # 3. Score, filter, deduplicate against known + pending + recently queued
    fresh: list = []
    seen: Set[str] = set()
    pending_lower = {t.lower() for t in _pending_unknowns}
    # Clean stale dedup entries (>2h)
    _dedup_cutoff = now - _DEDUP_WINDOW
    stale_dedup = [h for h, ts in _recently_queued.items() if ts < _dedup_cutoff]
    for h in stale_dedup:
        _recently_queued.pop(h, None)
    # Permanent discard: candidates retried _MAX_RETRY+ times are blacklisted
    _perm_discard = {h for h, n in _queue_count.items() if n > _MAX_RETRY}
    for h in _perm_discard:
        _recently_queued.pop(h, None)
        _queue_count.pop(h, None)
    # Clear 20-min short cache periodically
    if now - _LAST_SHORT_CLEAN > _SHORT_DEDUP:
        _SHORT_CACHE.clear()
        _LAST_SHORT_CLEAN = now
    # Clean stale pending candidates (>_MAX_CANDIDATE_AGE)
    _stale_pending = [t for t in _pending_unknowns
                      if t.lower() in _pending_queued_at
                      and now - _pending_queued_at[t.lower()] > _MAX_CANDIDATE_AGE]
    for t in _stale_pending:
        _pending_unknowns.remove(t)
        _pending_queued_at.pop(t.lower(), None)
    if _stale_pending:
        log_func(f"  🗑️ [UnknownDiscovery] dropped {len(_stale_pending)} stale candidate(s) (>24h)")

    for term in candidates:
        low = term.lower()
        h = _md5(term)
        if low in seen or low in pending_lower:
            continue
        # 20-min short-term cache check
        if h in _SHORT_CACHE:
            continue
        # Check dedup with exponential backoff
        if h in _recently_queued:
            _queue_n = _queue_count.get(h, 0)
            # Permanent discard: exceeded max retries
            if _queue_n > _MAX_RETRY:
                continue
            _effective_ttl = _DEDUP_WINDOW * (2 ** min(_queue_n, 5))  # 2h, 4h, 8h, 16h, 32h, 64h
            if now - _recently_queued[h] < _effective_ttl:
                continue
        seen.add(low)
        if _is_known(term):
            continue
        s = _score(term)
        if s >= 0.5:
            fresh.append((term, s))

    if not fresh:
        _empty_scans += 1
        return False

    # 4. Take top 2 by score (capped to prevent queue flooding)
    fresh.sort(key=lambda x: x[1], reverse=True)
    top = [t for t, _ in fresh[:2]]
    _empty_scans = 0  # produced output → reset backoff

    _pending_unknowns.extend(top)
    # Record queue timestamp for aging
    for t in top:
        _pending_queued_at[t.lower()] = now
    # Record in 20-min short cache
    for t in top:
        _SHORT_CACHE.add(_md5(t))
    # Record in long-term dedup cache with exponential backoff (MD5 hash key)
    for t in top:
        h = _md5(t)
        _recently_queued[h] = now
        _queue_count[h] = _queue_count.get(h, 0) + 1
    _save_dedup()
    if len(_pending_unknowns) > _MAX_PENDING:
        _pending_unknowns = _pending_unknowns[-_MAX_PENDING:]

    for term in top:
        log_func(f"  🔍 [UnknownDiscovery] queued: {term}")

    return True


def pop_unknown_candidates(max_count: int = 1) -> List[str]:
    """Pop highest-scored pending candidates for learning.

    Called by curiosity.activate_curiosity during idle cycles.
    Stale candidates (>_MAX_CANDIDATE_AGE) are silently dropped.
    """
    global _pending_unknowns
    if not _pending_unknowns:
        return []
    # Drop stale entries before serving
    now = time.time()
    _pending_unknowns = [t for t in _pending_unknowns
                         if t.lower() not in _pending_queued_at
                         or now - _pending_queued_at[t.lower()] <= _MAX_CANDIDATE_AGE]
    result = _pending_unknowns[:max_count]
    _pending_unknowns = _pending_unknowns[max_count:]
    return result


def pending_unknown_count() -> int:
    return len(_pending_unknowns)


__all__ = [
    "scan_unknowns",
    "pop_unknown_candidates",
    "pending_unknown_count",
]
