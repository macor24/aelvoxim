"""aelvoxim.utils.backup — Scheduled data backup (tar.gz, logs excluded)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import DATA_DIR

log = logging.getLogger("aelvoxim.backup")

BACKUP_DIR = DATA_DIR / "backups"
_MAX_BACKUPS = 3  # keep 3 most recent backups
_BACKUP_INTERVAL = 86400  # once per day
_EXCLUDE_DIRS = {"backups", "logs"}  # logs live in /var/aelvoxim/logs — no need to duplicate 54MB each time


def _backup_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"aelvoxim_backup_{ts}.tar.gz"


def _cleanup_old():
    """Remove backups beyond the max count (legacy dirs + new tarballs both match)."""
    backups = sorted(BACKUP_DIR.glob("aelvoxim_backup_*"))
    while len(backups) > _MAX_BACKUPS:
        try:
            victim = backups[0]
            if victim.is_dir():
                shutil.rmtree(victim)
            else:
                victim.unlink()
            log.info("Removed old backup: %s", victim.name)
        except Exception as e:
            log.warning("Failed to remove old backup %s: %s", backups[0], e)
        backups = backups[1:]


def _do_backup() -> Optional[Path]:
    """Pack DATA_DIR into a timestamped tar.gz, excluding backups/ and logs/."""
    try:
        dest = _backup_path()
        with tarfile.open(dest, "w:gz") as tar:
            for item in DATA_DIR.iterdir():
                if item.name in _EXCLUDE_DIRS:
                    continue
                try:
                    tar.add(item, arcname=item.name)
                except Exception as e:
                    log.warning("Skipped %s in backup: %s", item.name, e)
        log.info("Backup created: %s", dest)
        _cleanup_old()
        return dest
    except Exception as e:
        log.error("Backup failed: %s", e)
        return None


def backup_now() -> Optional[Path]:
    """Run a backup immediately. Returns backup path if successful."""
    return _do_backup()


class BackupScheduler:
    """Background thread that backs up daily."""

    def __init__(self, interval: int = _BACKUP_INTERVAL):
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="aelvoxim-backup")
        self._thread.start()
        log.info("Backup scheduler started (every %ds)", self._interval)

    def stop(self):
        self._running = False

    def _loop(self):
        time.sleep(300)  # delay first backup 5 min after startup
        while self._running:
            try:
                _do_backup()
            except Exception as e:
                log.warning("Backup error: %s", e)
            time.sleep(self._interval)


_scheduler: Optional[BackupScheduler] = None


def start_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackupScheduler()
        _scheduler.start()
