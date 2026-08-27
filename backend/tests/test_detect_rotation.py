from photosort.faces import box_from_rotated, box_to_rotated, same_face_box, _rotate_pil
from PIL import Image


def test_box_from_rotated_90_maps_back_to_original():
    # Original 10x4. After a clockwise 90° turn the canvas is 4x10.
    # A face on the upright photo at the "top" sat on the left of the file.
    orig_w, orig_h = 10.0, 4.0
    # Box in the rotated image near the top-left of the upright view.
    box = box_from_rotated((0.5, 0.5, 1.5, 2.5), 90, orig_w, orig_h)
    x1, y1, x2, y2 = box
    assert x1 < x2 and y1 < y2
    assert 0 <= x1 <= orig_w and 0 <= x2 <= orig_w
    assert 0 <= y1 <= orig_h and 0 <= y2 <= orig_h


def test_box_to_rotated_round_trips_90():
    orig_w, orig_h = 10.0, 4.0
    box = (1.0, 0.5, 3.0, 2.0)
    turned = box_to_rotated(box, 90, orig_w, orig_h)
    back = box_from_rotated(turned, 90, orig_w, orig_h)
    for a, b in zip(box, back):
        assert abs(a - b) < 1e-6


def test_same_face_box_treats_nested_detector_boxes_as_one_person():
    outer = (870.0, 692.0, 1050.0, 822.0)
    inner = (879.0, 703.0, 1023.0, 811.0)
    assert same_face_box(outer, inner)
    assert same_face_box(inner, outer)
    neighbor = (1200.0, 692.0, 1380.0, 822.0)
    assert not same_face_box(outer, neighbor)


def test_rotate_pil_90_is_clockwise():
    im = Image.new("RGB", (10, 4), "white")
    im.putpixel((0, 0), (255, 0, 0))
    turned = _rotate_pil(im, 90)
    assert turned.size == (4, 10)
    assert turned.getpixel((3, 0)) == (255, 0, 0)


def test_rotate_photo_rewrites_crop_to_match_display(tmp_path, monkeypatch):
    from photosort import catalog, config, db, faces as faces_mod, originals
    from photosort.db import connect, init_db
    from photosort.faces import save_crop
    from photosort.main import app
    from photosort.util import now_iso
    from fastapi.testclient import TestClient

    data = tmp_path / "data"
    data.mkdir()
    crops = data / "crops"
    thumbs = data / "thumbs"
    views = data / "views"
    crops.mkdir()
    thumbs.mkdir()
    views.mkdir()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", thumbs)
    monkeypatch.setattr(config, "VIEW_DIR", views)
    monkeypatch.setattr(config, "CROP_DIR", crops)
    monkeypatch.setattr(faces_mod, "CROP_DIR", crops)
    monkeypatch.setattr(faces_mod, "THUMB_DIR", thumbs)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setattr(catalog, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(catalog, "BACKUP_DIR", data / "backups")
    (data / "backups").mkdir()

    photo = tmp_path / "sideways.jpg"
    im = Image.new("RGB", (40, 30), "white")
    for x in range(0, 8):
        for y in range(12, 20):
            im.putpixel((x, y), (255, 0, 0))
    im.save(photo)

    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo), "rotcrop", 40, 30, now_iso()),
    )
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1, 0, 10, 12, 22, 0.9, 'ok', ?)""",
        (now_iso(),),
    )
    conn.commit()
    conn.close()
    save_crop(photo, 40, 30, (0, 10, 12, 22), 1, photo_id=1, rotation=0)

    client = TestClient(app)
    out = client.patch("/api/photos/1", json={"rotate": "right"}).json()
    assert out["rotation"] == 90
    crop = Image.open(crops / "1.jpg")
    # Display-upright crop: the mark that sat on the left of the file is now near the top.
    pixels = list(crop.getdata())
    reds = [i for i, px in enumerate(pixels) if px[0] > 200 and px[1] < 40 and px[2] < 40]
    assert reds
    ys = [i // crop.size[0] for i in reds]
    assert sum(ys) / len(ys) < crop.size[1] / 2
