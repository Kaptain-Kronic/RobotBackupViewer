"""Discover + folder-walk unit tests - fully offline (synthetic folders + a fake
FTP), so they touch neither the network nor a user's %APPDATA% and are safe in
the public repo. Mirrors the ftp_factory injection style of test_ftpbackup.py."""
import ftplib
import json

from backupviewer import discover, session


# -- folder backup detection ----------------------------------------------------

def _touch(d, name):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("x", encoding="utf-8")


def test_looks_like_backup(tmp_path):
    md = tmp_path / "MD"
    _touch(md, "SUMMARY.DG")
    assert session.looks_like_backup(md) is True

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "notes.txt").write_text("hi", encoding="utf-8")
    assert session.looks_like_backup(plain) is False

    maint = tmp_path / "maint"
    (maint / "mnt_data").mkdir(parents=True)
    assert session.looks_like_backup(maint) is True


def test_find_backup_roots_latest_style(tmp_path):
    # a per-line Latest/ mirror: one backup per robot folder
    parent = tmp_path / "Latest"
    _touch(parent / "R1", "SUMMARY.DG")
    _touch(parent / "R2", "SYSTEM.VA")
    roots = session.find_backup_roots(parent)
    assert sorted(r.name for r in roots) == ["R1", "R2"]


def test_find_backup_roots_nested_and_empty(tmp_path):
    # a LINE/ROBOT/<date>/<time> tree: the time folder is the root, descended to
    deep = tmp_path / "LINE" / "ROBOT" / "2026_01_01" / "12_00_00"
    _touch(deep, "SUMMARY.DG")
    roots = session.find_backup_roots(tmp_path / "LINE")
    assert [r.name for r in roots] == ["12_00_00"]

    empty = tmp_path / "empty"
    (empty / "sub").mkdir(parents=True)
    assert session.find_backup_roots(empty) == []


def test_normalize_cidr_meets_users_where_they_are():
    # a bare IP means its /24 - nobody should need to know CIDR notation
    assert discover.normalize_cidr("192.0.2.0") == "192.0.2.0/24"
    assert discover.normalize_cidr(" 10.0.0.5 ") == "10.0.0.5/24"      # host bits fine
    assert discover.normalize_cidr("192.168.1.0/16") == "192.168.1.0/16"  # explicit wins
    assert discover.normalize_cidr("") == ""                           # empty -> default kicks in
    assert discover.normalize_cidr("not-an-ip") == "not-an-ip"         # let validation speak


def test_find_backup_roots_reports_cap_truncation(tmp_path):
    # more backup roots than the cap -> the caller is TOLD, not silently shorted
    parent = tmp_path / "Latest"
    for i in range(5):
        _touch(parent / f"R{i}", "SUMMARY.DG")
    stats: dict = {}
    roots = session.find_backup_roots(parent, cap=3, stats=stats)
    assert len(roots) == 3 and stats.get("truncated") is True
    ok: dict = {}
    assert len(session.find_backup_roots(parent, stats=ok)) == 5
    assert "truncated" not in ok


# -- network discovery -----------------------------------------------------------

class FakeFTP:
    """A FANUC controller at 10.0.0.5; any other host answers like a plain ftpd.
    Implements only the surface probe_controller + _resolve_name actually use."""

    def __init__(self, timeout=None):
        self._cwd = "/"
        self.host = None

    def connect(self, host, port=21):
        self.host = host

    def login(self, user="", passwd=""):
        pass

    def set_pasv(self, flag):
        pass

    def getwelcome(self):
        return "220 FANUC Robot FTP server ready" if self.host == "10.0.0.5" else "220 generic ftpd"

    def cwd(self, path):
        if path in ("/", ""):
            self._cwd = "/"
            return "250 ok"
        if path.rstrip("/").upper() == "MD:" and self.host == "10.0.0.5":
            self._cwd = "MD:"
            return "250 ok"
        raise ftplib.error_perm("550 no such device: " + path)

    def nlst(self, *args):
        return ["MAIN.LS", "ERRALL.LS"] if self._cwd == "MD:" else []

    def retrlines(self, cmd, callback):
        name = cmd.split(" ", 1)[1]
        line = {"MAIN.LS": "/PROG MAIN",
                "ERRALL.LS": "ERRALL.LS   Robot Name COOLBOT 2026/01/01"}.get(name, "")
        if not line and name.upper() != "LOGBOOK.LS":
            raise ftplib.error_perm("550 no such file: " + name)
        callback(line)  # discover stops after the first line via an internal exception
        return "226 transfer complete"

    def retrbinary(self, cmd, callback, blocksize=8192):
        # base controller serves no SUMMARY.DG - naming falls back to the .LS sniff
        raise ftplib.error_perm("550 no such file: " + cmd.split(" ", 1)[1])

    def quit(self):
        pass

    def close(self):
        pass


