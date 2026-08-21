"""Optional famous-face lookup. Sends face crops only, never originals."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from . import people as people_mod
from .config import CROP_DIR, LOOKUP_MODEL, LOOKUP_TIMEOUT, XAI_API_BASE, xai_api_key
from .db import connect, init_db
from .originals import drop_preview_rows

LOOKUP_PROMPT = """You identify publicly known people from face crops for a family photo catalog.

The crops are often old tourist snapshots: grainy, small, or poorly lit. A poor crop is not proof they are famous. Prefer found=false over a wrong name.

Search the public web, including image search of public portraits, and compare them to the crop. Consider royals, consorts, heads of state, first ladies, politicians, athletes, actors, and other documented public figures — including well-known Japanese public figures.

Use every catalog clue: filename, album folder, EXIF / file dates, camera, GPS, and people already named in this catalog. Companions in the same photo are the strongest clue. Name an already-catalogued person only when the face is clearly that same person, not a relative who looks similar. If they are standing with a named public figure, search who typically appears with that person at that date and place. A generic camera name like DSC00260 is not a person's name.

If this looks like a private family member, a lookalike, a child who only vaguely resembles someone, or you cannot name a specific public person, set found=false.

Rules:
- found=true only when a news caption, encyclopedia, or official biography would use this name, or when the crop is unmistakably someone already named in the catalog.
- Prefer an already-named catalog person only on a clear same-person match, never a cousin or lookalike.
- Use the most common English spelling of the public name.
- Do not invent a private person's name.
- Do not describe the photo beyond naming the person.
- confidence_pct is 0-100. Use 90+ only when public portraits clearly match. Use 70-85 when likely but the crop is weak. Below 60 if you are guessing. Never inflate confidence to force a name.
- If the user rejected names, do not suggest those names again.
"""

LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "description": "True only if this is a publicly known person."},
        "name": {
            "type": ["string", "null"],
            "description": "Official public name, or null if not found.",
        },
        "also_known_as": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Common aliases or titles.",
        },
        "role": {
            "type": ["string", "null"],
            "description": "Short public role, e.g. Emperor of Japan.",
        },
        "confidence_pct": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "How sure you are, 0-100, that this crop is that public person.",
        },
        "why": {"type": "string", "description": "One sentence on why this is or is not a public figure."},
    },
    "required": ["found", "name", "also_known_as", "role", "confidence_pct", "why"],
    "additionalProperties": False,
}


class LookupError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def lookup_status() -> dict[str, Any]:
    return {
        "available": bool(xai_api_key()),
        "model": LOOKUP_MODEL,
        "sends": "face crops only",
        "sends_originals": False,
    }


def lookup_cluster(
    cluster_id: int,
    note: str | None = None,
    rejected_names: list[str] | None = None,
    face_ids: list[int] | None = None,
) -> dict[str, Any]:
    ids = _face_ids_for_cluster(cluster_id)
    if not ids:
        ids = _existing_face_ids(face_ids)
    if ids is None:
        raise LookupError("Group not found", 404)
    if not ids:
        raise LookupError("No face crop in this group", 400)
    return lookup_face_ids(ids, note=note, rejected_names=rejected_names)


def lookup_face(
    face_id: int,
    note: str | None = None,
    rejected_names: list[str] | None = None,
) -> dict[str, Any]:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id FROM faces WHERE id = ?", (face_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise LookupError("Face not found", 404)
    return lookup_face_ids([face_id], note=note, rejected_names=rejected_names)


def lookup_person(
    person_id: int,
    note: str | None = None,
    rejected_names: list[str] | None = None,
) -> dict[str, Any]:
    ids = _face_ids_for_person(person_id)
    if ids is None:
        raise LookupError("Person not found", 404)
    if not ids:
        raise LookupError("No face crop for this person", 400)
    return lookup_face_ids(ids, note=note, rejected_names=rejected_names)


def normalize_confidence(parsed: dict[str, Any]) -> tuple[int, str]:
    raw_pct = parsed.get("confidence_pct", parsed.get("confidence"))
    if isinstance(raw_pct, bool):
        pct = None
    elif isinstance(raw_pct, (int, float)):
        pct = int(round(float(raw_pct)))
    elif isinstance(raw_pct, str) and raw_pct.strip().rstrip("%").isdigit():
        pct = int(raw_pct.strip().rstrip("%"))
    elif str(raw_pct).strip().lower() == "high":
        pct = 85
    elif str(raw_pct).strip().lower() == "medium":
        pct = 60
    elif str(raw_pct).strip().lower() == "low":
        pct = 30
    else:
        pct = 40
    pct = max(0, min(100, pct))
    if pct >= 80:
        label = "high"
    elif pct >= 55:
        label = "medium"
    else:
        label = "low"
    return pct, label


def feedback_block(note: str | None, rejected_names: list[str] | None) -> str:
    rejected = [str(name).strip() for name in (rejected_names or []) if str(name).strip()]
    bits: list[str] = []
    if rejected:
        bits.append("The user already said these names are wrong: " + ", ".join(rejected) + ". Do not suggest them again.")
    if note and str(note).strip():
        bits.append("User comment: " + str(note).strip())
    if bits:
        bits.append("Search again for a different public person, or set found=false if you cannot name one.")
    return "\n".join(bits)


def lookup_face_ids(
    face_ids: list[int],
    note: str | None = None,
    rejected_names: list[str] | None = None,
) -> dict[str, Any]:
    if not xai_api_key():
        raise LookupError("Add an xAI key or sign in with SuperGrok in Settings.", 503)
    images = _load_crops(face_ids)
    if not images:
        raise LookupError("No face crop to send. Find Known Faces first.", 400)
    hints = _lookup_hints(face_ids)
    extra = feedback_block(note, rejected_names)
    if extra:
        hints = f"{hints}\n\n{extra}".strip()
    raw = call_xai(images, hints=hints)
    parsed = parse_lookup_payload(raw)
    name = (parsed.get("name") or "").strip() or None
    found = bool(parsed.get("found") and name)
    aliases = [a.strip() for a in (parsed.get("also_known_as") or []) if str(a).strip()]
    existing = find_existing_person(name, aliases) if found and name else None
    pct, confidence = normalize_confidence(parsed)
    return {
        "found": found,
        "name": name if found else None,
        "also_known_as": aliases,
        "role": (parsed.get("role") or "").strip() or None,
        "confidence": confidence if found else "low",
        "confidence_pct": pct if found else min(pct, 40),
        "why": (parsed.get("why") or "").strip(),
        "existing_person_id": existing["id"] if existing else None,
        "existing_person_name": existing["name"] if existing else None,
        "sent_face_ids": [item["face_id"] for item in images],
        "sent_originals": False,
    }


def find_existing_person(name: str, aliases: list[str] | None = None) -> dict[str, Any] | None:
    wanted = {name.casefold()}
    for alias in aliases or []:
        if alias.strip():
            wanted.add(alias.strip().casefold())
    if not wanted:
        return None
    conn = connect()
    init_db(conn)
    try:
        for person in conn.execute("SELECT id, name FROM people"):
            n = (person["name"] or "").casefold()
            if n in wanted:
                return {"id": int(person["id"]), "name": person["name"]}
    finally:
        conn.close()
    return None


def parse_lookup_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        if _looks_like_result(raw):
            return raw
        raw = _output_text(raw)
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LookupError("Lookup returned no name. Try again, or type the name yourself.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LookupError("Lookup returned an unreadable answer. Try again.") from exc
    if not isinstance(data, dict):
        raise LookupError("Lookup returned an unreadable answer. Try again.")
    return data


def call_xai(images: list[dict[str, Any]], hints: str = "") -> dict[str, Any]:
    slim = images[:2]
    try:
        return _post_xai(slim, hints, image_search=True)
    except LookupError as exc:
        if "timed out" not in exc.message.lower():
            raise
        return _post_xai(slim[:1], hints, image_search=False)


def _post_xai(images: list[dict[str, Any]], hints: str, *, image_search: bool) -> dict[str, Any]:
    text = LOOKUP_PROMPT
    if hints.strip():
        text = (
            f"{LOOKUP_PROMPT}\n\n"
            "Catalog clues (text only; the original photo file is not attached):\n"
            f"{hints.strip()}"
        )
    content: list[dict[str, Any]] = [
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{item['b64']}",
            "detail": "high",
        }
        for item in images
    ]
    content.append({"type": "input_text", "text": text})
    tool: dict[str, Any] = {"type": "web_search"}
    if image_search:
        tool["enable_image_search"] = True
    body = {
        "model": LOOKUP_MODEL,
        "store": False,
        "input": [{"role": "user", "content": content}],
        "tools": [tool],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "famous_face",
                "schema": LOOKUP_SCHEMA,
                "strict": True,
            }
        },
    }
    timeout = httpx.Timeout(LOOKUP_TIMEOUT, connect=20.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.post(
                f"{XAI_API_BASE}/responses",
                headers={
                    "Authorization": f"Bearer {xai_api_key()}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise LookupError("Lookup timed out. Try again in a moment.", 504) from exc
    except httpx.HTTPError as exc:
        raise LookupError("Could not reach the lookup service.", 502) from exc
    if res.status_code == 401:
        raise LookupError("XAI_API_KEY was rejected. Check the key on the server.", 502)
    if res.status_code >= 400:
        detail = _error_detail(res)
        raise LookupError(detail or f"Lookup failed ({res.status_code}).", 502)
    try:
        return res.json()
    except json.JSONDecodeError as exc:
        raise LookupError("Lookup returned an unreadable answer. Try again.") from exc


def _load_crops(face_ids: list[int]) -> list[dict[str, Any]]:
    crop_root = CROP_DIR.resolve()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for face_id in face_ids:
        if face_id in seen:
            continue
        seen.add(face_id)
        path = (CROP_DIR / f"{face_id}.jpg").resolve()
        if crop_root not in path.parents and path.parent != crop_root:
            continue
        if not path.is_file():
            continue
        out.append({"face_id": face_id, "b64": base64.b64encode(path.read_bytes()).decode("ascii")})
        if len(out) >= 2:
            break
    return out


def _existing_face_ids(face_ids: list[int] | None) -> list[int] | None:
    """Crops for ids the page still has, even if the group was regrouped."""
    wanted: list[int] = []
    seen: set[int] = set()
    for raw in face_ids or []:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        if fid in seen:
            continue
        seen.add(fid)
        wanted.append(fid)
    if not wanted:
        return None
    conn = connect()
    init_db(conn)
    try:
        marks = ",".join("?" * len(wanted))
        faces = conn.execute(
            f"""
            SELECT f.id, f.det_score, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.id IN ({marks}) AND f.quality = 'ok'
            ORDER BY f.det_score DESC
            """,
            wanted,
        ).fetchall()
    finally:
        conn.close()
    kept = drop_preview_rows([dict(f) for f in faces])
    return [int(f["id"]) for f in kept[:4]]


def _face_ids_for_cluster(cluster_id: int) -> list[int] | None:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        if not row:
            return None
        faces = conn.execute(
            """
            SELECT f.id, f.det_score, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.cluster_id = ? AND f.quality = 'ok'
            ORDER BY f.det_score DESC
            """,
            (cluster_id,),
        ).fetchall()
        kept = drop_preview_rows([dict(f) for f in faces])
        return [int(f["id"]) for f in kept[:4]]
    finally:
        conn.close()


def _face_ids_for_person(person_id: int) -> list[int] | None:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            return None
        faces = conn.execute(
            """
            SELECT f.id, f.det_score, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id = ? AND f.quality = 'ok'
            ORDER BY f.det_score DESC
            """,
            (person_id,),
        ).fetchall()
        kept = drop_preview_rows([dict(f) for f in faces])
        return [int(f["id"]) for f in kept[:4]]
    finally:
        conn.close()


def _named_people(conn, sql: str, params: list[Any], limit: int = 16) -> list[str]:
    from .people import is_unknown_name

    names: list[str] = []
    seen: set[str] = set()
    for person in conn.execute(sql, params).fetchall():
        name = (person["name"] or "").strip()
        if not name or is_unknown_name(name) or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _fmt_when(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).replace("T", " ").replace("+00:00", " UTC")
    return text[:19] if len(text) > 19 and text[10] == " " else text


def _lookup_hints(face_ids: list[int]) -> str:
    """Filename, dates, EXIF, and named companions. Never attaches originals."""
    if not face_ids:
        return ""
    from .originals import read_photo_clues

    conn = connect()
    init_db(conn)
    try:
        placeholders = ",".join("?" * len(face_ids))
        rows = conn.execute(
            f"""
            SELECT f.id, ph.path, ph.taken_at, ph.created_at, ph.id AS photo_id
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.id IN ({placeholders})
            """,
            face_ids,
        ).fetchall()
        if not rows:
            return ""
        photo_ids: list[int] = []
        folders: list[str] = []
        seen_folder: set[str] = set()
        seen_photo: set[int] = set()
        lines: list[str] = []
        days: list[str] = []
        for row in rows:
            photo_id = int(row["photo_id"])
            if photo_id in seen_photo:
                continue
            seen_photo.add(photo_id)
            photo_ids.append(photo_id)
            path = Path(row["path"])
            folder = path.parent.name
            if folder and folder not in seen_folder:
                seen_folder.add(folder)
                folders.append(folder)
            try:
                clues = read_photo_clues(path)
            except Exception:
                clues = {"filename": path.name}
            taken = clues.get("exif_taken_at") or row["taken_at"]
            day = (taken or "")[:10]
            if day and day not in days:
                days.append(day)
            bits = [f"filename {clues.get('filename') or path.name}"]
            if folder:
                bits.append(f"folder {folder}")
            if taken:
                bits.append(f"taken {_fmt_when(taken)}")
            created = clues.get("file_created")
            if created and created[:10] != (taken or "")[:10]:
                bits.append(f"file created {_fmt_when(created)}")
            modified = clues.get("file_modified")
            if modified and modified[:10] not in {(taken or "")[:10], (created or "")[:10]}:
                bits.append(f"file modified {_fmt_when(modified)}")
            if clues.get("camera"):
                bits.append(f"camera {clues['camera']}")
            if clues.get("gps"):
                bits.append(f"GPS {clues['gps']}")
            lines.append("Photo: " + "; ".join(bits) + ".")

        same_photo = _named_people(
            conn,
            f"""
            SELECT DISTINCT p.name
            FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.photo_id IN ({",".join("?" * len(photo_ids))}) AND f.person_id IS NOT NULL
            ORDER BY p.name
            """,
            photo_ids,
        )
        if same_photo:
            lines.append(
                "Already named in the same photo(s): "
                + ", ".join(same_photo)
                + ". If this crop is one of them, use that exact name. If not, search who appears with them."
            )

        album_people: list[str] = []
        seen_album = {n.casefold() for n in same_photo}
        for folder in folders:
            extra = _named_people(
                conn,
                """
                SELECT DISTINCT p.name
                FROM faces f
                JOIN people p ON p.id = f.person_id
                JOIN photos ph ON ph.id = f.photo_id
                WHERE ph.path LIKE ? AND f.person_id IS NOT NULL
                ORDER BY p.name
                """,
                [f"%/{folder}/%"],
            )
            for name in extra:
                if name.casefold() in seen_album:
                    continue
                seen_album.add(name.casefold())
                album_people.append(name)
        if album_people:
            lines.append(f"Also named in this album: {', '.join(album_people[:16])}.")

        if days:
            same_day = _named_people(
                conn,
                f"""
                SELECT DISTINCT p.name
                FROM faces f
                JOIN people p ON p.id = f.person_id
                JOIN photos ph ON ph.id = f.photo_id
                WHERE substr(ph.taken_at, 1, 10) IN ({",".join("?" * len(days))})
                  AND f.person_id IS NOT NULL
                ORDER BY p.name
                """,
                days,
            )
            fresh = [n for n in same_day if n.casefold() not in seen_album]
            if fresh:
                lines.append(f"Also named on the same date: {', '.join(fresh[:12])}.")
                seen_album.update(n.casefold() for n in fresh)

        catalog = _named_people(
            conn,
            "SELECT name FROM people ORDER BY name COLLATE NOCASE",
            [],
            limit=32,
        )
        extra_catalog = [n for n in catalog if n.casefold() not in seen_album]
        if extra_catalog:
            lines.append(
                "Other people already named in this catalog: "
                + ", ".join(extra_catalog)
                + ". Use these as context. If the crop is clearly one of them, use that exact name."
            )

        lines.append("Crops only are attached. Search public portraits from that time, place, and company.")
        return "\n".join(lines)
    finally:
        conn.close()


def _looks_like_result(data: dict[str, Any]) -> bool:
    return "found" in data and ("name" in data or "why" in data)


def _output_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"])
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks)


def _error_detail(res: httpx.Response) -> str:
    try:
        payload = res.json()
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if payload.get("detail"):
            return str(payload["detail"])
    return ""


IDENTIFY_MAX_GROUPS = 24
IDENTIFY_MAX_FACES = 8
IDENTIFY_CATALOG_PCT = 80
IDENTIFY_NEW_PCT = 90


def run_identify(job_id: int) -> None:
    """Match unnamed groups to the catalog, then look remaining groups up with Grok."""
    from . import match as match_mod
    from .jobs import JobPaused, pause_requested, update_job
    from .originals import preview_path_sql

    update_job(job_id, message="Matching faces already in the catalog…", total=1, progress=0)
    matched = match_mod.match_unknown(job_id) or {}
    catalog_hits = int(matched.get("auto_assigned") or 0)
    if pause_requested():
        raise JobPaused()

    available = lookup_status().get("available")
    groups = _identify_groups()
    if not available:
        update_job(
            job_id,
            progress=1,
            total=1,
            message=(
                f"Matched {catalog_hits} from the catalog. "
                "Add an xAI key or SuperGrok in Settings to look up the rest."
            ),
        )
        return
    if not groups:
        update_job(
            job_id,
            progress=1,
            total=1,
            message=f"Matched {catalog_hits} from the catalog. Nothing left to look up.",
        )
        return

    total = len(groups)
    named = 0
    assigned = 0
    skipped = 0
    preview = preview_path_sql("ph.path")
    update_job(
        job_id,
        total=total,
        progress=0,
        message=f"Looking up 1 of {total} · matched {catalog_hits} from the catalog",
    )
    for i, group in enumerate(groups, start=1):
        if pause_requested():
            raise JobPaused()
        update_job(
            job_id,
            progress=i - 1,
            message=f"Looking up {i} of {total} · {named + assigned} named",
        )
        unnamed = _unnamed_ids_for_cluster(group["id"], preview)
        if not unnamed:
            skipped += 1
            continue
        try:
            result = lookup_cluster(group["id"], face_ids=unnamed[:4])
        except LookupError:
            skipped += 1
            continue
        except Exception:
            skipped += 1
            continue
        name = (result.get("name") or "").strip()
        pct = int(result.get("confidence_pct") or 0)
        existing = result.get("existing_person_id")
        person_id = None
        found = bool(result.get("found") and name)
        if found and existing and pct >= IDENTIFY_CATALOG_PCT:
            person_id = int(existing)
            assigned += 1
        elif (
            found
            and name
            and not people_mod.is_unknown_name(name)
            and pct >= IDENTIFY_NEW_PCT
            and int(group["face_count"]) <= IDENTIFY_MAX_FACES
        ):
            person = people_mod.find_person_by_name(name)
            if person is None:
                person = people_mod.create_person(name)
            person_id = int(person["id"])
            named += 1
        else:
            skipped += 1
            continue
        try:
            people_mod.assign_cluster(group["id"], person_id, face_ids=unnamed, sync_sidecars=False)
        except Exception:
            skipped += 1
            if found and existing and pct >= IDENTIFY_CATALOG_PCT:
                assigned = max(0, assigned - 1)
            else:
                named = max(0, named - 1)

    update_job(
        job_id,
        progress=total,
        message=(
            f"Named {named + assigned} groups"
            f" · {assigned} already in the catalog"
            f" · {catalog_hits} extra faces matched"
            f" · {skipped} skipped"
        ),
    )


def _identify_groups() -> list[dict[str, Any]]:
    from .originals import preview_path_sql

    preview = preview_path_sql("ph.path")
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            f"""
            SELECT c.id, COUNT(f.id) AS face_count
            FROM clusters c
            JOIN faces f ON f.cluster_id = c.id
            JOIN photos ph ON ph.id = f.photo_id
            WHERE c.status = 'unknown'
              AND f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND {preview}
            GROUP BY c.id
            HAVING COUNT(f.id) <= ?
            ORDER BY face_count DESC, c.id
            LIMIT ?
            """,
            (IDENTIFY_MAX_FACES, IDENTIFY_MAX_GROUPS),
        ).fetchall()
        return [{"id": int(r["id"]), "face_count": int(r["face_count"])} for r in rows]
    finally:
        conn.close()


def _unnamed_ids_for_cluster(cluster_id: int, preview: str) -> list[int]:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            f"""
            SELECT f.id, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.cluster_id = ?
              AND f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND {preview}
            ORDER BY f.det_score DESC
            """,
            (cluster_id,),
        ).fetchall()
    finally:
        conn.close()
    return [int(r["id"]) for r in drop_preview_rows([dict(r) for r in rows])]
