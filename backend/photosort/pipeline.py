"""Find Known Faces: import, detect, match — resume instead of starting over."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import faces as faces_mod
from . import importer
from . import state as state_mod
from .db import connect, init_db
from .jobs import active_job, latest_jobs, start_job, update_job

FOLDERS_KEY = "pipeline_folders"
AUTO_UPDATE_EVERY_SECONDS = 300
AUTO_UPDATE_START_DELAY = 20
_auto_loop_started = False


def remember_folders(folders: list[Path | str]) -> None:
    paths = [str(Path(item).expanduser()) for item in folders if str(item).strip()]
    state_mod.set_state(FOLDERS_KEY, json.dumps(paths) if paths else None)


def _library_folder() -> Path | None:
    from . import importer as importer_mod

    library = importer_mod.get_library()
    raw = (library or {}).get("folder")
    if not raw:
        return None
    folder = Path(str(raw)).expanduser()
    return folder if folder.is_dir() else None


def remembered_folder_paths() -> list[Path]:
    """Stored album paths, including ones whose drive is currently unmounted."""
    raw = state_mod.get_state(FOLDERS_KEY)
    if not raw:
        library = _library_folder()
        return [library] if library else []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        items = []
    out: list[Path] = []
    for item in items if isinstance(items, list) else []:
        text = str(item).strip()
        if text:
            out.append(Path(text).expanduser())
    if out:
        return out
    library = _library_folder()
    return [library] if library else []


def remembered_folders() -> list[Path]:
    existing = [path for path in remembered_folder_paths() if path.is_dir()]
    if existing:
        return existing
    library = _library_folder()
    return [library] if library else []


def pending_scan_count() -> int:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM photos WHERE scanned_at IS NULL AND IFNULL(hidden, 0) = 0"
        ).fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()


def run_pipeline(
    job_id: int,
    folders: list[Path],
    *,
    reindex: bool = True,
    scan: bool = True,
    faces_if_new_only: bool = False,
) -> None:
    remember_folders(folders)
    added = 0
    for folder in folders:
        result = importer.import_folder(
            job_id,
            folder,
            verify_existing=False,
            skip_if_complete=not reindex,
        ) or {}
        added += int(result.get("added") or 0)
    if not scan:
        update_job(
            job_id,
            message="New photos added. Face scanning is off in Settings.",
        )
        return
    pending = pending_scan_count()
    if faces_if_new_only and pending == 0:
        update_job(
            job_id,
            progress=added,
            total=max(added, 1),
            message="Listed folders are up to date. No new photos to scan.",
        )
        return
    update_job(job_id, message="Looking for faces in photos not scanned yet…")
    faces_mod.scan_pending(job_id)


def maybe_auto_update() -> dict | None:
    """Walk listed albums for new files. Optional face scan is a Settings toggle."""
    from . import settings as settings_mod

    if not settings_mod.auto_update_enabled():
        return None
    if active_job():
        return None
    folders = remembered_folders()
    if not folders:
        return None
    scan = settings_mod.auto_scan_new_enabled()
    captured = list(folders)
    job_type = "pipeline" if scan else "import"
    return start_job(
        job_type,
        lambda job_id: run_pipeline(
            job_id, captured, reindex=True, scan=scan, faces_if_new_only=True
        ),
    )


def start_auto_update_loop() -> None:
    global _auto_loop_started
    if _auto_loop_started or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _auto_loop_started = True

    def run() -> None:
        time.sleep(AUTO_UPDATE_START_DELAY)
        while True:
            try:
                maybe_auto_update()
            except Exception:
                pass
            time.sleep(AUTO_UPDATE_EVERY_SECONDS)

    threading.Thread(target=run, name="photosort-auto-update", daemon=True).start()


def should_resume() -> bool:
    if active_job():
        return False
    recent = latest_jobs(1)
    if recent and recent[0].get("status") == "paused":
        return False
    if pending_scan_count() > 0:
        return True
    if not recent:
        return False
    last = recent[0]
    if last.get("type") not in {"pipeline", "import", "scan", "match", "cluster"}:
        return False
    if last.get("status") != "error":
        return False
    err = str(last.get("error") or "")
    # Restart leftover work from a killed process. Do not retry code crashes
    # (they would pin the CPU in a start→fail→restart loop).
    return "stale running job" in err or "worker not alive" in err


def resume_pipeline(*, reindex: bool = False) -> dict | None:
    folders = remembered_folders()
    if not folders:
        return None
    if active_job():
        return active_job()
    captured = list(folders)
    remember_folders(captured)
    return start_job("pipeline", lambda job_id: run_pipeline(job_id, captured, reindex=reindex))


def resume_latest() -> dict | None:
    """Restart the last paused or interrupted job, or continue Find Known Faces."""
    current = active_job()
    if current:
        return current
    recent = latest_jobs(1)
    last = recent[0] if recent else None
    job_type = (last or {}).get("type")
    status = (last or {}).get("status")
    if last and status in {"paused", "error"}:
        if job_type == "identify":
            from . import lookup as lookup_mod

            return start_job("identify", lookup_mod.run_identify)
        if job_type == "match":
            from . import match as match_mod

            return start_job("match", match_mod.match_unknown)
        if job_type == "cluster":
            from . import cluster as cluster_mod

            return start_job("cluster", cluster_mod.run_clustering)
        if job_type == "verify":
            return start_job("verify", importer.verify_originals)
        if job_type in {"pipeline", "import", "scan"}:
            return resume_pipeline(reindex=False)
    return resume_pipeline(reindex=False)


def resume_interrupted_pipeline() -> dict | None:
    folders = remembered_folders()
    if not folders or not should_resume():
        return None
    return resume_pipeline(reindex=False)
