from __future__ import annotations

from . import thread_limits  # noqa: F401

import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:
    cv2.setNumThreads(1)
except Exception:
    pass

from .config import CROP_DIR, CROP_PAD, CROP_SIZE, MIN_DET_SCORE, MIN_FACE_PX, MODEL_DIR, THUMB_DIR
from .db import connect, init_db
from .jobs import JobPaused, pause_requested, update_job
from .originals import is_preview_path, open_image, save_image
from .util import embedding_to_bytes, now_iso

_analyzer = None
_analyzer_error: str | None = None
_scene_stats_cache: dict[int, tuple[float, float, float, float, float]] = {}
_SCENE_STATS_CACHE_MAX = 256


def analyzer_status() -> dict:
    from . import adaface as adaface_mod

    return {
        "ready": _analyzer is not None,
        "error": _analyzer_error,
        "model": "insightface buffalo_l",
        "fallback": "adaface ir18",
        "fallback_status": adaface_mod.status(),
    }


def _cap_session_options(so) -> None:
    try:
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        import onnxruntime as ort

        sequential = getattr(ort, "ExecutionMode", None)
        if sequential is not None:
            so.execution_mode = sequential.ORT_SEQUENTIAL
    except Exception:
        pass


def _cap_onnx_threads() -> None:
    """buffalo_l loads several ONNX models; each defaults to a core-count pool.

    Keep InferenceSession as a class. Replacing it with a function breaks
    InsightFace (`class PickableInferenceSession(onnxruntime.InferenceSession)`).
    """
    try:
        import onnxruntime as ort
    except Exception:
        return
    if getattr(ort.InferenceSession, "_photosort_capped", False):
        return
    if not isinstance(ort.InferenceSession, type):
        return
    orig_init = ort.InferenceSession.__init__

    def capped_init(self, *args, **kwargs):
        so = kwargs.get("sess_options")
        if so is None:
            so = ort.SessionOptions()
            kwargs["sess_options"] = so
        _cap_session_options(so)
        return orig_init(self, *args, **kwargs)

    ort.InferenceSession.__init__ = capped_init
    ort.InferenceSession._photosort_capped = True
    try:
        from insightface.model_zoo.model_zoo import ModelRouter

        get_model = ModelRouter.get_model

        def capped_get(self, **kwargs):
            if kwargs.get("sess_options") is None:
                so = ort.SessionOptions()
                _cap_session_options(so)
                kwargs["sess_options"] = so
            return get_model(self, **kwargs)

        ModelRouter.get_model = capped_get
    except Exception:
        pass


def get_analyzer():
    global _analyzer, _analyzer_error
    if _analyzer is not None:
        return _analyzer
    try:
        from insightface.app import FaceAnalysis

        _cap_onnx_threads()
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        app = FaceAnalysis(
            name="buffalo_l",
            root=str(MODEL_DIR),
            providers=["CPUExecutionProvider"],
            provider_options=[{"intra_op_num_threads": 1, "inter_op_num_threads": 1}],
        )
        app.prepare(ctx_id=0, det_size=(640, 640))
        _analyzer = app
        _analyzer_error = None
        return _analyzer
    except Exception as exc:  # noqa: BLE001
        _analyzer_error = str(exc)
        raise


def embeddings_from_image_bytes(data: bytes, *, max_side: int = 1280) -> list[np.ndarray]:
    """Detect faces in an uploaded photo. Does not write the file anywhere."""
    from io import BytesIO

    if not data:
        return []
    try:
        img = Image.open(BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:
        raise ValueError("Could not read that photo.") from exc
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    rgb = np.array(img)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    analyzer = get_analyzer()
    detected = analyzer.get(bgr) or []
    scored: list[tuple[float, np.ndarray]] = []
    for face in detected:
        emb = getattr(face, "normed_embedding", None)
        if emb is None:
            continue
        vec = np.asarray(emb, dtype=np.float32)
        if vec.size == 0:
            continue
        box = tuple(float(v) for v in getattr(face, "bbox", (0, 0, 0, 0)))
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        score = float(getattr(face, "det_score", 0.0)) * max(area, 1.0)
        scored.append((score, vec))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored]


