"""List folders the backend can see — including mounted NAS shares."""

from __future__ import annotations

import os
from pathlib import Path

from . import system
from .config import DATA_DIR, IMAGE_EXTS
from .originals import SKIP_DIR_NAMES

LOCAL_VOLUME_NAMES = {"macintosh hd", "macintosh hd - data", "recovery", "preboot", "vm"}
VOLUMES_TOKEN = "volumes"


def _volumes_dir() -> Path | None:
    """Where network shares appear as folders. Windows has no such folder: it
    uses UNC paths and drive letters, so the "volumes" listing is built instead."""
    if system.IS_WINDOWS:
        return None
    if system.IS_MAC:
        return Path("/Volumes")
    return Path("/mnt")


def _is_volumes_request(path: str | None) -> bool:
    if not path:
        return False
    raw = str(path).strip().rstrip("/")
    if raw.lower() in {VOLUMES_TOKEN, "/volumes"}:
        return True
    # A leftover relative "volumes" next to the app is not a real album.
    name = Path(raw).name.lower()
    if name != VOLUMES_TOKEN:
        return False
    try:
        return not Path(os.path.abspath(raw)).is_dir()
    except OSError:
        return True


def _public_path(folder: Path) -> str | None:
    try:
        volumes = _volumes_dir()
        if volumes is not None and folder == volumes:
            return VOLUMES_TOKEN
    except OSError:
        pass
    return str(folder)


def _volume_root_of(path: str | None) -> str | None:
    r"""The share root an album path lives on: /Volumes/name or \server\share."""
    unc = system.unc_root(path)
    if unc:
        return unc
    parts = Path(str(path or "")).parts
    if len(parts) >= 3 and parts[1].lower() == "volumes":
        return str(Path("/Volumes") / parts[2])
    return None


def remembered_volume_roots() -> list[str]:
    """NAS / volume roots the catalog has already seen, even if unmounted."""
    try:
        from .db import connect
    except Exception:
        return []
    try:
        conn = connect()
    except Exception:
        return []
    try:
        found: set[str] = set()
        try:
            row = conn.execute("SELECT folder FROM library WHERE id = 1").fetchone()
            if row and row[0]:
                root = _volume_root_of(row[0])
                if root:
                    found.add(root)
        except Exception:
            pass
        try:
            for (raw,) in conn.execute("SELECT DISTINCT path FROM photos"):
                root = _volume_root_of(raw)
                if root:
                    found.add(root)
        except Exception:
            pass
        return sorted(found)
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _public_parent(folder: Path) -> str | None:
    try:
        volumes = _volumes_dir()
        if (volumes is not None and folder == volumes) or folder.parent == folder:
            return None
        if volumes is not None and folder.parent == volumes:
            return VOLUMES_TOKEN
        if system.IS_WINDOWS and system.unc_root(folder) == str(folder):
            return VOLUMES_TOKEN
    except OSError:
        pass
    return str(folder.parent)


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _windows_network_entries() -> list[dict]:
    """Network drives and the UNC share roots the catalog has used."""
    entries: list[dict] = []
    seen: set[str] = set()
    for drive in system.drive_roots():
        if drive["kind"] != "network":
            continue
        seen.add(drive["path"].lower())
        entries.append(
            {"name": drive["path"], "path": drive["path"], "kind": "nas-volume", "hint": "Network drive"}
        )
    for root in remembered_volume_roots():
        if root.lower() in seen or not system.unc_parts(root):
            continue
        seen.add(root.lower())
        mounted = _is_dir(Path(root))
        entries.append(
            {
                "name": root,
                "path": root,
                "kind": "nas-volume",
                "hint": "NAS share" if mounted else "Not connected",
                "mounted": mounted,
                "image_count": None,
                "error": None if mounted else f"Not connected. {system.mount_hint()}, then click Refresh.",
            }
        )
    return entries


def roots() -> list[dict]:
    items: list[dict] = []
    if system.IS_WINDOWS:
        for drive in system.drive_roots():
            kind = {"network": "nas-volume", "removable": "nas-volume"}.get(drive["kind"], "local-volume")
            hint = {"network": "Network drive", "removable": "Removable"}.get(drive["kind"], system.local_label())
            items.append({"name": drive["path"], "path": drive["path"], "kind": kind, "hint": hint})
        for entry in _windows_network_entries():
            if entry["kind"] == "nas-volume" and entry["name"] not in {i["name"] for i in items}:
                items.append({k: entry[k] for k in ("name", "path", "kind", "hint")})
        home = Path.home()
        items.append({"name": "Home", "path": str(home), "kind": "home", "hint": system.local_label()})
        return items
    volumes = _volumes_dir()
    if volumes is not None and volumes.is_dir():
        try:
            kids = sorted(volumes.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            kids = []
        for child in kids:
            if child.name.startswith("."):
                continue
            kind = "local-volume" if child.name.lower() in LOCAL_VOLUME_NAMES else "nas-volume"
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "kind": kind,
                    "hint": "NAS / external" if kind == "nas-volume" else system.local_label(),
                }
            )
    home = Path.home()
    items.append({"name": "Home", "path": str(home), "kind": "home", "hint": system.local_label()})
    mnt = Path("/mnt")
    if mnt.is_dir() and volumes != mnt:
        items.append({"name": "mnt", "path": str(mnt), "kind": "mount", "hint": "Linux mounts"})
    return items


