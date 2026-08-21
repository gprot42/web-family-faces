"""Portable name sidecar next to each album folder.

The live catalog stays in data/photosort.db so one person can span many
albums. Each folder that contains photos also gets `.photosort.json` so a
copied album keeps its names. Originals and EXIF are never written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from . import config
from .config import APP_NAME
from .db import connect, init_db
from .originals import SIDECAR_NAME, assert_sidecar_write, is_preview_path
from .util import now_iso

SIDECAR_VERSION = 1
_IOU_MIN = 0.25


def sidecar_path(folder: Path | str) -> Path:
    return Path(folder) / SIDECAR_NAME


def album_dir(photo_path: str | Path) -> Path:
    return Path(photo_path).parent


def _same_album(photo_path: str | Path, folder: Path) -> bool:
    parent = str(Path(photo_path).parent)
    wanted = str(folder)
    return parent == wanted or parent.casefold() == wanted.casefold()


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _album_photo_clause(folder: Path) -> tuple[str, tuple[Any, ...]]:
    """Direct files in this album, without scanning the whole catalog."""
    wanted = str(folder).rstrip("/")
    pattern = _like_escape(wanted) + "/%"
    return (
        "(ph.path LIKE ? ESCAPE '\\' AND instr(substr(ph.path, ?), '/') = 0)",
        (pattern, len(wanted) + 2),
    )


def _under_root(photo_path: str | Path, root: Path) -> bool:
    path = str(photo_path)
    prefix = str(root)
    return path == prefix or path.startswith(prefix + "/") or path.casefold().startswith(prefix.casefold() + "/")


def _safe_folder(folder: Path) -> Path | None:
    try:
        folder = Path(folder)
        if not folder.is_dir():
            return None
        resolved = folder.resolve()
    except OSError:
        return None
    if resolved == Path(resolved.anchor or "/").resolve():
        return None
    try:
        data_root = config.DATA_DIR.resolve()
        if resolved == data_root or resolved.is_relative_to(data_root):
            return None
    except (OSError, ValueError):
        return None
    if is_preview_path(resolved):
        return None
    return folder


def _norm_box(box: Iterable[float], width: int | None, height: int | None) -> tuple[float, float, float, float] | None:
    vals = list(box) if box is not None else []
    if len(vals) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in vals)
    except (TypeError, ValueError):
        return None
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        return (x1, y1, x2, y2)
    w = float(width or 1)
    h = float(height or 1)
    if w <= 0 or h <= 0:
        return None
    return (x1 / w, y1 / h, x2 / w, y2 / h)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _face_tag_norm(face: dict[str, Any]) -> list[float] | None:
    tx, ty = face.get("tag_x"), face.get("tag_y")
    if tx is None or ty is None:
        return None
    try:
        left, top = float(tx), float(ty)
    except (TypeError, ValueError):
        return None
    if max(abs(left), abs(top)) > 1.5:
        left, top = left / 100.0, top / 100.0
    left = min(1.0, max(0.0, left))
    top = min(1.0, max(0.0, top))
    return [round(left, 5), round(top, 5)]


def _tag_pct(item: dict[str, Any]) -> tuple[float, float] | None:
    tag = item.get("tag")
    if not isinstance(tag, (list, tuple)) or len(tag) < 2:
        return None
    try:
        left, top = float(tag[0]), float(tag[1])
    except (TypeError, ValueError):
        return None
    if max(abs(left), abs(top)) <= 1.5:
        left, top = left * 100.0, top * 100.0
    return (max(0.0, min(100.0, left)), max(0.0, min(100.0, top)))


def _apply_face_tag(conn, face_id: int, item: dict[str, Any]) -> bool:
    pos = _tag_pct(item)
    if not pos:
        return False
    row = conn.execute("SELECT tag_x FROM faces WHERE id = ?", (face_id,)).fetchone()
    if row and row["tag_x"] is not None:
        return False
    conn.execute("UPDATE faces SET tag_x = ?, tag_y = ? WHERE id = ?", (pos[0], pos[1], face_id))
    return True


def _photo_entry(
    path: str,
    sha256: str | None,
    width: int | None,
    height: int | None,
    faces: list[dict[str, Any]],
    comment: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    labelled: list[dict[str, Any]] = []
    for face in faces:
        junk = (face.get("assigned_how") or "") == "junk"
        name = (face.get("name") or "").strip()
        face_note = str(face.get("face_comment") or face.get("comment") or "").strip()
        if not junk and not name and not face_note:
            continue
        box = _norm_box((face["x1"], face["y1"], face["x2"], face["y2"]), width, height)
        if not box:
            continue
        item: dict[str, Any] = {"box": [round(v, 6) for v in box]}
        if junk:
            item["junk"] = True
        elif name:
            item["name"] = name
        how = face.get("assigned_how")
        if how:
            item["how"] = how
        tag = _face_tag_norm(face)
        if tag:
            item["tag"] = tag
        if face_note:
            item["comment"] = face_note[:4000]
        labelled.append(item)
    note = str(comment or "").strip()
    labels = [str(item).strip() for item in (tags or []) if str(item).strip()]
    if not labelled and not note and not labels:
        return None
    entry: dict[str, Any] = {}
    if labelled:
        entry["faces"] = labelled
    if sha256:
        entry["sha256"] = sha256
    if note:
        entry["comment"] = note
    if labels:
        entry["tags"] = labels
    return entry


def _payload_for_folder(conn, folder: Path) -> dict[str, Any]:
    photos_out: dict[str, Any] = {}
    clause, params = _album_photo_clause(folder)
    rows = conn.execute(
        f"""
        SELECT ph.id, ph.path, ph.sha256, ph.width, ph.height, ph.comment,
               f.id AS face_id, f.x1, f.y1, f.x2, f.y2, f.assigned_how, f.tag_x, f.tag_y,
               f.comment AS face_comment, p.name
        FROM photos ph
        LEFT JOIN faces f ON f.photo_id = ph.id
        LEFT JOIN people p ON p.id = f.person_id
        WHERE {clause}
        """,
        params,
    ).fetchall()
    by_photo: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not _same_album(row["path"], folder):
            continue
        bucket = by_photo.setdefault(
            int(row["id"]),
            {
                "path": row["path"],
                "sha256": row["sha256"],
                "width": row["width"],
                "height": row["height"],
                "comment": row["comment"] or "",
                "tags": [],
                "faces": [],
            },
        )
        if row["face_id"] is None:
            continue
        bucket["faces"].append(dict(row))
    tag_rows = conn.execute("SELECT photo_id, tag FROM photo_tags ORDER BY tag COLLATE NOCASE").fetchall()
    for trow in tag_rows:
        bucket = by_photo.get(int(trow["photo_id"]))
        if bucket is not None:
            bucket["tags"].append(str(trow["tag"]))
    for bucket in by_photo.values():
        entry = _photo_entry(
            bucket["path"],
            bucket["sha256"],
            bucket["width"],
            bucket["height"],
            bucket["faces"],
            bucket.get("comment") or "",
            bucket.get("tags") or [],
        )
        if entry:
            photos_out[Path(bucket["path"]).name] = entry
    return {
        "version": SIDECAR_VERSION,
        "app": APP_NAME,
        "updated_at": now_iso(),
        "photos": photos_out,
    }


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _folder_has_faces(conn, folder: Path) -> bool:
    clause, params = _album_photo_clause(folder)
    row = conn.execute(
        f"""
        SELECT 1
        FROM photos ph
        JOIN faces f ON f.photo_id = ph.id
        WHERE {clause}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None


