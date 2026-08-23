from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import config as config_mod
from .config import (
    CLUSTER_PREVIEW_LIMIT,
    MATCH_HIGH,
    MATCH_MARGIN,
    MATCH_MEDIUM,
    MATCH_REMATCH_HIGH,
    MATCH_REMATCH_MARGIN,
    MATCH_REMATCH_MEDIUM,
    MIN_DET_SCORE,
)
from .db import connect, init_db
from .jobs import JobPaused, pause_requested, update_job
from .originals import is_preview_path
from .people import _name_sex, _norm_sex, all_person_centroids, is_unknown_name
from .util import bytes_to_embedding, cosine, l2_normalize

NN_VOTE_K = 64
NN_TOP_SAMPLES = 3
# Class photos and groups: one person is almost never in the frame twice.
CROWD_PHOTO_FACES = 8
# AdaFace is a second space. A few named photos per person is enough to retry
# a miss; filling every named crop would stall Re-identify for minutes.
ADA_EXEMPLARS_PER_PERSON = 3
SAME_FACE_IOU = 0.45
# Same gathering: name an occluded face from people named on nearby frames.
NEARBY_SECONDS = 5 * 60
# A bronze Buddha matches other Buddhas at ~0.7 and a real person at ~0.05.
STATUE_SIM = 0.58
# Cluster members looser than this are cousins/lookalikes, not identity seeds.
GALLERY_CLUSTER_MIN = 0.50
# Hits weaker than this must not fill the vote window for a huge catalog.
VOTE_MIN_SIM = 0.32
_GALLERY_SEED_HOW = ("manual", "sidecar", "merge", "split", "unknown_name")
_gallery_cache: dict[str, Any] | None = None
_gallery_stamp: tuple[Any, ...] | None = None
_ada_gallery_cache: dict[str, Any] | None = None
_ada_gallery_stamp: tuple[Any, ...] | None = None
_statue_cache: dict[str, Any] | None = None
_statue_cache_stamp: tuple[Any, ...] | None = None


def rank_people(embedding: np.ndarray, centroids: dict[int, dict[str, np.ndarray]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for pid, bands in centroids.items():
        best_sim = -1.0
        best_band = "all"
        for band, vec in bands.items():
            sim = cosine(embedding, vec)
            if sim > best_sim:
                best_sim = sim
                best_band = band
        ranked.append({"person_id": pid, "similarity": best_sim, "band": best_band})
    ranked.sort(key=lambda r: r["similarity"], reverse=True)
    return ranked


def _named_stamp(conn) -> tuple[Any, ...]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, IFNULL(MAX(f.id), 0) AS mx, IFNULL(SUM(f.person_id), 0) AS sp
        FROM faces f
        JOIN people p ON p.id = f.person_id
        WHERE f.person_id IS NOT NULL
          AND f.embedding IS NOT NULL
          AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
        """
    ).fetchone()
    return (str(config_mod.DB_PATH), int(row["n"]), int(row["mx"]), int(row["sp"]), "cluster-0.50")


def load_named_gallery(conn=None) -> dict[str, Any]:
    """Confirmed named faces only. Auto guesses must not define identity."""
    global _gallery_cache, _gallery_stamp
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        stamp = _named_stamp(conn)
        if _gallery_cache is not None and _gallery_stamp == stamp:
            return _gallery_cache
        rows = conn.execute(
            """
            SELECT f.id, f.person_id, p.name, f.embedding, f.assigned_how
            FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.person_id IS NOT NULL
              AND f.embedding IS NOT NULL
              AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
            """
        ).fetchall()
        by_person: dict[int, list[dict[str, Any]]] = defaultdict(list)
        names: dict[int, str] = {}
        for row in rows:
            if is_unknown_name(row["name"]):
                continue
            blob = row["embedding"]
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.size == 0:
                continue
            pid = int(row["person_id"])
            names[pid] = row["name"]
            by_person[pid].append(
                {
                    "id": int(row["id"]),
                    "vec": l2_normalize(vec),
                    "how": str(row["assigned_how"] or ""),
                }
            )
        vecs: list[np.ndarray] = []
        pids: list[int] = []
        face_ids: list[int] = []
        for pid, faces in by_person.items():
            seeds = [f for f in faces if f["how"] in _GALLERY_SEED_HOW]
            clustered = [f for f in faces if f["how"] not in _GALLERY_SEED_HOW]
            keep = list(seeds)
            if seeds and clustered:
                matrix = np.stack([f["vec"] for f in seeds])
                for face in clustered:
                    if float(np.max(matrix @ face["vec"])) >= GALLERY_CLUSTER_MIN:
                        keep.append(face)
            elif not seeds:
                keep.extend(clustered)
            for face in keep:
                vecs.append(face["vec"])
                pids.append(pid)
                face_ids.append(face["id"])
        if not vecs:
            gallery = {
                "matrix": None,
                "person_ids": np.zeros(0, dtype=np.int64),
                "face_ids": np.zeros(0, dtype=np.int64),
                "names": {},
            }
        else:
            gallery = {
                "matrix": np.stack(vecs).astype(np.float32, copy=False),
                "person_ids": np.asarray(pids, dtype=np.int64),
                "face_ids": np.asarray(face_ids, dtype=np.int64),
                "names": names,
            }
        _gallery_cache = gallery
        _gallery_stamp = stamp
        return gallery
    finally:
        if own:
            conn.close()


def _ada_stamp(conn) -> tuple[Any, ...]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, IFNULL(MAX(f.id), 0) AS mx,
               IFNULL(SUM(CASE WHEN f.embedding_ada IS NOT NULL THEN 1 ELSE 0 END), 0) AS na
        FROM faces f
        JOIN people p ON p.id = f.person_id
        WHERE f.person_id IS NOT NULL
          AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
        """
    ).fetchone()
    return (str(config_mod.DB_PATH), int(row["n"]), int(row["mx"]), int(row["na"]))


def _ada_fill_exemplars(conn) -> int:
    """AdaFace vectors for a few named photos per person, not the whole catalog."""
    from .adaface import embedding_for_face
    from .people import UNKNOWN_NAME

    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT f.id, f.embedding_ada,
                 ROW_NUMBER() OVER (
                   PARTITION BY f.person_id
                   ORDER BY
                     CASE WHEN p.cover_face_id IS NOT NULL AND f.id = p.cover_face_id THEN 0 ELSE 1 END,
                     CASE WHEN IFNULL(f.assigned_how, '') IN
                          ('manual', 'sidecar', 'merge', 'split', 'unknown_name') THEN 0 ELSE 1 END,
                     IFNULL(f.det_score, 0) DESC,
                     f.id
                 ) AS rn
          FROM faces f
          JOIN people p ON p.id = f.person_id
          WHERE f.person_id IS NOT NULL
            AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
            AND IFNULL(p.name, '') != ?
            AND IFNULL(p.name, '') NOT LIKE ?
        )
        SELECT id FROM ranked
        WHERE rn <= ? AND embedding_ada IS NULL
        """,
        (UNKNOWN_NAME, UNKNOWN_NAME + " %", ADA_EXEMPLARS_PER_PERSON),
    ).fetchall()
    wrote = 0
    for i, row in enumerate(rows, start=1):
        try:
            if embedding_for_face(conn, int(row["id"])) is not None:
                wrote += 1
        except Exception:
            continue
        if i % 20 == 0:
            conn.commit()
    if wrote:
        conn.commit()
    return wrote


def load_ada_gallery(conn=None, *, fill: bool = False) -> dict[str, Any]:
    """Named AdaFace vectors. Built only from confirmed names, like ArcFace."""
    global _ada_gallery_cache, _ada_gallery_stamp
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        if fill:
            wrote = _ada_fill_exemplars(conn)
            if wrote:
                _ada_gallery_cache = None
                _ada_gallery_stamp = None
        stamp = _ada_stamp(conn)
        if _ada_gallery_cache is not None and _ada_gallery_stamp == stamp:
            return _ada_gallery_cache
        rows = conn.execute(
            """
            SELECT f.id, f.person_id, p.name, f.embedding_ada, f.assigned_how
            FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.person_id IS NOT NULL
              AND f.embedding_ada IS NOT NULL
              AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
            """
        ).fetchall()
        by_person: dict[int, list[dict[str, Any]]] = defaultdict(list)
        names: dict[int, str] = {}
        for row in rows:
            if is_unknown_name(row["name"]):
                continue
            vec = bytes_to_embedding(row["embedding_ada"])
            if vec is None:
                continue
            pid = int(row["person_id"])
            names[pid] = row["name"]
            by_person[pid].append(
                {
                    "id": int(row["id"]),
                    "vec": l2_normalize(vec),
                    "how": str(row["assigned_how"] or ""),
                }
            )
        vecs: list[np.ndarray] = []
        pids: list[int] = []
        face_ids: list[int] = []
        for pid, faces in by_person.items():
            seeds = [f for f in faces if f["how"] in _GALLERY_SEED_HOW]
            keep = list(seeds) if seeds else list(faces)
            for face in keep:
                vecs.append(face["vec"])
                pids.append(pid)
                face_ids.append(face["id"])
        if not vecs:
            gallery = {
                "matrix": None,
                "person_ids": np.zeros(0, dtype=np.int64),
                "face_ids": np.zeros(0, dtype=np.int64),
                "names": {},
            }
        else:
            gallery = {
                "matrix": np.stack(vecs).astype(np.float32, copy=False),
                "person_ids": np.asarray(pids, dtype=np.int64),
                "face_ids": np.asarray(face_ids, dtype=np.int64),
                "names": names,
            }
        _ada_gallery_cache = gallery
        _ada_gallery_stamp = stamp
        return gallery
    finally:
        if own:
            conn.close()


def _ada_auto_person(
    conn,
    row,
    arc_ranked: list[dict[str, Any]],
    ada_gallery: dict[str, Any],
    *,
    high: float,
    margin: float,
    aggressive: bool,
    folder_people: set[int],
    nearby_people: set[int],
    medium: float,
    claimed: dict[int, list[Any]] | None = None,
) -> dict[str, Any] | None:
    """AdaFace name only when it is sure and ArcFace does not disagree."""
    if ada_gallery.get("matrix") is None:
        return None
    from .adaface import embedding_for_face

    vec = None
    if "embedding_ada" in row.keys():
        vec = bytes_to_embedding(row["embedding_ada"])
    if vec is None:
        vec = embedding_for_face(conn, int(row["id"]))
    if vec is None:
        return None
    ranked = rank_people_nn(vec, ada_gallery, limit=8, exclude_face_ids={int(row["id"])})
    ranked = _drop_sex_mismatch(
        ranked,
        row["sex_est"] if "sex_est" in row.keys() else None,
        _probe_age_for_sex(row),
    )
    if claimed:
        ranked = _ranked_without_claimed(ranked, claimed, row)
    if not ranked:
        return None
    if not _should_auto_assign(
        ranked,
        high,
        margin,
        aggressive=aggressive,
        folder_people=folder_people,
        nearby_people=nearby_people,
    ):
        return None
    ada_pid = int(ranked[0]["person_id"])
    if arc_ranked:
        arc = arc_ranked[0]
        if int(arc["person_id"]) != ada_pid and float(arc["similarity"]) >= medium:
            return None
    ranked[0]["band"] = "adaface"
    return ranked[0]


def _statue_stamp(conn) -> tuple[Any, ...]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, IFNULL(MAX(f.id), 0) AS mx
        FROM faces f
        WHERE f.assigned_how = 'junk' AND f.embedding IS NOT NULL
        """
    ).fetchone()
    return (str(config_mod.DB_PATH), int(row["n"]), int(row["mx"]))


