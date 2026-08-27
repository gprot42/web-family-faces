"""Catalog-only photo edits. Originals are never rotated or deleted."""

from __future__ import annotations

import re
from typing import Any

from .db import connect, init_db
from .serialize import photo_public
from .util import now_iso

TAG_MAX_LEN = 40
TAGS_PER_PHOTO = 12
_TAG_SPACE = re.compile(r"\s+")


def _int_field(row: Any, key: str, default: int = 0) -> int:
    try:
        return int(row[key] if row[key] is not None else default)
    except (KeyError, TypeError, ValueError):
        return default


def photo_rotation(row: Any) -> int:
    return _int_field(row, "rotation", 0) % 360


def photo_hidden(row: Any) -> bool:
    return _int_field(row, "hidden", 0) != 0


def normalize_tag(value: str) -> str:
    text = _TAG_SPACE.sub(" ", str(value or "").strip())
    if not text:
        return ""
    return text[:TAG_MAX_LEN].rstrip()


def clean_tags(values: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = normalize_tag(str(raw or ""))
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= TAGS_PER_PHOTO:
            break
    return out


def tags_for_photos(conn, photo_ids: list[int]) -> dict[int, list[str]]:
    by_photo: dict[int, list[str]] = {int(pid): [] for pid in photo_ids}
    if not photo_ids:
        return by_photo
    placeholders = ",".join("?" * len(photo_ids))
    rows = conn.execute(
        f"""
        SELECT photo_id, tag FROM photo_tags
        WHERE photo_id IN ({placeholders})
        ORDER BY tag COLLATE NOCASE
        """,
        [int(pid) for pid in photo_ids],
    ).fetchall()
    for row in rows:
        by_photo.setdefault(int(row["photo_id"]), []).append(str(row["tag"]))
    return by_photo


def _photo_out(conn, row: Any, *, check_file: bool = False) -> dict[str, Any]:
    pid = int(row["id"])
    return photo_public(
        dict(row),
        check_file=check_file,
        tags=tags_for_photos(conn, [pid]).get(pid, []),
    )


def list_photo_tags() -> list[dict[str, Any]]:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT t.tag AS tag, COUNT(*) AS photos
            FROM photo_tags t
            JOIN photos p ON p.id = t.photo_id
            WHERE IFNULL(p.hidden, 0) = 0
            GROUP BY t.tag COLLATE NOCASE
            ORDER BY photos DESC, t.tag COLLATE NOCASE
            """
        ).fetchall()
        return [{"tag": str(row["tag"]), "photos": int(row["photos"])} for row in rows]
    finally:
        conn.close()


def set_photo_tags(photo_id: int, tags: list[Any] | None) -> dict[str, Any]:
    """Replace custom tags on a photo. Originals are not touched."""
    cleaned = clean_tags(tags)
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            raise KeyError("Photo not found")
        if photo_hidden(row):
            raise KeyError("Photo not found")
        conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))
        stamp = now_iso()
        for tag in cleaned:
            conn.execute(
                "INSERT INTO photo_tags (photo_id, tag, created_at) VALUES (?, ?, ?)",
                (photo_id, tag, stamp),
            )
        conn.commit()
        updated = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        payload = _photo_out(conn, updated, check_file=False)
    finally:
        conn.close()
    from .sidecar import write_for_photo_ids

    write_for_photo_ids([photo_id])
    return payload


def rotate_photo(photo_id: int, direction: str) -> dict[str, Any]:
    step = -90 if direction == "left" else 90
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            raise KeyError("Photo not found")
        if photo_hidden(row):
            raise KeyError("Photo not found")
        nxt = (photo_rotation(row) + step) % 360
        conn.execute("UPDATE photos SET rotation = ? WHERE id = ?", (nxt, photo_id))
        conn.commit()
        from .faces import refresh_photo_crops

        refresh_photo_crops(photo_id, conn)
        updated = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return _photo_out(conn, updated, check_file=False)
    finally:
        conn.close()


def set_photo_comment(photo_id: int, comment: str) -> dict[str, Any]:
    """Store a catalog comment. The original file is not touched."""
    text = str(comment or "").strip()
    if len(text) > 4000:
        text = text[:4000].rstrip()
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            raise KeyError("Photo not found")
        if photo_hidden(row):
            raise KeyError("Photo not found")
        conn.execute("UPDATE photos SET comment = ? WHERE id = ?", (text, photo_id))
        conn.commit()
        updated = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        payload = _photo_out(conn, updated, check_file=False)
    finally:
        conn.close()
    from .sidecar import write_for_photo_ids

    write_for_photo_ids([photo_id])
    return payload


def hide_photo(photo_id: int) -> dict[str, Any]:
    """Hide from the catalog. The original file is not touched."""
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            raise KeyError("Photo not found")
        conn.execute("UPDATE photos SET hidden = 1 WHERE id = ?", (photo_id,))
        conn.commit()
        updated = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        payload = _photo_out(conn, updated, check_file=False)
        payload["hidden"] = True
        return payload
    finally:
        conn.close()
