from photosort import nas


def test_safe_host_rejects_credentials():
    assert nas._safe_host("apollo.local") == "apollo.local"
    assert nas._safe_host("smb://apollo") == "apollo"
    assert nas._safe_host("smb://user:secret@apollo/share") == "apollo"
    assert nas._safe_host("apollo/photos_share") is None
    assert nas._safe_host("") is None


def test_safe_share_skips_local_disk():
    assert nas._safe_share("photos_share") == "photos_share"
    assert nas._safe_share("Macintosh HD") is None
    assert nas._safe_share("share;rm") is None


def test_preferred_host_uses_bonjour_and_finder(monkeypatch):
    monkeypatch.setattr(nas, "discover_smb_hosts", lambda timeout=1.6: ["apollo"])
    monkeypatch.setattr(nas, "finder_last_smb_host", lambda: "apollo")
    monkeypatch.setattr(nas, "netauth_shares", lambda: {})
    assert nas.preferred_host() == "apollo.local"


def test_known_shares_merges_catalog_and_netauth(monkeypatch):
    monkeypatch.setattr(nas, "remembered_volume_roots", lambda: ["/Volumes/photos_share"])
    monkeypatch.setattr(nas, "netauth_shares", lambda: {"apollo": ["photos_share", "shared_docs"]})
    monkeypatch.setattr(nas, "finder_recent_shares", lambda: ["media_music"])
    monkeypatch.setattr(nas, "host_aliases", lambda host: {str(host or "").lower().removesuffix(".local"), "apollo"})
    names = nas.known_shares("apollo.local", recent=False)
    assert names == ["photos_share", "shared_docs"]
    names = nas.known_shares("apollo.local", recent=True)
    assert "media_music" in names


def test_mount_share_skips_osascript_when_already_up(monkeypatch, tmp_path):
    vol = tmp_path / "Volumes" / "photos_share"
    vol.mkdir(parents=True)
    monkeypatch.setattr(nas, "_volume_path", lambda share: tmp_path / "Volumes" / share)
    called = []
    monkeypatch.setattr(nas.subprocess, "run", lambda *a, **k: called.append(a))
    result = nas.mount_share("apollo.local", "photos_share")
    assert result["ok"] is True
    assert result["mounted"] is True
    assert called == []


def test_mount_share_uses_finder_url(monkeypatch, tmp_path):
    vols = tmp_path / "Volumes"
    vols.mkdir()

    class Proc:
        returncode = 0
        stdout = "file photos_share:"
        stderr = ""

    def run(cmd, **kwargs):
        assert cmd[0] == "osascript"
        assert "smb://apollo.local/photos_share" in cmd[-1]
        (vols / "photos_share").mkdir()
        return Proc()

    monkeypatch.setattr(nas, "_volume_path", lambda share: vols / share)
    monkeypatch.setattr(nas.subprocess, "run", run)
    result = nas.mount_share("apollo.local", "photos_share")
    assert result["ok"] is True
    assert result["mounted"] is True


def test_mount_known_without_host(monkeypatch):
    monkeypatch.setattr(nas, "preferred_host", lambda: None)
    result = nas.mount_known()
    assert result["ok"] is False
    assert result["items"] == []


def test_mount_known_without_share_names(monkeypatch):
    monkeypatch.setattr(nas, "preferred_host", lambda: "fileserver.local")
    monkeypatch.setattr(nas, "known_shares", lambda host, recent=False: [])
    result = nas.mount_known()
    assert result["ok"] is False
    assert result["items"] == []
    assert "share" in result["error"].lower()


def test_shares_for_paths_reads_volume_name():
    names = nas.shares_for_paths(
        [
            "/Volumes/media_shared_photos/Photo_Collection/2006 - Vienna",
            "/Volumes/media_shared_photos/other",
            "/Users/someone/Pictures",
        ]
    )
    assert names == ["media_shared_photos"]


def test_mount_for_paths_skips_already_mounted(monkeypatch, tmp_path):
    vol = tmp_path / "Volumes" / "photos_share"
    vol.mkdir(parents=True)
    monkeypatch.setattr(nas, "_volume_path", lambda share: tmp_path / "Volumes" / share)
    called = []
    monkeypatch.setattr(nas, "mount_share", lambda *a, **k: called.append(a))
    result = nas.mount_for_paths([vol / "1995 - Coast"])
    assert result["ok"] is True
    assert called == []
