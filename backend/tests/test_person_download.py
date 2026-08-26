from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from photosort import catalog, config, db, originals
from photosort.db import connect, init_db
from photosort.main import app
from photosort.people import (
    create_person,
    list_person_download_entries,
    person_zip_filename,
    unique_zip_name,
    write_person_photo_zip,
)
from photosort.util import now_iso


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "VIEW_DIR", data / "views")
    monkeypatch.setattr(config, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(catalog, "DB_PATH", path)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "backups").mkdir()
    (data / "views").mkdir()
    (data / "thumbs").mkdir()
    conn = connect()
    init_db(conn)
    return conn


def _jpeg(path: Path, color=(20, 80, 120)):
    Image.new("RGB", (40, 30), color).save(path, "JPEG")


def test_person_zip_filename_is_safe():
    assert person_zip_filename("Alex Reed") == "Alex-Reed-photos.zip"
    assert person_zip_filename("Alex Reed", labels=True) == "Alex-Reed-photos-labeled.zip"
    assert person_zip_filename("  ") == "person-photos.zip"


def test_unique_zip_name_disambiguates_collisions():
    used: set[str] = set()
    a = unique_zip_name(Path("/album/one/pic.jpg"), 1, used)
    b = unique_zip_name(Path("/album/two/pic.jpg"), 9, used)
    assert a == "pic.jpg"
    assert b == "pic-9.jpg"


def test_list_person_download_skips_junk_hidden_and_missing(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    keep = tmp_path / "keep.jpg"
    missing = tmp_path / "gone.jpg"
    hidden = tmp_path / "hid.jpg"
    _jpeg(keep)
    _jpeg(hidden)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(keep), "a", 40, 30, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(missing), "b", 40, 30, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, hidden, created_at) VALUES (?,?,?,?,?,?)",
        (str(hidden), "c", 40, 30, 1, now_iso()),
    )
    conn.commit()
    person = create_person("Pat Hall")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (3,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,20,0,30,10,0.4,'ok',?,'junk',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    listing = list_person_download_entries(person["id"])
    names = [item["arcname"] for item in listing["entries"]]
    assert names == ["keep.jpg"]
    assert listing["missing"] == 1
    assert listing["filename"] == "Pat-Hall-photos.zip"


def test_list_person_download_uses_local_view_when_original_is_offline(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    missing = tmp_path / "offline" / "trip.HEIC"
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(missing), "h", 40, 30, now_iso()),
    )
    conn.commit()
    person = create_person("Pat Hall")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    listing = list_person_download_entries(person["id"])
    assert listing["entries"] == []
    assert listing["missing"] == 1
    view = tmp_path / "data" / "views" / "1.jpg"
    _jpeg(view, (8, 9, 10))
    listing = list_person_download_entries(person["id"])
    assert len(listing["entries"]) == 1
    assert listing["entries"][0]["src"] == view
    assert listing["entries"][0]["arcname"] == "trip.jpg"
    assert listing["missing"] == 0
    dest = tmp_path / "offline.zip"
    built = write_person_photo_zip(person["id"], dest)
    with ZipFile(dest) as zf:
        assert zf.namelist() == ["trip.jpg"]


def test_write_person_photo_zip_one_file_per_picture(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    photo = tmp_path / "group.jpg"
    _jpeg(photo)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo), "g", 40, 30, now_iso()),
    )
    conn.commit()
    person = create_person("Sam Cole")
    conn = connect()
    for x in (0, 20):
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
               VALUES (1,?,?,?, ?,0.9,'ok',?,'manual',?)""",
            (x, 0, x + 10, 10, person["id"], now_iso()),
        )
    conn.commit()
    conn.close()
    dest = tmp_path / "out.zip"
    built = write_person_photo_zip(person["id"], dest)
    assert built["filename"] == "Sam-Cole-photos.zip"
    with ZipFile(dest) as zf:
        assert zf.namelist() == ["group.jpg"]


def test_write_person_photo_zip_with_labels_draws_names(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    photo = tmp_path / "group.jpg"
    _jpeg(photo, (10, 40, 80))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo), "g", 40, 30, now_iso()),
    )
    conn.commit()
    person = create_person("Sam Cole")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,2,2,18,18,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    dest = tmp_path / "labeled.zip"
    built = write_person_photo_zip(person["id"], dest, labels=True)
    assert built["filename"] == "Sam-Cole-photos-labeled.zip"
    with ZipFile(dest) as zf:
        assert zf.namelist() == ["group-labeled.jpg"]
        labeled = Image.open(BytesIO(zf.read("group-labeled.jpg"))).convert("RGB")
    original = Image.open(photo).convert("RGB")
    assert labeled.size[0] >= original.size[0]
    assert labeled.tobytes() != original.resize(labeled.size).tobytes()


def test_download_person_photos_http(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    one = tmp_path / "a.jpg"
    two = tmp_path / "b.jpg"
    _jpeg(one, (10, 20, 30))
    _jpeg(two, (40, 50, 60))
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(one), "a", 40, 30, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(two), "b", 40, 30, now_iso()),
    )
    conn.commit()
    person = create_person("Ada Cole")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (2,0,0,10,10,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    res = client.get(f"/api/people/{person['id']}/photos.zip")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")
    assert "Ada-Cole-photos.zip" in (res.headers.get("content-disposition") or "")
    zf = ZipFile(BytesIO(res.content))
    assert sorted(zf.namelist()) == ["a.jpg", "b.jpg"]
    missing = client.get("/api/people/99/photos.zip")
    assert missing.status_code == 404
