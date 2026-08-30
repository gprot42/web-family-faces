from pathlib import Path

from PIL import Image

from photosort import browse, catalog, config, db, importer, originals, state
from photosort.faces import looks_like_statue
from photosort.db import connect, init_db
from photosort.jobs import create_job
from photosort.people import assign_cluster, create_person
from photosort.util import file_sha256, now_iso


def _db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(config, "VIEW_DIR", data / "views")
    monkeypatch.setattr(config, "CROP_DIR", data / "crops")
    monkeypatch.setattr(config, "BACKUP_DIR", data / "backups")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(catalog, "DB_PATH", path)
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    (data / "backups").mkdir()
    (data / "thumbs").mkdir()
    (data / "views").mkdir()
    conn = connect()
    init_db(conn)
    conn.close()
    return data


def test_face_crop_is_square_and_sharpened(tmp_path, monkeypatch):
    from photosort import config, faces as faces_mod, originals
    from photosort.faces import save_crop

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", data)
    monkeypatch.setattr(faces_mod, "CROP_DIR", data)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    src = tmp_path / "face.jpg"
    Image.new("RGB", (400, 300), (180, 140, 110)).save(src, "JPEG")
    out = save_crop(src, 400, 300, (80, 40, 200, 200), 9)
    crop = Image.open(out)
    assert crop.size == (config.CROP_SIZE, config.CROP_SIZE)


def test_backup_catalog_is_gzip_and_restorable(tmp_path, monkeypatch):
    import gzip
    import sqlite3

    data = _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(tmp_path / "a.jpg"), "a", 10, 10, now_iso()),
    )
    conn.commit()
    conn.close()
    first = catalog.backup_catalog()
    assert first["compressed"] is True
    dest = Path(first["path"])
    assert dest.suffixes[-2:] == [".db", ".gz"] or dest.name.endswith(".db.gz")
    assert dest.is_file()
    raw = data / "restored.db"
    raw.write_bytes(gzip.decompress(dest.read_bytes()))
    restored = sqlite3.connect(str(raw))
    n = restored.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    restored.close()
    assert n == 1
    skipped = catalog.maybe_backup(min_age_seconds=3600)
    assert skipped.get("skipped") is True
    forced = catalog.maybe_backup(min_age_seconds=3600, force=True)
    assert forced.get("skipped") is not True
    assert Path(forced["path"]).is_file()


def test_prune_backups_drops_uncompressed_once_gzip_exists(tmp_path, monkeypatch):
    data = _db(tmp_path, monkeypatch)
    loose = data / "backups" / "photosort-2026-01-01T000000Z.db"
    loose.write_bytes(b"sqlite-fake")
    catalog.backup_catalog()
    assert not loose.exists()
    gz = list((data / "backups").glob("photosort-*.db.gz"))
    assert len(gz) == 1


def test_iso_from_stat_skips_overflow():
    assert originals._iso_from_stat(1_700_000_000).startswith("2023-")
    assert originals._iso_from_stat(1e20) is None
    assert originals._iso_from_stat(float("nan")) is None
    assert originals._iso_from_stat(float("inf")) is None
    assert originals._iso_from_stat("not-a-time") is None


def test_read_photo_clues_does_not_rewrite_original(tmp_path):
    photo = tmp_path / "shot.jpg"
    img = Image.new("RGB", (24, 24), "navy")
    exif = Image.Exif()
    exif[306] = "2003:12:02 03:58:08"
    img.save(photo, "JPEG", exif=exif)
    before = photo.read_bytes()
    before_mtime = photo.stat().st_mtime_ns
    clues = originals.read_photo_clues(photo)
    assert clues["filename"] == "shot.jpg"
    assert clues["exif_taken_at"]
    assert photo.read_bytes() == before
    assert photo.stat().st_mtime_ns == before_mtime


def test_preview_folder_is_detected():
    assert originals.is_preview_path("/album/1024 x 768/shot.jpg")
    assert originals.is_preview_path("/album/1994 - Trip 1024 x 768/shot.jpg")
    assert originals.is_preview_path("/album/1994 - Lake-1024x768/shot.jpg")
    assert not originals.is_preview_path("/album/1994 - Harbor/shot.jpg")
    assert originals.is_preview_dir_name("1994 - Woods - Hike 1024x768")
    assert not originals.is_preview_dir_name("1994 - Harbor")
    kept = originals.drop_preview_rows(
        [
            {"path": "/album/1024 x 768/DSC00267.jpg", "id": 1},
            {"path": "/album/DSC00267.JPG", "id": 2},
        ]
    )
    assert [r["id"] for r in kept] == [2]


def test_browse_volumes_token_is_not_a_full_path():
    listing = browse.list_folder("volumes")
    assert listing["path"] == "volumes"
    assert listing["parent"] is None
    assert listing.get("error") in (None, "")
    names = {entry["name"] for entry in listing["entries"]}
    assert "Macintosh HD" not in names


def test_browse_relative_volumes_is_still_nas_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listing = browse.list_folder("volumes")
    assert listing["path"] == "volumes"
    assert listing.get("error") in (None, "")


def test_browse_volumes_hides_local_and_remembers_unmounted(tmp_path, monkeypatch):
    from photosort import config, db
    from photosort.db import connect, init_db
    from photosort.util import now_iso

    data = tmp_path / "data"
    data.mkdir()
    vols = tmp_path / "Volumes"
    (vols / "Macintosh HD").mkdir(parents=True)
    (vols / "USB").mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    monkeypatch.setattr(browse, "_volumes_dir", lambda: vols)

    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO library (id, folder, decade_override, updated_at) VALUES (1, ?, NULL, ?)",
        ("/Volumes/photos_share/Photo_Collection", now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?, ?, ?, ?, ?)",
        ("/Volumes/photos_share/Photo_Collection/a.jpg", "x", 1, 1, now_iso()),
    )
    conn.commit()
    conn.close()

    listing = browse.list_folder("volumes")
    names = {entry["name"] for entry in listing["entries"]}
    assert "Macintosh HD" not in names
    assert "USB" in names
    assert "photos_share" in names
    remembered = next(item for item in listing["entries"] if item["name"] == "photos_share")
    assert remembered["mounted"] is False
    assert "Not mounted" in (remembered.get("error") or "")


