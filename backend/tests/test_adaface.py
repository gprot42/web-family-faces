import numpy as np

from photosort import config, db
from photosort.adaface import FACE_FRAC, INPUT_SIZE, _bgr_face
from photosort.db import connect, init_db
from photosort.match import (
    ADA_EXEMPLARS_PER_PERSON,
    _invalidate_galleries,
    load_ada_gallery,
    match_photo,
    match_unknown,
)
from photosort.people import create_person
from photosort.util import embedding_to_bytes, l2_normalize, now_iso


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    _invalidate_galleries()
    return connect()


def _vec(*xs):
    return embedding_to_bytes(l2_normalize(np.array(xs, dtype=np.float32)))


def test_bgr_face_is_112_square():
    rgb = np.zeros((384, 384, 3), dtype=np.uint8)
    rgb[100:280, 100:280] = 200
    out = _bgr_face(rgb)
    assert out.shape == (INPUT_SIZE, INPUT_SIZE, 3)
    assert int(round(384 * FACE_FRAC)) >= 8


def test_adaface_names_when_arcface_is_unsure(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    person = create_person("Sam")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, embedding_ada,
           person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?,?,'manual',?)""",
        (_vec(1, 0, 0), _vec(0, 1, 0), person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, embedding_ada, created_at)
           VALUES (2,0,0,20,20,0.9,'ok',?,?,?)""",
        (_vec(0, 0, 1), _vec(0, 1, 0), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 1
    assert out["adaface_assigned"] == 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE photo_id = 2").fetchone()
    conn.close()
    assert int(row["person_id"]) == int(person["id"])
    assert row["assigned_how"] == "auto"


def test_adaface_skips_when_models_disagree(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    sam = create_person("Sam")
    bea = create_person("Bea")
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
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, embedding_ada,
           person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.9,'ok',?,?,?,'manual',?)""",
        (_vec(1, 0, 0), _vec(0, 1, 0), sam["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, embedding_ada,
           person_id, assigned_how, created_at)
           VALUES (2,0,0,20,20,0.9,'ok',?,?,?,'manual',?)""",
        (_vec(0, 0, 1), _vec(1, 0, 0), bea["id"], now_iso()),
    )
    # ArcFace weakly prefers Sam (0.52); AdaFace is sure it is Bea.
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, embedding_ada, created_at)
           VALUES (3,0,0,20,20,0.9,'ok',?,?,?)""",
        (_vec(0.52, 0.854, 0), _vec(1, 0, 0), now_iso()),
    )
    conn.commit()
    conn.close()
    out = match_unknown()
    assert out["auto_assigned"] == 0
    assert out["adaface_assigned"] == 0
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE photo_id = 3").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_ada_gallery_fill_uses_few_exemplars_per_person(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    sam = create_person("Sam")
    bea = create_person("Bea")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/a.jpg", "a", 100, 100, now_iso()),
    )
    for i, person in enumerate((sam, bea)):
        for n in range(8):
            conn.execute(
                """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding,
                   person_id, assigned_how, created_at)
                   VALUES (1,?,?,?,?,0.9,'ok',?,?,'manual',?)""",
                (n, i * 20, n + 10, i * 20 + 10, _vec(1, 0, 0), person["id"], now_iso()),
            )
    conn.commit()
    called: list[int] = []

    def fake_embed(_conn, face_id):
        called.append(int(face_id))
        vec = l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        _conn.execute(
            "UPDATE faces SET embedding_ada = ? WHERE id = ?",
            (embedding_to_bytes(vec), int(face_id)),
        )
        return vec

    monkeypatch.setattr("photosort.adaface.embedding_for_face", fake_embed)
    gallery = load_ada_gallery(conn, fill=True)
    conn.close()
    assert len(called) == ADA_EXEMPLARS_PER_PERSON * 2
    assert gallery["matrix"] is not None
    assert len(gallery["person_ids"]) == ADA_EXEMPLARS_PER_PERSON * 2


def test_match_photo_still_names_one_face_in_a_family_group(tmp_path, monkeypatch):
    """Re-identify on a 9-person family shot must not switch off rematch thresholds."""
    conn = _setup(tmp_path, monkeypatch)
    nora = create_person("Nora Hall")
    probe = l2_normalize(np.array([0.48, (1.0 - 0.48**2) ** 0.5, 0.0], dtype=np.float32))
    named = l2_normalize(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    other = l2_normalize(np.array([0.0, 0.0, 1.0], dtype=np.float32))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/nora.jpg", "m", 200, 200, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/album/family.jpg", "f", 2000, 1400, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,?, 'manual', ?)""",
        (embedding_to_bytes(named), nora["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
           VALUES (2,80,0,120,40,0.9,'ok',?,?)""",
        (embedding_to_bytes(probe), now_iso()),
    )
    for i in range(8):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, embedding, created_at)
               VALUES (2,?,?,?,?,0.9,'ok',?,?)""",
            ((i + 2) * 80, 0, (i + 2) * 80 + 40, 40, embedding_to_bytes(other), now_iso()),
        )
    conn.commit()
    conn.close()
    skipped = match_unknown()
    assert skipped["auto_assigned"] == 0
    out = match_photo(2)
    assert out["auto_assigned"] == 1
    assert out["assigned"][0]["name"] == "Nora Hall"