def load_statue_gallery(conn=None) -> dict[str, Any]:
    """Junked faces on photos that have no remaining named person — usually statues."""
    global _statue_cache, _statue_cache_stamp
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        stamp = _statue_stamp(conn)
        if _statue_cache is not None and _statue_cache_stamp == stamp:
            return _statue_cache
        rows = conn.execute(
            """
            SELECT f.id, f.embedding
            FROM faces f
            WHERE f.assigned_how = 'junk'
              AND f.embedding IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM faces o
                WHERE o.photo_id = f.photo_id
                  AND o.person_id IS NOT NULL
                  AND IFNULL(o.assigned_how, '') != 'junk'
              )
            """
        ).fetchall()
        vecs: list[np.ndarray] = []
        face_ids: list[int] = []
        for row in rows:
            blob = row["embedding"]
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.size == 0:
                continue
            vecs.append(l2_normalize(vec))
            face_ids.append(int(row["id"]))
        gallery = {
            "matrix": None if not vecs else np.stack(vecs).astype(np.float32, copy=False),
            "face_ids": np.asarray(face_ids, dtype=np.int64),
        }
        _statue_cache = gallery
        _statue_cache_stamp = stamp
        return gallery
    finally:
        if own:
            conn.close()


def best_statue_similarity(
    embedding: np.ndarray,
    gallery: dict[str, Any] | None = None,
    *,
    exclude_face_ids: set[int] | None = None,
) -> float:
    gallery = gallery or load_statue_gallery()
    matrix = gallery.get("matrix")
    if matrix is None or len(matrix) == 0:
        return -1.0
    query = l2_normalize(embedding)
    sims = matrix @ query
    skip = {int(fid) for fid in (exclude_face_ids or set()) if fid}
    face_ids = gallery.get("face_ids")
    if skip and face_ids is not None and len(face_ids) == len(sims):
        sims = np.where(np.isin(face_ids, list(skip)), np.float32(-1.0), sims)
    return float(np.max(sims))


def matches_known_statue(
    embedding: np.ndarray,
    gallery: dict[str, Any] | None = None,
    *,
    min_sim: float = STATUE_SIM,
    exclude_face_ids: set[int] | None = None,
) -> bool:
    """True when this crop is the same kind of object as already-hidden statues."""
    return best_statue_similarity(embedding, gallery, exclude_face_ids=exclude_face_ids) >= min_sim


def _invalidate_galleries() -> None:
    global _gallery_cache, _gallery_stamp, _ada_gallery_cache, _ada_gallery_stamp
    global _statue_cache, _statue_cache_stamp
    _gallery_cache = None
    _gallery_stamp = None
    _ada_gallery_cache = None
    _ada_gallery_stamp = None
    _statue_cache = None
    _statue_cache_stamp = None


def _person_has_manual_seed(conn, person_id: int, cache: dict[int, bool] | None = None) -> bool:
    pid = int(person_id)
    if cache is not None and pid in cache:
        return cache[pid]
    row = conn.execute(
        """
        SELECT 1 FROM faces
        WHERE person_id = ?
          AND assigned_how IN ('manual', 'sidecar', 'merge')
          AND embedding IS NOT NULL
        LIMIT 1
        """,
        (pid,),
    ).fetchone()
    ok = bool(row)
    if cache is not None:
        cache[pid] = ok
    return ok


def _hide_as_statue(crop, photo_path, photo_id, vec, gallery, face_id) -> bool:
    """Hide statues. A real-looking crop is never hidden only because junked embeddings match."""
    from .faces import looks_like_statue

    if crop.exists():
        return looks_like_statue(crop, photo_path, photo_id=photo_id)
    return matches_known_statue(vec, gallery, exclude_face_ids={int(face_id)})


