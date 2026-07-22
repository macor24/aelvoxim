# SPDX-License-Identifier: MIT
"""
metacore.client.sentrikit — HTTP client for SentriKit API (port 8899).

SentriKit is an independent project. This module communicates with it
exclusively via HTTP. No SentriKit code is imported or modified.

Auto-registers an API key on first use. Gracefully handles SentriKit
being offline (all functions return None or empty dict).

Endpoints:
    GET  /health                  — Health check
    GET  /api/status              — Module status
    GET  /api/selfmodel           — Self model data
    GET  /api/selfmodel/snapshot  — Latest snapshot
    GET  /api/evolution           — Evolution history
    GET  /api/evolve/status       — Evolution status
    GET  /api/evolve/summary      — Evolution dashboard
    GET  /api/verify/results      — Verification results
    GET  /api/reflect/lessons     — Reflection lessons
    GET  /api/auth/register       — Register new API key (no auth needed)
    POST /api/judge               — Evaluate a proposal
    POST /api/evolve/run          — Trigger full evolution loop
    POST /api/metacog/evaluate    — Meta-cognition evaluation
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils import DATA_DIR

_log = logging.getLogger("aelvoxim.sentrikit")


# ── Configuration ──

_SENTRIKIT_HOST = os.environ.get("SENTRIKIT_HOST", "https://127.0.0.1:8899")
_SENTRIKIT_KEY: str = ""
_CACHE_DIR = DATA_DIR
_CACHE_FILE = _CACHE_DIR / "sentrikit_key.json"
_CONFIG_FILE = _CACHE_DIR / "sentrikit_config.json"
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _log_err(msg: str) -> None:
    import logging
    logging.getLogger("aelvoxim.client.sentrikit").warning(msg)

def _load_config() -> dict:
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text())
    except Exception:
        _log.exception("sentrikit error")
    return {}
def _save_config(cfg: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        _log.exception("sentrikit error")
def get_configured_host() -> str:
    cfg = _load_config()
    return cfg.get("host", "") or _SENTRIKIT_HOST
def set_host(host: str) -> None:
    global _SENTRIKIT_HOST
    _SENTRIKIT_HOST = host.rstrip("/")
    cfg = _load_config()
    cfg["host"] = host.rstrip("/")
    _save_config(cfg)


def set_api_key(api_key: str) -> None:
    """Explicitly set the SentriKit API key (bypasses auto-discovery)."""
    global _SENTRIKIT_KEY
    _SENTRIKIT_KEY = api_key
    _save_cached_key(api_key)


def test_connection(host: str = "", api_key: str = "") -> dict:
    """Test connection to a SentriKit server. Returns status dict."""
    target_host = host.rstrip("/") if host else _SENTRIKIT_HOST
    target_key = api_key or _load_cached_key()
    if not target_key:
        target_key = _SENTRIKIT_DEFAULT_KEY
    # Try health endpoint first
    try:
        resp = _get(f"{target_host}/health", timeout=5, api_key=target_key)
        if resp and resp.get("status") == "ok":
            return {"status": "ok", "host": target_host}
    except Exception:
        _log.exception("sentrikit error")
    # Try status endpoint
    try:
        resp = _get(f"{target_host}/api/status", timeout=5, api_key=target_key)
        if resp:
            return {"status": "ok", "host": target_host}
    except Exception:
        _log.exception("sentrikit error")
    # Try without auth
    try:
        resp = _get(f"{target_host}/health", timeout=5)
        if resp:
            return {"status": "auth_required", "host": target_host}
    except Exception:
        _log.exception("sentrikit error")
    return {"status": "unreachable", "host": target_host}


# Default API key for bridge (SentriKit --api-key mode)
_SENTRIKIT_DEFAULT_KEY = "aelvoxim_bridge_key"


def _load_cached_key() -> str:
    """Load cached SentriKit API key from disk."""
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text()).get("api_key", "")
    except Exception:
        _log.exception("sentrikit error")
    return ""


def _save_cached_key(key: str) -> None:
    """Cache SentriKit API key to disk."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"api_key": key}))
    except Exception:
        _log.exception("sentrikit error")


