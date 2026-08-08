"""The JS <-> Python surface. Exposed to the page as window.pywebview.api.

Every public method returns an envelope and never raises across the bridge:
    {"ok": True, "data": ...}
    {"ok": False, "error": {"code": "MISSING_FILE", "message": "..."}}
"""
from __future__ import annotations

import base64
import fnmatch
import functools
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000  # keep helper powershell spawns off-screen

from . import backuplog
from . import compare
from . import __version__
from . import cvx_remote
from . import discover
from . import ftpbackup
from . import healthscan
from . import keyence_workspace
from . import keyencebackup
from . import library
from . import modeldb
from . import mtxbackup
from . import phoneview
from . import qr
from . import screengrab
from . import search as search_mod
from . import settings
from .parsers import (alarms, callgraph, curpos, cvx_image, dcs, dcszones,
                      frames, gmwizlog, io_dg, kinematics, ls_edit, ls_program,
                      macros, magnet, mastering, mhvalves, mtx_portal,
                      mtx_saved_image, payloads, registers, styles, summary_dg,
                      sysvars)
from .parsers.common import is_binary, read_text
from .session import BackupSession, looks_like_backup

log = logging.getLogger(__name__)

MAX_TEXT_BYTES = 2_000_000
HEX_PREVIEW_BYTES = 4096
MAX_IMAGE_BYTES = 12_000_000
MAX_WS_SCAN_FILES = 20_000   # same bound the session walk uses
# CV-X images are decoded down to this on the long edge. 2048-square masters are
# both slower and bigger than any screen needs, and the decimation happens inside
# the decode, so a thumbnail costs a fraction of a hero.
DISPLAY_MAX_DIM = 1200
_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".bmp": "image/bmp"}


def _is_ls_program(p: Path) -> bool:
    """A TP program is a .LS whose FIRST bytes are /PROG - the same content
    test session._classify_ls uses. Extension alone is not enough: report dumps
    (ERRALL.LS, LOGBOOK.LS) are .LS too."""
    if p.suffix.lower() != ".ls":
        return False
    try:
        with open(p, "rb") as f:
            return f.read(120).decode("cp1252", errors="replace").startswith("/PROG")
    except OSError:
        return False


class ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _endpoint(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            data = fn(self, *args, **kwargs)
            return {"ok": True, "data": data, "ms": round((time.perf_counter() - t0) * 1000)}
        except ApiError as e:
            return {"ok": False, "error": {"code": e.code, "message": str(e)}}
        except Exception as e:  # noqa: BLE001 - bridge boundary
            log.exception("api %s failed", fn.__name__)
            return {"ok": False, "error": {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}"}}
    return wrapper


def _tree_size(root) -> tuple:
    r"""(file count, total bytes) under `root`, walked through the \\?\ prefix so a
    deep camera tree is measured rather than silently reported as empty (the same
    MAX_PATH trap that once emptied the photos index). Best effort: an unreadable
    file is skipped, never raised - this only feeds a size label."""
    files = total = 0
    try:
        for dirpath, _dirs, names in os.walk(ftpbackup.long_path(root)):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, n))
                    files += 1
                except OSError:
                    continue
    except OSError:
        pass
    return files, total


def _require_ip(spec: dict) -> str:
    """The validated camera IP out of a remote-connect spec, or ApiError."""
    ip = ((spec or {}).get("ip") or "").strip()
    if not ip:
        raise ApiError("BAD_SPEC", "camera IP is required")
    try:
        import ipaddress
        ipaddress.ip_address(ip)
    except ValueError:
        raise ApiError("BAD_SPEC", f"not a valid IP: {ip}")
    return ip