def _mark_face_junk(conn, face_id: int) -> None:
    conn.execute(
        """
        UPDATE faces
        SET quality = 'unidentifiable', assigned_how = 'junk', person_id = NULL, cluster_id = NULL
        WHERE id = ? AND (person_id IS NULL OR assigned_how = 'auto')
        """,
        (int(face_id),),
    )


def sweep_named_statues(conn=None) -> int:
    """Un-name auto faces that match hidden statues more than they match people."""
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        statues = load_statue_gallery(conn)
        if statues.get("matrix") is None:
            return 0
        rows = conn.execute(
            """
            SELECT f.id, f.embedding, f.photo_id, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.assigned_how = 'auto'
              AND f.person_id IS NOT NULL
              AND f.embedding IS NOT NULL
            """
        ).fetchall()
        n = 0
        from .config import CROP_DIR

        for row in rows:
            vec = bytes_to_embedding(row["embedding"])
            if vec is None:
                continue
            crop = CROP_DIR / f"{int(row['id'])}.jpg"
            if not _hide_as_statue(
                crop, row["path"], int(row["photo_id"]), vec, statues, row["id"]
            ):
                continue
            _mark_face_junk(conn, int(row["id"]))
            n += 1
        if n:
            conn.commit()
            _invalidate_galleries()
        return n
    finally:
        if own:
            conn.close()


def rank_people_nn(
    embedding: np.ndarray,
    gallery: dict[str, Any],
    limit: int = 5,
    vote_k: int = NN_VOTE_K,
    exclude_face_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Closest named photos per person, plus a short vote over the nearest hits.

    `similarity` is the single closest named photo. `mean3` is that person across
    their nearest few examples. `votes` is how often they appear in the top hits.
    A huge catalog of one person can win the vote without being the best match, so
    ranking stays by closest photo; votes only confirm a name.
    """
    matrix = gallery.get("matrix")
    pids = gallery.get("person_ids")
    names = gallery.get("names") or {}
    if matrix is None or pids is None or len(pids) == 0:
        return []
    query = l2_normalize(embedding)
    sims = matrix @ query
    skip_ids = {int(fid) for fid in (exclude_face_ids or set()) if fid}
    face_ids = gallery.get("face_ids")
    if skip_ids and face_ids is not None and len(face_ids) == len(sims):
        sims = np.where(np.isin(face_ids, list(skip_ids)), np.float32(-1.0), sims)
    n = int(sims.shape[0])
    want = max(limit, 8)
    vote_k = max(8, min(int(vote_k), n))
    scan_n = min(n, max(vote_k * 4, 256))
    if scan_n >= n:
        order = np.argsort(sims)[::-1]
    else:
        part = np.argpartition(sims, n - scan_n)[n - scan_n :]
        order = part[np.argsort(sims[part])[::-1]]
    best: dict[int, float] = {}
    samples: dict[int, list[float]] = {}
    votes: dict[int, int] = {}
    for n in range(scan_n):
        idx = int(order[n])
        pid = int(pids[idx])
        sim = float(sims[idx])
        if n < vote_k and sim >= VOTE_MIN_SIM:
            votes[pid] = votes.get(pid, 0) + 1
        bucket = samples.setdefault(pid, [])
        if len(bucket) < NN_TOP_SAMPLES:
            bucket.append(sim)
        if pid not in best:
            best[pid] = sim
    ranked = []
    for pid, sim in best.items():
        xs = samples.get(pid) or [sim]
        ranked.append(
            {
                "person_id": pid,
                "similarity": sim,
                "mean3": float(sum(xs) / len(xs)),
                "votes": int(votes.get(pid, 0)),
                "band": "nn",
                "name": names.get(pid),
            }
        )
    ranked.sort(key=lambda row: row["similarity"], reverse=True)
    return ranked[:limit]


def nn_disagrees_with_person(
    embedding: np.ndarray,
    person_id: int,
    gallery: dict[str, Any],
    *,
    exclude_face_ids: set[int] | None = None,
) -> bool:
    """True when the catalog clearly prefers someone other than `person_id`."""
    ranked = rank_people_nn(
        embedding,
        gallery,
        limit=2,
        exclude_face_ids=exclude_face_ids,
    )
    if not ranked:
        return False
    top = ranked[0]
    if int(top["person_id"]) == int(person_id):
        return False
    sim = float(top["similarity"])
    if sim < MATCH_HIGH:
        return False
    nxt = ranked[1] if len(ranked) > 1 else None
    gap = sim - (float(nxt["similarity"]) if nxt else -1.0)
    if gap >= MATCH_MARGIN:
        return True
    return int(top.get("votes") or 0) >= 8 and gap >= 0.04


def suggestions_for_face(
    face_id: int,
    limit: int = 5,
    centroids: dict[int, dict[str, np.ndarray]] | None = None,
    gallery: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT embedding FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not row:
            return []
        vec = bytes_to_embedding(row["embedding"])
        if vec is None:
            return []
        if gallery is None:
            gallery = load_named_gallery(conn)
        ranked = rank_people_nn(
            vec, gallery, limit=limit, exclude_face_ids={int(face_id)}
        )
        ranked = [row for row in ranked if float(row.get("similarity") or 0) >= MATCH_MEDIUM]
        if ranked:
            return ranked
        if gallery.get("matrix") is not None:
            return []
        if centroids is None:
            centroids = all_person_centroids(conn)
        ranked = rank_people(vec, centroids)[:limit]
        if not ranked:
            return []
        ids = [r["person_id"] for r in ranked]
        placeholders = ",".join("?" * len(ids))
        people = {
            p["id"]: dict(p)
            for p in conn.execute(f"SELECT * FROM people WHERE id IN ({placeholders})", ids)
        }
        out = []
        for item in ranked:
            person = people.get(item["person_id"])
            if not person:
                continue
            out.append({**item, "name": person["name"]})
        return out
    finally:
        conn.close()


def search_people_by_vectors(vectors: list[np.ndarray], limit: int = 8) -> list[dict[str, Any]]:
    """Rank catalog people against one or more face embeddings. Does not assign names."""
    from .people import list_people
    from .serialize import person_public

    if not vectors:
        return []
    gallery = load_named_gallery()
    best: dict[int, dict[str, Any]] = {}
    for vec in vectors:
        for row in rank_people_nn(vec, gallery, limit=max(limit, 8)):
            pid = int(row["person_id"])
            if pid not in best or float(row["similarity"]) > float(best[pid]["similarity"]):
                best[pid] = row
    ranked = sorted(best.values(), key=lambda row: row["similarity"], reverse=True)[:limit]
    catalog = {int(person["id"]): person for person in list_people()}
    out = []
    for row in ranked:
        person = catalog.get(int(row["person_id"]))
        if not person:
            continue
        item = person_public(person)
        item["similarity"] = round(float(row["similarity"]), 3)
        out.append(item)
    return out


def search_uploaded_face(data: bytes, limit: int = 8) -> dict[str, Any]:
    """Find named people who look like faces in an uploaded photo. File is not saved."""
    from .faces import embeddings_from_image_bytes

    vecs = embeddings_from_image_bytes(data)
    return {"faces_found": len(vecs), "people": search_people_by_vectors(vecs, limit=limit)}


def _box_iou(a: Any, b: Any) -> float:
    x1 = max(float(a["x1"] or 0), float(b["x1"] or 0))
    y1 = max(float(a["y1"] or 0), float(b["y1"] or 0))
    x2 = min(float(a["x2"] or 0), float(b["x2"] or 0))
    y2 = min(float(a["y2"] or 0), float(b["y2"] or 0))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if not inter:
        return 0.0
    area_a = max(0.0, float(a["x2"] or 0) - float(a["x1"] or 0)) * max(0.0, float(a["y2"] or 0) - float(a["y1"] or 0))
    area_b = max(0.0, float(b["x2"] or 0) - float(b["x1"] or 0)) * max(0.0, float(b["y2"] or 0) - float(b["y1"] or 0))
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def _person_boxes_on_photo(conn, photo_id: int) -> dict[int, list[Any]]:
    rows = conn.execute(
        """
        SELECT person_id, x1, y1, x2, y2 FROM faces
        WHERE photo_id = ?
          AND person_id IS NOT NULL
          AND IFNULL(assigned_how, '') != 'junk'
        """,
        (int(photo_id),),
    ).fetchall()
    out: dict[int, list[Any]] = {}
    for row in rows:
        out.setdefault(int(row["person_id"]), []).append(row)
    return out


def _already_on_photo(claimed: dict[int, list[Any]], person_id: int, box: Any) -> bool:
    existing = claimed.get(int(person_id)) or []
    if not existing:
        return False
    return not any(_box_iou(box, other) >= SAME_FACE_IOU for other in existing)


def _ranked_without_claimed(
    ranked: list[dict[str, Any]],
    claimed: dict[int, list[Any]],
    box: Any,
) -> list[dict[str, Any]]:
    """Skip people already named on a different box in this photo.

    Brothers and classmates must not inherit a name from someone else in frame.
    If every candidate is already in the frame, keep the list so a second
    detection of the same person can still match.
    """
    kept = [
        item
        for item in ranked
        if not _already_on_photo(claimed, int(item["person_id"]), box)
    ]
    if kept:
        return kept
    # Same person already occupies another box. Allow it only when this crop is
    # almost certainly that face (duplicate detection), not a classmate at 0.48.
    return [item for item in ranked if float(item.get("similarity") or 0) >= MATCH_HIGH]


def _person_ids_in_folder(conn, photo_id: int) -> set[int]:
    row = conn.execute("SELECT path FROM photos WHERE id = ?", (int(photo_id),)).fetchone()
    if not row:
        return set()
    parent = str(Path(row["path"]).parent).rstrip("/")
    if not parent:
        return set()
    people = conn.execute(
        """
        SELECT DISTINCT f.person_id
        FROM faces f
        JOIN photos p ON p.id = f.photo_id
        WHERE f.person_id IS NOT NULL
          AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
          AND (p.path = ? OR p.path LIKE ?)
        """,
        (parent, f"{parent}/%"),
    ).fetchall()
    return {int(item["person_id"]) for item in people}


def _parse_taken(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _people_on_nearby_photos(conn, photo_id: int, *, window_sec: int = NEARBY_SECONDS) -> set[int]:
    """People already named on other photos in this folder from the same few minutes."""
    photo = conn.execute(
        "SELECT path, taken_at FROM photos WHERE id = ?",
        (int(photo_id),),
    ).fetchone()
    if not photo:
        return set()
    origin = _parse_taken(photo["taken_at"])
    if origin is None:
        return set()
    parent = str(Path(photo["path"]).parent).rstrip("/")
    if not parent:
        return set()
    rows = conn.execute(
        """
        SELECT p.taken_at, f.person_id
        FROM faces f
        JOIN photos p ON p.id = f.photo_id
        WHERE f.person_id IS NOT NULL
          AND f.photo_id != ?
          AND IFNULL(f.assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
          AND p.taken_at IS NOT NULL
          AND (p.path = ? OR p.path LIKE ?)
        """,
        (int(photo_id), parent, f"{parent}/%"),
    ).fetchall()
    nearby: set[int] = set()
    window = float(window_sec)
    for row in rows:
        taken = _parse_taken(row["taken_at"])
        if taken is None:
            continue
        if abs((taken - origin).total_seconds()) <= window:
            nearby.add(int(row["person_id"]))
    return nearby


def _face_count_on_photo(conn, photo_id: int, cache: dict[int, int]) -> int:
    pid = int(photo_id)
    if pid not in cache:
        cache[pid] = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM faces
                WHERE photo_id = ? AND IFNULL(assigned_how, '') != 'junk'
                """,
                (pid,),
            ).fetchone()["n"]
            or 0
        )
    return cache[pid]


