from __future__ import annotations

import sqlite3
import threading
import time
import traceback
from typing import Any, Callable

from .db import connect, init_db
from .util import now_iso

_lock = threading.Lock()
_thread: threading.Thread | None = None
_pause_event = threading.Event()
_stale_checked_at = 0.0
_STALE_CHECK_EVERY = 5.0


class JobPaused(Exception):
    """Raised inside a worker when the user asks to pause."""


def _db():
    conn = connect()
    init_db(conn)
    return conn


def _clear_stale_running_jobs() -> None:
    """A crashed or deadlocked worker leaves status=running forever."""
    global _stale_checked_at
    now = time.monotonic()
    if now - _stale_checked_at < _STALE_CHECK_EVERY:
        return
    _stale_checked_at = now
    alive = _thread is not None and _thread.is_alive()
    if alive:
        return
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        if not rows:
            return
        now = now_iso()
        for row in rows:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'error',
                    message = ?,
                    error = ?,
                    finished_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (
                    "Stopped. Already indexed photos are kept. Resume to continue.",
                    "stale running job (worker not alive)",
                    now,
                    row["id"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def active_job() -> dict[str, Any] | None:
    _clear_stale_running_jobs()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def latest_jobs(limit: int = 8) -> list[dict[str, Any]]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_job(job_id: int) -> dict[str, Any] | None:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _db()
    try:
        for attempt in range(8):
            try:
                conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
                conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        conn.close()


def create_job(job_type: str) -> int:
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO jobs (type, status, progress, total, message, created_at) VALUES (?, 'queued', 0, 0, '', ?)",
            (job_type, now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def pause_requested() -> bool:
    return _pause_event.is_set()


def clear_pause_request() -> None:
    _pause_event.clear()


def request_pause() -> dict[str, Any]:
    job = active_job()
    if not job:
        return {"ok": False, "error": "No running job"}
    _pause_event.set()
    return {"ok": True, "id": job["id"]}


def start_job(job_type: str, fn: Callable[[int], None]) -> dict[str, Any]:
    global _thread
    with _lock:
        current = active_job()
        if current:
            return current
        clear_pause_request()
        job_id = create_job(job_type)

        def runner() -> None:
            update_job(job_id, status="running", message="Starting")
            try:
                fn(job_id)
                job = get_job(job_id)
                if job and job["status"] == "running":
                    update_job(job_id, status="done", finished_at=now_iso(), message=job.get("message") or "Done")
                    try:
                        from . import catalog as catalog_mod

                        catalog_mod.maybe_backup(min_age_seconds=0)
                    except Exception:
                        pass
            except JobPaused:
                job = get_job(job_id) or {}
                msg = str(job.get("message") or "").strip()
                if not msg.lower().startswith("paused"):
                    msg = f"Paused. {msg}".strip() if msg else "Paused. Already indexed photos are kept."
                update_job(
                    job_id,
                    status="paused",
                    error=None,
                    message=msg,
                    finished_at=now_iso(),
                )
            except Exception as exc:  # noqa: BLE001 — surface job errors to the UI
                update_job(
                    job_id,
                    status="error",
                    error=f"{exc}\n{traceback.format_exc()}",
                    message=str(exc),
                    finished_at=now_iso(),
                )
            finally:
                clear_pause_request()

        _thread = threading.Thread(target=runner, name=f"photosort-{job_type}", daemon=True)
        _thread.start()
        return get_job(job_id) or {"id": job_id, "type": job_type, "status": "queued"}