def write_sidecar(folder: Path | str) -> str:
    """Write or remove `.photosort.json` for one album folder. Soft-fails on NAS errors."""
    folder = _safe_folder(Path(folder))
    if folder is None:
        return "skipped"
    dest = sidecar_path(folder)
    try:
        assert_sidecar_write(dest)
    except OSError:
        return "failed"
    conn = connect()
    init_db(conn)
    try:
        payload = _payload_for_folder(conn, folder)
        has_faces = _folder_has_faces(conn, folder)
    finally:
        conn.close()
    photos = payload.get("photos") or {}
    try:
        if not photos:
            # Just-imported copy: no faces yet — keep the sidecar so scan can restore names.
            if dest.is_file() and not has_faces:
                return "kept"
            if dest.is_file():
                dest.unlink()
                return "removed"
            return "empty"
        text = _dump(payload)
        if dest.is_file() and dest.read_text(encoding="utf-8") == text:
            return "unchanged"
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dest)
        return "wrote"
    except OSError:
        try:
            tmp = dest.with_name(dest.name + ".tmp")
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return "failed"


def write_folders(folders: Iterable[Path | str]) -> dict[str, int]:
    counts = {"wrote": 0, "removed": 0, "unchanged": 0, "empty": 0, "kept": 0, "skipped": 0, "failed": 0}
    seen: set[str] = set()
    for folder in folders:
        key = str(Path(folder))
        if key in seen:
            continue
        seen.add(key)
        result = write_sidecar(folder)
        counts[result] = counts.get(result, 0) + 1
    return counts


