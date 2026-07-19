"""Matrox camera SMB backup - exercised against a local temp 'camera home' via an
injected mount. In production the SMB share is just a UNC filesystem path once
authenticated, so a local dir is a faithful stand-in and the whole
enumerate -> copy -> library chain is verified with NO real camera. Live testing
is never the first validation."""
import ftplib
import json
from pathlib import Path

from backupviewer import ftpbackup, mtxbackup, library, settings
from backupviewer.session import BackupSession

# a minimal but real-shaped camera home. da/ is the config tree; SavedImages has
# an OLDER and a NEWER date - only the newer must be pulled. Dotfiles + other home
# dirs must be ignored entirely.
HOME = {
    ".bashrc": "export PS1='cam'\n",
    "autost.sh": "#!/bin/sh\n",
    "Downloads/junk.bin": "should not be pulled",
    "da/AgentSettings/agent.xml": "<agent/>\n",
    "da/Projects/GMDAX/Settings/GMDAX": "recipe blob\n",
    "da/Projects/GMDAX/Persistent/uuid-1234": "persist\n",
    "da/DCFs/cam.dcf": "dcf\n",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.jpg": "old jpg",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.txt": "Overall Pass or Fail: Pass\n",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.jpg": "new jpg small",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.png": "new png bigger",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.txt":
        "Camera\nCamera Name: FAB-01RB172-R01CAM02\nCamera Type: Matrox GTX2000\n"
        "IP Address: 10.0.0.7\n\nInspection\nOverall Pass or Fail: Fail\n",
}

PULLED = [
    "da/AgentSettings/agent.xml",
    "da/Projects/GMDAX/Settings/GMDAX",
    "da/Projects/GMDAX/Persistent/uuid-1234",
    "da/DCFs/cam.dcf",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.jpg",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.png",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.txt",
]
IGNORED = [
    ".bashrc", "autost.sh", "Downloads/junk.bin",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.jpg",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.txt",
]


def _make_camera(tmp_path) -> Path:
    home = tmp_path / "cam_home"
    for rel, body in HOME.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return home


def _mount_factory(home):
    """A mount() stand-in: pretend to authenticate the SMB share and hand back the
    local home dir (+ a no-op cleanup). Ignores host so multi-camera tests reuse it."""
    def mount(host, user, passwd):
        return home, lambda: None
    return mount