def _load_bgr(path: Path) -> np.ndarray:
    img = open_image(path)
    img = ImageOps.exif_transpose(img)
    rgb = np.array(img.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _catalog_rotation(photo_row) -> int:
    try:
        return int(photo_row["rotation"] or 0) % 360
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _rotate_pil(img: Image.Image, rot: int) -> Image.Image:
    """Match on-screen clockwise rotate. Originals on disk stay as they are."""
    rot = ((int(rot) % 360) + 360) % 360
    if rot == 90:
        return img.transpose(Image.Transpose.ROTATE_270)
    if rot == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if rot == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def box_from_rotated(box: tuple[float, float, float, float], rot: int, orig_w: float, orig_h: float):
    """Map a box from the clockwise-rotated detect image back onto the original file."""
    x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    rot = ((int(rot) % 360) + 360) % 360
    if rot == 0:
        return (x1, y1, x2, y2)

    def one(x: float, y: float) -> tuple[float, float]:
        if rot == 90:
            return (y, orig_h - x)
        if rot == 180:
            return (orig_w - x, orig_h - y)
        if rot == 270:
            return (orig_w - y, x)
        return (x, y)

    pts = [one(x1, y1), one(x2, y1), one(x1, y2), one(x2, y2)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (
        max(0.0, min(xs)),
        max(0.0, min(ys)),
        min(float(orig_w), max(xs)),
        min(float(orig_h), max(ys)),
    )


def box_to_rotated(box: tuple[float, float, float, float], rot: int, orig_w: float, orig_h: float):
    """Map a box on the original file onto the clockwise-rotated detect image."""
    x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    rot = ((int(rot) % 360) + 360) % 360
    if rot == 0:
        return (x1, y1, x2, y2)

    def one(x: float, y: float) -> tuple[float, float]:
        if rot == 90:
            return (orig_h - y, x)
        if rot == 180:
            return (orig_w - x, orig_h - y)
        if rot == 270:
            return (y, orig_w - x)
        return (x, y)

    pts = [one(x1, y1), one(x2, y1), one(x1, y2), one(x2, y2)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    rw = orig_h if rot in (90, 270) else orig_w
    rh = orig_w if rot in (90, 270) else orig_h
    return (
        max(0.0, min(xs)),
        max(0.0, min(ys)),
        min(float(rw), max(xs)),
        min(float(rh), max(ys)),
    )


def _load_detect(photo_row) -> tuple[np.ndarray, float, float, int, float, float]:
    """BGR image for the detector, plus scale back to the original pixel size.

    Catalog rotation is applied so a photo you turned upright is detected upright.
    Boxes are mapped back onto the original file coordinates.
    """
    orig_w = float(photo_row["width"] or 0)
    orig_h = float(photo_row["height"] or 0)
    rot = _catalog_rotation(photo_row)
    thumb = THUMB_DIR / f"{int(photo_row['id'])}.jpg"
    if thumb.is_file():
        img = Image.open(thumb)
        img.load()
        img = img.convert("RGB")
    else:
        img = Image.fromarray(cv2.cvtColor(_load_bgr(Path(photo_row["path"])), cv2.COLOR_BGR2RGB))
    img = _rotate_pil(img, rot)
    rgb = np.array(img)
    dw, dh = img.size
    if rot in (90, 270):
        sx = orig_h / dw if dw and orig_h else 1.0
        sy = orig_w / dh if dh and orig_w else 1.0
    else:
        sx = orig_w / dw if dw and orig_w else 1.0
        sy = orig_h / dh if dh and orig_h else 1.0
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), sx, sy, rot, orig_w, orig_h


def _square_box(x1: float, y1: float, x2: float, y2: float, photo_w: int, photo_h: int) -> tuple[int, int, int, int]:
    """Pad the face, then grow to a square so the crop is not stretched."""
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    side = max(bw, bh) * (1.0 + 2.0 * CROP_PAD)
    side = min(side, float(min(photo_w, photo_h)))
    half = side / 2
    left = int(round(cx - half))
    top = int(round(cy - half))
    right = int(round(cx + half))
    bottom = int(round(cy + half))
    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > photo_w:
        left -= right - photo_w
        right = photo_w
    if bottom > photo_h:
        top -= bottom - photo_h
        bottom = photo_h
    left = max(0, left)
    top = max(0, top)
    return left, top, right, bottom


def save_crop(
    photo_path: Path,
    photo_w: int,
    photo_h: int,
    box: tuple[float, float, float, float],
    face_id: int,
    *,
    photo_id: int | None = None,
    rotation: int = 0,
) -> Path:
    dest = CROP_DIR / f"{face_id}.jpg"
    w, h = int(photo_w or 0), int(photo_h or 0)
    x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    img = None
    if photo_id is not None:
        thumb = THUMB_DIR / f"{int(photo_id)}.jpg"
        if thumb.is_file():
            img = Image.open(thumb)
            img.load()
            img = img.convert("RGB")
            tw, th = img.size
            if w > 0 and h > 0 and tw > 0 and th > 0:
                x1 *= tw / w
                x2 *= tw / w
                y1 *= th / h
                y2 *= th / h
                w, h = tw, th
    if img is None:
        img = open_image(photo_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
    left, top, right, bottom = _square_box(x1, y1, x2, y2, w, h)
    crop = img.crop((left, top, right, bottom))
    if rotation:
        crop = _rotate_pil(crop, rotation)
    native = min(crop.size)
    crop = crop.resize((CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS)
    # Sharpen after scale. Does not invent new identity features.
    percent = 165 if native < CROP_SIZE else 115
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1.3, percent=percent, threshold=2))
    return save_image(crop, dest, format="JPEG", quality=92, optimize=True)


def refresh_photo_crops(photo_id: int, conn=None) -> int:
    """Rewrite face crops for the photo's current catalog rotation.

    Crops are display-upright. Originals on disk are not changed.
    """
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (int(photo_id),)).fetchone()
        if not row:
            return 0
        rot = _catalog_rotation(row)
        faces = conn.execute(
            "SELECT id, x1, y1, x2, y2 FROM faces WHERE photo_id = ?",
            (int(photo_id),),
        ).fetchall()
        path = Path(row["path"])
        n = 0
        for face in faces:
            try:
                save_crop(
                    path,
                    int(row["width"] or 0),
                    int(row["height"] or 0),
                    (face["x1"], face["y1"], face["x2"], face["y2"]),
                    int(face["id"]),
                    photo_id=int(photo_id),
                    rotation=rot,
                )
                n += 1
            except Exception:
                pass
        return n
    finally:
        if own:
            conn.close()


def rebuild_all_crops() -> dict:
    """Rewrite face previews from the originals at the current crop size."""
    from .originals import is_preview_path

    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.x1, f.y1, f.x2, f.y2, p.path, p.width, p.height
            FROM faces f JOIN photos p ON p.id = f.photo_id
            """
        ).fetchall()
    finally:
        conn.close()
    done = 0
    skipped = 0
    for row in rows:
        path = Path(row["path"])
        if is_preview_path(path) or not path.exists():
            skipped += 1
            continue
        try:
            save_crop(path, int(row["width"] or 0), int(row["height"] or 0), (row["x1"], row["y1"], row["x2"], row["y2"]), int(row["id"]))
            done += 1
        except Exception:
            skipped += 1
    return {"rewritten": done, "skipped": skipped}


def _quality(det_score: float, box: tuple[float, float, float, float]) -> str:
    w = box[2] - box[0]
    h = box[3] - box[1]
    if det_score < MIN_DET_SCORE or min(w, h) < MIN_FACE_PX:
        return "unidentifiable"
    return "ok"


def _chroma_fraction(path: Path) -> float:
    try:
        img = Image.open(path).convert("RGB")
        img.thumbnail((256, 256))
        arr = np.asarray(img, dtype=np.float32)
    except OSError:
        return 0.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    return float((chroma >= 12).mean())


def _orange_gold_mask(arr: np.ndarray, *, sat_min: float = 0.28) -> np.ndarray:
    """Gold leaf / brass: yellow-orange, little blue. Includes orange temple gold."""
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    hsv = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue = hsv[:, :, 0] * (360.0 / 255.0)
    sat = hsv[:, :, 1] / 255.0
    return (
        (chroma >= 12)
        & (sat >= sat_min)
        & (hue >= 20)
        & (hue <= 62)
        & (g > b + 8)
        & (r > b + 14)
    )


def _sand_mask(arr: np.ndarray) -> np.ndarray:
    """Beige sand: modest chroma, R slightly above G above B. Not pink skin or gold leaf."""
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    hsv = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    sat = hsv[:, :, 1] / 255.0
    return (
        (chroma >= 10)
        & (chroma <= 48)
        & (r > g)
        & (r > b + 6)
        & (g >= b)
        & ((r - g) <= 22)
        & (sat >= 0.12)
        & (sat <= 0.38)
    )


def _edge_strength(arr: np.ndarray) -> float:
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    gray = 0.3 * r + 0.59 * g + 0.11 * b
    if gray.shape[0] < 2:
        return 0.0
    return float(np.abs(gray[1:, :] - gray[:-1, :]).mean())


def _poster_lettering(arr: np.ndarray) -> bool:
    """True when the crop has printed title lettering (movie posters, comics)."""
    if arr.ndim != 3 or arr.shape[0] < 16 or arr.shape[1] < 16:
        return False
    gray = 0.3 * arr[:, :, 0] + 0.59 * arr[:, :, 1] + 0.11 * arr[:, :, 2]
    h = int(gray.shape[0])

    def band(y0: int, y1: int) -> tuple[float, float]:
        g = gray[max(0, y0) : max(y1, y0 + 2)]
        if g.shape[1] < 8 or g.shape[0] < 2:
            return 0.0, 0.0
        dx = np.abs(np.diff(g, axis=1))
        sharp = float((dx >= 22).mean())
        col = (dx >= 22).mean(axis=0)
        return sharp, float(col.std())

    top_s, top_c = band(0, int(h * 0.32))
    bot_s, bot_c = band(int(h * 0.68), h)
    return max(top_s, bot_s) >= 0.033 and max(top_c, bot_c) >= 0.065


def _scene_stats(
    photo_path: Path | str | None, photo_id: int | None
) -> tuple[float, float, float, float, float] | None:
    """Gold, gray, colour, beige-sand, and cool-hue fractions of the surrounding photo."""
    if photo_id is not None and int(photo_id) in _scene_stats_cache:
        return _scene_stats_cache[int(photo_id)]
    img = None
    if photo_id is not None:
        thumb = THUMB_DIR / f"{int(photo_id)}.jpg"
        if thumb.is_file():
            try:
                img = Image.open(thumb).convert("RGB")
            except OSError:
                img = None
    if img is None and photo_path:
        path = Path(photo_path)
        if path.is_file():
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((256, 256))
            except OSError:
                img = None
    if img is None:
        return None
    arr = np.asarray(img)
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    gold_n = float(_orange_gold_mask(arr, sat_min=0.35).mean())
    gray_n = float((chroma < 20).mean())
    color_n = float((chroma >= 12).mean())
    sand_n = float(_sand_mask(arr).mean())
    hsv = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue = hsv[:, :, 0] * (360.0 / 255.0)
    sat = hsv[:, :, 1] / 255.0
    cool_n = float(((hue >= 80) & (hue <= 260) & (chroma >= 12) & (sat >= 0.12)).mean())
    stats = (gold_n, gray_n, color_n, sand_n, cool_n)
    if photo_id is not None:
        if len(_scene_stats_cache) >= _SCENE_STATS_CACHE_MAX:
            _scene_stats_cache.clear()
        _scene_stats_cache[int(photo_id)] = stats
    return stats


def _scene_gold_fraction(photo_path: Path | str | None, photo_id: int | None) -> float | None:
    """How much of the surrounding photo is gold metal. None if no image to open."""
    stats = _scene_stats(photo_path, photo_id)
    return None if stats is None else stats[0]


def _open_scene_image(photo_path: Path | str | None, photo_id: int | None) -> Image.Image | None:
    img = None
    if photo_id is not None:
        thumb = THUMB_DIR / f"{int(photo_id)}.jpg"
        if thumb.is_file():
            try:
                img = Image.open(thumb).convert("RGB")
            except OSError:
                img = None
    if img is None and photo_path:
        path = Path(photo_path)
        if path.is_file():
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((256, 256))
            except OSError:
                img = None
    return img


def _scene_mean_rgb(photo_path: Path | str | None, photo_id: int | None) -> np.ndarray | None:
    """Mean RGB of the surrounding photo. None if no image to open."""
    img = _open_scene_image(photo_path, photo_id)
    if img is None:
        return None
    return np.asarray(img, dtype=np.float32).reshape(-1, 3).mean(0)


def _scene_luma_std(photo_path: Path | str | None, photo_id: int | None) -> float | None:
    """How much the surrounding photo varies in brightness. Flat tungsten is ~0."""
    img = _open_scene_image(photo_path, photo_id)
    if img is None:
        return None
    arr = np.asarray(img, dtype=np.float32)
    luma = 0.3 * arr[:, :, 0] + 0.59 * arr[:, :, 1] + 0.11 * arr[:, :, 2]
    return float(luma.std())


def _scene_is_colour(photo_path: Path | str | None, photo_id: int | None) -> bool:
    """True when the album photo has real colour, not a black-and-white print."""
    stats = _scene_stats(photo_path, photo_id)
    if stats is not None:
        return stats[2] >= 0.15
    if photo_path:
        return _chroma_fraction(Path(photo_path)) >= 0.15
    return False


def _scene_frame_metrics(
    photo_path: Path | str | None, photo_id: int | None
) -> tuple[float, float, float, float] | None:
    """Border gold, cream+gold frame, inner blue, and border blue of the photo."""
    img = _open_scene_image(photo_path, photo_id)
    if img is None:
        return None
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape[:2]
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    val = np.maximum(np.maximum(r, g), b) / 255.0
    blue = (b > r + 8) & (b > g + 4) & (chroma >= 12) & (val >= 0.25)
    gold = _orange_gold_mask(arr, sat_min=0.28)
    cream = (chroma < 40) & (val >= 0.55) & (r >= g) & (r > b + 4)
    border = np.zeros((h, w), dtype=bool)
    bh, bw = max(int(h * 0.12), 4), max(int(w * 0.12), 4)
    border[:bh] = True
    border[-bh:] = True
    border[:, :bw] = True
    border[:, -bw:] = True
    inner = ~border
    if not inner.any() or not border.any():
        return None
    return (
        float(gold[border].mean()),
        float((gold | cream)[border].mean()),
        float(blue[inner].mean()),
        float(blue[border].mean()),
    )


def _scene_gilt_picture_frame(photo_path: Path | str | None, photo_id: int | None) -> bool:
    """Gold moulding around a painted map panel, not rocks under a pale sky.

    Gallery frames are gold on the border and olive/blue *inside*. Outdoor
    rock+sky photos look gold on both the border and the interior.
    """
    img = _open_scene_image(photo_path, photo_id)
    if img is None:
        return False
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape[:2]
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    val = np.maximum(np.maximum(r, g), b) / 255.0
    hsv = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue = hsv[:, :, 0] * (360.0 / 255.0)
    sat = hsv[:, :, 1] / 255.0
    blue = (b > r + 8) & (b > g + 4) & (chroma >= 12) & (val >= 0.25)
    olive = (hue >= 35) & (hue <= 150) & (sat >= 0.10) & (chroma >= 8) & (g >= b) & (g >= r - 18)
    gold = _orange_gold_mask(arr, sat_min=0.28)
    cream = (chroma < 40) & (val >= 0.55) & (r >= g) & (r > b + 4)
    border = np.zeros((h, w), dtype=bool)
    bh, bw = max(int(h * 0.12), 4), max(int(w * 0.12), 4)
    border[:bh] = True
    border[-bh:] = True
    border[:, :bw] = True
    border[:, -bw:] = True
    inner = ~border
    if not inner.any() or not border.any():
        return False
    border_gold = float(gold[border].mean())
    border_frame = float((gold | cream)[border].mean())
    border_olive = float(olive[border].mean())
    inner_blue = float(blue[inner].mean())
    inner_olive = float(olive[inner].mean())
    border_blue = float(blue[border].mean())
    inner_map = float((blue | olive)[inner].mean())
    return (
        border_gold >= 0.18
        and border_frame >= 0.28
        and border_olive < 0.10
        and inner_map >= 0.28
        and (
            inner_olive >= border_olive + 0.12
            or inner_blue >= border_blue + 0.10
        )
    )


def _scene_framed_painted_map(photo_path: Path | str | None, photo_id: int | None) -> bool:
    """Gilt picture-frame around a painted sea (Gallery of Maps / fresco wall).

    Outdoor group shots and train windows also have blue, so the sea has to
    sit *inside* a gold/cream border, not as a global colour cast.
    """
    metrics = _scene_frame_metrics(photo_path, photo_id)
    if metrics is None:
        return False
    border_gold, border_frame, inner_blue, border_blue = metrics
    return (
        inner_blue >= 0.25
        and border_gold >= 0.12
        and border_frame >= 0.28
        and inner_blue >= border_blue + 0.10
    )


def looks_like_statue(
    crop_path: Path,
    photo_path: Path | str | None = None,
    photo_id: int | None = None,
) -> bool:
    """Bronze, gold, stone, sand, printed posters, and diagram badges that are not people.

    Gold Buddhas read as "skin" if we only test R>G>B. Treat yellow metal
    separately. Orange gold-leaf (temple statues) needs a gold object in a
    mixed-colour photo so tungsten portraits are not hidden. Black-and-white
    family prints are skipped unless the surrounding photo is clearly colour
    (gray stone in a colour shot). A bronze head tight-cropped against blue
    sky is mostly colour, so gray-share alone is not enough. Sand sculptures
    are beige like skin, so they need a mixed-colour scene (sky/plants) and
    must not look like a sepia print of a real person. Org-chart circles on
    a projected slide are saturated fill plus pale lettering on a grey page.
    """
    try:
        img = Image.open(crop_path).convert("RGB")
        arr = np.asarray(img, dtype=np.float32)
    except OSError:
        return False
    h, w = arr.shape[:2]
    y0, y1 = int(h * 0.18), max(int(h * 0.82), int(h * 0.18) + 1)
    x0, x1 = int(w * 0.18), max(int(w * 0.82), int(w * 0.18) + 1)
    core = arr[y0:y1, x0:x1]
    r, g, b = core[:, :, 0], core[:, :, 1], core[:, :, 2]
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    color = chroma >= 12
    warm = (r > g + 6) & (r > b + 8)
    hsv = np.asarray(Image.fromarray(core.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue = hsv[:, :, 0] * (360.0 / 255.0)
    sat = hsv[:, :, 1] / 255.0
    # Pale skin is warm with modest saturation. Brass/gold leaf is yellower.
    gold = color & (sat >= 0.38) & (g > b + 10) & (r > b + 16) & (np.abs(r - g) <= 48)
    skin = warm & ~gold
    green = (g > r + 2) & (g >= b - 8) & color
    gray = chroma < 20
    yellow = color & (sat >= 0.32) & (hue >= 36) & (hue <= 80)
    skin_n = float(skin.mean())
    gold_n = float(gold.mean())
    yellow_n = float(yellow.mean())
    green_n = float(green.mean())
    gray_n = float(gray.mean())
    color_n = float(color.mean())
    if skin_n < 0.08 and float(sat.mean()) <= 0.32 and _scene_is_colour(photo_path, photo_id):
        # Gray metal/stone head in a colour photo (Tian Tan Buddha vs B&W prints).
        if gray_n >= 0.85:
            return True
        inner = arr[
            int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        inner_hsv = np.asarray(Image.fromarray(inner.astype(np.uint8)).convert("HSV"), dtype=np.float32)
        inner_val = float(inner_hsv[:, :, 2].mean() / 255.0)
        # Dark bronze, not a blown-out face or a near-black silhouette.
        if gray_n >= 0.70 and 0.12 <= inner_val <= 0.45:
            return True
        # Tight crop against blue sky: surround is colour so gray_n is low,
        # while the inner stays cool mid-tone metal. Portraits keep warm skin.
        if skin_n < 0.01 and 0.12 <= inner_val <= 0.55:
            ih = inner_hsv[:, :, 0] * (360.0 / 255.0)
            inner_cool = float(((ih >= 80) & (ih <= 260)).mean())
            inner_warm = float(((ih <= 40) | (ih >= 330)).mean())
            ring = np.ones((h, w), dtype=bool)
            ring[
                int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
                int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
            ] = False
            surr = arr[ring]
            sr, sg, sb = surr[:, 0], surr[:, 1], surr[:, 2]
            schroma = np.maximum(np.maximum(sr, sg), sb) - np.minimum(np.minimum(sr, sg), sb)
            surr_val = np.maximum(np.maximum(sr, sg), sb) / 255.0
            surr_sky = float(((sb > sr + 8) & (sb > sg + 4) & (schroma >= 12) & (surr_val >= 0.45)).mean())
            surr_pale = float(((schroma < 28) & (surr_val >= 0.50)).mean())
            if inner_cool >= 0.45 and inner_warm < 0.12 and (surr_sky >= 0.45 or surr_pale >= 0.45):
                return True
    # Gilded statue with a dark painted face (Egyptian ka statue in a case).
    # Gold nemes lives on the sides; the inner is low-chroma paint plus maybe a
    # glass-case flash. A person in a gold hat still has a warm-skin inner.
    # Runs before the colour-share gate: the face itself is black paint.
    face_box = np.zeros((h, w), dtype=bool)
    fy0, fy1 = int(h * 0.34), max(int(h * 0.66), int(h * 0.34) + 1)
    fx0, fx1 = int(w * 0.34), max(int(w * 0.66), int(w * 0.34) + 1)
    face_box[fy0:fy1, fx0:fx1] = True
    hsv_all = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    val_all = hsv_all[:, :, 2] / 255.0
    inner_keep = face_box & (val_all < 0.82)
    gold_map = _orange_gold_mask(arr)
    ring_gold = float(gold_map[~face_box].mean()) if (~face_box).any() else 0.0
    if int(inner_keep.sum()) >= 80 and ring_gold >= 0.16:
        fchroma = np.maximum(np.maximum(arr[:, :, 0], arr[:, :, 1]), arr[:, :, 2]) - np.minimum(
            np.minimum(arr[:, :, 0], arr[:, :, 1]), arr[:, :, 2]
        )
        fwarm = (arr[:, :, 0] > arr[:, :, 1] + 6) & (arr[:, :, 0] > arr[:, :, 2] + 8)
        if (
            float((fchroma[inner_keep] < 24).mean()) >= 0.68
            and float(fwarm[inner_keep].mean()) < 0.14
            and _edge_strength(core) >= 7.0
        ):
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, scene_gray, scene_color, _scene_sand, scene_cool = stats
                if (
                    scene_cool < 0.12
                    and scene_gray >= 0.38
                    and scene_color >= 0.28
                    and scene_gold >= 0.05
                ):
                    return True
    if color_n < 0.4:
        return False
    # Gold / brass Buddhas: yellow metal fills the head, almost no pink skin.
    if gold_n >= 0.68 and skin_n < 0.12:
        return True
    # Soft-focus gilded ornament (gondola seahorse): a little of the metal
    # reads as skin. Needs a gold-heavy colour scene with real brightness
    # range so tungsten portraits stay named. Crop-only stays False.
    if gold_n >= 0.72 and skin_n < 0.16 and float(_orange_gold_mask(core).mean()) >= 0.65:
        edges = _edge_strength(core)
        if 3.5 <= edges <= 6.5:
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, scene_gray, scene_color, _scene_sand, scene_cool = stats
                luma_std = _scene_luma_std(photo_path, photo_id)
                if (
                    scene_gold >= 0.50
                    and scene_color >= 0.80
                    and scene_cool < 0.06
                    and 0.15 <= scene_gray <= 0.25
                    and luma_std is not None
                    and luma_std >= 50
                ):
                    return True
    if yellow_n >= 0.50 and skin_n < 0.12 and float(sat.mean()) >= 0.42:
        return True
    if green_n >= 0.33 and skin_n < 0.25:
        return True
    # Darker bronze / carved stone: some green-gray, almost no warm skin.
    if green_n >= 0.18 and skin_n < 0.12:
        return True
    # Lanterns / stone objects in a colour photo: almost no warm skin, mostly gray.
    if skin_n < 0.08 and gray_n >= 0.7:
        return True
    # Orange gold-leaf (Chinese temple statues): R>>G still metal, so the brass
    # |R-G| test treats it as skin. Confirm a sharp gold head+body sitting in a
    # mixed-colour scene, not a tungsten portrait of a real person.
    if float((r - b).mean()) >= 24 and float((g - b).mean()) >= 8 and _edge_strength(core) >= 8.0:
        iy0, iy1 = int(h * 0.30), max(int(h * 0.70), int(h * 0.30) + 1)
        ix0, ix1 = int(w * 0.30), max(int(w * 0.70), int(w * 0.30) + 1)
        inner = arr[iy0:iy1, ix0:ix1]
        bot = arr[int(h * 0.70) :]
        if bot.size == 0:
            bot = core
        inner_n = float(_orange_gold_mask(inner).mean())
        bot_n = float(_orange_gold_mask(bot).mean())
        if inner_n >= 0.70 and bot_n >= 0.28:
            scene = _scene_gold_fraction(photo_path, photo_id)
            if scene is not None and 0.10 <= scene <= 0.28:
                return True
    # Painted gold face (temple guardian): the inner crop is gold paint, not
    # skin, in a mixed temple scene. A person with a gold hat keeps a skin inner.
    inner = arr[
        int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
        int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
    ]
    if gold_n >= 0.42 and skin_n < 0.30 and _edge_strength(core) >= 7.5:
        inner_gold = float(_orange_gold_mask(inner).mean())
        if inner_gold >= 0.52:
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, _scene_gray, scene_color = stats[0], stats[1], stats[2]
                if 0.08 <= scene_gold <= 0.40 and scene_color >= 0.45:
                    return True
    # Painted temple relief / door god: gold ornamental headdress, brown
    # painted face, stone architecture around it — not a person in a gold hat.
    top = arr[: max(int(h * 0.40), 1)]
    if float((top[:, :, 0] - top[:, :, 2]).mean()) >= 24 and _edge_strength(top) >= 7.0:
        inner = arr[
            int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        bot = arr[int(h * 0.70) :]
        if bot.size == 0:
            bot = core
        og_top = float(_orange_gold_mask(top, sat_min=0.40).mean())
        og_in = float(_orange_gold_mask(inner, sat_min=0.40).mean())
        og_bot = float(_orange_gold_mask(bot, sat_min=0.40).mean())
        hsv_in = np.asarray(Image.fromarray(inner.astype(np.uint8)).convert("HSV"), dtype=np.float32)
        hue_in = hsv_in[:, :, 0] * (360.0 / 255.0)
        sat_in = hsv_in[:, :, 1] / 255.0
        brown_n = float(((hue_in >= 8) & (hue_in <= 35) & (sat_in >= 0.18) & (sat_in <= 0.55)).mean())
        if (
            og_top >= 0.55
            and og_in <= 0.40
            and og_top - og_in >= 0.25
            and brown_n >= 0.55
            and og_bot <= 0.20
        ):
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, scene_gray = stats[0], stats[1]
                if 0.10 <= scene_gold <= 0.22 and scene_gray >= 0.40:
                    return True
    # Dark cave-carved stone (Elephanta-style relief). Warm R>G>B on near-black
    # brown reads as skin, but a real face is not this dark and does not match
    # the cave wall. Crop-only stays False. Dim indoor portraits stay named.
    inner_hsv = np.asarray(Image.fromarray(core.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    inner_val = float(inner_hsv[:, :, 2].mean() / 255.0)
    pink_n = float(((r > g + 24) & (r > b + 20)).mean())
    if inner_val <= 0.22 and gray_n >= 0.62 and pink_n < 0.06 and float(sat.mean()) <= 0.55:
        stats = _scene_stats(photo_path, photo_id)
        if stats is not None:
            _scene_gold, scene_gray, _scene_color, _scene_sand, scene_cool = stats
            if scene_cool < 0.08 and scene_gray >= 0.45:
                scene_mean = _scene_mean_rgb(photo_path, photo_id)
                if scene_mean is not None:
                    crop_mean = core.reshape(-1, 3).mean(0)
                    if (
                        float(np.linalg.norm(crop_mean - scene_mean)) <= 22
                        and float(crop_mean.mean()) <= 50
                    ):
                        return True
    # Sand sculpture / sand art: the crop is grainy beige, not pink skin.
    # Require a mixed-colour scene (sky, plants) so sepia family prints and
    # tungsten portraits stay named. Crop-only calls stay False.
    sand = (
        (chroma >= 10)
        & (chroma <= 48)
        & (r > g)
        & (r > b + 6)
        & (g >= b)
        & ((r - g) <= 22)
        & (sat >= 0.12)
        & (sat <= 0.38)
    )
    pink = (r > g + 24) & (r > b + 20)
    chroma_s = float(chroma.std())
    if float(sand.mean()) >= 0.62 and float(pink.mean()) < 0.12 and 4.0 <= chroma_s <= 12.0:
        stats = _scene_stats(photo_path, photo_id)
        if stats is not None:
            scene_sand, scene_cool = stats[3], stats[4]
            if scene_cool >= 0.22 and scene_sand >= 0.18:
                return True
    # Carved limestone / sandstone in a grey museum room (Egyptian stelae).
    # Beige reads as skin, but the crop is the same stone as the photo, with
    # carved edges, a grey wall, and no sky. Crop-only calls stay False.
    stats = _scene_stats(photo_path, photo_id)
    if stats is not None:
        _scene_gold, scene_gray, scene_color, scene_sand, scene_cool = stats
        sand_n = float(_sand_mask(core).mean())
        pink_n = float(pink.mean())
        if (
            0.50 <= scene_gray <= 0.82
            and 0.30 <= scene_color <= 0.70
            and scene_cool < 0.08
            and scene_sand >= 0.12
            and sand_n >= 0.28
            and pink_n < 0.16
            and _edge_strength(core) >= 8.0
        ):
            scene_mean = _scene_mean_rgb(photo_path, photo_id)
            if scene_mean is not None:
                crop_mean = core.reshape(-1, 3).mean(0)
                if float(np.linalg.norm(crop_mean - scene_mean)) <= 28:
                    return True
    # Ochre sandstone / painted temple wall filling the frame (Egyptian relief).
    # The "face" is the same orange-gold stone as the photo. Smooth paint on
    # stone has low edges, so texture is not required. Warm indoor portraits
    # are not 85%+ gold. Crop-only stays False.
    stats = _scene_stats(photo_path, photo_id)
    if stats is not None:
        scene_gold, scene_gray, _scene_color, _scene_sand, scene_cool = stats
        crop_gold = float(_orange_gold_mask(core).mean())
        if (
            scene_gold >= 0.85
            and scene_cool < 0.08
            and scene_gray < 0.15
            and crop_gold >= 0.50
        ):
            luma_std = _scene_luma_std(photo_path, photo_id)
            scene_mean = _scene_mean_rgb(photo_path, photo_id)
            if luma_std is not None and luma_std >= 6.0 and scene_mean is not None:
                crop_mean = core.reshape(-1, 3).mean(0)
                if float(np.linalg.norm(crop_mean - scene_mean)) <= 25:
                    return True
    # White marble temple idol: gold halo/crown, pale low-chroma inner face,
    # grey shrine around it. A person in a gold hat keeps warm skin in the inner.
    # Crop-only stays False.
    stats = _scene_stats(photo_path, photo_id)
    if stats is not None:
        _scene_gold, scene_gray, scene_color, _scene_sand, scene_cool = stats
        top = arr[: max(int(h * 0.40), 1)]
        inner = arr[
            int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
        ichroma = np.maximum(np.maximum(ir, ig), ib) - np.minimum(np.minimum(ir, ig), ib)
        inner_hsv = np.asarray(Image.fromarray(inner.astype(np.uint8)).convert("HSV"), dtype=np.float32)
        inner_val = float(inner_hsv[:, :, 2].mean() / 255.0)
        inner_pink = float(((ir > ig + 24) & (ir > ib + 20)).mean())
        if (
            scene_cool < 0.12
            and scene_gray >= 0.40
            and scene_color >= 0.28
            and float(_orange_gold_mask(top).mean()) >= 0.40
            and float(_orange_gold_mask(inner).mean()) <= 0.12
            and float((ichroma < 20).mean()) >= 0.55
            and 0.35 <= inner_val <= 0.75
            and inner_pink < 0.12
        ):
            return True
    # Painted putto / cartouche on a wall map: flesh-coloured fresco in a
    # gilt frame around painted sea + land. Crop-only stays False. Outdoor
    # groups and train windows have blue, but not a gold picture-frame.
    if _scene_framed_painted_map(photo_path, photo_id):
        ring = np.ones((h, w), dtype=bool)
        ring[
            int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ] = False
        surr = arr[ring]
        sr, sg, sb = surr[:, 0], surr[:, 1], surr[:, 2]
        schroma = np.maximum(np.maximum(sr, sg), sb) - np.minimum(np.minimum(sr, sg), sb)
        surr_blue = float(((sb > sr + 8) & (sb > sg + 4) & (schroma >= 12)).mean())
        surr_green = float(((sg > sr + 2) & (sg >= sb - 8) & (schroma >= 12)).mean())
        mapped = arr[
            int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        mr, mg, mb = mapped[:, :, 0], mapped[:, :, 1], mapped[:, :, 2]
        inner_skin = float(((mr > mg + 6) & (mr > mb + 8)).mean())
        inner_pink = float(((mr > mg + 24) & (mr > mb + 20)).mean())
        if (
            surr_blue >= 0.10
            and surr_green >= 0.06
            and inner_skin >= 0.70
            and inner_pink <= 0.40
        ):
            return True
    # Terracotta / stone bust against a window in a gilt gallery. Smooth carved
    # head, window fill, gold picture-frame. People walking the hall have
    # sharper faces and are not tight-cropped on a window. Crop-only stays False.
    clay = color & (sat >= 0.18) & (sat <= 0.42) & (r > g) & (r > b + 8) & (g >= b - 4)
    if _edge_strength(core) <= 2.2 and (gold_n >= 0.30 or float(clay.mean()) >= 0.30) and float(sat.mean()) <= 0.42:
        ring = np.ones((h, w), dtype=bool)
        ring[
            int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ] = False
        surr = arr[ring]
        sr, sg, sb = surr[:, 0], surr[:, 1], surr[:, 2]
        schroma = np.maximum(np.maximum(sr, sg), sb) - np.minimum(np.minimum(sr, sg), sb)
        surr_blue = float(((sb > sr + 8) & (sb > sg + 4) & (schroma >= 12)).mean())
        if surr_blue >= 0.42 and _scene_gilt_picture_frame(photo_path, photo_id):
            return True
    # Painted mural / illustrated panel: a smooth gold-crowned face in a
    # colourful scene with some painted sky or trees. Real outdoor sky is
    # cooler than 0.28. Skin is too red to count as metal gold. Crop-only stays False.
    if _edge_strength(core) <= 2.2:
        top = arr[: max(int(h * 0.40), 1)]
        tr, tg, _tb = top[:, :, 0], top[:, :, 1], top[:, :, 2]
        crown = _orange_gold_mask(top) & ((tr - tg) <= 42)
        if float(crown.mean()) >= 0.50:
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                _sg, _sgray, scene_color, _ssand, scene_cool = stats
                if 0.12 <= scene_cool <= 0.25 and scene_color >= 0.85:
                    return True
    # Gallery painting of a bronze/ink statue: hatched drawing, gold
    # highlights, hanging on a grey indoor wall. Crop-only stays False.
    if _edge_strength(core) >= 8.0:
        top = arr[: max(int(h * 0.40), 1)]
        inner = arr[
            int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
        inner_pink = float(((ir > ig + 24) & (ir > ib + 20)).mean())
        if (
            float(_orange_gold_mask(top).mean()) >= 0.25
            and gray_n >= 0.30
            and inner_pink < 0.12
        ):
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                _sg, scene_gray, scene_color, _ssand, scene_cool = stats
                if scene_gray >= 0.40 and scene_cool < 0.10 and scene_color >= 0.45:
                    return True
    # Org-chart / slide badge: pale lettering on a flat saturated disc, grey
    # projector screen around it. Distant faces on overcast days stay named.
    # Crop-only stays False.
    inner = arr[
        int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
        int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
    ]
    ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
    ichroma = np.maximum(np.maximum(ir, ig), ib) - np.minimum(np.minimum(ir, ig), ib)
    inner_hsv = np.asarray(Image.fromarray(inner.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    inner_sat = inner_hsv[:, :, 1] / 255.0
    inner_val = inner_hsv[:, :, 2] / 255.0
    inner_pink = (ir > ig + 24) & (ir > ib + 20)
    fill_n = float(((inner_sat >= 0.42) & inner_pink & (inner_val <= 0.82)).mean())
    light_n = float((inner_val >= 0.78).mean())
    if (
        0.28 <= fill_n <= 0.70
        and light_n >= 0.12
        and float(inner_pink.mean()) >= 0.90
        and float(ichroma.std()) >= 20.0
        and _edge_strength(core) <= 4.0
    ):
        stats = _scene_stats(photo_path, photo_id)
        if stats is not None:
            _sg, scene_gray, scene_color, _ssand, scene_cool = stats
            if scene_gray >= 0.75 and scene_cool < 0.08 and 0.15 <= scene_color <= 0.45:
                scene_mean = _scene_mean_rgb(photo_path, photo_id)
                if scene_mean is not None:
                    crop_mean = core.reshape(-1, 3).mean(0)
                    if float(np.linalg.norm(crop_mean - scene_mean)) >= 60:
                        return True
    # Printed mosaic on a museum plaque: pale photo-frame corners, tesserae
    # grain, grey floor. Live portraits against a white wall stay named.
    # Crop-only stays False.
    if 6.0 <= _edge_strength(core) <= 9.0:
        cy, cx = max(int(h * 0.14), 1), max(int(w * 0.14), 1)
        corners = np.concatenate(
            [
                arr[:cy, :cx].reshape(-1, 3),
                arr[:cy, -cx:].reshape(-1, 3),
                arr[-cy:, :cx].reshape(-1, 3),
                arr[-cy:, -cx:].reshape(-1, 3),
            ],
            0,
        )
        cr, cg, cb = corners[:, 0], corners[:, 1], corners[:, 2]
        cchroma = np.maximum(np.maximum(cr, cg), cb) - np.minimum(np.minimum(cr, cg), cb)
        cval = np.maximum(np.maximum(cr, cg), cb) / 255.0
        pale_n = float(((cchroma < 28) & (cval >= 0.55)).mean())
        if 0.68 <= pale_n <= 0.82 and float((cchroma < 20).mean()) >= 0.70:
            inner = arr[
                int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
                int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
            ]
            ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
            if float(((ir > ig + 24) & (ir > ib + 20)).mean()) >= 0.90:
                stats = _scene_stats(photo_path, photo_id)
                if stats is not None:
                    _sg, scene_gray, scene_color, _ssand, scene_cool = stats
                    if scene_gray >= 0.72 and scene_cool < 0.06 and 0.30 <= scene_color <= 0.45:
                        scene_mean = _scene_mean_rgb(photo_path, photo_id)
                        if scene_mean is not None:
                            crop_mean = core.reshape(-1, 3).mean(0)
                            if float(np.linalg.norm(crop_mean - scene_mean)) >= 75:
                                return True
    # Red porphyry / marble battle relief: the crop is the same dark red
    # stone as the wall. Tight face crops are darker; a glossy horse-and-rider
    # box is brighter but still magenta-red (G≈B), not orange skin. Crop-only
    # stays False. A person in front of it has a different-coloured face.
    if _edge_strength(core) >= 7.0:
        inner = arr[
            int(h * 0.32) : max(int(h * 0.68), int(h * 0.32) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ]
        ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
        inner_val = float(np.maximum(np.maximum(ir, ig), ib).mean() / 255.0)
        crop_mean = core.reshape(-1, 3).mean(0)
        # Magenta-red stone (B≈G). Beige sand and orange skin have G>>B.
        porphyry_hue = (
            float(crop_mean[0]) > float(crop_mean[1]) + 8
            and float(crop_mean[0]) > float(crop_mean[2]) + 8
            and float(crop_mean[2]) >= float(crop_mean[1]) - 6
            and abs(float(crop_mean[1]) - float(crop_mean[2])) <= 16
        )
        if 0.20 <= inner_val <= 0.70 and porphyry_hue:
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, scene_gray, scene_color, _ssand, scene_cool = stats
                if (
                    scene_color >= 0.70
                    and scene_gray < 0.22
                    and scene_gold < 0.08
                    and scene_cool < 0.12
                ):
                    scene_mean = _scene_mean_rgb(photo_path, photo_id)
                    if scene_mean is not None:
                        dist = float(np.linalg.norm(crop_mean - scene_mean))
                        # Glossy large boxes sit closer to the wall colour than
                        # a live face; allow more pink in the crop when so.
                        if dist <= (28 if float(pink.mean()) >= 0.55 else 42):
                            return True
    # Printed movie-poster / cartoon face on a street building: painted cream
    # paper, title lettering, ink outlines. A person under the sky has cool
    # surround, not poster paper. Crop-only stays False.
    if 3.2 <= _edge_strength(core) <= 6.5 and gold_n >= 0.52 and skin_n < 0.32 and _poster_lettering(arr):
        ring = np.ones((h, w), dtype=bool)
        ring[
            int(h * 0.28) : max(int(h * 0.72), int(h * 0.28) + 1),
            int(w * 0.28) : max(int(w * 0.72), int(w * 0.28) + 1),
        ] = False
        surr = arr[ring]
        sr, sg, sb = surr[:, 0], surr[:, 1], surr[:, 2]
        schroma = np.maximum(np.maximum(sr, sg), sb) - np.minimum(np.minimum(sr, sg), sb)
        shsv = np.asarray(Image.fromarray(arr.astype(np.uint8)).convert("HSV"), dtype=np.float32)
        shue = shsv[:, :, 0][ring] * (360.0 / 255.0)
        ssat = shsv[:, :, 1][ring] / 255.0
        sval = shsv[:, :, 2][ring] / 255.0
        ring_cool = float(((shue >= 80) & (shue <= 260) & (schroma >= 12) & (ssat >= 0.12)).mean())
        ring_pale = float(((schroma < 28) & (sval >= 0.62)).mean())
        if ring_cool <= 0.11 and 0.10 <= ring_pale <= 0.28:
            stats = _scene_stats(photo_path, photo_id)
            if stats is not None:
                scene_gold, scene_gray, scene_color, _ssand, scene_cool = stats
                if (
                    scene_cool >= 0.20
                    and scene_gold < 0.05
                    and scene_gray >= 0.45
                    and scene_color >= 0.40
                ):
                    return True
    return False


def mark_statue_if_needed(conn, face_id: int, photo_path: Path | str | None = None) -> bool:
    crop = CROP_DIR / f"{face_id}.jpg"
    photo_id = None
    if photo_path is None:
        row = conn.execute(
            """
            SELECT ph.path, ph.id AS photo_id FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.id = ?
            """,
            (face_id,),
        ).fetchone()
        photo_path = row["path"] if row else None
        photo_id = int(row["photo_id"]) if row and row["photo_id"] else None
    if not crop.exists() or not looks_like_statue(crop, photo_path, photo_id=photo_id):
        return False
    row = conn.execute("SELECT person_id, assigned_how FROM faces WHERE id = ?", (int(face_id),)).fetchone()
    how = str((row["assigned_how"] if row else "") or "")
    if how in {"manual", "cluster", "unknown_name", "merge"}:
        return False
    conn.execute(
        """
        UPDATE faces
        SET quality = 'unidentifiable', assigned_how = 'junk', person_id = NULL, cluster_id = NULL
        WHERE id = ? AND (person_id IS NULL OR assigned_how = 'auto')
        """,
        (face_id,),
    )
    return True


def restore_people_marked_junk() -> int:
    """Undo statue-suppression that hid real faces (people behind glass, etc.)."""
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.id, ph.path, ph.id AS photo_id
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.assigned_how = 'junk' AND f.person_id IS NULL
            """
        ).fetchall()
        n = 0
        for row in rows:
            crop = CROP_DIR / f"{row['id']}.jpg"
            if not crop.exists() or looks_like_statue(
                crop, row["path"], photo_id=int(row["photo_id"]) if row["photo_id"] else None
            ):
                continue
            conn.execute(
                "UPDATE faces SET quality = 'ok', assigned_how = NULL WHERE id = ?",
                (row["id"],),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def sweep_statues() -> int:
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.cluster_id, f.assigned_how, ph.path, ph.id AS photo_id
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE IFNULL(f.assigned_how, '') != 'junk'
              AND (f.person_id IS NULL OR f.assigned_how = 'auto')
            """
        ).fetchall()
        statue: dict[int, bool] = {}
        by_cluster: dict[int, list] = {}
        to_junk: set[int] = set()
        for row in rows:
            crop = CROP_DIR / f"{row['id']}.jpg"
            statue[row["id"]] = looks_like_statue(
                crop, row["path"], photo_id=int(row["photo_id"]) if row["photo_id"] else None
            )
            if row["cluster_id"] is None:
                if statue[row["id"]]:
                    to_junk.add(row["id"])
                continue
            by_cluster.setdefault(row["cluster_id"], []).append(row["id"])
        for members in by_cluster.values():
            to_junk.update(fid for fid in members if statue.get(fid))
        n = 0
        for face_id in to_junk:
            cur = conn.execute(
                """
                UPDATE faces
                SET quality = 'unidentifiable', assigned_how = 'junk', person_id = NULL, cluster_id = NULL
                WHERE id = ? AND (person_id IS NULL OR assigned_how = 'auto')
                """,
                (face_id,),
            )
            n += int(cur.rowcount)
        conn.commit()
        from .match import sweep_named_statues

        n += sweep_named_statues(conn)
        return n
    finally:
        conn.close()


def _sex_label(gender) -> str | None:
    if gender is None:
        return None
    try:
        return "M" if int(gender) == 1 else "F"
    except (TypeError, ValueError):
        return None


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def same_face_box(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """True when two detector boxes are the same person, not two people sitting close."""
    if box_iou(a, b) >= 0.45:
        return True
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    in_a = a[0] <= cb[0] <= a[2] and a[1] <= cb[1] <= a[3]
    in_b = b[0] <= ca[0] <= b[2] and b[1] <= ca[1] <= b[3]
    return in_a or in_b


def _face_keep_score(row) -> tuple:
    how = str(row["assigned_how"] or "")
    named = 1 if row["person_id"] is not None and how != "junk" else 0
    manual = 1 if how == "manual" else 0
    return (named, manual, float(row["det_score"] or 0), int(row["id"] or 0))


def merge_overlapping_faces(conn, photo_id: int) -> int:
    """Drop duplicate detector boxes on one photo. Keeps the named / stronger box."""
    rows = conn.execute(
        """
        SELECT id, x1, y1, x2, y2, det_score, person_id, assigned_how
        FROM faces
        WHERE photo_id = ?
          AND IFNULL(assigned_how, '') != 'junk'
        ORDER BY id
        """,
        (int(photo_id),),
    ).fetchall()
    drop: set[int] = set()
    kept = []
    for row in rows:
        box = (float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"]))
        hit = None
        for other in kept:
            obox = (float(other["x1"]), float(other["y1"]), float(other["x2"]), float(other["y2"]))
            if same_face_box(box, obox):
                hit = other
                break
        if hit is None:
            kept.append(row)
            continue
        if _face_keep_score(row) > _face_keep_score(hit):
            drop.add(int(hit["id"]))
            kept = [row if int(item["id"]) == int(hit["id"]) else item for item in kept]
        else:
            drop.add(int(row["id"]))
    for face_id in drop:
        conn.execute("DELETE FROM faces WHERE id = ?", (int(face_id),))
    if drop:
        conn.commit()
    return len(drop)


def scan_photo(conn, photo_row, analyzer) -> int:
    path = Path(photo_row["path"])
    thumb = THUMB_DIR / f"{int(photo_row['id'])}.jpg"
    try:
        image, sx, sy, rot, orig_w, orig_h = _load_detect(photo_row)
    except Exception:
        # Original unmounted and no local preview: leave pending so Resume can retry.
        if not path.is_file() and not thumb.is_file():
            return 0
        conn.execute(
            "UPDATE photos SET scanned_at = ? WHERE id = ?",
            (now_iso(), photo_row["id"]),
        )
        conn.commit()
        return 0
    detected = analyzer.get(image)
    existing = [
        (float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]))
        for r in conn.execute(
            "SELECT x1, y1, x2, y2 FROM faces WHERE photo_id = ?",
            (photo_row["id"],),
        ).fetchall()
    ]
    count = 0
    for face in detected:
        raw = tuple(float(v) for v in face.bbox)
        box = box_from_rotated(
            (raw[0] * sx, raw[1] * sy, raw[2] * sx, raw[3] * sy),
            rot,
            orig_w,
            orig_h,
        )
        if any(same_face_box(box, old) for old in existing):
            continue
        det_score = float(getattr(face, "det_score", 0.0))
        quality = _quality(det_score, box)
        emb = getattr(face, "normed_embedding", None)
        blob = embedding_to_bytes(emb) if emb is not None else None
        age = float(face.age) if getattr(face, "age", None) is not None else None
        sex = _sex_label(getattr(face, "gender", None))
        cur = conn.execute(
            """
            INSERT INTO faces (
                photo_id, x1, y1, x2, y2, det_score, quality, embedding,
                age_est, sex_est, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                photo_row["id"],
                box[0],
                box[1],
                box[2],
                box[3],
                det_score,
                quality,
                blob,
                age,
                sex,
                now_iso(),
            ),
        )
        face_id = int(cur.lastrowid)
        try:
            save_crop(
                path,
                photo_row["width"],
                photo_row["height"],
                box,
                face_id,
                photo_id=int(photo_row["id"]),
                rotation=rot,
            )
            mark_statue_if_needed(conn, face_id)
        except Exception:
            pass
        existing.append(box)
        count += 1

    merge_overlapping_faces(conn, int(photo_row["id"]))
    conn.execute(
        "UPDATE photos SET scanned_at = ? WHERE id = ?",
        (now_iso(), photo_row["id"]),
    )
    conn.commit()
    return count


