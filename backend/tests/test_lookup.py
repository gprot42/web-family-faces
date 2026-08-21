import base64
import json

import pytest
from PIL import Image

from photosort import config, db, lookup
from photosort.db import connect, init_db
from photosort.people import create_person
from photosort.util import now_iso


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    crops = data / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(lookup, "CROP_DIR", crops)
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = connect()
    init_db(conn)
    return conn, crops


def _face(conn, crops, face_id=1, cluster_id=1, person_id=None, path="/album/shot.jpg"):
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (path, f"h{face_id}", 100, 100, now_iso()),
    )
    photo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    if cluster_id:
        exists = conn.execute("SELECT id FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO clusters (id, status, created_at) VALUES (?, 'unknown', ?)",
                (cluster_id, now_iso()),
            )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, person_id, created_at)
           VALUES (?,?,0,0,10,10,0.9,'ok',?,?,?)""",
        (face_id, photo_id, cluster_id, person_id, now_iso()),
    )
    Image.new("RGB", (32, 32), (180, 140, 110)).save(crops / f"{face_id}.jpg", "JPEG")
    conn.commit()


def test_parse_lookup_from_markdown():
    raw = '```json\n{"found": true, "name": "Emperor Akihito", "also_known_as": ["Akihito"], "role": "Emperor of Japan", "confidence": "high", "why": "Public figure."}\n```'
    parsed = lookup.parse_lookup_payload(raw)
    assert parsed["found"] is True
    assert parsed["name"] == "Emperor Akihito"


def test_parse_lookup_from_responses_payload():
    raw = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "found": False,
                                "name": None,
                                "also_known_as": [],
                                "role": None,
                                "confidence": "low",
                                "why": "Private person.",
                            }
                        ),
                    }
                ],
            }
        ]
    }
    parsed = lookup.parse_lookup_payload(raw)
    assert parsed["found"] is False


def test_find_existing_person_is_case_insensitive(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_person("Emperor Akihito")
    hit = lookup.find_existing_person("emperor akihito", ["Akihito"])
    assert hit["name"] == "Emperor Akihito"
    assert lookup.find_existing_person("Someone else") is None


def test_lookup_requires_api_key(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops)
    conn.close()
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "")
    with pytest.raises(lookup.LookupError) as exc:
        lookup.lookup_cluster(1)
    assert exc.value.status == 503
    assert "Settings" in exc.value.message


def test_lookup_cluster_sends_crop_not_original(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops, path="/Volumes/media/originals/secret.jpg")
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")

    captured = {}

    def fake_call(images, hints=""):
        captured["images"] = images
        captured["hints"] = hints
        return {
            "found": True,
            "name": "Emperor Akihito",
            "also_known_as": [],
            "role": "Emperor of Japan",
            "confidence": "high",
            "why": "Matches public portraits.",
        }

    monkeypatch.setattr(lookup, "call_xai", fake_call)
    result = lookup.lookup_cluster(1)
    assert result["found"] is True
    assert result["name"] == "Emperor Akihito"
    assert result["confidence_pct"] == 85
    assert result["sent_originals"] is False
    assert result["sent_face_ids"] == [1]
    dumped = json.dumps(captured)
    assert "/Volumes/media/originals/secret.jpg" not in dumped
    assert "secret.jpg" in captured["hints"]
    assert "originals" in captured["hints"]
    decoded = base64.b64decode(captured["images"][0]["b64"])
    assert decoded[:2] == b"\xff\xd8"
    assert result["existing_person_id"] is None


def test_lookup_links_existing_person(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    person = create_person("Emperor Akihito")
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")
    monkeypatch.setattr(
        lookup,
        "call_xai",
        lambda images, hints="": {
            "found": True,
            "name": "Emperor Akihito",
            "also_known_as": [],
            "role": "Emperor",
            "confidence": "high",
            "why": "Public.",
        },
    )
    result = lookup.lookup_cluster(1)
    assert result["existing_person_id"] == person["id"]
    assert result["existing_person_name"] == "Emperor Akihito"


def test_lookup_skips_preview_copy(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops, face_id=1, path="/album/1024 x 768/shot.jpg")
    _face(conn, crops, face_id=2, path="/album/shot.jpg")
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")
    sent = {}

    def fake_call(images, hints=""):
        sent["ids"] = [i["face_id"] for i in images]
        return {
            "found": False,
            "name": None,
            "also_known_as": [],
            "role": None,
            "confidence": "low",
            "why": "Private.",
        }

    monkeypatch.setattr(lookup, "call_xai", fake_call)
    result = lookup.lookup_cluster(1)
    assert sent["ids"] == [2]
    assert result["found"] is False


def test_lookup_hints_include_folder_year_and_named_companions(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    person = create_person("Emperor Akihito")
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/album/1994 - Harbor/DSC00260.JPG", "h", "2003-12-02T03:58:08", 100, 100, now_iso()),
    )
    photo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("INSERT INTO clusters (id, status, created_at) VALUES (1, 'unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, person_id, created_at)
           VALUES (1,?,0,0,10,10,0.9,'ok',1,NULL,?)""",
        (photo_id, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (2,?,0,0,10,10,0.9,'ok',?,?)""",
        (photo_id, person["id"], now_iso()),
    )
    Image.new("RGB", (32, 32), (180, 140, 110)).save(crops / "1.jpg", "JPEG")
    conn.commit()
    conn.close()
    hints = lookup._lookup_hints([1])
    assert "1994 - Harbor" in hints
    assert "2003" in hints
    assert "Emperor Akihito" in hints
    assert "same photo" in hints
    assert "DSC00260.JPG" in hints
    assert "/album/1994 - Harbor/DSC00260.JPG" not in hints


def test_lookup_hints_include_other_catalog_names(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    create_person("Sam")
    create_person("Emperor Akihito")
    _face(conn, crops, path="/album/trip/shot.jpg")
    conn.close()
    hints = lookup._lookup_hints([1])
    assert "Sam" in hints
    assert "Emperor Akihito" in hints
    assert "already named in this catalog" in hints.lower()


def test_lookup_hints_survive_bad_file_dates(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    photo = tmp_path / "DSC00260.JPG"
    Image.new("RGB", (24, 24), "navy").save(photo, "JPEG")
    _face(conn, crops, path=str(photo))
    conn.close()

    def boom(_path):
        raise OverflowError("timestamp out of range for platform time_t")

    monkeypatch.setattr("photosort.originals.read_photo_clues", boom)
    hints = lookup._lookup_hints([1])
    assert "DSC00260.JPG" in hints
    assert str(photo) not in hints


def test_lookup_hints_include_filename_exif_and_file_dates(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    photo = tmp_path / "1994 - Harbor" / "DSC00260.JPG"
    photo.parent.mkdir()
    img = Image.new("RGB", (40, 40), (180, 140, 110))
    exif = Image.Exif()
    exif[271] = "Sony"
    exif[272] = "DSC-P8"
    exif[306] = "2003:12:02 03:58:08"
    img.save(photo, "JPEG", exif=exif)
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        (str(photo), "h", "2003-12-02T03:58:08", 40, 40, now_iso()),
    )
    conn.execute("INSERT INTO clusters (id, status, created_at) VALUES (1, 'unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    Image.new("RGB", (32, 32), (180, 140, 110)).save(crops / "1.jpg", "JPEG")
    conn.commit()
    conn.close()
    hints = lookup._lookup_hints([1])
    assert "DSC00260.JPG" in hints
    assert "2003-12-02" in hints
    assert "Sony" in hints
    assert str(photo) not in hints


def test_lookup_retries_without_image_search_after_timeout(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")
    calls = []

    def fake_post(images, hints, *, image_search):
        calls.append(image_search)
        if image_search:
            raise lookup.LookupError("Lookup timed out. Try again in a moment.", 504)
        return {
            "found": True,
            "name": "Emperor Akihito",
            "also_known_as": [],
            "role": "Emperor",
            "confidence": "medium",
            "why": "Retry without image search.",
        }

    monkeypatch.setattr(lookup, "_post_xai", fake_post)
    result = lookup.lookup_cluster(1)
    assert calls == [True, False]
    assert result["found"] is True
    assert result["name"] == "Emperor Akihito"


def test_lookup_missing_cluster(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(lookup.LookupError) as exc:
        lookup.lookup_cluster(99)
    assert exc.value.status == 404


def test_identify_assigns_existing_catalog_person(tmp_path, monkeypatch):
    from photosort.jobs import create_job
    from photosort.lookup import run_identify

    conn, crops = _setup(tmp_path, monkeypatch)
    person = create_person("Emperor Akihito")
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr("photosort.match.match_unknown", lambda job_id=None, **kwargs: {"auto_assigned": 0})
    monkeypatch.setattr(lookup, "lookup_status", lambda: {"available": True})
    monkeypatch.setattr(
        lookup,
        "lookup_cluster",
        lambda *args, **kwargs: {
            "found": True,
            "name": "Emperor Akihito",
            "existing_person_id": person["id"],
            "confidence_pct": 82,
        },
    )
    run_identify(create_job("identify"))
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] == person["id"]


def test_identify_skips_uncertain_catalog_match(tmp_path, monkeypatch):
    from photosort.jobs import create_job
    from photosort.lookup import run_identify

    conn, crops = _setup(tmp_path, monkeypatch)
    person = create_person("Emperor Akihito")
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr("photosort.match.match_unknown", lambda job_id=None, **kwargs: {"auto_assigned": 0})
    monkeypatch.setattr(lookup, "lookup_status", lambda: {"available": True})
    monkeypatch.setattr(
        lookup,
        "lookup_cluster",
        lambda *args, **kwargs: {
            "found": True,
            "name": "Emperor Akihito",
            "existing_person_id": person["id"],
            "confidence_pct": 64,
        },
    )
    run_identify(create_job("identify"))
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_identify_skips_when_lookup_unavailable(tmp_path, monkeypatch):
    from photosort.jobs import create_job, get_job
    from photosort.lookup import run_identify

    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr("photosort.match.match_unknown", lambda job_id=None, **kwargs: {"auto_assigned": 2})
    monkeypatch.setattr(lookup, "lookup_status", lambda: {"available": False})
    job_id = create_job("identify")
    run_identify(job_id)
    job = get_job(job_id)
    assert "Matched 2 from the catalog" in (job["message"] or "")
    conn = connect()
    row = conn.execute("SELECT person_id FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] is None


def test_lookup_cluster_uses_face_ids_if_group_was_regrouped(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops)
    conn.execute("DELETE FROM clusters")
    conn.execute("UPDATE faces SET cluster_id = NULL")
    conn.commit()
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")
    monkeypatch.setattr(
        lookup,
        "call_xai",
        lambda images, hints="": {
            "found": True,
            "name": "Emperor Akihito",
            "also_known_as": [],
            "role": "Emperor",
            "confidence": "high",
            "why": "Public.",
        },
    )
    with pytest.raises(lookup.LookupError) as exc:
        lookup.lookup_cluster(1)
    assert exc.value.status == 404
    result = lookup.lookup_cluster(1, face_ids=[1])
    assert result["found"] is True
    assert result["name"] == "Emperor Akihito"
    assert result["sent_face_ids"] == [1]


def test_normalize_confidence_pct_and_labels():
    assert lookup.normalize_confidence({"confidence_pct": 91}) == (91, "high")
    assert lookup.normalize_confidence({"confidence": "medium"}) == (60, "medium")
    assert lookup.normalize_confidence({"confidence_pct": 78}) == (78, "medium")
    assert lookup.normalize_confidence({"confidence": "12%"}) == (12, "low")


def test_feedback_block_lists_rejected_names():
    text = lookup.feedback_block("not Empress Michiko", ["Empress Michiko"])
    assert "Empress Michiko" in text
    assert "not Empress Michiko" in text
    assert "Do not suggest" in text


def test_lookup_sends_user_feedback(tmp_path, monkeypatch):
    conn, crops = _setup(tmp_path, monkeypatch)
    _face(conn, crops)
    conn.close()
    monkeypatch.setattr(lookup, "xai_api_key", lambda: "test-key")
    captured = {}

    def fake_call(images, hints=""):
        captured["hints"] = hints
        return {
            "found": True,
            "name": "Emperor Akihito",
            "also_known_as": [],
            "role": "Emperor of Japan",
            "confidence_pct": 64,
            "why": "Next guess.",
        }

    monkeypatch.setattr(lookup, "call_xai", fake_call)
    result = lookup.lookup_cluster(1, note="That is not Empress Michiko.", rejected_names=["Empress Michiko"])
    assert result["confidence_pct"] == 64
    assert "Empress Michiko" in captured["hints"]
    assert "That is not Empress Michiko." in captured["hints"]