def test_restore_people_marked_junk_keeps_statues(tmp_path, monkeypatch):
    from photosort import config, db, faces as faces_mod, originals
    from photosort.db import connect, init_db
    from photosort.faces import restore_people_marked_junk
    from photosort.util import now_iso

    data = tmp_path / "data"
    crops = data / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(faces_mod, "CROP_DIR", crops)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/a.jpg", "a", 100, 100, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, assigned_how, created_at)
           VALUES (1,1,0,0,10,10,0.9,'unidentifiable','junk',?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, assigned_how, created_at)
           VALUES (2,1,0,0,10,10,0.9,'unidentifiable','junk',?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (80, 80), (190, 140, 100)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (80, 80), (110, 145, 105)).save(crops / "2.jpg", "JPEG")
    n = restore_people_marked_junk()
    assert n == 1
    conn = connect()
    rows = {r["id"]: r["assigned_how"] for r in conn.execute("SELECT id, assigned_how FROM faces")}
    conn.close()
    assert rows[1] is None
    assert rows[2] == "junk"


def test_looks_like_statue_keeps_bw_family_prints(tmp_path):
    gray = tmp_path / "gray.jpg"
    bw_photo = tmp_path / "scan.jpg"
    Image.new("RGB", (80, 80), (120, 120, 120)).save(gray, "JPEG")
    Image.new("RGB", (160, 120), (110, 110, 110)).save(bw_photo, "JPEG")
    assert looks_like_statue(gray, bw_photo) is False


def test_sweep_hides_statue_majority_cluster(tmp_path, monkeypatch):
    from photosort import config, db, faces as faces_mod, originals
    from photosort.faces import sweep_statues
    from photosort.util import now_iso

    data = tmp_path / "data"
    crops = data / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(faces_mod, "CROP_DIR", crops)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    conn = connect()
    init_db(conn)
    garden = tmp_path / "garden.jpg"
    Image.new("RGB", (200, 160), (30, 150, 40)).save(garden, "JPEG")
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(garden), "g", 200, 160, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    for i in (1, 2, 3):
        conn.execute(
            """INSERT INTO faces (id, photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
               VALUES (?,?,0,0,10,10,0.9,'ok',1,?)""",
            (i, 1, now_iso()),
        )
        Image.new("RGB", (80, 80), (120, 120, 120) if i < 3 else (110, 145, 105)).save(crops / f"{i}.jpg", "JPEG")
    conn.commit()
    conn.close()
    n = sweep_statues()
    assert n >= 2
    conn = connect()
    leftover = conn.execute(
        "SELECT COUNT(*) AS n FROM faces WHERE quality = 'ok' AND person_id IS NULL"
    ).fetchone()["n"]
    conn.close()
    assert leftover == 0


def test_sweep_unassigns_auto_named_gold_statue(tmp_path, monkeypatch):
    from photosort import config, db, faces as faces_mod, originals
    from photosort.faces import sweep_statues
    from photosort.people import create_person
    from photosort.util import now_iso

    data = tmp_path / "data"
    crops = data / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(faces_mod, "CROP_DIR", crops)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/temple.jpg", "t", 200, 160, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Sam")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,40,40,0.9,'ok',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,50,0,90,40,0.9,'ok',?,'manual',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (80, 80), (176, 132, 40)).save(crops / "1.jpg", "JPEG")
    Image.new("RGB", (80, 80), (190, 140, 100)).save(crops / "2.jpg", "JPEG")
    n = sweep_statues()
    assert n >= 1
    conn = connect()
    rows = {r["id"]: r for r in conn.execute("SELECT id, person_id, assigned_how, quality FROM faces")}
    conn.close()
    assert rows[1]["assigned_how"] == "junk"
    assert rows[1]["person_id"] is None
    assert rows[2]["assigned_how"] == "manual"
    assert rows[2]["person_id"] == person["id"]