def scan_pending(job_id: int, limit: int | None = None, since: str | None = None) -> dict:
    conn = connect()
    init_db(conn)
    photos = []
    photo_ids: list[int] = []
    faces_found = 0
    scan_err: Exception | None = None
    try:
        sql = "SELECT * FROM photos WHERE scanned_at IS NULL AND IFNULL(hidden, 0) = 0"
        params: list = []
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        photos = conn.execute(sql, tuple(params)).fetchall()
        if not photos:
            update_job(
                job_id,
                progress=0,
                total=1,
                message="No photos waiting to scan",
            )
            return {"photos": 0, "faces": 0}
        update_job(job_id, message="Loading buffalo_l face model (first run downloads ~300MB)")
        analyzer = get_analyzer()
        update_job(
            job_id,
            progress=0,
            total=max(len(photos), 1),
            message=f"Scanning 0 of {len(photos)} photos · finding faces",
        )
        last_report = 0.0
        for i, photo in enumerate(photos, start=1):
            if pause_requested():
                update_job(
                    job_id,
                    progress=i - 1,
                    total=max(len(photos), 1),
                    message=f"Scanning {i - 1} of {len(photos)} photos · finding faces",
                )
                raise JobPaused()
            if is_preview_path(photo["path"]):
                conn.execute(
                    "UPDATE photos SET scanned_at = ? WHERE id = ?",
                    (now_iso(), photo["id"]),
                )
                conn.commit()
                continue
            found = scan_photo(conn, photo, analyzer)
            faces_found += found
            if i % 2000 == 0:
                from . import cluster as cluster_mod

                cluster_mod.try_run_clustering(only_unclustered=True)
            now = time.monotonic()
            if now - last_report >= 0.4 or i == len(photos):
                last_report = now
                update_job(
                    job_id,
                    progress=i,
                    total=max(len(photos), 1),
                    message=f"Scanning {i} of {len(photos)} photos · finding faces · {Path(photo['path']).name}",
                )
        update_job(
            job_id,
            progress=len(photos),
            total=max(len(photos), 1),
            message=f"Scanning {len(photos)} of {len(photos)} photos · found {faces_found} faces",
        )
        photo_ids = [int(p["id"]) for p in photos]
    except Exception as exc:  # noqa: BLE001 — re-raised after the scan connection is closed
        scan_err = exc
    finally:
        conn.close()
    if scan_err:
        raise scan_err

    from . import cluster as cluster_mod
    from . import match as match_mod
    from . import sidecar as sidecar_mod

    swept = sweep_statues()
    if job_id and swept:
        update_job(job_id, message=f"Ignored {swept} statue-like faces")
    applied = sidecar_mod.apply_to_photos(photo_ids)
    if job_id and (applied["assigned"] or applied["junked"]):
        update_job(
            job_id,
            message=f"Restored {applied['assigned']} names from folder files",
        )
    cluster_mod.run_clustering(job_id, sweep=False)
    match_mod.match_unknown(job_id)
    if match_mod.suppress_like_junk():
        cluster_mod.run_clustering(job_id, sweep=False)
    return {"photos": len(photos), "faces": faces_found}


