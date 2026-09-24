"""HTTP backup taker for FANUC Global-5 controllers (R-50iA / newer R-30iB
Plus), and the auto-detecting dispatcher that chooses it.

Why this exists: a Global-5 controller ships with FANUC User Management enabled,
which turns OFF the anonymous FTP that ftpbackup.py uses for Global-4 machines
(the login is refused with 530). But the controller's built-in web server on
port 80 is unauthenticated and serves the whole MD: device: the landing page
links four auto-generated index pages (INDEX_TP/VR/ER/OT.HTM) that list every
file as `<a href="../MD/NAME">`, and each file downloads from `/MD/<NAME>`. So a
Global-5 backs up over HTTP with NO credential, and the file set is the same MD:
ASCII backup (.SV/.TP/.VR/.IO/.DG/.VA) the viewer already parses - the snapshot
is indistinguishable on disk from an FTP one bar its `source`.

Engine ethics mirror ftpbackup.py (it may point at a running production robot):
  - ONE keep-alive connection per controller, no parallel GETs against one host
  - a small throttle between files, timeouts everywhere, no retry storms
  - each file to <name>.part then rename, so a crash never leaves a half-file
  - the four INDEX_*.HTM pages give the listing FTP's LIST would; the synthesised
    system files (SYSVARS.SV, ...) the indexes DON'T list are pulled by known
    name and simply skipped on a robot that lacks one - never counted as a loss
  - the dated snapshot is the source of truth; the Latest mirror swaps in last

The HTTP client is injected via opener_factory so the whole flow is testable
offline against a FakeHttp (tests/test_httpbackup.py) - live-controller testing
is never the first validation.
"""
from __future__ import annotations

import http.client
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from . import ftpbackup
from .ftpbackup import _JobBase, _now, dated_dir, long_path, probe_controller

log = logging.getLogger(__name__)

HTTP_PORT = 80
HTTP_TIMEOUT = 15
INDEX_PAGES = ("INDEX_TP.HTM", "INDEX_VR.HTM", "INDEX_ER.HTM", "INDEX_OT.HTM")
SKIP_EXTS = (".IMG", ".IMR")   # image artifacts: not a file backup's job (== ftpbackup)
MAX_FILES = 5000
CONSEC_FAIL_LIMIT = 10  # this many stalled GETs in a row = the connection is gone, abort

# System files the controller synthesises and serves by name but does NOT list
# in the index pages (an FTP MD: LIST includes them; the web index does not). A
# robot that lacks one just 404s and it is dropped - never a recorded loss.
SYSTEM_FILES = (
    "SYSVARS.SV", "SYSMAST.SV", "SYSMACRO.SV", "SYSSERVO.SV", "SYSMOTN.SV",
    "SYSFRAME.VR", "FRAMEVAR.SV", "DIOCFGSV.IO", "PRGSTATE.DG", "IOSTATE.DG",
)

_HREF = re.compile(
    r'href\s*=\s*["\']?(?:\.\./|/)MD/([A-Za-z0-9_.\-]+\.[A-Za-z0-9]{1,4})', re.I)