def test_sweep_hides_auto_named_unidentifiable_statue(tmp_path, monkeypatch):
    from photosort import config, db, faces as faces_mod, originals
    from photosort.faces import sweep_statues
    from photosort.people import create_person
    from photosort.util import now_iso

    data = tmp_path / "data"
    crops = data / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(faces_mod, "CROP_DIR", crops)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/mural.jpg", "m", 200, 160, now_iso()),
    )
    conn.commit()
    conn.close()
    person = create_person("Sam")
    conn = connect()
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, person_id, assigned_how, created_at)
           VALUES (1,0,0,20,20,0.7,'unidentifiable',?,'auto',?)""",
        (person["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    Image.new("RGB", (80, 80), (176, 132, 40)).save(crops / "1.jpg", "JPEG")
    assert sweep_statues() >= 1
    conn = connect()
    row = conn.execute("SELECT person_id, assigned_how, quality FROM faces WHERE id = 1").fetchone()
    conn.close()
    assert row["person_id"] is None
    assert row["assigned_how"] == "junk"


def test_looks_like_statue_skips_skin_and_gray(tmp_path):
    bronze = tmp_path / "bronze.jpg"
    skin = tmp_path / "skin.jpg"
    gray = tmp_path / "gray.jpg"
    colour_photo = tmp_path / "garden.jpg"
    Image.new("RGB", (80, 80), (110, 145, 105)).save(bronze, "JPEG")
    Image.new("RGB", (80, 80), (190, 140, 100)).save(skin, "JPEG")
    Image.new("RGB", (80, 80), (120, 120, 120)).save(gray, "JPEG")
    Image.new("RGB", (160, 120), (40, 140, 50)).save(colour_photo, "JPEG")
    gold = tmp_path / "gold-buddha.jpg"
    Image.new("RGB", (80, 80), (176, 132, 40)).save(gold, "JPEG")
    pale = tmp_path / "pale-portrait.jpg"
    Image.new("RGB", (80, 80), (174, 145, 126)).save(pale, "JPEG")
    assert looks_like_statue(bronze) is True
    assert looks_like_statue(gold) is True
    assert looks_like_statue(skin) is False
    assert looks_like_statue(pale) is False
    assert looks_like_statue(gray) is False
    assert looks_like_statue(gray, colour_photo) is True


def test_looks_like_statue_dark_bronze_against_colour_sky(tmp_path):
    """Outdoor bronze Buddha heads are gray metal in a colour landscape."""
    import numpy as np

    crop = np.full((80, 80, 3), (48, 52, 58), dtype=np.uint8)
    bronze = tmp_path / "buddha.png"
    Image.fromarray(crop).save(bronze)
    sky = np.zeros((120, 200, 3), dtype=np.uint8)
    sky[:70] = (90, 150, 210)
    sky[70:] = (30, 110, 40)
    scene = tmp_path / "lantau.png"
    Image.fromarray(sky).save(scene)
    bw = tmp_path / "scan.png"
    Image.new("RGB", (200, 120), (110, 110, 110)).save(bw)
    assert looks_like_statue(bronze, scene) is True
    assert looks_like_statue(bronze, bw) is False


def test_looks_like_statue_bronze_head_on_blue_sky(tmp_path):
    """A tight bronze-head crop can be mostly sky, so gray_n is too low for the gray-metal rule."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (90, 150, 210)
    crop[10:74, 14:70] = (70, 78, 95)
    bronze = tmp_path / "buddha-sky.png"
    Image.fromarray(crop).save(bronze)
    sky = np.zeros((120, 200, 3), dtype=np.uint8)
    sky[:70] = (90, 150, 210)
    sky[70:] = (30, 110, 40)
    scene = tmp_path / "lantau.png"
    Image.fromarray(sky).save(scene)
    bw = tmp_path / "scan.png"
    Image.new("RGB", (200, 120), (110, 110, 110)).save(bw)
    portrait = np.zeros((80, 80, 3), dtype=np.uint8)
    portrait[:] = (90, 150, 210)
    portrait[10:74, 14:70] = (190, 140, 100)
    face = tmp_path / "sky-portrait.png"
    Image.fromarray(portrait).save(face)
    assert looks_like_statue(bronze, scene) is True
    assert looks_like_statue(bronze, bw) is False
    assert looks_like_statue(face, scene) is False


def test_looks_like_statue_bronze_head_on_blown_white_sky(tmp_path):
    """Close bronze crops against overexposed sky have little blue, so gray_n stays low."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (220, 224, 230)
    crop[10:74, 14:70] = (70, 78, 95)
    bronze = tmp_path / "buddha-white.png"
    Image.fromarray(crop).save(bronze)
    sky = np.zeros((120, 200, 3), dtype=np.uint8)
    sky[:70] = (210, 220, 230)
    sky[70:] = (30, 110, 40)
    scene = tmp_path / "lantau-bright.png"
    Image.fromarray(sky).save(scene)
    assert looks_like_statue(bronze, scene) is True


def test_looks_like_statue_orange_gold_leaf_in_a_temple_photo(tmp_path):
    """Orange gold-leaf (R>>G) is metal, not skin, when the scene is a gold object."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (165, 115, 48)
    crop[42:] = (148, 98, 36)
    crop[10:18, 18:62] = (214, 176, 68)
    for y in range(0, 80, 3):
        crop[y] = (138, 92, 32)
    leaf = tmp_path / "gold-leaf.png"
    Image.fromarray(crop).save(leaf)
    scene = np.full((120, 200, 3), (36, 120, 48), dtype=np.uint8)
    scene[40:80, 70:150] = (165, 115, 48)
    temple = tmp_path / "temple.png"
    Image.fromarray(scene).save(temple)
    tungsten = tmp_path / "indoor.png"
    Image.new("RGB", (200, 120), (165, 115, 48)).save(tungsten)
    assert looks_like_statue(leaf, temple) is True
    assert looks_like_statue(leaf, tungsten) is False
    assert looks_like_statue(leaf) is False


def test_looks_like_statue_gilded_gondola_ornament_is_not_a_face(tmp_path):
    """A gold canal ornament in a bright mixed scene is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (168, 128, 72), dtype=np.uint8)
    for y in range(0, 80, 6):
        crop[y : y + 2] = (150, 112, 58)
    crop[26:39, 22:50] = (200, 130, 80)
    ferro = tmp_path / "ferro.png"
    Image.fromarray(crop).save(ferro)
    scene = np.zeros((120, 200, 3), dtype=np.uint8)
    scene[:30] = (240, 200, 160)
    scene[30:100] = (168, 128, 72)
    scene[100:] = (36, 34, 32)
    scene[100:, :50] = (120, 118, 116)
    canal = tmp_path / "canal.png"
    Image.fromarray(scene).save(canal)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    tungsten = tmp_path / "indoor.png"
    Image.new("RGB", (200, 120), (165, 115, 48)).save(tungsten)
    assert looks_like_statue(ferro, canal) is True
    assert looks_like_statue(ferro) is False
    assert looks_like_statue(ferro, tungsten) is False
    assert looks_like_statue(portrait, canal) is False


def test_looks_like_statue_painted_temple_relief(tmp_path):
    """Gold headdress + brown painted face on stone architecture is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (85, 62, 55)
    crop[:24] = (165, 115, 48)
    for y in range(0, 24, 3):
        crop[y, :80] = (138, 92, 32)
    crop[56:] = (36, 32, 28)
    relief = tmp_path / "relief.png"
    Image.fromarray(crop).save(relief)
    scene = np.full((120, 200, 3), (128, 128, 128), dtype=np.uint8)
    scene[40:80, 70:150] = (165, 115, 48)
    temple = tmp_path / "temple-gate.png"
    Image.fromarray(scene).save(temple)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(relief, temple) is True
    assert looks_like_statue(portrait, temple) is False


