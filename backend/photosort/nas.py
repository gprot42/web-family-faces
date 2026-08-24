"""Mount Synology / SMB shares through Finder so Keychain can supply the login."""

from __future__ import annotations

import plistlib
import re
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .browse import LOCAL_VOLUME_NAMES, remembered_volume_roots

HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,250}[A-Za-z0-9])?$")
SHARE_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
MOUNT_TIMEOUT = 30


def _safe_host(host: str | None) -> str | None:
    raw = str(host or "").strip().rstrip(".")
    if raw.lower().startswith("smb://"):
        raw = urlparse(raw).hostname or ""
    elif "/" in raw or "@" in raw:
        return None
    raw = raw.strip().rstrip(".")
    if not raw or not HOST_RE.fullmatch(raw):
        return None
    return raw


def _safe_share(name: str | None) -> str | None:
    raw = str(name or "").strip().strip("/")
    if not raw or raw.lower() in LOCAL_VOLUME_NAMES:
        return None
    if not SHARE_RE.fullmatch(raw):
        return None
    return raw


def host_aliases(host: str | None) -> set[str]:
    clean = _safe_host(host)
    if not clean:
        return set()
    names = {clean.lower(), clean.lower().removesuffix(".local")}
    try:
        infos = socket.getaddrinfo(clean, None)
    except OSError:
        infos = []
    for info in infos:
        addr = str(info[4][0] or "")
        if addr:
            names.add(addr.lower())
    return names


def _hosts_match(a: str | None, b: str | None) -> bool:
    return bool(host_aliases(a) & host_aliases(b))


def _volume_path(share: str) -> Path:
    return Path("/Volumes") / share


def is_mounted(share: str) -> bool:
    path = _volume_path(share)
    try:
        return path.is_dir()
    except OSError:
        return False


