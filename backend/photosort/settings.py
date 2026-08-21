"""App settings. The xAI key lives in a user-wide file so it survives restarts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR, XAI_API_BASE
from .originals import assert_data_write
from . import state as state_mod

AUTO_UPDATE_KEY = "auto_update"
AUTO_SCAN_NEW_KEY = "auto_scan_new"

# Official env name, stored as a file any xAI tool can find.
KEY_ENV = "XAI_API_KEY"
KEY_FILE = "api_key"
APP_KEY_NAME = "xai.api_key"
LEGACY_KEY_NAME = "xai.key"


class SettingsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def key_path() -> Path:
    """User-wide xAI key: ~/.config/xai/api_key (or $XDG_CONFIG_HOME/xai/api_key)."""
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (root / "xai" / KEY_FILE).resolve()


def app_key_path() -> Path:
    """Key next to the catalog so it survives Family Faces restarts."""
    return (DATA_DIR / APP_KEY_NAME).resolve()


def shared_key_path() -> Path:
    return (Path.home() / ".xai" / KEY_FILE).resolve()


def legacy_key_path() -> Path:
    return DATA_DIR / LEGACY_KEY_NAME


def key_candidates() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in (app_key_path(), key_path(), shared_key_path(), legacy_key_path()):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _read_key_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_key_file(path: Path, key: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    path.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _display_path(path: Path) -> str:
    try:
        home = Path.home().resolve()
        resolved = path.resolve()
        if resolved.is_relative_to(home):
            return "~/" + str(resolved.relative_to(home))
    except (OSError, ValueError, RuntimeError):
        pass
    return str(path)


def migrate_legacy_key() -> Path | None:
    existing = _read_key_file(app_key_path()) or _read_key_file(key_path())
    if existing:
        _sync_key_copies(existing)
        return app_key_path() if _read_key_file(app_key_path()) else key_path()
    for src in (legacy_key_path(), shared_key_path()):
        key = _read_key_file(src)
        if not key:
            continue
        written = _sync_key_copies(key)
        if src == legacy_key_path() and written and src.is_file() and src.resolve() != app_key_path():
            try:
                assert_data_write(src).unlink()
            except OSError:
                pass
        return written
    return None


def _sync_key_copies(key: str) -> Path | None:
    written: Path | None = None
    try:
        written = _write_key_file(assert_data_write(app_key_path()), key)
    except OSError:
        pass
    try:
        home_copy = _write_key_file(key_path(), key)
        written = written or home_copy
    except OSError:
        pass
    return written


def stored_key_path() -> Path:
    for path in key_candidates():
        if _read_key_file(path):
            return path
    return app_key_path()


def saved_xai_key() -> str:
    migrate_legacy_key()
    for path in key_candidates():
        key = _read_key_file(path)
        if key:
            return key
    return ""


def env_xai_key() -> str:
    return (os.environ.get(KEY_ENV) or "").strip()


def active_xai_key() -> str:
    from . import oauth as oauth_mod

    return saved_xai_key() or env_xai_key() or oauth_mod.valid_access_token()


def key_hint(key: str) -> str | None:
    key = (key or "").strip()
    if not key:
        return None
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"


def public_settings(*, reveal: bool = False) -> dict[str, Any]:
    saved = saved_xai_key()
    env = env_xai_key()
    from . import oauth as oauth_mod

    oauth = oauth_mod.session_public()
    oauth_ready = bool(oauth.get("oauth_signed_in"))
    if saved:
        source = "settings"
        active = True
    elif env:
        source = "environment"
        active = True
    elif oauth_ready:
        source = "supergrok"
        active = True
    else:
        source = None
        active = False
    out = {
        "xai_key_set": bool(active),
        "xai_key_source": source,
        "xai_key_hint": None if source == "supergrok" else key_hint(saved or env),
        "xai_key_path": _display_path(stored_key_path()),
        "lookup_available": bool(saved or env or oauth_ready),
        "auto_update": auto_update_enabled(),
        "auto_scan_new": auto_scan_new_enabled(),
        **oauth,
    }
    out["xai_key_set"] = bool(active)
    out["xai_key_source"] = source
    if reveal:
        out["xai_api_key"] = (saved or env) or None
    return out


def normalize_key(key: str) -> str:
    cleaned = (key or "").strip().strip('"').strip("'")
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and not ch.isspace())
    return cleaned


def save_xai_key(key: str) -> dict[str, Any]:
    cleaned = normalize_key(key)
    if not cleaned:
        raise SettingsError("Paste an xAI API key, or remove the saved one.")
    if len(cleaned) < 12:
        raise SettingsError("That key is too short.")
    written = _sync_key_copies(cleaned)
    if not written:
        raise SettingsError(f"Could not save the key to {_display_path(app_key_path())}.")
    leftover = legacy_key_path()
    if leftover.is_file() and leftover.resolve() != app_key_path():
        try:
            assert_data_write(leftover).unlink()
        except OSError:
            pass
    check = verify_xai_key(cleaned)
    out = public_settings()
    if not check.get("ok"):
        out["warning"] = (
            check.get("error")
            or "Saved on this Mac. xAI did not accept the key yet — Look up famous face may fail until it does."
        )
    elif check.get("warning"):
        out["warning"] = check["warning"]
    return out


def clear_xai_key() -> dict[str, Any]:
    for path in (app_key_path(), key_path(), shared_key_path(), legacy_key_path()):
        if not path.is_file():
            continue
        try:
            if path in {app_key_path(), legacy_key_path()}:
                path = assert_data_write(path)
            path.unlink()
        except OSError:
            pass
    return public_settings()


def verify_xai_key(key: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=12.0) as client:
            res = client.get(
                f"{XAI_API_BASE}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.HTTPError:
        return {"ok": True, "warning": "Saved on this Mac. Family Faces could not reach xAI to check the key."}
    detail = _xai_error(res)
    if res.status_code in {400, 401, 403}:
        return {
            "ok": False,
            "error": detail
            or "xAI did not accept that key. Create an API key at console.x.ai, not a Grok login token.",
        }
    if res.status_code >= 500:
        return {"ok": True, "warning": "Saved on this Mac. xAI was busy, so the key was not checked."}
    if res.status_code >= 400:
        return {"ok": False, "error": detail or f"Could not check the key ({res.status_code})."}
    return {"ok": True}


def _truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def auto_update_enabled() -> bool:
    return _truthy(state_mod.get_state(AUTO_UPDATE_KEY), True)


def auto_scan_new_enabled() -> bool:
    return _truthy(state_mod.get_state(AUTO_SCAN_NEW_KEY), True)


def save_auto_update(*, auto_update: bool | None = None, auto_scan_new: bool | None = None) -> dict[str, Any]:
    if auto_update is not None:
        state_mod.set_state(AUTO_UPDATE_KEY, "1" if auto_update else "0")
    if auto_scan_new is not None:
        state_mod.set_state(AUTO_SCAN_NEW_KEY, "1" if auto_scan_new else "0")
    return public_settings()


def _xai_error(res: httpx.Response) -> str:
    try:
        payload = res.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    text = str(payload.get("error") or payload.get("message") or "").strip()
    if text.lower().startswith("incorrect api key"):
        return "xAI said this API key is incorrect. Create a new one at console.x.ai → API keys."
    return text