def _ensure_key() -> Optional[str]:
    """Get a valid SentriKit API key, registering if needed."""
    global _SENTRIKIT_KEY
    if _SENTRIKIT_KEY:
        return _SENTRIKIT_KEY
    # Try default bridge key first
    try:
        _test = _get(f"{_SENTRIKIT_HOST}/api/status", api_key=_SENTRIKIT_DEFAULT_KEY)
        if _test and "version" in _test:
            _SENTRIKIT_KEY = _SENTRIKIT_DEFAULT_KEY
            return _SENTRIKIT_KEY
    except Exception:
        _log.exception("sentrikit error")
    # Try cache
    cached = _load_cached_key()
    if cached and len(cached) > 10:
        _SENTRIKIT_KEY = cached
        return _SENTRIKIT_KEY
    # Fallback register — but the response may truncate the key.
    # We read the actual key from the SentriKit users directory.
    try:
        resp = _get(f"{_SENTRIKIT_HOST}/api/auth/register")
        if resp and "api_key" in resp and len(resp["api_key"]) > 10:
            # Some SentriKit versions return full key
            _SENTRIKIT_KEY = resp["api_key"]
            _save_cached_key(_SENTRIKIT_KEY)
            return _SENTRIKIT_KEY
    except Exception:
        _log.exception("sentrikit error")
    # Fallback: scan SentriKit users directory for the latest key
    try:
        _users_dir = Path.home() / ".sentrikit" / "users"
        if _users_dir.exists():
            files = sorted(_users_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                latest = json.loads(files[0].read_text())
                if "api_key" in latest:
                    _SENTRIKIT_KEY = latest["api_key"]
                    _save_cached_key(_SENTRIKIT_KEY)
                    return _SENTRIKIT_KEY
    except Exception:
        _log.exception("sentrikit error")
    return None


def _get(url: str, timeout: int = 10, api_key: str = "") -> Optional[dict]:
    """HTTP GET, returns parsed JSON or None."""
    try:
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _log_err(f"GET {url} failed: {e}")
        return None


def _post(url: str, data: dict, api_key: str = "", timeout: int = 30) -> Optional[dict]:
    """HTTP POST with JSON body, returns parsed JSON or None."""
    try:
        body = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _log_err(f"POST {url} failed: {e}")
        return None


# ── Public API ──


def is_available() -> bool:
    """Check if SentriKit service is reachable."""
    resp = _get(f"{_SENTRIKIT_HOST}/health", timeout=3)
    return resp is not None and resp.get("status") == "ok"


def get_status() -> Optional[dict]:
    """Get SentriKit module status."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/status", api_key=key)


def get_selfmodel() -> Optional[dict]:
    """Get SentriKit self-model data (decisions, snapshots, capabilities)."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/selfmodel", api_key=key)


def get_selfmodel_snapshot() -> Optional[dict]:
    """Get latest SentriKit self-model snapshot."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/selfmodel/snapshot", api_key=key)


def get_evolution() -> Optional[dict]:
    """Get SentriKit evolution history."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/evolution", api_key=key)


def get_evolve_status() -> Optional[dict]:
    """Get SentriKit evolution status."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/evolve/status", api_key=key)


def get_evolve_summary() -> Optional[dict]:
    """Get SentriKit evolution dashboard summary."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/evolve/summary", api_key=key)


def get_verify_results() -> Optional[dict]:
    """Get SentriKit verification results."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/verify/results", api_key=key)


def get_reflect_lessons() -> Optional[dict]:
    """Get SentriKit reflection lessons."""
    key = _ensure_key()
    if not key:
        return None
    return _get(f"{_SENTRIKIT_HOST}/api/reflect/lessons", api_key=key)


def judge(proposal_summary: str) -> Optional[dict]:
    """Submit a proposal to SentriKit Judge for evaluation.

    Args:
        proposal_summary: Text description of the proposal.

    Returns:
        Dict with 'grade', 'total_score', 'dimensions' or None.
    """
    key = _ensure_key()
    if not key:
        return None
    return _post(
        f"{_SENTRIKIT_HOST}/api/judge",
        {"summary": proposal_summary},
        api_key=key,
    )


def evaluate_metacog(metrics: dict) -> Optional[dict]:
    """Submit metrics to SentriKit meta-cognition evaluator."""
    key = _ensure_key()
    if not key:
        return None
    return _post(
        f"{_SENTRIKIT_HOST}/api/metacog/evaluate",
        metrics,
        api_key=key,
    )


def run_evolve() -> Optional[dict]:
    """Trigger a full SentriKit evolution loop run."""
    key = _ensure_key()
    if not key:
        return None
    return _post(
        f"{_SENTRIKIT_HOST}/api/evolve/run",
        {},
        api_key=key,
        timeout=120,
    )


def full_report() -> dict:
    """Get a consolidated report from all SentriKit endpoints.

    Returns a dict with all available data. Individual failures
    are marked with None or empty dicts.
    """
    return {
        "available": is_available(),
        "status": get_status(),
        "selfmodel": get_selfmodel(),
        "evolution": get_evolution(),
        "evolve_status": get_evolve_status(),
        "verify_results": get_verify_results(),
        "reflect_lessons": get_reflect_lessons(),
    }
