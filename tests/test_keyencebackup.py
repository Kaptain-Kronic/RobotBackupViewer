"""Keyence CV-X FTP backup - exercised end to end against a FakeFTP that models the
REAL CV-X482D quirks captured live (see keyence-helper/CVX_FTP_LAYOUT.md):

  - anonymous login (empty user/pass), landing on the SD card at /SD1
  - `cv-x/setting/` holds the config; `cv-x/box/` holds big saved-set blobs
  - a pathful `RETR cv-x/setting/env.dat` is REFUSED ("550 Bad path") - the client
    must CWD into a directory and RETR a bare basename

That last quirk is the whole reason KeyenceBackupJob positions CWD per directory
instead of RETRing full relpaths the way the (Linux) Matrox job does, so the fake
enforces it: a pathful RETR raises, which would fail the backup if we regressed."""
import ftplib
from pathlib import Path

from backupviewer import keyencebackup, library, settings
from backupviewer.session import BackupSession

HOME = {
    "cv-x/setting/env.dat": "env blob",
    "cv-x/setting/3D_RBT_G_CLB_055.dat": "calib blob",
    "cv-x/setting/master.bmp": "bmp bytes",
    "cv-x/setting/recovery/env_bak.dat": "recovery blob",
    "cv-x/setting/001/RBT_G_RMD_000.dat": "program blob",
    "cv-x/setting/001/LYT_G_055.tbd": "layout blob",
    "cv-x/box/BOX_SD1_001_T100/TBM_L.tbd": "huge saved-set blob",
    "cv-x/temp/scratch.tmp": "scratch",
}
SETTING_FILES = [k for k in HOME if k.startswith("cv-x/setting/")]
BOX_FILES = [k for k in HOME if k.startswith("cv-x/box/")]


def _make_cvx(tmp_path) -> Path:
    home = tmp_path / "sd1"
    for rel, body in HOME.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return home


class FakeCVX:
    """ftplib.FTP stand-in for a Keyence CV-X482D, backed by a real temp tree."""

    def __init__(self, source, timeout=None):
        self._root = Path(source)
        self._cwd = self._root

    def connect(self, host, port=21):
        self.host = host

    def login(self, user="", passwd=""):
        if user not in ("", "anonymous"):
            raise ftplib.error_perm("530 Login incorrect.")
        self.user = user

    def set_pasv(self, flag):
        self.pasv = flag

    def getwelcome(self):
        return "220 CV-X482D (6.0.0000) FTP server ready."

    def pwd(self):
        rel = self._cwd.relative_to(self._root).as_posix()
        return "/SD1" if rel == "." else "/SD1/" + rel

    def cwd(self, path):
        if path in ("/", "", "/SD1", "/SD1/"):
            self._cwd = self._root
            return "250 ok"
        if path == "..":
            self._cwd = self._cwd.parent if self._cwd != self._root else self._root
            return "250 ok"
        if "/" in path.strip("/"):
            # the real CV-X refuses multi-segment paths
            raise ftplib.error_perm("550 Bad path")
        target = (self._cwd / path).resolve()
        if target.is_dir():
            self._cwd = target
            return "250 ok"
        raise ftplib.error_perm("550 Unable to find " + path)

    def nlst(self, *args):
        return [p.name for p in sorted(self._cwd.iterdir())]

    def retrbinary(self, cmd, callback, blocksize=8192):
        name = cmd.split(" ", 1)[1]
        if "/" in name:
            raise ftplib.error_perm("550 Bad path")   # pathful RETR refused, like the real unit
        f = self._cwd / name
        if not f.is_file():
            raise ftplib.error_perm("550 no such file: " + name)
        with open(f, "rb") as fh:
            while True:
                chunk = fh.read(blocksize)
                if not chunk:
                    break
                callback(chunk)
        return "226 transfer complete"

    def quit(self):
        pass

    def close(self):
        pass


def _factory(source):
    def make(timeout=None):
        return FakeCVX(source, timeout=timeout)
    return make