def test_looks_like_statue_sand_sculpture_in_a_beach_photo(tmp_path):
    """Grainy beige sand art in a mixed beach scene is not a person."""
    import numpy as np

    rng = np.random.default_rng(0)
    grain = rng.normal(0, 8, (80, 80, 1))
    chroma_noise = rng.normal(0, 4, (80, 80, 3))
    crop = np.clip(np.array([148, 132, 116], dtype=np.float32) + grain + chroma_noise, 0, 255).astype(
        np.uint8
    )
    sand = tmp_path / "sand.png"
    Image.fromarray(crop).save(sand)
    scene = np.zeros((120, 200, 3), dtype=np.uint8)
    scene[:50] = (90, 150, 210)
    scene[50:95, 30:170] = (130, 116, 102)
    scene[95:] = (40, 140, 50)
    beach = tmp_path / "beach.png"
    Image.fromarray(scene).save(beach)
    sepia = tmp_path / "sepia-print.png"
    Image.new("RGB", (200, 120), (140, 124, 110)).save(sepia)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(sand, beach) is True
    assert looks_like_statue(sand) is False
    assert looks_like_statue(sand, sepia) is False
    assert looks_like_statue(portrait, beach) is False


def test_looks_like_statue_limestone_stele_in_a_museum(tmp_path):
    """Carved beige stone in a grey museum room is not a person."""
    import numpy as np

    rng = np.random.default_rng(1)
    crop = np.clip(
        np.array([122, 108, 92], dtype=np.float32) + rng.normal(0, 5, (80, 80, 3)),
        0,
        255,
    ).astype(np.uint8)
    for y in range(0, 80, 3):
        crop[y] = (102, 92, 78)
    stone = tmp_path / "stele.png"
    Image.fromarray(crop).save(stone)
    scene = np.full((120, 200, 3), (120, 118, 116), dtype=np.uint8)
    scene[15:105, 50:149] = (122, 108, 92)
    museum = tmp_path / "museum.png"
    Image.fromarray(scene).save(museum)
    bw = tmp_path / "scan.png"
    Image.new("RGB", (200, 120), (120, 120, 120)).save(bw)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(stone, museum) is True
    assert looks_like_statue(stone) is False
    assert looks_like_statue(stone, bw) is False
    assert looks_like_statue(portrait, museum) is False


def test_looks_like_statue_dark_cave_relief_is_not_a_person(tmp_path):
    """A near-black carved cave face that matches the wall is not a person."""
    import numpy as np

    rng = np.random.default_rng(4)
    crop = np.clip(
        np.array([38, 30, 22], dtype=np.float32) + rng.normal(0, 4, (80, 80, 3)),
        0,
        255,
    ).astype(np.uint8)
    for y in range(0, 80, 4):
        crop[y] = (28, 22, 16)
    relief = tmp_path / "cave-face.png"
    Image.fromarray(crop).save(relief)
    scene = np.clip(
        np.array([40, 32, 23], dtype=np.float32) + rng.normal(0, 4, (120, 200, 3)),
        0,
        255,
    ).astype(np.uint8)
    scene[104:118, 118:148] = (210, 210, 205)
    cave = tmp_path / "cave.png"
    Image.fromarray(scene).save(cave)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(relief, cave) is True
    assert looks_like_statue(relief) is False
    assert looks_like_statue(portrait, cave) is False


def test_looks_like_statue_ochre_temple_wall_is_not_a_face(tmp_path):
    """A detector box on painted sandstone is not a person."""
    import numpy as np

    rng = np.random.default_rng(2)
    crop = np.clip(
        np.array([180, 131, 79], dtype=np.float32) + rng.normal(0, 6, (80, 80, 3)),
        0,
        255,
    ).astype(np.uint8)
    for y in range(0, 80, 3):
        crop[y] = (150, 110, 60)
    wall = tmp_path / "relief.png"
    Image.fromarray(crop).save(wall)
    scene = np.clip(
        np.array([175, 125, 75], dtype=np.float32) + rng.normal(0, 10, (120, 200, 3)),
        0,
        255,
    ).astype(np.uint8)
    temple = tmp_path / "temple-wall.png"
    Image.fromarray(scene).save(temple)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(wall, temple) is True
    assert looks_like_statue(wall) is False
    assert looks_like_statue(portrait, temple) is False


