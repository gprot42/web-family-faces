"""Connect Synology / SMB shares.

macOS mounts through Finder so Keychain supplies the login and the share
appears under /Volumes. Windows connects with `net use` so the saved Windows
credential is used and the share is reached by its \\server\share path.
"""

from __future__ import annotations

import plistlib
import re
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import system
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
    if system.IS_WINDOWS:
        for root in remembered_volume_roots():
            parts = system.unc_parts(root)
            if parts and parts[1].lower() == share.lower():
                return Path(root)
        host = preferred_host()
        return Path(f"\\\\{host}\\{share}") if host else Path(f"\\\\?\\{share}")
    if system.IS_MAC:
        return Path("/Volumes") / share
    return Path("/mnt") / share


def share_path(share: str, host: str | None = None) -> str:
    """Where a connected share is reached from, for the UI."""
    if system.IS_WINDOWS and host:
        return f"\\\\{host}\\{share}"
    return str(_volume_path(share))


def is_mounted(share: str, host: str | None = None) -> bool:
    candidates = [_volume_path(share)]
    if system.IS_WINDOWS and host:
        candidates.insert(0, Path(f"\\\\{host}\\{share}"))
    for path in candidates:
        try:
            if path.is_dir():
                return True
        except OSError:
            continue
    return False


def remembered_hosts() -> list[str]:
    """Servers of the UNC share roots the catalog has already used (Windows)."""
    hosts: list[str] = []
    for root in remembered_volume_roots():
        parts = system.unc_parts(root)
        host = _safe_host(parts[0]) if parts else None
        if host and host not in hosts:
            hosts.append(host)
    return hosts


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
    if system.IS_WINDOWS:
        remembered = remembered_hosts()
        if remembered:
            return remembered[0]
        bonjour = discover_smb_hosts()
        return f"{bonjour[0].removesuffix('.local')}.local" if bonjour else None
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
    if is_mounted(share, host):
        return {"share": share, "host": host, "ok": True, "mounted": True, "error": None}
    url = f"smb://{host}/{share}"
    if system.IS_WINDOWS:
        command = ["net", "use", f"\\\\{host}\\{share}", "/persistent:no"]
    elif system.IS_MAC:
        command = ["osascript", "-e", f'tell application "Finder" to mount volume "{url}"']
    else:
        command = ["gio", "mount", url]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        mounted = is_mounted(share, host)
        return {
            "share": share,
            "host": host,
            "ok": mounted,
            "mounted": mounted,
            "error": None if mounted else "The system did not finish connecting. Check for a login prompt.",
        }
    except OSError as exc:
        return {"share": share, "host": host, "ok": False, "mounted": False, "error": str(exc)}
    mounted = is_mounted(share, host)
    err = None
    if proc.returncode != 0 or not mounted:
        err = (proc.stderr or proc.stdout or "Could not connect the share.").strip()
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
            "error": f"No SMB share names are known yet. Choose a folder, or {system.mount_hint().lower()}.",
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
        unc = system.unc_parts(raw)
        if unc:
            share = _safe_share(unc[1])
            if share and share not in names:
                names.append(share)
            continue
        parts = Path(raw).expanduser().parts
        if len(parts) >= 3 and parts[1] == "Volumes":
            share = _safe_share(parts[2])
            if share and share not in names:
                names.append(share)
    return names


def unc_targets_for_paths(paths: list[Path | str]) -> list[tuple[str, str]]:
    """(server, share) pairs from UNC album paths, for Windows."""
    out: list[tuple[str, str]] = []
    for raw in paths or []:
        unc = system.unc_parts(raw)
        if not unc:
            continue
        host, share = _safe_host(unc[0]), _safe_share(unc[1])
        if host and share and (host, share) not in out:
            out.append((host, share))
    return out


def mount_for_paths(paths: list[Path | str]) -> dict:
    """Connect the shares used by stored album paths, if they are not up yet."""
    if system.IS_WINDOWS:
        targets = [(h, s) for h, s in unc_targets_for_paths(paths) if not is_mounted(s, h)]
        if not targets:
            return {"ok": True, "items": [], "error": None, "host": preferred_host()}
        items = [mount_share(h, s) for h, s in targets]
        ok = any(item["mounted"] for item in items)
        first_err = next((item["error"] for item in items if item["error"] and not item["mounted"]), None)
        return {"host": targets[0][0], "ok": ok, "items": items, "error": None if ok else first_err}
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