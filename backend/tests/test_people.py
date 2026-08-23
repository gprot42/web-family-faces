from photosort import config, db
from photosort.db import connect, init_db
from photosort.people import (
    assign_cluster,
    assign_cluster_report,
    assign_faces,
    auto_face_count,
    confirm_faces,
    advance_person_cover,
    create_person,
    create_unknown_person,
    display_faces,
    find_person_by_name,
    get_person,
    is_unknown_name,
    junk_cluster,
    junk_faces,
    list_auto_faces,
    list_people,
    merge_people,
    reset_matching,
    reset_names,
    revoke_cluster_names,
    person_matches_query,
    search_catalog,
    search_photos,
    split_person_cluster,
)
from photosort.match import match_photo, match_unknown, suppress_like_junk
from photosort.suggest import merge_suggestions
from photosort.util import embedding_to_bytes, l2_normalize, now_iso
import numpy as np


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = connect()
    init_db(conn)
    return conn


def test_merge_people_moves_faces(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/a.jpg", "a", "1990-01-01T00:00:00", 100, 100, now_iso()),
    )
    conn.commit()
    conn.close()
    child = create_person("Nora child")
    adult = create_person("Nora")
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?,?)""",
        (emb, child["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    merged = merge_people(child["id"], adult["id"])
    assert merged["id"] == adult["id"]
    assert merged["face_count"] == 1


def test_unnamed_counts_ignore_preview_copies(tmp_path, monkeypatch):
    from photosort.people import visible_unnamed_summary
    from photosort.stats import folder_stats

    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1024 x 768/a.jpg", "p", 10, 10, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,1,1,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (2,0,0,1,1,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    summary = visible_unnamed_summary()
    assert summary["faces"] == 1
    assert summary["clusters"] == 1
    stats = folder_stats()
    assert stats["faces_unknown"] == 1
    assert stats["unknown_clusters"] == 1
    assert stats["people_named"] == 0
    assert stats["people_unknown"] == 0


def test_unknown_name_people_count_as_not_yet_named(tmp_path, monkeypatch):
    from photosort.stats import folder_stats

    _setup(tmp_path, monkeypatch)
    create_person("Sam")
    create_unknown_person()
    create_unknown_person()
    stats = folder_stats()
    assert stats["people"] == 3
    assert stats["people_named"] == 1
    assert stats["people_unknown"] == 2


def test_name_in_one_folder_auto_matches_other_folder(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1996 - Picnic/b.jpg", "b", 100, 100, now_iso()),
    )
    vec = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?)""",
        (vec, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,20,20,0.9,'ok',?,?)""",
        (vec, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Sam")
    assign_faces([1], person["id"], "manual", rematch=False, sync_sidecars=False)
    match_photo(2)
    conn = connect()
    other = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert other["person_id"] == person["id"]
    assert other["assigned_how"] == "auto"


def test_match_does_not_treat_auto_guesses_as_identity(tmp_path, monkeypatch):
    """One auto-named statue must not pull the next statue onto that person."""
    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    statue = embedding_to_bytes(l2_normalize(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/c.jpg", "c", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?, 'manual', ?)""",
        (sam, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (2,0,0,20,20,0.9,'ok',?,?, 'auto', ?)""",
        (statue, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (3,0,0,20,20,0.9,'ok',?,?)""",
        (statue, now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 0
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 3").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_cluster_lookalikes_do_not_define_identity(tmp_path, monkeypatch):
    """A mixed cluster stamped as Sam must not name the next lookalike as Sam."""
    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    cousin = embedding_to_bytes(l2_normalize(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/c.jpg", "c", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?, 'manual', ?)""",
        (sam, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (2,0,0,20,20,0.9,'ok',?,?, 'cluster', ?)""",
        (cousin, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (3,0,0,20,20,0.9,'ok',?,?)""",
        (cousin, now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 0
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 3").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_suggestions_omit_weak_catalog_hits(tmp_path, monkeypatch):
    from photosort.match import suggestions_for_face

    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    other = embedding_to_bytes(l2_normalize(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?, 'manual', ?)""",
        (sam, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,40,0,60,20,0.9,'ok',?,?)""",
        (other, now_iso()),
    )
    conn.commit()
    conn.close()
    assert suggestions_for_face(2) == []


def test_person_matches_query_alan_allan():
    person = {"name": "Allan James Cole", "nickname": ""}
    assert person_matches_query(person, "alan")
    assert person_matches_query(person, "Alan Cole")
    assert person_matches_query(person, "allan")
    assert person_matches_query(person, "james")
    assert not person_matches_query(person, "alex")
    assert not person_matches_query(person, "zzz")
    nick = {"name": "Robert Smith", "nickname": "Bob"}
    assert person_matches_query(nick, "bob")
    assert not person_matches_query(nick, "alan")


def test_search_catalog_finds_person_and_photo(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1024 x 768/a.jpg", "p", 100, 100, now_iso()),
    )
    conn.commit()
    conn.close()
    sam = create_person("Sam")
    michiko = create_person("Empress Michiko")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (sam["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (michiko["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    found = search_catalog("sam")
    names = {p["name"] for p in found["people"]}
    assert names == {"Sam"}
    assert [p["id"] for p in found["photos"]] == [1]
    empty = search_catalog("zzz")
    assert empty["people"] == []
    assert empty["photos"] == []
    by_file = search_photos("Harbor")
    assert [p["id"] for p in by_file["photos"]] == [1]
    by_name = search_photos("DSCN")
    assert by_name["photos"] == []
    miss = search_photos("zzz")
    assert miss["photos"] == []


def test_review_auto_confirm_and_reject(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    assert auto_face_count() == 2
    groups = list_auto_faces()
    assert len(groups) == 1
    assert groups[0]["person"]["name"] == "Sam"
    assert len(groups[0]["faces"]) == 2
    assert confirm_faces(face_ids=[1]) == 1
    assert auto_face_count() == 1
    conn = connect()
    kept = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert kept["person_id"] == person["id"]
    assert kept["assigned_how"] == "manual"
    from photosort.people import unassign_faces

    unassign_faces([2])
    assert auto_face_count() == 0


def test_list_auto_faces_caps_per_person(tmp_path, monkeypatch):
    from photosort.util import now_iso

    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    conn = connect()
    for i in range(5):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/{i}.jpg", str(i), 10, 10, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,'auto',?)""",
            (i + 1, person["id"], now_iso()),
        )
    conn.commit()
    conn.close()
    first = list_auto_faces(limit=2)
    assert first[0]["face_count"] == 5
    assert len(first[0]["faces"]) == 2
    more = list_auto_faces(person_id=person["id"], offset=2, limit=2)
    assert len(more[0]["faces"]) == 2
    assert {f["id"] for f in first[0]["faces"]}.isdisjoint({f["id"] for f in more[0]["faces"]})
    rest = list_auto_faces(person_id=person["id"], offset=4, limit=2)
    assert len(rest[0]["faces"]) == 1
    after = list_auto_faces(person_id=person["id"], after_id=first[0]["faces"][-1]["id"], limit=10)
    assert [f["id"] for f in after[0]["faces"]] == [f["id"] for f in more[0]["faces"]] + [
        f["id"] for f in rest[0]["faces"]
    ]
    assert after[0]["face_count"] == 5


def test_list_auto_faces_one_card_per_photo(tmp_path, monkeypatch):
    from photosort.util import now_iso

    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    other = create_person("Alex")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/group.jpg", "same-bytes", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/copy/group.jpg", "same-bytes", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/other.jpg", "other", 100, 100, now_iso()),
    )
    now = now_iso()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,20,20,30,30,0.4,'ok',?,'auto',?)""",
        (person["id"], now),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.8,'ok',?,'auto',?)""",
        (person["id"], now),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (other["id"], now),
    )
    conn.commit()
    conn.close()
    assert auto_face_count() == 2
    groups = {g["person"]["name"]: g for g in list_auto_faces()}
    sam = groups["Sam"]
    assert sam["face_count"] == 1
    assert len(sam["faces"]) == 1
    assert sam["faces"][0]["id"] == 1
    assert set(sam["faces"][0]["face_ids"]) == {1, 2, 3}
    assert groups["Alex"]["face_count"] == 1
    assert confirm_faces(face_ids=[1]) == 3
    assert auto_face_count() == 1
    leftover = list_auto_faces()
    assert len(leftover) == 1
    assert leftover[0]["person"]["name"] == "Alex"


def test_list_auto_faces_after_id_skips_confirmed(tmp_path, monkeypatch):
    from photosort.util import now_iso

    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    conn = connect()
    for i in range(6):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/{i}.jpg", str(i), 10, 10, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,'auto',?)""",
            (i + 1, person["id"], now_iso()),
        )
    conn.commit()
    conn.close()
    first = list_auto_faces(person_id=person["id"], limit=3)
    shown = first[0]["faces"]
    confirm_faces(face_ids=[shown[0]["id"], shown[2]["id"]])
    leftover_id = shown[1]["id"]
    more = list_auto_faces(person_id=person["id"], after_id=leftover_id, limit=10)
    assert more[0]["face_count"] == 4
    assert [f["id"] for f in more[0]["faces"]] == [4, 5, 6]


def test_list_auto_faces_skips_preview_copies(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    person = create_person("Nora")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1024 x 768/a.jpg", "p", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    groups = list_auto_faces()
    assert auto_face_count() == 1
    assert len(groups) == 1
    assert groups[0]["person"]["name"] == "Nora"
    assert [f["photo_id"] for f in groups[0]["faces"]] == [1]


def test_unassign_face_clears_same_photo_copies(tmp_path, monkeypatch):
    from photosort.people import unassign_face_and_copies

    _setup(tmp_path, monkeypatch)
    person = create_person("Arthur")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "same", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/copy/a.jpg", "same", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,1,1,11,11,0.8,'ok',?,'cluster',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    assert unassign_face_and_copies(1) == 3
    conn = connect()
    left = conn.execute("SELECT COUNT(*) AS n FROM faces WHERE person_id = ?", (person["id"],)).fetchone()["n"]
    cleared = conn.execute("SELECT COUNT(*) AS n FROM faces WHERE assigned_how = 'cleared'").fetchone()["n"]
    conn.close()
    assert left == 0
    assert cleared == 3


def test_unassign_photo_names_clears_every_named_face(tmp_path, monkeypatch):
    from photosort.people import unassign_photo_names

    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    sam = create_person("Sam")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/class.jpg", "c", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,'manual',?)""",
        (sam["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,40,0,50,10,0.9,'ok',NULL,'junk',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    assert unassign_photo_names(1, sync_sidecars=False) >= 2
    conn = connect()
    rows = list(conn.execute("SELECT id, person_id, assigned_how FROM faces ORDER BY id"))
    conn.close()
    assert rows[0]["person_id"] is None and rows[0]["assigned_how"] == "cleared"
    assert rows[1]["person_id"] is None and rows[1]["assigned_how"] == "cleared"
    assert rows[2]["assigned_how"] == "junk"


def test_junk_unnamed_on_photo_hides_unnamed_keeps_named(tmp_path, monkeypatch):
    from photosort.people import junk_unnamed_on_photo

    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/class.jpg", "c", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/other.jpg", "o", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,40,0,50,10,0.8,'ok',?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, assigned_how, created_at)
           VALUES (1,60,0,70,10,0.9,'unidentifiable','junk',?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    assert junk_unnamed_on_photo(1, sync_sidecars=False) == 2
    conn = connect()
    rows = list(conn.execute("SELECT id, photo_id, person_id, assigned_how FROM faces ORDER BY id"))
    conn.close()
    assert rows[0]["person_id"] == ada["id"] and rows[0]["assigned_how"] == "manual"
    assert rows[1]["assigned_how"] == "junk" and rows[1]["person_id"] is None
    assert rows[2]["assigned_how"] == "junk" and rows[2]["person_id"] is None
    assert rows[3]["assigned_how"] == "junk"
    assert rows[4]["photo_id"] == 2 and rows[4]["assigned_how"] is None


def test_junk_unnamed_photo_http_does_not_suppress_library(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort import match as match_mod
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    ada = create_person("Ada")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/group.jpg", "g", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()

    def boom(*_a, **_k):
        raise AssertionError("bulk remove unnamed must not suppress similar faces library-wide")

    monkeypatch.setattr(match_mod, "suppress_like_junk", boom)
    client = TestClient(app)
    missing = client.post("/api/photos/99/junk-unnamed")
    assert missing.status_code == 404
    response = client.post("/api/photos/1/junk-unnamed")
    assert response.status_code == 200
    assert response.json()["junked"] == 1
    conn = connect()
    rows = list(conn.execute("SELECT id, person_id, assigned_how FROM faces ORDER BY id"))
    conn.close()
    assert rows[0]["person_id"] == ada["id"] and rows[0]["assigned_how"] == "manual"
    assert rows[1]["assigned_how"] == "junk" and rows[1]["person_id"] is None


def test_unassign_is_not_rematched(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.commit()
    conn.close()
    person = create_person("Clara")
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,1,?, 'cluster', ?)""",
        (emb, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, person_id, assigned_how, created_at)
           VALUES (1,10,0,20,10,0.9,'ok',?,1,?, 'cluster', ?)""",
        (emb, person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    from photosort.people import unassign_faces

    unassign_faces([2])
    match_unknown()
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how, cluster_id FROM faces WHERE id = 2").fetchone()
    kept = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert kept["person_id"] == person["id"]
    assert row["person_id"] is None
    assert row["assigned_how"] == "cleared"
    assert row["cluster_id"] is None


def test_assign_cluster_names_all_members(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    for _ in range(3):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (1,0,0,10,10,0.9,'ok',?,1,?)""",
            (emb, now_iso()),
        )
    conn.commit()
    conn.close()
    person = create_person("James")
    n = assign_cluster(1, person["id"])
    assert n == 3


def test_name_cluster_http_does_not_stamp_mega_cluster(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    for i in range(60):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/p{i}.jpg", f"h{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,1,?)""",
            (i + 1, emb, now_iso()),
        )
    conn.commit()
    conn.close()
    client = TestClient(app)
    result = client.post("/api/clusters/1/name", json={"name": "Dana Price", "face_ids": []}).json()
    assert result["assigned"] == 24
    conn = connect()
    named = conn.execute("SELECT COUNT(*) AS n FROM faces WHERE person_id IS NOT NULL").fetchone()["n"]
    conn.close()
    assert named == 24


def test_name_cluster_saves_new_person_when_lookalike_exists(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    existing = create_person("Jonathan Reid Cole")
    same = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named/a.jpg", "n", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (same, existing["id"], now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    for i in range(3):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/p{i}.jpg", f"h{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,1,?)""",
            (i + 2, same, now_iso()),
        )
    conn.commit()
    conn.close()
    client = TestClient(app)
    result = client.post("/api/clusters/1/name", json={"name": "Jonathan Cole", "face_ids": [2, 3, 4]}).json()
    assert result["assigned"] == 3
    assert result["person"]["name"] == "Jonathan Cole"
    assert result["person"]["id"] != existing["id"]
    conn = connect()
    named = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE person_id = ?",
        (result["person"]["id"],),
    ).fetchone()["n"]
    conn.close()
    assert named == 3


def test_name_cluster_ignores_wrong_sex_guess(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, sex_est, age_est, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,1,'F',40,?)""",
        (emb, now_iso()),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    result = client.post("/api/clusters/1/name", json={"name": "Jonathan Cole", "face_ids": [1]}).json()
    assert result["assigned"] == 1
    assert result["person"]["name"] == "Jonathan Cole"


def test_jonathan_is_a_male_name():
    from photosort.people import _name_sex

    assert _name_sex("Jonathan") == "M"
    assert _name_sex("Jonathan Cole") == "M"
    assert _name_sex("Jonathan Reid Cole") == "M"


def test_name_sex_check_off_allows_mismatch_auto_name(tmp_path, monkeypatch):
    from photosort import settings as settings_mod

    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    jonathan = create_person("Jonathan Cole")
    named = _unit(1.0, 0.0)
    probe = _unit(0.92, (1.0 - 0.92**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/a.jpg", "a", "1994-01-01T00:00:00", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/b.jpg", "b", "1994-01-01T00:01:00", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'M',?, 'manual', ?)""",
        (embedding_to_bytes(named), jonathan["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, age_est, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,'F',40,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    blocked = match_photo(2)
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert blocked["auto_assigned"] == 0
    assert row["person_id"] is None
    settings_mod.save_name_sex_check(False)
    allowed = match_photo(2)
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert allowed["auto_assigned"] == 1
    assert row["person_id"] == jonathan["id"]


def test_match_names_jonathan_even_if_detector_guessed_female(tmp_path, monkeypatch):
    """A man's given name plus a real gallery beats InsightFace saying F."""
    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    jonathan = create_person("Jonathan Cole")
    named = _unit(1.0, 0.0)
    probe = _unit(0.92, (1.0 - 0.92**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/a.jpg", "a", "1994-01-01T00:00:00", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/b.jpg", "b", "1994-01-01T00:01:00", 200, 200, now_iso()),
    )
    for i in range(20):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,'M',?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), jonathan["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, age_est, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,'F',40,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == jonathan["id"]


def test_match_unknown_does_not_stamp_mega_cluster_leftovers(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    for i in range(60):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/p{i}.jpg", f"h{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,1,?)""",
            (i + 1, emb, now_iso()),
        )
    conn.commit()
    conn.close()
    person = create_person("Dana Price")
    n = assign_cluster(1, person["id"], face_ids=list(range(1, 25)), sync_sidecars=False)
    assert n == 24
    match_unknown()
    conn = connect()
    named = conn.execute("SELECT COUNT(*) AS n FROM faces WHERE person_id IS NOT NULL").fetchone()["n"]
    conn.close()
    assert named == 24


def test_match_unknown_names_mega_leftover_that_matches_a_manual_person(tmp_path, monkeypatch):
    """A leftover in a huge unnamed cluster still gets a name if the catalog already has that person."""
    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    person = create_person("Sam")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named/a.jpg", "n", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (sam, person["id"], now_iso()),
    )
    for i in range(30):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/p{i}.jpg", f"h{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,1,?)""",
            (i + 2, sam, now_iso()),
        )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] >= 1
    conn = connect()
    sofa = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert sofa["person_id"] == person["id"]


def test_list_clusters_includes_named_group_with_leftovers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    person = create_person("Dana Price")
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('named', ?)", (now_iso(),))
    for i in range(5):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (str(tmp_path / f"{i}.jpg"), f"h{i}", 100, 100, now_iso()),
        )
        person_id = person["id"] if i == 0 else None
        how = "manual" if i == 0 else None
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id,
                                  person_id, assigned_how, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',1,?,?,?)""",
            (i + 1, person_id, how, now_iso()),
        )
    conn.commit()
    conn.close()
    items = TestClient(app).get("/api/clusters").json()["items"]
    assert items
    assert items[0]["face_count"] == 4


def test_inherit_mega_leftover_names_faces_that_match_manual_person(tmp_path, monkeypatch):
    from photosort.match import inherit_named_cluster_leftovers

    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    person = create_person("Sam")
    other = create_person("Bea")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    bea = embedding_to_bytes(l2_normalize(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('named', ?)", (now_iso(),))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named/a.jpg", "n", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', 1, ?)""",
        (sam, person["id"], now_iso()),
    )
    for i in range(30):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/p{i}.jpg", f"h{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',?,1,?)""",
            (i + 2, sam if i < 28 else bea, now_iso()),
        )
    conn.commit()
    conn.close()
    n = inherit_named_cluster_leftovers(1)
    assert n >= 20
    conn = connect()
    named_sam = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE person_id = ?", (person["id"],)
    ).fetchone()["n"]
    named_bea = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE person_id = ?", (other["id"],)
    ).fetchone()["n"]
    leftover = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE cluster_id = 1 AND person_id IS NULL"
    ).fetchone()["n"]
    conn.close()
    assert named_sam >= 21
    assert named_bea == 0
    assert leftover >= 1


def test_revoke_cluster_names_keeps_manual(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (2,20,0,30,10,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Dana Price")
    assign_faces([1], person["id"], how="manual", rematch=False, sync_sidecars=False)
    assign_cluster(1, person["id"], face_ids=[2], sync_sidecars=False)
    n = revoke_cluster_names(person["id"], sync_sidecars=False)
    assert n == 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] == person["id"]
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] is None


def test_assign_cluster_by_face_ids_after_cluster_rebuild(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.execute("UPDATE faces SET cluster_id = NULL")
    conn.execute("DELETE FROM clusters")
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute("UPDATE faces SET cluster_id = 2 WHERE id = 1")
    conn.commit()
    conn.close()
    person = create_person("Lila Cole")
    n = assign_cluster(1, person["id"], face_ids=[1])
    assert n == 1
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]
    listed = list_people()
    assert any(p["name"] == "Lila Cole" and p["face_count"] == 1 for p in listed)
    import photosort.people as people_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("lite lists should not scan embeddings for covers")

    monkeypatch.setattr(people_mod, "_sex_centroids", boom)
    lite = list_people(lite=True)
    assert any(p["name"] == "Lila Cole" and p["cover_face_id"] == 1 for p in lite)


def test_list_people_names_skips_cover_scan(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.close()
    named = create_person("Lila Cole")
    create_person("Adam Cole")
    import photosort.people as people_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("names lists should not scan faces for covers")

    monkeypatch.setattr(people_mod, "_list_people_covers", boom)
    monkeypatch.setattr(people_mod, "_list_people_covers_lite", boom)
    monkeypatch.setattr(people_mod, "_sex_centroids", boom)
    names = list_people(names=True)
    found = {p["name"]: p for p in names}
    assert "Lila Cole" in found
    assert "Adam Cole" in found
    assert found["Lila Cole"]["id"] == named["id"]
    assert found["Lila Cole"].get("cover_face_id") is None


def test_list_people_counts_named_unidentifiable_photos(tmp_path, monkeypatch):
    """A blurry face you named still counts as a photo of that person."""
    conn = _setup(tmp_path, monkeypatch)
    person = create_person("Leo")
    for i, name in enumerate(("a.jpg", "b.jpg", "c.jpg"), start=1):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (f"/album/{name}", str(i), 100, 100, now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,2,0,0,10,10,0.4,'unidentifiable',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (3,3,0,0,10,10,0.2,'unidentifiable',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    listed = {p["name"]: p for p in list_people()}
    lite = {p["name"]: p for p in list_people(lite=True)}
    detail = get_person(person["id"])
    assert listed["Leo"]["face_count"] == 3
    assert lite["Leo"]["face_count"] == 3
    assert detail["face_count"] == 3
    assert len(detail["shots"]) == 3


def test_find_person_by_unique_first_name(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.close()
    lila = create_person("Lila Cole")
    create_person("Adam Cole")
    assert find_person_by_name("Lila")["id"] == lila["id"]
    create_person("Lila Cruz")
    assert find_person_by_name("Lila") is None
    assert find_person_by_name("Lila Cole")["id"] == lila["id"]


def test_list_people_cover_prefers_clear_crop(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/dark.jpg", "d", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/clear.jpg", "c", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,40,40,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,80,80,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (8, 8, 8)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (150, 140, 130)).save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_skips_occluded_face(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    other = create_person("In Front")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/blocked.jpg", "b", 400, 400, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/clear.jpg", "c", 400, 400, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,40,40,160,160,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,1,80,80,200,200,0.9,'ok',?,?)""",
        (other["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (3,2,20,20,100,100,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    for i in (1, 2, 3):
        Image.new("RGB", (64, 64), (150, 140, 130)).save(crops / f"{i}.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 3
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 3


def test_list_people_cover_skips_face_blocked_in_padded_crop(tmp_path, monkeypatch):
    """A head in the lower cover crop is in the way even if detector boxes barely overlap."""
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    other = create_person("In Front")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/blocked.jpg", "b", 400, 400, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/clear.jpg", "c", 400, 400, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,140,70,220,200,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,1,205,175,290,285,0.9,'ok',?,?)""",
        (other["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (3,2,40,40,140,140,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    for i in (1, 2, 3):
        Image.new("RGB", (64, 64), (150, 140, 130)).save(crops / f"{i}.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 3
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 3


def test_list_people_cover_prefers_unobscured_over_color(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    other = create_person("In Front")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/color-blocked.jpg", "b", 400, 400, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/bw-clear.jpg", "c", 400, 400, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,40,40,160,160,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,1,80,80,200,200,0.9,'ok',?,?)""",
        (other["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (3,2,20,20,100,100,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (190, 110, 80)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (190, 110, 80)).save(crops / "2.jpg", "JPEG")
    Image.new("RGB", (64, 64), (128, 128, 128)).save(crops / "3.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 3
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 3


def test_list_people_cover_prefers_solo_over_side_neighbor(tmp_path, monkeypatch):
    """A face whose neighbour sits just outside the crop centre still shows in the pad."""
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    other = create_person("Beside")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/group.jpg", "g", 800, 800, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/solo.jpg", "s", 800, 800, now_iso()),
    )
    # Subject left-of-centre; neighbour is beside and slightly higher so its centre
    # sits outside the cover crop (not "in front") while the pad still overlaps.
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,80,80,280,280,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,1,320,70,500,250,0.9,'ok',?,?)""",
        (other["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (3,2,80,80,260,260,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    group = Image.new("RGB", (64, 64), (190, 140, 110))
    ImageDraw.Draw(group).rectangle((16, 38, 48, 46), fill=(230, 230, 220))
    group.save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (190, 140, 110)).save(crops / "3.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 3
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 3


def test_list_people_cover_prefers_color_over_bw(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/bw.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/color.jpg", "c", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,50,50,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (190, 110, 80)).save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_bright_over_dark(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/albums/Adam Cole night.jpg", "d", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/albums/picnic.jpg", "c", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,50,50,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (88, 42, 22)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (190, 110, 80)).save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_sharp_over_blur(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw, ImageFilter

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/blur.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/sharp.jpg", "s", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,70,70,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    blur = Image.new("RGB", (128, 128), (190, 140, 110))
    ImageDraw.Draw(blur).ellipse((18, 18, 110, 110), fill=(220, 170, 130))
    ImageDraw.Draw(blur).rectangle((40, 78, 88, 92), fill=(230, 230, 220))
    blur.filter(ImageFilter.GaussianBlur(14)).resize((64, 64)).save(crops / "1.jpg", "JPEG")
    sharp = Image.new("RGB", (64, 64), (190, 140, 110))
    draw = ImageDraw.Draw(sharp)
    draw.ellipse((8, 6, 56, 58), fill=(210, 160, 125))
    draw.rectangle((20, 22, 26, 28), fill=(40, 30, 25))
    draw.rectangle((38, 22, 44, 28), fill=(40, 30, 25))
    draw.rectangle((24, 40, 40, 46), fill=(90, 50, 45))
    sharp.save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_readable_over_blown_out(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/flash.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/clear.jpg", "c", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (2,2,0,0,70,70,0.80,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    def _face(path, *, flash=False):
        im = Image.new("RGB", (64, 64), (190, 140, 110))
        d = ImageDraw.Draw(im)
        d.ellipse((8, 6, 56, 58), fill=(210, 160, 125))
        d.rectangle((20, 22, 26, 28), fill=(40, 30, 25))
        d.rectangle((38, 22, 44, 28), fill=(40, 30, 25))
        if flash:
            d.rectangle((8, 6, 56, 40), fill=(255, 255, 255))
        im.save(path, "JPEG")

    _face(crops / "1.jpg", flash=True)
    _face(crops / "2.jpg")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_named_sex_over_smile(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/woman.jpg", "w", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/man.jpg", "m", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,'F',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (2,2,0,0,70,70,0.80,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    def _face(path, *, teeth=False):
        im = Image.new("RGB", (64, 64), (190, 140, 110))
        d = ImageDraw.Draw(im)
        d.ellipse((8, 6, 56, 58), fill=(210, 160, 125))
        d.rectangle((20, 22, 26, 28), fill=(40, 30, 25))
        d.rectangle((38, 22, 44, 28), fill=(40, 30, 25))
        if teeth:
            d.rectangle((16, 38, 48, 46), fill=(230, 230, 220))
        else:
            d.rectangle((24, 40, 40, 44), fill=(90, 50, 45))
        im.save(path, "JPEG")

    _face(crops / "1.jpg", teeth=True)
    _face(crops / "2.jpg")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_grown_over_child_smile(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/baby.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/adult.jpg", "a", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (2,2,0,0,80,80,0.80,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    baby = Image.new("RGB", (32, 32), (230, 200, 180))
    d = ImageDraw.Draw(baby)
    d.rectangle((20, 20, 26, 26), fill=(40, 30, 25))
    d.rectangle((16, 18, 28, 23), fill=(230, 230, 220))
    baby.save(crops / "1.jpg", "JPEG")
    adult = Image.new("RGB", (32, 32), (200, 150, 120))
    d = ImageDraw.Draw(adult)
    d.rectangle((20, 22, 26, 28), fill=(40, 30, 25))
    d.rectangle((10, 22, 22, 29), fill=(55, 40, 32))
    adult.save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_a_smile(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/frown.jpg", "f", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/smile.jpg", "s", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,80,80,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (190, 140, 110)).save(crops / "1.jpg", "JPEG")
    smile = Image.new("RGB", (64, 64), (190, 140, 110))
    ImageDraw.Draw(smile).rectangle((16, 38, 48, 46), fill=(230, 230, 220))
    smile.save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_facing_camera(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/side.jpg", "p", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/front.jpg", "f", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,80,80,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    profile = Image.new("RGB", (64, 64), (220, 175, 150))
    ImageDraw.Draw(profile).rectangle((32, 0, 64, 64), fill=(40, 28, 22))
    profile.save(crops / "1.jpg", "JPEG")
    front = Image.new("RGB", (64, 64), (190, 120, 90))
    ImageDraw.Draw(front).ellipse((12, 16, 24, 28), fill=(40, 28, 22))
    ImageDraw.Draw(front).ellipse((40, 16, 52, 28), fill=(40, 28, 22))
    front.save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_recent_camera_photo(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, taken_at, created_at) VALUES (?,?,?,?,?,?)",
        ("/scan.jpg", "o", 3000, 2000, "2016-11-01T00:00:00", now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, taken_at, created_at) VALUES (?,?,?,?,?,?)",
        ("/recent.jpg", "n", 8000, 5300, "2025-10-18T00:00:00", now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,80,80,0.80,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    old = Image.new("RGB", (64, 64), (190, 120, 90))
    ImageDraw.Draw(old).ellipse((12, 16, 24, 28), fill=(40, 28, 22))
    ImageDraw.Draw(old).ellipse((40, 16, 52, 28), fill=(40, 28, 22))
    ImageDraw.Draw(old).rectangle((16, 38, 48, 46), fill=(230, 230, 220))
    old.save(crops / "1.jpg", "JPEG")
    recent = Image.new("RGB", (64, 64), (190, 120, 90))
    ImageDraw.Draw(recent).ellipse((12, 16, 24, 28), fill=(40, 28, 22))
    ImageDraw.Draw(recent).ellipse((40, 16, 52, 28), fill=(40, 28, 22))
    recent.save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 2


def test_list_people_cover_prefers_majority_sex(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/woman.jpg", "w", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/man.jpg", "m", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,'F',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (2,2,0,0,70,70,0.80,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, sex_est, created_at)
           VALUES (3,2,10,10,50,50,0.78,'ok',?,'M',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    for i in (1, 2, 3):
        Image.new("RGB", (64, 64), (150, 140, 130)).save(crops / f"{i}.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] in (2, 3)


def test_advance_person_cover_cycles_and_pins(tmp_path, monkeypatch):
    from PIL import Image

    conn = _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    import photosort.people as people_mod

    monkeypatch.setattr(people_mod, "CROP_DIR", crops)
    person = create_person("Adam Cole")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/bw.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/color.jpg", "c", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,1,0,0,90,90,0.99,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,2,0,0,50,50,0.72,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (64, 64), (190, 110, 80)).save(crops / "2.jpg", "JPEG")
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 2
    first = advance_person_cover(person["id"])
    assert first["cover_face_id"] == 1
    listed = {p["name"]: p for p in list_people()}
    assert listed["Adam Cole"]["cover_face_id"] == 1
    lite = {p["name"]: p for p in list_people(lite=True)}
    assert lite["Adam Cole"]["cover_face_id"] == 1
    second = advance_person_cover(person["id"])
    assert second["cover_face_id"] == 2
    from fastapi.testclient import TestClient
    from photosort.main import app

    client = TestClient(app)
    shown = client.post(f"/api/people/{person['id']}/next-cover")
    assert shown.status_code == 200
    assert shown.json()["cover_face_id"] == 1
    assert "/api/faces/1/crop" in (shown.json()["cover_url"] or "")


def test_get_person_http_returns_photo_shots(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        (str(tmp_path / "wide.jpg"), "w", "2010-01-01T00:00:00", 1600, 900, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        (str(tmp_path / "tall.jpg"), "t", "2011-01-01T00:00:00", 900, 1600, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Pat Hall")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,0,0,10,10,0.8,'ok',?,?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    payload = client.get(f"/api/people/{person['id']}").json()
    assert payload["name"] == "Pat Hall"
    assert "faces" not in payload
    assert len(payload["shots"]) == 2
    first = payload["shots"][0]
    assert first["thumb_url"] == f"/api/photos/{first['photo_id']}/thumb"
    assert first["crop_url"].startswith("/api/faces/")
    assert first["photo_width"] == 1600
    assert first["photo_height"] == 900
    assert first["tags"] == []
    assert payload["shots"][1]["photo_width"] == 900
    assert payload["shots"][1]["photo_height"] == 1600


def test_name_cluster_http_attaches_faces_when_group_id_is_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.execute("UPDATE faces SET cluster_id = NULL")
    conn.execute("DELETE FROM clusters")
    conn.commit()
    conn.close()
    client = TestClient(app)
    first = client.post("/api/clusters/1/name", json={"name": "Mary Cruz", "face_ids": [1]})
    assert first.status_code == 200
    assert first.json()["assigned"] == 1
    people = client.get("/api/people").json()["items"]
    assert any(p["name"] == "Mary Cruz" and p["face_count"] == 1 for p in people)
    second = client.post("/api/clusters/99/name", json={"name": "Mary Cruz", "face_ids": [1]})
    assert second.status_code == 200
    assert second.json()["person"]["id"] == first.json()["person"]["id"]
    conn = connect()
    names = [r["name"] for r in conn.execute("SELECT name FROM people").fetchall()]
    conn.close()
    assert names.count("Mary Cruz") == 1
    assert find_person_by_name("mary cruz")["id"] == first.json()["person"]["id"]


def test_list_clusters_keeps_all_face_ids_when_preview_is_capped(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    for i in range(26):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
               VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
            (now_iso(),),
        )
    conn.commit()
    conn.close()
    client = TestClient(app)
    payload = client.get("/api/clusters").json()["items"][0]
    assert payload["face_count"] == 26
    assert len(payload["faces"]) == 1


def test_list_clusters_preview_caps_unique_pictures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    for i in range(30):
        conn.execute(
            "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
            (str(tmp_path / f"{i}.jpg"), f"sha-{i}", 100, 100, now_iso()),
        )
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
               VALUES (?,0,0,10,10,0.9,'ok',1,?)""",
            (i + 1, now_iso()),
        )
    conn.commit()
    conn.close()
    client = TestClient(app)
    payload = client.get("/api/clusters").json()["items"][0]
    assert payload["face_count"] == 30
    assert len(payload["faces"]) == 24


def test_assign_cluster_http_returns_when_clustering_is_busy(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.cluster import _cluster_lock
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    person = create_person("Alex Cole")
    assert _cluster_lock.acquire(blocking=False)
    try:
        client = TestClient(app)
        response = client.post("/api/clusters/1/assign", json={"person_id": person["id"]})
    finally:
        _cluster_lock.release()
    assert response.status_code == 200
    assert response.json()["assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = 1").fetchone()
    named = conn.execute("SELECT status FROM clusters WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]
    assert row["assigned_how"] == "cluster"
    assert named["status"] == "named"


def test_assign_and_unknown_cluster_http_set_category(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',2,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    person = create_person("Alex Cole")
    client = TestClient(app)
    assigned = client.post(
        "/api/clusters/1/assign",
        json={"person_id": person["id"], "category": "Family"},
    )
    unknown = client.post("/api/clusters/2/unknown", json={"category": "family"})
    assert assigned.status_code == 200
    assert unknown.status_code == 200
    conn = connect()
    named = conn.execute("SELECT category FROM people WHERE id = ?", (person["id"],)).fetchone()
    created = conn.execute(
        "SELECT category FROM people WHERE id = ?",
        (unknown.json()["person"]["id"],),
    ).fetchone()
    conn.close()
    assert named["category"] == "family"
    assert created["category"] == "family"


def test_unknown_cluster_http_does_not_recluster(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, cluster, faces, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()

    def boom(*_args, **_kwargs):
        raise AssertionError("unknown should not regroup remaining faces")

    monkeypatch.setattr(cluster, "try_run_clustering", boom)
    monkeypatch.setattr(cluster, "run_clustering", boom)
    monkeypatch.setattr(faces, "sweep_statues", boom)
    client = TestClient(app)
    response = client.post("/api/clusters/1/unknown", json={"face_ids": [1]})
    assert response.status_code == 200
    assert response.json()["assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = 1").fetchone()
    named = conn.execute("SELECT status FROM clusters WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"]
    assert row["assigned_how"] == "cluster"
    assert named["status"] == "named"


def test_display_faces_keeps_one_copy_of_each_picture():
    faces = [
        {
            "id": 1,
            "photo_id": 10,
            "det_score": 0.7,
            "sha256": "aaa",
            "path": "/album/IMG_1.JPG",
            "taken_at": "2021-12-26T04:35:05",
        },
        {
            "id": 2,
            "photo_id": 10,
            "det_score": 0.9,
            "sha256": "aaa",
            "path": "/album/IMG_1.JPG",
            "taken_at": "2021-12-26T04:35:05",
        },
        {
            "id": 3,
            "photo_id": 11,
            "det_score": 0.8,
            "sha256": "aaa",
            "path": "/album/Sebi/IMG_1.JPG",
            "taken_at": "2021-12-26T04:35:05",
        },
        {
            "id": 4,
            "photo_id": 12,
            "det_score": 0.6,
            "sha256": "bbb",
            "path": "/album/IMG_2.JPG",
            "taken_at": "2021-12-26T04:35:06",
        },
        {
            "id": 5,
            "photo_id": 13,
            "det_score": 0.85,
            "sha256": "ccc",
            "path": "/album/IMG_10.JPG",
            "taken_at": "2021-12-26T05:00:00",
        },
    ]
    shown = display_faces(faces)
    ids = [f["id"] for f in shown]
    assert ids == [2, 5]


def test_rename_person_http_updates_name(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app

    _setup(tmp_path, monkeypatch)
    person = create_person("Nora")
    client = TestClient(app)
    saved = client.patch(f"/api/people/{person['id']}", json={"name": "Nora Hall"}).json()
    assert saved["name"] == "Nora Hall"
    fetched = client.get(f"/api/people/{person['id']}").json()
    assert fetched["name"] == "Nora Hall"


def test_person_nickname_is_saved_and_used_in_search(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app
    from photosort.people import find_person_by_name
    from photosort.util import now_iso

    _setup(tmp_path, monkeypatch)
    person = create_person("Jordan Cole", nickname="Jordy")
    assert person["nickname"] == "Jordy"
    assert find_person_by_name("jordy")["id"] == person["id"]
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    saved = client.patch(f"/api/people/{person['id']}", json={"nickname": "Jo, Jordy"}).json()
    assert saved["nickname"] == "Jo, Jordy"
    found = client.get("/api/search", params={"q": "jordy"}).json()
    names = {p["name"] for p in found["people"]}
    assert "Jordan Cole" in names
    empty = client.patch(f"/api/people/{person['id']}", json={"nickname": ""}).json()
    assert empty["nickname"] == ""


def test_person_category_family_work_other(tmp_path, monkeypatch):
    from photosort.people import normalize_category, update_person

    _setup(tmp_path, monkeypatch)
    person = create_person("Alex")
    assert (person.get("category") or "") == ""
    assert normalize_category("Family") == "family"
    assert normalize_category("pets") == ""
    updated = update_person(person["id"], category="work")
    assert updated["category"] == "work"
    cleared = update_person(person["id"], category="")
    assert cleared["category"] == ""
    family = create_person("Pat", category="Family")
    assert family["category"] == "family"


def test_patch_person_http_sets_other_category(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app

    _setup(tmp_path, monkeypatch)
    person = create_person("Alex Cole")
    client = TestClient(app)
    other = client.patch(f"/api/people/{person['id']}", json={"category": "other"}).json()
    assert other["category"] == "other"
    shown = client.get(f"/api/people/{person['id']}").json()
    assert shown["category"] == "other"
    cleared = client.patch(f"/api/people/{person['id']}", json={"category": ""}).json()
    assert cleared["category"] == ""


def test_assign_face_http_reuses_name_and_sets_category(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    first = create_person("Jordan Cole", category="family")
    client = TestClient(app)
    response = client.post("/api/faces/1/assign", json={"name": "jordan cole", "category": "family"})
    assert response.status_code == 200
    assert response.json()["person_id"] == first["id"]


def test_assign_face_http_uses_unique_first_name(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    first = create_person("Jordan Cole", category="family")
    client = TestClient(app)
    names = client.get("/api/people", params={"names": "true"}).json()
    assert any(p["name"] == "Jordan Cole" for p in names["items"])
    response = client.post("/api/faces/1/assign", json={"name": "Jordan"})
    assert response.status_code == 200
    assert response.json()["person_id"] == first["id"]
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] == first["id"]


def test_assign_face_http_does_not_wait_for_rematch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals, match as match_mod
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    person = create_person("Mark Hall")

    def boom(*_a, **_k):
        raise AssertionError("match_unknown should not block assign")

    monkeypatch.setattr(match_mod, "match_unknown", boom)
    monkeypatch.setattr(match_mod, "match_photo", lambda *_a, **_k: {"auto_assigned": 0})
    client = TestClient(app)
    response = client.post("/api/faces/1/assign", json={"person_id": person["id"]})
    assert response.status_code == 200
    assert response.json()["person_id"] == person["id"]
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]
    assert row["assigned_how"] == "manual"


def test_restore_faces_undoes_junk(tmp_path, monkeypatch):
    from photosort.people import junk_faces, restore_faces

    _setup(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    assert junk_faces([1]) == 1
    conn = connect()
    hidden = conn.execute("SELECT quality, assigned_how FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert hidden["assigned_how"] == "junk"
    assert hidden["quality"] == "unidentifiable"
    assert restore_faces([1]) == 1
    conn = connect()
    row = conn.execute("SELECT quality, assigned_how FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["quality"] == "ok"
    assert row["assigned_how"] is None


def test_restore_faces_clears_sidecar_junk_so_apply_does_not_rehide(tmp_path, monkeypatch):
    import json
    from photosort import originals, sidecar
    from photosort.people import junk_faces, restore_faces

    album = tmp_path / "album"
    album.mkdir()
    photo = album / "picnic.jpg"
    photo.write_bytes(b"x")
    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(originals, "DATA_DIR", data)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo), "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    assert junk_faces([1]) == 1
    payload = json.loads((album / originals.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert payload["photos"]["picnic.jpg"]["faces"][0]["junk"] is True
    assert restore_faces([1]) == 1
    side = album / originals.SIDECAR_NAME
    if side.exists():
        payload = json.loads(side.read_text(encoding="utf-8"))
        stored = (payload.get("photos") or {}).get("picnic.jpg", {}).get("faces") or []
        assert not any(item.get("junk") for item in stored)
    sidecar.apply_to_photos([1])
    conn = connect()
    row = conn.execute("SELECT assigned_how, quality FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["assigned_how"] is None
    assert row["quality"] == "ok"


def test_match_photo_skip_detect_does_not_rescan(tmp_path, monkeypatch):
    from photosort import faces as faces_mod, match as match_mod

    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()

    def boom(*_a, **_k):
        raise AssertionError("restore rematch must not rescan")

    monkeypatch.setattr(faces_mod, "scan_photo", boom)
    monkeypatch.setattr(faces_mod, "analyzer_status", lambda: {"ready": True})
    match_mod.match_photo(1, detect=False)


def test_name_cluster_http_saves_category(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    response = client.post(
        "/api/clusters/1/name",
        json={"name": "Mary Cruz", "face_ids": [1], "category": "family"},
    )
    assert response.status_code == 200
    assert response.json()["person"]["category"] == "family"
    people = client.get("/api/people").json()["items"]
    assert any(p["name"] == "Mary Cruz" and p["category"] == "family" for p in people)


def test_unknown_name_tag_increments(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = create_unknown_person()
    b = create_unknown_person()
    assert a["name"] == "Unknown name of person"
    assert b["name"] == "Unknown name of person 2"
    assert is_unknown_name(a["name"])
    assert is_unknown_name(b["name"])
    assert not is_unknown_name("Alex")


def test_match_assigns_small_unidentifiable_face(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    vec = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/wide.jpg", "w", 1280, 960, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,80,80,0.9,'ok',?,?, 'cluster', ?)""",
        (vec, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,700,500,736,543,0.86,'unidentifiable',?,?)""",
        (vec, now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE quality = 'unidentifiable'").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]
    assert row["assigned_how"] == "auto"


def test_match_photo_retries_cleared_but_leaves_junk(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app

    _setup(tmp_path, monkeypatch)
    person = create_person("Clara")
    vec = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/group.jpg", "g", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (vec, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,80,0,120,40,0.9,'ok',?,'cleared',?)""",
        (vec, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,160,0,200,40,0.9,'ok',?,'junk',?)""",
        (vec, now_iso()),
    )
    conn.commit()
    conn.close()
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    client = TestClient(app)
    out = client.post("/api/photos/1/match", params={"wait": True}).json()
    assert out["auto_assigned"] == 1
    assert out["photo_id"] == 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] == person["id"]
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] == person["id"]
    assert rows[2]["assigned_how"] == "auto"
    assert rows[3]["person_id"] is None
    assert rows[3]["assigned_how"] == "junk"


def test_rematch_photo_api_returns_before_matching(tmp_path, monkeypatch):
    import threading
    import time
    from fastapi.testclient import TestClient
    from photosort import main as main_mod
    from photosort.main import app

    _setup(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/group.jpg", "g", 800, 600, now_iso()),
    )
    conn.commit()
    conn.close()
    gate = threading.Event()

    def slow_match(photo_id, detect=True):
        gate.wait(timeout=5)
        return {
            "photo_id": photo_id,
            "auto_assigned": 0,
            "assigned": [],
            "medium": 0,
            "new_faces": 0,
            "considered": 0,
        }

    monkeypatch.setattr(main_mod.match_mod, "match_photo", slow_match)
    main_mod._photo_match.clear()
    client = TestClient(app)
    try:
        t0 = time.monotonic()
        out = client.post("/api/photos/1/match").json()
        assert time.monotonic() - t0 < 0.8
        assert out["started"] is True
        assert out["status"] == "running"
        assert "auto_assigned" not in out
        status = client.get("/api/photos/1/match").json()
        assert status["status"] == "running"
        jobs = client.get("/api/jobs").json()
        assert any(item.get("photo_id") == 1 for item in jobs.get("photo_matches") or [])
        gate.set()
        done = None
        for _ in range(100):
            done = client.get("/api/photos/1/match").json()
            if done.get("status") == "done":
                break
            time.sleep(0.02)
        assert done["status"] == "done"
        assert done["photo_id"] == 1
    finally:
        gate.set()
        main_mod._photo_match.clear()


def test_match_photo_restores_junk_face_named_on_nearby_photos(tmp_path, monkeypatch):
    """Re-identify should un-hide a person marked not-a-person if they are in this gathering."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    named = _unit(1.0, 0.0)
    probe = _unit(0.72, (1.0 - 0.72**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/xmas/214756.jpg", "a", "2017-12-25T21:47:56", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/xmas/214759.jpg", "b", "2017-12-25T21:47:59", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(named), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (2,0,0,40,40,0.9,'unidentifiable',?,'junk',?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Ada"
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == ada["id"]
    assert row["assigned_how"] == "auto"


def test_match_unknown_rescues_hidden_face_that_matches_catalog(tmp_path, monkeypatch):
    """Find Known Faces should un-hide a real person marked not-a-person."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    named = _unit(1.0, 0.0)
    probe = _unit(0.72, (1.0 - 0.72**2) ** 0.5)
    other = _unit(0.0, 0.0, 1.0)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 200, 200, now_iso()),
    )
    for i in range(4):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), ada["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(other), bea["id"], now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/b.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (2,0,0,40,40,0.9,'unidentifiable',?,'junk',?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] >= 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == ada["id"]
    assert row["assigned_how"] == "auto"


def test_match_unknown_keeps_junk_when_that_person_is_already_named(tmp_path, monkeypatch):
    """A second box of someone already named on the photo stays hidden."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    named = _unit(1.0, 0.0)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(named), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,80,0,120,40,0.9,'unidentifiable',?,'junk',?)""",
        (embedding_to_bytes(named), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE assigned_how = 'junk'").fetchone()
    conn.close()
    assert out["auto_assigned"] == 0
    assert row["person_id"] is None
    assert row["assigned_how"] == "junk"


def test_match_photo_uses_nearest_named_face_not_centroid(tmp_path, monkeypatch):
    """A large catalog of unlike photos of one person should not hide a close match."""
    _setup(tmp_path, monkeypatch)
    person = create_person("Ada")
    other = create_person("Bea")
    rng = np.random.default_rng(7)
    probe = l2_normalize(rng.normal(size=32).astype(np.float32))
    noise = l2_normalize(rng.normal(size=32).astype(np.float32))
    bea = l2_normalize(rng.normal(size=32).astype(np.float32))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/ada.jpg", "ada", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/new.jpg", "new", 200, 200, now_iso()),
    )
    for i in range(16):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(noise), person["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(probe), person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,100,0,110,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(bea), other["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    from photosort.people import all_person_centroids
    from photosort.util import cosine

    centroid = all_person_centroids()[person["id"]]["all"]
    assert cosine(probe, centroid) < 0.52
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Ada"
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]


def test_match_does_not_auto_name_a_man_as_a_woman(tmp_path, monkeypatch):
    from photosort.match import match_unknown, revoke_auto_sex_mismatches

    _setup(tmp_path, monkeypatch)
    joan = create_person("June Reed")
    david = create_person("David John Reed")
    joan_vec = _unit(1.0, 0.0)
    david_vec = _unit(0.0, 1.0)
    boy = _unit(0.72, (1.0 - 0.72**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/b.jpg", "b", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'F',?, 'manual', ?)""",
        (embedding_to_bytes(joan_vec), joan["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,'M',?, 'manual', ?)""",
        (embedding_to_bytes(david_vec), david["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,'M',?)""",
        (embedding_to_bytes(boy), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] != joan["id"]
    assert out["auto_assigned"] == 0 or row["person_id"] == david["id"]


def test_match_photo_names_boy_even_if_detector_guessed_female(tmp_path, monkeypatch):
    """Re-identify must not skip a strong catalog hit because InsightFace guessed the wrong sex."""
    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    thomas = create_person("Thomas Cole")
    joan = create_person("June Reed")
    named = _unit(1.0, 0.0)
    other = _unit(0.0, 1.0)
    probe = _unit(0.56, 0.2, (1.0 - 0.56**2 - 0.2**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/a.jpg", "a", "1994-01-01T00:00:00", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/set/b.jpg", "b", "1994-01-01T00:01:00", 200, 200, now_iso()),
    )
    for i in range(30):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,'M',?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), thomas["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,'F',?, 'manual', ?)""",
        (embedding_to_bytes(other), joan["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, sex_est, age_est, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,'F',12,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == thomas["id"]


def test_revoke_auto_sex_mismatches_clears_wrong_auto_names(tmp_path, monkeypatch):
    from photosort.match import revoke_auto_sex_mismatches

    _setup(tmp_path, monkeypatch)
    joan = create_person("June Reed")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, sex_est, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok','M',?, 'auto', ?)""",
        (joan["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, sex_est, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok','F',?, 'manual', ?)""",
        (joan["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    n = revoke_auto_sex_mismatches()
    assert n == 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] is None
    assert rows[2]["person_id"] == joan["id"]


def test_revoke_unlike_confirmed_drops_auto_names_of_other_people(tmp_path, monkeypatch):
    from photosort.match import revoke_unlike_confirmed

    _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    sam = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    other = embedding_to_bytes(l2_normalize(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (sam, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,?, 'auto', ?)""",
        (other, person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,40,0,50,10,0.9,'ok',?,?, 'cluster', ?)""",
        (sam, person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    n = revoke_unlike_confirmed()
    assert n == 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] is None
    assert rows[3]["person_id"] == person["id"]


def test_search_people_by_vectors_ranks_named_catalog(tmp_path, monkeypatch):
    from photosort.match import search_people_by_vectors
    from photosort.util import embedding_to_bytes

    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    probe = l2_normalize(np.array([1.0, 0.0, 0.0] + [0.0] * 29, dtype=np.float32))
    other = l2_normalize(np.array([0.0, 1.0, 0.0] + [0.0] * 29, dtype=np.float32))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(probe), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(other), bea["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    hits = search_people_by_vectors([probe], limit=2)
    assert hits[0]["name"] == "Ada"
    assert hits[0]["similarity"] > 0.9
    empty = search_people_by_vectors([], limit=2)
    assert empty == []


def test_match_photo_skips_rescan_when_everyone_is_named(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    vec = embedding_to_bytes(l2_normalize(np.ones(8, dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (vec, ada["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("should not rescan a fully named photo")

    monkeypatch.setattr("photosort.faces.scan_photo", boom)
    monkeypatch.setattr("photosort.faces.analyzer_status", lambda: {"ready": True})
    out = match_photo(1)
    assert called == []
    assert out["new_faces"] == 0
    assert out["auto_assigned"] == 0


def test_undo_match_photo_clears_auto_names_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app
    from photosort.match import undo_match_photo

    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    vec = embedding_to_bytes(l2_normalize(np.ones(8, dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/group.jpg", "g", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (vec, ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,80,0,120,40,0.9,'ok',?,?)""",
        (vec, now_iso()),
    )
    conn.commit()
    conn.close()
    named = match_photo(1)
    auto_ids = [item["face_id"] for item in named["assigned"]]
    assert auto_ids == [2]
    kept = undo_match_photo(1, auto_ids + [1])
    assert kept["undone"] == 1
    assert kept["face_ids"] == [2]
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] == ada["id"]
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] is None
    assert rows[2]["assigned_how"] == "cleared"
    client = TestClient(app)
    again = client.post("/api/photos/1/match", params={"wait": True}).json()
    # Re-identify is an explicit retry: a still-strong catalog hit can be named again.
    assert again["auto_assigned"] == 1
    conn = connect()
    stuck = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = 2").fetchone()
    conn.close()
    assert stuck["person_id"] == ada["id"]
    assert stuck["assigned_how"] == "auto"


def _unit(*vals, dim=32):
    vec = np.zeros(dim, dtype=np.float32)
    for i, value in enumerate(vals):
        vec[i] = value
    return l2_normalize(vec)


def test_match_photo_assigns_when_several_named_photos_agree(tmp_path, monkeypatch):
    """Re-identify should name someone at ~0.44 if several catalog photos agree."""
    from photosort.config import MATCH_HIGH

    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    probe = _unit(0.44, (1.0 - 0.44**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/new.jpg", "q", 200, 200, now_iso()),
    )
    for i, tilt in enumerate((0.0, 0.02, 0.04, -0.02, 0.03)):
        named = _unit(1.0, tilt)
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), ada["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(0.0, 0.0, 1.0)), bea["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    from photosort.util import cosine

    assert cosine(probe, _unit(1.0, 0.0)) < MATCH_HIGH
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Ada"


def test_match_photo_does_not_name_a_class_as_one_person(tmp_path, monkeypatch):
    """Re-identify on a group photo must not copy one name onto every child."""
    _setup(tmp_path, monkeypatch)
    nora = create_person("Nora Hall")
    probe = _unit(0.48, (1.0 - 0.48**2) ** 0.5)
    named = _unit(1.0, 0.0)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/nora.jpg", "m", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/class.jpg", "c", 2000, 1400, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, cluster_id, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', 1, ?)""",
        (embedding_to_bytes(named), nora["id"], now_iso()),
    )
    for i in range(12):
        x = (i % 6) * 80
        y = (i // 6) * 80
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (2,?,?,?,?,0.9,'ok',?,1,?)""",
            (x, y, x + 40, y + 40, embedding_to_bytes(probe), now_iso()),
        )
    conn.commit()
    conn.close()
    out = match_photo(2)
    conn = connect()
    named_kids = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE photo_id = 2 AND person_id IS NOT NULL"
    ).fetchone()["n"]
    conn.close()
    assert named_kids <= 1
    assert out["auto_assigned"] <= 1


def test_match_photo_names_when_several_catalog_photos_agree_at_042(tmp_path, monkeypatch):
    """Vintage child scans often sit at ~0.42. Several named photos of one person still win."""
    _setup(tmp_path, monkeypatch)
    nick = create_person("Nick Cole")
    bea = create_person("Bea Cole")
    probe = _unit(0.42, (1.0 - 0.42**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/new.jpg", "q", 200, 200, now_iso()),
    )
    for i, tilt in enumerate((0.0, 0.01, -0.01, 0.02, -0.02, 0.015)):
        named = _unit(1.0, tilt)
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), nick["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(0.0, 0.0, 1.0)), bea["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    from photosort.util import cosine
    from photosort.config import MATCH_REMATCH_HIGH

    assert cosine(probe, _unit(1.0, 0.0)) < MATCH_REMATCH_HIGH
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Nick Cole"


def test_match_photo_retries_cleared_faces(tmp_path, monkeypatch):
    """Clicking Re-identify should try faces whose names were previously taken off."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada Cole")
    named = _unit(1.0, 0.0)
    probe = _unit(0.72, (1.0 - 0.72**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/new.jpg", "q", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(named), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,'cleared',?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == ada["id"]
    assert row["assigned_how"] == "auto"


def test_match_photo_skips_a_near_tie(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    probe = _unit(0.41, 0.41, (1.0 - 2 * 0.41**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/set/new.jpg", "q", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(1.0, 0.0, 0.0)), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(0.0, 1.0, 0.0)), bea["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_photo(2)
    assert out["auto_assigned"] == 0
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_match_names_occluded_face_from_nearby_photos(tmp_path, monkeypatch):
    """A hand-over-face shot still gets the name from the same gathering a minute away."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    probe = _unit(0.34, (1.0 - 0.34**2) ** 0.5)
    named = _unit(1.0, 0.0)
    other = _unit(0.0, 0.0, 1.0)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/xmas/214518.jpg", "a", "2017-12-25T21:45:18", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/xmas/214559.jpg", "b", "2017-12-25T21:45:59", 200, 200, now_iso()),
    )
    for i in range(12):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), ada["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(other), bea["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    from photosort.util import cosine
    from photosort.config import MATCH_REMATCH_HIGH

    assert cosine(probe, named) < MATCH_REMATCH_HIGH
    out = match_unknown()
    assert out["auto_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] == ada["id"]


def test_match_skips_low_sim_when_not_a_nearby_burst(tmp_path, monkeypatch):
    """Occlusion-level cosine is not enough if that person is not in this gathering."""
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    probe = _unit(0.34, (1.0 - 0.34**2) ** 0.5)
    named = _unit(1.0, 0.0)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/other/old.jpg", "a", "2010-01-01T00:00:00", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/xmas/214559.jpg", "b", "2017-12-25T21:45:59", 200, 200, now_iso()),
    )
    for i in range(12):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,?, 'manual', ?)""",
            (i, 0, i + 10, 10, embedding_to_bytes(named), ada["id"], now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 0
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_match_photo_prefers_someone_already_named_in_the_album(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ada = create_person("Ada")
    bea = create_person("Bea")
    probe = _unit(0.46, (1.0 - 0.46**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/set/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/set/new.jpg", "q", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(1.0, 0.0)), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(_unit(0.0, 0.0, 1.0)), bea["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,0,0,40,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Ada"


def test_junk_cluster_stays_hidden_and_matches_similar(tmp_path, monkeypatch):
    from photosort import config, match as match_mod

    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(match_mod, "connect", connect)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/statue.jpg", "s", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    base = np.ones(8, dtype=np.float32)
    for _ in range(2):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
               VALUES (1,0,0,10,10,0.9,'ok',?,1,?)""",
            (embedding_to_bytes(base), now_iso()),
        )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?)""",
        (embedding_to_bytes(base), now_iso()),
    )
    conn.commit()
    conn.close()
    n = junk_cluster(1)
    assert n == 2
    extra = suppress_like_junk(threshold=0.9)
    assert extra == 1
    conn = connect()
    leftover = conn.execute("SELECT COUNT(*) AS n FROM faces WHERE quality = 'ok'").fetchone()["n"]
    conn.close()
    assert leftover == 0


def test_suppress_like_junk_keeps_faces_that_match_a_named_person(tmp_path, monkeypatch):
    """Hiding a lookalike object must not hide the real person in the next frame."""
    from photosort import config, match as match_mod

    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(match_mod, "connect", connect)
    ada = create_person("Ada")
    named = _unit(1.0, 0.0)
    person = _unit(0.72, (1.0 - 0.72**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/room.jpg", "r", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(named), ada["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.9,'unidentifiable',?,'junk',?)""",
        (embedding_to_bytes(person), now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,40,0,50,10,0.9,'ok',?,?)""",
        (embedding_to_bytes(person), now_iso()),
    )
    conn.commit()
    conn.close()
    extra = suppress_like_junk(threshold=0.5)
    conn = connect()
    row = conn.execute("SELECT assigned_how FROM faces WHERE id = 3").fetchone()
    conn.close()
    assert extra == 0
    assert row["assigned_how"] is None


def test_suppress_like_junk_keeps_a_skin_crop_even_if_embeddings_match(tmp_path, monkeypatch):
    """A dinner-table face must not be hidden because it is 0.66 like a junked crop."""
    from PIL import Image
    from photosort import config, match as match_mod

    _setup(tmp_path, monkeypatch)
    crops = tmp_path / "crops"
    crops.mkdir()
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(match_mod, "connect", connect)
    vec = embedding_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/dinner.jpg", "d", 200, 200, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'unidentifiable',?,'junk',?)""",
        (vec, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (1,40,0,80,40,0.9,'ok',?,?)""",
        (vec, now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (80, 80), (190, 140, 100)).save(crops / "2.jpg", "JPEG")
    extra = suppress_like_junk(threshold=0.5)
    conn = connect()
    row = conn.execute("SELECT assigned_how, quality FROM faces WHERE id = 2").fetchone()
    conn.close()
    assert extra == 0
    assert row["assigned_how"] is None
    assert row["quality"] == "ok"


def test_junk_cluster_http_does_not_recluster(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, cluster, faces, match as match_mod, originals
    from photosort.main import app

    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()

    def boom(*_args, **_kwargs):
        raise AssertionError("junk should not regroup remaining faces on the request")

    monkeypatch.setattr(cluster, "try_run_clustering", boom)
    monkeypatch.setattr(cluster, "run_clustering", boom)
    monkeypatch.setattr(faces, "sweep_statues", boom)
    monkeypatch.setattr(match_mod, "suppress_like_junk", lambda *a, **k: 0)
    client = TestClient(app)
    response = client.post("/api/clusters/1/junk", json={"face_ids": [1]})
    assert response.status_code == 200
    assert response.json()["cleared"] == 1
    conn = connect()
    row = conn.execute("SELECT assigned_how, quality FROM faces WHERE id = 1").fetchone()
    named = conn.execute("SELECT status FROM clusters WHERE id = 1").fetchone()
    conn.close()
    assert row["assigned_how"] == "junk"
    assert row["quality"] == "unidentifiable"
    assert named["status"] == "junk"


def test_junk_faces_hides_one_detection(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/buddha.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,20,20,30,30,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    assert junk_faces([2]) == 1
    conn = connect()
    rows = conn.execute("SELECT id, assigned_how, person_id FROM faces ORDER BY id").fetchall()
    conn.close()
    assert rows[0]["assigned_how"] != "junk"
    assert rows[1]["assigned_how"] == "junk"
    assert rows[1]["person_id"] is None


def test_split_person_cluster_restores_identity(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/a.jpg", "a", "2003-01-01T00:00:00", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('named', ?)", (now_iso(),))
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('named', ?)", (now_iso(),))
    conn.commit()
    conn.close()
    adult = create_person("Nora")
    child = create_person("Alex")
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?,1,?)""",
        (emb, child["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, cluster_id, created_at)
           VALUES (1,20,0,30,10,0.9,'ok',?,?,2,?)""",
        (emb, adult["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    merged = merge_people(child["id"], adult["id"])
    assert merged["face_count"] == 1
    restored = split_person_cluster(adult["id"], 1, "Alex")
    assert restored["name"] == "Alex"
    assert restored["face_count"] == 1
    leftover = get_person(adult["id"])
    assert leftover["face_count"] == 1
    assert leftover["name"] == "Nora"


def test_photos_stay_on_one_person(tmp_path, monkeypatch):
    from photosort.main import _visible_photo_rows

    conn = _setup(tmp_path, monkeypatch)
    for i, taken in enumerate(("2001-01-01T00:00:00", "2002-01-01T00:00:00", "2003-01-01T00:00:00"), start=1):
        conn.execute(
            "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
            (f"/p{i}.jpg", str(i), taken, 100, 100, now_iso()),
        )
    conn.commit()
    conn.close()
    a = create_person("A")
    b = create_person("B")
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?,?)""",
        (emb, a["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,?,?)""",
        (emb, b["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',?,?,?)""",
        (emb, a["id"], now_iso()),
    )
    conn.commit()
    only_a = [r["id"] for r in _visible_photo_rows(conn, person_id=a["id"])]
    conn.close()
    assert only_a == [1, 3]


def test_merge_suggestions_veto_same_photo(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/c.jpg", "c", "2000-01-01T00:00:00", 100, 100, now_iso()),
    )
    conn.commit()
    conn.close()
    a = create_person("A")
    b = create_person("B")
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn = connect()
    for pid in (a["id"], b["id"]):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, age_est, person_id, created_at)
               VALUES (1,0,0,10,10,0.9,'ok',?,30,?,?)""",
            (emb, pid, now_iso()),
        )
    conn.commit()
    conn.close()
    assert merge_suggestions() == []


def test_reset_names_keeps_photos_and_junk(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/keep.jpg", "k", 100, 100, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('junk', ?)", (now_iso(),))
    conn.commit()
    conn.close()
    person = create_person("Ada")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?, 'cluster', ?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'unidentifiable',2,'junk',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    out = reset_names()
    assert out["writes_originals"] is False
    assert out["writes_sidecars"] is False
    assert out["people_removed"] == 1
    conn = connect()
    assert conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM photos").fetchone()["n"] == 1
    named = conn.execute("SELECT person_id, assigned_how, quality FROM faces WHERE assigned_how = 'junk'").fetchone()
    assert named["quality"] == "unidentifiable"
    cleared = conn.execute("SELECT person_id, assigned_how FROM faces WHERE quality = 'ok'").fetchone()
    assert cleared["person_id"] is None
    conn.close()


def test_list_people_filters_to_one_folder(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1996 - Picnic/b.jpg", "b", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1024 x 768/preview.jpg", "p", 10, 10, now_iso()),
    )
    conn.commit()
    conn.close()
    tokyo = create_person("Empress Michiko")
    vodka = create_person("Sam")
    both = create_person("Emperor Akihito")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (tokyo["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (vodka["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (both["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (both["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (tokyo["id"], now_iso()),
    )
    conn.commit()
    conn.close()

    everyone = {p["name"]: p for p in list_people()}
    assert set(everyone) == {"Empress Michiko", "Sam", "Emperor Akihito"}
    assert everyone["Empress Michiko"]["face_count"] == 1
    assert everyone["Emperor Akihito"]["face_count"] == 2
    assert everyone["Empress Michiko"]["cover_face_id"] is not None

    tokyo_only = {p["name"]: p for p in list_people(folder="1994 - Harbor")}
    assert set(tokyo_only) == {"Empress Michiko", "Emperor Akihito"}
    assert tokyo_only["Empress Michiko"]["face_count"] == 1
    assert tokyo_only["Emperor Akihito"]["face_count"] == 1

    vodka_only = {p["name"]: p for p in list_people(folder="1996 - Picnic")}
    assert set(vodka_only) == {"Sam", "Emperor Akihito"}
    assert vodka_only["Sam"]["face_count"] == 1
    assert vodka_only["Emperor Akihito"]["face_count"] == 1

    from photosort.people import list_people_folders

    names = {row["folder"] for row in list_people_folders()}
    assert names == {"1996 - Picnic", "1994 - Harbor"}


def test_reset_names_one_folder_keeps_other_folder(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1996 - Picnic/b.jpg", "b", 10, 10, now_iso()),
    )
    conn.commit()
    conn.close()
    tokyo = create_person("Empress Michiko")
    vodka = create_person("Sam")
    both = create_person("Emperor Akihito")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (tokyo["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (vodka["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (both["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (both["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    out = reset_names("1994 - Harbor")
    assert out["folder"] == "1994 - Harbor"
    conn = connect()
    names = {r["name"] for r in conn.execute("SELECT name FROM people")}
    assert names == {"Sam", "Emperor Akihito"}
    tokyo_named = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE photo_id = 1 AND person_id IS NOT NULL"
    ).fetchone()["n"]
    vodka_named = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE photo_id = 2 AND person_id IS NOT NULL"
    ).fetchone()["n"]
    conn.close()
    assert tokyo_named == 0
    assert vodka_named == 2


def test_reset_matching_clears_auto_keeps_manual(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1996 - Picnic/b.jpg", "b", 10, 10, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Sam")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    out = reset_matching("1996 - Picnic")
    assert out["faces_cleared"] == 1
    conn = connect()
    tokyo = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 1").fetchone()
    vodka = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    names = {r["name"] for r in conn.execute("SELECT name FROM people")}
    conn.close()
    assert tokyo["person_id"] == person["id"]
    assert tokyo["assigned_how"] == "manual"
    assert vodka["person_id"] is None
    assert names == {"Sam"}


def test_reset_names_two_folders(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1994 - Harbor/a.jpg", "a", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/1996 - Picnic/b.jpg", "b", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/Sam@Cafe/c.jpg", "c", 10, 10, now_iso()),
    )
    conn.commit()
    conn.close()
    tokyo = create_person("Empress Michiko")
    vodka = create_person("Sam")
    cafe = create_person("Ada")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (tokyo["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (vodka["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',?,'cluster',?)""",
        (cafe["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    out = reset_names(folders=["1994 - Harbor", "1996 - Picnic"])
    assert set(out["folders"]) == {"1994 - Harbor", "1996 - Picnic"}
    conn = connect()
    names = {r["name"] for r in conn.execute("SELECT name FROM people")}
    assert names == {"Ada"}
    leftover = conn.execute(
        "SELECT photo_id FROM faces WHERE person_id IS NOT NULL"
    ).fetchall()
    conn.close()
    assert [r["photo_id"] for r in leftover] == [3]


def test_merge_suggestions_skip_named_pair_on_age_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = create_person("Emperor Akihito")
    b = create_person("Sam")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/old.jpg", "o", "1980-01-01T00:00:00", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/new.jpg", "n", "2003-01-01T00:00:00", 100, 100, now_iso()),
    )
    young = embedding_to_bytes(np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    old = embedding_to_bytes(np.array([0, 1, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, age_est, person_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,25,?,?)""",
        (young, a["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, age_est, person_id, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,55,?,?)""",
        (old, b["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    assert merge_suggestions() == []


def test_match_unknown_does_not_reuse_a_name_already_in_the_photo(tmp_path, monkeypatch):
    """Brothers in one frame: the unnamed man is the other person, not a second Alex."""
    _setup(tmp_path, monkeypatch)
    alex = create_person("Alex Cole")
    jordan = create_person("Jordan Cole")
    alex_vec = _unit(1.0, 0.0)
    jon_vec = _unit(0.70, (1.0 - 0.70**2) ** 0.5)
    probe = _unit(0.99, (1.0 - 0.99**2) ** 0.5)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named.jpg", "n", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(alex_vec), alex["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,120,40,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(jon_vec), jordan["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (2,0,0,80,80,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(alex_vec), alex["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,200,0,280,80,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    conn = connect()
    unnamed = conn.execute(
        "SELECT person_id, assigned_how FROM faces WHERE photo_id = 2 AND x1 > 100"
    ).fetchone()
    conn.close()
    assert out["auto_assigned"] == 1
    assert unnamed["person_id"] == jordan["id"]
    assert unnamed["assigned_how"] == "auto"


def test_assign_cluster_skips_face_when_that_person_is_already_in_the_photo(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,0,0,80,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,200,0,280,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.commit()
    conn.close()
    alex = create_person("Alex Cole")
    assign_faces([1], alex["id"], how="manual", rematch=False, sync_sidecars=False)
    n = assign_cluster(1, alex["id"], sync_sidecars=False)
    assert n == 0
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] == alex["id"]
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] is None


def test_assign_cluster_does_not_overwrite_a_manual_name(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    jon_emb = embedding_to_bytes(_unit(1.0, 0.0))
    other_emb = embedding_to_bytes(_unit(0.0, 1.0))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,1,?)""",
        (jon_emb, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,1,?)""",
        (other_emb, now_iso()),
    )
    conn.commit()
    conn.close()
    jordan = create_person("Jordan Cole")
    alex = create_person("Alex Cole")
    assign_faces([1], jordan["id"], how="manual", rematch=False, sync_sidecars=False)
    n = assign_cluster(1, alex["id"], sync_sidecars=False)
    assert n == 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how FROM faces")}
    conn.close()
    assert rows[1]["person_id"] == jordan["id"]
    assert rows[1]["assigned_how"] == "manual"
    assert rows[2]["person_id"] == alex["id"]
    assert rows[2]["assigned_how"] == "cluster"


def test_assign_cluster_skips_face_that_matches_someone_else(tmp_path, monkeypatch):
    """Brothers in one unnamed group: naming the group Alex must not stamp Jordan."""
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    alex_vec = _unit(1.0, 0.0)
    jon_vec = _unit(0.0, 1.0)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named.jpg", "n", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(alex_vec), None, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (2,0,0,80,80,0.9,'ok',?,1,?)""",
        (embedding_to_bytes(jon_vec), now_iso()),
    )
    conn.commit()
    conn.close()
    alex = create_person("Alex Cole")
    jordan = create_person("Jordan Cole")
    assign_faces([1], alex["id"], how="manual", rematch=False, sync_sidecars=False)
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(jon_vec), jordan["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    n = assign_cluster(1, alex["id"], sync_sidecars=False)
    assert n == 0
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE cluster_id = 1").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_assign_cluster_report_explains_already_in_photo(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,0,0,80,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,200,0,280,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.commit()
    conn.close()
    alex = create_person("Alex Cole")
    assign_faces([1], alex["id"], how="manual", rematch=False, sync_sidecars=False)
    report = assign_cluster_report(1, alex["id"], face_ids=[2], sync_sidecars=False)
    assert report.assigned == 0
    assert report.reason == "already_in_photo"
    assert "already named" in report.message().lower()


def test_assign_cluster_report_explains_lookalike(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    alex_vec = _unit(1.0, 0.0)
    jon_vec = _unit(0.0, 1.0)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/named.jpg", "n", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(alex_vec), None, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (2,0,0,80,80,0.9,'ok',?,1,?)""",
        (embedding_to_bytes(jon_vec), now_iso()),
    )
    conn.commit()
    conn.close()
    alex = create_person("Alex Cole")
    jordan = create_person("Jordan Cole")
    assign_faces([1], alex["id"], how="manual", rematch=False, sync_sidecars=False)
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,80,0,90,10,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(jon_vec), jordan["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    report = assign_cluster_report(1, alex["id"], sync_sidecars=False)
    assert report.assigned == 0
    assert report.reason == "lookalike"
    assert "someone else" in report.message().lower()


def test_name_cluster_http_returns_reason_and_writes_log(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort import catalog, originals
    from photosort.main import app

    monkeypatch.setenv("PHOTOSORT_LOG_FILE", "1")
    conn = _setup(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(catalog, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    emb = embedding_to_bytes(np.ones(8, dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/pub.jpg", "p", 800, 600, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,0,0,80,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id, created_at)
           VALUES (1,200,0,280,80,0.9,'ok',?,1,?)""",
        (emb, now_iso()),
    )
    conn.commit()
    conn.close()
    alex = create_person("Alex Cole")
    assign_faces([1], alex["id"], how="manual", rematch=False, sync_sidecars=False)
    client = TestClient(app)
    result = client.post(
        "/api/clusters/1/assign",
        json={"person_id": alex["id"], "face_ids": [2]},
    ).json()
    assert result["assigned"] == 0
    assert result["reason"] == "already_in_photo"
    assert "already named" in (result["message"] or "").lower()
    log_text = (data / "logs" / "app.log").read_text()
    assert "save assign failed" in log_text
    assert "cluster=1" in log_text
    assert "already_in_photo" in log_text


def test_client_log_endpoint_writes_app_log(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app

    monkeypatch.setenv("PHOTOSORT_LOG_FILE", "1")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    client = TestClient(app)
    out = client.post(
        "/api/log",
        json={"message": "Could not save that name.", "page": "to-name", "action": "name", "cluster_id": 42},
    ).json()
    assert out["ok"] is True
    log_text = (data / "logs" / "app.log").read_text()
    assert "Could not save that name." in log_text
    assert "page=to-name" in log_text
    assert "cluster=42" in log_text