def test_looks_like_statue_marble_idol_with_gold_halo(tmp_path):
    """A painted white-marble temple idol is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (92, 84, 80)
    crop[:22] = (165, 115, 48)
    for y in range(0, 22, 3):
        crop[y] = (138, 92, 32)
    crop[32:58, 18:62] = (110, 102, 98)
    idol = tmp_path / "idol.png"
    Image.fromarray(crop).save(idol)
    scene = np.full((120, 200, 3), (130, 128, 126), dtype=np.uint8)
    scene[18:100, 72:138] = (100, 92, 88)
    scene[18:40, 80:130] = (165, 115, 48)
    scene[10:50, 10:55] = (165, 115, 48)
    scene[70:92, 40:70] = (170, 40, 50)
    scene[80:110, 150:190] = (180, 45, 55)
    shrine = tmp_path / "shrine.png"
    Image.fromarray(scene).save(shrine)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(idol, shrine) is True
    assert looks_like_statue(idol) is False
    assert looks_like_statue(portrait, shrine) is False


def test_looks_like_statue_fresco_putto_on_a_wall_map(tmp_path):
    """A painted cartouche face on a gilt-framed map wall is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (48, 78, 168)
    crop[:, :12] = (52, 122, 72)
    crop[22:58, 24:58] = (160, 138, 130)
    for y in range(24, 56, 4):
        crop[y, 24:58] = (148, 128, 120)
    putto = tmp_path / "map-putto.png"
    Image.fromarray(crop).save(putto)
    scene = np.zeros((160, 220, 3), dtype=np.uint8)
    scene[:] = (186, 148, 68)
    scene[20:140, 26:194] = (42, 78, 168)
    scene[58:108, 96:150] = (62, 128, 72)
    gallery = tmp_path / "map-gallery.png"
    Image.fromarray(scene).save(gallery)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    dusk = np.full((160, 220, 3), (70, 90, 140), dtype=np.uint8)
    dusk[70:150, 20:200] = (120, 90, 70)
    dusk[80:130, 40:80] = (190, 140, 100)
    garden = tmp_path / "dusk-garden.png"
    Image.fromarray(dusk).save(garden)
    train = np.full((160, 220, 3), (90, 110, 150), dtype=np.uint8)
    train[:, 140:] = (200, 160, 50)
    train[40:120, 70:130] = (190, 140, 100)
    carriage = tmp_path / "train.png"
    Image.fromarray(train).save(carriage)
    assert looks_like_statue(putto, gallery) is True
    assert looks_like_statue(putto) is False
    assert looks_like_statue(portrait, gallery) is False
    assert looks_like_statue(putto, garden) is False
    assert looks_like_statue(putto, carriage) is False
    assert looks_like_statue(portrait, garden) is False
    assert looks_like_statue(portrait, carriage) is False


def test_looks_like_statue_terracotta_bust_against_a_gallery_window(tmp_path):
    """A carved terracotta head against a window in a gilt map hall is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (150, 120, 95)
    crop[32:50, 40:58] = (176, 118, 112)
    crop[:, :36] = (186, 198, 220)
    bust = tmp_path / "terracotta-bust.png"
    Image.fromarray(crop).save(bust)
    scene = np.zeros((160, 220, 3), dtype=np.uint8)
    scene[:] = (186, 148, 68)
    scene[20:140, 26:194] = (70, 120, 72)
    gallery = tmp_path / "map-hall.png"
    Image.fromarray(scene).save(gallery)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    window_face = np.zeros((80, 80, 3), dtype=np.uint8)
    window_face[:] = (190, 140, 100)
    window_face[:, :24] = (92, 148, 210)
    person = tmp_path / "window-portrait.png"
    Image.fromarray(window_face).save(person)
    dusk = np.full((160, 220, 3), (70, 110, 180), dtype=np.uint8)
    dusk[110:160, :] = (80, 120, 70)
    garden = tmp_path / "garden.png"
    Image.fromarray(dusk).save(garden)
    assert looks_like_statue(bust, gallery) is True
    assert looks_like_statue(bust) is False
    assert looks_like_statue(portrait, gallery) is False
    assert looks_like_statue(person, gallery) is False
    assert looks_like_statue(bust, garden) is False


def test_looks_like_statue_painted_mural_figure_is_not_a_person(tmp_path):
    """A gold-crowned face painted on a temple mural is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (180, 130, 100)
    crop[:22] = (168, 136, 48)
    mural = tmp_path / "mural-face.png"
    Image.fromarray(crop).save(mural)
    scene = np.full((120, 200, 3), (150, 130, 105), dtype=np.uint8)
    scene[12:75, 25:175] = (160, 120, 70)
    scene[12:28, 25:175] = (90, 150, 210)
    scene[28:42, 40:160] = (40, 120, 50)
    panel = tmp_path / "mural-panel.png"
    Image.fromarray(scene).save(panel)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(mural, panel) is True
    assert looks_like_statue(mural) is False
    assert looks_like_statue(portrait, panel) is False


def test_looks_like_statue_hatched_bronze_painting_on_a_gallery_wall(tmp_path):
    """A drawn bronze Buddha in a frame on a grey wall is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (78, 70, 64), dtype=np.uint8)
    for y in range(0, 80, 2):
        crop[y] = (48, 42, 38)
    crop[:22] = (150, 110, 50)
    drawn = tmp_path / "buddha-paint.png"
    Image.fromarray(crop).save(drawn)
    scene = np.full((120, 200, 3), (145, 146, 148), dtype=np.uint8)
    scene[15:105, 40:170] = (90, 70, 50)
    scene[15:40, 40:170] = (150, 110, 50)
    gallery = tmp_path / "gallery.png"
    Image.fromarray(scene).save(gallery)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(drawn, gallery) is True
    assert looks_like_statue(drawn) is False
    assert looks_like_statue(portrait, gallery) is False


def test_looks_like_statue_slide_badge_is_not_a_face(tmp_path):
    """A labelled org-chart circle on a projected slide is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (168, 70, 56), dtype=np.uint8)
    crop[:8] = (200, 198, 194)
    crop[-8:] = (200, 198, 194)
    crop[:, :6] = (200, 198, 194)
    crop[:, -6:] = (200, 198, 194)
    crop[32:48, 20:60] = (248, 196, 180)
    badge = tmp_path / "sm-badge.png"
    Image.fromarray(crop).save(badge)
    scene = np.full((120, 200, 3), (212, 210, 208), dtype=np.uint8)
    scene[14:50, 24:90] = (200, 95, 70)
    scene[74:108, 120:180] = (210, 140, 60)
    slide = tmp_path / "slide.png"
    Image.fromarray(scene).save(slide)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(badge, slide) is True
    assert looks_like_statue(badge) is False
    assert looks_like_statue(portrait, slide) is False