def _detected_face_count(conn, photo_id: int, cache: dict[int, int]) -> int:
    """Every detector box, including hidden ones. Used to skip statue junk in groups."""
    pid = int(photo_id)
    if pid not in cache:
        cache[pid] = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM faces WHERE photo_id = ?",
                (pid,),
            ).fetchone()["n"]
            or 0
        )
    return cache[pid]


def _probe_age_for_sex(row: Any) -> Any:
    """InsightFace often ages kids in group shots as adults. Trust the box size."""
    age = row["age_est"] if "age_est" in row.keys() else None
    try:
        face_h = abs(float(row["y2"] or 0) - float(row["y1"] or 0))
        photo_h = float(row["height"] or 0)
    except (TypeError, ValueError, KeyError):
        return age
    if photo_h >= 400 and face_h > 0 and face_h / photo_h <= 0.14:
        try:
            return min(float(age), 12.0) if age is not None else 12.0
        except (TypeError, ValueError):
            return 12.0
    return age


def _sex_ok(
    face_sex: Any,
    person_name: str | None,
    *,
    sim: float | None = None,
    votes: int | None = None,
    age: float | None = None,
) -> bool:
    """Block weak man-as-woman (and reverse) autos. Strong matches and kids still name.

    InsightFace sex_est is often wrong (long hair, hats, lighting). A name like
    James is male; if that person already has a real gallery, trust the name.
    Settings can turn this check off.
    """
    from . import settings as settings_mod

    if not settings_mod.name_sex_check_enabled():
        return True
    got = _norm_sex(face_sex)
    want = _name_sex(person_name or "")
    if not got or not want or got == want:
        return True
    try:
        age_n = float(age) if age is not None else None
    except (TypeError, ValueError):
        age_n = None
    if age_n is not None and age_n < 18:
        return True
    sim_n = float(sim) if sim is not None else -1.0
    votes_n = int(votes or 0)
    if votes_n >= 16:
        return True
    if sim_n >= 0.52 and votes_n >= 24:
        return True
    if sim_n >= MATCH_HIGH and votes_n >= 12:
        return True
    return False


def _drop_sex_mismatch(
    ranked: list[dict[str, Any]],
    face_sex: Any,
    age: Any = None,
) -> list[dict[str, Any]]:
    return [
        row
        for row in ranked
        if _sex_ok(
            face_sex,
            row.get("name"),
            sim=row.get("similarity"),
            votes=row.get("votes"),
            age=age,
        )
    ]


