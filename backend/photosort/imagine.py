"""Temporary Grok Imagine prompt edits. Original photo files are never written."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .config import (
    IMAGINE_DIR,
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

PROMPT_MIN = 3
PROMPT_MAX = 2000
CACHE_KEEP = 40

PROMPT_WRAP = """Edit this existing family photograph as requested.

Request:
{prompt}

Keep the same crop, framing, camera angle, and aspect ratio unless the request asks otherwise.
Keep the people in the photo recognizable as the same people unless the request asks to restyle them.
Do not add extra people. Do not write captions onto the photo unless asked.
Output at the highest resolution available.
"""


class ImagineError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def imagine_status() -> dict[str, Any]:
    return {
        "available": bool(xai_api_key()),
        "model": SHARPEN_MODEL,
        "writes_original": False,
        "resolution": SHARPEN_RESOLUTION,
        "cache": "temporary preview under the app data folder",
    }


def preview_path(photo_id: int) -> Path:
    ensure_dirs()
    return (IMAGINE_DIR / f"{int(photo_id)}.jpg").resolve()


def meta_path(photo_id: int) -> Path:
    return preview_path(photo_id).with_suffix(".json")


def has_preview(photo_id: int) -> bool:
    path = preview_path(photo_id)
    return path.is_file() and path.stat().st_size > 32


def drop_preview(photo_id: int) -> bool:
    path = preview_path(photo_id)
    meta = meta_path(photo_id)
    existed = path.is_file() or meta.is_file()
    if path.is_file():
        path.unlink()
    if meta.is_file():
        meta.unlink()
    return existed


def normalize_prompt(raw: str | None) -> str:
    text = " ".join(str(raw or "").split())
    return text.strip()


def preview_info(photo_id: int) -> dict[str, Any]:
    photo_id = int(photo_id)
    path = preview_path(photo_id)
    if not has_preview(photo_id):
        return {
            "ok": True,
            "exists": False,
            "photo_id": photo_id,
            "prompt": "",
            "original_untouched": True,
            "preview_only": True,
        }
    prompt = ""
    meta_file = meta_path(photo_id)
    if meta_file.is_file():
        try:
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                prompt = str(payload.get("prompt") or "")
        except json.JSONDecodeError:
            prompt = ""
    width, height = 0, 0
    try:
        img = Image.open(path)
        width, height = img.size
        img.close()
    except Exception:
        pass
    return {
        "ok": True,
        "exists": True,
        "photo_id": photo_id,
        "url": f"/api/photos/{photo_id}/imagined",
        "prompt": prompt,
        "model": SHARPEN_MODEL,
        "resolution": SHARPEN_RESOLUTION,
        "width": width,
        "height": height,
        "original_untouched": True,
        "preview_only": True,
    }


def edit_photo(photo_id: int, prompt: str, *, fresh: bool = False) -> dict[str, Any]:
    if not xai_api_key():
        raise ImagineError("Add an xAI key or sign in with SuperGrok in Settings.", 503)
    prompt = normalize_prompt(prompt)
    if len(prompt) < PROMPT_MIN:
        raise ImagineError("Describe the change in a few words.")
    if len(prompt) > PROMPT_MAX:
        raise ImagineError(f"Keep the prompt under {PROMPT_MAX} characters.")
    photo_id = int(photo_id)
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id, path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ImagineError("Photo not found", 404)
    original = Path(row["path"])
    original_bytes = original.read_bytes() if original.is_file() else None
    cached = has_preview(photo_id) and _cached_prompt(photo_id) == prompt
    if cached and not fresh:
        return _result(photo_id, cached=True, original=original, prompt=prompt)
    source = _source_image(photo_id, original)
    jpeg, src_w, src_h = _downscale_jpeg(source)
    raw = _edit_with_grok(jpeg, src_w, src_h, prompt)
    _store_preview(photo_id, raw, prompt)
    if original.is_file() and original_bytes is not None and original.read_bytes() != original_bytes:
        raise ImagineError("Edit aborted: the original file changed on disk.", 500)
    _prune_cache()
    return _result(photo_id, cached=False, original=original, prompt=prompt)


def _cached_prompt(photo_id: int) -> str:
    meta_file = meta_path(photo_id)
    if not meta_file.is_file():
        return ""
    try:
        payload = json.loads(meta_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        return normalize_prompt(str(payload.get("prompt") or ""))
    return ""


def _result(photo_id: int, *, cached: bool, original: Path, prompt: str) -> dict[str, Any]:
    info = preview_info(photo_id)
    info["cached"] = cached
    info["prompt"] = prompt
    info["original_path"] = str(original) if original.is_file() else None
    return info


def _source_image(photo_id: int, original: Path) -> Image.Image:
    if original.is_file():
        return open_image(original).convert("RGB")
    thumb = THUMB_DIR / f"{photo_id}.jpg"
    if thumb.is_file():
        img = Image.open(thumb)
        img.load()
        return img.convert("RGB")
    raise ImagineError(
        "The original is offline. Mount the album to change the real photo.",
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


def _edit_with_grok(jpeg: bytes, width: int, height: int, prompt: str) -> bytes:
    b64 = base64.b64encode(jpeg).decode("ascii")
    body = {
        "model": SHARPEN_MODEL,
        "prompt": PROMPT_WRAP.format(prompt=prompt),
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
        raise ImagineError("Grok Imagine timed out. Try again in a moment.", 504) from exc
    except httpx.HTTPError as exc:
        raise ImagineError("Could not reach Grok Imagine.", 502) from exc
    if res.status_code == 401:
        raise ImagineError("XAI_API_KEY was rejected. Check the key on the server.", 502)
    if res.status_code >= 400:
        raise ImagineError(_error_detail(res) or f"Grok Imagine failed ({res.status_code}).", 502)
    try:
        payload = res.json()
    except json.JSONDecodeError as exc:
        raise ImagineError("Grok Imagine returned an unreadable image.") from exc
    if not isinstance(payload, dict):
        raise ImagineError("Grok Imagine returned an unreadable image.")
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
    raise ImagineError("Grok Imagine did not return an image.")


def _decode_b64(raw: str) -> bytes:
    text = raw.strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except Exception as exc:
        raise ImagineError("Grok Imagine returned a broken image.") from exc


def _download_url(url: str) -> bytes:
    if not url.lower().startswith("https://"):
        raise ImagineError("Grok Imagine returned an unsafe image URL.")
    timeout = httpx.Timeout(60.0, connect=20.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            res = client.get(url)
    except httpx.HTTPError as exc:
        raise ImagineError("Could not download the changed preview.") from exc
    if res.status_code >= 400 or not res.content:
        raise ImagineError("Could not download the changed preview.")
    return res.content


def _store_preview(photo_id: int, raw: bytes, prompt: str) -> Path:
    ensure_dirs()
    root = IMAGINE_DIR.resolve()
    path = preview_path(photo_id)
    if root not in path.parents and path.parent != root:
        raise ImagineError("Invalid preview path.", 500)
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        frame = img.convert("RGB")
    except Exception as exc:
        raise ImagineError("Grok Imagine returned a file that is not an image.") from exc
    tmp = path.with_suffix(".tmp.jpg")
    frame.save(tmp, format="JPEG", quality=90, optimize=True)
    tmp.replace(path)
    meta = {
        "photo_id": photo_id,
        "prompt": prompt,
        "model": SHARPEN_MODEL,
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
        (p for p in IMAGINE_DIR.glob("*.jpg") if ".tmp." not in p.name),
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
