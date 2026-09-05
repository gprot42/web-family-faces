"""Windows branches, run on any platform by flipping the platform flags."""

from pathlib import Path

from photosort import browse, importer, nas, oauth, system


def _windows(monkeypatch):
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "IS_MAC", False)
    monkeypatch.setattr(system, "IS_LINUX", False)


def test_unc_helpers():
    assert system.unc_parts(r"\\nas\photos\2019\a.jpg") == ("nas", "photos")
    assert system.unc_parts("//nas/photos") == ("nas", "photos")
    assert system.unc_parts(r"C:\Users\me") is None
    assert system.unc_parts(r"\\?\C:\x") is None
    assert system.unc_root(r"\\nas\photos\2019") == r"\\nas\photos"
    assert system.unc_root("/Volumes/photos") is None


def test_norm_folder_keeps_drive_root_on_windows(monkeypatch):
    _windows(monkeypatch)
    assert importer._norm_folder("C:\\Photos\\") == "C:\\Photos"
    assert importer._norm_folder("C:\\") == "C:\\"
    assert importer._norm_folder(r"\\nas\photos\\") == r"\\nas\photos"


def test_browse_roots_on_windows(monkeypatch):
    _windows(monkeypatch)
    monkeypatch.setattr(
        system,
        "drive_roots",
        lambda: [{"path": "C:\\", "kind": "local"}, {"path": "Z:\\", "kind": "network"}],
    )
    monkeypatch.setattr(browse, "remembered_volume_roots", lambda: [r"\\nas\photos"])
    items = browse.roots()
    by_name = {item["name"]: item for item in items}
    assert by_name["C:\\"]["hint"] == "This PC"
    assert by_name["Z:\\"]["kind"] == "nas-volume"
    assert by_name[r"\\nas\photos"]["kind"] == "nas-volume"
    assert by_name["Home"]["hint"] == "This PC"
    listing = browse.list_folder("volumes")
    names = [e["name"] for e in listing["entries"]]
    assert "Z:\\" in names and r"\\nas\photos" in names
    unc = next(e for e in listing["entries"] if e["name"] == r"\\nas\photos")
    assert unc["mounted"] is False and "File Explorer" in unc["error"]
    assert browse._volume_root_of(r"\\nas\photos\2019\x.jpg") == r"\\nas\photos"
    assert browse._public_parent(Path(r"\\nas\photos")) == "volumes"


def test_nas_on_windows_uses_net_use(monkeypatch):
    _windows(monkeypatch)
    calls = []

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(nas.subprocess, "run", fake_run)
    monkeypatch.setattr(nas, "remembered_volume_roots", lambda: [r"\\nas\photos"])
    state = {"up": False}
    monkeypatch.setattr(nas, "is_mounted", lambda share, host=None: state["up"])
    result = nas.mount_share("nas", "photos")
    assert calls and calls[0][:2] == ["net", "use"] and calls[0][2] == r"\\nas\photos"
    assert result["mounted"] is False and result["error"]
    state["up"] = True
    assert nas.mount_share("nas", "photos")["mounted"] is True
    assert nas.preferred_host() == "nas"
    assert nas.share_path("photos", "nas") == r"\\nas\photos"
    assert nas.unc_targets_for_paths([r"\\nas\photos\2019", "/Volumes/other"]) == [("nas", "photos")]
    assert nas.shares_for_paths([r"\\nas\photos\2019"]) == ["photos"]


def test_windows_browser_lookup(monkeypatch, tmp_path):
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LocalAppData", raising=False)
    assert oauth._windows_browser_exe(oauth.BROWSERS["chrome"]) == str(exe)
    assert oauth._windows_browser_exe(oauth.BROWSERS["safari"]) is None


def test_platform_labels(monkeypatch):
    _windows(monkeypatch)
    assert system.local_label() == "This PC"
    assert "File Explorer" in system.mount_hint()
    monkeypatch.setattr(system, "IS_WINDOWS", False)
    monkeypatch.setattr(system, "IS_MAC", True)
    assert system.local_label() == "This Mac"
    assert "Finder" in system.mount_hint()