def revoke_auto_sex_mismatches(conn=None) -> int:
    """Undo auto/cluster names that conflict with the person's first name vs the face sex."""
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.sex_est, p.name
            FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.assigned_how IN ('auto', 'cluster')
              AND f.person_id IS NOT NULL
            """
        ).fetchall()
        ids = [int(row["id"]) for row in rows if not _sex_ok(row["sex_est"], row["name"])]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        conn.execute(
            f"""
            UPDATE faces
            SET person_id = NULL, assigned_how = NULL
            WHERE id IN ({marks}) AND assigned_how IN ('auto', 'cluster')
            """,
            ids,
        )
        conn.commit()
        from . import sidecar as sidecar_mod

        sidecar_mod.write_for_face_ids(ids)
        return len(ids)
    finally:
        if own:
            conn.close()


def revoke_unlike_confirmed(conn=None, min_sim: float = 0.32) -> int:
    """Drop auto/cluster names that are not close to that person's hand-named photos."""
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        people = conn.execute(
            """
            SELECT DISTINCT person_id FROM faces
            WHERE assigned_how IN ('manual', 'sidecar')
              AND person_id IS NOT NULL
              AND embedding IS NOT NULL
            """
        ).fetchall()
        drop: list[int] = []
        for person in people:
            pid = int(person["person_id"])
            seeds = conn.execute(
                """
                SELECT embedding FROM faces
                WHERE person_id = ?
                  AND assigned_how IN ('manual', 'sidecar')
                  AND embedding IS NOT NULL
                """,
                (pid,),
            ).fetchall()
            vecs = []
            for row in seeds:
                vec = bytes_to_embedding(row["embedding"])
                if vec is not None:
                    vecs.append(l2_normalize(vec))
            if not vecs:
                continue
            matrix = np.stack(vecs)
            guessed = conn.execute(
                """
                SELECT id, embedding FROM faces
                WHERE person_id = ?
                  AND assigned_how IN ('auto', 'cluster')
                  AND embedding IS NOT NULL
                """,
                (pid,),
            ).fetchall()
            for row in guessed:
                vec = bytes_to_embedding(row["embedding"])
                if vec is None:
                    continue
                if float(np.max(matrix @ l2_normalize(vec))) < min_sim:
                    drop.append(int(row["id"]))
        if not drop:
            return 0
        marks = ",".join("?" * len(drop))
        conn.execute(
            f"""
            UPDATE faces
            SET person_id = NULL, assigned_how = NULL
            WHERE id IN ({marks}) AND assigned_how IN ('auto', 'cluster')
            """,
            drop,
        )
        conn.commit()
        _invalidate_galleries()
        return len(drop)
    finally:
        if own:
            conn.close()


def _should_auto_assign(
    ranked: list[dict[str, Any]],
    high: float,
    margin: float,
    *,
    aggressive: bool = False,
    folder_people: set[int] | None = None,
    nearby_people: set[int] | None = None,
) -> bool:
    if not ranked:
        return False
    top = ranked[0]
    nxt = ranked[1] if len(ranked) > 1 else None
    sim = float(top["similarity"])
    mean3 = float(top.get("mean3") or sim)
    votes = int(top.get("votes") or 0)
    sim2 = float(nxt["similarity"]) if nxt else -1.0
    mean3_b = float(nxt.get("mean3") or sim2) if nxt else -1.0
    votes_b = int(nxt.get("votes") or 0) if nxt else 0
    gap = sim - sim2
    mean_gap = mean3 - mean3_b
    vote_leader = votes >= votes_b
    pid = int(top["person_id"])
    in_album = bool(folder_people) and pid in folder_people
    in_burst = bool(nearby_people) and pid in nearby_people

    # Occluded face in the same gathering: still has to look like that person,
    # not merely share a folder with them (statues on a Hong Kong day trip).
    burst_min = 0.32
    if in_burst and vote_leader and votes >= 12 and votes - votes_b >= 8 and sim >= burst_min:
        return True
    if in_burst and votes >= 24 and votes_b <= 4 and sim >= burst_min:
        return True

    if not aggressive:
        if sim >= high and gap >= margin:
            return True
        return mean3 >= high and mean_gap >= margin and sim >= high - 0.03

    # Re-identify: looser than Find Known Faces, not so loose that cousins match.
    if sim >= 0.50 and gap >= 0.05:
        return True
    if sim >= high and gap >= margin and (vote_leader or votes >= 8 or mean_gap >= 0.05):
        return True
    if mean3 >= 0.44 and mean_gap >= 0.06 and sim >= 0.42:
        return True
    # Vintage scans and childhood photos: several named examples of one person
    # agree even when the single closest crop is only ~0.42.
    if (
        vote_leader
        and votes >= 5
        and votes - votes_b >= 2
        and sim >= 0.40
        and mean3 >= 0.38
        and mean_gap >= 0.05
    ):
        return True
    if in_album and sim >= 0.44 and gap >= 0.05:
        return True
    if in_album and sim >= 0.46 and (not nxt or int(nxt["person_id"]) not in folder_people):
        return True
    return False


