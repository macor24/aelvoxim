"""aelvoxim.utils.backup — Scheduled data backup."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import DATA_DIR

log = logging.getLogger("aelvoxim.backup")

BACKUP_DIR = DATA_DIR / "backups"
_MAX_BACKUPS = 7  # keep 7 days of backups
_BACKUP_INTERVAL = 86400  # once per day


def _backup_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"aelvoxim_backup_{ts}"


def _cleanup_old():
    """Remove backups older than the max count."""
    backups = sorted(BACKUP_DIR.glob("aelvoxim_backup_*"))
    while len(backups) > _MAX_BACKUPS:
        try:
            shutil.rmtree(backups[0])
            log.info("Removed old backup: %s", backups[0].name)
        except Exception as e:
            log.warning("Failed to remove old backup %s: %s", backups[0].name, e)
        backups = backups[1:]


def _do_backup() -> Optional[Path]:
    """Copy DATA_DIR to timestamped backup dir, excluding backups/ itself."""
    try:
        dest = _backup_path()
        dest.mkdir(parents=True, exist_ok=True)
        for item in DATA_DIR.iterdir():
            if item.name == "backups":
                continue
            if item.is_file():
                shutil.copy2(item, dest / item.name)
            elif item.is_dir():
                shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
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
