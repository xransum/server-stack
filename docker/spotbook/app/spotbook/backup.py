"""
backup.py — Daily SQLite online backup with 14-day rotation and Discord alerts.

Uses the sqlite3.backup() API (not file copy) so the backup is safe to run
while the database is live. The backup file is a valid SQLite database.

Runs as a background thread (not async) since sqlite3 is synchronous.
Started by server.py lifespan.

SPOTBOOK_BACKUP_DIR defaults to /data/backups (local to media-vm).
Proper target is a TrueNAS NFS export — see docs/spotbook.md open items.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_KEEP_DAYS = 14
BACKUP_INTERVAL_SECONDS = 86400  # 24 hours
BACKUP_HOUR_UTC = 3              # run at 03:00 UTC


def _discord_notify(webhook_url: str, message: str) -> None:
    """Post a message to Discord. Silently ignores errors."""
    if not webhook_url:
        return
    try:
        data = json.dumps({"content": message}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning("Discord notify failed: %s", e)


def run_backup(db_path: str, backup_dir: str, discord_webhook: str = "") -> bool:
    """
    Run one backup of db_path into backup_dir.
    Returns True on success, False on failure.
    """
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_path / f"spotbook-{ts}.db"

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.close()
        finally:
            src.close()

        log.info("Backup completed: %s", dest)

        # Prune old backups
        _prune_backups(backup_path)
        return True

    except Exception as e:
        log.error("Backup failed: %s", e)
        _discord_notify(
            discord_webhook,
            f":warning: **spotbook backup failed**: {e}",
        )
        return False


def _prune_backups(backup_dir: Path) -> None:
    """Remove backup files older than BACKUP_KEEP_DAYS."""
    files = sorted(backup_dir.glob("spotbook-*.db"), key=lambda f: f.stat().st_mtime)
    cutoff = len(files) - BACKUP_KEEP_DAYS
    if cutoff > 0:
        for f in files[:cutoff]:
            try:
                f.unlink()
                log.info("Pruned old backup: %s", f)
            except Exception as e:
                log.warning("Could not prune %s: %s", f, e)


def restore_from_backup(backup_file: str, db_path: str) -> None:
    """
    Restore the database from a backup file.
    Call this manually from the command line; not invoked automatically.

    Usage:
        python -c "from spotbook.backup import restore_from_backup; \
            restore_from_backup('/data/backups/spotbook-20261123T030000Z.db', \
                                '/data/spotbook.db')"
    """
    src = sqlite3.connect(backup_file)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
        log.info("Restored %s → %s", backup_file, db_path)
    finally:
        src.close()
        dst.close()


def _backup_loop(db_path: str, backup_dir: str, discord_webhook: str) -> None:
    """Background thread: run backup once per day at BACKUP_HOUR_UTC."""
    log.info("Backup scheduler started (daily at %02d:00 UTC)", BACKUP_HOUR_UTC)
    while True:
        now = datetime.now(timezone.utc)
        # Sleep until next BACKUP_HOUR_UTC
        next_run = now.replace(
            hour=BACKUP_HOUR_UTC, minute=0, second=0, microsecond=0
        )
        if next_run <= now:
            from datetime import timedelta
            next_run = next_run + timedelta(days=1)

        sleep_seconds = (next_run - now).total_seconds()
        log.debug("Next backup in %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)

        run_backup(db_path, backup_dir, discord_webhook)


def start_backup_scheduler(
    db_path: str,
    backup_dir: str,
    discord_webhook: str = "",
) -> threading.Thread:
    """Start the backup background thread. Returns the thread object."""
    t = threading.Thread(
        target=_backup_loop,
        args=(db_path, backup_dir, discord_webhook),
        daemon=True,
        name="spotbook-backup",
    )
    t.start()
    return t