def _iso_lib(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


def test_probe_camera(tmp_path):
    home = _make_camera(tmp_path)
    res = mtxbackup.probe_camera("10.0.0.7", mount=_mount_factory(home))
    assert res["reachable"] is True
    assert res["has_da"] is True
    assert res["has_images"] is True
    assert res["error"] == ""


def test_probe_camera_unreachable():
    def dead_mount(host, user, passwd):
        raise OSError("SMB connect failed (WinError 53)")
    res = mtxbackup.probe_camera("10.0.0.9", mount=dead_mount)
    assert res["reachable"] is False
    assert res["error"]


def test_diagnose_camera(tmp_path):
    home = _make_camera(tmp_path)
    res = mtxbackup.diagnose_camera("10.0.0.7", mount=_mount_factory(home))
    assert res["newest_date"] == "2026-07-07"
    assert "2026-06-25" in res["image_dates"] and "2026-07-07" in res["image_dates"]
    assert "da" in res["home_list"]
    assert res["error"] == ""


def test_resolve_camera_name(tmp_path):
    home = _make_camera(tmp_path)
    ident = mtxbackup.resolve_camera_name("10.0.0.7", mount=_mount_factory(home))
    assert ident["name"] == "FAB-01RB172-R01CAM02"     # from the newest sidecar
    assert ident["model"] == "Matrox GTX2000"


def test_enumerate_scope(tmp_path):
    """Enumerate returns all of da/ + only the newest SavedImages date."""
    home = _make_camera(tmp_path)
    rels = {p.relative_to(home).as_posix() for p in mtxbackup._enumerate_files(home)}
    assert rels == set(PULLED), sorted(rels)


def test_copy_over_max_path(tmp_path):
    r"""A destination past the 260-char Windows MAX_PATH still copies (the
    halfway-through 'cannot find the path' field failure) via the \\?\ prefix."""
    import os
    from backupviewer import ftpbackup
    src = tmp_path / "src.png"
    src.write_bytes(b"x" * 512)
    deep = tmp_path
    for i in range(6):
        deep = deep / ("Matrox Design Assistant SavedImages segment %02d" % i)
    dest = deep / "FAB-01RB172-R01CAM02-402-0-Fail-2026_07_16-verylongfilename.png"
    assert len(str(dest)) > 260, len(str(dest))
    n = mtxbackup._copy_file(src, dest)
    assert n == 512
    assert os.path.exists(ftpbackup.long_path(dest))
    assert not os.path.exists(ftpbackup.long_path(dest.with_name(dest.name + ".part")))


def test_backup_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    dest = tmp_path / "MTXBackups"

    registered = {}

    def on_complete(job):
        registered["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""),
        )

    job = mtxbackup.CameraBackupJob(
        "10.0.0.7", dest, "FAKEPLANT", "RBB01", "RB172R01",
        note="post-PM camera pull", mount=_mount_factory(home), throttle=0,
        on_complete=on_complete,
    )
    res = job.run()

    assert res["status"] == "done", res
    assert res["done"] == len(PULLED)
    assert res["bytes"] > 0
    assert res["device_type"] == "camera-mtx"

    dated = Path(res["dated_path"])
    assert dated.is_dir()
    assert "RBB01" in dated.parts and "RB172R01" in dated.parts
    cam = dated / "CAM1"
    for rel in PULLED:
        assert cam.joinpath(*rel.split("/")).is_file(), rel
    for rel in IGNORED:
        assert not cam.joinpath(*rel.split("/")).exists(), rel
    assert not list(dated.rglob("*.part"))
    assert (dated / "notes.txt").read_text(encoding="utf-8").startswith("post-PM camera pull")
    md = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert md["type"] == mtxbackup.BACKUP_TYPE and md["device_type"] == "camera-mtx"
    assert md["source"] == "smb"

    latest = Path(res["latest_path"])
    assert latest.is_dir()
    assert latest.parts[-2:] == ("Latest", "RB172R01")
    assert latest.joinpath("CAM1", "da", "DCFs", "cam.dcf").is_file()

    data = library.list_robots()
    assert len(data["robots"]) == 1
    e = data["robots"][0]
    assert e["robot"] == "RB172R01" and e["line"] == "RBB01" and e["plant"] == "FAKEPLANT"
    assert e.get("device_type") == "camera-mtx"
    assert "10.0.0.7" in e.get("ips", [])
    assert len(e["backups"]) == 1
    assert registered["entry"]["id"] == e["id"]

    m = BackupSession(latest).manifest()
    assert m["file_count"] >= len(PULLED)


def test_multi_camera_layout(monkeypatch, tmp_path):
    """Two cameras on one station land in CAM1/ and CAM2/ of one snapshot."""
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    dest = tmp_path / "MTXBackups"
    job = mtxbackup.CameraBackupJob(
        "10.0.0.7", dest, "FAKEPLANT", "RBB01", "RB172R01",
        cameras=[{"label": "CAM1", "host": "10.0.0.7"},
                 {"label": "CAM2", "host": "10.0.0.8"}],
        mount=_mount_factory(home), throttle=0,
    )
    res = job.run()
    assert res["status"] == "done", res
    assert res["done"] == 2 * len(PULLED)
    dated = Path(res["dated_path"])
    assert dated.joinpath("CAM1", "da", "DCFs", "cam.dcf").is_file()
    assert dated.joinpath("CAM2", "da", "DCFs", "cam.dcf").is_file()


def test_backup_cancel(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "out", "P", "L", "R",
                                    mount=_mount_factory(home), throttle=0)
    job.cancel()
    res = job.run()
    assert res["status"] == "cancelled"
    assert library.list_robots()["robots"] == []  # nothing registered on cancel


# -- the FANUC guard still refuses an FTP host that looks like a camera ----------

class _CameraFTP:
    """A tiny FTP stand-in whose root looks like a Matrox home (da/ + Documents/):
    a FANUC BackupJob pointed here must refuse rather than pull junk."""

    def __init__(self, timeout=None):
        self._cwd = "/"

    def connect(self, host, port=21):
        self.host = host

    def login(self, user="", passwd=""):
        pass

    def set_pasv(self, flag):
        pass

    def getwelcome(self):
        return "220 ready"

    def cwd(self, path):
        if path in ("/", ""):
            self._cwd = "/"
            return "250 ok"
        raise ftplib.error_perm("550 no device: " + path)   # no MD:, roots at /

    def nlst(self, *args):
        return ["da", "Documents", "autost.sh"]

    def quit(self):
        pass

    def close(self):
        pass


def test_fanuc_job_refuses_matrox_ftp_host(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    job = ftpbackup.BackupJob("10.0.0.7", tmp_path / "out", "P", "L", "CAM-AS-ROBOT",
                              ftp_factory=lambda timeout=None: _CameraFTP(timeout), throttle=0)
    res = job.run()
    assert res["status"] == "error", res
    assert "matrox camera" in res["error"].lower()
    assert library.list_robots()["robots"] == []
