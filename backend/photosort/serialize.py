from __future__ import annotations

from pathlib import Path
from typing import Any

from .people import is_unknown_name

# Never SELECT f.* for lists — embedding blobs stall the single API worker.
FACE_SELECT = """
f.id, f.photo_id, f.x1, f.y1, f.x2, f.y2, f.det_score, f.quality,
f.age_est, f.sex_est, f.person_id, f.cluster_id, f.assigned_how,
f.tag_x, f.tag_y, f.comment, f.created_at
"""


def _row_text(row: Any, key: str, default: str = "") -> str:
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return str(val or default)


def _row_opt_float(row: Any, key: str) -> float | None:
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _row_int(row: Any, key: str, default: int = 0) -> int:
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if val is None:
        return default
    return int(val)


def face_public(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "photo_id": row["photo_id"],
        "x1": row["x1"],
        "y1": row["y1"],
        "x2": row["x2"],
        "y2": row["y2"],
        "det_score": row["det_score"],
        "quality": row["quality"],
        "age_est": row.get("age_est"),
        "sex_est": row.get("sex_est"),
        "person_id": row.get("person_id"),
        "cluster_id": row.get("cluster_id"),
        "assigned_how": row.get("assigned_how"),
        "person_name": row.get("person_name"),
        "tag_x": _row_opt_float(row, "tag_x"),
        "tag_y": _row_opt_float(row, "tag_y"),
        "comment": _row_text(row, "comment"),
        "tags": list(row.get("tags") or []),
        "crop_url": f"/api/faces/{row['id']}/crop?v=384",
        "thumb_url": f"/api/photos/{row['photo_id']}/thumb",
    }
    if row.get("taken_at") is not None:
        out["taken_at"] = row["taken_at"]
    if row.get("path"):
        out["path"] = row["path"]
        out["filename"] = Path(row["path"]).name
    if row.get("sha256"):
        out["sha256"] = row["sha256"]
    if row.get("width"):
        out["photo_width"] = row["width"]
        out["photo_height"] = row["height"]
    return out


def photo_public(
    row: dict[str, Any],
    faces: list[dict[str, Any]] | None = None,
    *,
    check_file: bool = True,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    path = Path(row["path"])
    return {
        "id": row["id"],
        "path": row["path"],
        "filename": path.name,
        "sha256": row["sha256"],
        "taken_at": row["taken_at"],
        "width": row["width"],
        "height": row["height"],
        "scanned_at": row["scanned_at"],
        "rotation": _row_int(row, "rotation"),
        "hidden": bool(_row_int(row, "hidden")),
        "comment": _row_text(row, "comment"),
        "tags": list(tags or []),
        "thumb_url": f"/api/photos/{row['id']}/thumb",
        "file_url": f"/api/photos/{row['id']}/file",
        "file_available": path.exists() if check_file else None,
        "faces": [face_public(f) for f in faces] if faces is not None else None,
    }


def person_public(row: dict[str, Any], *, cover_size: int = 384) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "name": row["name"],
        "nickname": row.get("nickname") or "",
        "notes": row.get("notes") or "",
        "birth_year": row.get("birth_year"),
        "category": row.get("category") or "",
        "face_count": row.get("face_count"),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "age_min": row.get("age_min"),
        "age_max": row.get("age_max"),
        "created_at": row.get("created_at"),
        "unknown_name": is_unknown_name(row.get("name")),
        "cover_url": f"/api/faces/{row['cover_face_id']}/crop?v={cover_size}" if row.get("cover_face_id") else None,
    }
    if "faces" in row and row["faces"] is not None:
        out["faces"] = [face_public(f) for f in row["faces"]]
    if "shots" in row and row["shots"] is not None:
        out["shots"] = [face_public(f) for f in row["shots"]]
    return out
