import numpy as np

from photosort.cluster import FaceVec, cluster_vectors, try_run_clustering, _cluster_lock
from photosort.match import matches_known_statue, rank_people, rank_people_nn
from photosort.people import age_band
from photosort.stats import folder_stats
from photosort.util import cosine, l2_normalize


def test_cluster_groups_similar_and_splits_different():
    rng = np.random.default_rng(0)
    base_a = l2_normalize(rng.normal(size=32))
    base_b = l2_normalize(rng.normal(size=32))
    faces = [
        FaceVec(1, l2_normalize(base_a + rng.normal(scale=0.02, size=32))),
        FaceVec(2, l2_normalize(base_a + rng.normal(scale=0.02, size=32))),
        FaceVec(3, l2_normalize(base_b + rng.normal(scale=0.02, size=32))),
        FaceVec(4, l2_normalize(base_b + rng.normal(scale=0.02, size=32))),
    ]
    groups = {frozenset(g) for g in cluster_vectors(faces, threshold=0.8)}
    assert frozenset([1, 2]) in groups
    assert frozenset([3, 4]) in groups


def test_rank_people_picks_closest_centroid():
    a = l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    b = l2_normalize(np.array([0.0, 1.0, 0.0], dtype=np.float32))
    query = l2_normalize(np.array([0.9, 0.1, 0.0], dtype=np.float32))
    ranked = rank_people(query, {10: {"adult": a}, 20: {"adult": b}})
    assert ranked[0]["person_id"] == 10
    assert ranked[0]["similarity"] > ranked[1]["similarity"]


def test_should_auto_assign_burst_still_needs_a_real_match():
    from photosort.match import _should_auto_assign

    ranked = [
        {"person_id": 3, "similarity": 0.25, "mean3": 0.22, "votes": 20, "name": "Sam"},
        {"person_id": 9, "similarity": 0.10, "mean3": 0.08, "votes": 4, "name": "Clara"},
    ]
    assert _should_auto_assign(ranked, 0.55, 0.10, nearby_people={3}) is False
    ranked[0]["similarity"] = 0.34
    ranked[0]["mean3"] = 0.34
    assert _should_auto_assign(ranked, 0.55, 0.10, nearby_people={3}) is True


def test_rank_people_nn_ignores_the_query_face_itself():
    """A wrong current name at similarity 1.0 must not hide the real person."""
    jon = l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
    jon_other = l2_normalize(np.array([0.98, 0.199], dtype=np.float32))
    alex = l2_normalize(np.array([0.0, 1.0], dtype=np.float32))
    gallery = {
        "matrix": np.stack([jon, jon_other, alex]).astype(np.float32),
        "person_ids": np.array([96, 96, 61], dtype=np.int64),
        "face_ids": np.array([1533, 1530, 1886], dtype=np.int64),
        "names": {96: "Jordan Cole", 61: "Alex Cole"},
    }
    ranked = rank_people_nn(jon, gallery, exclude_face_ids={1533})
    assert ranked[0]["person_id"] == 96
    assert ranked[0]["similarity"] < 0.999
    with_self = rank_people_nn(jon, gallery)
    assert with_self[0]["similarity"] > 0.999


def test_matches_known_statue_is_nearer_to_hidden_heads_than_people():
    """A bronze head matches other hidden statues, not a real person at ~0.05."""
    statue = l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    other = l2_normalize(np.array([0.97, 0.24, 0.0], dtype=np.float32))
    person = l2_normalize(np.array([0.0, 1.0, 0.0], dtype=np.float32))
    gallery = {"matrix": np.stack([statue, other]).astype(np.float32)}
    probe = l2_normalize(np.array([0.98, 0.2, 0.0], dtype=np.float32))
    assert matches_known_statue(probe, gallery) is True
    assert matches_known_statue(person, gallery) is False


def test_load_statue_gallery_survives_invalidate(tmp_path, monkeypatch):
    """Cache stamp must stay callable after a sweep invalidates the gallery."""
    from photosort import config, db, match as match_mod
    from photosort.db import connect, init_db
    from photosort.util import embedding_to_bytes, now_iso

    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/statue.jpg", "s", 100, 100, now_iso()),
    )
    vec = l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'unidentifiable',?,'junk',?)""",
        (embedding_to_bytes(vec), now_iso()),
    )
    conn.commit()
    first = match_mod.load_statue_gallery(conn)
    assert first["matrix"] is not None
    match_mod._invalidate_galleries()
    second = match_mod.load_statue_gallery(conn)
    assert second["matrix"] is not None
    third = match_mod.load_statue_gallery(conn)
    assert third["matrix"] is not None
    conn.close()


def test_try_run_clustering_skips_when_lock_is_held():
    assert _cluster_lock.acquire(blocking=False)
    try:
        out = try_run_clustering()
        assert out.get("skipped") is True
    finally:
        _cluster_lock.release()


def test_age_bands():
    assert age_band(3) == "child"
    assert age_band(16) == "teen"
    assert age_band(40) == "adult"
    assert age_band(75) == "elder"


def test_cosine_identical_is_one():
    v = l2_normalize(np.array([0.3, 0.4, 0.5], dtype=np.float32))
    assert cosine(v, v) == 1.0


def test_folder_stats_empty(tmp_path, monkeypatch):
    from photosort import config, db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    stats = folder_stats()
    assert stats["photos"] == 0
    assert stats["people"] == 0
    assert stats["people_named"] == 0
    assert stats["people_unknown"] == 0
    assert stats["identification_rate"] == 0.0