def _probe_http(url: str, timeout: float = 4.0):
    """GET url; returns (status, headers, final_url, body_text). An HTTP error
    response (401/404/...) is still a live server and is returned, not raised;
    only socket-level failures propagate (as OSError). Injectable for tests."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "BackupViewer"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(262144).decode("utf-8", "replace")
            return r.status, dict(r.headers), r.geturl(), body
    except urllib.error.HTTPError as e:
        try:
            body = e.read(262144).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, dict(e.headers or {}), url, body


# -- merge-identity evidence ------------------------------------------------------
# What confirms two library entries are the SAME physical robot (Cody's field
# checklist): hostname (sometimes changes), IP (sometimes changes), F-number
# (never changes), master counts (rarely change; equal counts = same arm).
# 2+ matches = a match; exactly 1 = maybe. FANUC's factory hostname carries no
# identity, and full names follow the <LL><op>R<nn>B<bb> plant convention.

_DEFAULT_HOSTNAMES = {"ROBOT"}
_FULL_NAME_RE = re.compile(r"^[A-Z]{2}\d{2,4}R\d{2}B\d{2}$", re.IGNORECASE)


def _merge_evidence(a: dict, b: dict) -> list | None:
    """The identity signals two robot fingerprints share (see
    Api._robot_fingerprint). Returns the matched signal names — or None when
    the F-numbers actively DISAGREE: an F-number never changes, so a mismatch
    means different robots no matter what else lines up (a veto, not merely
    a missing signal)."""
    ev = []
    an, bn = (a.get("name") or "").upper(), (b.get("name") or "").upper()
    if an and an == bn and an not in _DEFAULT_HOSTNAMES:
        ev.append("name")
    if (a.get("ips") or set()) & (b.get("ips") or set()):
        ev.append("IP")
    af, bf = (a.get("f_number") or "").upper(), (b.get("f_number") or "").upper()
    if af and bf:
        if af != bf:
            return None
        ev.append("F-number")
    if a.get("counts") and b.get("counts") and a["counts"] == b["counts"]:
        ev.append("master counts")
    return ev


def _watch_step(last: str | None, pending: bool, sig: str) -> tuple[str, bool, bool]:
    """One debounced watcher transition: (last, pending, sig) -> (last, pending,
    fire). The first tick only baselines (never fire at boot); a changed
    signature arms `pending`; a QUIET tick with pending armed fires — so a burst
    of Explorer copies produces one notification after it settles."""
    if last is None:
        return sig, False, False
    if sig != last:
        return sig, True, False
    if pending:
        return last, False, True
    return last, False, False


# -- device-type registry ---------------------------------------------------------
# One row per backable device brand: how to probe it (pre-flight, no writes), how
# to diagnose it (read-only), and how to build its backup job from a start_backup
# spec. The per-brand credential defaults live here and nowhere else, so adding a
# brand is one registration (+ its module), not three edits to matching if/elif
# chains. "" is the FANUC robot default row; job rows get the common
# (host, root, plant, line, robot, note, run_id, on_complete) plus their job_kw.

_DEVICE_REGISTRY = {
    "camera-mtx": {   # SMB - no port/passive; blank creds -> burned-in camera login
        "probe": lambda host, spec: mtxbackup.probe_camera(
            host, user=spec.get("user") or mtxbackup.MTX_USER,
            passwd=spec.get("passwd") or mtxbackup.MTX_PASS),
        "diagnose": lambda host, spec: mtxbackup.diagnose_camera(
            host, user=spec.get("user") or mtxbackup.MTX_USER,
            passwd=spec.get("passwd") or mtxbackup.MTX_PASS),
        "job_cls": mtxbackup.CameraBackupJob,
        "job_kw": lambda spec: {
            "cameras": spec.get("cameras"),
            "user": spec.get("user") or mtxbackup.MTX_USER,
            "passwd": spec.get("passwd") or mtxbackup.MTX_PASS,
        },
    },
    "camera-keyence": {   # anonymous FTP
        "probe": lambda host, spec: keyencebackup.probe_keyence(
            host, passive=spec.get("passive", True), port=spec.get("port", 21)),
        "diagnose": lambda host, spec: keyencebackup.diagnose_keyence(
            host, passive=spec.get("passive", True), port=spec.get("port", 21)),
        "job_cls": keyencebackup.KeyenceBackupJob,
        "job_kw": lambda spec: {
            "cameras": spec.get("cameras"),
            "passive": spec.get("passive", True), "port": spec.get("port", 21),
            "include_box": bool(spec.get("include_box")),
        },
    },
    "": {   # FANUC robot (the default row)
        "probe": lambda host, spec: ftpbackup.probe_controller(
            host, user=spec.get("user", ""), passwd=spec.get("passwd", ""),
            passive=spec.get("passive", True), port=spec.get("port", 21)),
        "diagnose": lambda host, spec: discover.diagnose_controller(
            host, port=spec.get("port", 21)),
        "job_cls": ftpbackup.BackupJob,
        "job_kw": lambda spec: {
            "user": spec.get("user", ""), "passwd": spec.get("passwd", ""),
            "passive": spec.get("passive", True), "port": spec.get("port", 21),
        },
    },
}


def _device_row(spec: dict) -> dict:
    dt = spec.get("device_type") or ""
    return _DEVICE_REGISTRY.get(dt, _DEVICE_REGISTRY[""])


class Api:
    MAX_OPEN_SESSIONS = 6  # parse caches are per-session and never evicted -
    #                        open tabs are real memory; closing one is the
    #                        eviction story, so the cap refuses honestly

    def __init__(self):
        self._window = None
        # open viewer sessions, keyed by sid = str(session.root) - the display
        # path, matching the compare cache key (`compare:{b.root}:{mode}`).
        # Each entry owns its own compare session so every tab restores
        # exactly how you left it.
        # entry: {"session": BackupSession, "compare": BackupSession | None,
        #         "manifest": dict, "owner": "tab" | "popout",
        #         "window": webview.Window | None (popouts only)}
        self._sessions: dict[str, dict] = {}
        self._active_sid: str | None = None  # what the MAIN window is showing
        self._sessions_lock = threading.Lock()  # registry mutations only
        self._jobs: dict[str, ftpbackup.BackupJob] = {}  # active/finished backup jobs
        self._scans: dict[str, discover._ScanJob] = {}  # folder + network scan jobs
        self._lib_sig: str | None = None  # tree signature at the last scan (None = never)
        self._cvx: dict[str, cvx_remote.CvxRemoteSession] = {}  # live CV-X remote sessions
        self._cvx_server = None  # lazy MJPEG frame server (one for all sessions)
        # CV-X remotes popped into their own OS window: sid -> webview.Window.
        # The session MOVES (the overlay in the sending window closes), so a
        # controller's single remote slot is never asked for twice.
        self._cvx_windows: dict[str, object] = {}
        # which windows are borderless-fullscreen right now, by window key
        # ("main" or a popped-out sid) - pywebview only toggles, it doesn't tell
        self._fullscreen: set[str] = set()
        self._phone_share: phoneview.PhoneShare | None = None  # lazy phone-view relay
        # linked-camera photo sessions, keyed camera_id -> (path, sig, session).
        # sig is the latest mirror's backup.json mtime, so a fresh camera backup
        # (which rewrites the SAME Latest/ path) invalidates the cache.
        self._camera_sessions: dict[str, tuple] = {}
        self._lib_seeded = False  # _lib_sig lazily seeded from settings on the first listing
        self._lib_progress = {"active": False, "done": 0, "total": 0, "current": "",
                              "entries": []}
        self._lib_progress_lock = threading.Lock()
        self._scan_thread: threading.Thread | None = None  # the one background library scan
        self._scan_thread_lock = threading.Lock()

    def bind(self, window, initial_backup: str | None = None):
        self._window = window
        if initial_backup:
            try:
                self._open_session(Path(initial_backup))
            except Exception:
                log.exception("could not open initial backup %s", initial_backup)
        if not os.environ.get("BV_NO_WATCHER"):
            threading.Thread(target=self._watch_library, name="libwatch", daemon=True).start()
        try:
            window.events.closing += self._confirm_close
            window.events.closed += self._destroy_popouts
        except Exception:  # noqa: BLE001 - a GUI backend without the event still gets a working app
            log.exception("could not attach the close-confirmation handler")

    # -- library watcher -------------------------------------------------------
    # Polls a cheap tree signature so folders copied in / deleted via Explorer
    # show up without pressing rescan. Polling, not ReadDirectoryChangesW:
    # library roots commonly live on network shares / USB where change
    # notifications are unreliable. Paused while backup jobs run (they write
    # thousands of files into the watched tree).

    _WATCH_POLL_S = 4.0

    def _active_backup_count(self) -> int:
        return sum(1 for j in self._jobs.values()
                   if not ftpbackup.is_terminal(j.snapshot().get("status")))

    def _active_run_id(self) -> str:
        """The run_id of the backup run still in flight, or "". A backup fired
        while others are running JOINS their run: a mid-run retry of a few
        refused robots must land in the same "last run" report, not push a new
        run on top of one that hasn't finished."""
        for j in self._jobs.values():
            snap = j.snapshot()
            if not ftpbackup.is_terminal(snap.get("status")):
                return snap.get("run_id") or ""
        return ""

    def _backups_active(self) -> bool:
        return self._active_backup_count() > 0

    def _confirm_close(self):
        """pywebview `closing` handler: returning False keeps the window open.
        Closing kills the daemon backup threads mid-download (the .part protocol
        means no half-file ever looks complete, but the snapshot is left partial
        with no sidecars), so closing during a backup deserves an explicit yes.
        Any failure fails OPEN - never trap the user inside the app."""
        try:
            n = self._active_backup_count()
            if not n:
                return True
            msg = ("%d backup%s still running. Closing now cuts %s off mid-transfer "
                   "and leaves incomplete snapshot folders. Close anyway?"
                   % (n, "s" if n != 1 else "", "them" if n != 1 else "it"))
            return bool(self._window.create_confirmation_dialog("backups in progress", msg))
        except Exception:  # noqa: BLE001
            log.exception("close-confirmation check failed")
            return True

    def _watch_library(self):
        last: str | None = None
        pending = False
        while True:
            time.sleep(self._WATCH_POLL_S)
            try:
                if self._backups_active():
                    continue
                sig = library.scan_signature(settings.library_root())
                last, pending, fire = _watch_step(last, pending, sig)
                # fire only when the tree differs from what the UI last saw:
                # an app-initiated change (rename/merge/backup) refreshes the
                # library itself, and re-notifying it produces a second,
                # jarring repaint a few seconds after the first. A background
                # rescan already in flight will push library-updated when it
                # settles — piling library-dirty on top just double-paints.
                if fire and sig != self._lib_sig and not self._scan_alive():
                    self._notify_library_changed()
            except Exception:  # noqa: BLE001 - the watcher must never die
                log.exception("library watcher tick failed")

    def _notify_library_changed(self):
        w = self._window
        if w is None:
            return
        try:
            w.evaluate_js("window.BV && BV.state && BV.state.emit && BV.state.emit('library-dirty')")
        except Exception:  # noqa: BLE001 - window mid-teardown at app exit
            pass

    def _notify_library_updated(self):
        """A background rescan settled: the cache is fresh, refetching is now
        a ms-cheap cache read. (library-dirty means the opposite — the tree
        changed and the cache is behind it.)"""
        w = self._window
        if w is None:
            return
        try:
            w.evaluate_js(
                "window.BV && BV.state && BV.state.emit && BV.state.emit('library-updated')")
        except Exception:  # noqa: BLE001 - window mid-teardown at app exit
            pass

    # -- internals -----------------------------------------------------------
    # builders take an optional session so compare can run them against a
    # second backup; caches live on the session object, so both sides cache
    # independently

    def _entry(self, sid: str | None = None) -> dict:
        """The registry entry for `sid`, or the active one when sid is None.
        Endpoints reach sessions ONLY through here / the helpers below."""
        key = sid or self._active_sid
        e = self._sessions.get(key) if key else None
        if e is None:
            raise ApiError("NO_BACKUP", "No backup folder is open")
        return e

    def _need_session(self, sid: str | None = None) -> BackupSession:
        return self._entry(sid)["session"]

    def _open_session(self, p: Path, owner: str = "tab") -> dict:
        """Open (or refresh) a viewer session and make it active. Construction
        happens outside the lock (it walks the whole tree); the registry swap
        is atomic. An existing sid is replaced IN PLACE - same tab identity,
        fresh caches - which is exactly what the compare tab's refresh does on
        purpose. Only NEW sids count against the cap."""
        sess = BackupSession(p)
        sid = str(sess.root)
        with self._sessions_lock:
            e = self._sessions.get(sid)
            if e is None:
                if len(self._sessions) >= self.MAX_OPEN_SESSIONS:
                    raise ApiError(
                        "SESSION_CAP",
                        "%d backups are already open — close one first" % self.MAX_OPEN_SESSIONS)
                e = {"session": sess, "compare": None, "manifest": None,
                     "owner": owner, "window": None}
                self._sessions[sid] = e
            else:
                e["session"] = sess
                e["compare"] = None
            self._active_sid = sid
        m = sess.manifest()
        m["sid"] = sid
        e["manifest"] = m
        return e

    def _drop_session(self, sid: str) -> None:
        with self._sessions_lock:
            e = self._sessions.pop(sid, None)
            if self._active_sid == sid:
                self._active_sid = next(iter(self._sessions), None)
        if e and e.get("window") is not None:
            try:
                e["window"].destroy()
            except Exception:  # noqa: BLE001 - window already tearing down
                pass

    def _release_sessions_under(self, *folders):
        """Drop any open session whose root (or loaded compare root) is inside
        one of `folders`, so a relocate/merge can move that tree (Windows
        blocks renaming a folder a process holds a handle into). The main
        window hears about closed tabs via `sessions-released`."""
        roots = []
        for f in folders:
            if not f:
                continue
            try:
                roots.append(Path(f).resolve())
            except OSError:
                roots.append(Path(f))

        def _under(sess):
            if sess is None:
                return False
            try:
                sr = Path(sess.root).resolve()
            except OSError:
                sr = Path(sess.root)
            return any(sr == r or library._within(sr, r) for r in roots)

        dropped = []
        for sid, e in list(self._sessions.items()):
            if _under(e["session"]):
                dropped.append(sid)
            elif _under(e.get("compare")):
                e["compare"] = None
        for sid in dropped:
            self._drop_session(sid)
        if dropped and self._window is not None:
            try:
                self._window.evaluate_js(
                    "window.BV && BV.state && BV.state.emit && "
                    "BV.state.emit('sessions-released', " + json.dumps(dropped) + ")")
            except Exception:  # noqa: BLE001 - window mid-teardown at app exit
                pass

    def _side_session(self, side: str, sid: str | None = None) -> BackupSession:
        """'a' = the open backup, 'b' = ITS loaded comparison backup (compare
        is per-entry - a diff always pairs the two backups the user paired)."""
        if side == "b":
            b = self._entry(sid).get("compare")
            if b is None:
                raise ApiError("NO_COMPARE", "No comparison backup loaded")
            return b
        return self._need_session(sid)

    def _need_text(self, name: str, s: BackupSession | None = None) -> str:
        s = s or self._need_session()
        text = s.text(name)
        if text is None:
            raise ApiError("MISSING_FILE", f"{name} not found in {s.root.name}")
        return text

    # -- backup lifecycle ------------------------------------------------------

    @_endpoint
    def pick_backup_folder(self):
        import webview

        start = settings.get("last_folder") or ""
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=start if Path(start or ".").exists() else ""
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    @_endpoint
    def open_backup(self, path: str):
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"Not a folder: {path}")
        e = self._open_session(p)
        settings.set_value("last_folder", str(p))
        return e["manifest"]

    @_endpoint
    def get_state(self, sid: str | None = None):
        # called once by the frontend on boot - this log line doubles as proof
        # that html/js loaded and the bridge works (useful for frozen builds).
        # Solo pop-out windows pass their pinned sid.
        log.info("ui booted; sid=%s active=%s", sid, self._active_sid)
        if sid:
            return self._entry(sid)["manifest"]
        if self._active_sid is None:
            return None
        return self._entry()["manifest"]

    # -- session tabs (the browser-style backup tabs) -------------------------

    @_endpoint
    def switch_session(self, sid: str):
        """Make `sid` the main window's session (a tab click), or front its
        pop-out window if that's where it lives - also the dedupe-focus path."""
        e = self._entry(sid)
        if e["owner"] == "popout" and e.get("window") is not None:
            try:
                e["window"].restore()
                e["window"].show()
            except Exception:  # noqa: BLE001 - window backend without restore
                log.exception("could not front the pop-out for %s", sid)
            return {"owner": "popout"}
        with self._sessions_lock:
            self._active_sid = sid
        return {"owner": "tab", "manifest": e["manifest"],
                "compare": e["compare"].manifest() if e.get("compare") else None}

    @_endpoint
    def focus_main_workspace(self):
        """Raise the MAIN window and open the edit workspace there.

        A pop-out has no workspace of its own and never will: the working set
        spans robots while a pop-out is pinned to exactly one, and two windows
        editing one set of drafts is a merge problem nobody asked for. So the
        pop-out's ctrl+e brings you to the workspace instead of bringing a
        second one to you.
        """
        w = self._window
        if w is None:  # no main window bound (a headless probe)
            raise ApiError("NO_MAIN_WINDOW", "there is no main window to raise")
        try:
            w.restore()
            w.show()
        except Exception:  # noqa: BLE001 - window backend without restore
            log.exception("could not front the main window")
        try:
            w.evaluate_js("window.BV && BV.openWorkspace && BV.openWorkspace()")
        except Exception:  # noqa: BLE001 - main window tearing down
            log.exception("could not open the workspace in the main window")
        return True

    @_endpoint
    def close_session(self, sid: str):
        self._entry(sid)  # an unknown sid is an error, never a silent no-op
        self._drop_session(sid)
        return True

    @_endpoint
    def pop_out_backup(self, sid: str):
        """Move a session into its own OS window (solo mode: the new window
        boots pinned to this sid via the ?sid= query and can't open other
        robots). Ownership TRANSFERS - the strip's tab leaves without closing
        the session; the pop-out window's close is what drops it."""
        import webview

        from .app import resource_path

        e = self._entry(sid)
        if e["owner"] == "popout":
            if e.get("window") is not None:  # already out - just front it
                try:
                    e["window"].restore()
                    e["window"].show()
                except Exception:  # noqa: BLE001
                    log.exception("could not front the pop-out for %s", sid)
            return True
        label = (e["manifest"] or {}).get("robot_name") \
            or (e["manifest"] or {}).get("name") or "backup"
        # the sid rides a FRAGMENT, not a query: WebView2 refuses to load a
        # file:// url with a query string (the page never booted - probed),
        # while fragments never reach file resolution. api.js adopts it and
        # the router immediately replaces the hash with a real route.
        url = resource_path("web/index.html").as_uri() + "#sid=" + urllib.parse.quote(sid, safe="")
        w = webview.create_window(
            "backupviewer · " + label, url, js_api=self,
            width=1150, height=800, min_size=(800, 560))
        with self._sessions_lock:
            e["owner"] = "popout"
            e["window"] = w
        # closing the pop-out is what really closes the backup
        w.events.closed += (lambda: self._drop_session(sid))
        return True

    def _popped_window(self, key: str):
        """The window we opened under this key - a CV-X session id or a backup
        sid - or None. The ONLY way a caller names a window: never by title, so
        nothing outside this app can be pointed at."""
        w = self._cvx_windows.get(key)
        if w is not None:
            return w
        e = self._sessions.get(key)
        return e.get("window") if e else None

    # -- window fullscreen -------------------------------------------------------
    # The web Fullscreen API cannot do this. WebView2 GRANTS requestFullscreen
    # (fullscreenElement is set, no error) but only stretches the element inside
    # the same window - and a remote overlay is already inset:0, so the button
    # did visibly nothing. Only the host window can go fullscreen, so the bars
    # ask Python, which owns the toggling and therefore the state.

    @_endpoint
    def toggle_fullscreen(self, spec: dict = None):
        """Take a window borderless-fullscreen and back; returns the new state.
        spec.window is a viewfinder-style key: "main" (default) or the sid of a
        window we popped out."""
        key = (spec or {}).get("window") or "main"
        w = self._window if key == "main" else self._popped_window(key)
        if w is None:
            raise ApiError("NO_WINDOW", "that window is not open")
        w.toggle_fullscreen()
        if key in self._fullscreen:
            self._fullscreen.discard(key)
        else:
            self._fullscreen.add(key)
        return {"fullscreen": key in self._fullscreen}

    def _destroy_popouts(self):
        """Main window closed = app closes: take every pop-out with it - backups
        and popped-out CV-X remotes alike (a stranded remote would hold the
        controller's one remote slot)."""
        windows = [e["window"] for e in list(self._sessions.values())
                   if e.get("window") is not None]
        windows += list(self._cvx_windows.values())
        for w in windows:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001 - already tearing down
                pass

    @_endpoint
    def list_open_sessions(self):
        return [{"sid": sid, "owner": e["owner"],
                 "label": (e["manifest"] or {}).get("robot_name")
                 or (e["manifest"] or {}).get("name") or sid,
                 "robot_id": (e["manifest"] or {}).get("robot_id")}
                for sid, e in self._sessions.items()]

    # -- shared builders (used by endpoints and search) ---------------------------

    def _build_io(self, s: BackupSession | None = None):
        s = s or self._need_session()

        def build():
            cfg_text = s.text("IOCONFIG.DG")
            state_text = s.text("IOSTATE.DG")
            source = "dg"
            if cfg_text is None and state_text is None:
                # backup formats without the IO .DG files still carry the full
                # signal tables inside SUMMARY.DG sections 4/5 (never mixed
                # with .DG sources - all-or-nothing fallback)
                summary = s.text("SUMMARY.DG")
                if summary:
                    state_text, cfg_text = summary_dg.io_section_texts(summary)
                    source = "summary"
            if cfg_text is None and state_text is None:
                raise ApiError(
                    "MISSING_FILE",
                    "No IOCONFIG.DG/IOSTATE.DG and no I/O sections in SUMMARY.DG",
                )
            cfg = io_dg.parse_io_config(cfg_text) if cfg_text else None
            state = io_dg.parse_io_state(state_text) if state_text else None
            out = io_dg.merge_io(cfg, state)
            out["source"] = source
            return out

        return s.cached("io", build)

    def _build_registers(self, kind: str, s: BackupSession | None = None):
        s = s or self._need_session()
        sources = {
            "num": ("NUMREG.VA", registers.parse_numreg),
            "pos": ("POSREG.VA", registers.parse_posreg),
            "str": ("STRREG.VA", registers.parse_strreg),
        }
        if kind not in sources:
            raise ApiError("NOT_FOUND", f"Unknown register kind: {kind}")
        fname, parser = sources[kind]
        text = self._need_text(fname, s)
        return s.cached(f"registers:{kind}", lambda: parser(text))

    def _build_frames(self, s: BackupSession | None = None):
        s = s or self._need_session()
        sysframe = self._need_text("SYSFRAME.VA", s)
        framevar = s.text("FRAMEVAR.VA")
        return s.cached("frames", lambda: frames.build_frames_model(sysframe, framevar))

    def _build_macros(self, s: BackupSession | None = None):
        s = s or self._need_session()

        def build():
            text = s.text("SUMMARY.DG")
            if text:
                parsed = s.cached("summary", lambda: summary_dg.parse_summary(text))
                if parsed["macros"]:
                    return parsed["macros"]
            text = s.text("SYSMACRO.VA")
            if text:
                return macros.parse_macros(text)
            raise ApiError("MISSING_FILE", f"Neither SUMMARY.DG nor SYSMACRO.VA in {s.root.name}")

        return s.cached("macros", build)

    def _build_styles(self, s: BackupSession | None = None):
        s = s or self._need_session()

        def build():
            for fname in ("CELLIO.VA", "SYSTEM.VA"):
                text = s.text(fname)
                if text:
                    table = styles.parse_style_table(text)
                    if table:
                        return table
            return []

        return s.cached("styles", build)

    def _program_texts(self, s: BackupSession | None = None) -> dict[str, str]:
        s = s or self._need_session()

        def build():
            return {p.stem.upper(): read_text(p)
                    for p in sorted(s.program_files, key=lambda p: p.name.upper())}

        return s.cached("progtext", build)

    def _build_call_graph(self, sid: str | None = None):
        s = self._need_session(sid)

        def build():
            try:
                macro_by_name = {m["name"]: m["prog_name"]
                                 for m in self._build_macros(s) if m.get("prog_name")}
            except ApiError:
                macro_by_name = {}
            return callgraph.build_call_graph(self._program_texts(s), macro_by_name)

        return s.cached("callgraph", build)

    def _build_summary(self, s: BackupSession | None = None) -> dict:
        s = s or self._need_session()
        text = self._need_text("SUMMARY.DG", s)
        return s.cached("summary", lambda: summary_dg.parse_summary(text))

    def _build_mastering(self, s: BackupSession | None = None) -> list:
        s = s or self._need_session()
        mast_text = self._need_text("SYSMAST.VA", s)
        return s.cached("mastering", lambda: mastering.parse_mastering(mast_text))

    # -- tab data ---------------------------------------------------------------

    @_endpoint
    def get_overview(self, sid: str | None = None):
        s = self._need_session(sid)

        def build():
            ov = dict(self._build_summary(s))
            wiz_text = s.text("GMWIZLOG.DT")
            ov["gmwizard"] = gmwizlog.parse_gmwizlog(wiz_text) if wiz_text else None
            # SUMMARY.DG truncates the first char of the customization string
            # (controller bug); the wizard log has it intact
            if ov["gmwizard"] and ov["gmwizard"]["header"].get("custo_version"):
                ov["identity"] = dict(ov["identity"])
                ov["identity"]["customization"] = ov["gmwizard"]["header"]["custo_version"]
            try:
                ov["mastering"] = self._build_mastering(s)
            except ApiError:
                ov["mastering"] = []
            return ov

        return s.cached("overview", build)

    @_endpoint
    def get_frames(self, sid: str | None = None, side: str = "a"):
        return self._build_frames(self._side_session(side, sid))

    @_endpoint
    def get_io(self, sid: str | None = None, side: str = "a"):
        return self._build_io(self._side_session(side, sid))

    @_endpoint
    def get_registers(self, kind: str, sid: str | None = None, side: str = "a"):
        return self._build_registers(kind, self._side_session(side, sid))

    @_endpoint
    def get_styles(self, sid: str | None = None):
        return self._build_styles(self._need_session(sid))

    def _build_programs(self, s: BackupSession | None = None):
        s = s or self._need_session()

        def build():
            style_by_prog: dict[str, list[int]] = {}
            for st in self._build_styles(s):
                style_by_prog.setdefault(st["program"].upper(), []).append(st["style"])

            out = []
            seen_stems = set()
            for p in sorted(s.program_files, key=lambda p: p.name.upper()):
                try:
                    h = ls_program.parse_ls_header(read_text(p))
                except Exception:
                    log.exception("header parse failed: %s", p.name)
                    continue
                a = h["attrs"]
                name = h["name"] or p.stem
                seen_stems.add(p.stem.upper())
                out.append({
                    "name": name,
                    "file": p.name,
                    # path relative to the backup root: the edit workspace keys
                    # on it, because a basename collides across robots and a
                    # program may live in a dump subfolder (md_ls/, mdb/)
                    "rel": s.rel(p),
                    "prog_type": h["prog_type"] or "TP",
                    "comment": a.get("comment", ""),
                    "owner": a.get("owner", ""),
                    "create": a.get("create", ""),
                    "modified": a.get("modified", ""),
                    "line_count": a.get("line_count"),
                    "prog_size": a.get("prog_size"),
                    "protect": a.get("protect", ""),
                    "styles": style_by_prog.get(name.upper(), []),
                    "system": a.get("owner", "") == "BACKGRND" or name.startswith("-"),
                    "binary": False,
                })
            # program files that exist only in binary form (.TP/.PC/.MR with no
            # .LS listing) - shown so the program list is truly complete.
            # by_name winners only: the same .tp duplicated across subfolders
            # must list once.
            for fname in sorted(s.by_name):
                p = s.by_name[fname][0]
                ext = p.suffix.upper().lstrip(".")
                if ext not in ("TP", "PC", "MR") or p.stem.upper() in seen_stems:
                    continue
                seen_stems.add(p.stem.upper())
                out.append({
                    "name": p.stem, "file": p.name,
                    "prog_type": ext + " (binary)",
                    "comment": "", "owner": "", "create": "", "modified": "",
                    "line_count": None, "prog_size": p.stat().st_size, "protect": "",
                    "styles": style_by_prog.get(p.stem.upper(), []),
                    "system": p.stem.startswith("-"),
                    "binary": True,
                })
            # KAREL programs (.VR binary + .VA variable twin) - shown as <stem>.PC
            # (the pendant name). Opening one shows its variables, not source.
            for key, kp in sorted(s.karel_programs.items()):
                if key in seen_stems:
                    continue
                out.append({
                    "name": kp["stem"], "file": kp["stem"] + ".PC",
                    "prog_type": "PC", "kind": "karel",
                    "comment": "", "owner": "", "create": "", "modified": "",
                    "line_count": None, "prog_size": kp["va"].stat().st_size, "protect": "",
                    "styles": style_by_prog.get(key, []),
                    "system": False, "binary": False,
                })
            return out

        return s.cached("programs", build)

    @_endpoint
    def get_programs(self, sid: str | None = None, side: str = "a"):
        return self._build_programs(self._side_session(side, sid))

    @_endpoint
    def diff_program(self, file_a: str, file_b: str):
        """Line-aligned diff of a program from the open backup (a) against one
        from the comparison backup (b)."""
        a = self._need_session()
        b = self._side_session("b")

        def load(s, name):
            p = s.find(name)
            if p is None or p not in s.program_files:
                raise ApiError("NOT_FOUND", f"Program not found in {s.root.name}: {name}")
            prog = ls_program.parse_ls_program(read_text(p))
            prog["rel"] = s.rel(p)      # workspace identity for this side
            return prog

        pa = load(a, file_a)
        pb = load(b, file_b)
        out = compare.align_program_lines(pa["body"], pb["body"])
        out["a"] = {"name": pa["name"], "file": file_a, "rel": pa["rel"],
                    "robot": a.robot_name or a.root.name,
                    "comment": pa["attrs"].get("comment", ""), "modified": pa["attrs"].get("modified", "")}
        out["b"] = {"name": pb["name"], "file": file_b, "rel": pb["rel"],
                    "robot": b.robot_name or b.root.name,
                    "comment": pb["attrs"].get("comment", ""), "modified": pb["attrs"].get("modified", "")}
        return out

    @_endpoint
    def get_program(self, file_name: str, sid: str | None = None):
        s = self._need_session(sid)
        p = s.find(file_name)
        if p is None or p not in s.program_files:
            raise ApiError("NOT_FOUND", f"Program not found: {file_name}")

        def build():
            text = read_text(p)
            prog = ls_program.parse_ls_program(text)
            prog["rel"] = s.rel(p)      # workspace identity (see _build_programs)
            graph = self._build_call_graph()
            key = prog["name"].upper() if prog["name"] else p.stem.upper()
            prog["calls"] = graph["calls"].get(key, [])
            prog["called_by"] = graph["called_by"].get(key, [])
            # per-line hop targets so CALL/RUN names + bare macro-name lines
            # become click-to-open in the source viewer
            try:
                macro_by_name = {m["name"]: m["prog_name"]
                                 for m in self._build_macros() if m.get("prog_name")}
            except ApiError:
                macro_by_name = {}
            stems = {st.upper() for st in self._program_texts()}
            prog["hops"] = {str(n): v for n, v in
                            callgraph.line_hops(text, macro_by_name, stems).items()}
            return prog

        return s.cached(f"program:{p.name.upper()}", build)

    # -- edit workspace (path-addressed) ---------------------------------------
    # The viewer is read-only evidence; editing exports modified COPIES to a new
    # folder the user picks and never touches a backup (enforced below).
    #
    # These endpoints address a program by {root, file-relative-to-root} and
    # NEVER touch the session registry: an edit workspace routinely spans more
    # robots than MAX_OPEN_SESSIONS allows, and session parse caches are never
    # evicted. The relative path (not the basename) is the identity, because
    # every robot has a MAIN.LS and a basename would silently edit the wrong
    # robot's program.

    def _ws_root(self, root: str) -> Path:
        try:
            p = Path(root or "").resolve()
        except OSError:
            raise ApiError("BAD_ROOT", "could not resolve that backup folder")
        if not p.is_dir():
            raise ApiError("BAD_ROOT", f"not a folder: {root}")
        if not looks_like_backup(p):
            raise ApiError("BAD_ROOT", f"{p.name} does not look like a backup folder")
        return p

    def _ws_program(self, root: Path, rel: str) -> Path:
        """Resolve a program inside `root`, refusing traversal and anything that
        is not a /PROG-headed .LS (classification is by CONTENT - a bare *.LS
        sweep would drag alarm reports like ERRALL.LS into the workspace)."""
        r = (rel or "").replace("\\", "/").strip("/")
        if not r or ".." in r.split("/"):
            raise ApiError("BAD_FILE", "bad program path")
        try:
            p = (root / r).resolve()
        except OSError:
            raise ApiError("BAD_FILE", "could not resolve that program")
        if not library._within(p, root) or not p.is_file():
            raise ApiError("NOT_FOUND", f"program not found: {rel}")
        if not _is_ls_program(p):
            raise ApiError("BAD_FILE", f"{p.name} is not a TP program (.LS with /PROG)")
        return p

    def _ws_label(self, root: Path, hint: str = "") -> str:
        """An honest robot folder name for the export tree. backup.json is the
        app's own sidecar and the most reliable; then the caller's hint; then
        the folder name (which for a dated snapshot is a timestamp, hence last)."""
        try:
            meta = json.loads((root / "backup.json").read_text(encoding="utf-8"))
            if meta.get("robot"):
                return ftpbackup._safe_name(str(meta["robot"]))
        except (OSError, ValueError):
            pass
        if hint:
            return ftpbackup._safe_name(hint)
        return ftpbackup._safe_name(root.name)

    @_endpoint
    def ws_list_programs(self, root: str):
        """Every TP program in a backup folder, WITHOUT opening a session.
        Duplicate basenames across dump subfolders (md_ls/ vs mdb/ vs root) are
        the same program - the shallowest copy wins, matching the session's own
        priority rule. Returns [{file (relative), name, comment}]."""
        r = self._ws_root(root)
        # walk the \\?\-prefixed form so deep plant trees are reachable, but
        # keep every RELATIVE path measured against that same walk root - the
        # prefix is not in `r`, so relative_to(r) would raise.
        walk = Path(ftpbackup.long_path(str(r)))
        best: dict[str, tuple[int, str, Path]] = {}
        seen = 0
        for p in walk.rglob("*"):
            seen += 1
            if seen > MAX_WS_SCAN_FILES:
                break
            if not p.is_file() or not _is_ls_program(p):
                continue
            rel = p.relative_to(walk).as_posix()
            depth = len(rel.split("/"))
            cur = best.get(p.name.upper())
            if cur is None or depth < cur[0]:
                best[p.name.upper()] = (depth, rel, p)
        out = []
        for key in sorted(best):
            _depth, rel, p = best[key]
            try:
                head = ls_program.parse_ls_header(read_text(p))
            except OSError:
                continue
            out.append({
                "file": rel,
                "name": p.name,
                "comment": head["attrs"].get("comment", ""),
                "prog_type": head.get("prog_type", ""),
            })
        return out

    @_endpoint
    def ws_robot_programs(self, robot_id: str, which: str = "latest"):
        """Every TP program in a library robot's backup, ready to drop into the
        edit workspace: {root, label, programs:[...]}. Resolves the folder with
        library.resolve_open_path (a stale Latest mirror must not make a robot
        unopenable) and opens NO session."""
        e = library.get_robot(robot_id)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        path = library.resolve_open_path(e, which)
        if not path or not Path(path).is_dir():
            raise ApiError("NOT_FOUND",
                           f"backup folder missing: {path or '(no backup on disk)'}")
        r = self._ws_root(path)
        return {
            "root": str(r),
            "label": e.get("robot", "") or r.name,
            "programs": self.ws_list_programs(str(r))["data"],
        }

    @_endpoint
    def ws_find_programs(self, pattern: str):
        """Program-name search across EVERY library robot's saved backup, for
        the workspace's add-many picker: 'KEYPLC' finds S01KEYPLCTRG and
        S62KEYPLCTRG alike. Case-insensitive substring; a '*' in the pattern
        switches to explicit wildcard matching. Opens no sessions - the same
        no-session listing ws_robot_programs uses, per robot. Robots without a
        backup on disk are counted, never silently dropped."""
        pat = str(pattern or "").strip().upper()
        if not pat:
            raise ApiError("BAD_SPEC", "a search pattern is required")

        def match(stem: str) -> bool:
            return (fnmatch.fnmatchcase(stem.upper(), pat) if "*" in pat
                    else pat in stem.upper())

        groups, searched, skipped = [], 0, 0
        for e in library.load()["robots"]:
            path = library.resolve_open_path(e)
            if not path or not Path(path).is_dir():
                skipped += 1
                continue
            searched += 1
            try:
                r = self._ws_root(path)
                progs = [p for p in self.ws_list_programs(str(r))["data"]
                         if match(Path(p["name"]).stem)]
            except Exception:  # noqa: BLE001 - one unreadable backup must not kill the search
                skipped += 1
                continue
            if progs:
                groups.append({"robot_id": e.get("id", ""),
                               "robot": e.get("robot", ""), "line": e.get("line", ""),
                               "root": str(r),
                               "label": e.get("robot", "") or r.name,
                               "programs": progs})
        return {"groups": groups, "searched": searched, "skipped": skipped,
                "total": sum(len(g["programs"]) for g in groups)}

    @_endpoint
    def ws_get_program(self, root: str, file: str):
        """The editor's seed for one program: the body as display text (no line
        numbers, no ';', wrapped statements joined) plus the editable
        attributes. Derived from pristine bytes via ls_edit.split_sections - the
        SAME code the export uses, so what you edit is exactly what re-emits.
        Byte-exact latin-1, NOT get_program's lossy cp1252 `source`."""
        r = self._ws_root(root)
        p = self._ws_program(r, file)
        text = ls_edit.decode_ls(p.read_bytes())
        sections = ls_edit.split_sections(text)
        prog = ls_program.parse_ls_program(text)
        attrs = prog["attrs"]
        return {
            "name": p.name,
            "file": (p.relative_to(r).as_posix()
                     if library._within(p, r) else p.name),
            "body": ls_edit.body_text(sections),
            "attrs": {"owner": attrs.get("owner", ""),
                      "comment": attrs.get("comment", ""),
                      "protect": attrs.get("protect", "")},
            "positions": prog["positions"],
        }

    @_endpoint
    def ws_diff_texts(self, a_text, b_text, normalize=False):
        """Line-aligned diff of two program bodies given as EDITOR TEXT (the
        workspace's display form: one statement per line, no numbers, no ';').
        One endpoint, both workspace diff views:

        - review-your-edits (pristine vs edited buffer): normalize=False -
          same file, same save-time state, so byte differences are real.
        - pane-vs-pane (two robots' programs, live): normalize=True - ref
          comments and the pendant's IO-status display are save-time state,
          so a row differing only there is classified 'equiv', never
          'change' (see compare.align_program_lines).

        Also returns io_status {a, b}: whether each side was saved with the
        pendant's IO-status view ON (any 3-field IO ref in the file). When
        the flags differ, every IO line differs textually and the UI can say
        why instead of crying wolf. Parsing/diffing stays in Python - JS
        never interprets robot files."""
        def body(t):
            t = str(t if t is not None else "")
            if not t:
                return []
            return [{"n": i + 1, "text": ln} for i, ln in enumerate(t.split("\n"))]

        a_body, b_body = body(a_text), body(b_text)
        out = compare.align_program_lines(a_body, b_body, normalize=bool(normalize))
        out["io_status"] = {
            "a": compare.io_status_shown(str(a_text if a_text is not None else "")),
            "b": compare.io_status_shown(str(b_text if b_text is not None else "")),
        }
        return out

    @_endpoint
    def pick_export_folder(self):
        import webview

        start = settings.get("last_export_folder") or settings.library_root()
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=start if Path(start or ".").exists() else ""
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    @_endpoint
    def ws_export(self, edits, dest):
        """Write edited .LS programs to a NEW folder chosen by the user, ONE
        SUBFOLDER PER ROBOT (program names repeat across robots, so a flat drop
        would silently overwrite one robot's work with another's).

        No backup is ever touched: the destination must not be, sit inside, or
        look like ANY of the backups being edited. All-or-nothing - every output
        is built in memory first, then written .part -> os.replace, so a
        half-finished export never appears on disk.

        `edits` = [{"root": <backup folder>, "file": <path relative to root>,
                    "label": <robot name hint, optional>,
                    "save_as": <new program name, optional>,
                    "body": tokens|None, "attrs": {...}|None,
                    "positions": [...]|None}] - each edit part None when
        unchanged. Body tokens: {"ref": i} keeps pristine record i byte-exact
        (renumbered), {"text": s} emits canonically; engine in parsers/ls_edit.py.

        save_as renames: the output file becomes <save_as>.LS and its /PROG
        header is repointed to match, because a header that disagrees with its
        file name is a real defect (tools/restyle.py warns about exactly that).
        Two edits may share a source with different save_as values - that is how
        a duplicate is exported.
        """
        if not edits:
            raise ApiError("NO_EDITS", "nothing to export")
        if not dest:
            raise ApiError("BAD_DEST", "an export folder is required")
        try:
            d = Path(dest).resolve()
        except OSError:
            raise ApiError("BAD_DEST", "could not resolve the export folder")
        if looks_like_backup(d):
            raise ApiError(
                "BAD_DEST",
                "that folder looks like a backup - the viewer never writes into "
                "a backup")
        outputs: list[tuple[Path, bytes]] = []
        for e in edits:
            e = e or {}
            r = self._ws_root(e.get("root", ""))
            if d == r or library._within(d, r):
                raise ApiError(
                    "BAD_DEST",
                    "choose a folder outside the backups you are editing - the "
                    "viewer never writes into a backup")
            p = self._ws_program(r, e.get("file", ""))
            text = ls_edit.decode_ls(p.read_bytes())
            try:
                sections = ls_edit.split_sections(text)
                if e.get("attrs"):
                    sections["prefix"] = ls_edit.apply_attrs(sections["prefix"], e["attrs"])
                if e.get("positions"):
                    sections["suffix"] = ls_edit.apply_positions(
                        sections["suffix"], e["positions"])
                tokens = e.get("body")
                if tokens is None:
                    tokens = [{"ref": i} for i in range(len(sections["records"]))]
                out_text = ls_edit.emit(sections, tokens)
                out_name = p.name
                save_as = (e.get("save_as") or "").strip()
                if save_as:
                    out_text = ls_edit.rename_program(out_text, save_as)
                    stem = save_as[:-3] if save_as.lower().endswith(".ls") else save_as
                    out_name = stem + p.suffix          # keep the source's .ls/.LS case
                data = ls_edit.encode_ls(out_text)
            except ls_edit.LsEncodeError as ex:
                raise ApiError("BAD_CHAR", f"{p.name}: {ex}")
            except ls_edit.LsEditError as ex:
                raise ApiError("BAD_EDIT", f"{p.name}: {ex}")
            target = d / self._ws_label(r, e.get("label", "")) / out_name
            if any(t == target for t, _ in outputs):
                raise ApiError(
                    "NAME_CLASH",
                    f"two programs would both export as {out_name} in "
                    f"{target.parent.name}/ - rename one first")
            outputs.append((target, data))
        written: list[str] = []
        for target, data in outputs:
            os.makedirs(ftpbackup.long_path(str(target.parent)), exist_ok=True)
            part = ftpbackup.long_path(str(target) + ".part")
            with open(part, "wb") as f:
                f.write(data)
            os.replace(part, ftpbackup.long_path(str(target)))
            written.append(target.parent.name + "/" + target.name)
        self._last_export_dest = str(d)
        settings.set_value("last_export_folder", str(d))
        return {"dest": str(d), "files": written, "count": len(written)}

    @_endpoint
    def reveal_export_folder(self, path: str):
        """Open the just-exported folder in Explorer. Guarded to the folder THIS
        session last exported to (open_path's guard is library-root-only)."""
        import subprocess

        last = getattr(self, "_last_export_dest", None)
        if not last:
            raise ApiError("BAD_PATH", "nothing has been exported yet")
        try:
            rp = Path(path or "").resolve()
        except OSError:
            raise ApiError("BAD_PATH", "could not resolve path")
        if str(rp) != last or not rp.is_dir():
            raise ApiError("BAD_PATH", "not the exported folder")
        try:
            os.startfile(str(rp))  # Windows-native; the app only ships on Windows
        except (AttributeError, OSError):
            try:
                subprocess.Popen(["explorer", str(rp)])  # noqa: S607
            except OSError:
                pass
        return str(rp)

    def _karel_records(self, s: BackupSession, stem: str):
        kp = s.karel_programs.get(stem.upper())
        if kp is None:
            raise ApiError("NOT_FOUND", f"PC program not found: {stem}")
        return s.cached(f"karel:{stem.upper()}", lambda: sysvars.records(read_text(kp["va"])))

    @_endpoint
    def get_program_variables(self, stem: str, sid: str | None = None, side: str = "a"):
        """A KAREL (.PC) program's variables, as collapsible trees - shown
        instead of TP source when a PC program is opened."""
        s = self._side_session(side, sid)
        if stem.upper().endswith(".PC"):
            stem = stem[:-3]
        recs = self._karel_records(s, stem)
        return {
            "name": stem + ".PC",
            "stem": stem,
            "records": [sysvars.record_tree(r) for r in recs],
        }

    def _karel_flat(self, s: BackupSession, stem: str) -> dict:
        recs = self._karel_records(s, stem)
        flat: dict[str, str] = {}
        for r in recs:
            flat.update(sysvars.flatten(r))
        return flat

    @_endpoint
    def get_pc_diff_rows(self, stem: str, mode: str = "all"):
        """The differing variables of one PC program pair (for the compare
        report's inline dropdown)."""
        a = self._need_session()
        b = self._side_session("b")
        ig_c, ig_v = mode == "no_comments", mode == "no_values"
        diff = compare.diff_variables(self._karel_flat(a, stem), self._karel_flat(b, stem), ig_c, ig_v)
        rows = diff["rows"][:80]
        return {"name": stem + ".PC", "total": len(diff["rows"]), "rows": rows,
                "truncated": len(diff["rows"]) > len(rows)}

    @_endpoint
    def get_call_tree(self, root: str, depth: int = 6, sid: str | None = None):
        """Expandable call tree rooted at a program; cycles marked, depth-limited."""
        depth = depth or 6   # a solo-shim pad arrives as None
        graph = self._build_call_graph(sid)
        calls = graph["calls"]

        def node(name: str, path: tuple[str, ...], d: int) -> dict:
            edges = calls.get(name.upper(), [])
            n = {"name": name, "exists": name.upper() in calls}
            if name.upper() in path:
                n["cycle"] = True
                return n
            if d <= 0 and edges:
                n["truncated"] = True
                return n
            children = []
            for e in edges:
                child = node(e["target"], path + (name.upper(),), d - 1)
                child["kind"] = e["kind"]
                child["count"] = e["count"]
                children.append(child)
            if children:
                n["children"] = children
            return n

        return node(root.upper(), (), max(1, min(depth, 8)))

    @_endpoint
    def get_alarm_files(self, sid: str | None = None):
        s = self._need_session(sid)
        out = []
        for p in s.alarm_files():
            parsed = self._alarms_for(p.name)
            out.append({"file": p.name, "rows": len(parsed["rows"]), "exported": parsed["exported"]})
        return out

    def _alarms_for(self, name: str, sid: str | None = None) -> dict:
        s = self._need_session(sid)
        text = self._need_text(name, s)
        return s.cached(f"alarms:{name.upper()}", lambda: alarms.parse_alarm_file(text))

    @_endpoint
    def get_alarms(self, file_name: str, offset: int = 0, limit: int = 200, query: str = "", sid: str | None = None):
        offset, limit, query = offset or 0, limit or 200, query or ""   # solo-shim pads
        parsed = self._alarms_for(file_name, sid)
        rows = parsed["rows"]
        if query:
            q = query.lower()
            rows = [
                r for r in rows
                if q in r["code"].lower() or q in r["message"].lower()
                or q in r["datetime"].lower() or q in r["severity"].lower()
            ]
        page = rows[offset:offset + limit]
        return {
            "total": len(parsed["rows"]),
            "filtered": len(rows),
            "offset": offset,
            "rows": page,
            "robot_name": parsed["robot_name"],
            "exported": parsed["exported"],
            "unparsed": len(parsed["unparsed"]),
        }

    @_endpoint
    def get_macros(self, sid: str | None = None, side: str = "a"):
        return self._build_macros(self._side_session(side, sid))

    # -- dcs ------------------------------------------------------------------

    _DCS_FILES = [
        ("DCSVRFY.DG", "verify"),
        ("DCSCHGD1.DG", "change 1"),
        ("DCSCHGD2.DG", "change 2"),
        ("DCSCHGD3.DG", "change 3"),
    ]

    def _dcs_report(self, s: BackupSession, name: str) -> dict:
        text = self._need_text(name, s)
        return s.cached(f"dcs:{name.upper()}", lambda: dcs.parse_dcs_report(text))

    @_endpoint
    def get_dcs_files(self, sid: str | None = None, side: str = "a"):
        """Available DCS reports with their export dates (change history)."""
        s = self._side_session(side, sid)
        out = []
        for fname, kind in self._DCS_FILES:
            if not s.find(fname):
                continue
            rep = self._dcs_report(s, fname)
            out.append({
                "file": fname,
                "kind": kind,
                "date": rep["header"].get("date", ""),
                "counts": rep["counts"],
                "all_signatures_match": rep["all_signatures_match"],
            })
        if not out:
            raise ApiError("MISSING_FILE", "No DCS reports in this backup")
        return out

    @_endpoint
    def get_dcs(self, file_name: str = "DCSVRFY.DG", sid: str | None = None, side: str = "a"):
        return self._dcs_report(self._side_session(side, sid), file_name or "DCSVRFY.DG")

    @_endpoint
    def get_dcs_zones(self, sid: str | None = None, side: str = "a"):
        """Zone geometry for the 3D view: DCSPOS.VA (authoritative) merged
        with the verify report's status/method/TCP; either may be absent."""
        s = self._side_session(side, sid)

        def build():
            pos_text = s.text("DCSPOS.VA")
            vrfy = self._dcs_report(s, "DCSVRFY.DG") if s.find("DCSVRFY.DG") else None
            if pos_text is None and vrfy is None:
                raise ApiError("MISSING_FILE", "No DCSPOS.VA / DCSVRFY.DG in this backup")
            return dcszones.build_zones(pos_text, vrfy)

        return s.cached("dcszones", build)

    # -- robot pose (3D view) -------------------------------------------------

    @_endpoint
    def import_kinematics(self, path: str = ""):
        """Import every robot def's kinematics from a Roboguide 'Robot
        Library' folder into the local registry. With a path (the detected
        install), no dialog; without one, the user picks the folder.
        User-initiated, user's own licensed files - nothing ships with
        the app."""
        folder = path
        if not folder:
            import webview

            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return None
            folder = result[0] if isinstance(result, (list, tuple)) else result
        out = modeldb.import_folder(folder)
        out["counts"] = modeldb.counts()
        return out

    @_endpoint
    def get_robot_pose(self, sid: str | None = None, side: str = "a"):
        """Everything the 3D view needs to pose the arm: the backup's robot
        type (DCS verify report), the matching imported kinematics, the
        CURPOS.DG pose snapshot, and the flange correction measured from
        this backup's own numbers (see kinematics.measure_flange). All
        fields degrade to None - the view falls back honestly."""
        s = self._side_session(side, sid)

        robot_type = ""
        if s.find("DCSVRFY.DG"):
            rep = self._dcs_report(s, "DCSVRFY.DG")
            for sec in rep.get("sections", []):
                if sec.get("id") != "robot-setup":
                    continue
                for row in sec.get("rows", []):
                    if row.get("kind") == "kv" and row.get("key") == "Robot":
                        robot_type = row.get("value", "")
                        break
                break

        entry = modeldb.match(robot_type) if robot_type else None

        q = None
        pose_date = ""
        tool_n = None
        world = None
        cp_text = s.text("CURPOS.DG")
        if cp_text:
            cp = curpos.parse_curpos(cp_text)
            if cp["groups"]:
                g1 = cp["groups"][0]
                q = g1["joints"] or None
                tool_n = g1["tool"]
                world = g1["world"]
                pose_date = cp["date"]

        tool = None
        fr_text = s.text("FRAME.DG")
        if fr_text and tool_n:
            for t in curpos.parse_tool_frames(fr_text):
                if t["n"] == tool_n:
                    tool = t["xyzwpr"]
                    break

        calib = None
        flange_dz = 0.0
        if entry and q and tool and world and len(world) == 6:
            calib = kinematics.measure_flange(entry["kin"], q, tool, world)
            for k in ("dz", "dxy", "ori_err"):
                calib[k] = round(calib[k], 3)
            if calib["ok"]:
                flange_dz = round(calib["dz"], 2)

        return {
            "backup_type": robot_type,
            "matched": bool(entry),
            "type_name": entry["name"] if entry else "",
            "source_kind": entry.get("source_kind", "") if entry else "",
            "imported_date": entry.get("imported", "") if entry else "",
            "validated": entry.get("validated") if entry else None,
            "kin": entry["kin"] if entry else None,
            "counts": modeldb.counts(),
            "q": q, "q_source": "curpos" if q else None,
            "pose_date": pose_date,
            "flange_dz": flange_dz,
            "calib": calib,
            "suggested_library": "" if entry else modeldb.default_library(),
        }

    # -- system vars ----------------------------------------------------------

    def _sysvar_index(self, s: BackupSession):
        """Cached (records list, name->record) for the whole system-variable
        dump - every [*SYSTEM*] record merged out of ALL the backup's .VA files,
        not just SYSTEM.VA (which is only ~70% of them). Built once per session."""
        def build():
            recs = sysvars.merge_system_records(
                (name, read_text(path)) for name, path in s.va_files())
            if not recs:
                raise ApiError("MISSING_FILE", f"No system variables found in {s.root.name}")
            # first record wins a name clash (SYSTEM.VA leads va_files); in
            # practice every [*SYSTEM*] $-name is unique across the dump, so this
            # never fires - build the map BEFORE sorting so the precedence holds
            by_name: dict[str, sysvars.VaRecord] = {}
            for r in recs:
                by_name.setdefault(r.name.upper(), r)
            # one alphabetical run by $-name, like the pendant's system-var list -
            # the source file is provenance (a tag), not a grouping key
            recs = sorted(recs, key=lambda r: r.name.upper())
            return recs, by_name
        return s.cached("sysvar_index", build)

    @_endpoint
    def get_sysvar_records(self, sid: str | None = None, side: str = "a"):
        recs, _ = self._sysvar_index(self._side_session(side, sid))
        return [sysvars.summarize(r) for r in recs]

    @_endpoint
    def get_sysvar(self, name: str, sid: str | None = None, side: str = "a"):
        _, by_name = self._sysvar_index(self._side_session(side, sid))
        rec = by_name.get(name.upper())
        if rec is None:
            raise ApiError("NOT_FOUND", f"System variable not found: {name}")
        return sysvars.record_tree(rec)

    # -- MH valves (material-handling grippers) --------------------------------------

    @_endpoint
    def get_mhvalves(self, sid: str | None = None, side: str = "a"):
        # Each valve's *_SN field is a 1-based index into one of the four signal
        # tables stored in MHGRIPDT (VALVE_TAB/PARTP_TAB/CLAMP_TAB/VMADE_TAB); the
        # parser resolves them to real DI/DO (name + number). See parsers/mhvalves.
        s = self._side_session(side, sid)
        text = self._need_text("MHGRIPDT.VA", s)
        model = s.cached("mhvalves", lambda: mhvalves.build_mhvalves(text))
        # the full, untouched config as a nested tree (every field, headers on
        # headers) - MHGRIPDT (gripper data) + MHGRIPSU (valve setup) if present
        recs = sysvars.records(text)
        su = s.text("MHGRIPSU.VA")
        if su:
            recs = recs + sysvars.records(su)
        return {
            "tools": model["tools"],
            "tables": model["tables"],
            "records": [sysvars.record_tree(r) for r in recs],
        }

    @_endpoint
    def get_magnet(self, sid: str | None = None, side: str = "a"):
        """Magnet end-effector detection + config (MAG*.PC programs, R[800s])."""
        s = self._side_session(side, sid)

        def build():
            numreg_text = s.text("NUMREG.VA")
            numreg = registers.parse_numreg(numreg_text) if numreg_text else []
            return magnet.build_magnet(numreg, list(s.karel_programs))

        return s.cached("magnet", build)

    # -- payload schedules -----------------------------------------------------------

    @_endpoint
    def get_payloads(self, sid: str | None = None, side: str = "a"):
        s = self._side_session(side, sid)
        text = self._need_text("SYMOTN.VA", s)
        return s.cached("payloads", lambda: payloads.build_payloads_model(text))

    # -- compare two backups ---------------------------------------------------------

    def _payloads_for(self, s: BackupSession) -> dict:
        """Payload model for compare; an absent SYMOTN.VA yields an empty model so
        the other side's schedules still diff as two-column added rows."""
        text = s.text("SYMOTN.VA")
        if text is None:
            return {"groups": {}}
        return s.cached("payloads", lambda: payloads.build_payloads_model(text))

    def _side_info(self, s: BackupSession) -> dict:
        m = s.manifest()
        backup_date = ""
        try:
            backup_date = self._build_summary(s)["identity"].get("backup_date", "")
        except ApiError:
            pass
        return {
            "name": m["name"], "path": m["path"], "robot_name": m["robot_name"],
            "f_number": m["f_number"], "backup_type": m["backup_type"],
            "backup_date": backup_date,
        }

    @_endpoint
    def open_compare(self, path: str):
        e = self._entry()  # comparing needs a primary first
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"Not a folder: {path}")
        e["compare"] = BackupSession(p)
        return e["compare"].manifest()

    @_endpoint
    def close_compare(self):
        try:
            self._entry()["compare"] = None
        except ApiError:
            pass  # closing compare with nothing open was always a no-op
        return True

    def _program_body(self, session: BackupSession, stem_upper: str) -> list[dict] | None:
        """Parsed /MN body for a program by stem, cached per session."""
        bodies = session.cached("cmp_bodies", dict)
        if stem_upper not in bodies:
            text = self._program_texts(session).get(stem_upper)
            bodies[stem_upper] = (
                ls_program.parse_ls_program(text)["body"] if text else None
            )
        return bodies[stem_upper]

    @_endpoint
    def get_compare(self, mode: str = "all"):
        e = self._entry()
        a = e["session"]
        b = e.get("compare")
        if b is None:
            raise ApiError("NO_COMPARE", "No comparison backup loaded")
        if mode not in ("all", "no_comments", "no_values"):
            raise ApiError("NOT_FOUND", f"Unknown compare mode: {mode}")
        ig_c = mode == "no_comments"
        ig_v = mode == "no_values"

        def build():
            categories = []
            skipped = []

            def run(cid, label, fn):
                try:
                    result = fn()
                    if result is None:
                        return
                    result.update({"id": cid, "label": label})
                    categories.append(result)
                except ApiError as e:
                    skipped.append({"id": cid, "label": label, "reason": str(e)})
                except Exception as e:  # noqa: BLE001 - one bad category must not kill the report
                    log.exception("compare category %s failed", cid)
                    skipped.append({"id": cid, "label": label, "reason": f"{type(e).__name__}: {e}"})

            def programs_deep():
                result = compare.diff_programs(self._build_programs(a), self._build_programs(b))
                # a "changed" program is only worth showing if its LISTING actually
                # differs. Drop changes that are metadata-only (dates/size/positions)
                # or have no listing to diff - that's the bulk of the clutter.
                kept = []
                for row in result["rows"]:
                    if row["kind"] != "changed":
                        kept.append(row)  # added / removed always shown
                        continue
                    stem = row["name"].upper()
                    body_a = self._program_body(a, stem)
                    body_b = self._program_body(b, stem)
                    if body_a is None or body_b is None:
                        continue  # no listing -> metadata only, not useful
                    n = compare.count_program_line_diffs(body_a, body_b, ignore_comments=ig_c)
                    if not n:
                        continue  # only dates/size/positions changed -> noise
                    row["n_diffs"] = n
                    row["diffable"] = True
                    row["summary"] = f"{n} difference{'s' if n != 1 else ''} detected"
                    kept.append(row)
                # re-finish over the kept rows so counts/rows/truncated agree with
                # each other. diff_programs already capped once, so carry its
                # truncation forward - rows were dropped even if kept is short.
                out = compare.finish(kept)
                out["truncated"] = out["truncated"] or bool(result.get("truncated"))
                return out

            def mastering_audit():
                result = compare.audit_mastering(
                    self._build_mastering(a), self._build_mastering(b))
                # healthy = counts differ = nothing to say; omit the section
                return None if result["ok"] else result

            def pc_deep():
                a_progs, b_progs = set(a.karel_programs), set(b.karel_programs)
                if not (a_progs or b_progs):
                    return None
                rows = []
                for stem in sorted(a_progs | b_progs):
                    kp = a.karel_programs.get(stem) or b.karel_programs.get(stem)
                    name = kp["stem"] + ".PC"
                    if stem not in b_progs:
                        rows.append({"kind": "removed", "name": name, "a": "present", "b": ""})
                    elif stem not in a_progs:
                        rows.append({"kind": "added", "name": name, "a": "", "b": "present"})
                    else:
                        n = compare.count_variable_diffs(
                            self._karel_flat(a, stem), self._karel_flat(b, stem), ig_c, ig_v)
                        if n:
                            rows.append({"kind": "changed", "name": name, "a": "", "b": "",
                                         "summary": f"{n} variable{'s' if n != 1 else ''} differ",
                                         "diffable": True, "pc_stem": kp["stem"]})
                return compare.finish(rows)

            # order tuned for the shop floor: programs first, paperwork last
            run("programs", "programs", programs_deep)
            run("pc", "program variables (PC)", pc_deep)
            run("io", "io", lambda: compare.diff_io(
                self._build_io(a), self._build_io(b), ig_c, ig_v))
            run("frames", "frames", lambda: compare.diff_frames(
                self._build_frames(a), self._build_frames(b), ig_c, ig_v))
            run("payloads", "payloads", lambda: compare.diff_payloads(
                self._payloads_for(a), self._payloads_for(b), ig_c, ig_v))
            run("numreg", "numeric registers", lambda: compare.diff_scalar_registers(
                self._build_registers("num", a), self._build_registers("num", b), "R", ig_c, ig_v))
            run("posreg", "position registers", lambda: compare.diff_posreg(
                self._build_registers("pos", a), self._build_registers("pos", b), ig_c, ig_v))
            run("strreg", "string registers", lambda: compare.diff_scalar_registers(
                self._build_registers("str", a), self._build_registers("str", b), "SR", ig_c, ig_v))
            run("macros", "macros", lambda: compare.diff_macros(
                self._build_macros(a), self._build_macros(b)))
            run("mastering", "mastering check", mastering_audit)
            run("identity", "identity & versions", lambda: compare.diff_kv(
                self._build_summary(a)["identity"], self._build_summary(b)["identity"], [
                    ("robot_model", "robot model"), ("application", "application"),
                    ("version", "version"), ("software_edition", "edition"),
                    ("servo_code", "servo code"), ("dcs_version", "dcs"),
                    ("customization", "customization"), ("teach_pendant", "teach pendant"),
                    ("serial_no", "serial no"),
                ]))
            run("options", "software options", lambda: compare.diff_options(
                self._build_summary(a)["options"], self._build_summary(b)["options"]))

            total = sum(sum(c["counts"].values()) for c in categories)
            return {
                "a": self._side_info(a),
                "b": self._side_info(b),
                "mode": mode,
                "categories": categories,
                "skipped": skipped,
                "total": total,
            }

        # cache on the primary session, keyed by compare root + mode: re-opening
        # either side rebuilds, re-visiting the tab is instant
        return a.cached(f"compare:{b.root}:{mode}", build)

    @_endpoint
    def get_program_diff_rows(self, name: str, mode: str = "all"):
        """The differing lines of one program pair, for the report's inline
        dropdown. Capped; the full picture lives in #pdiff."""
        a = self._need_session()
        b = self._side_session("b")
        stem = name.upper()
        body_a = self._program_body(a, stem)
        body_b = self._program_body(b, stem)
        if body_a is None or body_b is None:
            raise ApiError("NOT_FOUND", f"No listing for {name} on both sides")
        aligned = compare.align_program_lines(body_a, body_b)
        ig_c = mode == "no_comments"
        rows = [r for r in aligned["rows"]
                if r["kind"] != "same" and not (ig_c and compare._comment_only_row(r))]
        capped = rows[:60]
        return {
            "name": name,
            "file_a": name + ".LS",
            "file_b": name + ".LS",
            "total_diffs": len(rows),
            "rows": capped,
            "truncated": len(rows) > len(capped),
        }

    # -- backup-wide search --------------------------------------------------------

    @_endpoint
    def search_backup(self, query: str, sid: str | None = None, side: str = "a"):
        # side="b" searches the compare robot - clicking a signal in a vs-mode
        # pane must search THAT robot, not always the primary one.
        return self._search_session(self._side_session(side, sid), query)

    def _search_session(self, s: BackupSession, query: str):
        # the composition behind backup-wide search, session-explicit so the
        # fleet health scan can run the same search over its own sessions
        def opt(builder, default):
            try:
                return builder()
            except ApiError:
                return default
            except Exception:
                log.exception("search source failed")
                return default

        regs = {}
        for kind in ("num", "pos", "str"):
            regs[kind] = opt(lambda k=kind: self._build_registers(k, s), [])
        io_data = opt(lambda: self._build_io(s), {"signals": []})
        return search_mod.search_backup(
            query,
            program_texts=self._program_texts(s),
            io_signals=io_data["signals"],
            registers=regs,
            frames_model=opt(lambda: self._build_frames(s), None),
            macros=opt(lambda: self._build_macros(s), []),
            file_names=[s.rel(p) for p in s.files.values()],
        )

    # -- raw files ----------------------------------------------------------------

    # extensions we know on sight - only unknown ones get content-sniffed,
    # so list_files doesn't open 800+ files
    _TEXT_EXTS = {"LS", "VA", "DG", "DT", "CM", "XML", "CSV", "STM", "LOG", "TXT", "HTM", "HTML"}
    _BINARY_EXTS = {"TP", "VR", "SV", "PMC", "ZIP", "JPG", "JPEG", "PNG", "DAT", "DF", "IO", "MR", "PC", "BMP"}

    @_endpoint
    def list_files(self, sid: str | None = None):
        s = self._need_session(sid)

        def build():
            out = []
            for name in sorted(s.files):
                p = s.files[name]
                stat = p.stat()
                ext = p.suffix.upper().lstrip(".")
                if ext in self._TEXT_EXTS:
                    binary = False
                elif ext in self._BINARY_EXTS:
                    binary = True
                else:
                    binary = is_binary(p) if stat.st_size else False
                out.append({
                    "name": p.name,
                    "rel": s.rel(p),
                    "ext": ext,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "binary": binary,
                })
            return out

        return s.cached("files", build)

    @_endpoint
    def get_file(self, name: str, sid: str | None = None):
        s = self._need_session(sid)
        p = s.find(name)
        if p is None:
            raise ApiError("NOT_FOUND", f"File not found: {name}")
        size = p.stat().st_size
        if size and is_binary(p):
            data = p.read_bytes()[:HEX_PREVIEW_BYTES]
            lines = []
            for off in range(0, len(data), 16):
                chunk = data[off:off + 16]
                hexpart = " ".join(f"{b:02x}" for b in chunk)
                asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{off:08x}  {hexpart:<47}  {asciipart}")
            return {"kind": "hex", "name": p.name, "rel": s.rel(p), "size": size,
                    "text": "\n".join(lines), "truncated": size > HEX_PREVIEW_BYTES}
        text = read_text(p)
        truncated = False
        if len(text) > MAX_TEXT_BYTES:
            text = text[:MAX_TEXT_BYTES]
            truncated = True
        return {"kind": "text", "name": p.name, "rel": s.rel(p), "size": size,
                "text": text, "truncated": truncated}

    # -- matrox camera photos --------------------------------------------------------
    # A Matrox DA camera saves each inspection as a jpg (preview) + png (full) +
    # txt (metadata) triple under Documents/.../SavedImages/<date>/. get_photos
    # groups the triples, parses the sidecar (pass/fail, camera identity, per-tool
    # results), and returns them newest-first; get_image streams one image as a
    # base64 data-URI (the reliable path under pywebview's private-mode CSP).

    def _camera_session(self, camera_id: str) -> BackupSession:
        """Open (and cache) a library camera's latest backup as a session, so a
        robot's Cameras tab can show a linked camera's photos without making it
        the primary open backup. Cached on the mirror's signature (path +
        backup.json mtime): the Latest/ path never changes across backups, so a
        fresh pull would otherwise keep serving the previous session's photos."""
        e = library.get_robot(camera_id)
        if e is None:
            raise ApiError("NOT_FOUND", "camera not in library")
        path = e.get("latest_path", "")
        if not path or not Path(path).is_dir():
            raise ApiError("NO_BACKUP", f"{e.get('robot', 'camera')} has no backup yet")
        p = Path(path)
        try:
            marker = p / "backup.json"
            sig = (marker if marker.exists() else p).stat().st_mtime_ns
        except OSError:
            sig = 0
        cached = self._camera_sessions.get(camera_id)
        if cached is None or cached[0] != str(p) or cached[1] != sig:
            sess = BackupSession(p)
            self._camera_sessions[camera_id] = (str(p), sig, sess)
            return sess
        return cached[2]

    def _photos_data(self, s: BackupSession):
        """The photos payload for whichever kind of camera this backup came off.
        Matrox saved images win when a backup somehow has both, since those carry
        a parsed pass/fail report and a CV-X master does not."""
        if not s.saved_image_files() and s.cvx_image_files():
            return self._cvx_photos_data(s)
        return self._mtx_photos_data(s)

    def _cvx_photos_data(self, s: BackupSession):
        """A CV-X's images, paired into one record per scene.

        A camera stores each taught program (and each logged trigger) as TWO
        files - a grayscale photo and a height map of the same moment - so they
        are shown as one photo with both halves attached, and the tab crossfades
        between them. Kind comes from the file header, never the name; see
        parsers/cvx_image."""
        def build():
            groups: dict[str, dict] = {}
            for p in s.cvx_image_files():
                rel = s.rel(p)
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(64)
                    size = p.stat().st_size
                except OSError:
                    continue
                kind = cvx_image.header_kind(head)
                if not kind:
                    continue
                g = groups.setdefault(cvx_image.pair_key(rel), {"files": {}})
                g["files"][kind] = {"rel": rel, "name": p.name, "size": size}

            photos = []
            for key, g in groups.items():
                got = g["files"]
                shown = got.get(cvx_image.INTENSITY) or got.get(cvx_image.HEIGHT)
                if not shown:
                    continue
                rel = shown["rel"]
                parts = rel.split("/")
                # .../cv-x/setting/<program>/[<sub>/]<name> - the program number is
                # the folder right under setting/, whatever nests below it
                program = ""
                for i, part in enumerate(parts[:-1]):
                    if part.lower() == "setting" and i + 1 < len(parts) - 1:
                        program = parts[i + 1]
                        break
                info = cvx_image.label(rel)
                verdict = info["verdict"]
                rows = [["program", program], ["camera", info["cam"]],
                        ["captured", info["stamp"]], ["sequence", info["seq"]],
                        ["result", verdict]]
                for kind in (cvx_image.INTENSITY, cvx_image.HEIGHT):
                    f = got.get(kind)
                    if f:
                        rows.append([kind, f"{f['name']} · {f['size'] // 1000} kB"])
                rec = {
                    "name": shown["name"],
                    "date": info["stamp"][:10] or (f"program {program}" if program else ""),
                    "thumb": rel,
                    "full": rel,
                    "txt": "",
                    # OK/NG is the camera's own verdict, mapped onto the pass/fail
                    # the grid already colours by; the raw token stays in the rows
                    "result": {"OK": "Pass", "NG": "Fail"}.get(verdict, ""),
                    "timestamp": info["stamp"],
                    "camera": {}, "recipe": {}, "tools": [],
                    "sections": [{"title": "image", "rows": [
                        {"key": k, "value": v} for k, v in rows if v]}],
                    "_sort": (info["stamp"], program, key),
                }
                if got.get(cvx_image.HEIGHT) and got.get(cvx_image.INTENSITY):
                    rec["overlay"] = {
                        "base": got[cvx_image.INTENSITY]["rel"], "base_label": "greyscale",
                        "top": got[cvx_image.HEIGHT]["rel"], "top_label": "height",
                    }
                photos.append(rec)

            photos.sort(key=lambda x: x.pop("_sort"), reverse=True)
            return {"photos": photos, "count": len(photos), "camera": {}}

        return s.cached("cvx_photos", build)

    def _mtx_photos_data(self, s: BackupSession):
        """Thin wrapper: the grouping + record shaping is the parser's
        (mtx_saved_image.group_photo_files / photo_record); this layer owns the
        session index, sidecar reads and file stats."""
        def build():
            by_rel = {s.rel(p): p for key, p in s.files.items()
                      if "SAVEDIMAGES/" in key}
            photos = []
            for g in mtx_saved_image.group_photo_files(by_rel).values():
                info: dict = {}
                if g.get("txt"):
                    try:
                        info = mtx_saved_image.parse_saved_image(read_text(by_rel[g["txt"]]))
                    except Exception:  # noqa: BLE001 - a bad sidecar must not sink the grid
                        log.exception("saved-image sidecar parse failed: %s", g.get("txt"))
                img_p = by_rel.get(g.get("jpg") or g.get("png") or "")
                try:
                    mtime = int(img_p.stat().st_mtime) if img_p else 0
                except OSError:
                    mtime = 0
                rec = mtx_saved_image.photo_record(g, info, mtime)
                if rec is not None:   # None = a stray sidecar with no image
                    photos.append(rec)
            photos.sort(key=lambda x: x.pop("_sort"), reverse=True)
            camera = photos[0]["camera"] if photos else {}
            return {"photos": photos, "count": len(photos), "camera": camera}

        return s.cached("photos", build)

    def _image_data(self, s: BackupSession, rel: str):
        p = s.find(rel)
        if p is None:
            raise ApiError("NOT_FOUND", f"image not found: {rel}")
        cvx = self._cvx_image_data(p, s)
        if cvx is not None:
            return cvx
        ext = p.suffix.lower()
        mime = _IMAGE_MIME.get(ext)
        if mime is None:
            raise ApiError("NOT_IMAGE", f"not a viewable image: {p.name}")
        size = p.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ApiError("TOO_BIG", f"image too large to preview ({size // 1_000_000} MB)")
        import base64
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        return {"rel": s.rel(p), "name": p.name, "mime": mime, "size": size,
                "data_uri": f"data:{mime};base64,{data}"}

    def _cvx_image_data(self, p, s: BackupSession):
        """A CV-X BMP rendered to a PNG the browser can actually show, or None
        when this file is not one.

        Two reasons this cannot be served as-is like a Matrox jpg: a height BMP
        is packed range data that renders as grey mush in any image viewer, and
        even the intensity halves are 8-12 MB - over MAX_IMAGE_BYTES, and far
        past what belongs in a data-URI. Decoding decimates to DISPLAY_MAX_DIM
        as it goes, so the work is proportional to what is shown, not to what is
        on disk."""
        if p.suffix.lower() != ".bmp":
            return None
        # scope: only files the CV-X index vouches for (cv-x/ tree, header
        # checked). The decode itself would accept virtually any plain 24-bpp
        # BMP - a Matrox SavedImages preview, a stray screenshot in a backup -
        # and serve it as false-color height garbage. The packed-range claim
        # is proven for the camera's own files, so it applies to exactly those.
        if p not in s.cvx_image_files():
            return None
        try:
            data = p.read_bytes()
        except OSError as e:
            raise ApiError("UNREADABLE", f"cannot read {p.name}: {e}") from e
        try:
            kind = cvx_image.probe(data)["kind"]
            w, h, rgba = cvx_image.to_rgba(data, DISPLAY_MAX_DIM)
        except cvx_image.BadImage:
            return None                      # a plain .bmp: let the raw path have it
        # local import: screengrab pulls in ctypes.wintypes at module scope, and
        # this path has no business being Windows-only just to reuse a PNG writer
        from .screengrab import png_encode
        png = png_encode(w, h, rgba)
        return {"rel": s.rel(p), "name": p.name, "mime": "image/png",
                "size": len(png), "kind": kind, "width": w, "height": h,
                "data_uri": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}

    @_endpoint
    def get_photos(self, sid: str | None = None):
        return self._photos_data(self._need_session(sid))

    @_endpoint
    def get_image(self, rel: str, sid: str | None = None):
        return self._image_data(self._need_session(sid), rel)

    # -- a robot's linked cameras (its Cameras tab) --------------------------------

    @_endpoint
    def get_camera_photos(self, camera_id: str):
        """Full photos payload for a linked camera's latest backup (same shape as
        get_photos) - drives the Photos view inside a robot's Cameras tab."""
        return self._photos_data(self._camera_session(camera_id))

    @_endpoint
    def get_camera_image(self, camera_id: str, rel: str):
        return self._image_data(self._camera_session(camera_id), rel)

    @_endpoint
    def lib_robot_cameras(self, robot_id: str):
        """The cameras linked to a robot, each with a light summary (newest photo
        thumb + pass/fail) for the Cameras tab list."""
        out = []
        for cam in library.cameras_for_robot(robot_id):
            row = {
                "id": cam["id"], "name": cam.get("robot", ""),
                "device_type": cam.get("device_type", ""), "model": cam.get("model", ""),
                "ips": cam.get("ips", []), "last_backup": cam.get("last_backup", ""),
                "has_backup": bool(cam.get("latest_path")),
                "photos": 0, "result": "", "thumb": "", "timestamp": "",
            }
            try:
                data = self._photos_data(self._camera_session(cam["id"]))
                row["photos"] = data["count"]
                if data["photos"]:
                    top = data["photos"][0]
                    row["result"] = top.get("result", "")
                    row["thumb"] = top.get("thumb", "")
                    row["timestamp"] = top.get("timestamp", "") or top.get("date", "")
            except ApiError:
                pass   # camera has no backup yet - listed, just no preview
            out.append(row)
        return {"cameras": out}

    @_endpoint
    def lib_link_camera(self, camera_id: str, robot_id: str = ""):
        """Link a camera to the robot it inspects (robot_id='' unlinks)."""
        e = self._claim_tree_changes(
            lambda: library.link_camera(camera_id, robot_id))
        if e is None:
            raise ApiError("NOT_FOUND", "camera not in library")
        return e

    @_endpoint
    def lib_auto_link(self):
        """Auto-link unlinked cameras to robots by matching the station+robot in
        their names. Manual links are preserved. Returns linked/ambiguous/unmatched."""
        return self._claim_tree_changes(library.auto_link_cameras)

    # -- CV-X live remote-desktop (screen mirror + mouse) -----------------------------
    # A Keyence CV-X controller's live screen, mirrored over its custom TCP protocol
    # (cvx_remote.py). Frames stream to the frontend as MJPEG over a localhost HTTP
    # server; mouse events come back through the bridge. Wholly separate from the
    # CV-X anon-FTP backup path. One session per controller.

    def _cvx_frame_server(self):
        if self._cvx_server is None:
            self._cvx_server = cvx_remote.start_frame_server(self._cvx)
        return self._cvx_server

    @_endpoint
    def cvx_remote_start(self, spec: dict):
        """Open a live remote-desktop session to a CV-X at spec['ip']; returns a
        session id + the MJPEG stream URL to point an <img> at."""
        ip = _require_ip(spec)
        sess = cvx_remote.CvxRemoteSession(ip)
        if not sess.start():
            raise ApiError("CVX_CONNECT", sess.error or "could not connect to the camera")
        sid = uuid.uuid4().hex
        self._cvx[sid] = sess
        port = self._cvx_frame_server().server_address[1]
        return {"session_id": sid, "stream_url": f"http://127.0.0.1:{port}/cvx/{sid}",
                "screen": {"w": cvx_remote.SCREEN_W, "h": cvx_remote.SCREEN_H}}

    @_endpoint
    def cvx_remote_status(self, session_id: str):
        sess = self._cvx.get(session_id)
        if sess is None:
            raise ApiError("NO_SESSION", "unknown remote session")
        return {"alive": sess.alive, "frames": sess.frames,
                "handshake_done": sess.handshake_done, "error": sess.error}

    @_endpoint
    def cvx_remote_mouse(self, session_id: str, event_id: int, x: int, y: int,
                         seq: int | None = None):
        """seq (the overlay always sends it) reorders bridge calls back into
        gesture order - pywebview runs each call on its own thread."""
        sess = self._cvx.get(session_id)
        if sess is None:
            raise ApiError("NO_SESSION", "unknown remote session")
        if seq is None:
            sess.send_mouse(int(event_id), int(x), int(y))
        else:
            sess.queue_mouse(int(seq), int(event_id), int(x), int(y))
        return True

    @_endpoint
    def cvx_remote_reload(self, session_id: str):
        """Hang up and dial the same camera again, KEEPING the session id - so
        the overlay (and a window popped out around it) never has to re-key.
        Strictly in that order: the controller holds one remote slot, and it
        only frees when we let go."""
        old = self._cvx.pop(session_id, None)
        if old is None:
            raise ApiError("NO_SESSION", "unknown remote session")
        ip = old.ip
        old.stop()
        time.sleep(0.4)   # let the controller free its one remote slot before redialing
        sess = cvx_remote.CvxRemoteSession(ip)
        if not sess.start():
            raise ApiError("CVX_CONNECT", sess.error or "could not reconnect to the camera")
        self._cvx[session_id] = sess
        port = self._cvx_frame_server().server_address[1]
        return {"session_id": session_id, "ip": ip,
                "stream_url": f"http://127.0.0.1:{port}/cvx/{session_id}",
                "screen": {"w": cvx_remote.SCREEN_W, "h": cvx_remote.SCREEN_H}}

    @_endpoint
    def cvx_remote_info(self, session_id: str):
        """The connection facts for a session that is ALREADY open - same shape
        as cvx_remote_start, minus the connecting. A remote popped into its own
        window adopts the session this way instead of dialling the controller
        again (it only has one remote slot)."""
        sess = self._cvx.get(session_id)
        if sess is None:
            raise ApiError("NO_SESSION", "unknown remote session")
        port = self._cvx_frame_server().server_address[1]
        return {"session_id": session_id, "ip": sess.ip,
                "stream_url": f"http://127.0.0.1:{port}/cvx/{session_id}",
                "screen": {"w": cvx_remote.SCREEN_W, "h": cvx_remote.SCREEN_H}}

    @_endpoint
    def cvx_remote_window(self, spec: dict):
        """Move a live CV-X remote (spec.session_id) into its own OS window: the
        app boots there pinned to that session (#cvx= fragment) and adopts it,
        so the picture and the mouse keep working with no reconnect. The caller
        closes its own overlay - ownership TRANSFERS, and closing the window is
        what stops the session."""
        import webview

        from .app import resource_path

        sid = (spec or {}).get("session_id") or ""
        if sid not in self._cvx:
            raise ApiError("NO_SESSION", "unknown remote session")
        w = self._cvx_windows.get(sid)
        if w is not None:                      # already out - just front it
            try:
                w.restore()
                w.show()
            except Exception:  # noqa: BLE001 - window backend without restore
                log.exception("could not front the CV-X window for %s", sid)
            return {"title": w.title}
        label = ((spec or {}).get("label") or "").strip() or self._cvx[sid].ip
        title = f"CV-X remote · {label}"
        url = (resource_path("web/index.html").as_uri()
               + "#cvx=" + urllib.parse.quote(sid, safe="")
               + "&label=" + urllib.parse.quote(label, safe=""))
        w = webview.create_window(title, url, js_api=self,
                                  width=1100, height=880, min_size=(640, 520))
        self._cvx_windows[sid] = w
        # closing the window is what really ends the remote session. The hook
        # resolves the sid by REVERSE lookup at close time, not by capture: a
        # failed reload re-dials under a new id and rebinds the registry, and a
        # captured id would then stop the dead session instead of the live one.
        w.events.closed += (lambda: self._close_cvx_window_obj(w))
        return {"title": title}

    @_endpoint
    def cvx_remote_window_rebind(self, old_sid: str, new_sid: str):
        """A pop-out whose reload failed re-dials under a NEW session id; the
        window registry (and the window's fullscreen state) must follow it, or
        closing the window stops nothing and the camera's single remote slot
        stays held until app exit."""
        if new_sid not in self._cvx:
            raise ApiError("NO_SESSION", "unknown remote session")
        w = self._cvx_windows.pop(old_sid, None)
        if w is None:
            raise ApiError("NOT_FOUND", "that window is not registered")
        self._cvx_windows[new_sid] = w
        if old_sid in self._fullscreen:
            self._fullscreen.discard(old_sid)
            self._fullscreen.add(new_sid)
        return True

    def _close_cvx_window_obj(self, w):
        sid = next((k for k, v in self._cvx_windows.items() if v is w), None)
        if sid is not None:
            self._close_cvx_window(sid)

    @_endpoint
    def cvx_remote_window_close(self, session_id: str):
        """Close a popped-out CV-X window from inside it (its own ✕). Destroying
        the window fires _close_cvx_window, which is what stops the session."""
        w = self._cvx_windows.get(session_id)
        if w is None:                       # never made it out - just hang up
            self._close_cvx_window(session_id)
            return True
        try:
            w.destroy()
        except Exception:  # noqa: BLE001 - already gone
            self._close_cvx_window(session_id)
        return True

    def _close_cvx_window(self, sid: str):
        self._cvx_windows.pop(sid, None)
        self._fullscreen.discard(sid)      # the window is gone, so is its state
        sess = self._cvx.pop(sid, None)
        if sess is not None:
            sess.stop()

    @_endpoint
    def cvx_remote_stop(self, session_id: str):
        sess = self._cvx.pop(session_id, None)
        if sess is not None:
            sess.stop()
        return True

    # -- Matrox live remote (the camera's own web UI) ---------------------------------
    # A Matrox camera is operated through the web page it serves on port 80, so
    # "remote" = that page. Preferred: embed it in an in-app overlay (iframe).
    # Some pages refuse framing (X-Frame-Options / CSP frame-ancestors), so the
    # probe reports embeddability and the fallback opens a separate app window.

    @_endpoint
    def mtx_remote_start(self, spec: dict):
        """Probe http://<ip>/ - is the camera's web page up, and may we embed it?
        Returns {url, embeddable, pages} where pages are the portal's
        DesignAssistant operator page(s), scraped so the viewer can show them as
        in-app tabs instead of letting the portal pop a browser window. An HTTP
        error status (401 login etc) still counts as up; only a dead socket
        raises."""
        ip = _require_ip(spec)
        url = f"http://{ip}/"
        try:
            status, headers, final, body = _probe_http(url)
        except OSError as e:
            raise ApiError(
                "MTX_CONNECT",
                f"no web page answered at {url} - is the camera on this network? ({e})")
        h = {k.lower(): v for k, v in headers.items()}
        xfo = (h.get("x-frame-options") or "").strip().lower()
        csp = (h.get("content-security-policy") or "").lower()
        embeddable = xfo in ("", "allowall") and "frame-ancestors" not in csp
        pages = mtx_portal.find_da_pages(ip, body)
        if not pages:
            # portal home didn't name any operator page - try the DA root itself
            try:
                pages = mtx_portal.find_da_pages(ip, _probe_http(f"http://{ip}/DesignAssistant/")[3])
            except OSError:
                pass
        return {"url": final or url, "embeddable": embeddable, "status": status,
                "pages": pages}

    @_endpoint
    def mtx_remote_window(self, spec: dict):
        """Open a camera web page in its own app window (the fallback when the
        page can't be embedded, or on user request). spec.url may name a specific
        page, but only one served by that same camera."""
        ip = _require_ip(spec)
        label = (spec.get("label") or "").strip() or ip
        url = f"http://{ip}/"
        want = (spec.get("url") or "").strip()
        if want:
            import urllib.parse
            p = urllib.parse.urlsplit(want)
            if p.scheme in ("http", "https") and p.hostname == ip:
                url = want
        import webview   # pywebview supports create_window after start()
        webview.create_window(f"MTX remote · {label}", url, width=1200, height=850)
        return True

    # -- phone live view (scan a QR, the phone shows the camera's frame) --------------
    # For focus/aim work AT the camera: the phone in your hand shows the same
    # HMI frame the multicam wall polls, relayed by the laptop so the phone
    # only needs a route to the laptop (hotspot or wifi), never to the camera
    # VLAN. Posture (token gates, single-flight camera fetch, off by default)
    # lives in phoneview.py.

    @_endpoint
    def phone_view_start(self, spec: dict):
        """Share camera spec.ip with phones. Boots the relay if needed, mints
        (or rejoins) the camera's share, and returns {token, port, urls} where
        urls lists every address this machine answers on, most phone-reachable
        first: [{ip, url, kind}] with kind hotspot / lan / camera network."""
        ip = _require_ip(spec)
        label = (spec.get("label") or "").strip() or ip
        if self._phone_share is None:
            self._phone_share = phoneview.PhoneShare()
        try:
            r = self._phone_share.start_session(ip, label)
        except OSError as e:
            raise ApiError("PHONE_VIEW", f"could not start the share server: {e}")
        urls = phoneview.lan_urls(ip, r["port"], r["token"])
        if not urls:
            raise ApiError("PHONE_VIEW",
                           "this machine has no reachable address - is any network up?")
        return {"token": r["token"], "port": r["port"], "urls": urls}

    @_endpoint
    def phone_view_qr(self, spec: dict):
        """QR matrix for a share URL: {size, rows} with rows as "0110..."
        strings, 1 = dark. Renders only URLs the running share actually
        serves - this is the share's QR, not a general QR maker."""
        text = ((spec or {}).get("text") or "").strip()
        share = self._phone_share
        p = urllib.parse.urlsplit(text)
        tokens = {s["token"] for s in share.status()["sessions"]} if share else set()
        if not (share and p.scheme == "http" and p.port == share.port
                and p.path in {f"/v/{t}" for t in tokens}):
            raise ApiError("BAD_SPEC", "not an active share URL")
        matrix = qr.encode(text)
        return {"size": len(matrix), "rows": ["".join(map(str, row)) for row in matrix]}

    @_endpoint
    def phone_view_stop(self, spec: dict = None):
        """Stop one share (spec.token) or every share (no token). The relay
        server stops with the last share. Returns how many remain."""
        if self._phone_share is None:
            return 0
        return self._phone_share.stop_session((spec or {}).get("token"))

    # -- the window viewfinder (mirror one of our windows to a phone) -----------------
    # Jake's "window to the phone", simplest form: the phone mirrors whatever a
    # window of ours shows - the app window with a camera remote up, a popped-out
    # backup, a popped-out CV-X. No rectangle to pick: grab that window's client
    # area live (it follows if you move or resize it) and the phone shows exactly
    # that. The caller names the window it is IN by key, never by title: only
    # windows this app created can be mirrored, never some other app's.

    _MAIN_TITLE = "Backup Viewer"             # app.py's create_window title

    def _window_title(self, key: str | None) -> str:
        """Resolve a viewfinder window key to the title screengrab looks up.
        None/"main" is the app window; anything else must be a session WE popped
        out (a backup sid or a CV-X session id)."""
        if not key or key == "main":
            # the LIVE title, not the constant: FindWindowW is exact-match and
            # a hardcoded copy already went stale once (the v1.4 branding
            # rename silently broke the main-window mirror)
            try:
                return self._window.title or self._MAIN_TITLE
            except Exception:  # noqa: BLE001 - window backend without .title
                return self._MAIN_TITLE
        w = self._popped_window(key)
        if w is None:
            raise ApiError("PHONE_VIEW", "that window is not open")
        try:
            return w.title
        except Exception:  # noqa: BLE001 - window backend without .title
            raise ApiError("PHONE_VIEW", "could not identify that window") from None

    @_endpoint
    def viewfinder_start(self, spec: dict = None):
        """Mirror one of our windows (spec.window: "main" by default, or the sid
        of a popped-out backup / CV-X remote) to phones: boots the relay, mints
        (or rejoins) THE window share pointed at it, and returns {token, port,
        urls, mirroring} - mirroring names the window the phone now shows. No
        picker; the QR is ready to scan immediately."""
        if self._phone_share is None:
            self._phone_share = phoneview.PhoneShare()
        title = self._window_title((spec or {}).get("window"))
        if not screengrab.window_is_open(title):
            raise ApiError("PHONE_VIEW", f"could not find the '{title}' window to mirror")
        try:
            r = self._phone_share.start_window_session(
                title, lambda: screengrab.grab_window_png(title))
        except OSError as e:
            raise ApiError("PHONE_VIEW", f"could not start the share server: {e}")
        urls = phoneview.lan_urls(None, r["port"], r["token"])
        if not urls:
            raise ApiError("PHONE_VIEW",
                           "this machine has no reachable address - is any network up?")
        return {"token": r["token"], "port": r["port"], "urls": urls,
                "mirroring": title}

    @_endpoint
    def phone_view_status(self):
        """The relay right now: {running, port, sessions:[{token, ip, label,
        phones, pulls, last_pull_ms, frame_age_ms, fetch_err}]}."""
        if self._phone_share is None:
            return {"running": False, "port": None, "sessions": []}
        return self._phone_share.status()

    # -- the firewall helper ("server stopped responding" = blocked port) -------------
    # When the phone reaches the laptop but the page times out, it's almost
    # always the Windows Firewall dropping inbound TCP on the network profile
    # the phone is on (e.g. a rule scoped to Public while the hotspot is
    # Private). The fix is a one-time inbound-allow rule for the phone-view
    # port range. The app can't self-elevate, so the "add" path spawns an
    # elevated PowerShell (UAC); the command is also shown for copy/paste.

    _FW_RULE_NAME = "BackupViewer phone view"

    def _fw_port_range(self) -> str:
        return f"{phoneview.PORT_BASE}-{phoneview.PORT_BASE + phoneview.PORT_TRIES - 1}"

    def _fw_command(self) -> str:
        """The exact rule-adding command, shown in the UI for copy/paste."""
        return ("New-NetFirewallRule -DisplayName '" + self._FW_RULE_NAME +
                "' -Direction Inbound -Action Allow -Protocol TCP -LocalPort " +
                self._fw_port_range() + " -Profile Any")

    @_endpoint
    def phone_view_firewall_status(self):
        """Whether our inbound-allow rule exists, plus the exact command to add
        it (single source of truth for the UI's copy button). A non-elevated
        read - no UAC."""
        present = False
        probe = ("if (Get-NetFirewallRule -DisplayName '" + self._FW_RULE_NAME +
                 "' -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", probe],
                               capture_output=True, text=True, timeout=10,
                               creationflags=_CREATE_NO_WINDOW)
            present = "yes" in (r.stdout or "").lower()
        except (OSError, subprocess.SubprocessError):
            pass   # can't read it - the UI just offers the command anyway
        return {"rule_present": present, "command": self._fw_command(),
                "port_range": self._fw_port_range()}

    @_endpoint
    def phone_view_firewall_fix(self):
        """Add the inbound-allow rule for the phone-view port range, elevating
        via UAC (the app isn't admin). Spawns an elevated PowerShell that
        replaces-then-adds the rule; the user approves the Windows prompt.
        Returns {launched}; the UI re-checks status to confirm approval."""
        inner = ("Remove-NetFirewallRule -DisplayName '" + self._FW_RULE_NAME +
                 "' -ErrorAction SilentlyContinue; " + self._fw_command() +
                 " | Out-Null")
        enc = base64.b64encode(inner.encode("utf-16-le")).decode()
        outer = ("Start-Process powershell -Verb RunAs -WindowStyle Hidden "
                 "-ArgumentList '-NoProfile','-EncodedCommand','" + enc + "'")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command", outer],
                             creationflags=_CREATE_NO_WINDOW)
        except OSError as e:
            raise ApiError("PHONE_VIEW", f"could not launch the admin prompt: {e}")
        return {"launched": True}

    # -- themes & settings ------------------------------------------------------------

    # A theme is these 9 colors. User-made themes live as individual JSON files in
    # settings.user_themes_dir() so they're trivially shareable (copy the file); get_themes
    # loads them next to the bundled packs and tags them user=True / category="Custom".
    _THEME_KEYS = ("bg", "bg2", "sub", "subAlt", "text", "accent", "error", "ok", "warn")
    _SERIKA_FALLBACK = {
        "bg": "#323437", "bg2": "#2c2e31", "sub": "#646669", "subAlt": "#51545a",
        "text": "#d1d0c5", "accent": "#e2b714", "error": "#ca4754", "ok": "#7ec384",
        "warn": "#e2b714",
    }

    @_endpoint
    def get_themes(self):
        from .app import resource_path

        themes = []
        seen = set()
        for d in (resource_path("web/themes"), settings.user_themes_dir()):
            if not d.is_dir():
                continue
            is_user = d == settings.user_themes_dir()
            for f in sorted(d.glob("*.json")):
                try:
                    t = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(t, dict) and t.get("id") and t.get("colors") and t["id"] not in seen:
                        t["user"] = is_user
                        if is_user:
                            t["category"] = "Custom"   # user themes always group under Custom
                        themes.append(t)
                        seen.add(t["id"])
                except (OSError, ValueError):
                    log.warning("bad theme file: %s", f)

        return {"themes": themes, "active": settings.get("theme", "serika_dark")}

    @staticmethod
    def _theme_slug(name: str) -> str:
        """A filesystem- and id-safe token from a display name: lowercase, every run of
        non-alphanumerics collapsed to a single underscore."""
        slug = "".join(ch if ch.isalnum() else "_" for ch in str(name).strip().lower())
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug.strip("_") or "custom"

    @_endpoint
    def save_user_theme(self, theme, prev_id=None):
        """Write a custom theme as <slug>.json in the user themes dir and return the saved
        theme (with its final id + user flag). prev_id, when given, is the theme being
        edited; if the name (hence slug) changed, its old file is removed (a rename)."""
        from .app import resource_path

        if not isinstance(theme, dict):
            raise ValueError("theme must be an object")
        name = str(theme.get("name", "")).strip()
        if not name:
            raise ValueError("theme needs a name")
        src = theme.get("colors")
        colors = dict(self._SERIKA_FALLBACK)
        if isinstance(src, dict):
            colors.update({k: src[k] for k in self._THEME_KEYS if k in src})

        d = settings.user_themes_dir()
        # never shadow a bundled id; pick a slug unique across all themes (except the one
        # being edited, which we're overwriting/renaming)
        bundled = resource_path("web/themes")
        taken = {p.stem for p in bundled.glob("*.json")} if bundled.is_dir() else set()
        taken |= {p.stem for p in d.glob("*.json")}
        if prev_id:
            taken.discard(str(prev_id))
        base_slug = self._theme_slug(name)
        slug = base_slug
        n = 2
        while slug in taken:
            slug = f"{base_slug}_{n}"
            n += 1

        saved = {"id": slug, "name": name, "category": "Custom", "colors": colors}
        (d / f"{slug}.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
        if prev_id and str(prev_id) != slug:
            old = d / f"{Path(str(prev_id)).name}.json"
            if old.is_file() and old.parent == d:
                try:
                    old.unlink()
                except OSError:
                    pass
        saved["user"] = True
        return saved

    @_endpoint
    def delete_user_theme(self, theme_id):
        """Remove a custom theme file. Guarded to the user themes dir; ignores ids that try
        to escape it (path traversal) or that don't exist there."""
        d = settings.user_themes_dir()
        f = d / f"{Path(str(theme_id)).name}.json"
        if f.is_file() and f.parent == d:
            f.unlink()
            return True
        return False

    @_endpoint
    def reveal_themes_dir(self):
        """Open the user themes folder in the OS file manager (so files can be shared)."""
        import os
        import subprocess

        d = settings.user_themes_dir()
        try:
            os.startfile(str(d))  # Windows-native; the app only ships on Windows
        except (AttributeError, OSError):
            try:
                subprocess.Popen(["explorer", str(d)])  # noqa: S607
            except OSError:
                pass
        return str(d)

    @_endpoint
    def get_version(self):
        return __version__

    @_endpoint
    def check_update(self, auto=False):
        """Newest GitHub release vs the running version (updatecheck.py).
        auto=True is the boot path: the frozen/setting/env policy can turn it
        into a no-op that answers "skipped" without touching the network
        (source runs, probes, ⚙-toggled-off). The about box's manual button
        calls without auto and always really checks."""
        import sys

        from . import updatecheck
        if auto and not updatecheck.should_autocheck(
                settings.load(), os.environ, getattr(sys, "frozen", False)):
            return {"status": "skipped", "current": __version__}
        return updatecheck.check(__version__)

    @_endpoint
    def get_settings(self):
        return settings.load()

    @_endpoint
    def set_setting(self, key: str, value):
        settings.set_value(key, value)
        return True

    # -- library --------------------------------------------------------------
    # The saved set of robots (PLANT/LINE/ROBOT) + per-robot backup history.
    # Persists to %APPDATA%\BackupViewer\library.json (see library.py).

    @_endpoint
    def get_library_root(self):
        """The configured library folder (FTP destination + scanned source)."""
        return {"path": settings.library_root()}

    @_endpoint
    def set_library_root(self, path: str):
        p = (path or "").strip()
        if not p:
            raise ApiError("BAD_PATH", "a folder path is required")
        settings.set_value("library_root", p)
        settings.set_value("backup_root", p)   # keep the legacy key in sync
        self._lib_sig = None                   # next lib_list rescans the new root
        return {"path": p}

    @_endpoint
    def pick_library_root(self):
        import webview

        start = settings.library_root()
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=start if Path(start or ".").exists() else ""
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    # -- the CV-X simulator's flat folder ------------------------------------
    # The simulator takes ONE base path and lists the workspace folders directly
    # inside it - it does not walk our plant/line/dated tree. So loading cameras
    # into it is an explicit export: pick cameras, their latest workspaces are
    # copied side by side, overwriting the previous copy of the same camera.

    @_endpoint
    def get_sim_root(self):
        """The flat folder the simulator's base path should point at."""
        return {"path": settings.sim_root()}

    @_endpoint
    def set_sim_root(self, path: str):
        p = (path or "").strip()
        if not p:
            raise ApiError("BAD_PATH", "a folder path is required")
        settings.set_value("sim_root", p)
        return {"path": p}

    @_endpoint
    def pick_sim_root(self):
        import webview

        start = settings.sim_root()
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=start if Path(start or ".").exists() else ""
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def _sim_candidates(self) -> list:
        """Every Keyence camera in the library whose LATEST backup holds simulator
        workspaces, with the folder name each would take in the flat folder.

        Reads the folders, not the library's claims: a camera shows up only if a
        real workspace is on disk. Backups taken before the workspace layout have
        no workspace.xml and are honestly absent rather than silently broken."""
        items = []
        for e in library.list_robots().get("robots", []):
            if e.get("device_type") != "camera-keyence":
                continue
            latest = e.get("latest_path") or ""
            if not latest or not Path(latest).is_dir():
                continue
            for ws in keyence_workspace.workspaces_in(Path(latest)):
                items.append({
                    "id": e.get("id", ""), "station": e.get("robot", ""),
                    "line": e.get("line", ""), "plant": e.get("plant", ""),
                    "label": ws.name if ws != Path(latest) else "",
                    "src": str(ws), "taken": e.get("last_backup", ""),
                })
        planned = keyence_workspace.plan_exports(items)

        root = Path(settings.sim_root())
        for it in planned:
            it["key"] = it["src"]
            it["files"], it["bytes"] = _tree_size(it["src"])
            it["ip"] = keyence_workspace.read_workspace_xml(
                Path(it["src"]) / keyence_workspace.WORKSPACE_XML).get("ControllerIpAddress", "")
            it["already"] = (root / it["name"]).is_dir()
            # "already there" is two very different things: our own previous
            # export (replace freely) or a folder somebody made by hand, which
            # exporting would DESTROY. The picker must not blur them.
            it["foreign"] = keyence_workspace.would_replace_foreign(root, it["name"])
        return planned

    @_endpoint
    def sim_candidates(self):
        return {"root": settings.sim_root(), "cameras": self._sim_candidates()}

    @_endpoint
    def sim_export(self, keys, replace_foreign: bool = False):
        """Copy the chosen cameras' latest workspaces into the flat simulator
        folder, replacing any previous copy of the same camera. Never touches a
        backup - the source is read-only and the destination is swapped in whole,
        so a failed copy leaves the old workspace intact.

        A camera whose folder name is already taken by something this export did
        NOT create is not copied; it comes back in `blocked` so the caller can
        show exactly what would be destroyed and ask. Only a deliberate
        replace_foreign=True goes through with it."""
        wanted = {str(k) for k in (keys or [])}
        if not wanted:
            raise ApiError("NOTHING_PICKED", "pick at least one camera")
        root = Path(settings.sim_root())
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ApiError("BAD_PATH", f"cannot use {root}: {e}") from e

        done, failed, blocked = [], [], []
        for it in self._sim_candidates():
            if it["key"] not in wanted:
                continue
            row = {"key": it["key"], "name": it["name"], "station": it["station"]}
            try:
                dest = keyence_workspace.export_workspace(
                    it["src"], root, it["name"], replace_foreign=bool(replace_foreign))
            except keyence_workspace.ForeignWorkspace:
                blocked.append({**row, "path": str(root / it["name"])})
                continue
            (done if dest else failed).append({**row, "path": str(dest or "")})
        if not done and failed and not blocked:
            raise ApiError("EXPORT_FAILED", f"nothing copied ({len(failed)} failed)")
        return {"root": str(root), "exported": done, "failed": failed, "blocked": blocked}

    @_endpoint
    def lib_rescan(self):
        """Rebuild the library from the folder tree (picks up copied-in folders)
        and return the reconciled set."""
        root = settings.library_root()
        data = self._scan_with_progress(root)
        self._set_lib_sig(root, library.settled_signature(root))   # this scan IS the fresh baseline
        return data

    def _saved_lib_sig(self) -> str | None:
        """The tree signature persisted by the previous run's scan — trusted only
        when it was stamped for THIS root and the cached library actually holds
        robots. Lets an unchanged tree boot straight off library.json instead of
        paying a full rescan every launch (the cheap signature walk still runs
        on every listing, so any tree change is caught exactly as before)."""
        v = settings.get("lib_sig")
        if not isinstance(v, dict) or not v.get("sig"):
            return None
        if v.get("root") != settings.library_root():
            return None
        try:
            if not library.load().get("robots"):
                return None   # wiped/empty cache: rescan, never serve an empty tree
        except Exception:  # noqa: BLE001 - unreadable cache = no shortcut
            return None
        return v["sig"]

    def _set_lib_sig(self, root, sig: str) -> None:
        """Remember which tree the library cache reflects — in memory for this
        run and in settings for the next boot. A falsy sig (unreachable root)
        is not persisted: it's not a baseline, and keeping the old stamp means
        the drive coming back unchanged still boots off the cache."""
        self._lib_sig = sig
        if not sig:
            return
        try:
            settings.set_value("lib_sig", {"root": str(root), "sig": sig})
        except OSError:
            log.exception("could not persist the library signature")

    def _seed_lib_sig(self) -> None:
        """Adopt the previous run's persisted signature, once, lazily — so the
        first listing (or the first mutation) starts from the boot baseline."""
        if self._lib_sig is None and not self._lib_seeded:
            self._lib_seeded = True
            self._lib_sig = self._saved_lib_sig()

    def _claim_tree_changes(self, mutate):
        """Run a library mutation whose folder/sidecar writes are already
        reflected into library.json by the operation itself (rename, merge,
        camera link, config edit — they all update the entry in place AND
        touch the tree). When the tree was CLEAN before the op, adopt the
        post-op signature as the new baseline: the next lib_list serves the
        cache the op just updated instead of paying a full rescan that would
        only re-derive what the op already wrote (seconds of churn at plant
        scale for a one-entry change, and the reason a rename used to blank
        the library). A tree already dirty keeps its pending rescan — external
        Explorer edits are never absorbed unseen. (A change landing DURING the
        op's own window rides the claim — the same inherent race lib_list's
        post-scan baseline accepts; any later touch re-dirties the tree, and
        "refresh library" exists for exactly this.) Exceptions propagate
        without claiming: a failed op leaves the tree dirty, so the next
        listing rescans to the truth."""
        root = settings.library_root()
        self._seed_lib_sig()
        before = library.scan_signature(root)
        clean = bool(before) and before == self._lib_sig
        result = mutate()
        if clean:
            # settled: a pre-flush mtime stored here would read as a phantom
            # tree change on the very next listing — the rescan this exists
            # to prevent. (A pre-flush `before` is harmless the other way:
            # it fails the clean check and simply doesn't claim.)
            self._set_lib_sig(root, library.settled_signature(root))
        return result

    def _scan_with_progress(self, root):
        """library.scan_library_root with the shared progress snapshot raised so
        lib_scan_progress polls (the home tab's loading bar) can watch it move —
        and, per robot the walk completes, an entry appended to the snapshot's
        `entries` feed (favorites first), so a cold scan's UI can fill in the
        library progressively instead of spinning."""
        def tick(done, total, current):
            with self._lib_progress_lock:
                self._lib_progress.update(done=done, total=total, current=current)

        def entry(g):
            with self._lib_progress_lock:
                self._lib_progress["entries"].append(g)

        with self._lib_progress_lock:
            self._lib_progress.update(active=True, done=0, total=0, current="",
                                      entries=[])
        try:
            return library.scan_library_root(root, progress=tick, on_entry=entry)
        finally:
            with self._lib_progress_lock:
                self._lib_progress["active"] = False

    @_endpoint
    def lib_scan_progress(self, offset: int = 0):
        """The running library scan's snapshot (inactive zeros between scans) —
        polled by the home tab while a scan is in flight. `offset` is how many
        streamed entries the caller already holds: the reply carries only the
        tail beyond it plus `count`, the new total — so the 400ms poll stays
        small at plant scale."""
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            off = 0
        with self._lib_progress_lock:
            p = dict(self._lib_progress)
            ents = p.pop("entries", [])
            p["count"] = len(ents)
            p["entries"] = ents[off:]   # sliced under the lock: the scan thread appends
        return p

    def _scan_runner(self, root):
        """One full rescan on the background thread. A tree that MOVED while
        the walk ran (a rename mid-scan, an Explorer copy landing) can leave
        the merged result reflecting the pre-move tree — so the walk repeats
        until its start and end signatures agree, and only that quiesced
        result is stamped as the current baseline. Never quiesces (a copy
        still in flight) -> the signature stays stale, so the next listing
        simply scans again. Always ends by telling the UI the cache settled."""
        try:
            for _ in range(3):
                start = library.scan_signature(root)
                self._scan_with_progress(root)
                end = library.settled_signature(root)
                if end == start:
                    self._set_lib_sig(root, end)
                    break
        except Exception:  # noqa: BLE001 - a scan crash must not kill the app; sig stays stale
            log.exception("background library scan failed")
        finally:
            with self._scan_thread_lock:
                self._scan_thread = None
            self._notify_library_updated()

    def _scan_alive(self) -> bool:
        with self._scan_thread_lock:
            t = self._scan_thread
            return t is not None and t.is_alive()

    def _start_background_scan(self, root) -> None:
        """Kick the one background rescan (no-op while one is running)."""
        with self._scan_thread_lock:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                return
            t = threading.Thread(target=self._scan_runner, args=(str(root),),
                                 name="libscan", daemon=True)
            self._scan_thread = t
            t.start()

    @_endpoint
    def lib_list(self):
        """The library. Unchanged tree -> the cached state, no scan, no
        library.json rewrite. A changed tree (files are law - Explorer
        copies/deletes just show up) -> the last-known library IMMEDIATELY,
        stamped {"scanning": true}, with the rescan on a background thread;
        a 'library-updated' push follows when the cache has settled, and the
        refetch it triggers is a ms-cheap cache read. The one blocking case
        left is a library with nothing to serve (virgin install / wiped
        cache): an empty tree would be a lie, so that first scan is waited
        out (the home tab draws its progress from lib_scan_progress). An
        unreachable root serves the last known library with everything
        pilled stale, scanning quietly behind it."""
        root = settings.library_root()
        self._seed_lib_sig()
        sig = library.scan_signature(root)
        if sig and sig == self._lib_sig:
            return library.list_robots()
        cached = library.list_robots()
        if not cached.get("robots") or self._lib_sig is None:
            # Nothing servable, or no baseline for THIS root (first-ever look,
            # or a switched library root whose persisted stamp was refused):
            # the cache can't honestly claim to be "the last known state of
            # this tree", so the real scan is waited out - the one blocking
            # case left, and the home tab draws its progress while it runs.
            data = self._scan_with_progress(root)
            # store the POST-scan signature: NTFS flushes directory-mtime
            # updates lazily, and the scan's own walk forces the flush - the
            # settled value is the one future listings will see.
            self._set_lib_sig(root, library.settled_signature(root))
            return data
        if self._backups_active():
            # a running backup writes thousands of files into this tree —
            # scanning under it is churn the watcher already refuses (it
            # pauses for the same reason). Serve the cache quietly; the
            # run's end triggers the refresh that scans for real.
            return cached
        self._start_background_scan(root)
        cached["scanning"] = True
        return cached

    def _materialize_robot_folder(self, e: dict) -> dict:
        """Files are law: a robot IS a folder. Ensure a just-added robot exists
        on disk (folder + robot.json sidecar) so it survives rescans, root
        changes, and rebuilds - a discovery-added robot is real from second one,
        and its IP travels in the sidecar, not just this machine's cache."""
        hr = e.get("history_root", "")
        if hr and Path(hr).is_dir():
            return e
        root = library._root()
        d = library._robot_dir_for(root, e.get("plant", ""), e.get("line", ""), e.get("robot", ""))
        sidecar = d / library.SIDECAR
        if sidecar.is_file():
            owner = library._read_json(sidecar)
            if owner.get("id") and owner["id"] != e.get("id"):
                log.warning("not adopting %s: folder already belongs to %r",
                            d, owner.get("robot", "") or d.name)
                return e
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise ApiError("BAD_PATH", f"could not create the robot folder: {ex}")
        return library.update_robot(e["id"], {"history_root": str(d)}) or e

    @_endpoint
    def lib_add(self, entry: dict):
        return self._claim_tree_changes(
            lambda: self._materialize_robot_folder(library.add_robot(entry or {})))

    @_endpoint
    def lib_update(self, robot_id: str, patch: dict):
        e = self._claim_tree_changes(
            lambda: library.update_robot(robot_id, patch or {}))
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        return e

    @_endpoint
    def lib_set_hidden(self, robot_id: str, hidden: bool = True):
        """Hide/unhide a robot from the library view (overlay-only; survives a
        rescan). The everyday, non-destructive alternative to deleting."""
        e = library.set_hidden(robot_id, hidden)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        return e

    @_endpoint
    def lib_set_favorite(self, robot_id: str, favorite: bool = True):
        """Star/unstar a robot (overlay-only, survives a rescan like hidden).
        Favorites render pinned in a section at the top of the library."""
        e = library.set_favorite(robot_id, favorite)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        return e

    # (lib_delete_files, lib_scan_folder, and lib_add_from_session were removed
    # with the v0.98 files-are-law pivot: the app never deletes backup data, and
    # backups join the library by being COPIED into the library folder - the
    # scan/watcher picks them up. Hiding covers the everyday remove case.)

    @_endpoint
    def lib_open(self, robot_id: str, which: str = "latest", side: str = "a"):
        """Load a library robot's backup as a session. which='latest' opens its
        latest_path; any other value is a specific backup folder from its history.
        side='b' loads it as the COMPARE session (needs a primary first) so the
        compare flow can pick a second robot straight from the library, instead of
        the folder dialog; side='a' (default) loads it as the single primary."""
        e = library.get_robot(robot_id)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        path = library.resolve_open_path(e, which)
        p = Path(path) if path else None
        if p is None or not p.is_dir():
            raise ApiError("NOT_FOUND",
                           f"backup folder missing: {path or '(no backup on disk)'}")
        if side == "b":
            ent = self._entry()  # comparing needs a primary first
            ent["compare"] = BackupSession(p)
            return ent["compare"].manifest()
        ent = self._open_session(p)
        settings.set_value("last_folder", str(p))
        # carry the robot's identity + dated history so the backup view can show
        # a date-picker timeline (a folder opened directly leaves these unset).
        # Enrich the STORED manifest: get_state/switch_session serve this same
        # dict, so a tab switch or a solo boot keeps the identity fields.
        m = ent["manifest"]
        m["robot_id"] = e["id"]
        # the library's display name beats a leaf-folder fallback (tab labels,
        # pop-out titles); a robot_name parsed from the backup itself wins
        m.setdefault("robot_name", e.get("name", ""))
        m["backups"] = e.get("backups", [])
        m["current_path"] = str(p)
        # a camera carries its brand + IP so the viewer can offer "remote" (a live
        # CV-X screen-mirror, or the Matrox web UI) alongside its saved photos.
        dt = e.get("device_type", "robot")
        if dt.startswith("camera"):
            m["device_type"] = dt
            m["camera_name"] = e.get("name", "")
            ips = e.get("ips") or []
            m["camera_ip"] = ips[0] if ips else ""
        # light up the photos tab for a robot that has linked cameras (their photos
        # live in their own backups; lib_robot_cameras fetches them on demand). The
        # photos tab handles both a camera's own images and a robot's linked cameras.
        if dt == "robot":
            cams = library.cameras_for_robot(e["id"])
            if cams:
                m.setdefault("tabs", {})["photos"] = True
                m["cameras_count"] = len(cams)
        return m

    # -- rename / merge / tidy + open backup location -------------------------
    # Fix IP-named legacy robots from their backup contents, merge duplicates,
    # and jump to a folder in Explorer. relocate_robot/merge_robots move the
    # on-disk tree WITH the entry (see library.py); these endpoints just preview,
    # release any open session over the affected tree, and apply.

    def _robot_fingerprint(self, e: dict) -> dict:
        """Merge-confirmation evidence for one robot, read from its latest
        backup: reported hostname, F-number, its OWN IP (the host-table entry
        matching the hostname — not the whole table, which lists servers and
        neighbours too), and master counts — plus the entry's recorded IPs.
        The folder name is deliberately NOT the name signal here: fingerprints
        exist exactly because folder names lie. Best-effort — a missing or
        sparse backup just yields fewer signals."""
        fp = {"name": "", "ips": set(e.get("ips") or []),
              "f_number": (e.get("f_number") or ""), "counts": None, "drafted": False}
        lp = e.get("latest_path") or ""
        if not lp or not Path(lp).is_dir():
            return fp
        try:
            s = BackupSession(Path(lp))
            m = s.manifest()
            ident, hosts = {}, []
            try:
                ov = self._build_summary(s)
                ident = ov.get("identity") or {}
                hosts = (ov.get("ethernet") or {}).get("hosts", []) or []
            except ApiError:
                pass
            fp["drafted"] = True
            fp["name"] = (m["robot_name"] or ident.get("robot_name", "") or "").strip()
            fp["f_number"] = m["f_number"] or ident.get("f_number", "") or fp["f_number"]
            own = next((h.get("addr") for h in hosts
                        if (h.get("name") or "").upper() == fp["name"].upper() and h.get("addr")),
                       None) or next((h.get("addr") for h in hosts
                                      if h.get("slot") == 1 and h.get("addr")), None)
            if own:
                fp["ips"].add(own)
            try:
                groups = self._build_mastering(s)
                counts = tuple(tuple(g.get("master_counts") or ()) for g in groups)
                if any(any(c) for c in counts):        # all-zero = unmastered = no signal
                    fp["counts"] = counts
            except ApiError:
                pass
        except Exception:  # noqa: BLE001 - a sparse/locked backup just yields fewer signals
            log.exception("fingerprint failed for %r", e.get("robot", ""))
        return fp

    @_endpoint
    def lib_resolve_names(self, ids: list):
        """Preview 'fix names from backups' for the given robots: read each
        robot's REAL name from its latest backup and classify the change as
        noop / rename / merge. A merge is suggested on EVIDENCE that two
        entries are the same physical robot — hostname, shared IP, F-number,
        master counts (see _merge_evidence): 2+ signals = confidence "sure",
        1 = "maybe" (the UI previews maybes deselected). The FANUC factory
        hostname ("ROBOT") identifies nothing: it is never proposed as a name
        and never counts as a name match — the field bug was three robots
        whose backups all said ROBOT getting merged into a robot literally
        named ROBOT on name alone. Merge targets are line-scoped (never
        cross-line) and prefer the convention-named / richer-history side.
        Pure preview; the UI applies on confirm. Returns {items:[{id, current,
        proposed, plant, line, action, merge_into, target, evidence,
        confidence, reason}]}."""
        ids = list(ids or [])
        data = library.list_robots()
        by_id = {e["id"]: e for e in data["robots"]}
        fps: dict = {}

        def fp_of(e: dict) -> dict:
            if e["id"] not in fps:
                fps[e["id"]] = self._robot_fingerprint(e)
            return fps[e["id"]]

        def better_target(c: dict, e: dict) -> bool:
            """Should c survive a merge of the pair (c, e)? The convention-
            named side wins, then the richer history, then stable id order."""
            cf = bool(_FULL_NAME_RE.match(c.get("robot") or ""))
            ef = bool(_FULL_NAME_RE.match(e.get("robot") or ""))
            if cf != ef:
                return cf
            cb, eb = len(c.get("backups") or []), len(e.get("backups") or [])
            if cb != eb:
                return cb > eb
            return (c.get("id") or "") < (e.get("id") or "")

        items, claimed, paired = [], {}, set()
        for rid in ids:
            e = by_id.get(rid)
            if e is None:
                continue
            cur, line = e.get("robot", ""), e.get("line", "")
            fp = fp_of(e)
            host = fp["name"] or ""
            default_host = host.upper() in _DEFAULT_HOSTNAMES
            proposed = "" if default_host else host

            # strongest same-line merge candidate, cheap prefilter first
            best, best_ev = None, []
            nm = cur.upper()
            for c in data["robots"]:
                if c["id"] == rid or (c.get("line", "") or "").upper() != (line or "").upper():
                    continue
                cn = (c.get("robot") or "").upper()
                pre = (bool(proposed) and cn == proposed.upper()) \
                    or bool(set(c.get("ips") or []) & fp["ips"]) \
                    or bool((c.get("f_number") or "") and fp["f_number"]
                            and c["f_number"].upper() == fp["f_number"].upper()) \
                    or (len(nm) >= 5 and (nm in cn or cn in nm))
                if not pre:
                    continue
                ev = _merge_evidence(fp, fp_of(c))
                if ev is None:
                    continue                   # F-numbers disagree: NOT the same robot
                if "name" not in ev and proposed and cn == proposed.upper():
                    ev = ev + ["name"]         # proposed name == candidate's FOLDER name
                if len(ev) > len(best_ev):
                    best, best_ev = c, ev

            action, merge_into, target, confidence, reason = "noop", None, "", "", ""
            pair = frozenset((rid, best["id"])) if best is not None else None
            name_coll = bool(proposed) and best is not None \
                and (best.get("robot") or "").upper() == proposed.upper()
            if best is not None and pair not in paired and (name_coll or better_target(best, e)):
                # a name collision forces the direction (renaming onto that name
                # would merge into its owner at apply time anyway)
                action, merge_into, target = "merge", best["id"], best.get("robot", "")
                confidence = "sure" if len(best_ev) >= 2 else "maybe"
                reason = " + ".join(best_ev) + (" match" if len(best_ev) > 1 else " matches")
                paired.add(pair)
            elif proposed and proposed.upper() != nm:
                key = (proposed.upper(), (line or "").upper())
                if key in claimed:
                    action, merge_into = "merge", claimed[key]
                    target = (by_id.get(claimed[key]) or {}).get("robot", "") or proposed
                    confidence, reason = "maybe", "duplicate within the selection"
                else:
                    claimed[key] = rid
                    action = "rename"
            elif not fp["drafted"]:
                reason = "no backup to read a name from"
            elif default_host:
                reason = f"backup reports the factory-default name ({host})"
            else:
                reason = "name already matches the backup"
            items.append({"id": rid, "current": cur, "proposed": proposed,
                          "plant": e.get("plant", ""), "line": line,
                          "action": action, "merge_into": merge_into, "target": target,
                          "evidence": best_ev if action == "merge" and merge_into == (best or {}).get("id") else [],
                          "confidence": confidence, "reason": reason})
        return {"items": items}

    @_endpoint
    def lib_apply_renames(self, items: list):
        """Apply clean renames (relocating their folders). `items` = [{id, plant?,
        line?, robot}]. A collision discovered at apply time surfaces as a 'merged'
        result rather than aborting the batch. Failures carry the robot's label and
        the reason ({id, robot, error}) - the UI shows them verbatim."""
        renamed, merged, failed = [], [], []

        def apply():
            for it in (items or []):
                rid = it.get("id")
                e = library.get_robot(rid)
                if e is None:
                    failed.append({"id": rid, "robot": it.get("robot", "") or str(rid),
                                   "error": "robot not in library"})
                    continue
                label = e.get("robot", "") or str(rid)
                plant = it.get("plant", e.get("plant", ""))
                line = it.get("line", e.get("line", ""))
                robot = it.get("robot", e.get("robot", ""))
                target = str(library._robot_dir_for(library._root(), plant, line, robot))
                self._release_sessions_under(e.get("history_root"), e.get("latest_path"), target)
                try:
                    res = library.relocate_robot(rid, plant, line, robot)
                except library.PathGuard as ex:
                    failed.append({"id": rid, "robot": label, "error": f"BAD_PATH: {ex}"})
                    continue
                except (ValueError, OSError) as ex:
                    failed.append({"id": rid, "robot": label, "error": str(ex)})
                    continue
                if res.get("action") == "blocked":
                    # the collision-merge had nothing to fold: the move did NOT happen
                    failed.append({"id": rid, "robot": label,
                                   "error": "not merged: " + res.get("reason", "")})
                    continue
                (merged if res.get("action") == "merged" else renamed).append(res)

        # one claim for the whole batch: every successful move is already in
        # library.json, and a per-item failure is safe to claim over because a
        # failed relocate moves nothing (transactional — source left intact)
        self._claim_tree_changes(apply)
        return {"renamed": renamed, "merged": merged, "failed": failed}

    @_endpoint
    def lib_merge(self, primary_id: str, secondary_ids):
        """Merge one or more secondary robots INTO a primary (folders + history).
        Cross-line pairs are refused (reported, not merged); a secondary the merge
        could fold nothing from comes back in `blocked` with its reason."""
        if isinstance(secondary_ids, str):
            secondary_ids = [secondary_ids]
        merged, refused, failed, blocked = [], [], [], []

        def apply():
            for sid in (secondary_ids or []):
                prim, sec = library.get_robot(primary_id), library.get_robot(sid)
                if prim is None or sec is None:
                    failed.append({"id": sid, "error": "robot not in library"})
                    continue
                self._release_sessions_under(prim.get("history_root"), prim.get("latest_path"),
                                             sec.get("history_root"), sec.get("latest_path"))
                try:
                    res = library.merge_robots(primary_id, sid)
                except library.PathGuard as ex:
                    failed.append({"id": sid, "error": f"BAD_PATH: {ex}"})
                    continue
                except (ValueError, OSError) as ex:
                    failed.append({"id": sid, "error": str(ex)})
                    continue
                if res.get("action") == "refused":
                    refused.append(res)
                elif res.get("action") == "blocked":
                    blocked.append(res)
                else:
                    merged.append(res)

        self._claim_tree_changes(apply)
        return {"merged": merged, "refused": refused, "blocked": blocked, "failed": failed}

    @_endpoint
    def lib_relocate(self, robot_id: str, plant: str, line: str, robot: str):
        """Rename/relocate one robot, moving its folder tree. Returns the raw
        relocate result so the edit modal can detect a merge (collision)."""
        e = library.get_robot(robot_id)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        target = str(library._robot_dir_for(library._root(), plant, line, robot))
        self._release_sessions_under(e.get("history_root"), e.get("latest_path"), target)
        try:
            return self._claim_tree_changes(
                lambda: library.relocate_robot(robot_id, plant, line, robot))
        except library.PathGuard as ex:
            raise ApiError("BAD_PATH", str(ex))
        except ValueError as ex:
            raise ApiError("BAD_SPEC", str(ex))
        except OSError as ex:
            raise ApiError("MOVE_FAILED", str(ex))

    @_endpoint
    def open_path(self, path: str):
        """Open a folder in the OS file manager. Guarded: only existing directories
        under library_root() (mirrors reveal_themes_dir + the delete root-guard)."""
        import os
        import subprocess

        p = (path or "").strip()
        if not p:
            raise ApiError("BAD_PATH", "a folder path is required")
        try:
            root = Path(settings.library_root()).resolve()
            rp = Path(p).resolve()
        except OSError:
            raise ApiError("BAD_PATH", "could not resolve path")
        if not rp.is_dir():
            raise ApiError("BAD_PATH", f"not a folder: {p}")
        if not library._within(rp, root):
            raise ApiError("BAD_PATH", "path is outside the library root")
        try:
            os.startfile(str(rp))  # Windows-native; the app only ships on Windows
        except (AttributeError, OSError):
            try:
                subprocess.Popen(["explorer", str(rp)])  # noqa: S607
            except OSError:
                pass
        return str(rp)

    @_endpoint
    def open_url(self, url: str):
        """Open a link in the user's default browser (the about box's source link).
        Guarded to http/https so this can never become an arbitrary-scheme or
        local-file launcher for anything that reaches the bridge."""
        import webbrowser

        u = (url or "").strip()
        if not u.lower().startswith(("http://", "https://")):
            raise ApiError("BAD_URL", "only http/https links can be opened")
        webbrowser.open(u)
        return u

    # -- take a new backup (FTP pull) ------------------------------------------

    @_endpoint
    def probe_controller(self, spec: dict):
        """Pre-flight reachability check - connect + sniff devices, no writes.
        A Matrox camera (device_type='camera-mtx') sniffs da/ + SavedImages with
        the mtxuser/Matrox default login instead of the FANUC MD:/FR: devices."""
        spec = spec or {}
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "host/IP is required")
        return _device_row(spec)["probe"](host, spec)

    @_endpoint
    def diagnose_controller(self, spec: dict):
        """Read-only probe (writes a JSON summary to app.log and returns it, no
        writes to the device). Robots: banner/cwd/listings/auto-name (FTP). Matrox
        cameras (device_type='camera-mtx'): the SMB share's home + da/ + SavedImages
        layout, so the real login can be confirmed before the first real pull."""
        spec = spec or {}
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "host/IP is required")
        return _device_row(spec)["diagnose"](host, spec)

    @_endpoint
    def start_backup(self, spec: dict):
        """Kick off an FTP backup on a worker thread; returns a job_id to poll.
        device_type='camera-mtx' runs a Matrox CameraBackupJob (da/ + newest
        SavedImages, mtxuser/Matrox), 'camera-keyence' a CV-X job; anything else
        runs the FANUC BackupJob. All jobs share the snapshot()/cancel()/library_*
        shape the poll + strip endpoints rely on.
        spec.run_id (the frontend stamps one per bulk click) groups the jobs of
        one user action in the durable backup log - but while a run is still in
        flight, every new job joins THAT run regardless of the stamp (see
        _active_run_id)."""
        return self._start_backup_job(spec or {})

    def _start_backup_job(self, spec: dict) -> dict:
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "host/IP is required")
        root = (spec.get("dest_root") or settings.library_root())
        # Persisting the root is incidental - it must never kill the backup, and
        # a 20-robot multi-select fires 20 of these at once (write only on change;
        # the field failure was every one of those backups dying on a settings
        # rename race before a single file was pulled).
        try:
            if settings.get("library_root") != str(root):
                settings.set_value("library_root", str(root))
            if settings.get("backup_root") != str(root):
                settings.set_value("backup_root", str(root))   # keep the legacy key in sync
        except OSError:
            log.warning("could not persist library root (backup continues)")

        def _register(job):
            entry = library.register_backup(
                job.library_match(), job.library_backup(),
                latest_path=job.snapshot().get("latest_path", ""),
            )
            # A camera self-names from the backup it just pulled when its entry
            # only carries a placeholder - the camera twin of a robot naming
            # itself from SUMMARY.DG - and then auto-linking gets a fresh chance
            # to seat it under its robot. The teach renames the camera's FOLDER
            # along with the entry (files are law), so the name still stands
            # after the next library rescan. Each camera kind reads its own
            # evidence: Matrox the newest saved-image sidecar, Keyence the names
            # of its inspection programs (a CV-X exposes no name over FTP).
            # Best-effort: identity work must never fail a finished backup.
            namer = {"camera-mtx": mtxbackup.name_from_backup,
                     "camera-keyence": keyencebackup.name_from_backup}.get(
                         spec.get("device_type", ""))
            if namer:
                try:
                    ident = namer(job.snapshot().get("dated_path", ""))
                    if ident.get("name"):
                        library.teach_camera_name(
                            entry["id"], ident["name"], ident.get("model", ""))
                    library.auto_link_cameras()
                except Exception:  # noqa: BLE001
                    log.exception("camera self-name/auto-link after backup failed")

        # in-flight run joining: a backup fired while a run is still live joins
        # that run instead of stacking a new one. Every job kind (FANUC + both
        # camera jobs) carries run_id in its snapshot, so _active_run_id() sees an
        # in-flight camera pull too.
        run_id = (self._active_run_id()
                  or (spec.get("run_id") or "").strip()
                  or uuid.uuid4().hex)
        row = _device_row(spec)
        job = row["job_cls"](
            host, root, spec.get("plant", ""), spec.get("line", ""), spec.get("robot", ""),
            note=spec.get("note", ""), run_id=run_id, on_complete=_register,
            **row["job_kw"](spec),
        )
        self._jobs[job.id] = job
        backuplog.start_job(run_id, job.id, spec)

        def _run_and_log():
            snap = job.run()          # returns the final snapshot on every path
            try:
                backuplog.finish_job(run_id, job.id, snap)
            except Exception:  # noqa: BLE001 - the log must never kill a backup thread
                log.exception("backup log write failed for %s", job.id)

        threading.Thread(target=_run_and_log, name="backup-" + job.id, daemon=True).start()
        return {"job_id": job.id, "run_id": run_id}

    @_endpoint
    def backup_log(self):
        """The persisted backup-run history, newest run first (passwords are
        never stored). Powers the Manage-backups "last run" panel."""
        return backuplog.load()

    @_endpoint
    def retry_failed_backups(self, run_id: str = "", passwd: str = ""):
        """Re-fire exactly the FAILED jobs of a run (default: the newest run).
        While that run is still in flight the retries fold back into it (its
        failed rows are replaced, attempts counted); only against an idle
        engine do they open a fresh run. passwd, when given, applies to
        retried robots whose saved spec carries an FTP user - the same
        shared-password model the bulk flow uses; nothing is persisted."""
        specs = backuplog.failed_specs(run_id or None)
        if not specs:
            raise ApiError("NOTHING_TO_RETRY", "that run has no failed backups")
        fired = []
        actual_run = ""
        for sp in specs:
            sp = dict(sp)
            if sp.get("user") and passwd:
                sp["passwd"] = passwd
            res = self._start_backup_job(sp)
            actual_run = res["run_id"]
            fired.append({"robot_id": sp.get("robot_id", ""), "robot": sp.get("robot", ""),
                          "job_id": res["job_id"]})
        return {"run_id": actual_run, "jobs": fired}

    @_endpoint
    def list_backup_jobs(self):
        """Snapshots of every backup job this session (active AND finished), so
        the global progress strip can watch them all with one call per tick and
        a reloaded frontend can re-discover in-flight jobs it never started."""
        return {"jobs": [j.snapshot() for j in self._jobs.values()]}

    @_endpoint
    def cancel_backup(self, job_id: str):
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("NO_JOB", "unknown backup job")
        job.cancel()
        return True

    # -- bulk import + network discovery ---------------------------------------

    @_endpoint
    def local_subnet(self):
        """The local /24 (and IP) to prefill the discover dialog."""
        return {"cidr": discover.default_cidr(), "ip": discover.local_ipv4()}

    @_endpoint
    def list_adapters(self):
        """Network adapters (name/kind/ip/cidr) for the discover dialog, plus the
        local-subnet fallback when adapter enumeration is unavailable."""
        return {
            "adapters": discover.list_adapters(),
            "fallback": {"cidr": discover.default_cidr(), "ip": discover.local_ipv4()},
        }

    @_endpoint
    def net_scan_start(self, spec: dict):
        """Sweep a subnet for FANUC controllers + cameras on a worker thread; poll
        via scan_progress. spec={cidr?, port?, smb_port?}; cidr defaults to the
        local /24, smb_port to 445 (the Matrox share port)."""
        spec = spec or {}
        cidr = discover.normalize_cidr(spec.get("cidr") or "") or discover.default_cidr()
        if not cidr:
            raise ApiError("BAD_SPEC", "could not determine a subnet to scan")
        job = discover.NetworkScanJob(cidr, port=spec.get("port", 21),
                                      smb_port=spec.get("smb_port", discover.SMB_PORT))
        self._scans[job.id] = job
        threading.Thread(target=job.run, name="netscan-" + job.id, daemon=True).start()
        return {"job_id": job.id, "cidr": cidr}

    @_endpoint
    def scan_progress(self, job_id: str):
        job = self._scans.get(job_id)
        if job is None:
            raise ApiError("NO_JOB", "unknown scan job")
        return job.snapshot()

    @_endpoint
    def cancel_scan(self, job_id: str):
        job = self._scans.get(job_id)
        if job is None:
            raise ApiError("NO_JOB", "unknown scan job")
        job.cancel()
        return True

    # -- fleet health scan ------------------------------------------------------

    @_endpoint
    def health_checks(self):
        """The scan-check registry (id/label/desc, display order) for the picker."""
        return healthscan.check_list()

    @_endpoint
    def save_last_scan(self, report: dict):
        """Keep the finished report so closing the app does not throw away
        minutes of scanning. Its OWN file, never settings.json: settings are
        read on every boot and a fleet report can run to megabytes, while
        this one is read only when the scan window opens."""
        if not isinstance(report, dict):
            raise ApiError("BAD_SPEC", "a report object is required")
        path = settings.app_dir() / "last_scan.json"
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(report, f)
            os.replace(tmp, path)            # atomic: a killed write leaves the old one
        except OSError as e:
            raise ApiError("IO", f"could not save the report: {e}") from e
        return {"saved": True, "bytes": path.stat().st_size}

    @_endpoint
    def load_last_scan(self):
        """The kept report, or None when there is none / it is unreadable —
        a corrupt file must never stop the scan window from opening."""
        path = settings.app_dir() / "last_scan.json"
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    @_endpoint
    def clear_last_scan(self):
        try:
            (settings.app_dir() / "last_scan.json").unlink()
        except OSError:
            pass
        return {"cleared": True}

    @_endpoint
    def health_scan_start(self, robot_ids: list, checks: list, queries=None, params=None):
        """Run selected checks (and/or free-text finds - a list of queries, each
        its own report section) across the given library robots on a worker
        thread; poll via scan_progress, stop via cancel_scan. params carries
        per-check inputs ({check_id: string}, e.g. the clock-drift tolerance)."""
        by_id = {e.get("id"): e for e in library.load()["robots"]}
        entries = [by_id[r] for r in (robot_ids or []) if r in by_id]
        if not entries:
            raise ApiError("BAD_SPEC", "no library robots to scan")
        ids = healthscan.valid_ids(checks)
        qs = healthscan.norm_queries(queries)
        if not ids and not qs:
            raise ApiError("BAD_SPEC", "pick at least one check or a find query")
        job = healthscan.HealthScanJob(entries, ids, qs,
                                       params if isinstance(params, dict) else None,
                                       search_fn=self._search_session)
        self._scans[job.id] = job
        threading.Thread(target=job.run, name="healthscan-" + job.id, daemon=True).start()
        return {"job_id": job.id, "total": len(entries)}

    @_endpoint
    def lib_bulk_add(self, entries: list, plant: str = "", line: str = ""):
        """Add many drafts at once under one plant/line, skipping existing
        robots. Each added robot gets its on-disk folder + sidecar immediately
        (files are law) - and a brand-new library (fresh machine / first line,
        root folder never created yet) is BUILT here, not refused."""
        root = Path(settings.library_root())
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise ApiError("BAD_PATH",
                           f"could not create the library folder {root}: {ex}")
        def apply():
            res = library.bulk_add(entries or [], plant=plant, line=line)
            materialized = []
            for e in res.get("added", []):
                try:
                    materialized.append(self._materialize_robot_folder(e))
                except ApiError as ex:
                    log.warning("could not create folder for %r: %s", e.get("robot", ""), ex)
                    materialized.append(e)
            res["added"] = materialized
            return res

        return self._claim_tree_changes(apply)
