"""SuperGrok OAuth via the public xAI device-code flow (same family as Grok Build)."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR, ensure_dirs
from .originals import assert_data_write

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
SCOPE = "openid profile email offline_access api:access grok-cli:access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
AUTH_FILE = "xai.auth.json"
TOKEN_FILE = "xai.oauth.json"
PENDING_FILE = "xai.oauth.pending.json"
SKEW_SECONDS = 120


class OAuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dirs()
    dest = assert_data_write(path)
    dest.write_text(json.dumps(payload), encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def auth_path() -> Path:
    return DATA_DIR / AUTH_FILE


def token_path() -> Path:
    return DATA_DIR / TOKEN_FILE


def pending_path() -> Path:
    return DATA_DIR / PENDING_FILE


def auth_mode() -> str:
    mode = str(_read_json(auth_path()).get("mode") or "").strip().lower()
    if mode in {"supergrok", "oauth", "supergrok_oauth"}:
        return "supergrok"
    return "api_key"


def set_auth_mode(mode: str) -> None:
    _write_json(auth_path(), {"mode": "supergrok" if mode == "supergrok" else "api_key"})


def session_public() -> dict[str, Any]:
    tokens = _read_json(token_path())
    pending = _read_json(pending_path())
    access = str(tokens.get("access_token") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    expires_at = float(tokens.get("expires_at") or 0)
    live = bool(access) and expires_at > time.time()
    mode = auth_mode()
    signed_in = live
    expired = mode == "supergrok" and not live and not pending.get("device_code")
    out = {
        "auth_mode": mode,
        "oauth_signed_in": signed_in,
        "oauth_expired": expired,
        "oauth_email": tokens.get("email") or None,
        "oauth_pending": bool(pending.get("device_code")),
        "oauth_browsers": listed_browsers(),
    }
    if pending.get("device_code"):
        out["oauth_user_code"] = pending.get("user_code")
        # Never hand out verification_uri_complete. After a typo xAI blocks that URL.
        out["oauth_verification_uri"] = pending.get("verification_uri") or "https://accounts.x.ai/oauth2/device"
        out["oauth_interval"] = int(pending.get("interval") or 5)
    return out


def start_login() -> dict[str, Any]:
    cancel_login()
    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(
                DEVICE_CODE_URL,
                data={"client_id": CLIENT_ID, "scope": SCOPE},
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise OAuthError("Could not start SuperGrok sign-in. Try again.") from exc
    if res.status_code >= 400:
        raise OAuthError(f"SuperGrok sign-in failed to start ({res.status_code}).")
    data = res.json()
    device_code = str(data.get("device_code") or "")
    user_code = str(data.get("user_code") or "")
    if not device_code or not user_code:
        raise OAuthError("SuperGrok did not return a sign-in code.")
    pending = {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": data.get("verification_uri") or "https://accounts.x.ai/oauth2/device",
        "verification_uri_complete": data.get("verification_uri_complete") or None,
        "interval": int(data.get("interval") or 5),
        "expires_at": time.time() + int(data.get("expires_in") or 1800),
    }
    _write_json(pending_path(), pending)
    return session_public()


def cancel_login() -> dict[str, Any]:
    path = pending_path()
    if path.is_file():
        assert_data_write(path).unlink()
    return session_public()


def poll_login() -> dict[str, Any]:
    pending = _read_json(pending_path())
    device_code = str(pending.get("device_code") or "")
    if not device_code:
        return {"status": "idle", **session_public()}
    if time.time() > float(pending.get("expires_at") or 0):
        cancel_login()
        return {"status": "expired", "message": "The sign-in code expired. Start again.", **session_public()}
    result = _token_request(
        {
            "grant_type": DEVICE_GRANT,
            "device_code": device_code,
            "client_id": CLIENT_ID,
        }
    )
    status = result.get("status")
    if status == "ok":
        _store_tokens(result["tokens"])
        set_auth_mode("supergrok")
        cancel_login()
        return {"status": "ok", "message": "Signed in with SuperGrok.", **session_public()}
    if status == "pending":
        return {"status": "pending", **session_public()}
    cancel_login()
    hint = {
        "denied": "Sign-in was denied or the code was entered wrong. Get a new code — the old link is spent.",
        "expired": "That code expired. Get a new code.",
        "error": result.get("message") or "SuperGrok sign-in did not finish. Get a new code.",
    }.get(status, "Get a new code and type it carefully in the browser you opened.")
    return {"status": status, "message": hint, **session_public()}


BROWSERS: dict[str, dict[str, Any]] = {
    "brave": {
        "label": "Brave",
        "darwin": ("Brave Browser", "Brave Browser Beta", "Brave Browser Nightly"),
        "linux": ("brave-browser", "brave"),
        "win32": (r"BraveSoftware\Brave-Browser\Application\brave.exe",),
    },
    "chrome": {
        "label": "Chrome",
        "darwin": ("Google Chrome", "Google Chrome Beta", "Chromium"),
        "linux": ("google-chrome", "chromium", "chromium-browser"),
        "win32": (r"Google\Chrome\Application\chrome.exe", r"Chromium\Application\chrome.exe"),
    },
    "firefox": {
        "label": "Firefox",
        "darwin": ("Firefox", "Firefox Developer Edition", "Firefox Nightly"),
        "linux": ("firefox",),
        "win32": (r"Mozilla Firefox\firefox.exe",),
    },
    "safari": {
        "label": "Safari",
        "darwin": ("Safari",),
        "linux": (),
        "win32": (),
    },
}


def _windows_browser_exe(spec: dict[str, Any]) -> str | None:
    """The first installed executable for this browser under the usual Windows folders."""
    import os
    from pathlib import Path

    bases = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
    for rel in spec.get("win32", ()):
        for base in bases:
            if not base:
                continue
            candidate = Path(base).joinpath(*rel.split("\\"))
            if candidate.is_file():
                return str(candidate)
    return None


def listed_browsers() -> list[dict[str, Any]]:
    return [
        {"id": key, "label": spec["label"], "available": browser_available(key)}
        for key, spec in BROWSERS.items()
    ]


def browser_available(browser_id: str) -> bool:
    spec = BROWSERS.get(browser_id)
    if not spec:
        return False
    if sys.platform == "darwin":
        return any(_darwin_app_exists(app) for app in spec["darwin"])
    if sys.platform == "win32":
        return _windows_browser_exe(spec) is not None
    return any(_which(name) for name in spec["linux"])


def _darwin_app_exists(app: str) -> bool:
    probe = subprocess.run(["open", "-Ra", app], capture_output=True, check=False)
    return probe.returncode == 0


def open_in_browser(browser_id: str) -> dict[str, Any]:
    pending = _read_json(pending_path())
    url = str(pending.get("verification_uri") or "https://accounts.x.ai/oauth2/device")
    opened = launch_browser(browser_id, url)
    return {**session_public(), "opened_in": opened}


def sign_out() -> dict[str, Any]:
    for path in (token_path(), pending_path()):
        if path.is_file():
            assert_data_write(path).unlink()
    set_auth_mode("api_key")
    return session_public()


def valid_access_token() -> str:
    tokens = _read_json(token_path())
    access = str(tokens.get("access_token") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    expires_at = float(tokens.get("expires_at") or 0)
    now = time.time()
    if access and expires_at > now + SKEW_SECONDS:
        return access
    if refresh:
        try:
            result = _token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": CLIENT_ID,
                }
            )
            if result.get("status") == "ok":
                _store_tokens(result["tokens"], keep_refresh=refresh)
                return str(result["tokens"]["access_token"])
        except OAuthError:
            pass
    if access and expires_at > now:
        return access
    return ""


def launch_browser(browser_id: str, url: str) -> str:
    if not url.startswith("https://"):
        raise OAuthError("Refusing to open that address.")
    spec = BROWSERS.get((browser_id or "").strip().lower())
    if not spec:
        raise OAuthError("Choose Brave, Chrome, Firefox, or Safari.")
    label = spec["label"]
    if sys.platform == "darwin":
        for app in spec["darwin"]:
            if not _darwin_app_exists(app):
                continue
            opened = subprocess.run(["open", "-a", app, url], capture_output=True, check=False)
            if opened.returncode == 0:
                return app
        raise OAuthError(f"{label} is not installed.")
    if sys.platform == "win32":
        exe = _windows_browser_exe(spec)
        if exe:
            subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return exe
        # Fall back to the default browser rather than fail the sign-in.
        import webbrowser

        if webbrowser.open(url):
            return "default browser"
        raise OAuthError(f"{label} is not installed.")
    binary = next((name for name in spec["linux"] if _which(name)), None)
    if not binary:
        raise OAuthError(f"{label} is not installed.")
    subprocess.Popen([binary, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return binary


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def email_from_jwt(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    raw = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("email", "preferred_username"):
        value = str(payload.get(field) or "").strip()
        if value:
            return value
    return None


def _store_tokens(tokens: dict[str, Any], keep_refresh: str | None = None) -> None:
    access = str(tokens.get("access_token") or "")
    refresh = str(tokens.get("refresh_token") or keep_refresh or "")
    expires_in = int(tokens.get("expires_in") or 3600)
    existing = _read_json(token_path())
    email = email_from_jwt(access) or existing.get("email")
    _write_json(
        token_path(),
        {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": time.time() + expires_in,
            "email": email,
        },
    )


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(TOKEN_URL, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError("Could not reach SuperGrok sign-in.") from exc
    raw = res.text
    try:
        data = res.json()
    except json.JSONDecodeError:
        data = {}
    if res.is_success and isinstance(data, dict) and data.get("access_token"):
        return {"status": "ok", "tokens": data}
    err = str((data or {}).get("error") or "")
    desc = str((data or {}).get("error_description") or err or raw[:200] or f"HTTP {res.status_code}")
    if err in {"authorization_pending", "slow_down"} or (res.status_code == 400 and "pending" in desc.lower()):
        return {"status": "pending"}
    if err == "access_denied":
        return {"status": "denied", "message": desc}
    if err == "expired_token":
        return {"status": "expired", "message": desc}
    return {"status": "error", "message": desc}