def _folders_from_paths(paths: Iterable[str]) -> list[Path]:
    return [Path(path).parent for path in paths]


def write_for_face_ids(face_ids: Iterable[int]) -> dict[str, int]:
    ids = [int(fid) for fid in face_ids]
    if not ids:
        return write_folders([])
    conn = connect()
    init_db(conn)
    try:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT DISTINCT ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    finally:
        conn.close()
    return write_folders(_folders_from_paths(r["path"] for r in rows))


def write_for_photo_ids(photo_ids: Iterable[int]) -> dict[str, int]:
    ids = [int(pid) for pid in photo_ids]
    if not ids:
        return write_folders([])
    conn = connect()
    init_db(conn)
    try:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT path FROM photos WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()
    return write_folders(_folders_from_paths(r["path"] for r in rows))


def write_for_person_ids(person_ids: Iterable[int]) -> dict[str, int]:
    ids = [int(pid) for pid in person_ids]
    if not ids:
        return write_folders([])
    conn = connect()
    init_db(conn)
    try:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT DISTINCT ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    finally:
        conn.close()
    return write_folders(_folders_from_paths(r["path"] for r in rows))


def write_all() -> dict[str, int]:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute("SELECT path FROM photos").fetchall()
    finally:
        conn.close()
    return write_folders(_folders_from_paths(r["path"] for r in rows))


def write_under(root: Path | str) -> dict[str, int]:
    root = Path(root)
    try:
        root_res = root.resolve()
    except OSError:
        root_res = root
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute("SELECT path FROM photos").fetchall()
    finally:
        conn.close()
    folders: list[Path] = []
    for row in rows:
        if _under_root(row["path"], root_res):
            folders.append(Path(row["path"]).parent)
    return write_folders(folders)


def read_sidecar(folder: Path | str) -> dict[str, Any] | None:
    path = sidecar_path(folder)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _entry_for_photo(photos_map: dict[str, Any], filename: str, sha256: str | None) -> dict[str, Any] | None:
    raw = photos_map.get(filename)
    if isinstance(raw, dict):
        return raw
    folded = {str(key).casefold(): value for key, value in photos_map.items()}
    raw = folded.get(filename.casefold())
    if isinstance(raw, dict):
        return raw
    if sha256:
        for value in photos_map.values():
            if isinstance(value, dict) and value.get("sha256") == sha256:
                return value
    return None


def _match_faces(
    detected: list[dict[str, Any]],
    sidecar_faces: list[dict[str, Any]],
    width: int | None,
    height: int | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[float, int, int]] = []
    norms: list[tuple[float, float, float, float] | None] = []
    for face in detected:
        norms.append(_norm_box((face["x1"], face["y1"], face["x2"], face["y2"]), width, height))
    side_norms: list[tuple[float, float, float, float] | None] = []
    for item in sidecar_faces:
        side_norms.append(_norm_box(item.get("box") or [], width, height))
    for i, db_box in enumerate(norms):
        if not db_box:
            continue
        for j, side_box in enumerate(side_norms):
            if not side_box:
                continue
            pairs.append((_iou(db_box, side_box), i, j))
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_i: set[int] = set()
    used_j: set[int] = set()
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for score, i, j in pairs:
        if score < _IOU_MIN or i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        matches.append((detected[i], sidecar_faces[j]))
    if not matches and len(detected) == 1 and len(sidecar_faces) == 1:
        matches.append((detected[0], sidecar_faces[0]))
    return matches


