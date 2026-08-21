from photosort import config, db, oauth, originals, settings


def _data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    xdg = tmp_path / "config"
    home.mkdir()
    xdg.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "photosort.db")
    monkeypatch.setattr(db, "DB_PATH", data / "photosort.db")
    monkeypatch.setattr(settings, "DATA_DIR", data)
    monkeypatch.setattr(oauth, "DATA_DIR", data)
    monkeypatch.setattr(originals, "DATA_DIR", data)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    return data


def test_normalize_key_strips_paste_junk():
    assert settings.normalize_key("  Bearer xai-abc123  ") == "xai-abc123"
    assert settings.normalize_key('"xai-abc123"') == "xai-abc123"


def test_key_hint_hides_middle():
    assert settings.key_hint("xai-abcdefghijklmnopqrstuvwxyz") == "xai-…wxyz"
    assert settings.key_hint("") is None


def test_save_and_clear_key_uses_user_config(tmp_path, monkeypatch):
    data = _data(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "verify_xai_key", lambda key: {"ok": True})
    out = settings.save_xai_key("xai-test-key-123456")
    assert out["xai_key_set"] is True
    assert out["xai_key_source"] == "settings"
    assert out["xai_key_hint"] == "xai-…3456"
    assert "xai-test-key-123456" not in str(out)
    shown = settings.public_settings(reveal=True)
    assert shown["xai_api_key"] == "xai-test-key-123456"
    hidden = settings.public_settings()
    assert "xai_api_key" not in hidden
    app_path = data / "xai.api_key"
    home_path = tmp_path / "config" / "xai" / "api_key"
    assert app_path.is_file()
    assert app_path.read_text(encoding="utf-8").strip() == "xai-test-key-123456"
    assert oct(app_path.stat().st_mode)[-3:] == "600"
    assert home_path.is_file()
    assert home_path.read_text(encoding="utf-8").strip() == "xai-test-key-123456"
    assert not (data / "xai.key").exists()
    assert settings.active_xai_key() == "xai-test-key-123456"
    assert config.xai_api_key() == "xai-test-key-123456"
    cleared = settings.clear_xai_key()
    assert cleared["xai_key_set"] is False
    assert not app_path.exists()
    assert not home_path.exists()


def test_saved_key_survives_data_dir_change(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "verify_xai_key", lambda key: {"ok": True})
    settings.save_xai_key("xai-keep-me-123456")
    other = tmp_path / "other-data"
    other.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", other)
    monkeypatch.setattr(settings, "DATA_DIR", other)
    monkeypatch.setattr(originals, "DATA_DIR", other)
    assert settings.active_xai_key() == "xai-keep-me-123456"


def test_legacy_data_key_is_migrated(tmp_path, monkeypatch):
    data = _data(tmp_path, monkeypatch)
    legacy = data / "xai.key"
    legacy.write_text("xai-old-key-zzzzzz\n", encoding="utf-8")
    dest = tmp_path / "config" / "xai" / "api_key"
    app = data / "xai.api_key"
    assert settings.saved_xai_key() == "xai-old-key-zzzzzz"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8").strip() == "xai-old-key-zzzzzz"
    assert app.is_file()
    assert app.read_text(encoding="utf-8").strip() == "xai-old-key-zzzzzz"
    assert not legacy.exists()


def test_saved_key_wins_over_environment(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env-aaaaaaaa")
    monkeypatch.setattr(settings, "verify_xai_key", lambda key: {"ok": True})
    assert settings.active_xai_key() == "xai-from-env-aaaaaaaa"
    settings.save_xai_key("xai-from-settings-bbbb")
    assert settings.active_xai_key() == "xai-from-settings-bbbb"
    settings.clear_xai_key()
    assert settings.active_xai_key() == "xai-from-env-aaaaaaaa"


def test_key_is_saved_even_if_xai_is_unreachable(tmp_path, monkeypatch):
    data = _data(tmp_path, monkeypatch)
    monkeypatch.setattr(
        settings,
        "verify_xai_key",
        lambda key: {"ok": False, "error": "xAI was busy."},
    )
    out = settings.save_xai_key("xai-offline-key-123456")
    assert out["xai_key_set"] is True
    assert (data / "xai.api_key").read_text(encoding="utf-8").strip() == "xai-offline-key-123456"
    assert "xAI was busy" in (out.get("warning") or "")


def test_short_key_is_rejected(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    try:
        settings.save_xai_key("short")
        raise AssertionError("should have failed")
    except settings.SettingsError:
        pass


def test_auto_update_defaults_on_scan_on(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    out = settings.public_settings()
    assert out["auto_update"] is True
    assert out["auto_scan_new"] is True


def test_auto_update_flags_persist(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    saved = settings.save_auto_update(auto_update=True, auto_scan_new=False)
    assert saved["auto_update"] is True
    assert saved["auto_scan_new"] is False
    again = settings.public_settings()
    assert again["auto_update"] is True
    assert again["auto_scan_new"] is False
    settings.save_auto_update(auto_update=False)
    assert settings.auto_update_enabled() is False
    assert settings.auto_scan_new_enabled() is False


def test_name_sex_check_defaults_on_and_persists(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    assert settings.name_sex_check_enabled() is True
    assert settings.public_settings()["name_sex_check"] is True
    settings.save_name_sex_check(False)
    assert settings.name_sex_check_enabled() is False
    assert settings.public_settings()["name_sex_check"] is False
    settings.save_name_sex_check(True)
    assert settings.name_sex_check_enabled() is True
