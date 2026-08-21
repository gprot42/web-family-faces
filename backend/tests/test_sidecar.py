import json
import shutil

from PIL import Image

from photosort import config, db, importer, originals, sidecar
from photosort.db import connect, init_db
from photosort.jobs import create_job
from photosort.people import assign_faces, create_person, junk_cluster, reset_names, set_face_comment
from photosort.photos import set_photo_comment, set_photo_tags
from photosort.util import now_iso


def _db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(config, "CROP_DIR", data / "crops")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    (data / "thumbs").mkdir()
    conn = connect()
    init_db(conn)
    conn.close()
    return data


def _photo(folder, name="shot.jpg", color="navy"):
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    Image.new("RGB", (80, 80), color).save(dest, "JPEG")
    return dest


def _add_face(conn, photo_id, box=(10, 10, 40, 50), person_id=None, how=None):
    conn.execute(
        """
        INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
        VALUES (?,?,?,?,?,0.9,'ok',?,?,?)
        """,
        (photo_id, *box, person_id, how, now_iso()),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def test_naming_writes_sidecar_and_leaves_original(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "2003 - Tokyo"
    photo = _photo(album)
    before = photo.read_bytes()
    before_mtime = photo.stat().st_mtime_ns
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    face_id = _add_face(conn, photo_id)
    conn.close()
    person = create_person("Sam")
    assign_faces([face_id], person["id"], "manual")
    dest = album / originals.SIDECAR_NAME
    assert dest.is_file()
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["app"] == config.APP_NAME
    assert payload["photos"]["shot.jpg"]["faces"][0]["name"] == "Sam"
    assert photo.read_bytes() == before
    assert photo.stat().st_mtime_ns == before_mtime


def test_copied_folder_restores_name_to_same_person(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    tokyo = tmp_path / "2003 - Tokyo"
    _photo(tokyo)
    importer.import_folder(create_job("import"), tokyo)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    face_id = _add_face(conn, photo_id, box=(8, 8, 42, 48))
    conn.close()
    person = create_person("Sam")
    assign_faces([face_id], person["id"], "manual")

    copied = tmp_path / "copy" / "2003 - Tokyo"
    shutil.copytree(tokyo, copied)
    importer.import_folder(create_job("import"), copied)
    assert (copied / originals.SIDECAR_NAME).is_file()
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{copied}%",)).fetchone()["id"]
    _add_face(conn, new_id, box=(9, 9, 41, 47))
    conn.close()
    applied = sidecar.apply_to_photos([new_id])
    assert applied["assigned"] == 1
    conn = connect()
    names = [r["name"] for r in conn.execute("SELECT name FROM people")]
    rows = conn.execute(
        """
        SELECT p.name, f.assigned_how
        FROM faces f JOIN people p ON p.id = f.person_id
        JOIN photos ph ON ph.id = f.photo_id
        WHERE ph.path LIKE ?
        """,
        (f"{copied}%",),
    ).fetchall()
    conn.close()
    assert names == ["Sam"]
    assert rows[0]["name"] == "Sam"
    assert rows[0]["assigned_how"] == "sidecar"


def test_reset_folder_keeps_sidecar(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "2001 - Vodka"
    _photo(album, "drink.jpg", "maroon")
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    face_id = _add_face(conn, photo_id)
    conn.close()
    assign_faces([face_id], create_person("Ada")["id"], "manual")
    sidecar = album / originals.SIDECAR_NAME
    assert sidecar.is_file()
    before = sidecar.read_text(encoding="utf-8")
    out = reset_names("2001 - Vodka")
    assert out["writes_sidecars"] is False
    assert sidecar.is_file()
    assert sidecar.read_text(encoding="utf-8") == before


def test_junk_is_stored_and_restored(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "statues"
    _photo(album, "bronze.jpg", "gray")
    importer.import_folder(create_job("import"), album)
    conn = connect()
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
        VALUES (?,?,?,?,?,0.9,'ok',1,?)
        """,
        (photo_id, 5, 5, 30, 30, now_iso()),
    )
    conn.commit()
    conn.close()
    junk_cluster(1)
    payload = json.loads((album / originals.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert payload["photos"]["bronze.jpg"]["faces"][0]["junk"] is True

    other = tmp_path / "statues-copy"
    shutil.copytree(album, other)
    importer.import_folder(create_job("import"), other)
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{other}%",)).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
        VALUES (?,?,?,?,?,0.9,'ok',?)
        """,
        (new_id, 5, 5, 30, 30, now_iso()),
    )
    conn.commit()
    conn.close()
    applied = sidecar.apply_to_photos([new_id])
    assert applied["junked"] == 1
    conn = connect()
    how = conn.execute(
        "SELECT assigned_how, quality FROM faces WHERE photo_id = ?", (new_id,)
    ).fetchone()
    conn.close()
    assert how["assigned_how"] == "junk"
    assert how["quality"] == "unidentifiable"


def test_label_tag_writes_sidecar_and_restores(tmp_path, monkeypatch):
    from photosort.people import set_face_tag

    _db(tmp_path, monkeypatch)
    album = tmp_path / "2010 - Garden"
    _photo(album, "group.jpg", "green")
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    face_id = _add_face(conn, photo_id)
    conn.close()
    person = create_person("Pat")
    assign_faces([face_id], person["id"], "manual")
    set_face_tag(face_id, 30, 20)
    payload = json.loads((album / originals.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert payload["photos"]["group.jpg"]["faces"][0]["tag"] == [0.3, 0.2]

    copied = tmp_path / "copy" / "2010 - Garden"
    shutil.copytree(album, copied)
    importer.import_folder(create_job("import"), copied)
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{copied}%",)).fetchone()["id"]
    new_face = _add_face(conn, new_id)
    conn.close()
    sidecar.apply_to_photos([new_id])
    conn = connect()
    row = conn.execute("SELECT tag_x, tag_y FROM faces WHERE id = ?", (new_face,)).fetchone()
    conn.close()
    assert round(row["tag_x"], 1) == 30
    assert round(row["tag_y"], 1) == 20


def test_face_comment_writes_sidecar_and_restores(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "2008 - Wedding"
    photo = _photo(album, "aisle.jpg", "ivory")
    before = photo.read_bytes()
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    face_id = _add_face(conn, photo_id, box=(8, 8, 42, 48))
    conn.close()
    assign_faces([face_id], create_person("Clifford")["id"], "manual")
    saved = set_face_comment(face_id, "holding the cake")
    assert saved["comment"] == "holding the cake"
    dest = album / originals.SIDECAR_NAME
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["photos"]["aisle.jpg"]["faces"][0]["comment"] == "holding the cake"
    assert photo.read_bytes() == before

    copied = tmp_path / "copy" / "2008 - Wedding"
    shutil.copytree(album, copied)
    importer.import_folder(create_job("import"), copied)
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{copied}%",)).fetchone()["id"]
    _add_face(conn, new_id, box=(9, 9, 41, 47))
    conn.close()
    sidecar.apply_to_photos([new_id])
    conn = connect()
    row = conn.execute(
        """
        SELECT f.comment, p.name
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        LEFT JOIN people p ON p.id = f.person_id
        WHERE ph.path LIKE ?
        """,
        (f"{copied}%",),
    ).fetchone()
    conn.close()
    assert row["comment"] == "holding the cake"
    assert row["name"] == "Clifford"


def test_comment_writes_sidecar_and_restores(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "2008 - Wedding"
    photo = _photo(album, "aisle.jpg", "ivory")
    before = photo.read_bytes()
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    conn.close()
    saved = set_photo_comment(photo_id, "Clifford at the back.")
    assert saved["comment"] == "Clifford at the back."
    dest = album / originals.SIDECAR_NAME
    assert dest.is_file()
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["photos"]["aisle.jpg"]["comment"] == "Clifford at the back."
    assert photo.read_bytes() == before

    copied = tmp_path / "copy" / "2008 - Wedding"
    shutil.copytree(album, copied)
    importer.import_folder(create_job("import"), copied)
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{copied}%",)).fetchone()["id"]
    conn.close()
    applied = sidecar.apply_to_photos([new_id])
    assert applied["commented"] == 1
    conn = connect()
    row = conn.execute("SELECT comment FROM photos WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    assert row["comment"] == "Clifford at the back."


def test_photo_tags_write_sidecar_and_restore(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "2008 - Wedding"
    photo = _photo(album, "aisle.jpg", "ivory")
    before = photo.read_bytes()
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    conn.close()
    saved = set_photo_tags(photo_id, ["Christmas", "family"])
    assert saved["tags"] == ["Christmas", "family"]
    dest = album / originals.SIDECAR_NAME
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["photos"]["aisle.jpg"]["tags"] == ["Christmas", "family"]
    assert photo.read_bytes() == before

    copied = tmp_path / "copy" / "2008 - Wedding"
    shutil.copytree(album, copied)
    importer.import_folder(create_job("import"), copied)
    conn = connect()
    new_id = conn.execute("SELECT id FROM photos WHERE path LIKE ?", (f"{copied}%",)).fetchone()["id"]
    conn.close()
    sidecar.apply_to_photos([new_id])
    conn = connect()
    tags = [row["tag"] for row in conn.execute("SELECT tag FROM photo_tags WHERE photo_id = ? ORDER BY tag", (new_id,))]
    conn.close()
    assert tags == ["Christmas", "family"]


def test_import_without_names_does_not_add_sidecar(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "empty-names"
    _photo(album)
    importer.import_folder(create_job("import"), album)
    assert list(album.iterdir()) == [album / "shot.jpg"]


def test_sidecar_write_refuses_other_album_files(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    try:
        originals.assert_sidecar_write(album / "notes.txt")
        raised = False
    except originals.OriginalWriteError:
        raised = True
    assert raised
    ok = originals.assert_sidecar_write(album / originals.SIDECAR_NAME)
    assert ok == album / originals.SIDECAR_NAME
