"""
metacore.core.health — Service watchdog + health monitor.

Background thread that checks all Aelvoxim services periodically,
logs status, auto-recovers dead services, and exposes status data.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..utils import METACORE_DIR

# ── Config ──

# Auto-detect project root — supports both WSL and native Linux
_HERE = Path(__file__).resolve().parent.parent.parent.parent  # src/aelvoxim/core/../../../ → project root
_BASE = Path(os.environ.get("AELVOXIM_ROOT", str(_HERE)))
_PYTHON = "python3 -B"

SERVICES: Dict[str, Dict[str, Any]] = {
    "api": {
        "port": 9701,
        "url": "http://127.0.0.1:9701/v1/health",
        "cmd": f"cd {_BASE} && PYTHONPATH=src {_PYTHON} src/run_server.py 9701",
        "label": "API 9701",
        "auto_heal": True,
        "max_retries": 3,
    },
    "chatael": {
        "port": 9702,
        "url": "http://127.0.0.1:9702/",
        "cmd": f"cd {_BASE}/frontend/chatael-v2 && {_PYTHON} {_BASE}/serve_chatael.py",
        "label": "ChatAEL 9702",
        "auto_heal": True,
        "max_retries": 3,
    },
}

HEAL_LOG_PATH = METACORE_DIR / "health" / "heal_log.jsonl"
HEAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_watchdog_instance: Optional["Watchdog"] = None


class Watchdog:
    """Background service watchdog with auto-heal."""

    def __init__(self, check_interval: int = 30):
        self._interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._status: Dict[str, dict] = {}
        self._heal_counts: Dict[str, int] = {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return self._running

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                pass
            self._stop.wait(self._interval)

    def _tick(self):
        now = time.time()
        for name, cfg in SERVICES.items():
            up, latency, err = self._check(cfg["url"])
            self._status[name] = {
                "up": up,
                "latency_ms": latency,
                "label": cfg["label"],
                "port": cfg["port"],
                "error": err,
                "checked_at": datetime.now().isoformat(),
            }
            if not up and cfg.get("auto_heal", False):
                self._heal(name, cfg)

    def _check(self, url: str) -> tuple[bool, int, str]:
        """Returns (up, latency_ms, error). Single attempt, 5s timeout."""
        t0 = time.time()
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=5):
                return True, round((time.time() - t0) * 1000), ""
        except Exception as e:
            return False, 0, str(e)[:80]

    def _heal(self, name: str, cfg: dict):
        count = self._heal_counts.get(name, 0)
        if count >= cfg.get("max_retries", 3):
            return
        self._heal_counts[name] = count + 1
        try:
            subprocess.Popen(
                cfg["cmd"], shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            result = "initiated"
        except Exception as e:
            result = f"failed: {e}"
        self._log_heal({"time": datetime.now().isoformat(), "service": name, "action": "restart", "result": result})

    def _log_heal(self, record: dict):
        with open(HEAL_LOG_PATH, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_status(self) -> dict:
        return dict(self._status)

    def get_heal_log(self, limit: int = 20) -> list[dict]:
        if not HEAL_LOG_PATH.exists():
            return []
        lines = HEAL_LOG_PATH.read_text().strip().split("\n")
        result = []
        for line in lines[-limit:]:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result

    def get_heal_counts(self) -> dict:
        return dict(self._heal_counts)


def get_watchdog() -> Watchdog:
    global _watchdog_instance
    if _watchdog_instance is None:
        _watchdog_instance = Watchdog()
    return _watchdog_instance


def start_watchdog():
    wd = get_watchdog()
    wd.start()
    return wd


def get_resource_usage() -> dict:
    """Get CPU, memory, disk usage via /proc (no psutil needed)."""
    try:
        cpu = _cpu_percent()
        mem = _memory_info()
        disk = _disk_usage("/")
        return {
            "cpu": {"percent": round(cpu, 1), "cores": os.cpu_count() or 1},
            "memory": mem,
            "disk": disk,
        }
    except Exception:
        return {}


def _cpu_percent() -> float:
    """Rough CPU percentage from /proc/stat over a short interval."""
    def _read():
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(v) for v in parts[1:]]
        return sum(vals), sum(vals[:8])  # total, active
    total_1, active_1 = _read()
    time.sleep(0.3)
    total_2, active_2 = _read()
    delta_total = total_2 - total_1
    delta_active = active_2 - active_1
    return (delta_active / max(delta_total, 1)) * 100


def _memory_info() -> dict:
    with open("/proc/meminfo") as f:
        raw = f.read()
    def _kb(key):
        for line in raw.split("\n"):
            if line.startswith(key + ":"):
                return int(line.split()[1])
        return 0
    total_kb = _kb("MemTotal")
    available_kb = _kb("MemAvailable")
    used_kb = total_kb - available_kb
    return {
        "total_gb": round(total_kb / (1024**2), 1),
        "used_gb": round(used_kb / (1024**2), 1),
        "percent": round(used_kb / max(total_kb, 1) * 100, 1),
    }


def _disk_usage(path: str) -> dict:
    st = os.statvfs(path)
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bfree
    used = total - free
    return {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "percent": round(used / max(total, 1) * 100, 1),
    }


def get_pg_status() -> dict:
    """Check PostgreSQL connectivity."""
    try:
        from ..storage.db import use_pg, fetch_one
        if use_pg():
            fetch_one("SELECT 1")
            return {"up": True, "version": "16+pgvector"}
        return {"up": False, "error": "PG not configured"}
    except Exception as e:
        return {"up": False, "error": str(e)[:60]}
