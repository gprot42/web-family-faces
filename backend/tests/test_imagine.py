import base64
from io import BytesIO

from PIL import Image

from photosort import config, db, imagine
from photosort.db import connect, init_db
from photosort.util import now_iso


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    imagine_dir = data / "imagine"
    thumbs = data / "thumbs"
    imagine_dir.mkdir()
    thumbs.mkdir()
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "THUMB_DIR", thumbs)
    monkeypatch.setattr(config, "IMAGINE_DIR", imagine_dir)
    monkeypatch.setattr(imagine, "IMAGINE_DIR", imagine_dir)
    monkeypatch.setattr(imagine, "THUMB_DIR", thumbs)
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = connect()
    init_db(conn)
    return conn, data, imagine_dir


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


def test_imagine_sends_user_prompt(tmp_path, monkeypatch):
    conn, _data, _imagine_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    edited = _jpeg_bytes((200, 40, 40), (40, 30))
    payload = {"data": [{"b64_json": base64.b64encode(edited).decode("ascii")}]}
    client = _FakeClient(payload)
    monkeypatch.setattr(imagine, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(imagine.httpx, "Client", lambda **kwargs: client)
    imagine.edit_photo(1, "  make it black and white  ")
    assert client.posts
    body = client.posts[0][1]["json"]
    assert "make it black and white" in body["prompt"]
    assert body["model"] == "grok-imagine-image-2.0"
    assert body["resolution"] == "2k"


def test_imagine_writes_preview_not_original(tmp_path, monkeypatch):
    conn, data, imagine_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    edited = _jpeg_bytes((200, 40, 40), (40, 30))
    payload = {"data": [{"b64_json": base64.b64encode(edited).decode("ascii")}]}
    monkeypatch.setattr(imagine, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(imagine.httpx, "Client", lambda **kwargs: _FakeClient(payload))
    result = imagine.edit_photo(1, "colourise this photo")
    assert result["original_untouched"] is True
    assert result["preview_only"] is True
    assert result["cached"] is False
    assert result["prompt"] == "colourise this photo"
    assert src.read_bytes() == before
    preview = imagine_dir / "1.jpg"
    assert preview.is_file()
    assert preview.resolve().is_relative_to(data.resolve())
    assert preview.read_bytes() != before
    again = imagine.edit_photo(1, "colourise this photo")
    assert again["cached"] is True
    assert src.read_bytes() == before


def test_imagine_new_prompt_bypasses_cache(tmp_path, monkeypatch):
    conn, _data, imagine_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    first = _jpeg_bytes((200, 40, 40), (40, 30))
    second = _jpeg_bytes((10, 200, 10), (40, 30))
    payloads = [
        {"data": [{"b64_json": base64.b64encode(first).decode("ascii")}]},
        {"data": [{"b64_json": base64.b64encode(second).decode("ascii")}]},
    ]

    class _SeqClient(_FakeClient):
        def post(self, url, **kwargs):
            self.payload = payloads[len(self.posts)]
            return super().post(url, **kwargs)

    client = _SeqClient(payloads[0])
    monkeypatch.setattr(imagine, "xai_api_key", lambda: "xai-test")
    monkeypatch.setattr(imagine.httpx, "Client", lambda **kwargs: client)
    imagine.edit_photo(1, "black and white")
    imagine.edit_photo(1, "restore faded colours")
    assert len(client.posts) == 2
    assert "restore faded colours" in client.posts[1][1]["json"]["prompt"]
    assert src.read_bytes() == before
    assert (imagine_dir / "1.json").read_text(encoding="utf-8").find("restore faded colours") >= 0


def test_imagine_requires_key(tmp_path, monkeypatch):
    conn, _data, _imagine_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    monkeypatch.setattr(imagine, "xai_api_key", lambda: "")
    try:
        imagine.edit_photo(1, "make it sharper")
        raise AssertionError("expected ImagineError")
    except imagine.ImagineError as exc:
        assert exc.status == 503


def test_imagine_rejects_blank_prompt(tmp_path, monkeypatch):
    conn, _data, _imagine_dir = _setup(tmp_path, monkeypatch)
    _photo(conn, tmp_path)
    monkeypatch.setattr(imagine, "xai_api_key", lambda: "xai-test")
    try:
        imagine.edit_photo(1, "  \n  ")
        raise AssertionError("expected ImagineError")
    except imagine.ImagineError as exc:
        assert exc.status == 400


def test_drop_preview_leaves_original(tmp_path, monkeypatch):
    conn, _data, imagine_dir = _setup(tmp_path, monkeypatch)
    src = _photo(conn, tmp_path)
    before = src.read_bytes()
    preview = imagine_dir / "1.jpg"
    preview.write_bytes(_jpeg_bytes((9, 9, 9)))
    assert imagine.drop_preview(1) is True
    assert not preview.exists()
    assert src.read_bytes() == before
