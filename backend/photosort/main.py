from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import browse as browse_mod
from . import photos as photos_mod
from . import pipeline as pipeline_mod
from . import nas as nas_mod
from . import catalog as catalog_mod
from . import cluster as cluster_mod
from . import faces as faces_mod
from . import gedcom as gedcom_mod
from . import importer
from . import log as log_mod
from . import lookup as lookup_mod
from . import match as match_mod
from . import sharpen as sharpen_mod
from . import imagine as imagine_mod
from . import oauth as oauth_mod
from . import people as people_mod
from . import settings as settings_mod
from . import state as state_mod
from . import stats as stats_mod
from . import suggest as suggest_mod
from .config import CLUSTER_PREVIEW_LIMIT, CROP_DIR, ROOT, THUMB_DIR, UI_PORT
from .originals import drop_preview_rows, is_preview_path, preview_path_sql
from .db import connect, init_db
from .jobs import active_job, latest_jobs, request_pause, start_job
from .serialize import FACE_SELECT, face_public, person_public, photo_public


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    pipeline_mod.resume_interrupted_pipeline()
    catalog_mod.start_backup_loop()
    pipeline_mod.start_auto_update_loop()
    yield


app = FastAPI(title="Family Faces", version="0.1.0", lifespan=_lifespan)
_ui_origins = [
    f"http://127.0.0.1:{UI_PORT}",
    f"http://localhost:{UI_PORT}",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys(_ui_origins)),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_http(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        log_mod.exception("%s %s crashed", request.method, request.url.path)
        return JSONResponse(
            {"detail": "Something went wrong. The error is in data/logs/app.log."},
            status_code=500,
        )
    if (
        request.method in {"POST", "PATCH", "PUT", "DELETE"}
        and response.status_code >= 400
        and str(request.url.path).startswith("/api/")
    ):
        log_mod.warning("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


class ImportBody(BaseModel):
    folder: str | None = None
    folders: list[str] | None = None
    decade_override: int | None = None


class ResetBody(BaseModel):
    folder: str | None = None
    folders: list[str] | None = None


class LookupBody(BaseModel):
    note: str | None = None
    rejected_names: list[str] | None = None
    face_ids: list[int] | None = None


class NameBody(BaseModel):
    name: str = Field(min_length=1)
    notes: str = ""
    birth_year: int | None = None
    face_ids: list[int] | None = None
    category: str | None = None
    nickname: str | None = None


class PhotoPatch(BaseModel):
    rotate: str | None = None
    hidden: bool | None = None
    comment: str | None = None
    tags: list[str] | None = None


class AddFaceBody(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class UndoMatchBody(BaseModel):
    face_ids: list[int] = []


class FacePatch(BaseModel):
    tag_x: float | None = None
    tag_y: float | None = None
    clear_tag: bool = False
    comment: str | None = None


class AssignBody(BaseModel):
    person_id: int | None = None
    name: str | None = None
    face_ids: list[int] | None = None
    category: str | None = None


class ClientLogBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    page: str | None = None
    action: str | None = None
    cluster_id: int | None = None
    photo_id: int | None = None


class SplitBody(BaseModel):
    face_ids: list[int]


class MergeBody(BaseModel):
    source_person_id: int


class PersonSplitBody(BaseModel):
    cluster_id: int
    name: str = Field(min_length=1)


class PersonPatch(BaseModel):
    name: str | None = None
    nickname: str | None = None
    notes: str | None = None
    birth_year: int | None = None
    category: str | None = None


class CursorBody(BaseModel):
    photo_id: int | None = None
    cluster_id: int | None = None
    activity: str | None = None


class SettingsBody(BaseModel):
    xai_api_key: str | None = None
    clear_xai_key: bool = False
    auto_update: bool | None = None
    auto_scan_new: bool | None = None
    name_sex_check: bool | None = None
    folders: list[str] | None = None


class SharpenBody(BaseModel):
    fresh: bool = False


class ImagineBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    fresh: bool = False


class OAuthOpenBody(BaseModel):
    browser: str = "brave"


class ConfirmBody(BaseModel):
    face_ids: list[int] | None = None
    person_id: int | None = None


class MountBody(BaseModel):
    share: str | None = None
    all_shares: bool = False


def _conn():
    return connect()


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    return JSONResponse({"detail": "Something went wrong. Try again."}, status_code=502)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model": faces_mod.analyzer_status(),
        "lookup": lookup_mod.lookup_status(),
        "sharpen": sharpen_mod.sharpen_status(),
        "imagine": imagine_mod.imagine_status(),
    }


@app.get("/api/settings")
def get_settings(reveal: bool = Query(default=False)) -> dict[str, Any]:
    return settings_mod.public_settings(reveal=reveal)


@app.put("/api/settings")
def put_settings(body: SettingsBody) -> dict[str, Any]:
    try:
        changed = False
        if body.folders is not None:
            pipeline_mod.remember_folders(body.folders)
            changed = True
        if body.auto_update is not None or body.auto_scan_new is not None:
            settings_mod.save_auto_update(auto_update=body.auto_update, auto_scan_new=body.auto_scan_new)
            changed = True
            if settings_mod.auto_update_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
                threading.Thread(
                    target=pipeline_mod.maybe_auto_update,
                    daemon=True,
                    name="photosort-auto-update-now",
                ).start()
        if body.name_sex_check is not None:
            settings_mod.save_name_sex_check(body.name_sex_check)
            changed = True
        if body.clear_xai_key:
            return settings_mod.clear_xai_key()
        if body.xai_api_key is not None and str(body.xai_api_key).strip() != "":
            return settings_mod.save_xai_key(body.xai_api_key)
        if changed:
            return settings_mod.public_settings()
        raise settings_mod.SettingsError("Nothing to change.")
    except settings_mod.SettingsError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.post("/api/settings/oauth/start")
def oauth_start() -> dict[str, Any]:
    try:
        return oauth_mod.start_login()
    except oauth_mod.OAuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.post("/api/settings/oauth/poll")
def oauth_poll() -> dict[str, Any]:
    try:
        return oauth_mod.poll_login()
    except oauth_mod.OAuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.post("/api/settings/oauth/cancel")
def oauth_cancel() -> dict[str, Any]:
    return oauth_mod.cancel_login()


@app.post("/api/settings/oauth/open")
def oauth_open(body: OAuthOpenBody) -> dict[str, Any]:
    try:
        return oauth_mod.open_in_browser(body.browser)
    except oauth_mod.OAuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.post("/api/settings/oauth/sign-out")
def oauth_sign_out() -> dict[str, Any]:
    return oauth_mod.sign_out()


@app.get("/api/gedcom")
def get_gedcom() -> dict[str, Any]:
    return gedcom_mod.summary()


@app.get("/api/gedcom/people/{xref}")
def get_gedcom_person(xref: str) -> dict[str, Any]:
    try:
        return gedcom_mod.get_person(xref)
    except FileNotFoundError:
        raise HTTPException(404, "Load a .ged file first.") from None
    except KeyError:
        raise HTTPException(404, "That person is not in this file.") from None


@app.post("/api/gedcom")
async def upload_gedcom(file: UploadFile = File(...)) -> dict[str, Any]:
    name = file.filename or "family.ged"
    lower = name.lower()
    if not (lower.endswith(".ged") or lower.endswith(".gedcom")):
        raise HTTPException(400, "Choose a .ged or .gedcom file.")
    data = await file.read()
    try:
        return gedcom_mod.save_file(data, name)
    except gedcom_mod.GedcomError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/gedcom")
def delete_gedcom() -> dict[str, Any]:
    gedcom_mod.clear_file()
    return {"loaded": False}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return stats_mod.folder_stats()


@app.get("/api/jobs")
def jobs() -> dict[str, Any]:
    return {
        "active": active_job(),
        "recent": latest_jobs(),
        "photo_matches": _running_photo_matches(),
    }


@app.post("/api/jobs/pause")
def pause_jobs() -> dict[str, Any]:
    result = request_pause()
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "No running job")
    return result


@app.post("/api/jobs/resume")
def resume_jobs() -> dict[str, Any]:
    job = pipeline_mod.resume_latest()
    if not job:
        stored = pipeline_mod.remembered_folder_paths()
        if stored:
            names = ", ".join(path.name or str(path) for path in stored[:3])
            raise HTTPException(
                409,
                f"{names} isn't available. Mount the drive, then Resume.",
            )
        raise HTTPException(409, "Nothing to resume. Choose folders, then Find Known Faces.")
    return job


@app.get("/api/browse")
def browse(path: str | None = Query(default=None)) -> dict[str, Any]:
    return browse_mod.list_folder(path)


@app.get("/api/nas")
def nas_status() -> dict[str, Any]:
    host = nas_mod.preferred_host()
    shares = nas_mod.known_shares(host)
    return {
        "host": host,
        "shares": [
            {"name": name, "mounted": nas_mod.is_mounted(name), "path": f"/Volumes/{name}"}
            for name in shares
        ],
    }


@app.post("/api/nas/mount")
def nas_mount(body: MountBody | None = None) -> dict[str, Any]:
    share = body.share if body else None
    recent = bool(body.all_shares) if body else False
    return nas_mod.mount_known(share=share, recent=recent)


@app.get("/api/resume")
def resume() -> dict[str, Any]:
    return state_mod.resume_target()


@app.post("/api/cursor")
def save_cursor(body: CursorBody) -> dict[str, Any]:
    if body.photo_id is not None:
        state_mod.set_state("last_photo_id", str(body.photo_id))
        state_mod.set_state("last_activity", "photo")
    if body.cluster_id is not None:
        state_mod.set_state("last_cluster_id", str(body.cluster_id))
        state_mod.set_state("last_activity", "clusters")
    if body.activity:
        state_mod.set_state("last_activity", body.activity)
    return {"ok": True, "resume": state_mod.resume_target()}


@app.post("/api/verify")
def verify() -> dict[str, Any]:
    return start_job("verify", importer.verify_originals)


@app.post("/api/backup")
def backup() -> dict[str, Any]:
    return catalog_mod.backup_catalog()


@app.get("/api/catalog/folders")
def catalog_folders(under: list[str] = Query(default=[])) -> dict[str, Any]:
    folders = [item for item in under if str(item or "").strip()]
    if folders:
        return {"items": people_mod.list_albums_under(folders)}
    return {"items": people_mod.list_name_folders()}


def _import_folders(body: ImportBody) -> list[Path]:
    raw: list[str] = []
    if body.folders:
        raw.extend(item for item in body.folders if str(item).strip())
    if body.folder and str(body.folder).strip():
        raw.append(body.folder)
    folders: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        folder = Path(item).expanduser()
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        if not folder.is_dir():
            raise HTTPException(400, f"Folder not found: {folder}")
        folders.append(folder)
    if not folders:
        raise HTTPException(400, "Choose at least one folder")
    return folders


@app.post("/api/catalog/reset")
def reset_catalog(body: ResetBody | None = None) -> dict[str, Any]:
    try:
        return people_mod.reset_names(
            folder=body.folder if body else None,
            folders=body.folders if body else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/catalog/reset-matching")
def reset_matching(body: ResetBody | None = None) -> dict[str, Any]:
    try:
        return people_mod.reset_matching(
            folder=body.folder if body else None,
            folders=body.folders if body else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/library/import")
def import_library(body: ImportBody) -> dict[str, Any]:
    folders = _import_folders(body)

    def run(job_id: int) -> None:
        for folder in folders:
            importer.import_folder(job_id, folder, body.decade_override)

    return start_job("import", run)


@app.post("/api/scan")
def scan() -> dict[str, Any]:
    return start_job("scan", faces_mod.scan_pending)


@app.post("/api/cluster")
def cluster_faces() -> dict[str, Any]:
    return start_job("cluster", cluster_mod.run_clustering)


@app.post("/api/match")
def match_faces() -> dict[str, Any]:
    return start_job("match", match_mod.match_unknown)


@app.post("/api/identify")
def identify_faces() -> dict[str, Any]:
    return start_job("identify", lookup_mod.run_identify)


@app.post("/api/pipeline")
def pipeline(body: ImportBody) -> dict[str, Any]:
    folders = _import_folders(body)
    captured = list(folders)
    pipeline_mod.remember_folders(captured)
    return start_job("pipeline", lambda job_id: pipeline_mod.run_pipeline(job_id, captured))


def _folder_prefixes(folders: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in folders or []:
        text = str(raw or "").strip().rstrip("/")
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _sql_like_literal(text: str) -> str:
    """Escape LIKE wildcards so folder names with _ (Scanned_Album_...) stay exact."""
    return text.replace("#", "##").replace("%", "#%").replace("_", "#_")


def _photo_where(
    person_id: int | None = None,
    q: str = "",
    unidentified: bool = False,
    folders: list[str] | None = None,
    tag: str | None = None,
) -> tuple[list[str], list[Any]]:
    where = ["IFNULL(hidden, 0) = 0"]
    params: list[Any] = []
    if q:
        where.append(
            """(
                path LIKE ?
                OR IFNULL(comment, '') LIKE ?
                OR EXISTS (
                    SELECT 1 FROM faces fx
                    WHERE fx.photo_id = photos.id AND IFNULL(fx.comment, '') LIKE ?
                )
                OR EXISTS (
                    SELECT 1 FROM photo_tags tx
                    WHERE tx.photo_id = photos.id AND tx.tag LIKE ?
                )
            )"""
        )
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
    prefixes = _folder_prefixes(folders)
    if prefixes:
        bits = []
        for folder in prefixes:
            bits.append("(path = ? OR path LIKE ? ESCAPE '#')")
            params.extend([folder, f"{_sql_like_literal(folder)}/%"])
        where.append(f"({' OR '.join(bits)})")
    if unidentified:
        where.append(
            """EXISTS (
                SELECT 1 FROM faces f
                WHERE f.photo_id = photos.id AND f.person_id IS NULL AND f.quality = 'ok'
            )"""
        )
    if person_id:
        where.append(
            """EXISTS (
                SELECT 1 FROM faces f
                WHERE f.photo_id = photos.id AND f.person_id = ?
            )"""
        )
        params.append(person_id)
    label = photos_mod.normalize_tag(tag or "")
    if label:
        where.append(
            """EXISTS (
                SELECT 1 FROM photo_tags t
                WHERE t.photo_id = photos.id AND t.tag = ? COLLATE NOCASE
            )"""
        )
        params.append(label)
    where.extend(
        [
            "path NOT LIKE '%1024 x 768%'",
            "path NOT LIKE '%1024x768%'",
            "path NOT LIKE '%640 x 480%'",
            "path NOT LIKE '%640x480%'",
            "path NOT LIKE '%800 x 600%'",
            "path NOT LIKE '%800x600%'",
        ]
    )
    return where, params


def _visible_photo_rows(
    conn,
    person_id: int | None = None,
    q: str = "",
    unidentified: bool = False,
    folders: list[str] | None = None,
    tag: str | None = None,
):
    where, params = _photo_where(
        person_id=person_id, q=q, unidentified=unidentified, folders=folders, tag=tag
    )
    clause = " AND ".join(where)
    rows = conn.execute(
        f"SELECT * FROM photos WHERE {clause} ORDER BY taken_at IS NULL, taken_at, id",
        params,
    ).fetchall()
    return [r for r in rows if not is_preview_path(r["path"])]


def _faces_for_photos(conn, photo_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    by_photo: dict[int, list[dict[str, Any]]] = {photo_id: [] for photo_id in photo_ids}
    if not photo_ids:
        return by_photo
    placeholders = ",".join("?" * len(photo_ids))
    rows = conn.execute(
        f"""
        SELECT {FACE_SELECT}, p.name AS person_name
        FROM faces f LEFT JOIN people p ON p.id = f.person_id
        WHERE f.photo_id IN ({placeholders})
        ORDER BY f.x1
        """,
        photo_ids,
    ).fetchall()
    for row in rows:
        by_photo.setdefault(int(row["photo_id"]), []).append(dict(row))
    return by_photo


@app.get("/api/photos")
def list_photos(
    offset: int = 0,
    limit: int = 60,
    q: str = "",
    unidentified: bool = False,
    person_id: int | None = Query(default=None),
    tag: str | None = Query(default=None),
    folder: list[str] = Query(default=[]),
) -> dict[str, Any]:
    conn = _conn()
    try:
        where, params = _photo_where(
            person_id=person_id, q=q, unidentified=unidentified, folders=folder, tag=tag
        )
        clause = " AND ".join(where)
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM photos WHERE {clause}", params).fetchone()["n"])
        rows = conn.execute(
            f"SELECT * FROM photos WHERE {clause} ORDER BY taken_at IS NULL, taken_at, id LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 500)), max(0, offset)),
        ).fetchall()
        rows = [r for r in rows if not is_preview_path(r["path"])]
        ids = [row["id"] for row in rows]
        faces = _faces_for_photos(conn, ids)
        tags = photos_mod.tags_for_photos(conn, ids)
        items = [
            photo_public(
                dict(row),
                faces.get(row["id"], []),
                check_file=False,
                tags=tags.get(int(row["id"]), []),
            )
            for row in rows
        ]
        return {"total": total, "offset": offset, "limit": limit, "items": items}
    finally:
        conn.close()


@app.get("/api/photos/tags")
def list_photo_tags() -> dict[str, Any]:
    return {"items": photos_mod.list_photo_tags()}


_PREVIEW_PH = """
    AND ph.path NOT LIKE '%1024 x 768%'
    AND ph.path NOT LIKE '%1024x768%'
    AND ph.path NOT LIKE '%640 x 480%'
    AND ph.path NOT LIKE '%640x480%'
    AND ph.path NOT LIKE '%800 x 600%'
    AND ph.path NOT LIKE '%800x600%'
"""


def _parent_folder(path: str | None) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    if "/" not in text:
        return ""
    return text.rsplit("/", 1)[0]


def _sequence_from(
    person_id: int | None = None,
    tag: str | None = None,
    folder: str | None = None,
) -> tuple[str, tuple]:
    """FROM/WHERE for person, tag, or album photo order."""
    label = photos_mod.normalize_tag(tag or "")
    if person_id:
        from_sql = f"""
            FROM photos ph
            JOIN faces f ON f.photo_id = ph.id AND f.person_id = ?
            WHERE IFNULL(ph.hidden, 0) = 0
            {_PREVIEW_PH}
        """
        return from_sql, (int(person_id),)
    if label:
        from_sql = f"""
            FROM photos ph
            JOIN photo_tags t ON t.photo_id = ph.id AND t.tag = ? COLLATE NOCASE
            WHERE IFNULL(ph.hidden, 0) = 0
            {_PREVIEW_PH}
        """
        return from_sql, (label,)
    folder = str(folder or "").strip().rstrip("/")
    if folder:
        like = f"{_sql_like_literal(folder)}/%"
        nested = f"{_sql_like_literal(folder)}/%/%"
        from_sql = f"""
            FROM photos ph
            WHERE IFNULL(ph.hidden, 0) = 0
            {_PREVIEW_PH}
              AND ph.path LIKE ? ESCAPE '#'
              AND ph.path NOT LIKE ? ESCAPE '#'
        """
        return from_sql, (like, nested)
    from_sql = f"""
        FROM photos ph
        WHERE IFNULL(ph.hidden, 0) = 0
        {_PREVIEW_PH}
    """
    return from_sql, ()


def _sequence_folder(person_id: int | None, tag: str | None, path: str | None) -> str | None:
    if person_id or photos_mod.normalize_tag(tag or ""):
        return None
    folder = _parent_folder(path)
    return folder or None


def _neighbor_ids(
    conn,
    photo_id: int,
    person_id: int | None = None,
    tag: str | None = None,
) -> tuple[int | None, int | None]:
    """Prev/next in album order: dated photos first, then undated, then id."""
    row = conn.execute(
        "SELECT id, taken_at, path FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()
    if not row:
        return None, None
    taken = row["taken_at"]
    from_sql, base = _sequence_from(person_id, tag, _sequence_folder(person_id, tag, row["path"]))
    if taken is None:
        prev = conn.execute(
            f"""
            SELECT DISTINCT ph.id AS id
            {from_sql}
              AND (
                ph.taken_at IS NOT NULL
                OR (ph.taken_at IS NULL AND ph.id < ?)
              )
            ORDER BY ph.taken_at IS NULL DESC, ph.taken_at DESC, ph.id DESC
            LIMIT 1
            """,
            (*base, photo_id),
        ).fetchone()
        nxt = conn.execute(
            f"""
            SELECT DISTINCT ph.id AS id
            {from_sql}
              AND ph.taken_at IS NULL
              AND ph.id > ?
            ORDER BY ph.id
            LIMIT 1
            """,
            (*base, photo_id),
        ).fetchone()
    else:
        prev = conn.execute(
            f"""
            SELECT DISTINCT ph.id AS id
            {from_sql}
              AND ph.taken_at IS NOT NULL
              AND (ph.taken_at < ? OR (ph.taken_at = ? AND ph.id < ?))
            ORDER BY ph.taken_at DESC, ph.id DESC
            LIMIT 1
            """,
            (*base, taken, taken, photo_id),
        ).fetchone()
        nxt = conn.execute(
            f"""
            SELECT DISTINCT ph.id AS id
            {from_sql}
              AND (
                ph.taken_at > ?
                OR (ph.taken_at = ? AND ph.id > ?)
                OR ph.taken_at IS NULL
              )
            ORDER BY ph.taken_at IS NULL, ph.taken_at, ph.id
            LIMIT 1
            """,
            (*base, taken, taken, photo_id),
        ).fetchone()
    return (int(prev["id"]) if prev else None, int(nxt["id"]) if nxt else None)


def _sequence_place(
    conn,
    photo_id: int,
    person_id: int | None = None,
    tag: str | None = None,
) -> tuple[int | None, int | None]:
    """1-based index and count in the same order as prev/next neighbors."""
    photo = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        return None, None
    folder = _sequence_folder(person_id, tag, photo["path"])
    if not person_id and not photos_mod.normalize_tag(tag or "") and not folder:
        return None, None
    from_sql, base = _sequence_from(person_id, tag, folder)
    row = conn.execute(
        f"""
        SELECT idx, n FROM (
            SELECT ph.id AS id,
                   ROW_NUMBER() OVER (
                       ORDER BY ph.taken_at IS NULL, ph.taken_at, ph.id
                   ) AS idx,
                   COUNT(*) OVER () AS n
            FROM (
                SELECT DISTINCT ph.id, ph.taken_at
                {from_sql}
            ) ph
        )
        WHERE id = ?
        """,
        (*base, photo_id),
    ).fetchone()
    if not row:
        return None, None
    return int(row["idx"]), int(row["n"])


@app.get("/api/photos/{photo_id}")
def get_photo(
    photo_id: int,
    person_id: int | None = Query(default=None),
    tag: str | None = Query(default=None),
    lite: bool = False,
) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or photos_mod.photo_hidden(row):
            raise HTTPException(404, "Photo not found")
        faces = conn.execute(
            f"""
            SELECT {FACE_SELECT}, p.name AS person_name
            FROM faces f
            LEFT JOIN people p ON p.id = f.person_id
            WHERE f.photo_id = ?
            ORDER BY f.x1
            """,
            (photo_id,),
        ).fetchall()
        payload = photo_public(
            dict(row),
            [dict(f) for f in faces],
            check_file=False,
            tags=photos_mod.tags_for_photos(conn, [photo_id]).get(photo_id, []),
        )
        for face, raw in zip(payload["faces"], faces):
            face["person_name"] = raw["person_name"]
            if payload.get("taken_at") and not face.get("taken_at"):
                face["taken_at"] = payload["taken_at"]
            face["suggestions"] = []
        payload["prev_id"], payload["next_id"] = _neighbor_ids(conn, photo_id, person_id, tag)
        payload["photo_index"], payload["photo_count"] = _sequence_place(
            conn, photo_id, person_id, tag
        )
        payload["person_id"] = person_id
        if photos_mod.normalize_tag(tag or ""):
            payload["tag"] = photos_mod.normalize_tag(tag or "")
    finally:
        conn.close()
    if not lite:
        try:
            state_mod.set_state("last_photo_id", str(photo_id))
            state_mod.set_state("last_activity", "photo")
        except Exception:
            pass
    need_suggest = (
        (not lite)
        and (not active_job())
        and (not _photo_match_busy(photo_id))
        and any(not face.get("person_id") and face.get("assigned_how") != "junk" for face in payload["faces"])
    )
    if need_suggest:
        gallery = match_mod.load_named_gallery()
        for face in payload["faces"]:
            if face.get("person_id") or face.get("assigned_how") == "junk":
                continue
            face["suggestions"] = match_mod.suggestions_for_face(face["id"], gallery=gallery)
    return payload


_photo_match_lock = threading.Lock()
_photo_match: dict[int, dict[str, Any]] = {}


def _running_photo_matches() -> list[dict[str, Any]]:
    with _photo_match_lock:
        return [
            {"photo_id": pid, "status": "running", "type": "photo_match"}
            for pid, st in _photo_match.items()
            if st.get("status") == "running"
        ]


def _photo_match_busy(photo_id: int | None = None) -> bool:
    with _photo_match_lock:
        if photo_id is None:
            return any(st.get("status") == "running" for st in _photo_match.values())
        return (_photo_match.get(int(photo_id)) or {}).get("status") == "running"


def _run_photo_match(photo_id: int, detect: bool = True) -> None:
    try:
        out = match_mod.match_photo(photo_id, detect=detect)
        with _photo_match_lock:
            _photo_match[int(photo_id)] = {"status": "done", **out}
    except Exception as exc:  # noqa: BLE001 — status is polled by the UI
        with _photo_match_lock:
            _photo_match[int(photo_id)] = {
                "status": "error",
                "error": str(exc),
                "photo_id": int(photo_id),
            }


def _start_photo_match(photo_id: int, *, detect: bool = True) -> dict[str, Any]:
    pid = int(photo_id)
    with _photo_match_lock:
        cur = _photo_match.get(pid) or {}
        if cur.get("status") == "running":
            return {"ok": True, "started": True, "status": "running", "photo_id": pid}
        _photo_match[pid] = {"status": "running", "photo_id": pid}
    threading.Thread(
        target=_run_photo_match,
        args=(pid, detect),
        daemon=True,
        name=f"photosort-photo-match-{pid}",
    ).start()
    return {"ok": True, "started": True, "status": "running", "photo_id": pid}


@app.post("/api/photos/{photo_id}/match")
def rematch_photo(photo_id: int, wait: bool = False) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or photos_mod.photo_hidden(row):
            raise HTTPException(404, "Photo not found")
    finally:
        conn.close()
    if wait:
        return match_mod.match_photo(photo_id)
    return _start_photo_match(photo_id)


@app.get("/api/photos/{photo_id}/match")
def rematch_photo_status(photo_id: int) -> dict[str, Any]:
    with _photo_match_lock:
        return dict(_photo_match.get(int(photo_id)) or {"status": "idle", "photo_id": int(photo_id)})


@app.post("/api/photos/{photo_id}/match/undo")
def undo_rematch_photo(photo_id: int, body: UndoMatchBody) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or photos_mod.photo_hidden(row):
            raise HTTPException(404, "Photo not found")
    finally:
        conn.close()
    return match_mod.undo_match_photo(photo_id, body.face_ids)


@app.post("/api/photos/{photo_id}/unassign")
def unassign_photo(photo_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or photos_mod.photo_hidden(row):
            raise HTTPException(404, "Photo not found")
    finally:
        conn.close()
    n = people_mod.unassign_photo_names(photo_id, sync_sidecars=False)
    return {"ok": True, "cleared": n}


@app.post("/api/faces/warmup")
def warmup_faces() -> dict[str, Any]:
    status = faces_mod.analyzer_status()
    if status.get("ready"):
        return {"ready": True, "loading": False}
    def load() -> None:
        try:
            faces_mod.get_analyzer()
        except Exception:
            pass
    threading.Thread(target=load, daemon=True, name="photosort-face-warmup").start()
    return {"ready": False, "loading": True}


@app.post("/api/photos/{photo_id}/faces")
def add_photo_face(photo_id: int, body: AddFaceBody) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or photos_mod.photo_hidden(row):
            raise HTTPException(404, "Photo not found")
    finally:
        conn.close()
    try:
        added = faces_mod.add_manual_face(photo_id, body.x1, body.y1, body.x2, body.y2)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = _conn()
    try:
        raw = conn.execute(
            f"""
            SELECT {FACE_SELECT}, p.name AS person_name
            FROM faces f
            LEFT JOIN people p ON p.id = f.person_id
            WHERE f.id = ?
            """,
            (added["face_id"],),
        ).fetchone()
        if not raw:
            raise HTTPException(404, "Face not found")
        face = face_public(dict(raw))
        face["person_name"] = raw["person_name"]
        face["suggestions"] = []
    finally:
        conn.close()
    return {
        "face": face,
        "existing": bool(added.get("existing")),
        "restored": bool(added.get("restored")),
    }


@app.patch("/api/photos/{photo_id}")
def patch_photo(photo_id: int, body: PhotoPatch) -> dict[str, Any]:
    rotate = (body.rotate or "").strip().lower()
    if rotate:
        if rotate not in {"left", "right"}:
            raise HTTPException(400, "Rotate left or right.")
        try:
            return photos_mod.rotate_photo(photo_id, rotate)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    if body.hidden:
        try:
            return photos_mod.hide_photo(photo_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    payload = None
    if body.comment is not None:
        try:
            payload = photos_mod.set_photo_comment(photo_id, body.comment)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    if body.tags is not None:
        try:
            payload = photos_mod.set_photo_tags(photo_id, body.tags)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    if payload is not None:
        return payload
    raise HTTPException(400, "Nothing to change.")


@app.get("/api/photos/{photo_id}/file")
def photo_file(photo_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or not Path(row["path"]).exists():
            raise HTTPException(404, "File not found")
        return FileResponse(row["path"])
    finally:
        conn.close()


@app.get("/api/photos/{photo_id}/thumb")
def photo_thumb(photo_id: int):
    path = THUMB_DIR / f"{photo_id}.jpg"
    if path.exists():
        return FileResponse(path, media_type="image/jpeg")
    conn = _conn()
    try:
        row = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row or not Path(row["path"]).exists():
            raise HTTPException(404, "Thumb not found")
        made = importer.make_thumb(Path(row["path"]), photo_id)
        if made and made.exists():
            return FileResponse(made, media_type="image/jpeg")
        return FileResponse(row["path"])
    finally:
        conn.close()


@app.post("/api/photos/{photo_id}/sharpen")
def sharpen_photo(photo_id: int, body: SharpenBody | None = None) -> dict[str, Any]:
    try:
        return sharpen_mod.sharpen_photo(photo_id, fresh=bool(body.fresh) if body else False)
    except sharpen_mod.SharpenError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.get("/api/photos/{photo_id}/sharpened")
def sharpened_file(photo_id: int):
    path = sharpen_mod.preview_path(photo_id)
    if not path.is_file():
        raise HTTPException(404, "No sharpened preview")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/photos/{photo_id}/sharpened")
def drop_sharpened(photo_id: int) -> dict[str, Any]:
    if not sharpen_mod.drop_preview(photo_id):
        raise HTTPException(404, "No sharpened preview")
    return {"ok": True, "original_untouched": True}


@app.get("/api/photos/{photo_id}/imagine")
def imagine_info(photo_id: int) -> dict[str, Any]:
    return imagine_mod.preview_info(photo_id)


@app.post("/api/photos/{photo_id}/imagine")
def imagine_photo(photo_id: int, body: ImagineBody) -> dict[str, Any]:
    try:
        return imagine_mod.edit_photo(photo_id, body.prompt, fresh=bool(body.fresh))
    except imagine_mod.ImagineError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@app.get("/api/photos/{photo_id}/imagined")
def imagined_file(photo_id: int):
    path = imagine_mod.preview_path(photo_id)
    if not path.is_file():
        raise HTTPException(404, "No changed preview")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/photos/{photo_id}/imagined")
def drop_imagined(photo_id: int) -> dict[str, Any]:
    if not imagine_mod.drop_preview(photo_id):
        raise HTTPException(404, "No changed preview")
    return {"ok": True, "original_untouched": True}


@app.get("/api/faces/{face_id}/crop")
def face_crop(face_id: int):
    path = CROP_DIR / f"{face_id}.jpg"
    if not path.exists():
        raise HTTPException(404, "Crop not found")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.patch("/api/faces/{face_id}")
def patch_face(face_id: int, body: FacePatch) -> dict[str, Any]:
    move_tag = body.clear_tag or body.tag_x is not None or body.tag_y is not None
    if body.comment is None and not move_tag:
        raise HTTPException(400, "Nothing to update.")
    try:
        row = None
        if move_tag:
            row = people_mod.set_face_tag(
                face_id,
                body.tag_x,
                body.tag_y,
                clear=body.clear_tag,
                sync_sidecars=False,
            )
        if body.comment is not None:
            row = people_mod.set_face_comment(face_id, body.comment, sync_sidecars=False)
        if row is None:
            raise HTTPException(400, "Nothing to update.")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Need a label position.") from exc

    def follow_up() -> None:
        try:
            people_mod._sync_sidecars_for_faces([face_id])
        except Exception:
            log_mod.exception("sidecar follow-up failed face=%s", face_id)

    threading.Thread(target=follow_up, daemon=True).start()
    return face_public(row)


@app.post("/api/faces/{face_id}/assign")
def assign_face(face_id: int, body: AssignBody) -> dict[str, Any]:
    conn = _conn()
    try:
        face = conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not face:
            raise HTTPException(404, "Face not found")
    finally:
        conn.close()
    category = people_mod.normalize_category(body.category)
    if body.person_id:
        person_id = body.person_id
        if category:
            people_mod.update_person(person_id, category=category)
    elif body.name:
        existing = people_mod.find_person_by_name(body.name)
        if existing and not people_mod.is_unknown_name(existing.get("name") or ""):
            person_id = existing["id"]
            if category:
                people_mod.update_person(person_id, category=category)
        else:
            person_id = people_mod.create_person(body.name, category=category)["id"]
    else:
        raise HTTPException(400, "person_id or name required")
    try:
        people_mod.assign_faces([face_id], person_id, "manual", rematch=False, sync_sidecars=False)
    except Exception:
        log_mod.exception("save face assign crashed face=%s person=%s", face_id, person_id)
        raise HTTPException(500, "Could not save that name. The error is in data/logs/app.log.") from None

    photo_id = int(face["photo_id"]) if face and face["photo_id"] else None
    if photo_id and not active_job():
        _start_photo_match(photo_id)
    return {"ok": True, "person_id": person_id, "also_matched": 0}


@app.post("/api/faces/{face_id}/unknown")
def unknown_face(face_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        face = conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not face:
            raise HTTPException(404, "Face not found")
    finally:
        conn.close()
    person = people_mod.create_unknown_person()
    people_mod.assign_faces([face_id], person["id"], "unknown_name")
    return {"ok": True, "person_id": person["id"], "person": person_public(person)}


@app.post("/api/faces/{face_id}/unassign")
def unassign_face(face_id: int) -> dict[str, Any]:
    n = people_mod.unassign_face_and_copies(face_id, sync_sidecars=False)
    return {"ok": True, "cleared": n}


@app.post("/api/faces/{face_id}/junk")
def junk_face(face_id: int) -> dict[str, Any]:
    n = people_mod.junk_faces([face_id], sync_sidecars=False)

    def follow_up() -> None:
        try:
            people_mod._sync_sidecars_for_faces([face_id])
        except Exception:
            log_mod.exception("sidecar follow-up failed face=%s", face_id)
        try:
            match_mod.suppress_like_junk()
        except Exception:
            log_mod.exception("junk follow-up failed face=%s", face_id)

    threading.Thread(target=follow_up, daemon=True).start()
    return {"ok": True, "junked": n, "also_ignored": 0}


@app.post("/api/faces/{face_id}/restore")
def restore_face(face_id: int) -> dict[str, Any]:
    n = people_mod.restore_faces([face_id])
    if not n:
        raise HTTPException(404, "No hidden face to restore")
    photo_id = None
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT photo_id FROM faces WHERE id = ?", (int(face_id),)).fetchone()
        photo_id = int(row["photo_id"]) if row and row["photo_id"] else None
    finally:
        conn.close()
    # Do not rematch here. Matching would hide this crop again as a statue,
    # undoing This is a person. The user can type a name on the card.
    return {"ok": True, "restored": n, "photo_id": photo_id, "assigned": [], "started": False}


@app.get("/api/faces/{face_id}/suggestions")
def face_suggestions(face_id: int) -> dict[str, Any]:
    return {"items": match_mod.suggestions_for_face(face_id)}


def _lookup_or_raise(fn, *args):
    try:
        return fn(*args)
    except lookup_mod.LookupError as exc:
        log_mod.warning("lookup failed status=%s %s", exc.status, exc.message)
        raise HTTPException(exc.status, exc.message) from exc
    except Exception as exc:
        log_mod.exception("lookup crashed")
        raise HTTPException(502, "Lookup failed. Try again, or type the name yourself.") from exc


@app.post("/api/faces/{face_id}/lookup")
def lookup_face(face_id: int, body: LookupBody | None = None) -> dict[str, Any]:
    return _lookup_or_raise(
        lookup_mod.lookup_face,
        face_id,
        body.note if body else None,
        body.rejected_names if body else None,
    )


@app.get("/api/clusters")
def list_clusters() -> dict[str, Any]:
    preview = preview_path_sql("ph.path")
    conn = _conn()
    try:
        top = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT c.id, c.status, c.created_at,
                       COUNT(f.id) AS face_count,
                       AVG(f.age_est) AS age_mean
                FROM clusters c
                JOIN faces f ON f.cluster_id = c.id
                JOIN photos ph ON ph.id = f.photo_id
                WHERE c.status != 'junk'
                  AND f.person_id IS NULL
                  AND f.quality = 'ok'
                  AND IFNULL(f.assigned_how, '') != 'junk'
                  AND {preview}
                GROUP BY c.id
                ORDER BY face_count DESC, c.id
                LIMIT 80
                """
            ).fetchall()
        ]
        clustering = False
        if not top:
            loose = conn.execute(
                """
                SELECT COUNT(*) AS n FROM faces
                WHERE person_id IS NULL
                  AND quality = 'ok'
                  AND IFNULL(assigned_how, '') != 'junk'
                  AND cluster_id IS NULL
                  AND embedding IS NOT NULL
                """
            ).fetchone()["n"]
            if int(loose or 0) > 0:
                clustering = True
                threading.Thread(target=cluster_mod.try_run_clustering, daemon=True).start()
            return {"items": [], "total": 0, "clustering": clustering}
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM (
                  SELECT c.id
                  FROM clusters c
                  JOIN faces f ON f.cluster_id = c.id
                  JOIN photos ph ON ph.id = f.photo_id
                  WHERE c.status != 'junk'
                    AND f.person_id IS NULL
                    AND f.quality = 'ok'
                    AND IFNULL(f.assigned_how, '') != 'junk'
                    AND {preview}
                  GROUP BY c.id
                )
                """
            ).fetchone()["n"]
            or 0
        )
        ids = [int(row["id"]) for row in top]
        marks = ",".join("?" * len(ids))
        face_rows = conn.execute(
            f"""
            SELECT * FROM (
              SELECT {FACE_SELECT}, ph.path, ph.taken_at, ph.sha256,
                     ROW_NUMBER() OVER (
                       PARTITION BY f.cluster_id ORDER BY f.det_score DESC
                     ) AS rn
              FROM faces f JOIN photos ph ON ph.id = f.photo_id
              WHERE f.cluster_id IN ({marks})
                AND f.person_id IS NULL
                AND f.quality = 'ok'
                AND IFNULL(f.assigned_how, '') != 'junk'
                AND {preview}
            ) ranked
            WHERE rn <= 48
            """,
            ids,
        ).fetchall()
    finally:
        conn.close()
    by_cluster: dict[int, list[dict[str, Any]]] = {cid: [] for cid in ids}
    for face in face_rows:
        by_cluster[int(face["cluster_id"])].append(dict(face))
    items = []
    for row in top:
        faces = by_cluster.get(int(row["id"])) or []
        if not faces:
            continue
        item = dict(row)
        item["face_count"] = int(row["face_count"])
        item["faces"] = [face_public(f) for f in people_mod.display_faces(faces)[:24]]
        item["face_ids"] = [int(f["id"]) for f in item["faces"]]
        items.append(item)
    return {"items": items, "total": total, "clustering": clustering}


@app.get("/api/clusters/{cluster_id}")
def get_cluster(cluster_id: int) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Cluster not found")
        faces = conn.execute(
            f"""
            SELECT {FACE_SELECT}, ph.path, ph.taken_at, ph.width, ph.height
            FROM faces f JOIN photos ph ON ph.id = f.photo_id
            WHERE f.cluster_id = ?
            ORDER BY ph.taken_at IS NULL, ph.taken_at
            """,
            (cluster_id,),
        ).fetchall()
        payload = dict(row)
        payload["faces"] = [face_public(dict(f)) for f in faces]
        payload["face_count"] = len(payload["faces"])
        return payload
    finally:
        conn.close()


def _after_cluster_edit(*, match: bool = True, person_id: int | None = None) -> dict[str, Any]:
    """Match, regroup, sidecar, and backup off the request so Save stays instant."""
    if active_job():
        return {"auto_assigned": 0}

    extra = 0

    def follow_up() -> None:
        nonlocal extra
        try:
            if person_id:
                people_mod._sync_sidecars_for_people([int(person_id)])
            if match:
                from . import match as match_mod

                extra = match_mod.inherit_named_cluster_leftovers()
            cluster_mod.try_run_clustering(only_unclustered=True)
            catalog_mod.maybe_backup()
        except Exception:
            log_mod.exception("after cluster edit failed person_id=%s", person_id)

    if os.environ.get("PYTEST_CURRENT_TEST"):
        follow_up()
    else:
        threading.Thread(target=follow_up, daemon=True).start()
    return {"auto_assigned": extra}


def _cluster_save_payload(report: people_mod.ClusterAssignReport, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "assigned": report.assigned,
        "reason": report.reason,
        "message": None if report.assigned else report.message(),
        "skipped": {
            "considered": report.considered,
            "protected": report.skipped_protected,
            "already_in_photo": report.skipped_already,
            "lookalike": report.skipped_lookalike,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _save_cluster(
    cluster_id: int,
    person_id: int,
    face_ids: list[int],
    *,
    action: str,
    person_name: str | None = None,
) -> people_mod.ClusterAssignReport:
    try:
        report = people_mod.assign_cluster_report(
            cluster_id, person_id, face_ids=face_ids, sync_sidecars=False
        )
    except Exception:
        log_mod.exception(
            "save %s crashed cluster=%s person=%s",
            action,
            cluster_id,
            person_name or person_id,
        )
        raise HTTPException(500, "Could not save. The error is in data/logs/app.log.") from None
    if not report.assigned:
        log_mod.save_failed(
            action,
            cluster_id=cluster_id,
            person=person_name,
            person_id=person_id,
            reason=report.reason,
            considered=report.considered,
            skipped_protected=report.skipped_protected,
            skipped_already=report.skipped_already,
            skipped_lookalike=report.skipped_lookalike,
            message=report.message(),
        )
    return report


@app.post("/api/log")
def client_log(body: ClientLogBody) -> dict[str, Any]:
    log_mod.warning(
        "ui %s page=%s action=%s cluster=%s photo=%s",
        body.message.strip(),
        body.page or "",
        body.action or "",
        body.cluster_id if body.cluster_id is not None else "",
        body.photo_id if body.photo_id is not None else "",
    )
    return {"ok": True}


@app.post("/api/clusters/{cluster_id}/name")
def name_cluster(cluster_id: int, body: NameBody) -> dict[str, Any]:
    try:
        person = people_mod.find_person_by_name(body.name)
        category = people_mod.normalize_category(body.category)
        if person is None or people_mod.is_unknown_name(person.get("name") or ""):
            person = people_mod.create_person(body.name, body.notes, body.birth_year, category=category)
        elif category:
            person = people_mod.update_person(person["id"], category=category) or person
        face_ids = [int(fid) for fid in (body.face_ids or []) if fid] or people_mod.cluster_preview_face_ids(
            cluster_id
        )
        report = _save_cluster(
            cluster_id,
            person["id"],
            face_ids,
            action="name",
            person_name=person.get("name") or body.name,
        )
    except HTTPException:
        raise
    except Exception:
        log_mod.exception("save name crashed cluster=%s person=%s", cluster_id, body.name)
        raise HTTPException(500, "Could not save that name. The error is in data/logs/app.log.") from None
    matched = _after_cluster_edit(person_id=person["id"], match=True)
    state_mod.set_state("last_activity", "clusters")
    remaining = people_mod.cluster_unnamed_count(cluster_id)
    return _cluster_save_payload(
        report,
        {
            "person": person_public(person),
            "also_matched": matched.get("auto_assigned", 0),
            "remaining": remaining,
        },
    )


@app.post("/api/clusters/{cluster_id}/unknown")
def unknown_cluster(cluster_id: int, body: AssignBody | None = None) -> dict[str, Any]:
    try:
        person = people_mod.create_unknown_person()
        category = people_mod.normalize_category(body.category if body else None)
        if category:
            person = people_mod.update_person(person["id"], category=category) or person
        face_ids = [int(fid) for fid in ((body.face_ids if body else None) or []) if fid]
        if not face_ids:
            face_ids = people_mod.cluster_preview_face_ids(cluster_id)
        report = _save_cluster(
            cluster_id,
            person["id"],
            face_ids,
            action="unknown",
            person_name=person.get("name"),
        )
    except HTTPException:
        raise
    except Exception:
        log_mod.exception("save unknown crashed cluster=%s", cluster_id)
        raise HTTPException(500, "Could not save. The error is in data/logs/app.log.") from None
    return _cluster_save_payload(report, {"person": person_public(person)})


@app.post("/api/clusters/{cluster_id}/assign")
def assign_cluster(cluster_id: int, body: AssignBody) -> dict[str, Any]:
    if not body.person_id:
        raise HTTPException(400, "person_id required")
    person = people_mod.get_person(body.person_id)
    person_name = (person or {}).get("name")
    try:
        category = people_mod.normalize_category(body.category)
        if category:
            people_mod.update_person(body.person_id, category=category)
        face_ids = [int(fid) for fid in (body.face_ids or []) if fid] or people_mod.cluster_preview_face_ids(
            cluster_id
        )
        report = _save_cluster(
            cluster_id,
            body.person_id,
            face_ids,
            action="assign",
            person_name=person_name,
        )
    except HTTPException:
        raise
    except Exception:
        log_mod.exception(
            "save assign crashed cluster=%s person=%s", cluster_id, person_name or body.person_id
        )
        raise HTTPException(500, "Could not save that name. The error is in data/logs/app.log.") from None
    matched = _after_cluster_edit(person_id=body.person_id, match=True)
    remaining = people_mod.cluster_unnamed_count(cluster_id)
    return _cluster_save_payload(
        report,
        {
            "person_id": body.person_id,
            "also_matched": matched.get("auto_assigned", 0),
            "remaining": remaining,
        },
    )


@app.post("/api/clusters/{cluster_id}/junk")
def junk_cluster(cluster_id: int, body: SplitBody | None = None) -> dict[str, Any]:
    face_ids = body.face_ids if body else None
    try:
        cleared = people_mod.junk_cluster(cluster_id, face_ids, sync_sidecars=False)
    except Exception:
        log_mod.exception("save junk crashed cluster=%s", cluster_id)
        raise HTTPException(500, "Could not save. The error is in data/logs/app.log.") from None
    if not cleared:
        log_mod.save_failed("junk", cluster_id=cluster_id, reason="no_faces", message="That group was regrouped.")

    def follow_up() -> None:
        try:
            match_mod.suppress_like_junk()
        except Exception:
            log_mod.exception("junk follow-up failed cluster=%s", cluster_id)

    threading.Thread(target=follow_up, daemon=True).start()
    return {
        "cleared": cleared,
        "also_ignored": 0,
        "reason": None if cleared else "no_faces",
        "message": None if cleared else "That group was regrouped. Click Not a person again.",
    }


@app.post("/api/clusters/{cluster_id}/split")
def split_cluster(cluster_id: int, body: SplitBody) -> dict[str, Any]:
    new_id = people_mod.split_cluster(cluster_id, body.face_ids)
    return {"new_cluster_id": new_id}


@app.post("/api/clusters/{cluster_id}/lookup")
def lookup_cluster(cluster_id: int, body: LookupBody | None = None) -> dict[str, Any]:
    return _lookup_or_raise(
        lookup_mod.lookup_cluster,
        cluster_id,
        body.note if body else None,
        body.rejected_names if body else None,
        body.face_ids if body else None,
    )


@app.post("/api/search/face")
async def search_uploaded_face(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "That photo is too large. Try a smaller JPEG or PNG.")
    try:
        return match_mod.search_uploaded_face(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log_mod.exception("search uploaded face crashed")
        raise HTTPException(503, "Could not read a face in that photo yet. Try again in a moment.") from exc


@app.get("/api/search")
def search_catalog(q: str = Query(default=""), by: str = Query(default="name")) -> dict[str, Any]:
    kind = "photo" if (by or "").strip().lower() == "photo" else "name"
    if kind == "photo":
        found = people_mod.search_photos(q)
        conn = _conn()
        try:
            photo_ids = [row["id"] for row in found["photos"]]
            faces = _faces_for_photos(conn, photo_ids)
            photos = [
                photo_public(row, faces.get(row["id"], []), check_file=False) for row in found["photos"]
            ]
        finally:
            conn.close()
        return {"query": found["query"], "by": kind, "people": [], "photos": photos}
    found = people_mod.search_catalog(q)
    return {
        "query": found["query"],
        "by": kind,
        "people": [person_public(p) for p in found["people"]],
        "photos": [
            photo_public(p, check_file=False)
            | {"match_person_id": p.get("match_person_id"), "match_person_name": p.get("match_person_name")}
            for p in found["photos"]
        ],
    }


@app.get("/api/review/auto")
def list_auto_review(
    person_id: int | None = Query(default=None),
    offset: int = 0,
    limit: int = people_mod.REVIEW_PAGE,
    after_id: int | None = Query(default=None),
) -> dict[str, Any]:
    max_cap = people_mod.REVIEW_MORE_CAP if person_id is not None else 120
    cap = None if limit <= 0 else max(1, min(int(limit), max_cap))
    groups = []
    total = 0
    for group in people_mod.list_auto_faces(
        person_id=person_id, offset=offset, limit=cap, after_id=after_id
    ):
        person = group["person"]
        n = int(group.get("face_count") or len(group["faces"]))
        total += n
        groups.append(
            {
                "person": {
                    "id": person["id"],
                    "name": person["name"],
                    "nickname": person.get("nickname") or "",
                    "unknown_name": people_mod.is_unknown_name(person.get("name")),
                },
                "faces": [
                    {
                        "id": f["id"],
                        "photo_id": f["photo_id"],
                        "face_ids": f.get("face_ids") or [f["id"]],
                        "crop_url": f"/api/faces/{f['id']}/crop?v=384",
                        "filename": Path(f["path"]).name if f.get("path") else "",
                        "taken_at": f.get("taken_at"),
                    }
                    for f in group["faces"]
                ],
                "face_count": n,
            }
        )
    if person_id is not None:
        total = groups[0]["face_count"] if groups else 0
    return {"items": groups, "face_count": total}


@app.post("/api/review/auto/confirm")
def confirm_auto_review(body: ConfirmBody) -> dict[str, Any]:
    n = people_mod.confirm_faces(face_ids=body.face_ids, person_id=body.person_id)
    return {"ok": True, "confirmed": n}


@app.post("/api/faces/{face_id}/confirm")
def confirm_face(face_id: int) -> dict[str, Any]:
    n = people_mod.confirm_faces(face_ids=[face_id])
    if not n:
        raise HTTPException(404, "No auto-named face to keep")
    return {"ok": True, "confirmed": n}


@app.get("/api/people")
def list_people(
    folder: str | None = Query(default=None),
    lite: bool = False,
    names: bool = False,
) -> dict[str, Any]:
    items = [
        person_public(p, cover_size=128)
        for p in people_mod.list_people(folder=folder, lite=lite, names=names)
    ]
    return {
        "items": items,
        "folders": [] if lite or names else people_mod.list_people_folders(),
    }


@app.get("/api/people/folders")
def people_folders() -> dict[str, Any]:
    return {"items": people_mod.list_people_folders()}


@app.post("/api/people")
def create_person(body: NameBody) -> dict[str, Any]:
    return person_public(
        people_mod.create_person(
            body.name,
            body.notes,
            body.birth_year,
            category=body.category or "",
            nickname=body.nickname or "",
        )
    )


@app.get("/api/people/merge-suggestions")
def merge_suggestions() -> dict[str, Any]:
    return {"items": suggest_mod.merge_suggestions()}


@app.get("/api/people/{person_id}")
def get_person(person_id: int) -> dict[str, Any]:
    person = people_mod.get_person(person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    # Shots are the pictures to show. Skip serializing every raw face.
    person["faces"] = None
    return person_public(person)


@app.patch("/api/people/{person_id}")
def patch_person(person_id: int, body: PersonPatch, background_tasks: BackgroundTasks) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    try:
        person = people_mod.update_person(person_id, sync_sidecars=False, **fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not person:
        raise HTTPException(404, "Person not found")
    if "name" in fields:
        background_tasks.add_task(people_mod._sync_sidecars_for_people, [person_id])
    person["faces"] = None
    return person_public(person)


@app.post("/api/people/{person_id}/merge")
def merge_person(person_id: int, body: MergeBody) -> dict[str, Any]:
    try:
        person = people_mod.merge_people(body.source_person_id, person_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not active_job() and not os.environ.get("PYTEST_CURRENT_TEST"):
        start_job("match", match_mod.match_unknown)
    return person_public(person)


@app.post("/api/people/{person_id}/split")
def split_person(person_id: int, body: PersonSplitBody) -> dict[str, Any]:
    try:
        person = people_mod.split_person_cluster(person_id, body.cluster_id, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return person_public(person)


@app.post("/api/people/{person_id}/lookup")
def lookup_person(person_id: int, body: LookupBody | None = None) -> dict[str, Any]:
    return _lookup_or_raise(
        lookup_mod.lookup_person,
        person_id,
        body.note if body else None,
        body.rejected_names if body else None,
    )


FRONTEND_DIST = ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")


def run() -> None:
    import uvicorn

    from .config import API_HOST, API_PORT

    uvicorn.run("photosort.main:app", host=API_HOST, port=API_PORT, reload=False)


if __name__ == "__main__":
    run()
