"""Temporary Grok Imagine sharpen previews. Original photo files are never written."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .config import (
    SHARPEN_DIR,
    SHARPEN_MAX_SIDE,
    SHARPEN_MODEL,
    SHARPEN_RESOLUTION,
    SHARPEN_TIMEOUT,
    THUMB_DIR,
    XAI_API_BASE,
    ensure_dirs,
    xai_api_key,
)
from .db import connect, init_db
from .originals import open_image
from .util import now_iso

PROMPT_VERSION = 2

SHARPEN_PROMPT = """Restore this existing family photograph so it stays clear at 400% zoom.

Make every face the sharpest, most readable part of the image: eyes, eyebrows, eyelashes, nose, mouth, teeth, skin texture, and hair. Recover facial detail first. Then sharpen clothing, objects, and the background.

Hard rules:
- Keep the same crop, framing, camera angle, and aspect ratio.
- Keep every person, face, pose, expression, clothing, object, and background identical.
- Do not add, remove, restyle, colorize, beautify, or age anyone.
- Do not change the era, film look, or lighting except a modest sharpness boost.
- Output at the highest resolution available. Identities must stay exact.
"""

CACHE_KEEP = 40


class SharpenError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def sharpen_status() -> dict[str, Any]:
    return {
        "available": bool(xai_api_key()),
        "model": SHARPEN_MODEL,
        "writes_original": False,
        "resolution": SHARPEN_RESOLUTION,
        "cache": "temporary preview under the app data folder",
    }


def preview_path(photo_id: int) -> Path:
    ensure_dirs()
    return (SHARPEN_DIR / f"{int(photo_id)}.jpg").resolve()


def has_preview(photo_id: int) -> bool:
    path = preview_path(photo_id)
    return path.is_file() and path.stat().st_size > 32


def _preview_is_current(photo_id: int) -> bool:
    if not has_preview(photo_id):
        return False
    meta = preview_path(photo_id).with_suffix(".json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return int(data.get("prompt_version") or 0) == PROMPT_VERSION


def drop_preview(photo_id: int) -> bool:
    path = preview_path(photo_id)
    meta = path.with_suffix(".json")
    existed = path.is_file() or meta.is_file()
    if path.is_file():
        path.unlink()
    if meta.is_file():
        meta.unlink()
    return existed


def sharpen_photo(photo_id: int, *, fresh: bool = False) -> dict[str, Any]:
    if not xai_api_key():
        raise SharpenError("Add an xAI key or sign in with SuperGrok in Settings.", 503)
    photo_id = int(photo_id)
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id, path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise SharpenError("Photo not found", 404)
    original = Path(row["path"])
    original_bytes = original.read_bytes() if original.is_file() else None
    cached = _preview_is_current(photo_id)
    if cached and not fresh:
        return _result(photo_id, cached=True, original=original)
    source = _source_image(photo_id, original)
    jpeg, src_w, src_h = _downscale_jpeg(source)
    raw = _edit_with_grok(jpeg, src_w, src_h, photo_id=photo_id)
    _store_preview(photo_id, raw)
    if original.is_file() and original_bytes is not None and original.read_bytes() != original_bytes:
        raise SharpenError("Sharpen aborted: the original file changed on disk.", 500)
    _prune_cache()
    return _result(photo_id, cached=False, original=original)


def _result(photo_id: int, *, cached: bool, original: Path) -> dict[str, Any]:
    path = preview_path(photo_id)
    img = Image.open(path)
    width, height = img.size
    img.close()
    return {
        "ok": True,
        "photo_id": photo_id,
        "url": f"/api/photos/{photo_id}/sharpened",
        "cached": cached,
        "model": SHARPEN_MODEL,
        "resolution": SHARPEN_RESOLUTION,
        "width": width,
        "height": height,
        "original_untouched": True,
        "original_path": str(original) if original.is_file() else None,
        "preview_only": True,
    }


def _source_image(photo_id: int, original: Path) -> Image.Image:
    if original.is_file():
        return open_image(original).convert("RGB")
    thumb = THUMB_DIR / f"{photo_id}.jpg"
    if thumb.is_file():
        img = Image.open(thumb)
        img.load()
        return img.convert("RGB")
    raise SharpenError(
        "The original is offline. Mount the album to sharpen from the real photo.",
        404,
    )


def _downscale_jpeg(img: Image.Image) -> tuple[bytes, int, int]:
    frame = img.convert("RGB")
    w, h = frame.size
    longest = max(w, h)
    if longest > SHARPEN_MAX_SIDE:
        scale = SHARPEN_MAX_SIDE / float(longest)
        frame = frame.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        w, h = frame.size
    buf = BytesIO()
    frame.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue(), w, h


def _aspect_ratio(width: int, height: int) -> str:
    ar = (width or 1) / float(height or 1)
    choices = {
        "1:1": 1.0,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "3:2": 1.5,
        "2:3": 2 / 3,
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "2:1": 2.0,
        "1:2": 0.5,
    }
    return min(choices, key=lambda key: abs(choices[key] - ar))


def _face_count(photo_id: int) -> int:
    conn = connect()
    init_db(conn)
    try:
        n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE photo_id = ?
              AND IFNULL(assigned_how, '') != 'junk'
            """,
            (int(photo_id),),
        ).fetchone()["n"]
        return int(n or 0)
    finally:
        conn.close()


