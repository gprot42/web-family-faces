from fastapi.testclient import TestClient

from photosort import catalog, config, db, originals
from photosort.db import connect, init_db
from photosort.main import app
from photosort.people import list_name_folders
from photosort.util import now_iso


def _db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(config, "CROP_DIR", data / "crops")
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(catalog, "DB_PATH", path)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    (data / "backups").mkdir()
    (data / "thumbs").mkdir()
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/share/1995 - Coast/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/share/1994 - Harbor/b.jpg", "b", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()


def test_list_photos_filters_folder_without_touching_disk(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    all_photos = client.get("/api/photos").json()
    assert all_photos["total"] == 2
    assert all_photos["items"][0]["file_available"] is None
    assert {item["filename"] for item in all_photos["items"]} == {"a.jpg", "b.jpg"}
    assert all_photos["items"][0]["faces"] or all_photos["items"][1]["faces"]

    scoped = client.get("/api/photos", params=[("folder", "/Volumes/share/1995 - Coast")]).json()
    assert scoped["total"] == 1
    assert scoped["items"][0]["filename"] == "a.jpg"

    parent = client.get("/api/photos", params=[("folder", "/Volumes/share")]).json()
    assert parent["total"] == 2


def test_list_photos_folder_underscore_is_literal(tmp_path, monkeypatch):
    """LIKE _ is a wildcard; Scanned_Photos_ must not swallow a sibling folder."""
    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/share/Scanned_Album_1994/Set 1/a.jpg", "n1", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/share/ScannedXAlbumY1994/other.jpg", "n2", 100, 100, now_iso()),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    scoped = client.get(
        "/api/photos",
        params=[("folder", "/Volumes/share/Scanned_Album_1994")],
    ).json()
    assert scoped["total"] == 1
    assert scoped["items"][0]["filename"] == "a.jpg"


def test_catalog_folders_include_album_path(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    items = list_name_folders()
    by_name = {row["folder"]: row for row in items}
    assert by_name["1995 - Coast"]["path"] == "/Volumes/share/1995 - Coast"
    assert by_name["1995 - Coast"]["photos"] == 1


def test_list_albums_under_includes_unscanned_children(tmp_path, monkeypatch):
    from photosort.people import list_albums_under

    _db(tmp_path, monkeypatch)
    root = tmp_path / "Photo_Collection"
    (root / "1997 - Market").mkdir(parents=True)
    (root / "1998 - Harbor 2").mkdir()
    (root / "1994 - Trip 1024 x 768").mkdir()
    items = list_albums_under([str(root)])
    names = [row["folder"] for row in items]
    assert names == ["1997 - Market", "1998 - Harbor 2"]
    by_name = {row["folder"]: row for row in items}
    assert by_name["1997 - Market"]["photos"] == 0
    assert by_name["1998 - Harbor 2"]["photos"] == 0
    assert by_name["1997 - Market"].get("group") in ("", None)


def test_list_albums_under_expands_nested_scan_sets(tmp_path, monkeypatch):
    from photosort.people import list_albums_under
    from photosort.util import now_iso

    _db(tmp_path, monkeypatch)
    root = tmp_path / "Photo_Collection"
    nested = root / "Scanned_Album_1998_Apr"
    (nested / "Loose Photos").mkdir(parents=True)
    (nested / "Scanned Photos Set (1)").mkdir()
    (root / "1997 - Market").mkdir()
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(nested / "Loose Photos" / "a.jpg"), "n1", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(nested / "Scanned Photos Set (1)" / "b.jpg"), "n2", 100, 100, now_iso()),
    )
    conn.commit()
    conn.close()
    items = list_albums_under([str(root)])
    by_path = {row["path"]: row for row in items}
    assert str(nested) not in by_path
    assert str(nested / "Loose Photos") in by_path
    assert str(nested / "Scanned Photos Set (1)") in by_path
    assert by_path[str(nested / "Loose Photos")]["photos"] == 1
    assert by_path[str(nested / "Loose Photos")]["group"] == "Scanned_Album_1998_Apr"
    assert by_path[str(nested / "Loose Photos")]["group_path"] == str(nested)
    assert by_path[str(root / "1997 - Market")]["group"] in ("", None)
    assert by_path[str(root / "1997 - Market")]["photos"] == 0


def test_path_in_folder_includes_nested_scan_sets():
    from photosort.people import path_in_folder

    nested = "/Volumes/media/Scanned_Album_1994/Set 3 - Holiday scans/a.jpg"
    assert path_in_folder(nested, "Scanned_Album_1994")
    assert path_in_folder(nested, "/Volumes/media/Scanned_Album_1994")
    assert path_in_folder(nested, "Set 3 - Holiday scans")
    assert not path_in_folder(nested, "Scanned_Album_1998_Apr")