def index_filenames(html: str) -> list[str]:
    """The MD: filenames an INDEX_*.HTM page links: de-duplicated, upper-cased,
    first-seen order, with the INDEX_* pages themselves excluded (a page links
    its siblings). Pure text -> list, no I/O."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _HREF.finditer(html or ""):
        name = m.group(1).upper()
        if name.startswith("INDEX_") or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _looks_like_error_body(body: bytes) -> bool:
    """The web server answers a missing file with an HTML `404 File Not Found`
    page and a bare directory with a plaintext `HTTP/1.1 404 File Access Error`.
    Match those exact phrases, NOT HTML in general: the INDEX_*.HTM listings are
    HTML, and legitimate MD: files include .STM pages that are HTML too - so a
    blanket "starts with <HTML>" would drop real content. (A missing file also
    carries HTTP status 404, checked separately; this catches the 200+plaintext
    directory case and belts-and-braces the 404 page.)"""
    head = body[:96].upper()
    return b"404 FILE NOT FOUND" in head or b"FILE ACCESS ERROR" in head


class _Http:
    """A tiny keep-alive GET client over one http.client connection. Injected in
    tests. get(path) -> (status, body); reconnects once if the server drops the
    keep-alive mid-backup, then surfaces the error so the job records a real
    loss rather than retry-storming a live controller."""

    def __init__(self, host, port=HTTP_PORT, timeout=HTTP_TIMEOUT):
        self.host = host
        self.port = int(port or HTTP_PORT)
        self.timeout = timeout
        self._c = None

    def _conn(self):
        if self._c is None:
            self._c = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return self._c

    def get(self, path):
        for attempt in (0, 1):
            try:
                c = self._conn()
                c.request("GET", path, headers={
                    "User-Agent": "BackupViewer", "Connection": "keep-alive"})
                r = c.getresponse()
                return r.status, r.read()
            except (http.client.HTTPException, OSError):
                self.close()
                if attempt:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover - loop always returns/raises

    def close(self):
        if self._c is not None:
            try:
                self._c.close()
            except Exception:  # noqa: BLE001
                pass
            self._c = None


def _default_opener(host, port, timeout):
    return _Http(host, port, timeout)


def _download(client, name: str, dest: Path) -> int | None:
    """GET /MD/<name> to `dest` via .part-then-rename. Returns bytes written, or
    None when the server has no such file (a clean 404 / error body - an index
    can outrace a just-deleted program, and a system-file probe legitimately
    misses). Raises the transport error when the connection itself fails, so a
    dead link fails the job instead of silently emptying the backup."""
    status, body = client.get("/MD/" + name)
    if status != 200 or _looks_like_error_body(body):
        return None
    os.makedirs(long_path(dest.parent), exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part_l, dest_l = long_path(part), long_path(dest)
    with open(part_l, "wb") as fh:
        fh.write(body)
    os.replace(part_l, dest_l)
    return len(body)


class HttpBackupJob(_JobBase):
    """One Global-5 backup run over HTTP. Same lifecycle and on-disk contract as
    ftpbackup.BackupJob: construct, .run() on a worker thread, poll .snapshot(),
    .cancel() between files. Writes the same dated-history + Latest-mirror tree,
    so the viewer reads it with no change - only backup.json `source` differs."""

    TYPE_STR = "all of above"
    SOURCE = "http"
    NOTE_PREFIX = "backup of"
    LOG_LABEL = "http backup"

    def __init__(self, host, dest_root, plant, line, robot, *,
                 port=HTTP_PORT, note="", run_id="", throttle=0.02,
                 on_complete=None, opener_factory=_default_opener):
        super().__init__(host, dest_root, plant, line, robot, note=note,
                         run_id=run_id, throttle=throttle, on_complete=on_complete)
        self.port = int(port or HTTP_PORT)
        self.devices = ["MD:"]
        self._opener_factory = opener_factory

    def _meta_extra(self) -> dict:
        return {"devices": self.devices}

    def run(self) -> dict:
        self._set(status="connecting", started=_now().isoformat(timespec="seconds"))
        client = self._opener_factory(self.host, self.port, HTTP_TIMEOUT)
        try:
            names = self._enumerate(client)
            if self.cancelled:
                return self._finish("cancelled")
            if not names:
                return self._finish(
                    "error", error="no MD: index served (not a FANUC web server?)")
            total = len(names)
            self._set(status="downloading", total=total)

            when = _now()
            dated = dated_dir(self.dest_root, self.plant, self.line, self.robot, when)
            dated.mkdir(parents=True, exist_ok=True)
            self._set(dated_path=str(dated))
            # started-marker first: backup.json exists from the first moment with
            # complete:false; only _write_sidecars - the LAST step of a
            # successful pull - flips it true (see _JobBase).
            self._write_meta(dated, when, complete=False)

            done = 0
            nbytes = 0
            consec_fail = 0
            # 1) files the indexes list. A clean 404 (listed-but-gone race) OR a
            #    transport stall on ONE file is recorded as a skip and the pull
            #    continues - the embedded web server occasionally stalls a single
            #    GET past the timeout, and losing a whole ~600-file backup to that
            #    would be the fragile kind of correct. A run of CONSEC_FAIL_LIMIT
            #    stalls in a row means the connection is gone, so abort honestly
            #    rather than skip-storm the rest at one timeout each.
            for name in names:
                if self.cancelled:
                    return self._finish("cancelled")
                self._set(current=name)
                try:
                    n = _download(client, name, dated / name)
                except (http.client.HTTPException, OSError) as e:
                    consec_fail += 1
                    log.info("http backup skip %s on %s: %s", name, self.host, e)
                    with self._lock:
                        self._p["skipped"].append(name)
                    if consec_fail >= CONSEC_FAIL_LIMIT:
                        return self._finish(
                            "error",
                            error=f"connection lost after {done} files "
                                  f"({consec_fail} stalls in a row: {type(e).__name__})")
                    continue
                consec_fail = 0
                if n is None:
                    with self._lock:
                        self._p["skipped"].append(name)
                    continue
                done += 1
                nbytes += n
                self._set(done=done, bytes=nbytes)
                if self._throttle:
                    time.sleep(self._throttle)
            # 2) synthesised system files the indexes omit: best-effort. Present
            #    ones join the count (bump total WITH done so a complete pull
            #    stays done==total); absent ones AND a stall on one are dropped -
            #    never a skip, never a job failure (they are a bonus, not the set).
            for name in SYSTEM_FILES:
                if self.cancelled:
                    return self._finish("cancelled")
                self._set(current=name)
                try:
                    n = _download(client, name, dated / name)
                except (http.client.HTTPException, OSError) as e:
                    log.info("http backup optional %s on %s: %s", name, self.host, e)
                    continue
                if n is None:
                    continue
                total += 1
                done += 1
                nbytes += n
                self._set(total=total, done=done, bytes=nbytes)
                if self._throttle:
                    time.sleep(self._throttle)

            if done == 0:
                return self._finish("error", error="reachable but pulled 0 files")

            self._write_sidecars(dated, when, done, nbytes)
            latest = self._mirror_latest(dated)
            self._set(latest_path=str(latest) if latest else "")
            result = self._finish("done")
            self._fire_on_complete()
            return result
        except (http.client.HTTPException, OSError) as e:
            log.warning("http backup of %s@%s failed: %s", self.robot, self.host, e)
            return self._finish("error", error=f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface, never crash the worker
            log.exception("http backup of %s failed", self.robot)
            return self._finish("error", error=f"{type(e).__name__}: {e}")
        finally:
            client.close()

    def _enumerate(self, client) -> list[str]:
        """Union of the four index pages, minus image artifacts, capped. An index
        page a given controller lacks is skipped; a total miss (no page served)
        returns [] so run() reports 'not a FANUC web server'. The system files
        are appended by run(), not here, because their existence is unknown
        until fetched."""
        self._set(status="listing")
        names: list[str] = []
        seen: set[str] = set()
        for page in INDEX_PAGES:
            if self.cancelled:
                return []
            try:
                status, body = client.get("/MD/" + page)
            except (http.client.HTTPException, OSError) as e:
                log.info("index %s failed on %s: %s", page, self.host, e)
                continue
            if status != 200 or _looks_like_error_body(body):
                continue
            for name in index_filenames(body.decode("latin1", "replace")):
                if name.endswith(SKIP_EXTS):
                    with self._lock:
                        self._p["skipped"].append(name)
                    continue
                if name in seen or name in SYSTEM_FILES:
                    continue
                if len(names) >= MAX_FILES:
                    log.warning("file cap %d hit on %s", MAX_FILES, self.host)
                    return names
                seen.add(name)
                names.append(name)
        return names


def _probe_http(host, http_port, opener_factory) -> dict:
    """Read-only web-server check: does /MD/INDEX_TP.HTM parse as a FANUC MD:
    index? Only then is HTTP a real backup transport for this host."""
    out = {"reachable": False, "transport": "http", "banner": "",
           "has_md": False, "has_fr": False, "error": ""}
    client = opener_factory(host, http_port, HTTP_TIMEOUT)
    try:
        status, body = client.get("/MD/INDEX_TP.HTM")
        if (status == 200 and not _looks_like_error_body(body)
                and index_filenames(body.decode("latin1", "replace"))):
            out["reachable"] = True
            out["has_md"] = True
            out["banner"] = "FANUC web server (MD: over HTTP, no login)"
        else:
            out["error"] = "web server present but no MD: index"
    except (http.client.HTTPException, OSError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        client.close()
    return out


def probe_robot(host, *, user="", passwd="", passive=True, port=21,
                http_port=HTTP_PORT, ftp_factory=None,
                opener_factory=_default_opener) -> dict:
    """Auto-detecting pre-flight (read-only, no downloads). Try anonymous FTP
    first (Global-4). If FTP refuses/cannot connect, check the web server: a
    FANUC MD: index means transport 'http' (Global-5). Returns the same shape as
    ftpbackup.probe_controller plus a `transport` key ('ftp' | 'http' | '')."""
    kw = {} if ftp_factory is None else {"ftp_factory": ftp_factory}
    ftp = probe_controller(host, user=user, passwd=passwd,
                           passive=passive, port=port, **kw)
    if ftp.get("reachable"):
        ftp["transport"] = "ftp"
        return ftp
    web = _probe_http(host, http_port, opener_factory)
    if web.get("reachable"):
        return web
    # neither transport: keep the FTP error (the useful one) and say so
    ftp["transport"] = ""
    if web.get("error") and ftp.get("error"):
        ftp["error"] = f"{ftp['error']}; web: {web['error']}"
    return ftp


class RobotBackupJob:
    """FANUC backup dispatcher. On the worker thread it detects whether the
    controller speaks anonymous FTP (Global-4 -> ftpbackup.BackupJob) or only
    the unauthenticated HTTP MD: server (Global-5 -> HttpBackupJob), then runs
    that transport. It presents the exact job interface start_backup/poll rely
    on (id, run_id, run/snapshot/cancel/cancelled, library_match/backup) and
    forwards to the chosen inner job, so neither transport class changes.

    Detection runs INSIDE run() (never in start_backup): an unreachable host
    must not block the UI thread on a connect timeout."""

    def __init__(self, host, dest_root, plant, line, robot, *,
                 user="", passwd="", passive=True, port=21,
                 note="", run_id="", throttle=None, on_complete=None,
                 ftp_factory=None, opener_factory=_default_opener):
        self.id = uuid.uuid4().hex
        self.run_id = run_id or ""
        self.host = host
        self._args = (host, dest_root, plant, line, robot)
        self._note = note or ""
        self._on_complete = on_complete
        self._throttle = throttle
        self._ftp = {"user": user, "passwd": passwd, "passive": passive, "port": port}
        if ftp_factory is not None:
            self._ftp_factory = ftp_factory
        else:
            self._ftp_factory = None
        self._opener_factory = opener_factory
        self._cancel = threading.Event()
        self._inner = None
        self._pending = {
            "id": self.id, "run_id": self.run_id, "status": "pending", "host": host,
            "robot": robot, "line": line, "plant": plant, "total": 0, "done": 0,
            "bytes": 0, "current": "", "skipped": [], "error": "", "dated_path": "",
            "latest_path": "", "started": "", "finished": "",
        }

    def _build(self):
        host, dest_root, plant, line, robot = self._args
        probe = probe_robot(
            host, http_port=HTTP_PORT, opener_factory=self._opener_factory,
            ftp_factory=self._ftp_factory, **self._ftp)
        common = dict(note=self._note, run_id=self.run_id, on_complete=self._on_complete)
        if self._throttle is not None:
            common["throttle"] = self._throttle
        if probe.get("transport") == "http":
            inner = HttpBackupJob(host, dest_root, plant, line, robot,
                                  port=HTTP_PORT, opener_factory=self._opener_factory,
                                  **common)
        else:
            # 'ftp', or unreachable: run the FTP job so it fails with the real
            # 530/timeout, exactly as before this dispatcher existed.
            ftpkw = dict(self._ftp)
            if self._ftp_factory is not None:
                ftpkw["ftp_factory"] = self._ftp_factory
            inner = ftpbackup.BackupJob(host, dest_root, plant, line, robot,
                                        **ftpkw, **common)
        inner.id = self.id
        inner._p["id"] = self.id
        inner._cancel = self._cancel      # share cancellation across detect -> run
        return inner

    def run(self) -> dict:
        try:
            self._inner = self._build()
        except Exception as e:  # noqa: BLE001 - detection must not crash the worker
            log.exception("transport detection for %s failed", self._args[4])
            self._pending.update(status="error", error=f"{type(e).__name__}: {e}",
                                 finished=_now().isoformat(timespec="seconds"))
            return dict(self._pending)
        return self._inner.run()

    def snapshot(self) -> dict:
        return self._inner.snapshot() if self._inner else dict(self._pending)

    def cancel(self):
        self._cancel.set()
        if self._inner is not None:
            self._inner.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def library_match(self) -> dict:
        return self._inner.library_match()

    def library_backup(self) -> dict:
        return self._inner.library_backup()