def discover_smb_hosts(timeout: float = 1.6) -> list[str]:
    """Bonjour SMB names on the LAN. dns-sd does not exit; timeout is expected."""
    try:
        proc = subprocess.run(
            ["dns-sd", "-B", "_smb._tcp", "local."],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
    except (FileNotFoundError, OSError):
        return []
    names: list[str] = []
    for line in text.splitlines():
        if "_smb._tcp" not in line or "Add" not in line:
            continue
        name = _safe_host(line.strip().split()[-1] if line.strip() else "")
        if name and name not in names:
            names.append(name)
    return names


def finder_last_smb_host() -> str | None:
    plist = Path.home() / "Library/Preferences/com.apple.finder.plist"
    try:
        data = plistlib.loads(plist.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return _safe_host(data.get("FXConnectToLastURL"))


def netauth_shares() -> dict[str, list[str]]:
    plist = Path.home() / "Library/Preferences/com.apple.NetAuthAgent.plist"
    try:
        data = plistlib.loads(plist.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    raw = data.get("PreviouslySelectedShares") or {}
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for host, shares in raw.items():
        key = _safe_host(str(host))
        if not key:
            continue
        names = []
        for item in shares or []:
            share = _safe_share(str(item))
            if share and share not in names:
                names.append(share)
        if names:
            out[key] = names
    return out


def finder_recent_shares() -> list[str]:
    plist = Path.home() / "Library/Preferences/com.apple.finder.plist"
    try:
        data = plistlib.loads(plist.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return []
    names: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            text = node.replace("file://", "").split("?")[0]
            share = None
            if text.lower().startswith("smb://"):
                share = _safe_share((urlparse(text).path or "").strip("/").split("/")[0])
            elif "/Volumes/" in text:
                parts = Path(text).parts
                if len(parts) >= 3 and parts[1].lower() == "volumes":
                    share = _safe_share(parts[2])
            if share and share not in names:
                names.append(share)

    walk(data)
    return names


def preferred_host() -> str | None:
    bonjour = discover_smb_hosts()
    finder = finder_last_smb_host()
    netauth = list(netauth_shares())
    candidates = [finder, *bonjour, *netauth]
    for item in candidates:
        host = _safe_host(item)
        if not host:
            continue
        short = host.removesuffix(".local")
        if bonjour and not any(_hosts_match(host, seen) for seen in bonjour):
            continue
        return f"{short}.local"
    host = _safe_host(finder or (bonjour[0] if bonjour else None) or (netauth[0] if netauth else None))
    if not host:
        return None
    short = host.removesuffix(".local")
    return f"{short}.local"


def known_shares(host: str | None = None, *, recent: bool = True) -> list[str]:
    names: list[str] = []

    def add(name: str | None) -> None:
        share = _safe_share(name)
        if share and share not in names:
            names.append(share)

    for root in remembered_volume_roots():
        add(Path(root).name)
    target = _safe_host(host)
    aliases = host_aliases(target) if target else set()
    for key, shares in netauth_shares().items():
        if target and not (aliases & host_aliases(key)):
            continue
        for share in shares:
            add(share)
    if recent:
        for share in finder_recent_shares():
            add(share)
    return names


def mount_share(host: str, share: str, timeout: float = MOUNT_TIMEOUT) -> dict:
    host = _safe_host(host)
    share = _safe_share(share)
    if not host or not share:
        return {"share": share, "host": host, "ok": False, "mounted": False, "error": "Bad host or share name."}
    if is_mounted(share):
        return {"share": share, "host": host, "ok": True, "mounted": True, "error": None}
    url = f"smb://{host}/{share}"
    try:
        proc = subprocess.run(
            ["osascript", "-e", f'tell application "Finder" to mount volume "{url}"'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        mounted = is_mounted(share)
        return {
            "share": share,
            "host": host,
            "ok": mounted,
            "mounted": mounted,
            "error": None if mounted else "Finder did not finish connecting. Check the login dialog.",
        }
    except OSError as exc:
        return {"share": share, "host": host, "ok": False, "mounted": False, "error": str(exc)}
    mounted = is_mounted(share)
    err = None
    if proc.returncode != 0 or not mounted:
        err = (proc.stderr or proc.stdout or "Finder could not mount the share.").strip()
        if mounted:
            err = None
    return {"share": share, "host": host, "ok": mounted, "mounted": mounted, "error": err}


def mount_known(share: str | None = None, *, recent: bool = False) -> dict:
    host = preferred_host()
    if not host:
        return {
            "host": None,
            "ok": False,
            "items": [],
            "error": "No Synology or SMB server is on this network.",
        }
    wanted = [_safe_share(share)] if share else known_shares(host, recent=recent)
    wanted = [item for item in wanted if item]
    if not wanted:
        return {
            "host": host,
            "ok": False,
            "items": [],
            "error": "No SMB share names are known yet. Choose a folder or mount the volume in Finder.",
        }
    items = [mount_share(host, item) for item in wanted]
    ok = any(item["mounted"] for item in items)
    first_err = next((item["error"] for item in items if item["error"] and not item["mounted"]), None)
    return {
        "host": host,
        "ok": ok,
        "items": items,
        "error": None if ok else first_err or "Could not mount the NAS share.",
    }


def shares_for_paths(paths: list[Path | str]) -> list[str]:
    names: list[str] = []
    for raw in paths or []:
        parts = Path(raw).expanduser().parts
        if len(parts) >= 3 and parts[1] == "Volumes":
            share = _safe_share(parts[2])
            if share and share not in names:
                names.append(share)
    return names


def mount_for_paths(paths: list[Path | str]) -> dict:
    """Mount /Volumes shares used by stored album paths, if they are not up yet."""
    wanted = [share for share in shares_for_paths(paths) if not is_mounted(share)]
    if not wanted:
        return {"ok": True, "items": [], "error": None, "host": preferred_host()}
    host = preferred_host()
    if not host:
        return {
            "host": None,
            "ok": False,
            "items": [],
            "error": "No Synology or SMB server is on this network.",
        }
    items = [mount_share(host, item) for item in wanted]
    ok = any(item["mounted"] for item in items)
    first_err = next((item["error"] for item in items if item["error"] and not item["mounted"]), None)
    return {
        "host": host,
        "ok": ok,
        "items": items,
        "error": None if ok else first_err or "Could not mount the NAS share.",
    }