from __future__ import annotations

from typing import Any

from .catalog import backup_status, integrity_counts
from .db import connect, init_db
from .originals import ORIGINALS_POLICY
from .state import resume_target


def folder_stats() -> dict[str, Any]:
    from .people import UNKNOWN_NAME, auto_face_count, visible_unnamed_summary

    conn = connect()
    init_db(conn)
    try:
        photos = conn.execute("SELECT COUNT(*) AS n FROM photos").fetchone()["n"]
        scanned = conn.execute("SELECT COUNT(*) AS n FROM photos WHERE scanned_at IS NOT NULL").fetchone()["n"]
        with_faces = conn.execute(
            "SELECT COUNT(DISTINCT photo_id) AS n FROM faces"
        ).fetchone()["n"]
        faces = conn.execute("SELECT COUNT(*) AS n FROM faces").fetchone()["n"]
        identified = conn.execute(
            "SELECT COUNT(*) AS n FROM faces WHERE person_id IS NOT NULL"
        ).fetchone()["n"]
        ignored = conn.execute(
            "SELECT COUNT(*) AS n FROM faces WHERE assigned_how = 'junk'"
        ).fetchone()["n"]
        unidentifiable = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE quality = 'unidentifiable' AND IFNULL(assigned_how, '') != 'junk'
            """
        ).fetchone()["n"]
        people = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        people_unknown = conn.execute(
            "SELECT COUNT(*) AS n FROM people WHERE name = ? OR name LIKE ?",
            (UNKNOWN_NAME, UNKNOWN_NAME + " %"),
        ).fetchone()["n"]
        library = conn.execute("SELECT * FROM library WHERE id = 1").fetchone()
    finally:
        conn.close()
    unnamed = visible_unnamed_summary()
    faces_auto = auto_face_count()
    people_named = max(0, int(people) - int(people_unknown))
    identifiable = faces - unidentifiable - ignored
    rate = (identified / identifiable) if identifiable else 0.0
    return {
        "folder": library["folder"] if library else None,
        "photos": photos,
        "photos_scanned": scanned,
        "photos_with_faces": with_faces,
        "faces": faces,
        "faces_identified": identified,
        "faces_unknown": unnamed["faces"],
        "faces_unidentifiable": unidentifiable,
        "faces_ignored": ignored,
        "people": people,
        "people_named": people_named,
        "people_unknown": people_unknown,
        "faces_auto": faces_auto,
        "unknown_clusters": unnamed["clusters"],
        "identification_rate": rate,
        "writes_originals": False,
        "writes_exif": False,
        "moves_originals": False,
        "names_live_in": "app database and .photosort.json per folder",
        "originals_policy": ORIGINALS_POLICY,
        "integrity": integrity_counts(),
        "resume": resume_target(unnamed),
        "backup": backup_status(),
    }
