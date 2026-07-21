"""The JS <-> Python surface. Exposed to the page as window.pywebview.api.

Every public method returns an envelope and never raises across the bridge:
    {"ok": True, "data": ...}
    {"ok": False, "error": {"code": "MISSING_FILE", "message": "..."}}
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from . import backuplog
from . import compare
from . import __version__
from . import cvx_remote
from . import discover
from . import ftpbackup
from . import healthscan
from . import keyencebackup
from . import library
from . import modeldb
from . import mtxbackup
from . import search as search_mod
from . import settings
from .parsers import (alarms, callgraph, curpos, dcs, dcszones, frames,
                      gmwizlog, io_dg, kinematics, ls_program, macros, magnet,
                      mastering, mhvalves, mtx_portal, mtx_saved_image,
                      payloads, registers, styles, summary_dg, sysvars)
from .parsers.common import is_binary, read_text
from .session import BackupSession

log = logging.getLogger(__name__)

MAX_TEXT_BYTES = 2_000_000
HEX_PREVIEW_BYTES = 4096
MAX_IMAGE_BYTES = 12_000_000
_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".bmp": "image/bmp"}


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
            "devices": spec.get("devices"),
            "recurse_fr": spec.get("recurse_fr", False),
        },
    },
}


def _device_row(spec: dict) -> dict:
    dt = spec.get("device_type") or ""
    return _DEVICE_REGISTRY.get(dt, _DEVICE_REGISTRY[""])


class Api:
    def __init__(self):
        self._window = None
        self._session: BackupSession | None = None
        self._compare_session: BackupSession | None = None
        self._jobs: dict[str, ftpbackup.BackupJob] = {}  # active/finished backup jobs
        self._scans: dict[str, discover._ScanJob] = {}  # folder + network scan jobs
        self._lib_sig: str | None = None  # tree signature at the last scan (None = never)
        self._cvx: dict[str, cvx_remote.CvxRemoteSession] = {}  # live CV-X remote sessions
        self._cvx_server = None  # lazy MJPEG frame server (one for all sessions)
        # linked-camera photo sessions, keyed camera_id -> (path, sig, session).
        # sig is the latest mirror's backup.json mtime, so a fresh camera backup
        # (which rewrites the SAME Latest/ path) invalidates the cache.
        self._camera_sessions: dict[str, tuple] = {}
        self._lib_seeded = False  # _lib_sig lazily seeded from settings on the first listing
        self._lib_progress = {"active": False, "done": 0, "total": 0, "current": ""}
        self._lib_progress_lock = threading.Lock()

    def bind(self, window, initial_backup: str | None = None):
        self._window = window
        if initial_backup:
            try:
                self._session = BackupSession(Path(initial_backup))
            except Exception:
                log.exception("could not open initial backup %s", initial_backup)
        if not os.environ.get("BV_NO_WATCHER"):
            threading.Thread(target=self._watch_library, name="libwatch", daemon=True).start()
        try:
            window.events.closing += self._confirm_close
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
                # jarring repaint a few seconds after the first
                if fire and sig != self._lib_sig:
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

    # -- internals -----------------------------------------------------------
    # builders take an optional session so compare can run them against a
    # second backup; caches live on the session object, so both sides cache
    # independently

    def _need_session(self) -> BackupSession:
        if self._session is None:
            raise ApiError("NO_BACKUP", "No backup folder is open")
        return self._session

    def _release_sessions_under(self, *folders):
        """Drop any open session whose root is inside one of `folders`, so a
        relocate/merge can move that tree (Windows blocks renaming a folder a
        process holds a handle into)."""
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

        if _under(self._session):
            self._session = None
        if _under(self._compare_session):
            self._compare_session = None

    def _side_session(self, side: str) -> BackupSession:
        """'a' = the open backup, 'b' = the loaded comparison backup."""
        if side == "b":
            if self._compare_session is None:
                raise ApiError("NO_COMPARE", "No comparison backup loaded")
            return self._compare_session
        return self._need_session()

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
        self._session = BackupSession(p)
        self._compare_session = None  # a new primary invalidates any loaded compare
        settings.set_value("last_folder", str(p))
        return self._session.manifest()

    @_endpoint
    def get_state(self):
        # called once by the frontend on boot - this log line doubles as proof
        # that html/js loaded and the bridge works (useful for frozen builds)
        log.info("ui booted; session=%s", self._session.root if self._session else None)
        return self._session.manifest() if self._session else None

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

    def _build_call_graph(self):
        s = self._need_session()

        def build():
            try:
                macro_by_name = {m["name"]: m["prog_name"]
                                 for m in self._build_macros() if m.get("prog_name")}
            except ApiError:
                macro_by_name = {}
            return callgraph.build_call_graph(self._program_texts(), macro_by_name)

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
    def get_overview(self):
        s = self._need_session()

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
    def get_frames(self, side: str = "a"):
        return self._build_frames(self._side_session(side))

    @_endpoint
    def get_io(self, side: str = "a"):
        return self._build_io(self._side_session(side))

    @_endpoint
    def get_registers(self, kind: str, side: str = "a"):
        return self._build_registers(kind, self._side_session(side))

    @_endpoint
    def get_styles(self):
        return self._build_styles()

    @_endpoint
    def get_call_graph(self):
        return self._build_call_graph()

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
    def get_programs(self, side: str = "a"):
        return self._build_programs(self._side_session(side))

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
            return ls_program.parse_ls_program(read_text(p))

        pa = load(a, file_a)
        pb = load(b, file_b)
        out = compare.align_program_lines(pa["body"], pb["body"])
        out["a"] = {"name": pa["name"], "file": file_a, "robot": a.robot_name or a.root.name,
                    "comment": pa["attrs"].get("comment", ""), "modified": pa["attrs"].get("modified", "")}
        out["b"] = {"name": pb["name"], "file": file_b, "robot": b.robot_name or b.root.name,
                    "comment": pb["attrs"].get("comment", ""), "modified": pb["attrs"].get("modified", "")}
        return out

    @_endpoint
    def get_program(self, file_name: str):
        s = self._need_session()
        p = s.find(file_name)
        if p is None or p not in s.program_files:
            raise ApiError("NOT_FOUND", f"Program not found: {file_name}")

        def build():
            text = read_text(p)
            prog = ls_program.parse_ls_program(text)
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

    def _karel_records(self, s: BackupSession, stem: str):
        kp = s.karel_programs.get(stem.upper())
        if kp is None:
            raise ApiError("NOT_FOUND", f"PC program not found: {stem}")
        return s.cached(f"karel:{stem.upper()}", lambda: sysvars.records(read_text(kp["va"])))

    @_endpoint
    def get_program_variables(self, stem: str, side: str = "a"):
        """A KAREL (.PC) program's variables, as collapsible trees - shown
        instead of TP source when a PC program is opened."""
        s = self._side_session(side)
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
    def get_call_tree(self, root: str, depth: int = 6):
        """Expandable call tree rooted at a program; cycles marked, depth-limited."""
        graph = self._build_call_graph()
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
    def get_alarm_files(self):
        s = self._need_session()
        out = []
        for p in s.alarm_files():
            parsed = self._alarms_for(p.name)
            out.append({"file": p.name, "rows": len(parsed["rows"]), "exported": parsed["exported"]})
        return out

    def _alarms_for(self, name: str) -> dict:
        s = self._need_session()
        text = self._need_text(name)
        return s.cached(f"alarms:{name.upper()}", lambda: alarms.parse_alarm_file(text))

    @_endpoint
    def get_alarms(self, file_name: str, offset: int = 0, limit: int = 200, query: str = ""):
        parsed = self._alarms_for(file_name)
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
    def get_macros(self, side: str = "a"):
        return self._build_macros(self._side_session(side))

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
    def get_dcs_files(self, side: str = "a"):
        """Available DCS reports with their export dates (change history)."""
        s = self._side_session(side)
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
    def get_dcs(self, file_name: str = "DCSVRFY.DG", side: str = "a"):
        return self._dcs_report(self._side_session(side), file_name)

    @_endpoint
    def get_dcs_zones(self, side: str = "a"):
        """Zone geometry for the 3D view: DCSPOS.VA (authoritative) merged
        with the verify report's status/method/TCP; either may be absent."""
        s = self._side_session(side)

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
    def get_robot_pose(self, side: str = "a"):
        """Everything the 3D view needs to pose the arm: the backup's robot
        type (DCS verify report), the matching imported kinematics, the
        CURPOS.DG pose snapshot, and the flange correction measured from
        this backup's own numbers (see kinematics.measure_flange). All
        fields degrade to None - the view falls back honestly."""
        s = self._side_session(side)

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
        """Cached (records list, name->record) for SYSTEM.VA."""
        def build():
            text = self._need_text("SYSTEM.VA", s)
            recs = sysvars.records(text)
            return recs, {r.name.upper(): r for r in recs}
        return s.cached("sysvar_index", build)

    @_endpoint
    def get_sysvar_records(self, side: str = "a"):
        recs, _ = self._sysvar_index(self._side_session(side))
        return [sysvars.summarize(r) for r in recs]

    @_endpoint
    def get_sysvar(self, name: str, side: str = "a"):
        _, by_name = self._sysvar_index(self._side_session(side))
        rec = by_name.get(name.upper())
        if rec is None:
            raise ApiError("NOT_FOUND", f"System variable not found: {name}")
        return sysvars.record_tree(rec)

    # -- MH valves (GM material-handling grippers) -----------------------------------

    @_endpoint
    def get_mhvalves(self, side: str = "a"):
        # Each valve's *_SN field is a 1-based index into one of the four signal
        # tables stored in MHGRIPDT (VALVE_TAB/PARTP_TAB/CLAMP_TAB/VMADE_TAB); the
        # parser resolves them to real DI/DO (name + number). See parsers/mhvalves.
        s = self._side_session(side)
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
    def get_magnet(self, side: str = "a"):
        """Magnet end-effector detection + config (MAG*.PC programs, R[800s])."""
        s = self._side_session(side)

        def build():
            numreg_text = s.text("NUMREG.VA")
            numreg = registers.parse_numreg(numreg_text) if numreg_text else []
            return magnet.build_magnet(numreg, list(s.karel_programs))

        return s.cached("magnet", build)

    # -- payload schedules -----------------------------------------------------------

    @_endpoint
    def get_payloads(self, side: str = "a"):
        s = self._side_session(side)
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
        self._need_session()  # comparing needs a primary first
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"Not a folder: {path}")
        self._compare_session = BackupSession(p)
        return self._compare_session.manifest()

    @_endpoint
    def close_compare(self):
        self._compare_session = None
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
        a = self._need_session()
        b = self._compare_session
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
                result["rows"] = kept
                counts = {"added": 0, "removed": 0, "changed": 0}
                for r in kept:
                    counts[r["kind"]] += 1
                result["counts"] = counts
                return result

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
    def search_backup(self, query: str, side: str = "a"):
        # side="b" searches the compare robot - clicking a signal in a vs-mode
        # pane must search THAT robot, not always the primary one.
        return self._search_session(self._side_session(side), query)

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
    def list_files(self):
        s = self._need_session()

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
    def get_file(self, name: str):
        s = self._need_session()
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

    @_endpoint
    def get_photos(self):
        return self._photos_data(self._need_session())

    @_endpoint
    def get_image(self, rel: str):
        return self._image_data(self._need_session(), rel)

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
        e = library.link_camera(camera_id, robot_id)
        if e is None:
            raise ApiError("NOT_FOUND", "camera not in library")
        return e

    @_endpoint
    def lib_auto_link(self):
        """Auto-link unlinked cameras to robots by matching the station+robot in
        their names. Manual links are preserved. Returns linked/ambiguous/unmatched."""
        return library.auto_link_cameras()

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
    def cvx_remote_mouse(self, session_id: str, event_id: int, x: int, y: int):
        sess = self._cvx.get(session_id)
        if sess is None:
            raise ApiError("NO_SESSION", "unknown remote session")
        sess.send_mouse(int(event_id), int(x), int(y))
        return True

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
    def get_settings(self):
        return settings.load()

    @_endpoint
    def set_setting(self, key: str, value):
        settings.set_value(key, value)
        return True

    # -- library --------------------------------------------------------------
    # The saved set of robots (PLANT/LINE/ROBOT) + per-robot backup history.
    # Persists to %APPDATA%\BackupViewer\library.json (see library.py).

    def _draft_from_session(self, s: BackupSession, path: str) -> dict:
        """A library-entry draft prefilled from a parsed backup: robot name,
        model, F-number from the manifest/summary, and IPs from the SUMMARY.DG
        ethernet host table. Best-effort - a sparse backup just yields blanks."""
        m = s.manifest()
        ips: list[str] = []
        model = ""
        ident: dict = {}
        try:
            ov = self._build_summary(s)
            ident = ov.get("identity") or {}
            for h in (ov.get("ethernet") or {}).get("hosts", []):
                addr = h.get("addr")
                if addr and addr not in ips:
                    ips.append(addr)
            model = ident.get("robot_model", "") or ""
        except ApiError:
            pass
        # name/F-number: the .LS report header, then the SUMMARY identity
        # ($HOSTNAME), and only as a last resort the folder name.
        return {
            "robot": m["robot_name"] or ident.get("robot_name", "") or Path(path).name,
            "model": model,
            "f_number": m["f_number"] or ident.get("f_number", "") or "",
            "ips": ips,
            "latest_path": path,
            "backup_type": m["backup_type"],
        }

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

    @_endpoint
    def lib_rescan(self):
        """Rebuild the library from the folder tree (picks up copied-in folders)
        and return the reconciled set."""
        root = settings.library_root()
        data = self._scan_with_progress(root)
        self._set_lib_sig(root, library.scan_signature(root))   # this scan IS the fresh baseline
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

    def _scan_with_progress(self, root):
        """library.scan_library_root with the shared progress snapshot raised so
        lib_scan_progress polls (the home tab's loading bar) can watch it move."""
        def tick(done, total, current):
            with self._lib_progress_lock:
                self._lib_progress.update(done=done, total=total, current=current)
        with self._lib_progress_lock:
            self._lib_progress.update(active=True, done=0, total=0, current="")
        try:
            return library.scan_library_root(root, progress=tick)
        finally:
            with self._lib_progress_lock:
                self._lib_progress["active"] = False

    @_endpoint
    def lib_scan_progress(self):
        """The running library scan's snapshot (inactive zeros between scans) —
        polled by the home tab while a lib_list/lib_rescan call is in flight."""
        with self._lib_progress_lock:
            return dict(self._lib_progress)

    @_endpoint
    def lib_list(self):
        """The library, freshly rescanned whenever the folder tree changed since
        the last look (files are law - the tree IS the library, so Explorer
        copies/deletes just show up). Unchanged tree -> the cached state, with
        no scan and no library.json rewrite. An unreachable root always takes
        the scan path, which serves the last known library marked stale."""
        root = settings.library_root()
        if self._lib_sig is None and not self._lib_seeded:
            self._lib_seeded = True
            self._lib_sig = self._saved_lib_sig()
        sig = library.scan_signature(root)
        if sig != self._lib_sig or not sig:
            data = self._scan_with_progress(root)
            # store the POST-scan signature: NTFS flushes directory-mtime
            # updates lazily, and the scan's own walk forces the flush - the
            # settled value is the one future listings will see.
            self._set_lib_sig(root, library.scan_signature(root))
            return data
        return library.list_robots()

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
        e = library.add_robot(entry or {})
        return self._materialize_robot_folder(e)

    @_endpoint
    def lib_update(self, robot_id: str, patch: dict):
        e = library.update_robot(robot_id, patch or {})
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        return e

    @_endpoint
    def lib_remove(self, robot_id: str):
        return library.remove_robot(robot_id)

    @_endpoint
    def lib_set_hidden(self, robot_id: str, hidden: bool = True):
        """Hide/unhide a robot from the library view (overlay-only; survives a
        rescan). The everyday, non-destructive alternative to deleting."""
        e = library.set_hidden(robot_id, hidden)
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
            self._need_session()  # comparing needs a primary first
            self._compare_session = BackupSession(p)
            return self._compare_session.manifest()
        self._session = BackupSession(p)
        self._compare_session = None
        settings.set_value("last_folder", str(p))
        # carry the robot's identity + dated history so the backup view can show
        # a date-picker timeline (a folder opened directly leaves these unset).
        m = self._session.manifest()
        m["robot_id"] = e["id"]
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
        return {"renamed": renamed, "merged": merged, "failed": failed}

    @_endpoint
    def lib_merge(self, primary_id: str, secondary_ids):
        """Merge one or more secondary robots INTO a primary (folders + history).
        Cross-line pairs are refused (reported, not merged); a secondary the merge
        could fold nothing from comes back in `blocked` with its reason."""
        if isinstance(secondary_ids, str):
            secondary_ids = [secondary_ids]
        merged, refused, failed, blocked = [], [], [], []
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
            return library.relocate_robot(robot_id, plant, line, robot)
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
            # A Matrox camera self-names from the backup it just pulled (newest
            # saved-image sidecar) when its entry only carries a placeholder -
            # the camera twin of a robot naming itself from SUMMARY.DG - and
            # then auto-linking gets a fresh chance to seat it under its robot.
            # Best-effort: identity work must never fail a finished backup.
            if spec.get("device_type") == "camera-mtx":
                try:
                    ident = mtxbackup.name_from_backup(job.snapshot().get("dated_path", ""))
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
    def get_backup_progress(self, job_id: str):
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("NO_JOB", "unknown backup job")
        return job.snapshot()

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
    def health_scan_start(self, robot_ids: list, checks: list, queries=None):
        """Run selected checks (and/or free-text finds - a list of queries, each
        its own report section) across the given library robots on a worker
        thread; poll via scan_progress, stop via cancel_scan."""
        by_id = {e.get("id"): e for e in library.load()["robots"]}
        entries = [by_id[r] for r in (robot_ids or []) if r in by_id]
        if not entries:
            raise ApiError("BAD_SPEC", "no library robots to scan")
        ids = healthscan.valid_ids(checks)
        qs = healthscan.norm_queries(queries)
        if not ids and not qs:
            raise ApiError("BAD_SPEC", "pick at least one check or a find query")
        job = healthscan.HealthScanJob(entries, ids, qs, search_fn=self._search_session)
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