def test_looks_like_statue_museum_plaque_mosaic_is_not_a_face(tmp_path):
    """A mosaic face printed on a museum information plaque is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (160, 110, 95), dtype=np.uint8)
    for y in range(0, 80, 5):
        crop[y] = (142, 92, 80)
    crop[:5] = (200, 198, 194)
    crop[-5:] = (200, 198, 194)
    crop[:, :5] = (200, 198, 194)
    crop[:, -5:] = (200, 198, 194)
    mosaic = tmp_path / "plaque-mosaic.png"
    Image.fromarray(crop).save(mosaic)
    scene = np.full((120, 200, 3), (152, 150, 148), dtype=np.uint8)
    scene[12:108, 20:120] = (220, 218, 214)
    scene[15:95, 125:195] = (155, 108, 92)
    scene[108:120, :] = (165, 157, 150)
    plaque = tmp_path / "museum-sign.png"
    Image.fromarray(scene).save(plaque)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(mosaic, plaque) is True
    assert looks_like_statue(mosaic) is False
    assert looks_like_statue(portrait, plaque) is False


def test_looks_like_statue_red_stone_relief_is_not_a_face(tmp_path):
    """A carved red porphyry battle relief is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (100, 78, 82), dtype=np.uint8)
    for y in range(0, 80, 2):
        crop[y] = (72, 54, 58)
    relief = tmp_path / "porphyry-head.png"
    Image.fromarray(crop).save(relief)
    scene = np.full((120, 200, 3), (108, 70, 74), dtype=np.uint8)
    scene[10:110, 60:140] = (96, 62, 66)
    wall = tmp_path / "relief-wall.png"
    Image.fromarray(scene).save(wall)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    glossy = np.full((80, 80, 3), (132, 97, 100), dtype=np.uint8)
    for y in range(0, 80, 2):
        glossy[y] = (96, 70, 74)
    rider = tmp_path / "porphyry-rider.png"
    Image.fromarray(glossy).save(rider)
    assert looks_like_statue(relief, wall) is True
    assert looks_like_statue(rider, wall) is True
    assert looks_like_statue(relief) is False
    assert looks_like_statue(rider) is False
    assert looks_like_statue(portrait, wall) is False


def test_looks_like_statue_movie_poster_on_a_street_is_not_a_face(tmp_path):
    """A painted cartoon on a restaurant poster strip is not a person."""
    import numpy as np

    crop = np.full((80, 80, 3), (232, 228, 222), dtype=np.uint8)
    crop[:, :18] = (140, 48, 40)
    crop[:, 62:] = (140, 48, 40)
    crop[16:64, 16:64] = (176, 138, 72)
    crop[28:52, 28:52] = (200, 118, 88)
    for x in range(0, 80, 5):
        crop[2:12, x : x + 2] = (20, 18, 16)
    for y in range(20, 60, 8):
        crop[y, 20:60] = (160, 124, 64)
    poster = tmp_path / "poster-face.png"
    Image.fromarray(crop).save(poster)
    scene = np.full((120, 200, 3), (150, 151, 152), dtype=np.uint8)
    scene[:36] = (90, 150, 210)
    scene[36:52] = (70, 128, 122)
    street = tmp_path / "street.png"
    Image.fromarray(scene).save(street)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(poster, street) is True
    assert looks_like_statue(poster) is False
    assert looks_like_statue(portrait, street) is False