def _person_id(conn, name: str, cache: dict[str, int]) -> int:
    key = name.casefold()
    if key in cache:
        return cache[key]
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row:
        cache[key] = int(row["id"])
        return cache[key]
    cur = conn.execute(
        "INSERT INTO people (name, notes, birth_year, created_at) VALUES (?, ?, ?, ?)",
        (name, "", None, now_iso()),
    )
    pid = int(cur.lastrowid)
    cache[key] = pid
    return pid


def apply_to_photos(photo_ids: Iterable[int] | None = None) -> dict[str, int]:
    """Restore names from album sidecars onto unnamed faces (central catalog wins if already named)."""
    conn = connect()
    init_db(conn)
    assigned = 0
    junked = 0
    commented = 0
    photos_seen = 0
    try:
        sql = "SELECT id, path, sha256, width, height, comment FROM photos"
        params: list[Any] = []
        ids = [int(pid) for pid in photo_ids] if photo_ids is not None else None
        if ids is not None:
            if not ids:
                return {"photos": 0, "assigned": 0, "junked": 0, "commented": 0}
            sql += f" WHERE id IN ({','.join('?' * len(ids))})"
            params = ids
        photos = conn.execute(sql, params).fetchall()
        cache: dict[str, int] = {}
        sidecar_cache: dict[str, dict[str, Any] | None] = {}
        for photo in photos:
            if is_preview_path(photo["path"]):
                continue
            folder = album_dir(photo["path"])
            key = str(folder)
            if key not in sidecar_cache:
                data = read_sidecar(folder)
                sidecar_cache[key] = data
            data = sidecar_cache[key]
            if not data:
                continue
            photos_map = data.get("photos")
            if not isinstance(photos_map, dict):
                continue
            entry = _entry_for_photo(photos_map, Path(photo["path"]).name, photo["sha256"])
            if not entry:
                continue
            note = str(entry.get("comment") or "").strip()
            if note and not str(photo["comment"] or "").strip():
                conn.execute("UPDATE photos SET comment = ? WHERE id = ?", (note[:4000], photo["id"]))
                commented += 1
            side_tags = entry.get("tags")
            if isinstance(side_tags, list):
                from .photos import clean_tags

                have = conn.execute(
                    "SELECT 1 FROM photo_tags WHERE photo_id = ? LIMIT 1",
                    (photo["id"],),
                ).fetchone()
                if not have:
                    stamp = now_iso()
                    for tag in clean_tags(side_tags):
                        conn.execute(
                            "INSERT OR IGNORE INTO photo_tags (photo_id, tag, created_at) VALUES (?, ?, ?)",
                            (photo["id"], tag, stamp),
                        )
            side_faces = entry.get("faces")
            if not isinstance(side_faces, list) or not side_faces:
                if note:
                    photos_seen += 1
                continue
            faces = conn.execute(
                """
                SELECT id, x1, y1, x2, y2, person_id, assigned_how, quality
                FROM faces WHERE photo_id = ?
                """,
                (photo["id"],),
            ).fetchall()
            if not faces:
                continue
            photos_seen += 1
            detected = [dict(f) for f in faces]
            for face, item in _match_faces(detected, side_faces, photo["width"], photo["height"]):
                if not isinstance(item, dict):
                    continue
                _apply_face_tag(conn, face["id"], item)
                face_note = str(item.get("comment") or "").strip()
                if face_note:
                    conn.execute(
                        """
                        UPDATE faces SET comment = ?
                        WHERE id = ? AND IFNULL(comment, '') = ''
                        """,
                        (face_note[:4000], face["id"]),
                    )
                if face.get("person_id") is not None:
                    continue
                if (face.get("assigned_how") or "") == "cleared":
                    continue
                if item.get("junk"):
                    if (face.get("assigned_how") or "") == "junk":
                        continue
                    conn.execute(
                        """
                        UPDATE faces
                        SET quality = 'unidentifiable', assigned_how = 'junk', cluster_id = NULL
                        WHERE id = ?
                        """,
                        (face["id"],),
                    )
                    junked += 1
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                person_id = _person_id(conn, name, cache)
                conn.execute(
                    "UPDATE faces SET person_id = ?, assigned_how = 'sidecar' WHERE id = ?",
                    (person_id, face["id"]),
                )
                assigned += 1
        conn.commit()
        return {"photos": photos_seen, "assigned": assigned, "junked": junked, "commented": commented}
    finally:
        conn.close()
