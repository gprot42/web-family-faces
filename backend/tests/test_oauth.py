import base64
import json

from photosort import config, oauth, originals


def _data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(oauth, "DATA_DIR", data)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    return data


def _jwt(email="heavy@example.com"):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"aaa.{payload}.sig"


def test_email_from_jwt():
    assert oauth.email_from_jwt(_jwt("a@b.c")) == "a@b.c"
    assert oauth.email_from_jwt("not-a-jwt") is None


def test_start_and_poll_success(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)

    class FakeResp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload)

        @property
        def is_success(self):
            return self.status_code < 400

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None, headers=None):
            calls["n"] += 1
            if "device/code" in url:
                return FakeResp(
                    {
                        "device_code": "dev-1",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://accounts.x.ai/oauth2/device",
                        "verification_uri_complete": "https://accounts.x.ai/oauth2/device?user_code=ABCD-EFGH",
                        "interval": 5,
                        "expires_in": 600,
                    }
                )
            return FakeResp(
                {
                    "access_token": _jwt(),
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                }
            )

    monkeypatch.setattr(oauth.httpx, "Client", FakeClient)
    started = oauth.start_login()
    assert started["oauth_pending"] is True
    assert started["oauth_user_code"] == "ABCD-EFGH"
    assert "oauth_verification_uri_complete" not in started
    assert "user_code=" not in json.dumps(started)
    assert "dev-1" not in json.dumps(started)
    done = oauth.poll_login()
    assert done["status"] == "ok"
    assert done["oauth_signed_in"] is True
    assert done["oauth_email"] == "heavy@example.com"
    assert oauth.auth_mode() == "supergrok"
    token = oauth.valid_access_token()
    assert token.startswith("aaa.")
    assert "refresh-1" not in json.dumps(oauth.session_public())


def test_poll_pending_keeps_code(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    oauth._write_json(
        oauth.pending_path(),
        {
            "device_code": "dev-2",
            "user_code": "WAIT-CODE",
            "verification_uri": "https://accounts.x.ai/oauth2/device",
            "interval": 5,
            "expires_at": 9e12,
        },
    )
    monkeypatch.setattr(
        oauth,
        "_token_request",
        lambda form: {"status": "pending"},
    )
    out = oauth.poll_login()
    assert out["status"] == "pending"
    assert out["oauth_user_code"] == "WAIT-CODE"


def test_launch_browser_requires_known_app(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setattr(oauth.sys, "platform", "darwin")

    def fake_run(cmd, capture_output=True, check=False):
        class R:
            returncode = 1
        return R()

    monkeypatch.setattr(oauth.subprocess, "run", fake_run)
    try:
        oauth.launch_browser("netscape", "https://accounts.x.ai/oauth2/device")
        raise AssertionError("should fail")
    except oauth.OAuthError as exc:
        assert "Brave" in exc.message
    try:
        oauth.launch_browser("firefox", "https://accounts.x.ai/oauth2/device")
        raise AssertionError("should fail when Firefox is missing")
    except oauth.OAuthError as exc:
        assert "Firefox" in exc.message
