"""FTP backup engine - exercised end to end against a FakeFTP so the whole
pull -> disk-layout -> library -> re-open chain is verified with NO real
controller. Live-controller testing is never the first validation."""
import ftplib
import json
from pathlib import Path

from backupviewer import ftpbackup, library, settings
from backupviewer.session import BackupSession

# a minimal but real-shaped MD: device the fake controller serves
SUMMARY = (
    "SUMMARY.DG                                  \r\n"
    "Robot Software Version\r\n"
    " F Number: F123456\r\n"
    " Robot Model: R-2000iC/165F\r\n"
)
FILES = {
    "SUMMARY.DG": SUMMARY,
    "SYSTEM.VA": "[SYSTEM]\r\n$WORD = 1\r\n",
    "NUMREG.VA": "[*NUMREG*]\r\n[1] = 10\r\n",
    "CONFIG.DG": "config dump\r\n",
    "BACKDATE.IMG": "BINARY IMAGE - must be skipped",  # image artifact
}


def _make_controller(tmp_path) -> Path:
    src = tmp_path / "ctrl_md"
    src.mkdir()
    for name, body in FILES.items():
        (src / name).write_text(body, encoding="cp1252")
    return src


class FakeFTP:
    """ftplib.FTP stand-in modelling a FANUC controller with a flat MD: device.
    Implements only the surface ftpbackup.BackupJob/probe_controller actually use."""

    def __init__(self, source, timeout=None):
        self._source = Path(source)
        self._cwd = "/"

    def connect(self, host, port=21):
        self.host = host

    def login(self, user="", passwd=""):
        self.user = user

    def set_pasv(self, flag):
        self.pasv = flag

    def getwelcome(self):
        return "220 FANUC Robot FTP server ready"

    def cwd(self, path):
        if path in ("/", ""):
            self._cwd = "/"
            return "250 ok"
        if path.rstrip("/").upper() == "MD:" and self._source.is_dir():
            self._cwd = "MD:"
            return "250 ok"
        raise ftplib.error_perm("550 no such device: " + path)

    def nlst(self, *args):
        if self._cwd != "MD:":
            return []
        return [p.name for p in sorted(self._source.iterdir()) if p.is_file()]

    def retrbinary(self, cmd, callback, blocksize=8192):
        name = cmd.split(" ", 1)[1]
        f = self._source / name
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
        return FakeFTP(source, timeout=timeout)
    return make


