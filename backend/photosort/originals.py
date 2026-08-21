"""Heirloom protection: originals stay where they are.

Photos are read-only. Nothing in Family Faces may move, rename, copy, delete,
chmod, or rewrite an original — including EXIF. The one allowed album write
is a portable `.photosort.json` sidecar next to the photos (names only).
Thumbs, crops, models, and the live catalog live under DATA_DIR.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import IFD

from .config import DATA_DIR, RAW_EXTS
from .util import file_sha256

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

SIDECAR_NAME = ".photosort.json"

# Contract exposed to the UI and API. Originals stay untouched.
ORIGINALS_POLICY = {
    "move": False,
    "rename": False,
    "copy_out": False,
    "rewrite": False,
    "write_exif": False,
    "write_sidecars_in_album": True,
    "delete": False,
}

SKIP_DIR_NAMES = {
    "@eadir",
    "#recycle",
    "@recycle",
    ".trash",
    ".trashes",
    ".spotlight-v100",
    ".fseventsd",
    ".temporaryitems",
    ".documentrevisions-v100",
    "thumbs.db",
    "sample-album",
    "1024 x 768",
    "1024x768",
    "640 x 480",
    "800 x 600",
    "thumbs",
    "thumbnails",
}

# Folders that are themselves a resized album, e.g. "1994 - Trip 1024 x 768".
_PREVIEW_NAME_MARKERS = ("1024x768", "640x480", "800x600")


def is_preview_dir_name(name: str) -> bool:
    """True for Synology preview dirs and albums named after a preview size."""
    n = (name or "").lower().strip()
    if n in SKIP_DIR_NAMES:
        return True
    compact = n.replace(" ", "").replace("_", "").replace("-", "")
    return any(marker in compact for marker in _PREVIEW_NAME_MARKERS)


def is_preview_path(path: str | Path) -> bool:
    """Resized copies (e.g. 1024 x 768) — hide so the same shot is not shown twice."""
    return any(is_preview_dir_name(part) for part in Path(path).parts)


def preview_path_sql(column: str = "ph.path") -> str:
    """SQL that skips Synology preview-size copies. Column name is a trusted identifier."""
    patterns = (
        "%1024 x 768%",
        "%1024x768%",
        "%640 x 480%",
        "%640x480%",
        "%800 x 600%",
        "%800x600%",
    )
    return " AND ".join(f"{column} NOT LIKE '{p}'" for p in patterns)


def drop_preview_rows(rows: list, path_key: str = "path") -> list:
    """Keep the full-size shot when a 1024 copy of the same file was also indexed."""
    return [row for row in rows if not is_preview_path(row[path_key])]


class OriginalWriteError(PermissionError):
    """Raised if anything tries to write a photo or an unapproved album file."""


def is_sidecar_path(path: Path) -> bool:
    return Path(path).name == SIDECAR_NAME


def assert_sidecar_write(dest: Path) -> Path:
    """Allow creating, replacing, or removing only `.photosort.json` in an album."""
    dest = Path(dest)
    if dest.name != SIDECAR_NAME:
        raise OriginalWriteError(f"Only {SIDECAR_NAME} may be written in an album: {dest}")
    parent = dest.parent
    if parent.resolve() == Path(parent.anchor or "/").resolve():
        raise OriginalWriteError(f"Refusing sidecar at filesystem root: {dest}")
    try:
        data_root = DATA_DIR.resolve()
        if dest.resolve().is_relative_to(data_root) or parent.resolve() == data_root:
            raise OriginalWriteError(f"Sidecar does not belong in app data: {dest}")
    except (OSError, ValueError):
        pass
    return dest


def skip_dir(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith(".") or name in SKIP_DIR_NAMES or is_preview_dir_name(path.name):
        return True
    text = str(path)
    data = str(DATA_DIR)
    return text == data or text.startswith(data + os.sep)


def current_library() -> Path | None:
    from .db import connect, init_db

    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT folder FROM library WHERE id = 1").fetchone()
        if not row or not row["folder"]:
            return None
        return Path(row["folder"])
    finally:
        conn.close()


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def assert_not_library(dest: Path, library: Path | None = None) -> None:
    lib = library or current_library()
    if not lib:
        return
    if _under(dest, lib):
        raise OriginalWriteError(
            f"Refusing to write, move, or create files inside the photo library: {dest}"
        )


def assert_data_write(dest: Path) -> Path:
    dest = dest.resolve()
    if not dest.is_relative_to(DATA_DIR.resolve()):
        raise OriginalWriteError(f"Refusing to write outside app data: {dest}")
    assert_not_library(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def save_image(img: Image.Image, dest: Path, **kwargs) -> Path:
    dest = assert_data_write(dest)
    img.save(dest, **kwargs)
    return dest


def _gps_decimal(values, ref: str | None) -> float | None:
    try:
        deg, minutes, seconds = (float(v) for v in values[:3])
    except (TypeError, ValueError):
        return None
    dec = deg + minutes / 60 + seconds / 3600
    if (ref or "").upper() in {"S", "W"}:
        dec = -dec
    return round(dec, 5)


def _iso_from_stat(ts: float) -> str | None:
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    # Some NAS / copied files have birth times that overflow time_t.
    if value < -2_208_988_800 or value > 4_102_444_800:  # 1900..2100 UTC
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def read_photo_clues(path: str | Path) -> dict[str, Any]:
    """Read-only filename, file dates, and EXIF clues. Never writes the original."""
    src = Path(path)
    out: dict[str, Any] = {
        "filename": src.name,
        "folder": src.parent.name if src.parent.name else None,
        "file_created": None,
        "file_modified": None,
        "exif_taken_at": None,
        "camera": None,
        "gps": None,
    }
    try:
        st = src.stat()
    except OSError:
        return out
    out["file_modified"] = _iso_from_stat(st.st_mtime)
    birth = getattr(st, "st_birthtime", None)
    if birth:
        out["file_created"] = _iso_from_stat(float(birth))
    try:
        img = open_image(src)
    except (UnidentifiedImageError, OSError):
        return out
    try:
        exif = img.getexif()
        if not exif:
            return out
        make = str(exif.get(271) or "").strip()
        model = str(exif.get(272) or "").strip()
        camera = " ".join(part for part in (make, model) if part)
        if camera:
            out["camera"] = camera
        taken = exif.get(306)
        try:
            extra = exif.get_ifd(IFD.Exif)
        except Exception:
            extra = {}
        if extra:
            taken = extra.get(36867) or extra.get(36868) or taken
        if taken:
            text = str(taken).strip()
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
                try:
                    out["exif_taken_at"] = datetime.strptime(text[:19], fmt).isoformat()
                    break
                except ValueError:
                    continue
            if not out["exif_taken_at"] and text:
                out["exif_taken_at"] = text
        try:
            gps = exif.get_ifd(IFD.GPSInfo)
        except Exception:
            gps = {}
        if gps:
            lat = _gps_decimal(gps.get(2), gps.get(1))
            lon = _gps_decimal(gps.get(4), gps.get(3))
            if lat is not None and lon is not None:
                out["gps"] = f"{lat}, {lon}"
        return out
    finally:
        img.close()


def open_original(path: Path):
    """Open a photo for reading only. The OS file descriptor cannot write."""
    fd = os.open(path, os.O_RDONLY)
    return os.fdopen(fd, "rb")


def _open_raw(path: Path) -> Image.Image:
    try:
        import rawpy
    except ImportError as exc:
        raise UnidentifiedImageError(f"Cannot decode RAW (rawpy missing): {path.name}") from exc
    handle = open_original(path)
    try:
        try:
            raw = rawpy.imread(handle)
        except Exception as exc:
            raise UnidentifiedImageError(f"Not a readable RAW file: {path.name}") from exc
        try:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=8,
                half_size=True,
            )
            return Image.fromarray(rgb)
        except Exception as exc:
            raise UnidentifiedImageError(f"Could not develop RAW: {path.name}") from exc
        finally:
            raw.close()
    finally:
        handle.close()


def open_image(path: Path) -> Image.Image:
    path = Path(path)
    suffix = path.suffix.lower()
    raw_err: Exception | None = None
    if suffix in RAW_EXTS:
        try:
            return _open_raw(path)
        except UnidentifiedImageError as exc:
            raw_err = exc
    handle = open_original(path)
    try:
        img = Image.open(handle)
        img.load()
        return img
    except Exception:
        handle.close()
        if raw_err:
            raise raw_err
        raise
    finally:
        handle.close()


def open_preview(path: Path, max_side: int = 1280) -> tuple[Image.Image, tuple[int, int]]:
    """Smaller decode for catalog thumbs. Returns (image, original_size). Originals stay unread for write."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in RAW_EXTS:
        try:
            import rawpy
        except ImportError:
            img = open_image(path)
            return img, img.size
        handle = open_original(path)
        try:
            try:
                raw = rawpy.imread(handle)
            except Exception as exc:
                raise UnidentifiedImageError(f"Not a readable RAW file: {path.name}") from exc
            try:
                sizes = getattr(raw, "sizes", None)
                orig = (
                    int(getattr(sizes, "iwidth", 0) or 0),
                    int(getattr(sizes, "iheight", 0) or 0),
                )
                try:
                    thumb = raw.extract_thumb()
                except Exception:
                    thumb = None
                if thumb is not None and getattr(thumb, "data", None):
                    data = thumb.data
                    fmt = str(getattr(thumb, "format", "") or "")
                    if "JPEG" in fmt.upper():
                        img = Image.open(BytesIO(data))
                        img.load()
                        if orig[0] <= 0:
                            orig = img.size
                        return img, orig
                    if hasattr(data, "shape"):
                        img = Image.fromarray(data)
                        if orig[0] <= 0:
                            orig = img.size
                        return img, orig
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=True,
                )
                img = Image.fromarray(rgb)
                if orig[0] <= 0:
                    orig = img.size
                return img, orig
            finally:
                raw.close()
        finally:
            handle.close()
    handle = open_original(path)
    try:
        img = Image.open(handle)
        orig = img.size
        if max(orig) > max_side:
            try:
                img.draft("RGB", (max_side, max_side))
            except Exception:
                pass
        img.load()
        return img.copy(), orig
    except Exception:
        handle.close()
        img = open_image(path)
        return img, img.size
    finally:
        handle.close()


def library_must_stay_outside_data(folder: Path) -> None:
    folder = folder.resolve()
    if DATA_DIR == folder or DATA_DIR.is_relative_to(folder):
        raise OriginalWriteError(
            "App data would sit inside this album. Point PHOTOSORT_DATA at another disk, not the photo folder."
        )


def verify_file(path: Path, expected_sha: str) -> str:
    if not path.exists():
        return "missing"
    try:
        digest = file_sha256(path)
    except OSError:
        return "unreadable"
    if digest != expected_sha:
        return "changed"
    return "ok"