def test_face_tag_position_is_stored(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    saved = client.patch("/api/faces/1", json={"tag_x": 42.5, "tag_y": 18}).json()
    assert saved["tag_x"] == 42.5
    assert saved["tag_y"] == 18
    photo = client.get("/api/photos/1").json()
    face = next(item for item in photo["faces"] if item["id"] == 1)
    assert face["tag_x"] == 42.5
    assert face["tag_y"] == 18
    cleared = client.patch("/api/faces/1", json={"clear_tag": True}).json()
    assert cleared["tag_x"] is None
    assert cleared["tag_y"] is None


def test_face_comment_is_stored_without_clearing_tag(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    client.patch("/api/faces/1", json={"tag_x": 10, "tag_y": 20})
    saved = client.patch("/api/faces/1", json={"comment": "  girl in the red coat  "}).json()
    assert saved["comment"] == "girl in the red coat"
    assert saved["tag_x"] == 10
    assert saved["tag_y"] == 20
    photo = client.get("/api/photos/1").json()
    face = next(item for item in photo["faces"] if item["id"] == 1)
    assert face["comment"] == "girl in the red coat"
    listed = client.get("/api/photos", params={"q": "red coat"}).json()
    assert {item["filename"] for item in listed["items"]} == {"a.jpg"}
    cleared = client.patch("/api/faces/1", json={"comment": ""}).json()
    assert cleared["comment"] == ""
    assert cleared["tag_x"] == 10


def test_person_notes_are_stored(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    person = client.post("/api/people", json={"name": "Nora"}).json()
    saved = client.patch(f"/api/people/{person['id']}", json={"notes": "  Mum's sister  "}).json()
    assert saved["notes"] == "Mum's sister"
    loaded = client.get(f"/api/people/{person['id']}").json()
    assert loaded["notes"] == "Mum's sister"


def test_photo_comment_is_stored_and_listed(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    saved = client.patch("/api/photos/1", json={"comment": "  Wedding at the church  "}).json()
    assert saved["comment"] == "Wedding at the church"
    assert saved["path"] == "/Volumes/share/1995 - Coast/a.jpg"
    photo = client.get("/api/photos/1").json()
    assert photo["comment"] == "Wedding at the church"
    listed = client.get("/api/photos", params={"q": "church"}).json()
    assert {item["filename"] for item in listed["items"]} == {"a.jpg"}
    cleared = client.patch("/api/photos/1", json={"comment": ""}).json()
    assert cleared["comment"] == ""
    gone = client.get("/api/photos", params={"q": "church"}).json()
    assert gone["total"] == 0


def test_photo_tags_are_stored_listed_and_filter(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    saved = client.patch("/api/photos/1", json={"tags": ["  Christmas  ", "family", "christmas", ""]}).json()
    assert saved["tags"] == ["Christmas", "family"]
    photo = client.get("/api/photos/1").json()
    assert photo["tags"] == ["Christmas", "family"]
    listed = client.get("/api/photos", params={"tag": "christmas"}).json()
    assert {item["filename"] for item in listed["items"]} == {"a.jpg"}
    assert listed["items"][0]["tags"] == ["Christmas", "family"]
    names = client.get("/api/photos/tags").json()
    assert names["items"][0]["tag"] in {"Christmas", "family"}
    assert {row["tag"] for row in names["items"]} == {"Christmas", "family"}
    found = client.get("/api/photos", params={"q": "Christmas"}).json()
    assert {item["filename"] for item in found["items"]} == {"a.jpg"}
    tagged = client.get("/api/photos/1", params={"tag": "family"}).json()
    assert tagged["tag"] == "family"
    assert tagged["prev_id"] is None
    assert tagged["next_id"] is None
    cleared = client.patch("/api/photos/1", json={"tags": []}).json()
    assert cleared["tags"] == []
    gone = client.get("/api/photos", params={"tag": "Christmas"}).json()
    assert gone["total"] == 0


def test_rotate_and_hide_photo_leave_original_path(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    first = client.patch("/api/photos/1", json={"rotate": "right"}).json()
    assert first["rotation"] == 90
    assert first["path"] == "/Volumes/share/1995 - Coast/a.jpg"
    again = client.patch("/api/photos/1", json={"rotate": "right"}).json()
    assert again["rotation"] == 180
    left = client.patch("/api/photos/1", json={"rotate": "left"}).json()
    assert left["rotation"] == 90
    hidden = client.patch("/api/photos/1", json={"hidden": True}).json()
    assert hidden["hidden"] is True
    listed = client.get("/api/photos").json()
    assert listed["total"] == 1
    assert listed["items"][0]["filename"] == "b.jpg"
    assert client.get("/api/photos/1").status_code == 404


def test_photo_neighbors_use_person_join(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, taken_at, width, height, created_at) VALUES (?,?,?,?,?,?)",
        ("/Volumes/share/1995 - Coast/c.jpg", "c", "2004-01-02T00:00:00", 100, 100, now_iso()),
    )
    conn.execute(
        "INSERT INTO people (id, name, created_at) VALUES (1, 'Pat', ?)",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    photo = client.get("/api/photos/1", params={"person_id": 1}).json()
    assert photo["id"] == 1
    assert photo["prev_id"] == 3
    assert photo["next_id"] is None
    lite = client.get("/api/photos/1", params={"person_id": 1, "lite": "true"}).json()
    assert lite["prev_id"] == 3
    assert lite["next_id"] is None
    assert lite["faces"][0]["suggestions"] == []


def test_box_iou_identical_is_one():
    from photosort.faces import box_iou

    box = (10.0, 10.0, 50.0, 80.0)
    assert box_iou(box, box) == 1.0
    assert box_iou(box, (200.0, 200.0, 210.0, 210.0)) == 0.0


def test_photo_lite_skips_suggestions(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    full = client.get("/api/photos/1").json()
    lite = client.get("/api/photos/1", params={"lite": "true"}).json()
    assert full["filename"] == "a.jpg"
    assert lite["filename"] == "a.jpg"
    assert lite["faces"][0]["suggestions"] == []


def test_pipeline_should_resume_after_interrupted_job(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod
    from photosort.jobs import create_job, update_job
    from photosort.util import now_iso

    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute("UPDATE photos SET scanned_at = ?", (now_iso(),))
    conn.commit()
    conn.close()
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    assert pipeline_mod.should_resume() is False
    job_id = create_job("pipeline")
    update_job(job_id, status="error", error="stale running job (worker not alive)", finished_at=now_iso())
    assert pipeline_mod.should_resume() is True


def test_resume_latest_restarts_failed_identify(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod
    from photosort.jobs import create_job, update_job

    _db(tmp_path, monkeypatch)
    job_id = create_job("identify")
    update_job(job_id, status="error", error="stale running job (worker not alive)", finished_at=now_iso())
    started = []

    def fake_start(job_type, fn):
        started.append(job_type)
        return {"id": 99, "type": job_type, "status": "queued"}

    monkeypatch.setattr(pipeline_mod, "start_job", fake_start)
    result = pipeline_mod.resume_latest()
    assert started == ["identify"]
    assert result["type"] == "identify"


def test_resume_latest_uses_library_when_pipeline_folders_missing(tmp_path, monkeypatch):
    from photosort import importer, pipeline as pipeline_mod
    from photosort.jobs import create_job, update_job

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    importer.set_library(album)
    job_id = create_job("pipeline")
    update_job(job_id, status="paused", message="Paused. Scanning 10 of 100 photos")
    started = []

    def fake_start(job_type, fn):
        started.append(job_type)
        return {"id": 101, "type": job_type, "status": "queued"}

    monkeypatch.setattr(pipeline_mod, "start_job", fake_start)
    result = pipeline_mod.resume_latest()
    assert pipeline_mod.remembered_folders() == [album.resolve()]
    assert started == ["pipeline"]
    assert result["type"] == "pipeline"


def test_resume_latest_restarts_paused_pipeline(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    from photosort.jobs import create_job, update_job

    job_id = create_job("pipeline")
    update_job(job_id, status="paused", message="Paused. Scanning 10 of 100 photos")
    started = []

    def fake_start(job_type, fn):
        started.append(job_type)
        return {"id": 100, "type": job_type, "status": "queued"}

    monkeypatch.setattr(pipeline_mod, "start_job", fake_start)
    result = pipeline_mod.resume_latest()
    assert started == ["pipeline"]
    assert result["type"] == "pipeline"


def test_paused_job_does_not_autoresume(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod
    from photosort.jobs import create_job, update_job

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    job_id = create_job("pipeline")
    update_job(job_id, status="paused", message="Paused. Scanning 10 of 100 photos")
    assert pipeline_mod.should_resume() is False


def test_settings_put_auto_update_does_not_clear_key(tmp_path, monkeypatch):
    from photosort import settings as settings_mod

    _db(tmp_path, monkeypatch)
    xdg = tmp_path / "config"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings_mod, "verify_xai_key", lambda key: {"ok": True})
    settings_mod.save_xai_key("xai-keep-across-settings-1")
    client = TestClient(app)
    out = client.put("/api/settings", json={"auto_update": True, "auto_scan_new": False}).json()
    assert out["auto_update"] is True
    assert settings_mod.saved_xai_key() == "xai-keep-across-settings-1"
    assert out["xai_key_set"] is True


def test_settings_put_auto_update_without_key(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    client = TestClient(app)
    out = client.put(
        "/api/settings",
        json={"auto_update": True, "auto_scan_new": False, "folders": [str(album)]},
    ).json()
    assert out["auto_update"] is True
    assert out["auto_scan_new"] is False
    shown = client.get("/api/settings").json()
    assert shown["auto_update"] is True
    assert shown["auto_scan_new"] is False


def test_maybe_auto_update_skips_when_disabled(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod, settings as settings_mod

    _db(tmp_path, monkeypatch)
    settings_mod.save_auto_update(auto_update=False)
    started = []
    monkeypatch.setattr(pipeline_mod, "start_job", lambda *a, **k: started.append(a) or {})
    assert pipeline_mod.maybe_auto_update() is None
    assert started == []


def test_maybe_auto_update_imports_without_scan_when_ai_off(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod, settings as settings_mod

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    settings_mod.save_auto_update(auto_update=True, auto_scan_new=False)
    started = []

    def fake_start(job_type, fn):
        started.append(job_type)
        return {"id": 7, "type": job_type, "status": "queued"}

    monkeypatch.setattr(pipeline_mod, "start_job", fake_start)
    result = pipeline_mod.maybe_auto_update()
    assert started == ["import"]
    assert result["type"] == "import"


def test_maybe_auto_update_scans_when_ai_on(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod, settings as settings_mod

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    settings_mod.save_auto_update(auto_update=True, auto_scan_new=True)
    started = []

    def fake_start(job_type, fn):
        started.append(job_type)
        return {"id": 8, "type": job_type, "status": "queued"}

    monkeypatch.setattr(pipeline_mod, "start_job", fake_start)
    result = pipeline_mod.maybe_auto_update()
    assert started == ["pipeline"]
    assert result["type"] == "pipeline"


def test_run_pipeline_can_skip_face_scan(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod, faces as faces_mod

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    scanned = []
    monkeypatch.setattr(pipeline_mod.importer, "import_folder", lambda *a, **k: {"added": 1})
    monkeypatch.setattr(faces_mod, "scan_pending", lambda job_id: scanned.append(job_id))
    pipeline_mod.run_pipeline(1, [album], scan=False)
    assert scanned == []
    pipeline_mod.run_pipeline(2, [album], scan=True)
    assert scanned == [2]


def test_run_pipeline_auto_update_skips_scan_when_caught_up(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod, faces as faces_mod
    from photosort.jobs import create_job
    from photosort.util import now_iso

    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute("UPDATE photos SET scanned_at = ?", (now_iso(),))
    conn.commit()
    conn.close()
    album = tmp_path / "heirlooms"
    album.mkdir()
    scanned = []
    monkeypatch.setattr(pipeline_mod.importer, "import_folder", lambda *a, **k: {"added": 0})
    monkeypatch.setattr(faces_mod, "scan_pending", lambda job_id: scanned.append(job_id))
    pipeline_mod.run_pipeline(
        create_job("pipeline"), [album], scan=True, faces_if_new_only=True
    )
    assert scanned == []
    pipeline_mod.run_pipeline(create_job("pipeline"), [album], scan=True)
    assert len(scanned) == 1


def test_should_resume_skips_code_crash(tmp_path, monkeypatch):
    from photosort import pipeline as pipeline_mod
    from photosort.jobs import create_job, update_job
    from photosort.util import now_iso

    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute("UPDATE photos SET scanned_at = ?", (now_iso(),))
    conn.commit()
    conn.close()
    album = tmp_path / "heirlooms"
    album.mkdir()
    pipeline_mod.remember_folders([album])
    job_id = create_job("pipeline")
    update_job(
        job_id,
        status="error",
        error="TypeError: 'NoneType' object is not callable",
        finished_at=now_iso(),
    )
    assert pipeline_mod.should_resume() is False


def test_scan_pending_stops_when_paused(tmp_path, monkeypatch):
    from photosort import faces as faces_mod
    from photosort.jobs import JobPaused, create_job

    _db(tmp_path, monkeypatch)
    monkeypatch.setattr(faces_mod, "get_analyzer", lambda: object())
    monkeypatch.setattr(faces_mod, "pause_requested", lambda: True)
    try:
        faces_mod.scan_pending(create_job("scan"))
        raised = False
    except JobPaused:
        raised = True
    assert raised