def _iso_lib(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


def test_probe_keyence(tmp_path):
    home = _make_cvx(tmp_path)
    res = keyencebackup.probe_keyence("10.0.0.55", ftp_factory=_factory(home))
    assert res["reachable"] is True
    assert res["has_cvx"] is True and res["has_setting"] is True
    assert "CV-X482D" in res["banner"]
    assert res["home"] == "/SD1"
    assert res["error"] == ""


def test_diagnose_keyence(tmp_path):
    home = _make_cvx(tmp_path)
    res = keyencebackup.diagnose_keyence("10.0.0.55", ftp_factory=_factory(home))
    assert "cv-x" in res["home_list"]
    assert sorted(res["cvx_list"]) == ["box", "setting", "temp"]
    assert "env.dat" in res["setting_list"]
    assert res["error"] == ""


def test_enumerate_scope_setting_only(tmp_path):
    """Default scope is the whole cv-x/setting tree - box (huge) and temp are left
    on the camera."""
    home = _make_cvx(tmp_path)
    ftp = FakeCVX(home)
    rels = keyencebackup.keyence_enumerate(ftp, "/SD1")
    assert set(rels) == set(SETTING_FILES), sorted(rels)
    assert ftp.pwd() == "/SD1"          # CWD restored for the download pass


def test_enumerate_with_box(tmp_path):
    home = _make_cvx(tmp_path)
    ftp = FakeCVX(home)
    rels = keyencebackup.keyence_enumerate(ftp, "/SD1", include_box=True)
    assert set(rels) == set(SETTING_FILES) | set(BOX_FILES)


def test_backup_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_cvx(tmp_path)
    dest = tmp_path / "Backups"

    registered = {}

    def on_complete(job):
        registered["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""))

    job = keyencebackup.KeyenceBackupJob(
        "10.0.0.55", dest, "FAKEPLANT", "RBB01", "RB172-CVX",
        note="cv-x settings pull", ftp_factory=_factory(home), throttle=0,
        on_complete=on_complete)
    res = job.run()

    assert res["status"] == "done", res
    assert res["done"] == len(SETTING_FILES)
    assert res["bytes"] > 0
    assert res["device_type"] == "camera-keyence"

    dated = Path(res["dated_path"])
    cam = dated / "CAM1"
    for rel in SETTING_FILES:
        assert cam.joinpath(*rel.split("/")).is_file(), rel
    for rel in BOX_FILES + ["cv-x/temp/scratch.tmp"]:
        assert not cam.joinpath(*rel.split("/")).exists(), rel
    assert not list(dated.rglob("*.part"))

    import json
    md = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert md["type"] == keyencebackup.BACKUP_TYPE
    assert md["device_type"] == "camera-keyence"

    latest = Path(res["latest_path"])
    assert latest.joinpath("CAM1", "cv-x", "setting", "env.dat").is_file()

    e = library.list_robots()["robots"][0]
    assert e.get("device_type") == "camera-keyence"
    assert "10.0.0.55" in e.get("ips", [])
    assert registered["entry"]["id"] == e["id"]

    # a pulled CV-X backup re-opens and is recognised as a keyence camera
    m = BackupSession(latest).manifest()
    assert m["backup_type"] == "keyence camera"
    assert m["tabs"]["files"] is True
    assert m["tabs"]["photos"] is False       # no SavedImages triples on a CV-X


def test_multi_camera_layout(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_cvx(tmp_path)
    job = keyencebackup.KeyenceBackupJob(
        "10.0.0.55", tmp_path / "b", "P", "L", "CELL",
        cameras=[{"label": "CAM1", "host": "10.0.0.55"},
                 {"label": "CAM2", "host": "10.0.0.56"}],
        ftp_factory=_factory(home), throttle=0)
    res = job.run()
    assert res["status"] == "done", res
    assert res["done"] == 2 * len(SETTING_FILES)
    dated = Path(res["dated_path"])
    assert dated.joinpath("CAM1", "cv-x", "setting", "env.dat").is_file()
    assert dated.joinpath("CAM2", "cv-x", "setting", "env.dat").is_file()


def test_backup_cancel(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    home = _make_cvx(tmp_path)
    job = keyencebackup.KeyenceBackupJob("10.0.0.55", tmp_path / "out", "P", "L", "C",
                                         ftp_factory=_factory(home), throttle=0)
    job.cancel()
    res = job.run()
    assert res["status"] == "cancelled"
    assert library.list_robots()["robots"] == []


def test_fanuc_job_refuses_cvx(monkeypatch, tmp_path):
    """A FANUC BackupJob pointed at a CV-X refuses loudly rather than pulling junk
    (the CV-X accepts the anonymous login, so nothing else would stop it)."""
    from backupviewer import ftpbackup
    _iso_lib(monkeypatch, tmp_path)
    home = _make_cvx(tmp_path)
    job = ftpbackup.BackupJob("10.0.0.55", tmp_path / "out", "P", "L", "CVX-AS-ROBOT",
                              ftp_factory=_factory(home), throttle=0)
    res = job.run()
    assert res["status"] == "error", res
    assert "keyence" in res["error"].lower()
    assert "device type" in res["error"].lower()
