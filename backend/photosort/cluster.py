from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

_cluster_lock = threading.Lock()

from .config import CLUSTER_SIM
from .db import connect, init_db
from .jobs import update_job
from .originals import is_preview_path
from .util import bytes_to_embedding, l2_normalize, now_iso


@dataclass
class FaceVec:
    id: int
    vec: np.ndarray


class UnionFind:
    def __init__(self, items: list[int]):
        self.parent = {i: i for i in items}
        self.rank = {i: 0 for i in items}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def cluster_vectors(faces: list[FaceVec], threshold: float = CLUSTER_SIM) -> list[list[int]]:
    """Conservative connected-component clustering on cosine similarity."""
    if not faces:
        return []
    ids = [f.id for f in faces]
    mat = np.stack([l2_normalize(f.vec) for f in faces])
    sims = mat @ mat.T
    uf = UnionFind(ids)
    ii, jj = np.nonzero(np.triu(sims >= threshold, k=1))
    for i, j in zip(ii.tolist(), jj.tolist()):
        uf.union(ids[i], ids[j])
    groups = uf.groups()
    return [sorted(members) for members in groups.values()]


def run_clustering(
    job_id: int | None = None,
    threshold: float = CLUSTER_SIM,
    *,
    sweep: bool = True,
) -> dict:
    if sweep:
        from .faces import sweep_statues

        sweep_statues()
    with _cluster_lock:
        return _run_clustering(job_id, threshold, only_unclustered=False)


def try_run_clustering(
    job_id: int | None = None,
    threshold: float = CLUSTER_SIM,
    *,
    only_unclustered: bool = False,
) -> dict:
    """Regroup faces unless Find Known Faces already holds the cluster lock."""
    if not _cluster_lock.acquire(blocking=False):
        return {"skipped": True}
    try:
        if not only_unclustered:
            from .faces import sweep_statues

            sweep_statues()
        return _run_clustering(job_id, threshold, only_unclustered=only_unclustered)
    finally:
        _cluster_lock.release()


def _run_clustering(job_id: int | None, threshold: float, *, only_unclustered: bool = False) -> dict:
    conn = connect()
    init_db(conn)
    try:
        if job_id:
            update_job(job_id, message="Loading unnamed faces")
        loose = "AND f.cluster_id IS NULL" if only_unclustered else ""
        rows = conn.execute(
            f"""
            SELECT f.id, f.embedding, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND f.embedding IS NOT NULL
              {loose}
            ORDER BY f.id
            """
        ).fetchall()
        faces: list[FaceVec] = []
        for row in rows:
            if is_preview_path(row["path"]):
                continue
            vec = bytes_to_embedding(row["embedding"])
            if vec is None or vec.size == 0:
                continue
            faces.append(FaceVec(id=row["id"], vec=vec))

        if job_id:
            update_job(job_id, total=3, progress=1, message=f"Clustering {len(faces)} faces")
        groups = cluster_vectors(faces, threshold=threshold)

        if not only_unclustered:
            conn.execute("UPDATE faces SET cluster_id = NULL WHERE person_id IS NULL")
            conn.execute("DELETE FROM clusters WHERE status = 'unknown'")

        created = 0
        for members in groups:
            cur = conn.execute(
                "INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)",
                (now_iso(),),
            )
            cid = int(cur.lastrowid)
            conn.executemany(
                "UPDATE faces SET cluster_id = ? WHERE id = ? AND person_id IS NULL",
                [(cid, fid) for fid in members],
            )
            created += 1

        conn.commit()
        if job_id:
            update_job(job_id, progress=3, message=f"Created {created} unknown clusters")
        return {"faces": len(faces), "clusters": created}
    finally:
        conn.close()