# a minimal SUMMARY.DG: version section (model + F-number) + ethernet ($HOSTNAME)
_SUMMARY_DG = (
    '<H2><A NAME="1">Version Information</A></H2><PRE>\n'
    "F Number    : F999001\n"
    "VERSION INFORMATION ::\n"
    "R-2000iC/210F   01/01/2026\n"
    "</PRE>\n"
    '<H2><A NAME="2">Ethernet</A></H2><PRE>\n'
    "$HOSTNAME : BINPICKER\n"
    "</PRE>\n"
)


def test_network_scan_finds_only_fanuc():
    hosts = ["10.0.0.1", "10.0.0.5", "10.0.0.9"]
    job = discover.NetworkScanJob(
        "10.0.0.0/24",
        ftp_factory=lambda timeout=None: FakeFTP(timeout),
        host_provider=lambda cidr: hosts,
        port_check=lambda h, p, t: h in ("10.0.0.1", "10.0.0.5"),  # .9 is down
    )
    job.run()
    snap = job.snapshot()
    assert snap["status"] == "done"
    assert snap["total"] == 3
    got = {r["host"]: r for r in snap["results"]}
    assert set(got) == {"10.0.0.5"}                # only the FANUC host (.1 is a plain ftpd)
    assert got["10.0.0.5"]["name"] == "COOLBOT"    # name read from the report header
    assert got["10.0.0.5"]["has_md"] is True


def test_network_scan_name_falls_back_to_ip():
    class NoName(FakeFTP):
        def nlst(self, *args):
            return ["MAIN.LS"] if self._cwd == "MD:" else []

        def retrlines(self, cmd, callback):
            name = cmd.split(" ", 1)[1]
            if name == "MAIN.LS":          # only a program listing - no robot name anywhere
                callback("/PROG MAIN")
                return "226 transfer complete"
            raise ftplib.error_perm("550 no such file: " + name)

    job = discover.NetworkScanJob(
        "10.0.0.0/24",
        ftp_factory=lambda timeout=None: NoName(timeout),
        host_provider=lambda cidr: ["10.0.0.5"],
        port_check=lambda h, p, t: True,
    )
    job.run()
    r = job.snapshot()["results"][0]
    assert r["host"] == "10.0.0.5"
    assert r["name"] == ""   # the UI falls back to the IP


def test_network_scan_name_from_summary():
    """SUMMARY.DG is the primary source - one GET yields name + model + F-number."""
    class WithSummary(FakeFTP):
        def retrbinary(self, cmd, callback, blocksize=8192):
            name = cmd.split(" ", 1)[1]
            if name.upper() == "SUMMARY.DG":
                data = _SUMMARY_DG.encode("cp1252")
                for i in range(0, len(data), blocksize):
                    callback(data[i:i + blocksize])
                return "226 transfer complete"
            raise ftplib.error_perm("550 no such file: " + name)

    job = discover.NetworkScanJob(
        "10.0.0.0/24",
        ftp_factory=lambda timeout=None: WithSummary(timeout),
        host_provider=lambda cidr: ["10.0.0.5"],
        port_check=lambda h, p, t: True,
    )
    job.run()
    r = job.snapshot()["results"][0]
    assert r["name"] == "BINPICKER"          # from $HOSTNAME
    assert r["model"] == "R-2000iC/210F"
    assert r["f_number"] == "F999001"


def test_network_scan_name_from_unlisted_report():
    """The regression: a live controller hides report .LS from nlst() but serves
    them on GET. The shortlist RETR finds the name where the old filter couldn't."""
    class Hidden(FakeFTP):
        def nlst(self, *args):
            return ["MAIN.TP", "SYSTEM.SV"] if self._cwd == "MD:" else []  # no .LS listed

    job = discover.NetworkScanJob(
        "10.0.0.0/24",
        ftp_factory=lambda timeout=None: Hidden(timeout),
        host_provider=lambda cidr: ["10.0.0.5"],
        port_check=lambda h, p, t: True,
    )
    job.run()
    r = job.snapshot()["results"][0]
    assert r["name"] == "COOLBOT"   # ERRALL.LS RETR'd directly despite not being listed