MANUAL_DET_THRESH = 0.15
MANUAL_OVERLAP = 0.45
MANUAL_MIN_BOX = 12


def add_manual_face(photo_id: int, x1: float, y1: float, x2: float, y2: float) -> dict:
    """Add a missed face from a user-drawn box. Detector refines the box when it can."""
    conn = connect()
    init_db(conn)
    try:
        photo = conn.execute("SELECT * FROM photos WHERE id = ?", (int(photo_id),)).fetchone()
        if not photo:
            raise KeyError("Photo not found")
        pw = float(photo["width"] or 0)
        ph = float(photo["height"] or 0)
        if pw < 1 or ph < 1:
            raise ValueError("This photo has no size recorded.")
        box = _clamp_box(x1, y1, x2, y2, pw, ph)
        if min(box[2] - box[0], box[3] - box[1]) < MANUAL_MIN_BOX:
            raise ValueError("Draw a larger box around the face.")
        existing = conn.execute(
            "SELECT id, x1, y1, x2, y2, assigned_how FROM faces WHERE photo_id = ?",
            (photo["id"],),
        ).fetchall()
        hit = _existing_in_box(box, existing)
        if hit:
            return _reuse_face(conn, hit)
        try:
            image, sx, sy, rot, orig_w, orig_h = _load_detect(photo)
        except Exception as exc:
            raise ValueError("Could not open this photo. Mount the album if it is on a NAS.") from exc
        found = _detect_in_user_box(image, sx, sy, box_to_rotated(box, rot, orig_w, orig_h))
        if found:
            det_box, det_score, blob, age, sex = found
            det_box = box_from_rotated(det_box, rot, orig_w, orig_h)
            hit = _existing_in_box(det_box, existing)
            if hit:
                return _reuse_face(conn, hit)
            quality = _quality(det_score, det_box)
            user_min = min(box[2] - box[0], box[3] - box[1])
            det_min = min(det_box[2] - det_box[0], det_box[3] - det_box[1])
            # Add a face is the user pointing at someone. A weak/tiny detector
            # hit (sleeping baby, profile) must not replace their box.
            if quality != "ok" or det_min < max(MIN_FACE_PX, user_min * 0.4):
                det_box, det_score, blob, age, sex = box, 0.99, None, None, None
                quality = "ok" if user_min >= MIN_FACE_PX else "unidentifiable"
        else:
            det_box, det_score, blob, age, sex = box, 0.99, None, None, None
            quality = "ok" if min(det_box[2] - det_box[0], det_box[3] - det_box[1]) >= MIN_FACE_PX else "unidentifiable"
        cur = conn.execute(
            """
            INSERT INTO faces (
                photo_id, x1, y1, x2, y2, det_score, quality, embedding,
                age_est, sex_est, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                photo["id"],
                det_box[0],
                det_box[1],
                det_box[2],
                det_box[3],
                det_score,
                quality,
                blob,
                age,
                sex,
                now_iso(),
            ),
        )
        face_id = int(cur.lastrowid)
        try:
            save_crop(
                Path(photo["path"]),
                int(pw),
                int(ph),
                det_box,
                face_id,
                photo_id=int(photo["id"]),
                rotation=rot,
            )
        except Exception:
            pass
        conn.commit()
        return {"face_id": face_id, "existing": False}
    finally:
        conn.close()


def _reuse_face(conn, face_id: int) -> dict:
    row = conn.execute(
        "SELECT assigned_how FROM faces WHERE id = ?",
        (int(face_id),),
    ).fetchone()
    restored = False
    if row and (row["assigned_how"] or "") == "junk":
        conn.execute(
            "UPDATE faces SET quality = 'ok', assigned_how = NULL WHERE id = ?",
            (int(face_id),),
        )
        conn.commit()
        restored = True
    return {"face_id": int(face_id), "existing": True, "restored": restored}


def _clamp_box(x1: float, y1: float, x2: float, y2: float, pw: float, ph: float) -> tuple[float, float, float, float]:
    left, right = sorted((float(x1), float(x2)))
    top, bottom = sorted((float(y1), float(y2)))
    left = min(pw, max(0.0, left))
    right = min(pw, max(0.0, right))
    top = min(ph, max(0.0, top))
    bottom = min(ph, max(0.0, bottom))
    if right <= left:
        right = min(pw, left + 1)
    if bottom <= top:
        bottom = min(ph, top + 1)
    return left, top, right, bottom


def _existing_in_box(box: tuple[float, float, float, float], rows) -> int | None:
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    best_id = None
    best_iou = 0.0
    for row in rows:
        old = (float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"]))
        iou = box_iou(box, old)
        inside = old[0] <= cx <= old[2] and old[1] <= cy <= old[3]
        if iou >= MANUAL_OVERLAP or (inside and iou >= 0.2):
            if iou >= best_iou:
                best_iou = iou
                best_id = int(row["id"])
    return best_id


def _detect_in_user_box(
    image: np.ndarray,
    sx: float,
    sy: float,
    box: tuple[float, float, float, float],
):
    try:
        analyzer = get_analyzer()
    except Exception:
        # User already drew the box. Keep that crop even if buffalo_l is still loading.
        return None
    h, w = image.shape[:2]
    scale_x = sx if sx else 1.0
    scale_y = sy if sy else 1.0
    ix1 = int(max(0, box[0] / scale_x))
    iy1 = int(max(0, box[1] / scale_y))
    ix2 = int(min(w, box[2] / scale_x))
    iy2 = int(min(h, box[3] / scale_y))
    bw = max(1, ix2 - ix1)
    bh = max(1, iy2 - iy1)
    pad_x = int(bw * 0.28)
    pad_y = int(bh * 0.28)
    cx1 = max(0, ix1 - pad_x)
    cy1 = max(0, iy1 - pad_y)
    cx2 = min(w, ix2 + pad_x)
    cy2 = min(h, iy2 + pad_y)
    if cx2 - cx1 < 8 or cy2 - cy1 < 8:
        return None
    crop = image[cy1:cy2, cx1:cx2]
    det = analyzer.models.get("detection") if getattr(analyzer, "models", None) else None
    old_app = getattr(analyzer, "det_thresh", None)
    old_det = getattr(det, "det_thresh", None) if det is not None else None
    try:
        analyzer.det_thresh = MANUAL_DET_THRESH
        if det is not None:
            det.det_thresh = MANUAL_DET_THRESH
        faces = analyzer.get(crop)
    finally:
        if old_app is not None:
            analyzer.det_thresh = old_app
        if det is not None and old_det is not None:
            det.det_thresh = old_det
    if not faces:
        return None
    ranked = []
    for face in faces:
        raw = tuple(float(v) for v in face.bbox)
        fx1 = raw[0] + cx1
        fy1 = raw[1] + cy1
        fx2 = raw[2] + cx1
        fy2 = raw[3] + cy1
        orig = (fx1 * scale_x, fy1 * scale_y, fx2 * scale_x, fy2 * scale_y)
        mid_x = (raw[0] + raw[2]) / 2 + cx1
        mid_y = (raw[1] + raw[3]) / 2 + cy1
        inside = ix1 <= mid_x <= ix2 and iy1 <= mid_y <= iy2
        score = float(getattr(face, "det_score", 0.0))
        ranked.append((1 if inside else 0, score, orig, face))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, score, orig, face = ranked[0]
    emb = getattr(face, "normed_embedding", None)
    blob = embedding_to_bytes(emb) if emb is not None else None
    age = float(face.age) if getattr(face, "age", None) is not None else None
    sex = _sex_label(getattr(face, "gender", None))
    return orig, score, blob, age, sex