def test_looks_like_statue_egyptian_ka_statue_in_a_museum(tmp_path):
    """Gold nemes around a black painted face in a museum hall is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (48, 50, 52)
    crop[:, :22] = (165, 115, 48)
    crop[:, 58:] = (165, 115, 48)
    crop[:16] = (165, 115, 48)
    crop[28:52, 30:50] = (36, 38, 40)
    crop[34:40, 36:44] = (250, 250, 248)
    for y in range(0, 80, 3):
        crop[y, :22] = (120, 82, 28)
        crop[y, 58:] = (120, 82, 28)
    ka = tmp_path / "ka.png"
    Image.fromarray(crop).save(ka)
    scene = np.full((120, 200, 3), (168, 164, 156), dtype=np.uint8)
    scene[:40] = (200, 196, 188)
    scene[90:] = (132, 118, 96)
    scene[18:102, 78:122] = (165, 115, 48)
    scene[28:92, 88:112] = (40, 42, 44)
    museum = tmp_path / "cairo-museum.png"
    Image.fromarray(scene).save(museum)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    gold_hat = np.zeros((80, 80, 3), dtype=np.uint8)
    gold_hat[:] = (190, 140, 100)
    gold_hat[:, :18] = (165, 115, 48)
    gold_hat[:, 62:] = (165, 115, 48)
    hat = tmp_path / "gold-hat.png"
    Image.fromarray(gold_hat).save(hat)
    assert looks_like_statue(ka, museum) is True
    assert looks_like_statue(ka) is False
    assert looks_like_statue(portrait, museum) is False
    assert looks_like_statue(hat, museum) is False


def test_looks_like_statue_painted_gold_temple_face(tmp_path):
    """Gold-painted guardian face in a mixed temple scene is not a person."""
    import numpy as np

    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = (160, 120, 48)
    for y in range(0, 80, 3):
        crop[y] = (140, 100, 36)
    crop[70:] = (70, 42, 28)
    face = tmp_path / "guardian.png"
    Image.fromarray(crop).save(face)
    scene = np.full((120, 200, 3), (210, 205, 195), dtype=np.uint8)
    scene[50:90, 80:120] = (160, 120, 48)
    scene[:, 160:] = (120, 90, 70)
    temple = tmp_path / "shrine.png"
    Image.fromarray(scene).save(temple)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (80, 80), (190, 140, 100)).save(portrait)
    assert looks_like_statue(face, temple) is True
    assert looks_like_statue(portrait, temple) is False


def test_browse_unmounted_path_lists_catalog_albums(tmp_path, monkeypatch):
    from photosort import config, db
    from photosort.db import connect, init_db
    from photosort.util import now_iso

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "t.db")
    monkeypatch.setattr(db, "DB_PATH", data / "t.db")
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/photos_share/Photo_Collection/1994 - Holiday/a.jpg", "a", 1, 1, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/Volumes/photos_share/Photo_Collection/1995 - Home/b.jpg", "b", 1, 1, now_iso()),
    )
    conn.commit()
    conn.close()

    missing = browse.list_folder("/Volumes/no_such_share")
    assert missing["entries"] == []
    assert missing.get("from_catalog") is False

    listing = browse.list_folder("/Volumes/photos_share")
    names = {entry["name"] for entry in listing["entries"]}
    assert "Photo_Collection" in names
    assert listing["from_catalog"] is True
    inner = browse.list_folder("/Volumes/photos_share/Photo_Collection")
    inner_names = {entry["name"] for entry in inner["entries"]}
    assert "1994 - Holiday" in inner_names
    assert "1995 - Home" in inner_names


def test_browse_lists_only_folders(tmp_path):
    (tmp_path / "album").mkdir()
    (tmp_path / "album" / "nested").mkdir()
    Image.new("RGB", (12, 12), "red").save(tmp_path / "album" / "a.jpg", "JPEG")
    listing = browse.list_folder(str(tmp_path / "album"))
    names = {e["name"] for e in listing["entries"]}
    assert "nested" in names
    assert "a.jpg" not in names
    assert listing["image_count"] == 1


def test_refuse_write_into_library(tmp_path, monkeypatch):
    data = _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    dest = album / "destroyed.jpg"
    try:
        originals.assert_not_library(dest, album)
        raised = False
    except originals.OriginalWriteError:
        raised = True
    assert raised
    ok = originals.assert_data_write(data / "thumbs" / "1.jpg")
    assert ok == (data / "thumbs" / "1.jpg").resolve()


def _tree(root: Path) -> list[tuple[str, int, int]]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        st = path.stat()
        out.append((rel, st.st_ino, st.st_mtime_ns))
    return out


def test_import_does_not_move_or_modify_originals(tmp_path, monkeypatch):
    data = _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    (album / "trip").mkdir(parents=True)
    photo = album / "trip" / "scan.jpg"
    Image.new("RGB", (40, 40), "navy").save(photo, "JPEG")
    before_hash = file_sha256(photo)
    before_tree = _tree(album)
    job_id = create_job("import")
    importer.import_folder(job_id, album)
    assert file_sha256(photo) == before_hash
    assert _tree(album) == before_tree
    assert photo.exists()
    assert not any(p.is_relative_to(album) for p in data.rglob("*") if p == photo)
    conn = connect()
    stored = conn.execute("SELECT path FROM photos").fetchone()["path"]
    conn.close()
    assert stored == str(photo.resolve()) or stored == str(photo)


def test_import_two_sibling_folders(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    tokyo = tmp_path / "1994 - Harbor"
    vodka = tmp_path / "1996 - Picnic"
    tokyo.mkdir()
    vodka.mkdir()
    Image.new("RGB", (16, 16), "navy").save(tokyo / "a.jpg", "JPEG")
    Image.new("RGB", (16, 16), "maroon").save(vodka / "b.jpg", "JPEG")
    importer.import_folder(create_job("import"), tokyo)
    importer.import_folder(create_job("import"), vodka)
    conn = connect()
    paths = [r["path"] for r in conn.execute("SELECT path FROM photos ORDER BY path")]
    conn.close()
    assert len(paths) == 2
    assert any(path.endswith("a.jpg") for path in paths)
    assert any(path.endswith("b.jpg") for path in paths)


def test_import_writes_local_display_view(tmp_path, monkeypatch):
    data = _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    Image.new("RGB", (2000, 1200), "navy").save(album / "a.jpg", "JPEG")
    importer.import_folder(create_job("import"), album)
    conn = connect()
    photo_id = conn.execute("SELECT id FROM photos").fetchone()["id"]
    conn.close()
    view = Image.open(data / "views" / f"{photo_id}.jpg")
    assert max(view.size) <= 1600


def test_reimport_skips_known_paths_and_adds_only_new(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    Image.new("RGB", (16, 16), "navy").save(album / "a.jpg", "JPEG")
    Image.new("RGB", (16, 16), "maroon").save(album / "b.jpg", "JPEG")
    first = importer.import_folder(create_job("import"), album)
    assert first["added"] == 2
    conn = connect()
    ids = {row["path"]: row["id"] for row in conn.execute("SELECT id, path FROM photos")}
    conn.close()
    Image.new("RGB", (16, 16), "olive").save(album / "c.jpg", "JPEG")
    again = importer.import_folder(create_job("import"), album)
    assert again["added"] == 1
    assert again["skipped"] >= 2
    conn = connect()
    rows = conn.execute("SELECT id, path FROM photos").fetchall()
    conn.close()
    assert len(rows) == 3
    by_path = {row["path"]: row["id"] for row in rows}
    assert by_path[str((album / "a.jpg").resolve())] == ids[str((album / "a.jpg").resolve())]
    assert by_path[str((album / "b.jpg").resolve())] == ids[str((album / "b.jpg").resolve())]


def test_import_same_path_twice_does_not_raise(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    photo = album / "shot.jpg"
    Image.new("RGB", (16, 16), "navy").save(photo, "JPEG")
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo.resolve()), "abc", 16, 16, now_iso()),
    )
    conn.commit()
    conn.close()
    result = importer.import_folder(create_job("import"), album)
    assert result["added"] == 0
    assert result["skipped"] >= 1


def test_skip_if_complete_does_not_walk(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    Image.new("RGB", (16, 16), "navy").save(album / "shot.jpg", "JPEG")
    importer.import_folder(create_job("import"), album)
    importer.state_mod.set_state(importer.FILE_TOTAL_KEY, "1")

    def boom(_folder):
        raise AssertionError("walk should be skipped")

    monkeypatch.setattr(importer, "_walk_images", boom)
    result = importer.import_folder(create_job("import"), album, skip_if_complete=True)
    assert result["added"] == 0
    assert result["skipped"] >= 1


def test_insert_photo_ignores_duplicate_path(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    conn = connect()
    first = importer._insert_photo(conn, "/album/shot.jpg", "aaa", None, (8, 8), 12)
    again = importer._insert_photo(conn, "/album/shot.jpg", "bbb", None, (8, 8), 12)
    conn.close()
    assert first is not None
    assert again is None


def test_image_exts_cover_stills_and_raw():
    for ext in (".gif", ".tif", ".webp", ".heic", ".avif", ".cr3", ".raf", ".dng", ".nef", ".arw"):
        assert ext in config.IMAGE_EXTS
    assert ".mp4" not in config.IMAGE_EXTS
    assert ".mov" not in config.IMAGE_EXTS
    assert ".pdf" not in config.IMAGE_EXTS


def test_import_gif_and_webp_leaves_originals(tmp_path, monkeypatch):
    data = _db(tmp_path, monkeypatch)
    album = tmp_path / "stills"
    album.mkdir()
    gif = album / "loop.gif"
    webp = album / "shot.webp"
    Image.new("RGB", (20, 16), "navy").save(gif, "GIF")
    Image.new("RGB", (20, 16), "olive").save(webp, "WEBP")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (gif, webp)}
    result = importer.import_folder(create_job("import"), album)
    assert result["added"] == 2
    for path, (blob, mtime) in before.items():
        assert path.read_bytes() == blob
        assert path.stat().st_mtime_ns == mtime
    opened = originals.open_image(gif)
    assert opened.size == (20, 16)
    conn = connect()
    names = {Path(row["path"]).name for row in conn.execute("SELECT path FROM photos")}
    conn.close()
    assert names == {"loop.gif", "shot.webp"}
    assert (data / "thumbs").exists()


def test_bad_raw_is_skipped_not_a_crash(tmp_path, monkeypatch):
    from PIL import UnidentifiedImageError

    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    junk = album / "broken.CR3"
    junk.write_bytes(b"not-a-raw-file")
    Image.new("RGB", (16, 16), "navy").save(album / "ok.jpg", "JPEG")
    try:
        originals.open_image(junk)
        raised = False
    except UnidentifiedImageError:
        raised = True
    assert raised
    result = importer.import_folder(create_job("import"), album)
    assert result["added"] == 1
    conn = connect()
    names = {Path(row["path"]).name for row in conn.execute("SELECT path FROM photos")}
    conn.close()
    assert names == {"ok.jpg"}


def test_open_image_decodes_raw_read_only(tmp_path, monkeypatch):
    raw_path = tmp_path / "shot.CR3"
    raw_path.write_bytes(b"not-a-real-raw")
    before = raw_path.read_bytes()
    before_mtime = raw_path.stat().st_mtime_ns

    class FakeRaw:
        def postprocess(self, **kwargs):
            import numpy as np

            return np.zeros((8, 10, 3), dtype="uint8")

        def close(self):
            pass

    def fake_imread(handle):
        assert handle.readable()
        return FakeRaw()

    import rawpy

    monkeypatch.setattr(rawpy, "imread", fake_imread)
    img = originals.open_image(raw_path)
    assert img.size == (10, 8)
    assert raw_path.read_bytes() == before
    assert raw_path.stat().st_mtime_ns == before_mtime


def test_save_image_refuses_album(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    album = tmp_path / "heirlooms"
    album.mkdir()
    Image.new("RGB", (8, 8), "red").save(album / "keep.jpg", "JPEG")
    job_id = create_job("import")
    importer.import_folder(job_id, album)
    img = Image.new("RGB", (8, 8), "blue")
    try:
        originals.save_image(img, album / "keep.jpg")
        raised = False
    except originals.OriginalWriteError:
        raised = True
    assert raised
    try:
        originals.save_image(img, album / "sorted" / "new.jpg")
        raised_new = False
    except originals.OriginalWriteError:
        raised_new = True
    assert raised_new
    assert not (album / "sorted").exists()
    assert (album / "keep.jpg").exists()


def test_resume_points_at_unnamed_cluster(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/x.jpg", "x", 10, 10, now_iso()),
    )
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        ("/y.jpg", "y", 10, 10, now_iso()),
    )
    conn.execute("INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)", (now_iso(),))
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (1,0,0,1,1,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, cluster_id, created_at)
           VALUES (2,0,0,1,1,0.9,'ok',1,?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    target = state.resume_target()
    assert target["kind"] == "clusters"
    person = create_person("Ada")
    assign_cluster(1, person["id"])
    target = state.resume_target()
    assert target["kind"] != "clusters"
