"""The JS <-> Python surface. Exposed to the page as window.pywebview.api.

Every public method returns an envelope and never raises across the bridge:
    {"ok": True, "data": ...}
    {"ok": False, "error": {"code": "MISSING_FILE", "message": "..."}}
"""
from __future__ import annotations

import functools
import json
import logging
import threading
import time
from pathlib import Path

from . import compare
from . import __version__
from . import ftpbackup
from . import library
from . import search as search_mod
from . import settings
from .parsers import (alarms, callgraph, dcs, frames, gmwizlog, io_dg,
                      ls_program, macros, mastering, mhvalves, payloads,
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


class Api:
    def __init__(self):
        self._window = None
        self._session: BackupSession | None = None
        self._compare_session: BackupSession | None = None
        self._jobs: dict[str, ftpbackup.BackupJob] = {}  # active/finished backup jobs

    def bind(self, window, initial_backup: str | None = None):
        self._window = window
        if initial_backup:
            try:
                self._session = BackupSession(Path(initial_backup))
            except Exception:
                log.exception("could not open initial backup %s", initial_backup)

    # -- internals -----------------------------------------------------------
    # builders take an optional session so compare can run them against a
    # second backup; caches live on the session object, so both sides cache
    # independently

    def _need_session(self) -> BackupSession:
        if self._session is None:
            raise ApiError("NO_BACKUP", "No backup folder is open")
        return self._session

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
        # NOTE: the signal numbers (VALVETOA_SN, PART_PRES_SN, ...) are a
        # GM-specific namespace, NOT direct DI/DO indices - resolving them
        # against the IO table gives the wrong signal (PART_PRES_SN=1 ->
        # DI[1]=*IMSTP). They are shown raw until the true mapping is confirmed.
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
        return {"grippers": model["grippers"], "records": [sysvars.record_tree(r) for r in recs]}

    # -- payload schedules -----------------------------------------------------------

    @_endpoint
    def get_payloads(self, side: str = "a"):
        s = self._side_session(side)
        text = s.text("SYMOTN.VA")
        if text is None:
            raise ApiError("MISSING_FILE", "SYMOTN.VA not found in this backup")
        return s.cached("payloads", lambda: payloads.build_payloads_model(text))

    # -- compare two backups ---------------------------------------------------------

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

    @_endpoint
    def get_themes(self):
        from .app import resource_path

        themes = []
        seen = set()
        for d in (resource_path("web/themes"), settings.user_themes_dir()):
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    t = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(t, dict) and t.get("id") and t.get("colors") and t["id"] not in seen:
                        t["user"] = d == settings.user_themes_dir()
                        themes.append(t)
                        seen.add(t["id"])
                except (OSError, ValueError):
                    log.warning("bad theme file: %s", f)
        return {"themes": themes, "active": settings.get("theme", "serika_dark")}

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
        try:
            ov = self._build_summary(s)
            for h in (ov.get("ethernet") or {}).get("hosts", []):
                addr = h.get("addr")
                if addr and addr not in ips:
                    ips.append(addr)
            model = (ov.get("identity") or {}).get("robot_model", "") or ""
        except ApiError:
            pass
        return {
            "robot": m["robot_name"] or Path(path).name,
            "model": model,
            "f_number": m["f_number"],
            "ips": ips,
            "latest_path": path,
            "backup_type": m["backup_type"],
        }

    @_endpoint
    def lib_list(self):
        return library.list_robots()

    @_endpoint
    def lib_add(self, entry: dict):
        return library.add_robot(entry or {})

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
    def lib_scan_folder(self, path: str):
        """Parse a picked backup folder into a draft entry WITHOUT making it the
        active session - the 'add to library' flow shows this for editing."""
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"Not a folder: {path}")
        return self._draft_from_session(BackupSession(p), str(p))

    @_endpoint
    def lib_add_from_session(self, plant: str = "", line: str = ""):
        """Add the currently-open backup to the library."""
        s = self._need_session()
        draft = self._draft_from_session(s, str(s.root))
        draft["plant"] = plant
        draft["line"] = line
        return library.add_robot(draft)

    @_endpoint
    def lib_open(self, robot_id: str, which: str = "latest"):
        """Load a library robot's backup as the (single) active session.
        which='latest' opens its latest_path; any other value is a specific
        backup folder path from its history."""
        e = library.get_robot(robot_id)
        if e is None:
            raise ApiError("NOT_FOUND", "robot not in library")
        path = e.get("latest_path", "") if which == "latest" else which
        p = Path(path)
        if not p.is_dir():
            raise ApiError("NOT_FOUND", f"backup folder missing: {path}")
        self._session = BackupSession(p)
        self._compare_session = None
        settings.set_value("last_folder", str(p))
        return self._session.manifest()

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
    def start_backup(self, spec: dict):
        """Kick off an FTP backup on a worker thread; returns a job_id to poll."""
        spec = spec or {}
        host = (spec.get("host") or "").strip()
        if not host:
            raise ApiError("BAD_SPEC", "robot host/IP is required")
        root = (spec.get("dest_root") or settings.get("backup_root")
                or str(Path.home() / "RobotBackups"))
        settings.set_value("backup_root", str(root))

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
    def cancel_backup(self, job_id: str):
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError("NO_JOB", "unknown backup job")
        job.cancel()
        return True
