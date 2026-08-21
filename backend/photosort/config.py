from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "family-faces"

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PHOTOSORT_DATA", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "photosort.db"
THUMB_DIR = DATA_DIR / "thumbs"
CROP_DIR = DATA_DIR / "crops"
MODEL_DIR = DATA_DIR / "models"
BACKUP_DIR = DATA_DIR / "backups"
SHARPEN_DIR = DATA_DIR / "sharpen"
IMAGINE_DIR = DATA_DIR / "imagine"
GEDCOM_PATH = DATA_DIR / "family.ged"
GEDCOM_META_PATH = DATA_DIR / "family.ged.json"

# Still photos Pillow or rawpy can open. Videos, audio, PDF, and app files stay out.
PILLOW_EXTS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".jfi",
    ".png",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".dib",
    ".heic",
    ".heif",
    ".avif",
    ".jp2",
    ".j2k",
    ".jpx",
    ".ico",
    ".ppm",
    ".pgm",
    ".pbm",
    ".tga",
    ".psd",
}
RAW_EXTS = {
    ".3fr",
    ".arw",
    ".cr2",
    ".cr3",
    ".crw",
    ".dcr",
    ".dng",
    ".erf",
    ".kdc",
    ".mef",
    ".mos",
    ".mrw",
    ".nef",
    ".nrw",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".rw2",
    ".rwl",
    ".sr2",
    ".srf",
    ".srw",
    ".x3f",
}
IMAGE_EXTS = PILLOW_EXTS | RAW_EXTS

THUMB_MAX = 640
CROP_SIZE = 384
CROP_PAD = 0.32

# Conservative: split same person rather than glue cousins together.
CLUSTER_SIM = 0.52
# To-name shows this many faces. Naming a group never stamps more than this.
CLUSTER_PREVIEW_LIMIT = 24
MATCH_HIGH = 0.55
MATCH_MEDIUM = 0.42
# Auto-name only when the next person is clearly behind. Stops cousins tying.
MATCH_MARGIN = 0.10
# Re-identify (user asked) searches harder, but still above cousin-lookalike range.
MATCH_REMATCH_HIGH = 0.46
MATCH_REMATCH_MEDIUM = 0.34
MATCH_REMATCH_MARGIN = 0.06
# Adult-adult merge hint (never auto-applied).
MERGE_SIM = 0.36
CHILD_AGE = 13
TEEN_AGE = 21
ELDER_AGE = 55

MIN_FACE_PX = 40
MIN_DET_SCORE = 0.45

API_HOST = os.environ.get("PHOTOSORT_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PHOTOSORT_PORT", "8741"))
UI_PORT = int(os.environ.get("PHOTOSORT_UI_PORT", "5174"))

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
LOOKUP_MODEL = os.environ.get("PHOTOSORT_LOOKUP_MODEL", "grok-4.6")
LOOKUP_TIMEOUT = float(os.environ.get("PHOTOSORT_LOOKUP_TIMEOUT", "180"))
SHARPEN_MODEL = os.environ.get("PHOTOSORT_SHARPEN_MODEL", "grok-imagine-image-2.0")
SHARPEN_TIMEOUT = float(os.environ.get("PHOTOSORT_SHARPEN_TIMEOUT", "120"))
SHARPEN_MAX_SIDE = int(os.environ.get("PHOTOSORT_SHARPEN_MAX_SIDE", "2048"))
SHARPEN_RESOLUTION = os.environ.get("PHOTOSORT_SHARPEN_RESOLUTION", "2k")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    _load_env_file(ROOT / ".env")
    _load_env_file(DATA_DIR / ".env")


load_env()


def xai_api_key() -> str:
    from .settings import active_xai_key

    return active_xai_key()


def ensure_dirs() -> None:
    for path in (DATA_DIR, THUMB_DIR, CROP_DIR, MODEL_DIR, BACKUP_DIR, SHARPEN_DIR, IMAGINE_DIR):
        path.mkdir(parents=True, exist_ok=True)
