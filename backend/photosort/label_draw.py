"""Draw name tags onto a copy of a photo. Originals are never written."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import config as config_mod

LABEL_LONG_EDGE = 1600
_FACE_TONES = [
    (196, 90, 50),
    (31, 138, 122),
    (212, 160, 23),
    (61, 126, 201),
    (196, 77, 122),
    (74, 143, 58),
    (123, 94, 167),
    (224, 122, 47),
]
_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def overlay_faces(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vis = [f for f in (faces or []) if str(f.get("assigned_how") or "") != "junk"]
    vis.sort(key=lambda f: (float(f.get("x1") or 0), int(f.get("id") or 0)))
    return vis


def face_label(face: dict[str, Any]) -> str:
    if str(face.get("assigned_how") or "") == "junk":
        return ""
    name = str(face.get("person_name") or "").strip()
    if name and name != "unnamed" and not name.startswith("Unknown name of person"):
        return name
    if face.get("person_id"):
        return name or "unnamed"
    return ""


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _open_base(src: Path, photo_id: int | None) -> Image.Image:
    if photo_id is not None:
        view = config_mod.VIEW_DIR / f"{int(photo_id)}.jpg"
        if view.is_file() and view.stat().st_size > 0:
            img = Image.open(view)
            img.load()
            return img.convert("RGB")
    from .originals import open_image

    img = open_image(src)
    img = ImageOps.exif_transpose(img) or img
    return img.convert("RGB")


def _fit(img: Image.Image, long_edge: int = LABEL_LONG_EDGE) -> Image.Image:
    w, h = img.size
    long = max(w, h)
    if long <= long_edge:
        return img
    scale = long_edge / long
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)


def _apply_rotation(img: Image.Image, rotation: int) -> Image.Image:
    rot = ((int(rotation) or 0) % 360 + 360) % 360
    if rot == 90:
        return img.transpose(Image.Transpose.ROTATE_90)
    if rot == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if rot == 270:
        return img.transpose(Image.Transpose.ROTATE_270)
    return img


def _tag_xy(face: dict[str, Any], photo_w: float, photo_h: float, img_w: int, img_h: int) -> tuple[float, float]:
    tx = face.get("tag_x")
    ty = face.get("tag_y")
    if tx is not None and ty is not None:
        return float(tx) / 100.0 * img_w, float(ty) / 100.0 * img_h
    x1 = float(face.get("x1") or 0)
    y1 = float(face.get("y1") or 0)
    x2 = float(face.get("x2") or 0)
    y2 = float(face.get("y2") or 0)
    sx = img_w / max(photo_w, 1)
    sy = img_h / max(photo_h, 1)
    cx = (x1 + x2) / 2 * sx
    top = y1 * sy
    return cx, max(14.0, top - 10 * sy)


def draw_labels(img: Image.Image, faces: list[dict[str, Any]], photo_w: int, photo_h: int) -> Image.Image:
    named = []
    for face in overlay_faces(faces):
        label = face_label(face)
        if not label:
            continue
        named.append((face, label))
    if not named:
        return img
    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    img_w, img_h = img.size
    font_size = max(13, min(26, img_w // 55))
    font = _font(font_size)
    n_font = _font(max(11, font_size - 2))
    pw = float(photo_w or img_w)
    ph = float(photo_h or img_h)
    for i, (face, label) in enumerate(named):
        tone = _FACE_TONES[i % len(_FACE_TONES)]
        fill = (*tone, 230)
        n = str(i + 1)
        n_box = n_font.getbbox(n)
        n_w = max(16, n_box[2] - n_box[0] + 8)
        t_box = font.getbbox(label)
        t_w = t_box[2] - t_box[0]
        t_h = t_box[3] - t_box[1]
        h = max(22, t_h + 10)
        w = 10 + n_w + 6 + t_w + 12
        cx, cy = _tag_xy(face, pw, ph, img_w, img_h)
        left = min(img_w - w - 4, max(4, cx - w / 2))
        top = min(img_h - h - 4, max(4, cy - h / 2))
        draw.rounded_rectangle([left, top, left + w, top + h], radius=h / 2, fill=fill)
        nx = left + 6 + n_w / 2
        ny = top + h / 2
        draw.ellipse([nx - n_w / 2, ny - n_w / 2, nx + n_w / 2, ny + n_w / 2], fill=(255, 253, 250, 235))
        draw.text((nx, ny), n, font=n_font, fill=(26, 22, 18, 255), anchor="mm")
        draw.text((left + 8 + n_w, ny), label, font=font, fill=(255, 253, 250, 255), anchor="lm")
    out = Image.alpha_composite(img, overlay).convert("RGB")
    return out


def labeled_jpeg_bytes(
    src: Path,
    faces: list[dict[str, Any]],
    *,
    photo_id: int | None = None,
    photo_w: int = 0,
    photo_h: int = 0,
    rotation: int = 0,
) -> bytes:
    img = _open_base(src, photo_id)
    img = _fit(img)
    pw = int(photo_w) or img.size[0]
    ph = int(photo_h) or img.size[1]
    img = draw_labels(img, faces, pw, ph)
    img = _apply_rotation(img, rotation)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()
