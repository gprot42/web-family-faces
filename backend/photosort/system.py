"""Which desktop this runs on. macOS is the default; Windows and Linux take the other branches.

Everything platform-specific in the backend goes through here: where network
shares live, how they are connected, what the local disk is called in the UI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC


def local_label() -> str:
    if IS_WINDOWS:
        return "This PC"
    if IS_MAC:
        return "This Mac"
    return "This computer"


def mount_hint() -> str:
    """How to bring a network share online, in the words of the platform."""
    if IS_WINDOWS:
        return "Connect it in File Explorer (or map the network drive) first"
    if IS_MAC:
        return "Mount it in Finder first"
    return "Mount the share first"


def unc_parts(path: str | os.PathLike | None) -> tuple[str, str] | None:
    r"""(server, share) for a UNC path like \\nas\photos\2019, else None."""
    text = str(path or "").replace("/", "\\")
    if not text.startswith("\\\\"):
        return None
    parts = [part for part in text.strip("\\").split("\\") if part]
    if len(parts) < 2 or parts[0] in {"?", "."}:
        return None
    return parts[0], parts[1]


def unc_root(path: str | os.PathLike | None) -> str | None:
    parts = unc_parts(path)
    return f"\\\\{parts[0]}\\{parts[1]}" if parts else None


def drive_roots() -> list[dict]:
    """Windows drive letters with their kind: local, removable, or network."""
    if not IS_WINDOWS:
        return []
    lister = getattr(os, "listdrives", None)
    drives = list(lister()) if lister else [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
    out: list[dict] = []
    for drive in drives:
        out.append({"path": drive, "kind": drive_kind(drive)})
    return out


def drive_kind(drive: str) -> str:
    """local, removable, or network, from the Windows drive type."""
    try:
        import ctypes

        kind = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))  # type: ignore[attr-defined]
    except Exception:
        return "local"
    return {2: "removable", 4: "network"}.get(int(kind), "local")
