from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config
from . import state as state_mod
from .config import IMAGE_EXTS, THUMB_MAX, VIEW_MAX
from .db import connect, init_db
from .jobs import JobPaused, pause_requested, update_job
from .originals import (
    OriginalWriteError,
    library_must_stay_outside_data,
    open_image,
    open_preview,
    save_image,
    skip_dir,
    verify_file,
)
from .util import now_iso

def _norm_folder(path: Path | str) -> str:
    text = str(Path(path).expanduser()).rstrip("/")
    return text or "/"


def _is_excluded(current: Path, excluded: list[Path] | None) -> bool:
    if not excluded:
        return False
    key = _norm_folder(current)
    for item in excluded:
        parent = _norm_folder(item)
        if key == parent or key.startswith(f"{parent}/"):
            return True
    return False


def _walk_images(folder: Path, exclude_folders: list[Path] | None = None):
    for dirpath, dirnames, filenames in os.walk(folder):
        current = Path(dirpath)
        if current != folder and _is_excluded(current, exclude_folders):
            dirnames[:] = []
            continue
        if skip_dir(current) and current != folder:
            dirnames[:] = []
            continue
        dirnames[:] = [
            name
            for name in dirnames
            if not skip_dir(current / name) and not _is_excluded(current / name, exclude_folders)
        ]
        dirnames.sort()
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("._") or name == ".photosort.json":
                continue
            path = current / name
            if "fixtures/sample-album" in str(path):
                continue
            if path.suffix.lower() in IMAGE_EXTS:
                yield path


def _iter_images(folder: Path, exclude_folders: list[Path] | None = None) -> list[Path]:
    return list(_walk_images(folder, exclude_folders))


def _exif_taken_at(img: Image.Image) -> str | None:
    exif = img.getexif()
    if not exif:
        return None
    # 36867 DateTimeOriginal, 306 DateTime
    for key in (36867, 306):
        raw = exif.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
            try:
                return datetime.strptime(text[:19], fmt).isoformat()
            except ValueError:
                continue
    return None


def make_thumb(src: Path, photo_id: int, library: Path | None = None, img: Image.Image | None = None) -> Path | None:
    dest = config.THUMB_DIR / f"{photo_id}.jpg"
    try:
        if img is None:
            img, _orig = open_preview(src, max_side=THUMB_MAX)
            img = ImageOps.exif_transpose(img)
        else:
            img = img.copy()
        img = img.convert("RGB")
        img.thumbnail((THUMB_MAX, THUMB_MAX))
        return save_image(img, dest, format="JPEG", quality=82, optimize=True)
    except (UnidentifiedImageError, OSError, OriginalWriteError):
        return None


def make_view(src: Path, photo_id: int, img: Image.Image | None = None) -> Path | None:
    """Local display JPEG so the photo page does not wait on the original NAS file."""
    dest = config.VIEW_DIR / f"{photo_id}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        config.VIEW_DIR.mkdir(parents=True, exist_ok=True)
        if img is None:
            img, _orig = open_preview(src, max_side=VIEW_MAX)
            img = ImageOps.exif_transpose(img)
        else:
            img = img.copy()
        img = img.convert("RGB")
        img.thumbnail((VIEW_MAX, VIEW_MAX))
        return save_image(img, dest, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError, OriginalWriteError):
        return None


def _read_image_meta(src: Path) -> tuple[Image.Image, tuple[int, int], str | None] | None:
    try:
        img, orig = open_preview(src, max_side=VIEW_MAX)
        img = ImageOps.exif_transpose(img)
        taken = _exif_taken_at(img)
        return img, orig if orig[0] and orig[1] else img.size, taken
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    except Exception:
        return None


def _image_size(src: Path) -> tuple[int, int] | None:
    meta = _read_image_meta(src)
    return meta[1] if meta else None


def _taken_at(src: Path) -> str | None:
    meta = _read_image_meta(src)
    return meta[2] if meta else None