def match_unknown(
    job_id: int | None = None,
    high: float = MATCH_HIGH,
    medium: float = MATCH_MEDIUM,
    *,
    photo_id: int | None = None,
    include_cleared: bool = False,
    margin: float = MATCH_MARGIN,
    aggressive: bool = False,
    strict_crowd: bool = True,
) -> dict:
    named: list[int] = []
    assigned: list[dict[str, Any]] = []
    result = {"considered": 0, "auto_assigned": 0, "medium": 0}
    conn = connect()
    init_db(conn)
    try:
        gallery = load_named_gallery(conn)
        ada_gallery = load_ada_gallery(conn)
        rescued = rescue_hidden_named_faces(
            conn,
            photo_id=photo_id,
            high=high,
            margin=margin,
            aggressive=aggressive,
            job_id=job_id,
        )
        assigned.extend(rescued.get("assigned") or [])
        named.extend(int(item["face_id"]) for item in rescued.get("assigned") or [])
        blocked = "('junk')" if include_cleared else "('junk', 'cleared')"
        quality_ok = "('ok', 'unidentifiable')" if aggressive else "('ok')"
        sql = f"""
            SELECT f.id, f.photo_id, f.cluster_id, f.embedding, f.embedding_ada, f.sex_est, f.age_est,
                   f.x1, f.y1, f.x2, f.y2, ph.path, ph.width, ph.height
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NULL
              AND f.embedding IS NOT NULL
              AND IFNULL(f.assigned_how, '') NOT IN {blocked}
              AND (f.quality IN {quality_ok} OR f.det_score >= ?)
        """
        params: list[Any] = [MIN_DET_SCORE]
        if photo_id is not None:
            sql += " AND f.photo_id = ?"
            params.append(int(photo_id))
        rows = conn.execute(sql, params).fetchall()
        rows = [r for r in rows if not is_preview_path(r["path"])]
        mega_leftover = {
            int(item["cluster_id"])
            for item in conn.execute(
                """
                SELECT cluster_id FROM faces
                WHERE cluster_id IS NOT NULL
                  AND person_id IS NULL
                  AND quality = 'ok'
                  AND IFNULL(assigned_how, '') != 'junk'
                GROUP BY cluster_id
                HAVING COUNT(*) > ?
                """,
                (CLUSTER_PREVIEW_LIMIT,),
            ).fetchall()
        }
        if job_id:
            update_job(job_id, total=len(rows), message=f"Matching {len(rows)} unknown faces")
        auto = int(rescued.get("auto_assigned") or 0)
        ada_n = 0
        ada_filled = False
        suggested = 0
        use_nn = gallery.get("matrix") is not None
        centroids = None if use_nn else all_person_centroids(conn)
        folder_people: set[int] = set()
        crowd = (
            strict_crowd
            and photo_id is not None
            and len(rows) >= CROWD_PHOTO_FACES
        )
        if aggressive and photo_id is not None and not crowd:
            folder_people = _person_ids_in_folder(conn, int(photo_id))
        claimed_by_photo: dict[int, dict[int, list[Any]]] = {}
        manual_seed_cache: dict[int, bool] = {}
        if photo_id is not None:
            claimed_by_photo[int(photo_id)] = _person_boxes_on_photo(conn, int(photo_id))
        use_aggressive = aggressive and not crowd
        use_high = MATCH_HIGH if crowd else high
        use_margin = MATCH_MARGIN if crowd else margin
        nearby_by_photo: dict[int, set[int]] = {}
        face_count_cache: dict[int, int] = {}
        detected_count_cache: dict[int, int] = {}
        from .config import CROP_DIR
        from .faces import looks_like_statue

        statue_gallery = load_statue_gallery(conn)
        for i, row in enumerate(rows, start=1):
            vec = bytes_to_embedding(row["embedding"])
            if vec is None:
                continue
            crop = CROP_DIR / f"{int(row['id'])}.jpg"
            photo_path = row["path"] if "path" in row.keys() else None
            row_photo_id = int(row["photo_id"]) if "photo_id" in row.keys() and row["photo_id"] else None
            grouped = (
                row_photo_id is not None
                and _detected_face_count(conn, row_photo_id, detected_count_cache) >= 4
            )
            if not grouped and _hide_as_statue(
                crop, photo_path, row_photo_id, vec, statue_gallery, row["id"]
            ):
                _mark_face_junk(conn, row["id"])
                continue
            ranked = (
                rank_people_nn(vec, gallery, limit=8)
                if use_nn
                else rank_people(vec, centroids or {})
            )
            ranked = _drop_sex_mismatch(
                ranked,
                row["sex_est"] if "sex_est" in row.keys() else None,
                _probe_age_for_sex(row),
            )
            if not ranked:
                continue
            row_photo = int(row["photo_id"])
            claimed = claimed_by_photo.get(row_photo)
            if claimed is None:
                claimed = _person_boxes_on_photo(conn, row_photo)
                claimed_by_photo[row_photo] = claimed
            ranked = _ranked_without_claimed(ranked, claimed, row)
            if not ranked:
                continue
            top = ranked[0]
            pid = int(top["person_id"])
            row_crowd = (
                crowd
                if photo_id is not None
                else (
                    strict_crowd
                    and _face_count_on_photo(conn, row_photo, face_count_cache) >= CROWD_PHOTO_FACES
                )
            )
            nearby: set[int] = set()
            if not row_crowd:
                nearby = nearby_by_photo.get(row_photo) or set()
                if row_photo not in nearby_by_photo:
                    nearby = _people_on_nearby_photos(conn, row_photo)
                    nearby_by_photo[row_photo] = nearby
            can_auto = _should_auto_assign(
                ranked,
                use_high,
                use_margin,
                aggressive=use_aggressive and not row_crowd,
                folder_people=folder_people,
                nearby_people=nearby,
            )
            hit = top if can_auto else None
            via_ada = False
            if hit is None:
                if ada_gallery.get("matrix") is None and not ada_filled:
                    ada_gallery = load_ada_gallery(conn, fill=True)
                    ada_filled = True
                if ada_gallery.get("matrix") is not None:
                    hit = _ada_auto_person(
                        conn,
                        row,
                        ranked,
                        ada_gallery,
                        high=use_high,
                        margin=use_margin,
                        aggressive=use_aggressive and not row_crowd,
                        folder_people=folder_people,
                        nearby_people=nearby,
                        medium=medium,
                        claimed=claimed,
                    )
                    via_ada = hit is not None
                    if via_ada:
                        pid = int(hit["person_id"])
            if row["cluster_id"] is not None and int(row["cluster_id"]) in mega_leftover:
                # Naming 24 of a huge To-name group must not stamp the rest
                # unless this face independently matches a hand-named person.
                if hit is None or not _person_has_manual_seed(conn, pid, manual_seed_cache):
                    if top["similarity"] >= medium:
                        suggested += 1
                    continue
            if hit is not None:
                conn.execute(
                    "UPDATE faces SET person_id = ?, assigned_how = 'auto' WHERE id = ?",
                    (pid, row["id"]),
                )
                auto += 1
                if via_ada:
                    ada_n += 1
                named.append(int(row["id"]))
                claimed.setdefault(pid, []).append(row)
                assigned.append(
                    {
                        "face_id": int(row["id"]),
                        "person_id": pid,
                        "name": hit.get("name") or gallery.get("names", {}).get(pid) or ada_gallery.get("names", {}).get(pid),
                        "similarity": round(float(hit["similarity"]), 3),
                        "model": "adaface" if via_ada else "arcface",
                    }
                )
            elif top["similarity"] >= medium:
                suggested += 1
            if job_id and (i % 20 == 0 or i == len(rows)):
                conn.commit()
                msg = f"Auto-assigned {auto}"
                if ada_n:
                    msg += f" ({ada_n} AdaFace)"
                update_job(job_id, progress=i, message=msg)
                if pause_requested():
                    raise JobPaused()
        inherited = 0
        if photo_id is None:
            inherited = _inherit_named_clusters(conn, photo_id=photo_id, include_cleared=include_cleared)
        auto += inherited
        if inherited:
            extra_sql = """
                SELECT f.id, f.person_id, p.name
                FROM faces f
                JOIN people p ON p.id = f.person_id
                WHERE f.assigned_how = 'auto' AND f.person_id IS NOT NULL
            """
            extra_params: list[Any] = []
            if photo_id is not None:
                extra_sql += " AND f.photo_id = ?"
                extra_params.append(int(photo_id))
            extra = conn.execute(extra_sql, extra_params).fetchall()
            seen = {int(item["face_id"]) for item in assigned}
            already = set(named)
            for row in extra:
                fid = int(row["id"])
                if fid not in already:
                    named.append(fid)
                    already.add(fid)
                if fid in seen:
                    continue
                seen.add(fid)
                assigned.append(
                    {
                        "face_id": fid,
                        "person_id": int(row["person_id"]),
                        "name": row["name"],
                        "similarity": None,
                        "inherited": True,
                    }
                )
        conn.commit()
        if job_id:
            msg = f"Auto-assigned {auto}; {suggested} medium-confidence leftovers"
            if ada_n:
                msg = f"Auto-assigned {auto} ({ada_n} AdaFace); {suggested} medium-confidence leftovers"
            update_job(job_id, progress=len(rows), message=msg)
        result = {
            "considered": len(rows),
            "auto_assigned": auto,
            "adaface_assigned": ada_n,
            "medium": suggested,
            "inherited": inherited,
            "assigned": assigned,
        }
        if photo_id is not None:
            result["photo_id"] = int(photo_id)
    finally:
        conn.close()
    if named:
        from . import sidecar as sidecar_mod

        sidecar_mod.write_for_face_ids(named)
    return result


def undo_match_photo(photo_id: int, face_ids: list[int] | None = None) -> dict:
    """Undo auto names Re-identify just applied on this photo. Manual names stay."""
    from .people import unassign_faces

    wanted = []
    for value in face_ids or []:
        try:
            fid = int(value)
        except (TypeError, ValueError):
            continue
        if fid > 0 and fid not in wanted:
            wanted.append(fid)
    ids: list[int] = []
    conn = connect()
    init_db(conn)
    try:
        if wanted:
            marks = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"""
                SELECT id FROM faces
                WHERE photo_id = ?
                  AND id IN ({marks})
                  AND person_id IS NOT NULL
                  AND assigned_how = 'auto'
                """,
                (int(photo_id), *wanted),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
    finally:
        conn.close()
    undone = unassign_faces(ids) if ids else 0
    return {"ok": True, "photo_id": int(photo_id), "undone": int(undone), "face_ids": ids}


def best_named_similarity(embedding, gallery: dict[str, Any] | None = None) -> float:
    """Closest named-person cosine, or -1 if the catalog is empty."""
    vec = embedding if hasattr(embedding, "shape") else bytes_to_embedding(embedding)
    if vec is None:
        return -1.0
    if gallery is None:
        gallery = load_named_gallery()
    if gallery.get("matrix") is None:
        return -1.0
    ranked = rank_people_nn(vec, gallery, limit=1)
    if not ranked:
        return -1.0
    return float(ranked[0]["similarity"])