def test_network_scan_name_skips_programs_finds_logbook():
    """Real R-30iB shape (captured via --diagnose): MD: lists program .ls files
    alphabetically first; the report files are reached only by the direct-RETR
    shortlist. ERRALL.LS resets the data connection, LOGBOOK.LS reads cleanly."""
    HEADER = "LOGBOOK.LS     Robot Name RB232R01B01 29-JUN-26 15:30:16  "

    class R30iB(FakeFTP):
        def nlst(self, *args):
            if self._cwd != "MD:":
                return []
            return ["-bcked2-.ls", "abortit.ls", "agitate.ls", "atpounce.ls",
                    "blowoff.ls", "bypass.ls", "chkpart.ls", "clr2prcd.ls",
                    "errall.ls", "logbook.ls"]   # programs first; reports buried

        def retrlines(self, cmd, callback):
            name = cmd.split(" ", 1)[1].upper()
            if name == "LOGBOOK.LS":
                callback(HEADER)
                return "226 ok"
            if name == "ERRALL.LS":
                raise ftplib.error_perm("550 Connection reset by peer")
            callback("/PROG  " + name.split(".")[0])   # everything else is a program
            return "226 ok"

        def retrbinary(self, cmd, callback, blocksize=8192):
            raise ftplib.error_perm("550 no SUMMARY in this fake")

    job = discover.NetworkScanJob(
        "10.0.0.0/24", ftp_factory=lambda timeout=None: R30iB(timeout),
        host_provider=lambda c: ["10.0.0.5"], port_check=lambda h, p, t: True)
    job.run()
    r = job.snapshot()["results"][0]
    assert r["name"] == "RB232R01B01"   # skipped the programs, RETR'd LOGBOOK by name


def test_resolve_robot_name_roots_at_md():
    """A controller that refuses cwd('MD:') (roots straight at MD:) must still
    resolve - mirrors the proven backup enumeration's tolerance."""
    class RootsAtMd(FakeFTP):
        def cwd(self, path):
            if path in ("/", ""):
                return "250 ok"
            raise ftplib.error_perm("550 no such device: " + path)  # MD: refused

        def nlst(self, *args):
            return ["ERRALL.LS"]   # already rooted at the MD content

    info = discover.resolve_robot_name(lambda timeout=None: RootsAtMd(timeout), "10.0.0.5", 21)
    assert info["name"] == "COOLBOT"


def test_diagnose_controller_smoke():
    """The read-only probe returns a fully-keyed dict and never raises."""
    info = discover.diagnose_controller("10.0.0.5", ftp_factory=lambda timeout=None: FakeFTP(timeout))
    assert info["host"] == "10.0.0.5" and info["error"] == ""
    assert "FANUC" in info["banner"]
    assert info["cwd"]["MD:"] == "ok"
    assert isinstance(info["nlst"], dict) and isinstance(info["files"], dict)
    assert "ERRALL.LS" in info["files"] and "SUMMARY.DG" in info["files"]
    assert info["resolved"]["name"] == "COOLBOT"   # via the .LS fallback


def test_enumerate_hosts_single_and_range():
    assert discover.enumerate_hosts("127.0.0.1/32") == ["127.0.0.1"]
    assert len(discover.enumerate_hosts("10.0.0.0/24")) == 254


# -- network adapters ------------------------------------------------------------

class _Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout


def test_list_adapters_parses_powershell():
    sample = json.dumps([
        {"name": "Wi-Fi", "status": "Up", "media": "Native 802.11", "ip": "192.168.1.50", "prefix": 24},
        {"name": "Ethernet", "status": "Up", "media": "802.3", "ip": "192.0.2.42", "prefix": 24},
        {"name": "Ethernet 2", "status": "Disconnected", "media": "802.3", "ip": None, "prefix": None},
        {"name": "Loopback", "status": "Up", "media": "Loopback", "ip": "127.0.0.1", "prefix": 8},
    ])
    adapters = discover.list_adapters(runner=lambda *a, **k: _Proc(sample))
    names = [a["name"] for a in adapters]
    assert "Loopback" not in names          # 127.x skipped
    assert "Ethernet 2" not in names        # no IPv4 -> skipped
    assert adapters[0]["kind"] == "ethernet"   # ethernet sorts ahead of wifi
    eth = next(a for a in adapters if a["name"] == "Ethernet")
    assert eth["cidr"] == "192.0.2.0/24"
    assert eth["up"] is True and eth["default"] is True   # the up ethernet is the default
    wifi = next(a for a in adapters if a["name"] == "Wi-Fi")
    assert wifi["kind"] == "wifi" and wifi["default"] is False


def test_list_adapters_empty_on_failure():
    def boom(*a, **k):
        raise OSError("powershell missing")
    assert discover.list_adapters(runner=boom) == []
    assert discover.list_adapters(runner=lambda *a, **k: _Proc("")) == []
    assert discover.list_adapters(runner=lambda *a, **k: _Proc("not json")) == []