def set_library(folder: Path, decade_override: int | None = None) -> dict:
    conn = connect()
    init_db(conn)
    try:
        conn.execute(
            """
            INSERT INTO library (id, folder, decade_override, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                folder = excluded.folder,
                decade_override = excluded.decade_override,
                updated_at = excluded.updated_at
            """,
            (str(folder.resolve()), decade_override, now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM library WHERE id = 1").fetchone()
        return dict(row) if row else {"folder": str(folder)}
    finally:
        conn.close()


def get_library() -> dict | None:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM library WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


FILE_TOTAL_KEY = "pipeline_file_total"
FORMATS_KEY = "image_exts"


def _photo_key(path: Path) -> str:
    return os.path.normpath(str(path))


def _insert_photo(
    conn: sqlite3.Connection,
    key: str,
    digest: str,
    taken: str | None,
    size: tuple[int, int],
    size_bytes: int,
) -> int | None:
    """Insert one catalog row. None if that path is already stored."""
    for attempt in range(8):
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO photos
                    (path, sha256, taken_at, width, height, created_at, integrity, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    digest,
                    taken,
                    size[0],
                    size[1],
                    now_iso(),
                    "unchecked" if str(digest).startswith("pending:") else "ok",
                    size_bytes,
                ),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return None
        except sqlite3.OperationalError as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
    return None


def _remembered_file_total() -> int:
    raw = state_mod.get_state(FILE_TOTAL_KEY)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _scan_total(seen: int, already: int) -> int:
    return max(seen, already, _remembered_file_total(), 1)


def _report_scan(job_id: int, seen: int, already: int, detail: str = "") -> None:
    total = _scan_total(seen, already)
    suffix = f" · {detail}" if detail else ""
    update_job(
        job_id,
        progress=seen,
        total=total,
        message=f"Checking {seen} of {total} files{suffix}",
    )


def import_folder(
    job_id: int,
    folder: Path,
    decade_override: int | None = None,
    *,
    verify_existing: bool = False,
    skip_if_complete: bool = False,
    exclude_folders: list[Path] | None = None,
) -> dict:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    library_must_stay_outside_data(folder)

    set_library(folder, decade_override)
    conn = connect()
    init_db(conn)
    added = 0
    skipped = 0
    changed = 0
    seen = 0
    try:
        known = {row["path"] for row in conn.execute("SELECT path FROM photos")}
        known_cf = {item.casefold(): item for item in known}
        already = len(known)
        formats = ",".join(sorted(IMAGE_EXTS))
        if state_mod.get_state(FORMATS_KEY) != formats:
            state_mod.set_state(FORMATS_KEY, formats)
            state_mod.set_state(FILE_TOTAL_KEY, "0")
        remembered = _remembered_file_total()
        if skip_if_complete and remembered and already >= remembered:
            _report_scan(job_id, already, already, "already indexed — looking for faces next")
            return {"added": 0, "skipped": already, "changed": 0, "total_files": already}
        last_report = 0.0

        def report(detail: str = "") -> None:
            nonlocal last_report
            now = time.monotonic()
            if now - last_report < 0.4 and seen not in (0, already):
                return
            last_report = now
            _report_scan(job_id, seen, already, detail)

        _report_scan(
            job_id,
            already,
            already,
            f"{already} already in the catalog" if already else "looking for photos",
        )
        for path in _walk_images(folder, exclude_folders):
            if pause_requested():
                raise JobPaused()
            seen += 1
            key = _photo_key(path)
            hit = known_cf.get(key.casefold())
            if hit is None:
                row = conn.execute(
                    "SELECT id, sha256, path FROM photos WHERE path = ? OR lower(path) = lower(?)",
                    (key, key),
                ).fetchone()
                if row:
                    known.add(row["path"])
                    known_cf[row["path"].casefold()] = row["path"]
                    hit = row["path"]
                    if verify_existing:
                        status = verify_file(path, row["sha256"])
                        if status == "changed":
                            conn.execute(
                                "UPDATE photos SET integrity = 'changed' WHERE id = ?",
                                (row["id"],),
                            )
                            conn.commit()
                            changed += 1
                            _report_scan(job_id, seen, already, f"changed on disk · {path.name}")
                            continue
            if hit is not None:
                skipped += 1
                report(path.name)
                continue
            try:
                st = path.stat()
                size_bytes = st.st_size
                digest = f"pending:{size_bytes}:{getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9))}"
            except OSError:
                skipped += 1
                report(f"unreadable {path.name}")
                continue
            meta = _read_image_meta(path)
            if not meta:
                skipped += 1
                report(f"unreadable {path.name}")
                continue
            img, size, taken = meta
            photo_id = _insert_photo(conn, key, digest, taken, size, size_bytes)
            if photo_id is None:
                skipped += 1
                known.add(key)
                known_cf[key.casefold()] = key
                report(path.name)
                continue
            make_thumb(path, photo_id, library=folder, img=img)
            make_view(path, photo_id, img=img)
            known.add(key)
            known_cf[key.casefold()] = key
            added += 1
            report(path.name)
        state_mod.set_state(FILE_TOTAL_KEY, str(seen))
        _report_scan(
            job_id,
            seen,
            already,
            f"{added} new, {skipped} already in the catalog",
        )
        if added:
            from . import sidecar as sidecar_mod

            update_job(job_id, message="Saving names next to albums…")
            sidecar_mod.write_under(folder)
        return {"added": added, "skipped": skipped, "changed": changed, "total_files": seen}
    finally:
        conn.close()


def verify_originals(job_id: int) -> dict:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute("SELECT id, path, sha256 FROM photos").fetchall()
        update_job(job_id, total=len(rows), message="Re-hashing originals (read-only)")
        counts = {"ok": 0, "missing": 0, "changed": 0, "unreadable": 0}
        for i, row in enumerate(rows, start=1):
            status = verify_file(Path(row["path"]), row["sha256"])
            conn.execute("UPDATE photos SET integrity = ? WHERE id = ?", (status, row["id"]))
            conn.commit()
            counts[status] = counts.get(status, 0) + 1
            if i % 20 == 0 or i == len(rows):
                update_job(job_id, progress=i, message=f"{row['path']} → {status}")
        update_job(
            job_id,
            progress=len(rows),
            message=f"Originals: {counts['ok']} unchanged, {counts['changed']} changed, {counts['missing']} missing",
        )
        return counts
    finally:
        conn.close()