def rescue_hidden_named_faces(
    conn=None,
    photo_id: int | None = None,
    *,
    high: float = MATCH_HIGH,
    margin: float = MATCH_MARGIN,
    aggressive: bool = False,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Un-hide and name faces marked not-a-person when they clearly match the catalog."""
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    assigned: list[dict[str, Any]] = []
    named: list[int] = []
    try:
        gallery = load_named_gallery(conn)
        if gallery.get("matrix") is None:
            return {"restored": 0, "auto_assigned": 0, "assigned": []}
        sql = """
            SELECT f.id, f.photo_id, f.embedding, f.sex_est, f.age_est,
                   f.x1, f.y1, f.x2, f.y2, ph.path, ph.width, ph.height
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.assigned_how = 'junk'
              AND f.person_id IS NULL
              AND f.embedding IS NOT NULL
        """
        params: list[Any] = []
        if photo_id is not None:
            sql += " AND f.photo_id = ?"
            params.append(int(photo_id))
        rows = conn.execute(sql, params).fetchall()
        rows = [r for r in rows if not is_preview_path(r["path"])]
        if job_id:
            update_job(job_id, message=f"Checking {len(rows)} hidden faces against named people")
        nearby_by_photo: dict[int, set[int]] = {}
        folder_by_photo: dict[int, set[int]] = {}
        claimed_by_photo: dict[int, dict[int, list[Any]]] = {}
        face_count_cache: dict[int, int] = {}
        detected_count_cache: dict[int, int] = {}
        junk_count_cache: dict[int, int] = {}
        restored = 0
        from .config import CROP_DIR
        from .faces import looks_like_statue

        statue_gallery = load_statue_gallery(conn)
        for i, row in enumerate(rows, start=1):
            vec = bytes_to_embedding(row["embedding"])
            if vec is None:
                continue
            crop = CROP_DIR / f"{int(row['id'])}.jpg"
            photo_path = row["path"] if "path" in row.keys() else None
            row_photo_id = int(row["photo_id"]) if "photo_id" in row.keys() and row["photo_id"] else None
            grouped = (
                row_photo_id is not None
                and _detected_face_count(conn, row_photo_id, detected_count_cache) >= 4
            )
            if not grouped and _hide_as_statue(
                crop, photo_path, row_photo_id, vec, statue_gallery, row["id"]
            ):
                continue
            ranked = rank_people_nn(vec, gallery, limit=8)
            ranked = _drop_sex_mismatch(
                ranked,
                row["sex_est"] if "sex_est" in row.keys() else None,
                _probe_age_for_sex(row),
            )
            if not ranked:
                continue
            top = ranked[0]
            pid = int(top["person_id"])
            row_photo = int(row["photo_id"])
            visible = _face_count_on_photo(conn, row_photo, face_count_cache)
            if row_photo not in junk_count_cache:
                junk_count_cache[row_photo] = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS n FROM faces
                        WHERE photo_id = ? AND assigned_how = 'junk'
                        """,
                        (row_photo,),
                    ).fetchone()["n"]
                    or 0
                )
            hidden = junk_count_cache[row_photo]
            crowd = visible >= CROWD_PHOTO_FACES or hidden >= CROWD_PHOTO_FACES
            if crowd:
                continue
            if row_photo not in nearby_by_photo:
                nearby_by_photo[row_photo] = _people_on_nearby_photos(conn, row_photo)
            if row_photo not in folder_by_photo:
                folder_by_photo[row_photo] = _person_ids_in_folder(conn, row_photo)
            if row_photo not in claimed_by_photo:
                claimed_by_photo[row_photo] = _person_boxes_on_photo(conn, row_photo)
            nearby = nearby_by_photo[row_photo]
            folder_people = folder_by_photo[row_photo]
            claimed = claimed_by_photo[row_photo]
            if _already_on_photo(claimed, pid, row):
                continue
            if not _should_auto_assign(
                ranked,
                high,
                margin,
                aggressive=aggressive,
                folder_people=folder_people,
                nearby_people=nearby,
            ):
                continue
            conn.execute(
                """
                UPDATE faces
                SET quality = 'ok', assigned_how = 'auto', person_id = ?
                WHERE id = ? AND assigned_how = 'junk' AND person_id IS NULL
                """,
                (pid, int(row["id"])),
            )
            restored += 1
            named.append(int(row["id"]))
            claimed.setdefault(pid, []).append(row)
            face_count_cache[row_photo] = visible + 1
            junk_count_cache[row_photo] = max(0, hidden - 1)
            assigned.append(
                {
                    "face_id": int(row["id"]),
                    "person_id": pid,
                    "name": top.get("name") or gallery.get("names", {}).get(pid),
                    "similarity": round(float(top["similarity"]), 3),
                    "rescued": True,
                }
            )
            if i % 50 == 0:
                conn.commit()
                if job_id:
                    update_job(job_id, message=f"Restored {restored} hidden faces that match named people")
        conn.commit()
        if named:
            from . import sidecar as sidecar_mod

            sidecar_mod.write_for_face_ids(named)
        return {"restored": restored, "auto_assigned": restored, "assigned": assigned}
    finally:
        if own:
            conn.close()


def match_photo(photo_id: int, *, detect: bool = True) -> dict:
    """Match unnamed faces on one photo to the catalog. Rescan only if someone is still unnamed."""
    from . import faces as faces_mod

    new_faces = 0
    conn = connect()
    init_db(conn)
    try:
        rescue_hidden_named_faces(
            conn,
            photo_id=int(photo_id),
            high=MATCH_REMATCH_HIGH,
            margin=MATCH_REMATCH_MARGIN,
            aggressive=True,
        )
        unnamed = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE photo_id = ?
              AND person_id IS NULL
              AND IFNULL(assigned_how, '') != 'junk'
            """,
            (int(photo_id),),
        ).fetchone()["n"]
        have_faces = conn.execute(
            "SELECT COUNT(*) AS n FROM faces WHERE photo_id = ?",
            (int(photo_id),),
        ).fetchone()["n"]
        photo = conn.execute("SELECT * FROM photos WHERE id = ?", (int(photo_id),)).fetchone()
    finally:
        conn.close()
    need_detect = bool(detect) and (int(unnamed) > 0 or int(have_faces) == 0)
    if need_detect and photo and faces_mod.analyzer_status().get("ready"):
        conn = connect()
        init_db(conn)
        try:
            try:
                new_faces = int(faces_mod.scan_photo(conn, photo, faces_mod.get_analyzer()) or 0)
            except Exception:
                new_faces = 0
        finally:
            conn.close()
    conn = connect()
    init_db(conn)
    try:
        detected = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM faces WHERE photo_id = ?",
                (int(photo_id),),
            ).fetchone()["n"]
            or 0
        )
        # Vintage group shots: gold/bronze statue rules hide real kids. User
        # asked to re-identify, so put those faces back in the unnamed pool.
        if detected >= 4:
            conn.execute(
                """
                UPDATE faces
                SET quality = 'ok', assigned_how = NULL
                WHERE photo_id = ?
                  AND assigned_how = 'junk'
                  AND person_id IS NULL
                """,
                (int(photo_id),),
            )
            conn.commit()
    finally:
        conn.close()
    # Re-identify is a user click: use rematch thresholds even on a family group.
    # Class-photo "do not stamp one name on every child" is _ranked_without_claimed.
    # Library-wide Find Known Faces still uses crowd-strict thresholds.
    result = match_unknown(
        photo_id=int(photo_id),
        include_cleared=True,
        aggressive=True,
        high=MATCH_REMATCH_HIGH,
        medium=MATCH_REMATCH_MEDIUM,
        margin=MATCH_REMATCH_MARGIN,
        strict_crowd=False,
    )
    result["new_faces"] = new_faces
    return result


def _inherit_named_clusters(
    conn,
    photo_id: int | None = None,
    *,
    include_cleared: bool = False,
) -> int:
    """If a group is already one named person, give leftover faces that name."""
    extra = 0
    cluster_sql = """
        SELECT DISTINCT cluster_id FROM faces
        WHERE cluster_id IS NOT NULL AND person_id IS NOT NULL
    """
    cluster_params: list[Any] = []
    if photo_id is not None:
        cluster_sql += " AND photo_id = ?"
        cluster_params.append(int(photo_id))
    clusters = conn.execute(cluster_sql, cluster_params).fetchall()
    blocked = "('junk')" if include_cleared else "('junk', 'cleared')"
    for row in clusters:
        people_sql = """
            SELECT DISTINCT person_id FROM faces
            WHERE cluster_id = ? AND person_id IS NOT NULL
        """
        people_params: list[Any] = [row["cluster_id"]]
        if photo_id is not None:
            people_sql += " AND photo_id = ?"
            people_params.append(int(photo_id))
        people = conn.execute(people_sql, people_params).fetchall()
        if len(people) != 1:
            continue
        unnamed_n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE cluster_id = ?
              AND person_id IS NULL
              AND quality = 'ok'
              AND IFNULL(assigned_how, '') != 'junk'
            """,
            (row["cluster_id"],),
        ).fetchone()["n"]
        pid = int(people[0]["person_id"])
        leftover = int(unnamed_n or 0)
        if leftover > CLUSTER_PREVIEW_LIMIT:
            extra += _assign_matching_cluster_leftovers(
                conn,
                int(row["cluster_id"]),
                pid,
                blocked=blocked,
                photo_id=photo_id,
            )
            continue
        inherit_sql = f"""
            UPDATE faces
            SET person_id = ?, assigned_how = 'auto'
            WHERE cluster_id = ?
              AND person_id IS NULL
              AND IFNULL(assigned_how, '') NOT IN {blocked}
              AND embedding IS NOT NULL
        """
        inherit_params: list[Any] = [pid, row["cluster_id"]]
        if photo_id is not None:
            inherit_sql += " AND photo_id = ?"
            inherit_params.append(int(photo_id))
        cur = conn.execute(inherit_sql, inherit_params)
        extra += int(cur.rowcount or 0)
    return extra