def _iso_lib(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


def test_probe_controller(tmp_path):
    src = _make_controller(tmp_path)
    res = ftpbackup.probe_controller("10.0.0.5", ftp_factory=_factory(src))
    assert res["reachable"] is True
    assert res["has_md"] is True
    assert res["has_fr"] is False
    assert "FANUC" in res["banner"]
    assert res["error"] == ""


def test_probe_unreachable():
    def boom(timeout=None):
        class Dead:
            def connect(self, *a, **k):
                raise OSError("no route to host")
        return Dead()
    res = ftpbackup.probe_controller("10.0.0.9", ftp_factory=boom)
    assert res["reachable"] is False
    assert res["error"]


def test_backup_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    dest = tmp_path / "RobotBackups"

    registered = {}

    def on_complete(job):
        registered["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""),
        )

    job = ftpbackup.BackupJob(
        "10.0.0.5", dest, "PLANT1", "BODY-1", "DEMOBOT01",
        note="post-PM verification", ftp_factory=_factory(src), throttle=0,
        on_complete=on_complete,
    )
    res = job.run()

    assert res["status"] == "done", res
    pulled = [n for n in FILES if not n.endswith(".IMG")]
    assert res["done"] == len(pulled)
    assert res["bytes"] > 0
    assert "BACKDATE.IMG" in res["skipped"]

    # dated snapshot: PLANT/LINE/ROBOT/date/time, with files + sidecars, no leftovers
    dated = Path(res["dated_path"])
    assert dated.is_dir()
    assert dated.parts[-6:][0] == "PLANT1" or "PLANT1" in dated.parts
    assert "BODY-1" in dated.parts and "DEMOBOT01" in dated.parts
    for name in pulled:
        assert (dated / name).is_file()
    assert not (dated / "BACKDATE.IMG").exists()
    assert not list(dated.glob("*.part"))
    assert (dated / "notes.txt").read_text(encoding="utf-8").startswith("post-PM verification")
    assert (dated / "backup.json").is_file()

    # a file we could not pull is recorded IN the backup, not just in the live
    # snapshot the UI throws away when the job strip closes. backup.json is the
    # honesty record; a lossy pull must never read as a whole one later.
    meta = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert "BACKDATE.IMG" in meta["skipped"]

    # Latest mirror: <...>/Latest/<robot>, full copy of the dated snapshot
    latest = Path(res["latest_path"])
    assert latest.is_dir()
    assert latest.parts[-2:] == ("Latest", "DEMOBOT01")
    for name in pulled:
        assert (latest / name).is_file()

    # library got a registered backup for this robot
    data = library.list_robots()
    assert len(data["robots"]) == 1
    e = data["robots"][0]
    assert e["robot"] == "DEMOBOT01" and e["line"] == "BODY-1" and e["plant"] == "PLANT1"
    assert len(e["backups"]) == 1
    assert e["latest_path"] == str(latest)
    assert registered["entry"]["id"] == e["id"]

    # the pulled backup re-opens as a real session (F-number proves SUMMARY.DG landed)
    m = BackupSession(latest).manifest()
    assert m["f_number"] == "F123456"
    assert m["file_count"] >= len(pulled)


def test_backup_cancel(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    job = ftpbackup.BackupJob("10.0.0.5", tmp_path / "out", "P", "L", "R",
                         ftp_factory=_factory(src), throttle=0)
    job.cancel()
    res = job.run()
    assert res["status"] == "cancelled"
    assert library.list_robots()["robots"] == []  # nothing registered on cancel


def test_backup_completion_marker(monkeypatch, tmp_path):
    """backup.json is a started-marker (complete:false) from the moment the
    dated dir exists, flipped true only as the LAST step of a successful pull -
    a pull that dies mid-download is then self-identifying on disk."""
    import json

    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)

    ok = ftpbackup.BackupJob("10.0.0.5", tmp_path / "out", "P", "L", "R1",
                             ftp_factory=_factory(src), throttle=0).run()
    assert ok["status"] == "done"
    meta = json.loads((Path(ok["dated_path"]) / "backup.json").read_text(encoding="utf-8"))
    assert meta["complete"] is True
    assert meta["files"] == ok["done"] and meta["bytes"] == ok["bytes"]

    class DyingFTP(FakeFTP):
        """The connection drops mid-pull: file 1 lands whole, file 2 dies."""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._calls = 0

        def retrbinary(self, cmd, callback, blocksize=8192):
            self._calls += 1
            if self._calls > 1:                    # retries die too
                raise ftplib.error_temp("426 connection closed; transfer aborted")
            return super().retrbinary(cmd, callback, blocksize)

    bad = ftpbackup.BackupJob("10.0.0.5", tmp_path / "out", "P", "L", "R2",
                              ftp_factory=lambda timeout=None: DyingFTP(src, timeout=timeout),
                              throttle=0).run()
    assert bad["status"] == "error"
    dated = Path(bad["dated_path"])
    assert dated.is_dir()                          # the partial folder exists...
    meta = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert meta["complete"] is False               # ...and says it never finished
    assert not (dated / "notes.txt").exists()      # the success sidecars never ran
    assert bad["latest_path"] == ""                # and no Latest mirror was made


def test_terminal_vocabulary_and_run_id_in_snapshot(tmp_path):
    assert all(ftpbackup.is_terminal(s) for s in ("done", "error", "cancelled"))
    assert not any(ftpbackup.is_terminal(s)
                   for s in ("pending", "connecting", "listing", "downloading", ""))
    job = ftpbackup.BackupJob("192.0.2.9", tmp_path, "", "L1", "R1", run_id="run-x",
                              ftp_factory=lambda timeout=None: FakeFTP(tmp_path))
    assert job.snapshot()["run_id"] == "run-x"


class _StubJob:
    """snapshot()-only stand-in for registry-level Api tests."""

    def __init__(self, status, run_id):
        self._snap = {"status": status, "run_id": run_id}

    def snapshot(self):
        return dict(self._snap)


def test_api_joins_the_in_flight_run():
    """A backup started while another run is live must reuse that run's id -
    a mid-run retry lands in the same "last run" report instead of pushing a
    new run on top of one that hasn't finished (api._active_run_id)."""
    from backupviewer.api import Api

    api = Api()
    assert api._active_run_id() == ""
    api._jobs["a"] = _StubJob("done", "run-old")
    assert api._active_run_id() == ""              # settled jobs hold no run open
    api._jobs["b"] = _StubJob("downloading", "run-live")
    assert api._active_run_id() == "run-live"
    api._jobs["b"] = _StubJob("cancelled", "run-live")
    assert api._active_run_id() == ""
