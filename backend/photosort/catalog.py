from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BACKUP_DIR, DB_PATH, ensure_dirs
from .db import connect, init_db
from .originals import assert_data_write
from .util import now_iso

KEEP = 14
AUTO_EVERY_SECONDS = 6 * 3600
STARTUP_MIN_AGE_SECONDS = 24 * 3600

_lock = threading.Lock()
_loop_started = False


def _stamp() -> str:
    return now_iso().replace(":", "").replace("+", "Z")


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    files = [
        path
        for path in (*BACKUP_DIR.glob("photosort-*.db.gz"), *BACKUP_DIR.glob("photosort-*.db"))
        if path.is_file() and not path.name.endswith(".tmp")
    ]
    return sorted(files, key=lambda path: path.name)


def latest_backup() -> Path | None:
    items = list_backups()
    return items[-1] if items else None


def backup_info(path: Path | None = None) -> dict[str, Any] | None:
    target = path or latest_backup()
    if not target or not target.exists():
        return None
    compressed = target.name.endswith(".db.gz")
    mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return {
        "path": str(target),
        "name": target.name,
        "bytes": target.stat().st_size,
        "mtime": mtime.isoformat(),
        "compressed": compressed,
    }


def backup_status() -> dict[str, Any]:
    items = list_backups()
    return {
        "dir": str(BACKUP_DIR),
        "latest": backup_info(items[-1]) if items else None,
        "count": len(items),
        "keep": KEEP,
        "automatic": True,
        "compressed": True,
    }


def prune_backups(keep: int = KEEP) -> list[str]:
    """Drop extras. Once a gzip copy exists, remove uncompressed .db snapshots."""
    ensure_dirs()
    removed: list[str] = []
    gzipped = sorted(BACKUP_DIR.glob("photosort-*.db.gz"))
    if gzipped:
        for path in BACKUP_DIR.glob("photosort-*.db"):
            if path.name.endswith(".tmp") or path.name.endswith(".db.gz"):
                continue
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                pass
    items = list_backups()
    extra = items[:-keep] if keep > 0 else items
    for path in extra:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            pass
    return removed


def backup_catalog() -> dict[str, Any]:
    """Consistent gzip snapshot of the tagging database. Originals are never copied."""
    ensure_dirs()
    with _lock:
        if not DB_PATH.exists():
            return {"path": None, "bytes": 0, "compressed": True, "skipped": True}
        stamp = _stamp()
        raw = assert_data_write(BACKUP_DIR / f"photosort-{stamp}.db.tmp")
        dest = assert_data_write(BACKUP_DIR / f"photosort-{stamp}.db.gz")
        src = sqlite3.connect(str(DB_PATH), timeout=30.0)
        try:
            src.execute("PRAGMA busy_timeout=30000")
            dst = sqlite3.connect(str(raw))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        try:
            with raw.open("rb") as inf, gzip.open(dest, "wb", compresslevel=6) as out:
                shutil.copyfileobj(inf, out, 1024 * 1024)
        finally:
            raw.unlink(missing_ok=True)
        pruned = prune_backups()
        return {
            "path": str(dest),
            "name": dest.name,
            "bytes": dest.stat().st_size if dest.exists() else 0,
            "compressed": True,
            "pruned": len(pruned),
        }


def maybe_backup(*, min_age_seconds: float = AUTO_EVERY_SECONDS, force: bool = False) -> dict[str, Any]:
    """Take a compressed backup unless a recent gzip copy already exists."""
    if not force:
        latest = latest_backup()
        if latest and latest.name.endswith(".db.gz"):
            age = time.time() - latest.stat().st_mtime
            if age < min_age_seconds:
                info = backup_info(latest) or {}
                return {**info, "skipped": True, "age_seconds": int(age)}
    return backup_catalog()


def start_backup_loop() -> None:
    """On launch, back up if none today; then every few hours while the app runs."""
    global _loop_started
    if _loop_started or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _loop_started = True

    def run() -> None:
        try:
            maybe_backup(min_age_seconds=STARTUP_MIN_AGE_SECONDS)
        except Exception:
            pass
        while True:
            time.sleep(AUTO_EVERY_SECONDS)
            try:
                maybe_backup()
            except Exception:
                pass

    threading.Thread(target=run, name="photosort-backup", daemon=True).start()


def integrity_counts() -> dict[str, int]:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            "SELECT integrity, COUNT(*) AS n FROM photos GROUP BY integrity"
        ).fetchall()
        counts = {r["integrity"]: r["n"] for r in rows}
        return {
            "ok": counts.get("ok", 0),
            "changed": counts.get("changed", 0),
            "missing": counts.get("missing", 0),
            "unreadable": counts.get("unreadable", 0),
            "unchecked": counts.get("unchecked", 0),
        }
    finally:
        conn.close()