def _assign_matching_cluster_leftovers(
    conn,
    cluster_id: int,
    person_id: int,
    *,
    blocked: str,
    photo_id: int | None = None,
) -> int:
    """Name leftover faces in a huge group only when they independently match that person."""
    if not _person_has_manual_seed(conn, person_id):
        return 0
    gallery = load_named_gallery(conn)
    sql = f"""
        SELECT f.id, f.embedding, f.sex_est, f.age_est, f.photo_id
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        WHERE f.cluster_id = ?
          AND f.person_id IS NULL
          AND f.quality = 'ok'
          AND IFNULL(f.assigned_how, '') NOT IN {blocked}
          AND f.embedding IS NOT NULL
    """
    params: list[Any] = [int(cluster_id)]
    if photo_id is not None:
        sql += " AND f.photo_id = ?"
        params.append(int(photo_id))
    rows = conn.execute(sql, params).fetchall()
    n = 0
    pid = int(person_id)
    for row in rows:
        vec = bytes_to_embedding(row["embedding"])
        if vec is None:
            continue
        ranked = rank_people_nn(vec, gallery, limit=3, exclude_face_ids={int(row["id"])})
        ranked = _drop_sex_mismatch(ranked, row["sex_est"], row["age_est"])
        if not ranked or int(ranked[0]["person_id"]) != pid:
            continue
        if not _should_auto_assign(
            ranked,
            MATCH_REMATCH_HIGH,
            MATCH_REMATCH_MARGIN,
            aggressive=True,
        ):
            continue
        conn.execute(
            "UPDATE faces SET person_id = ?, assigned_how = 'auto' WHERE id = ?",
            (pid, int(row["id"])),
        )
        n += 1
    return n


def inherit_named_cluster_leftovers(cluster_id: int | None = None) -> int:
    """Attach leftover faces in already-named groups that independently match that person."""
    conn = connect()
    init_db(conn)
    try:
        photo_id = None
        if cluster_id is not None:
            pid = _cluster_lone_person_id(conn, int(cluster_id))
            if not pid:
                return 0
            n = _assign_matching_cluster_leftovers(
                conn,
                int(cluster_id),
                pid,
                blocked="('junk', 'cleared')",
            )
            if n:
                conn.commit()
                _invalidate_galleries()
            return n
        n = _inherit_named_clusters(conn)
        if n:
            conn.commit()
            _invalidate_galleries()
        return n
    finally:
        conn.close()


def _cluster_lone_person_id(conn, cluster_id: int) -> int | None:
    rows = conn.execute(
        "SELECT DISTINCT person_id FROM faces WHERE cluster_id = ? AND person_id IS NOT NULL",
        (int(cluster_id),),
    ).fetchall()
    if len(rows) != 1:
        return None
    return int(rows[0]["person_id"])


def suppress_like_junk(threshold: float = 0.46, junk_face_ids: list[int] | None = None) -> int:
    """Mark unnamed faces that look like previously rejected statues/objects."""
    conn = connect()
    init_db(conn)
    try:
        ids = [int(fid) for fid in (junk_face_ids or []) if fid]
        if ids:
            marks = ",".join("?" * len(ids))
            junk = conn.execute(
                f"""
                SELECT embedding FROM faces
                WHERE id IN ({marks}) AND assigned_how = 'junk' AND embedding IS NOT NULL
                """,
                ids,
            ).fetchall()
        else:
            junk = conn.execute(
                """
                SELECT embedding FROM faces
                WHERE assigned_how = 'junk' AND embedding IS NOT NULL
                """
            ).fetchall()
        unknown = conn.execute(
            """
            SELECT id, embedding FROM faces
            WHERE person_id IS NULL AND quality = 'ok' AND embedding IS NOT NULL
            """
        ).fetchall()
        junk_vecs = []
        for row in junk:
            vec = bytes_to_embedding(row["embedding"])
            if vec is not None:
                junk_vecs.append(l2_normalize(vec))
        if not junk_vecs or not unknown:
            return 0
        unk_ids: list[int] = []
        unk_vecs: list[np.ndarray] = []
        for row in unknown:
            vec = bytes_to_embedding(row["embedding"])
            if vec is None:
                continue
            unk_ids.append(int(row["id"]))
            unk_vecs.append(l2_normalize(vec))
        if not unk_vecs:
            return 0
        junk_mat = np.stack(junk_vecs)
        unk_mat = np.stack(unk_vecs)
        max_junk = (unk_mat @ junk_mat.T).max(axis=1)
        gallery = load_named_gallery(conn)
        has_gallery = gallery.get("matrix") is not None
        marked_ids: list[int] = []
        marked = 0
        from .config import CROP_DIR
        from .faces import looks_like_statue

        for i, face_id in enumerate(unk_ids):
            sim = float(max_junk[i])
            if sim < threshold:
                continue
            vec = unk_vecs[i]
            named = rank_people_nn(vec, gallery, limit=1) if has_gallery else []
            named_sim = float(named[0]["similarity"]) if named else -1.0
            # A real catalogued person must not be hidden because a lookalike
            # was marked not-a-person (photo-in-photo, statue, etc.).
            if named_sim >= MATCH_MEDIUM and named_sim >= sim - 0.08:
                continue
            if named_sim >= MATCH_HIGH:
                continue
            crop = CROP_DIR / f"{face_id}.jpg"
            # A dinner-table crop can match junked embeddings at 0.66 and
            # still be a person. Colour/shape of this crop decides.
            if crop.exists() and not looks_like_statue(crop):
                continue
            conn.execute(
                """
                UPDATE faces
                SET quality = 'unidentifiable', assigned_how = 'junk', cluster_id = NULL
                WHERE id = ?
                """,
                (face_id,),
            )
            marked += 1
            marked_ids.append(face_id)
        conn.commit()
    finally:
        conn.close()
    if marked_ids:
        from . import sidecar as sidecar_mod

        sidecar_mod.write_for_face_ids(marked_ids)
    return marked
