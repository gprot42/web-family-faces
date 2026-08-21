from __future__ import annotations

from typing import Any

from .db import connect, init_db
from .util import now_iso


def set_state(key: str, value: str | None) -> None:
    conn = connect()
    init_db(conn)
    try:
        if value is None:
            conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
        else:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def get_state(key: str) -> str | None:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def all_state() -> dict[str, str]:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute("SELECT key, value FROM app_state").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def resume_target(unnamed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where the user should continue. Clusters first, then unnamed photos."""
    from .originals import is_preview_path
    from .people import visible_unnamed_summary

    unnamed = unnamed if unnamed is not None else visible_unnamed_summary()
    if unnamed["clusters"]:
        n = unnamed["clusters"]
        faces = unnamed["faces"]
        if n == faces:
            label = f"{n} face{'' if n == 1 else 's'} to name"
        else:
            label = f"{n} group{'' if n == 1 else 's'} · {faces} face{'' if faces == 1 else 's'} to name"
        return {
            "kind": "clusters",
            "path": "/to-name",
            "label": label,
            "cluster_id": unnamed["top_cluster_id"],
        }

    conn = connect()
    init_db(conn)
    try:
        photo = None
        for row in conn.execute(
            """
            SELECT photos.id, photos.path
            FROM photos
            WHERE EXISTS (
                SELECT 1 FROM faces f
                WHERE f.photo_id = photos.id
                  AND f.person_id IS NULL
                  AND f.quality = 'ok'
                  AND IFNULL(f.assigned_how, '') != 'junk'
            )
            ORDER BY photos.taken_at IS NULL, photos.taken_at, photos.id
            """
        ):
            if not is_preview_path(row["path"]):
                photo = row
                break
        if photo:
            return {
                "kind": "photo",
                "path": f"/photos/{photo['id']}",
                "label": "A photo still has unnamed faces",
                "photo_id": photo["id"],
            }

        last_photo = get_state("last_photo_id")
        if last_photo:
            return {
                "kind": "photo",
                "path": f"/photos/{last_photo}",
                "label": "Return to the last photo you opened",
                "photo_id": int(last_photo),
            }
        return {
            "kind": "home",
            "path": "/",
            "label": "Nothing left to name",
        }
    finally:
        conn.close()
