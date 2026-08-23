import base64
from io import BytesIO

from PIL import Image

from photosort import config, db, sharpen
from photosort.db import connect, init_db
from photosort.util import now_iso


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    sharpen_dir = data / "sharpen"
    thumbs = data / "thumbs"
    sharpen_dir.mkdir()
    thumbs.mkdir()
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", thumbs)
    monkeypatch.setattr(config, "VIEW_DIR", data / "views")
    monkeypatch.setattr(config, "SHARPEN_DIR", sharpen_dir)
    monkeypatch.setattr(sharpen, "SHARPEN_DIR", sharpen_dir)
    monkeypatch.setattr(sharpen, "THUMB_DIR", thumbs)
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = connect()
    init_db(conn)
    return conn, data, sharpen_dir


def _jpeg_bytes(color=(40, 80, 120), size=(48, 36)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _photo(conn, tmp_path, photo_id=1):
    src = tmp_path / "original.jpg"
    src.write_bytes(_jpeg_bytes())
    conn.execute(
        "INSERT INTO photos (id, path, sha256, width, height, created_at) VALUES (?,?,?,?,?,?)",
        (photo_id, str(src), "abc", 48, 36, now_iso()),
    )
    conn.commit()
    return src


class _FakeRes:
    def __init__(self, payload, status=200, content=b""):
        self._payload = payload
        self.status_code = status
        self.content = content
        self.text = ""

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, status=200, content=b""):
        self.payload = payload
        self.status = status
        self.content = content
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeRes(self.payload, self.status, self.content)

    def get(self, url, **kwargs):
        return _FakeRes(self.payload, self.status, self.content)


def test_sharpen_requests_high_resolution(tmp_path, monkeypatch):
    conn, _data, _sharpen_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    sharpened = _jpeg_bytes((200, 40, 40), (40, 30))
    payload = {"data": [{"b64_json": base64.b64encode(sharpened).decode("ascii")}]}
    client = _FakeClient(payload)
    monkeypatch.setattr(sharpen, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(sharpen.httpx, "Client", lambda **kwargs: client)
    sharpen.sharpen_photo(1)
    assert client.posts
    body = client.posts[0][1]["json"]
    assert body["resolution"] == "2k"
    assert body["aspect_ratio"] in {"1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "2:1", "1:2"}
    prompt = body["prompt"].lower()
    assert "face" in prompt
    assert prompt.find("face") < prompt.find("background")


def test_sharpen_writes_preview_not_original(tmp_path, monkeypatch):
    conn, data, sharpen_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    sharpened = _jpeg_bytes((200, 40, 40), (40, 30))
    payload = {"data": [{"b64_json": base64.b64encode(sharpened).decode("ascii")}]}
    monkeypatch.setattr(sharpen, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(sharpen.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    result = sharpen.sharpen_photo(1)
    assert result["original_untouched"] is True
    assert result["preview_only"] is True
    assert result["cached"] is False
    assert src.read_bytes() == before
    preview = sharpen_dir / "1.jpg"
    assert preview.is_file()
    assert preview.resolve().is_relative_to(data.resolve())
    assert preview.read_bytes() != before
    again = sharpen.sharpen_photo(1)
    assert again["cached"] is True
    assert src.read_bytes() == before


def test_sharpen_prompt_names_detected_faces(tmp_path, monkeypatch):
    conn, _data, _sharpen_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    conn.execute(
        """INSERT INTO faces (photo_id, x1, y1, x2, y2, det_score, quality, created_at)
           VALUES (1,0,0,10,10,0.9,'ok',?), (1,20,0,30,10,0.9,'ok',?)""",
        (now_iso(), now_iso()),
    )
    conn.commit()
    text = sharpen._sharpen_prompt(1)
    assert "2 faces" in text
    assert "every face" in text.lower()
    assert "facial detail first" in text.lower()


def test_sharpen_regenerates_when_prompt_version_changes(tmp_path, monkeypatch):
    conn, _data, sharpen_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    old = _jpeg_bytes((10, 10, 10), (20, 16))
    new = _jpeg_bytes((200, 40, 40), (40, 30))
    preview = sharpen_dir / "1.jpg"
    preview.write_bytes(old)
    (sharpen_dir / "1.json").write_text('{"prompt_version": 1}', encoding="utf-8")
    payload = {"data": [{"b64_json": __import__("base64").b64encode(new).decode("ascii")}]}
    monkeypatch.setattr(sharpen, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(sharpen.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    result = sharpen.sharpen_photo(1)
    assert result["cached"] is False
    assert preview.read_bytes() != old
    assert src.read_bytes() == before
    meta = __import__("json").loads((sharpen_dir / "1.json").read_text(encoding="utf-8"))
    assert meta["prompt_version"] == sharpen.PROMPT_VERSION
    again = sharpen.sharpen_photo(1)
    assert again["cached"] is True


def test_sharpen_requires_key(tmp_path, monkeypatch):
    conn, _data, _sharpen_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    monkeypatch.setattr(sharpen, "xai_api_key", lambda: "")
    try:
        sharpen.sharpen_photo(1)
        raise AssertionError("expected SharpenError")
    except sharpen.SharpenError as exc:
        assert exc.status == 503


def test_bytes_from_url_payload(tmp_path, monkeypatch):
    jpeg = _jpeg_bytes((10, 20, 30))
    payload = {"data": [{"url": "https://example.test/out.jpg"}]}
    monkeypatch.setattr(
        sharpen.httpx,
        "Client",
        lambda **kwargs: _FakeClient(payload, content=jpeg),
    )
    got = sharpen._bytes_from_edit(payload)
    assert got == jpeg


def test_drop_preview_leaves_original(tmp_path, monkeypatch):
    conn, _data, sharpen_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    preview = sharpen_dir / "1.jpg"
    preview.write_bytes(_jpeg_bytes((9, 9, 9)))
    assert sharpen.drop_preview(1) is True
    assert not preview.exists()
    assert src.read_bytes() == before
