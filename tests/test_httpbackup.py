"""HTTP backup engine (Global-5 controllers) - exercised end to end against a
FakeHttp so the enumerate -> download -> disk-layout -> library -> re-open chain
is verified with NO real controller, plus the auto-detecting dispatcher's
FTP-vs-HTTP choice. Live-controller testing is never the first validation.

Identifiers are synthetic (TEST-NET 192.0.2.x, fake robot RB010R01B01)."""
import ftplib
import json
from pathlib import Path

from backupviewer import ftpbackup, httpbackup, library, settings
from backupviewer.session import BackupSession

# a minimal but real-shaped MD: device the fake web server serves. SYSVARS.SV is
# a synthesised system file the real index pages omit but /MD/<name> still serves
# - httpbackup pulls it from its SYSTEM_FILES list, so it is here on disk.
FILES = {
    "SUMMARY.DG": "SUMMARY.DG\r\n F Number: F000000\r\n Robot Model: FAKE/10\r\n",
    "PROG1.TP": "/PROG PROG1\r\n/MN\r\n  1:  J P[1] 100% FINE ;\r\n/END\r\n",
    "NUMREG.VR": "[*NUMREG*]\r\n[1] = 10\r\n",
    "SYSVARS.SV": "[SYSTEM]\r\n$WORD = 1\r\n",
    "BACKDATE.IMG": "BINARY IMAGE - must be skipped",  # image artifact
}


def _make_controller(tmp_path) -> Path:
    src = tmp_path / "ctrl_md"
    src.mkdir()
    for name, body in FILES.items():
        (src / name).write_text(body, encoding="cp1252")
    return src


class FakeHttp:
    """Stand-in for httpbackup._Http: serves the four INDEX_*.HTM as a generated
    listing of a temp MD: dir, and /MD/<name> from that dir. A missing name
    answers the controller's real 404 body shape."""

    def __init__(self, source, host=None, port=None, timeout=None):
        self.source = Path(source)

    def get(self, path):
        name = path.rsplit("/", 1)[-1].upper()
        if name in httpbackup.INDEX_PAGES:
            return 200, self._index()
        f = self.source / name
        if f.is_file():
            return 200, f.read_bytes()
        return 404, b"<HTML><BODY><P><H2>HTTP/1.1 404 File Not Found</H2></BODY></HTML>"

    def _index(self):
        rows = "".join(
            f'<a href="../MD/{p.name}">{p.name}</a><a href="../MD/{p.stem}.LS">ascii</a>\n'
            for p in sorted(self.source.iterdir()) if p.is_file())
        return ("<html><body>\n" + rows + "</body></html>").encode("latin1")

    def close(self):
        pass


def _opener(source):
    def make(host, port, timeout):
        return FakeHttp(source, host, port, timeout)
    return make


class _FlakyHttp(FakeHttp):
    """FakeHttp that raises a timeout on named files - the real embedded web
    server occasionally stalls a single GET past the timeout. Index pages still
    serve, so enumeration succeeds and only the download stalls."""

    def __init__(self, source, stall=(), host=None, port=None, timeout=None):
        super().__init__(source, host, port, timeout)
        self._stall = {s.upper() for s in stall}

    def get(self, path):
        if path.rsplit("/", 1)[-1].upper() in self._stall:
            raise TimeoutError("timed out")
        return super().get(path)


def _flaky_opener(source, stall):
    def make(host, port, timeout):
        return _FlakyHttp(source, stall=stall, host=host, port=port, timeout=timeout)
    return make


class _DeadAfterIndex(FakeHttp):
    """Index pages serve, then every file GET stalls - the connection died after
    enumeration. (A 404 is a LIVE response and must not count toward the
    circuit breaker; only transport errors do, so this stalls, never 404s.)"""

    def get(self, path):
        if path.rsplit("/", 1)[-1].upper() in httpbackup.INDEX_PAGES:
            return super().get(path)
        raise TimeoutError("timed out")


def _dead_opener(source):
    def make(host, port, timeout):
        return _DeadAfterIndex(source, host, port, timeout)
    return make


