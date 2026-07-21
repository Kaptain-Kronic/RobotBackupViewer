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
    "da/Projects/SAMPLEPROJ/Settings/SAMPLEPROJ": "recipe blob\n",
    "da/Projects/SAMPLEPROJ/Persistent/uuid-1234": "persist\n",
    "da/DCFs/cam.dcf": "dcf\n",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.jpg": "old jpg",
    "Documents/Matrox Design Assistant/SavedImages/2026-06-25/OLD-Pass.txt": "Overall Pass or Fail: Pass\n",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.jpg": "new jpg small",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.png": "new png bigger",
    "Documents/Matrox Design Assistant/SavedImages/2026-07-07/NEW-Fail.txt":
        "Camera\nCamera Name: CELL-01RB172-R01CAM02\nCamera Type: Matrox GTX2000\n"
        "IP Address: 10.0.0.7\n\nInspection\nOverall Pass or Fail: Fail\n",
}

PULLED = [
    "da/AgentSettings/agent.xml",
    "da/Projects/SAMPLEPROJ/Settings/SAMPLEPROJ",
    "da/Projects/SAMPLEPROJ/Persistent/uuid-1234",
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
    assert ident["name"] == "CELL-01RB172-R01CAM02"     # from the newest sidecar
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
    dest = deep / "CELL-01RB172-R01CAM02-402-0-Fail-2026_07_16-verylongfilename.png"
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
    assert md["complete"] is True                 # flipped true only on success

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


def test_partial_backup_left_marked_incomplete(tmp_path):
    """A camera pull that lands no files leaves its dated folder marked
    complete:false, so the library rescan never adopts the partial as latest -
    the same started-marker guarantee the FANUC job makes (ftpbackup)."""
    empty = tmp_path / "empty_share"
    empty.mkdir()
    job = mtxbackup.CameraBackupJob("10.0.0.9", tmp_path / "lib", "P", "L", "STATION",
                                    mount=_mount_factory(empty), throttle=0)
    res = job.run()
    assert res["status"] == "error"
    dated = Path(res["dated_path"])
    md = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert md["complete"] is False
    assert not (dated / "notes.txt").exists()     # success sidecars never ran


def test_run_id_in_snapshot(tmp_path):
    """A camera job carries its run_id in the snapshot so an in-flight pull holds
    a run open for join/retry-fold (api._active_run_id)."""
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "o", "P", "L", "R",
                                    run_id="run-cam", mount=_mount_factory(tmp_path))
    assert job.snapshot()["run_id"] == "run-cam"


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


def test_name_from_backup(monkeypatch, tmp_path):
    """A pulled snapshot teaches the camera's real name offline (newest sidecar)."""
    _iso_lib(monkeypatch, tmp_path)
    home = _make_camera(tmp_path)
    job = mtxbackup.CameraBackupJob("10.0.0.7", tmp_path / "out", "P", "L", "CAM",
                                    mount=_mount_factory(home), throttle=0)
    res = job.run()
    assert res["status"] == "done", res
    ident = mtxbackup.name_from_backup(res["dated_path"])
    assert ident == {"name": "CELL-01RB172-R01CAM02", "model": "Matrox GTX2000"}


def test_name_from_backup_blank_on_nothing(tmp_path):
    """No sidecars / no folder -> blanks, never an exception."""
    (tmp_path / "CAM1" / "da").mkdir(parents=True)
    assert mtxbackup.name_from_backup(tmp_path) == {"name": "", "model": ""}
    assert mtxbackup.name_from_backup("") == {"name": "", "model": ""}
    assert mtxbackup.name_from_backup(tmp_path / "nope") == {"name": "", "model": ""}


def test_camera_self_names_and_links_after_first_backup(monkeypatch, tmp_path):
    """The api register flow end-to-end: a placeholder(IP)-named camera renames
    itself from the snapshot it just pulled (old name kept as an alias, model
    filled) and auto-links to its robot - the user story 'first backup teaches
    the camera who it is'."""
    _iso_lib(monkeypatch, tmp_path)
    # the dest root IS the library root (api._start_backup_job persists it as
    # such before every job starts) - teach renames the camera folder under it
    lib_root = tmp_path / "MTXBackups"
    monkeypatch.setattr(settings, "library_root", lambda: str(lib_root))
    home = _make_camera(tmp_path)
    robot = library.add_robot({"robot": "RB172R01B01", "plant": "FAKEPLANT",
                               "line": "RBB01", "device_type": "robot"})
    cam = library.add_robot({"robot": "10.0.0.7", "plant": "FAKEPLANT",
                             "line": "RBB01", "device_type": "camera-mtx",
                             "ips": ["10.0.0.7"]})

    def register(job):       # exactly what api._start_backup_job's on_complete does
        entry = library.register_backup(job.library_match(), job.library_backup(),
                                        latest_path=job.snapshot().get("latest_path", ""))
        ident = mtxbackup.name_from_backup(job.snapshot().get("dated_path", ""))
        if ident.get("name"):
            library.teach_camera_name(entry["id"], ident["name"], ident.get("model", ""))
        library.auto_link_cameras()

    job = mtxbackup.CameraBackupJob("10.0.0.7", lib_root, "FAKEPLANT",
                                    "RBB01", "10.0.0.7", mount=_mount_factory(home),
                                    throttle=0, on_complete=register)
    assert job.run()["status"] == "done"

    e = next(x for x in library.list_robots()["robots"] if x["id"] == cam["id"])
    assert e["robot"] == "CELL-01RB172-R01CAM02"            # self-named
    assert e["model"] == "Matrox GTX2000"
    assert {"plant": "FAKEPLANT", "line": "RBB01", "robot": "10.0.0.7"} in e["aliases"]
    assert e["linked_robot_id"] == robot["id"]             # and auto-linked

    # the teach renamed the camera's FOLDER with it - so the first library
    # rescan (a finished backup always triggers one) re-derives the same name
    # instead of reverting it to the IP. The revert was the original bug: the
    # name held only until the next scan, which believed the folder.
    assert (lib_root / "FAKEPLANT" / "RBB01" / "CELL-01RB172-R01CAM02").is_dir()
    library.scan_library_root(lib_root)
    e2 = next(x for x in library.list_robots()["robots"] if x["id"] == cam["id"])
    assert e2["robot"] == "CELL-01RB172-R01CAM02"          # STILL self-named
    assert e2["linked_robot_id"] == robot["id"]


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