def _abspath(path: Path) -> Path:
    # Avoid Path.resolve() on SMB — it can hang following dead aliases.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _skip_name(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(".") or lower in SKIP_DIR_NAMES


def _is_app_data(path: Path) -> bool:
    try:
        return str(path).startswith(str(DATA_DIR) + os.sep) or path == DATA_DIR
    except OSError:
        return False


def _catalog_photo_paths() -> list[str]:
    try:
        from .db import connect, init_db
        from .originals import is_preview_path
    except Exception:
        return []
    try:
        conn = connect()
        init_db(conn)
    except Exception:
        return []
    try:
        rows = conn.execute("SELECT path FROM photos WHERE IFNULL(hidden, 0) = 0").fetchall()
        return [str(row["path"]) for row in rows if not is_preview_path(row["path"])]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _catalog_children(folder: Path) -> tuple[list[dict], int]:
    """Immediate child albums from indexed photos, even if the share is unmounted."""
    prefix = str(folder).rstrip("/") + "/"
    kids: dict[str, dict] = {}
    image_count = 0
    for raw in _catalog_photo_paths():
        if not raw.startswith(prefix):
            continue
        rest = raw[len(prefix) :]
        if "/" not in rest:
            image_count += 1
            continue
        name = rest.split("/", 1)[0]
        if not name or _skip_name(name):
            continue
        child = prefix + name
        item = kids.get(name)
        if item is None:
            kids[name] = {
                "name": name,
                "path": child,
                "kind": "dir",
                "hint": "In catalog",
                "mounted": False,
                "from_catalog": True,
                "image_count": None,
                "error": None,
            }
    entries = sorted(kids.values(), key=lambda e: e["name"].lower())
    return entries, image_count


def _offline_listing(folder: Path, *, message: str) -> dict:
    entries, image_count = _catalog_children(folder)
    if not entries and not image_count:
        return {
            "path": _public_path(folder),
            "parent": _public_parent(folder),
            "entries": [],
            "image_count": 0,
            "from_catalog": False,
            "error": message,
        }
    return {
        "path": _public_path(folder),
        "parent": _public_parent(folder),
        "entries": entries,
        "image_count": image_count,
        "from_catalog": True,
        "error": "Share is not mounted. Showing albums already in the catalog.",
    }


def list_folder(path: str | None) -> dict:
    if not path:
        return {
            "path": None,
            "parent": None,
            "entries": [
                {**r, "image_count": None, "error": None} for r in roots()
            ],
            "image_count": 0,
            "error": None,
        }

    if _is_volumes_request(path) and system.IS_WINDOWS:
        return {
            "path": VOLUMES_TOKEN,
            "parent": None,
            "entries": sorted(_windows_network_entries(), key=lambda e: e["name"].lower()),
            "image_count": 0,
            "error": None,
        }
    folder = _volumes_dir() if _is_volumes_request(path) else _abspath(Path(path))
    if not _is_dir(folder):
        return _offline_listing(
            folder,
            message=f"Not a folder, or the NAS share is not connected. {system.mount_hint()}, then open NAS drives.",
        )

    entries: list[dict] = []
    image_count = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.name.startswith(".") or entry.name.startswith("._"):
                    continue
                suffix = Path(entry.name).suffix.lower()
                # Name-only skip for photos — is_dir() stats every file on SMB and freezes large albums.
                if suffix in IMAGE_EXTS:
                    image_count += 1
                    continue
                child = folder / entry.name
                if _skip_name(entry.name) or _is_app_data(child):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError as exc:
                    entries.append(
                        {
                            "name": entry.name,
                            "path": str(child),
                            "kind": "dir",
                            "image_count": None,
                            "error": str(exc),
                        }
                    )
                    continue
                if not is_dir:
                    continue
                kind = "dir"
                hint = None
                if folder == _volumes_dir():
                    kind = "local-volume" if entry.name.lower() in LOCAL_VOLUME_NAMES else "nas-volume"
                    hint = "NAS / external" if kind == "nas-volume" else "This Mac"
                    if kind == "local-volume":
                        continue
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child),
                        "kind": kind,
                        "hint": hint,
                        "mounted": True,
                        "image_count": None,
                        "error": None,
                    }
                )
    except OSError as exc:
        return _offline_listing(
            folder,
            message=f"Cannot read folder ({exc}). If this is a NAS: {system.mount_hint().lower()} and retry.",
        )

    if _volumes_dir() is not None and folder == _volumes_dir():
        present = {Path(item["path"]).name.lower() for item in entries}
        for root in remembered_volume_roots():
            name = Path(root).name
            if name.lower() in present or name.lower() in LOCAL_VOLUME_NAMES:
                continue
            if _is_dir(folder / name):
                continue
            entries.append(
                {
                    "name": name,
                    "path": root,
                    "kind": "nas-volume",
                    "hint": "Not mounted",
                    "mounted": False,
                    "image_count": None,
                    "error": f"Not mounted. {system.mount_hint()}, then click Refresh.",
                }
            )

    entries.sort(key=lambda e: e["name"].lower())
    return {
        "path": _public_path(folder),
        "parent": _public_parent(folder),
        "entries": entries,
        "image_count": image_count,
        "error": None,
    }
