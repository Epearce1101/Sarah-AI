"""Daily snapshots of the conversation database.

`sarah.db` holds every conversation, memory and setting in one file. A
consistent copy is taken with SQLite's online backup API (safe while the app
is writing, unlike copying the file), verified with `PRAGMA quick_check`,
and only the newest `SARAH_BACKUP_KEEP` (default 7) are kept.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "sarah_"
BACKUP_INTERVAL_SECONDS = 24 * 3600


def backup_dir() -> Path:
    return settings.backup_dir


def list_backups(directory: Optional[Path] = None) -> List[Path]:
    directory = directory or backup_dir()
    if not directory.exists():
        return []
    return sorted(directory.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def backup_database(db_path: Optional[Path] = None, directory: Optional[Path] = None,
                    keep: Optional[int] = None) -> Path:
    """Write a verified snapshot and prune old ones. Returns the new file."""
    from backend import db as db_module

    db_path = Path(db_path or db_module.DB_PATH)
    directory = directory or backup_dir()
    keep = settings.backup_keep if keep is None else keep
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{BACKUP_PREFIX}{datetime.now():%Y%m%d_%H%M%S}.db"
    partial = target.with_suffix(".db.partial")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(partial)
    try:
        src.backup(dst)
        status = dst.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if status != "ok":
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {status}")
    partial.replace(target)

    for old in list_backups(directory)[max(1, keep):]:
        try:
            old.unlink()
        except OSError as exc:
            logger.warning("Could not remove old backup %s: %s", old, exc)
    logger.info("Database backup written: %s", target.name)
    return target


def ensure_recent_backup(max_age_seconds: float = BACKUP_INTERVAL_SECONDS) -> Optional[Path]:
    """Back up if the newest snapshot is older than `max_age_seconds`."""
    if not settings.backup_enabled:
        return None
    newest = next(iter(list_backups()), None)
    if newest and time.time() - newest.stat().st_mtime < max_age_seconds:
        return None
    try:
        return backup_database()
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        return None