def _sharpen_prompt(photo_id: int) -> str:
    n = _face_count(photo_id)
    if n == 1:
        focus = (
            "There is one face in this photograph. Make that face the sharpest, most readable "
            "area of the image: eyes, eyebrows, eyelashes, nose, mouth, teeth, skin texture, and hair."
        )
    elif n > 1:
        focus = (
            f"There are {n} faces in this photograph. Make every face the sharpest, most readable "
            "areas of the image: eyes, eyebrows, eyelashes, nose, mouth, teeth, skin texture, and hair."
        )
    else:
        focus = (
            "If any faces are present, make them the sharpest, most readable areas of the image: "
            "eyes, eyebrows, eyelashes, nose, mouth, teeth, skin texture, and hair."
        )
    return (
        "Restore this existing family photograph so it stays clear at 400% zoom.\n\n"
        f"{focus}\n"
        "Recover facial detail first. Then sharpen clothing, objects, and the background.\n\n"
        "Hard rules:\n"
        "- Keep the same crop, framing, camera angle, and aspect ratio.\n"
        "- Keep every person, face, pose, expression, clothing, object, and background identical.\n"
        "- Do not add, remove, restyle, colorize, beautify, or age anyone.\n"
        "- Do not change the era, film look, or lighting except a modest sharpness boost.\n"
        "- Output at the highest resolution available. Identities must stay exact.\n"
    )


def _edit_with_grok(jpeg: bytes, width: int, height: int, *, photo_id: int | None = None) -> bytes:
    b64 = base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": SHARPEN_MODEL,
        "prompt": _sharpen_prompt(photo_id) if photo_id is not None else SHARPEN_PROMPT,
        "n": 1,
        "response_format": "b64_json",
        "resolution": SHARPEN_RESOLUTION,
        "quality": "medium",
        "aspect_ratio": _aspect_ratio(width, height),
        "image": {
            "url": f"data:image/jpeg;base64,{b64}",
            "type": "image_url",
        },
    }
    payload = _post_edit(body)
    return _bytes_from_edit(payload)


def _post_edit(body: dict[str, Any]) -> dict[str, Any]:
    timeout = httpx.Timeout(SHARPEN_TIMEOUT, connect=20.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            res = client.post(
                f"{XAI_API_BASE}/images/edits",
                headers={
                    "Authorization": f"Bearer {xai_api_key()}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise SharpenError("Sharpen timed out. Try again in a moment.", 504) from exc
    except httpx.HTTPError as exc:
        raise SharpenError("Could not reach Grok Imagine.", 502) from exc
    if res.status_code == 401:
        raise SharpenError("XAI_API_KEY was rejected. Check the key on the server.", 502)
    if res.status_code >= 400:
        raise SharpenError(_error_detail(res) or f"Sharpen failed ({res.status_code}).", 502)
    try:
        payload = res.json()
    except json.JSONDecodeError as exc:
        raise SharpenError("Grok Imagine returned an unreadable image.") from exc
    if not isinstance(payload, dict):
        raise SharpenError("Grok Imagine returned an unreadable image.")
    return payload


def _bytes_from_edit(payload: dict[str, Any]) -> bytes:
    data = payload.get("data")
    item: dict[str, Any] | None = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        item = data[0]
    if item and item.get("b64_json"):
        return _decode_b64(str(item["b64_json"]))
    url = None
    if item and item.get("url"):
        url = str(item["url"])
    elif isinstance(payload.get("url"), str):
        url = payload["url"]
    if url:
        return _download_url(url)
    raise SharpenError("Grok Imagine did not return an image.")


def _decode_b64(raw: str) -> bytes:
    text = raw.strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except Exception as exc:
        raise SharpenError("Grok Imagine returned a broken image.") from exc


def _download_url(url: str) -> bytes:
    if not url.lower().startswith("https://"):
        raise SharpenError("Grok Imagine returned an unsafe image URL.")
    timeout = httpx.Timeout(60.0, connect=20.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            res = client.get(url)
    except httpx.HTTPError as exc:
        raise SharpenError("Could not download the sharpened preview.") from exc
    if res.status_code >= 400 or not res.content:
        raise SharpenError("Could not download the sharpened preview.")
    return res.content


def _store_preview(photo_id: int, raw: bytes) -> Path:
    ensure_dirs()
    root = SHARPEN_DIR.resolve()
    path = preview_path(photo_id)
    if root not in path.parents and path.parent != root:
        raise SharpenError("Invalid preview path.", 500)
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        frame = img.convert("RGB")
    except Exception as exc:
        raise SharpenError("Grok Imagine returned a file that is not an image.") from exc
    tmp = path.with_suffix(".tmp.jpg")
    frame.save(tmp, format="JPEG", quality=90, optimize=True)
    tmp.replace(path)
    meta = {
        "photo_id": photo_id,
        "model": SHARPEN_MODEL,
        "prompt_version": PROMPT_VERSION,
        "created_at": now_iso(),
        "width": frame.size[0],
        "height": frame.size[1],
        "original_untouched": True,
    }
    path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def _prune_cache() -> None:
    ensure_dirs()
    files = sorted(
        (p for p in SHARPEN_DIR.glob("*.jpg") if ".tmp." not in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[CACHE_KEEP:]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".json").unlink(missing_ok=True)


def _error_detail(res: httpx.Response) -> str:
    try:
        payload = res.json()
    except json.JSONDecodeError:
        text = (res.text or "").strip()
        return text[:240]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err.strip():
            return err.strip()
        if payload.get("detail"):
            return str(payload["detail"])
    return ""
