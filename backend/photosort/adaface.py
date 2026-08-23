"""Second local recognizer. ArcFace (buffalo_l) stays primary; AdaFace retries misses."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .config import CROP_DIR, MODEL_DIR
from .util import bytes_to_embedding, embedding_to_bytes, l2_normalize

MODEL_NAME = "adaface_ir_18.onnx"
MODEL_URL = "https://github.com/yakhyo/adaface-onnx/releases/download/weights/adaface_ir_18.onnx"
MODEL_URL_FALLBACK = (
    "https://huggingface.co/yakhyo/uniface-weights/resolve/"
    "4c7ed723a20deb7ff154b1ba7d6e73747d954016/adaface_ir_18.onnx"
)
MODEL_SHA256 = "6b6a35772fb636cdd4fa86520c1a259d0c41472a76f70f802b351837a00d9870"
INPUT_SIZE = 112
# Face sits in the middle of the padded 384px crop (CROP_PAD 0.32).
FACE_FRAC = 0.62

_session = None
_session_error: str | None = None
_input_name: str | None = None


def status() -> dict:
    return {
        "ready": _session is not None,
        "error": _session_error,
        "model": "adaface ir18",
    }


def model_path() -> Path:
    return MODEL_DIR / "adaface" / MODEL_NAME


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _download(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    last_err: Exception | None = None
    for url in (MODEL_URL, MODEL_URL_FALLBACK):
        try:
            with urllib.request.urlopen(url, timeout=120) as src, tmp.open("wb") as out:
                while True:
                    chunk = src.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            if _sha256(tmp) != MODEL_SHA256:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("AdaFace model download did not match the expected file.")
            tmp.replace(dest)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            tmp.unlink(missing_ok=True)
    raise RuntimeError(str(last_err or "Could not download AdaFace."))


def get_session():
    global _session, _session_error, _input_name
    if _session is not None:
        return _session
    try:
        from .faces import _cap_onnx_threads

        _cap_onnx_threads()
        import onnxruntime as ort

        path = model_path()
        if not path.is_file() or _sha256(path) != MODEL_SHA256:
            _download(path)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        sess = ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        _input_name = sess.get_inputs()[0].name
        _session = sess
        _session_error = None
        return _session
    except Exception as exc:  # noqa: BLE001
        _session_error = str(exc)
        raise


def _bgr_face(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    side = min(h, w)
    box = int(round(side * FACE_FRAC))
    box = max(8, min(side, box))
    x1 = (w - box) // 2
    y1 = (h - box) // 2
    face = rgb[y1 : y1 + box, x1 : x1 + box]
    bgr = cv2.cvtColor(face, cv2.COLOR_RGB2BGR)
    return cv2.resize(bgr, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)


def embed_rgb(rgb: np.ndarray) -> np.ndarray | None:
    if rgb is None or rgb.size == 0:
        return None
    blob = _bgr_face(np.ascontiguousarray(rgb))
    blob = (blob.astype(np.float32) - 127.5) / 127.5
    blob = np.transpose(blob, (2, 0, 1))[None, ...]
    sess = get_session()
    out = sess.run(None, {_input_name: blob})[0]
    vec = np.asarray(out, dtype=np.float32).reshape(-1)
    if vec.size == 0:
        return None
    return l2_normalize(vec)


def embed_crop(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return embed_rgb(rgb)


def embedding_for_face(conn, face_id: int) -> np.ndarray | None:
    fid = int(face_id)
    row = conn.execute(
        """
        SELECT f.embedding_ada, ph.path
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        WHERE f.id = ?
        """,
        (fid,),
    ).fetchone()
    if row is None:
        return None
    if row["embedding_ada"]:
        return bytes_to_embedding(row["embedding_ada"])
    # Do not reuse leftover crops when the photo is not on disk (tests, unmounted NAS).
    if not Path(str(row["path"] or "")).is_file():
        return None
    vec = embed_crop(CROP_DIR / f"{fid}.jpg")
    if vec is None:
        return None
    conn.execute(
        "UPDATE faces SET embedding_ada = ? WHERE id = ?",
        (embedding_to_bytes(vec), fid),
    )
    return vec
