from fastapi.testclient import TestClient
from PIL import Image
import numpy as np

from photosort import catalog, config, db, originals
from photosort.db import connect, init_db
from photosort.main import app
from photosort.util import now_iso


def _setup(tmp_path, monkeypatch, *, with_face=False):
    path = tmp_path / "t.db"
    data = tmp_path / "data"
    data.mkdir()
    photo = tmp_path / "group.jpg"
    Image.new("RGB", (200, 200), (90, 80, 70)).save(photo, "JPEG")
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
    (data / "crops").mkdir()
    conn = connect()
    init_db(conn)
    conn.execute(
        "INSERT INTO photos (path, sha256, width, height, created_at) VALUES (?,?,?,?,?)",
        (str(photo), "abc", 200, 200, now_iso()),
    )
    if with_face:
        conn.execute(
            """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
               VALUES (1, 10, 10, 50, 50, 0.9, 'ok', ?)""",
            (now_iso(),),
        )
    conn.commit()
    conn.close()
    return photo


class _FakeFace:
    def __init__(self, bbox, score=0.88):
        self.bbox = bbox
        self.det_score = score
        self.normed_embedding = np.ones(8, dtype=np.float32)
        self.age = 42
        self.gender = 1


class _FakeAnalyzer:
    def __init__(self, faces):
        self.faces = faces
        self.det_thresh = 0.5
        self.models = {}

    def get(self, img, max_num=0, det_metric="default"):
        return list(self.faces)


def test_add_face_uses_detector_box(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(faces_mod, "get_analyzer", lambda: _FakeAnalyzer([_FakeFace([8, 8, 40, 40])]))
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 40, "y1": 40, "x2": 90, "y2": 90}).json()
    assert result["existing"] is False
    face = result["face"]
    assert face["photo_id"] == 1
    assert face["x2"] > face["x1"]
    assert face["y2"] > face["y1"]
    assert face["person_id"] is None
    listed = client.get("/api/photos/1", params={"lite": "true"}).json()
    assert any(item["id"] == face["id"] for item in listed["faces"])


def test_add_face_returns_existing_if_box_overlaps(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch, with_face=True)
    monkeypatch.setattr(faces_mod, "get_analyzer", lambda: _FakeAnalyzer([]))
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 12, "y1": 12, "x2": 48, "y2": 48}).json()
    assert result["existing"] is True
    assert result["face"]["id"] == 1
    listed = client.get("/api/photos/1", params={"lite": "true"}).json()
    assert len(listed["faces"]) == 1


def test_add_face_keeps_drawn_box_when_detector_hit_is_weak(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        faces_mod,
        "get_analyzer",
        lambda: _FakeAnalyzer([_FakeFace([4, 4, 12, 12], score=0.22)]),
    )
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 60, "y1": 70, "x2": 130, "y2": 150}).json()
    face = result["face"]
    assert round(face["x1"]) == 60
    assert round(face["y1"]) == 70
    assert round(face["x2"]) == 130
    assert round(face["y2"]) == 150
    assert face["quality"] == "ok"


def test_add_face_falls_back_to_drawn_box(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(faces_mod, "get_analyzer", lambda: _FakeAnalyzer([]))
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 60, "y1": 70, "x2": 110, "y2": 130}).json()
    assert result["existing"] is False
    face = result["face"]
    assert round(face["x1"]) == 60
    assert round(face["y1"]) == 70
    assert round(face["x2"]) == 110
    assert round(face["y2"]) == 130


def test_add_face_rejects_tiny_box(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post("/api/photos/1/faces", json={"x1": 10, "y1": 10, "x2": 14, "y2": 14})
    assert res.status_code == 400


def test_add_face_restores_hidden_face(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch, with_face=True)
    conn = connect()
    conn.execute("UPDATE faces SET assigned_how = 'junk', quality = 'unidentifiable' WHERE id = 1")
    conn.commit()
    conn.close()
    monkeypatch.setattr(faces_mod, "get_analyzer", lambda: _FakeAnalyzer([]))
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 12, "y1": 12, "x2": 48, "y2": 48}).json()
    assert result["existing"] is True
    assert result["restored"] is True
    assert result["face"]["assigned_how"] is None
    assert result["face"]["quality"] == "ok"


def test_add_face_missing_photo(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post("/api/photos/99/faces", json={"x1": 10, "y1": 10, "x2": 80, "y2": 80})
    assert res.status_code == 404


def test_add_face_uses_drawn_box_if_model_not_ready(tmp_path, monkeypatch):
    from photosort import faces as faces_mod

    _setup(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("function() argument 'code' must be code, not str")

    monkeypatch.setattr(faces_mod, "get_analyzer", boom)
    client = TestClient(app)
    result = client.post("/api/photos/1/faces", json={"x1": 60, "y1": 70, "x2": 110, "y2": 130}).json()
    assert result["existing"] is False
    face = result["face"]
    assert round(face["x1"]) == 60
    assert round(face["y1"]) == 70
    assert round(face["x2"]) == 110
    assert round(face["y2"]) == 130


def test_onnx_thread_cap_keeps_inference_session_a_class():
    import onnxruntime as ort
    from photosort.faces import _cap_onnx_threads

    _cap_onnx_threads()
    assert isinstance(ort.InferenceSession, type)
    class PickableInferenceSession(ort.InferenceSession):
        pass
    assert issubclass(PickableInferenceSession, ort.InferenceSession)
