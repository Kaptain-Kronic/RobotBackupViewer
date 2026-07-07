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
import threading
import time
from pathlib import Path

from . import compare
from . import __version__
from . import discover
from . import ftpbackup
from . import library
from . import search as search_mod
from . import settings
from .parsers import (alarms, callgraph, dcs, frames, gmwizlog, io_dg,
                      ls_program, macros, magnet, mastering, mhvalves, payloads,
                      registers, styles, summary_dg, sysvars)
from .parsers.common import is_binary, read_text
from .session import BackupSession

log = logging.getLogger(__name__)

MAX_TEXT_BYTES = 2_000_000
HEX_PREVIEW_BYTES = 4096


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


class Api:
    def __init__(self):
        self._window = None
        self._session: BackupSession | None = None
        self._compare_session: BackupSession | None = None
        self._jobs: dict[str, ftpbackup.BackupJob] = {}  # active/finished backup jobs
        self._scans: dict[str, discover._ScanJob] = {}  # folder + network scan jobs
        self._lib_sig: str | None = None  # tree signature at the last scan (None = never)

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
                   if j.snapshot().get("status") not in ("done", "error", "cancelled"))

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
                if fire:
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
        mast_text = s.text("SYSMAST.VA")
        if mast_text is None:
            raise ApiError("MISSING_FILE", f"SYSMAST.VA not found in {s.root.name}")
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
        text = s.text(name)
        if text is None:
            raise ApiError("MISSING_FILE", f"{name} not found in this backup")
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

    # -- system vars ----------------------------------------------------------

    def _sysvar_index(self, s: BackupSession):
        """Cached (records list, name->record) for SYSTEM.VA."""
        def build():
            text = s.text("SYSTEM.VA")
            if text is None:
                raise ApiError("MISSING_FILE", "SYSTEM.VA not found in this backup")
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
        text = s.text("MHGRIPDT.VA")
        if text is None:
            raise ApiError("MISSING_FILE", "MHGRIPDT.VA not found in this backup")
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
        text = s.text("SYMOTN.VA")
        if text is None:
            raise ApiError("MISSING_FILE", "SYMOTN.VA not found in this backup")
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
        s = self._side_session(side)

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
        data = library.scan_library_root(root)
        self._lib_sig = library.scan_signature(root)   # this scan IS the fresh baseline
        return data

    @_endpoint
    def lib_list(self):
        """The library, freshly rescanned whenever the folder tree changed since
        the last look (files are law - the tree IS the library, so Explorer
        copies/deletes just show up). Unchanged tree -> the cached state, with
        no scan and no library.json rewrite. An unreachable root always takes
        the scan path, which serves the last known library marked stale."""
        root = settings.library_root()
        sig = library.scan_signature(root)
        if sig != self._lib_sig or not sig:
            data = library.scan_library_root(root)
            # store the POST-scan signature: NTFS flushes directory-mtime
            # updates lazily, and the scan's own walk forces the flush - the
            # settled value is the one future listings will see.
            self._lib_sig = library.scan_signature(root)
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
                            d, owner.get("robot", ""))
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
        path = e.get("latest_path", "") if which == "latest" else which
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"backup folder missing: {path}")
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
        return m

    # -- rename / merge / tidy + open backup location -------------------------
    # Fix IP-named legacy robots from their backup contents, merge duplicates,
    # and jump to a folder in Explorer. relocate_robot/merge_robots move the
    # on-disk tree WITH the entry (see library.py); these endpoints just preview,
    # release any open session over the affected tree, and apply.

    @_endpoint
    def lib_resolve_names(self, ids: list):
        """Preview 'fix names from backups' for the given robots: read each robot's
        REAL name from its latest backup and classify the change as noop / rename
        (clean) / merge (the proposed name+line collides with another robot - in the
        library OR within this very selection). Pure preview; the UI applies on
        confirm. Returns {items:[{id,current,proposed,plant,line,action,merge_into,
        reason}]}."""
        ids = list(ids or [])
        data = library.list_robots()
        by_id = {e["id"]: e for e in data["robots"]}

        proposals: dict = {}
        for rid in ids:
            e = by_id.get(rid)
            if e is None:
                continue
            proposed = e.get("robot") or ""
            lp = e.get("latest_path") or ""
            if lp and Path(lp).is_dir():
                try:
                    d = self._draft_from_session(BackupSession(Path(lp)), lp)
                    proposed = (d.get("robot") or "").strip() or proposed
                except Exception:  # noqa: BLE001 - a sparse/locked backup just yields no change
                    pass
            proposals[rid] = proposed

        def _existing_collision(rid, name, line):
            nm, ln = name.upper(), (line or "").upper()
            for e in data["robots"]:
                if e["id"] == rid:
                    continue
                if e.get("robot", "").upper() == nm and (e.get("line", "") or "").upper() == ln:
                    return e["id"]
            return None

        items, seen_in_sel = [], {}
        for rid in ids:
            e = by_id.get(rid)
            if e is None:
                continue
            cur, proposed, line = e.get("robot", ""), proposals[rid], e.get("line", "")
            action, merge_into, reason = "rename", None, ""
            if not proposed or proposed.upper() == cur.upper():
                action, reason = "noop", "name already matches the backup"
            else:
                col = _existing_collision(rid, proposed, line)
                key = (proposed.upper(), (line or "").upper())
                if col is not None:
                    action, merge_into, reason = "merge", col, "another robot already has this name"
                elif key in seen_in_sel:
                    action, merge_into, reason = "merge", seen_in_sel[key], "duplicate within the selection"
                else:
                    seen_in_sel[key] = rid
            items.append({"id": rid, "current": cur, "proposed": proposed,
                          "plant": e.get("plant", ""), "line": line,
                          "action": action, "merge_into": merge_into, "reason": reason})
        return {"items": items}

    @_endpoint
    def lib_apply_renames(self, items: list):
        """Apply clean renames (relocating their folders). `items` = [{id, plant?,
        line?, robot}]. A collision discovered at apply time surfaces as a 'merged'
        result rather than aborting the batch."""
        renamed, merged, failed = [], [], []
        for it in (items or []):
            rid = it.get("id")
            e = library.get_robot(rid)
            if e is None:
                failed.append({"id": rid, "error": "robot not in library"})
                continue
            plant = it.get("plant", e.get("plant", ""))
            line = it.get("line", e.get("line", ""))
            robot = it.get("robot", e.get("robot", ""))
            target = str(library._robot_dir_for(library._root(), plant, line, robot))
            self._release_sessions_under(e.get("history_root"), e.get("latest_path"), target)
            try:
                res = library.relocate_robot(rid, plant, line, robot)
            except library.PathGuard as ex:
                failed.append({"id": rid, "error": f"BAD_PATH: {ex}"})
                continue
            except (ValueError, OSError) as ex:
                failed.append({"id": rid, "error": str(ex)})
                continue
            (merged if res.get("action") == "merged" else renamed).append(res)
        return {"renamed": renamed, "merged": merged, "failed": failed}

    @_endpoint
    def lib_merge(self, primary_id: str, secondary_ids):
        """Merge one or more secondary robots INTO a primary (folders + history).
        Cross-line pairs are refused (reported, not merged)."""
        if isinstance(secondary_ids, str):
            secondary_ids = [secondary_ids]
        merged, refused, failed = [], [], []
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
            (refused if res.get("action") == "refused" else merged).append(res)
        return {"merged": merged, "refused": refused, "failed": failed}

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

    # -- take a new backup (FTP pull) ------------------------------------------

    @_endpoint
    def probe_controller(self, spec: dict):
        """Pre-flight reachability check - connect + sniff devices, no writes."""
        spec = spec or {}
        if not (spec.get("host") or "").strip():
            raise ApiError("BAD_SPEC", "robot host/IP is required")
        return ftpbackup.probe_controller(
            spec["host"].strip(), user=spec.get("user", ""), passwd=spec.get("passwd", ""),
            passive=spec.get("passive", True), port=spec.get("port", 21),
        )

    @_endpoint
    def diagnose_controller(self, spec: dict):
        """Read-only FTP probe to debug auto-naming on a live robot: writes a JSON
        summary to app.log (banner, cwd behaviour, listings, sniffed headers,
        resolved name) and returns it. No writes to the controller."""
        spec = spec or {}
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "robot host/IP is required")
        return discover.diagnose_controller(host, port=spec.get("port", 21))

    @_endpoint
    def start_backup(self, spec: dict):
        """Kick off an FTP backup on a worker thread; returns a job_id to poll."""
        spec = spec or {}
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "robot host/IP is required")
        root = (spec.get("dest_root") or settings.library_root())
        settings.set_value("library_root", str(root))
        settings.set_value("backup_root", str(root))   # keep the legacy key in sync

        def _register(job: ftpbackup.BackupJob):
            library.register_backup(
                job.library_match(), job.library_backup(),
                latest_path=job.snapshot().get("latest_path", ""),
            )

        job = ftpbackup.BackupJob(
            host, root, spec.get("plant", ""), spec.get("line", ""), spec.get("robot", ""),
            user=spec.get("user", ""), passwd=spec.get("passwd", ""),
            passive=spec.get("passive", True), port=spec.get("port", 21),
            devices=spec.get("devices"), note=spec.get("note", ""),
            recurse_fr=spec.get("recurse_fr", False), on_complete=_register,
        )
        self._jobs[job.id] = job
        threading.Thread(target=job.run, name="backup-" + job.id, daemon=True).start()
        return {"job_id": job.id}

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
        """Sweep a subnet for FANUC controllers on a worker thread; poll via
        scan_progress. spec={cidr?, port?}; cidr defaults to the local /24."""
        spec = spec or {}
        cidr = (spec.get("cidr") or "").strip() or discover.default_cidr()
        if not cidr:
            raise ApiError("BAD_SPEC", "could not determine a subnet to scan")
        job = discover.NetworkScanJob(cidr, port=spec.get("port", 21))
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

    @_endpoint
    def lib_bulk_add(self, entries: list, plant: str = "", line: str = ""):
        """Add many drafts at once under one plant/line, skipping existing
        robots. Each added robot gets its on-disk folder + sidecar immediately
        (files are law), so the batch requires a reachable library root."""
        if not Path(settings.library_root()).is_dir():
            raise ApiError("BAD_PATH", "the library folder is unreachable - cannot add robots")
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