class _FtpRefusing:
    """A controller whose FTP login is refused - the Global-5 signature."""

    def __init__(self, timeout=None):
        pass

    def connect(self, host, port=21):
        pass

    def login(self, user="", passwd=""):
        raise ftplib.error_perm("530 Login incorrect")

    def quit(self):
        pass

    def close(self):
        pass


def _ftp_refuse_factory(timeout=None):
    return _FtpRefusing(timeout)


class _FtpOk:
    """Enough of a working FTP controller for probe_controller to call it a
    Global-4 (connect + login + cwd MD:)."""

    def __init__(self, timeout=None):
        pass

    def connect(self, host, port=21):
        pass

    def login(self, user="", passwd=""):
        pass

    def set_pasv(self, flag):
        pass

    def getwelcome(self):
        return "220 FANUC Robot FTP server ready"

    def cwd(self, path):
        if path.rstrip("/").upper() in ("", "/", "MD:"):
            return "250 ok"
        raise ftplib.error_perm("550 no such device")

    def quit(self):
        pass

    def close(self):
        pass


def _ftp_ok_factory(timeout=None):
    return _FtpOk(timeout)


def _iso_lib(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


# -- pure helpers ---------------------------------------------------------------

def test_index_filenames_dedup_and_skip_self():
    html = (
        '<a href="../MD/PROG1.TP">x</a>'
        '<a href="../MD/prog1.tp">dup, case-folded</a>'
        '<a href="../MD/INDEX_VR.HTM">a sibling index - excluded</a>'
        '<a href="/MD/NUMREG.VR">absolute path form</a>'
        '<a href="/softpart/genlink?">not an MD file</a>'
    )
    assert httpbackup.index_filenames(html) == ["PROG1.TP", "NUMREG.VR"]


def test_looks_like_error_body():
    assert httpbackup._looks_like_error_body(b"<HTML><BODY>404 File Not Found</BODY>")
    assert httpbackup._looks_like_error_body(b"   HTTP/1.1 404 File Access Error  ")
    assert not httpbackup._looks_like_error_body(b"\xff\xef\x00\x01real binary file")


# -- detection ------------------------------------------------------------------

def test_probe_robot_prefers_ftp(tmp_path):
    src = _make_controller(tmp_path)
    res = httpbackup.probe_robot("192.0.2.5", ftp_factory=_ftp_ok_factory,
                                 opener_factory=_opener(src))
    assert res["transport"] == "ftp"
    assert res["reachable"] is True
    assert res["has_md"] is True


def test_probe_robot_falls_back_to_http(tmp_path):
    src = _make_controller(tmp_path)
    res = httpbackup.probe_robot("192.0.2.6", ftp_factory=_ftp_refuse_factory,
                                 opener_factory=_opener(src))
    assert res["transport"] == "http"
    assert res["reachable"] is True
    assert res["has_md"] is True
    assert "HTTP" in res["banner"]


def test_probe_robot_neither(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = httpbackup.probe_robot("192.0.2.7", ftp_factory=_ftp_refuse_factory,
                                 opener_factory=_opener(empty))
    assert res["reachable"] is False
    assert res["transport"] == ""


# -- end to end -----------------------------------------------------------------

def test_http_backup_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    dest = tmp_path / "RobotBackups"
    registered = {}

    def on_complete(job):
        registered["entry"] = library.register_backup(
            job.library_match(), job.library_backup(),
            latest_path=job.snapshot().get("latest_path", ""),
        )

    job = httpbackup.HttpBackupJob(
        "192.0.2.5", dest, "PLANT1", "BODY-1", "RB010R01B01",
        note="global-5 web backup", opener_factory=_opener(src), throttle=0,
        on_complete=on_complete,
    )
    res = job.run()

    assert res["status"] == "done", res
    # SUMMARY.DG, PROG1.TP, NUMREG.VR (indexed) + SYSVARS.SV (system-file list);
    # BACKDATE.IMG is an image artifact -> skipped, never pulled.
    assert res["done"] == 4, res
    assert res["bytes"] > 0
    assert "BACKDATE.IMG" in res["skipped"]

    dated = Path(res["dated_path"])
    assert dated.is_dir()
    for name in ("SUMMARY.DG", "PROG1.TP", "NUMREG.VR", "SYSVARS.SV"):
        assert (dated / name).is_file()
    assert not (dated / "BACKDATE.IMG").exists()
    assert not any(p.name.endswith(".part") for p in dated.iterdir())

    meta = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert meta["complete"] is True          # the last-written marker of a good pull
    assert meta["source"] == "http"
    assert meta["files"] == 4

    # Latest mirror swapped in, and the snapshot re-opens as a real session.
    latest = Path(res["latest_path"])
    assert (latest / "SUMMARY.DG").is_file()
    sess = BackupSession(dated)
    assert sess.find("PROG1.TP")

    assert registered["entry"]["id"]


def test_http_backup_skips_a_stalled_file(monkeypatch, tmp_path):
    """One file timing out is a recorded skip, not a sunk 650-file backup - the
    failure mode a real R-50iA line actually produced."""
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    job = httpbackup.HttpBackupJob(
        "192.0.2.5", tmp_path / "RB", "P", "L", "RB010R01B01",
        opener_factory=_flaky_opener(src, stall=["PROG1.TP"]), throttle=0)
    res = job.run()
    assert res["status"] == "done", res
    assert "PROG1.TP" in res["skipped"]          # honestly recorded, not lost silently
    assert res["done"] == 3                       # SUMMARY.DG, NUMREG.VR, SYSVARS.SV
    dated = Path(res["dated_path"])
    meta = json.loads((dated / "backup.json").read_text(encoding="utf-8"))
    assert meta["complete"] is True
    assert "PROG1.TP" in meta["skipped"]


def test_http_backup_aborts_on_dead_connection(monkeypatch, tmp_path):
    """A run of stalls in a row means the connection is gone - abort honestly
    rather than skip-storm the rest at one timeout each and mark it complete."""
    _iso_lib(monkeypatch, tmp_path)
    monkeypatch.setattr(httpbackup, "CONSEC_FAIL_LIMIT", 2)
    src = _make_controller(tmp_path)
    job = httpbackup.HttpBackupJob(
        "192.0.2.5", tmp_path / "RB", "P", "L", "RB010R01B01",
        opener_factory=_dead_opener(src), throttle=0)
    res = job.run()
    assert res["status"] == "error"
    assert "connection lost" in res["error"]


def test_http_backup_cancel_leaves_incomplete(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    job = httpbackup.HttpBackupJob(
        "192.0.2.5", tmp_path / "RB", "P", "L", "RB010R01B01",
        opener_factory=_opener(src), throttle=0)
    job.cancel()
    res = job.run()
    assert res["status"] == "cancelled"


# -- dispatcher -----------------------------------------------------------------

def test_dispatcher_selects_http(tmp_path):
    src = _make_controller(tmp_path)
    job = httpbackup.RobotBackupJob(
        "192.0.2.6", tmp_path / "RB", "P", "L", "RB010R01B01",
        ftp_factory=_ftp_refuse_factory, opener_factory=_opener(src))
    inner = job._build()
    assert isinstance(inner, httpbackup.HttpBackupJob)
    assert inner.id == job.id          # the dispatcher's stable id carries through


def test_dispatcher_selects_ftp(tmp_path):
    src = _make_controller(tmp_path)
    job = httpbackup.RobotBackupJob(
        "192.0.2.5", tmp_path / "RB", "P", "L", "RB010R01B01",
        ftp_factory=_ftp_ok_factory, opener_factory=_opener(src))
    inner = job._build()
    assert isinstance(inner, ftpbackup.BackupJob)
    assert inner.id == job.id


def test_dispatcher_runs_http_end_to_end(monkeypatch, tmp_path):
    _iso_lib(monkeypatch, tmp_path)
    src = _make_controller(tmp_path)
    job = httpbackup.RobotBackupJob(
        "192.0.2.6", tmp_path / "RB", "P", "L", "RB010R01B01",
        ftp_factory=_ftp_refuse_factory, opener_factory=_opener(src), throttle=0)
    res = job.run()
    assert res["status"] == "done", res
    assert res["done"] == 4
    assert res["id"] == job.id
    assert job.snapshot()["status"] == "done"
