"""Fleet health scan - run selected checks across selected library robots.

The scan opens each robot's backup the same way the viewer would (Latest
mirror, falling back to the newest dated snapshot) and reads it with the same
parsers the single-robot tabs use, so a check sees exactly what a tech would
see on screen. Checks are conservative and explainable: they FLAG, they never
fix, and every finding says why (the safety ethos - wrong data erodes trust
worse than missing data).

Adding a check = one entry in CHECKS + one function taking a _RobotData and
returning {"status", "summary", "detail"?}:
    status "flag" - a problem worth a look (red)
    status "info" - a notable fact, not a fault (e.g. "has advanced DCS")
    status "ok"   - checked and fine
    status "na"   - could not be checked (missing file/section); says why

Two checks are cross-robot: per robot they only collect a value; the verdicts
are handed out in a fleet-wide pass after the loop. cloned_mastering groups
master-count vectors (identical encoder counts on two robots = copied
mastering data); software_version groups editions per line (one robot running
a different edition than the rest of its line = drift worth a look - robots
on DIFFERENT lines legitimately differ, so the pass never crosses lines).

Checks carry a "category" (safety / mastering / programs / config) so the
picker can group them under headers; registry order = display order.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import library
from .parsers import alarms, callgraph, dcs, macros, mastering, payloads, styles, summary_dg
from .parsers.common import read_text
from .discover import _ScanJob
from .session import BackupSession

log = logging.getLogger(__name__)

_DCS_MAIN = "DCSVRFY.DG"
_ENDIS = re.compile(r"\b(ENABLE|DISABLE)D?\b")
_SXX = re.compile(r"^S\d{2}")
_BLAL = re.compile(r"\bBLAL\b", re.I)

CHECKS = [
    {"id": "adv_dcs", "label": "advanced DCS", "category": "safety",
     "desc": "has the 'DCS Pos./Speed Check' software option (position/speed safety zones)"},
    {"id": "sig_mismatch", "label": "DCS signature mismatch", "category": "safety",
     "desc": "safety signature current ≠ latched — DCS config changed without being re-latched"},
    {"id": "cip_safety", "label": "CIP safety disabled", "category": "safety",
     "desc": "the DCS report's CIP Safety section shows DISABLE"},
    {"id": "mastering", "label": "mastering incomplete", "category": "mastering",
     "desc": "a motion group not mastered / ref position not set / all-zero master counts"},
    {"id": "cloned_mastering", "label": "cloned mastering", "category": "mastering",
     "desc": "two scanned robots share identical master counts — mastering data was likely copied"},
    {"id": "battery_alarm", "label": "low battery alarm", "category": "mastering",
     "desc": "a BLAL / SRVO-065 low-battery alarm in the alarm history — replace the batteries before mastering is lost"},
    {"id": "style_broken", "label": "style table broken", "category": "programs",
     "desc": "a style points at a TP program that isn't in the backup"},
    {"id": "style_orphans", "label": "unused S## programs", "category": "programs",
     "desc": "S-number programs never reached from any style program's call tree"},
    {"id": "broken_calls", "label": "broken CALLs", "category": "programs",
     "desc": "programs CALLed or RUN but not in the backup (info — a partial backup type makes this normal)"},
    {"id": "software_version", "label": "software version", "category": "config",
     "desc": "the software edition on every robot — flags one that differs from the rest of its line"},
    {"id": "payload_unset", "label": "payloads never set", "category": "config",
     "desc": "every payload schedule is still at the factory default (mass 100, no CG/inertia) — no real payload entered"},
]
_ALL_IDS = [c["id"] for c in CHECKS]


def check_list() -> list[dict]:
    return [dict(c) for c in CHECKS]


def valid_ids(ids) -> list[str]:
    """The requested check ids, de-duped, in registry order."""
    want = set(ids or [])
    return [c for c in _ALL_IDS if c in want]


def _cap(names: list[str], n: int = 8) -> str:
    if len(names) <= n:
        return ", ".join(names)
    return ", ".join(names[:n]) + f" +{len(names) - n} more"


# -- per-robot lazy parse access ---------------------------------------------------

class _RobotData:
    """Lazy parse access for one robot's backup. Each source is read and parsed
    at most once per robot, and only when a selected check asks for it - a scan
    for software options never touches a single .LS file."""

    def __init__(self, sess: BackupSession):
        self.s = sess

    def _summary(self) -> dict | None:
        """The parsed SUMMARY.DG, shared by options/identity/macros - one parse
        no matter how many checks read it. None when the file is absent."""
        def build():
            text = self.s.text("SUMMARY.DG")
            return summary_dg.parse_summary(text) if text else None
        return self.s.cached("hs_summary", build)

    def options(self) -> list | None:
        """Software options from SUMMARY.DG, None when the file is absent."""
        smry = self._summary()
        return None if smry is None else (smry.get("options") or [])

    def identity(self) -> dict | None:
        """Controller identity (edition/version/servo/DCS...), None when absent."""
        smry = self._summary()
        return None if smry is None else (smry.get("identity") or {})

    def dcs(self) -> dict | None:
        """The parsed main DCS verify report, None when absent."""
        def build():
            text = self.s.text(_DCS_MAIN)
            return dcs.parse_dcs_report(text) if text else None
        return self.s.cached("hs_dcs", build)

    def mastering(self) -> list | None:
        def build():
            text = self.s.text("SYSMAST.VA")
            if not text:
                return None
            return mastering.parse_mastering(text)
        return self.s.cached("hs_mastering", build)

    def styles(self) -> list | None:
        """Style table from CELLIO.VA (some loadouts keep it in SYSTEM.VA);
        None when neither file yields one."""
        def build():
            for fname in ("CELLIO.VA", "SYSTEM.VA"):
                text = self.s.text(fname)
                if text:
                    table = styles.parse_style_table(text)
                    if table:
                        return table
            return None
        return self.s.cached("hs_styles", build)

    def program_texts(self) -> dict:
        def build():
            return {p.stem.upper(): read_text(p)
                    for p in sorted(self.s.program_files, key=lambda p: p.name.upper())}
        return self.s.cached("progtext", build)   # same key/shape api.py uses

    def macro_by_name(self) -> dict:
        """macro name -> program, best-effort ({} when neither source exists)."""
        def build():
            smry = self._summary()
            table = (smry.get("macros") or []) if smry else []
            if not table:
                text = self.s.text("SYSMACRO.VA")
                if text:
                    table = macros.parse_macros(text)
            return {m["name"]: m["prog_name"] for m in table if m.get("prog_name")}
        return self.s.cached("hs_macros", build)

    def call_graph(self) -> dict:
        """The CALL/RUN/macro graph over every .LS program, built once no
        matter how many checks walk it (orphans + broken calls share it)."""
        def build():
            return callgraph.build_call_graph(self.program_texts(), self.macro_by_name())
        return self.s.cached("hs_callgraph", build)

    def alarms(self) -> list | None:
        """Alarm rows across every ERR* history report, de-duped (ERRALL
        repeats what ERRACT/ERRHIST carry). None when the backup has none."""
        def build():
            files = self.s.alarm_files()
            if not files:
                return None
            rows, seen = [], set()
            for p in files:
                text = read_text(p)
                if not text:
                    continue
                for a in alarms.parse_alarm_file(text).get("rows") or []:
                    key = (a.get("seq"), a.get("datetime"), a.get("code"))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(a)
            return rows
        return self.s.cached("hs_alarms", build)

    def payload_groups(self) -> dict | None:
        """{group: [schedules]} from SYMOTN.VA, None when the file is absent."""
        def build():
            text = self.s.text("SYMOTN.VA")
            if not text:
                return None
            return payloads.build_payloads_model(text).get("groups") or {}
        return self.s.cached("hs_payloads", build)


# -- the checks --------------------------------------------------------------------

def _na(why: str) -> dict:
    return {"status": "na", "summary": why}


def _check_adv_dcs(ctx: _RobotData) -> dict:
    feats = ctx.options()
    if feats is None:
        return _na("no SUMMARY.DG in this backup")
    hit = next((o for o in feats if "dcs pos" in o.get("feature", "").lower()), None)
    if hit:
        ordno = hit.get("ord_no", "")
        return {"status": "info", "summary": "advanced DCS",
                "detail": hit["feature"] + (f" ({ordno})" if ordno else "")}
    return {"status": "ok", "summary": "not installed"}


def _check_sigs(ctx: _RobotData) -> dict:
    rep = ctx.dcs()
    if rep is None:
        return _na("no DCS report in this backup")
    sigs = rep.get("signatures") or []
    if not sigs:
        return _na("report lists no signatures")
    bad = [x for x in sigs if not x.get("match")]
    if bad:
        return {"status": "flag",
                "summary": f"{len(bad)} of {len(sigs)} signatures current ≠ latch",
                "detail": "; ".join(f"{x['name']}: {x['current']} vs {x['latch']}" for x in bad)}
    return {"status": "ok", "summary": f"all {len(sigs)} signatures match"}


def _check_cip(ctx: _RobotData) -> dict:
    rep = ctx.dcs()
    if rep is None:
        return _na("no DCS report in this backup")
    sec = next((x for x in rep.get("sections", []) if x.get("id") == "cip-safety"), None)
    if sec is None:
        return _na("no CIP safety section in the report")
    # the enable state row: prefer a row whose key mentions CIP, else the first
    # row carrying an ENABLE/DISABLE word (section layouts drift across editions)
    cands = []
    for r in sec.get("rows") or []:
        txt = r.get("value") if r.get("kind") == "kv" else r.get("text")
        m = _ENDIS.search(str(txt or "").upper())
        if m:
            cands.append((str(r.get("key") or r.get("text") or "").strip(), m.group(1)))
    if not cands:
        return _na("no enable/disable line in the CIP section")
    key, state = next(((k, s) for k, s in cands if "cip" in k.lower()), cands[0])
    if state == "DISABLE":
        return {"status": "flag", "summary": "CIP safety disabled",
                "detail": f"{key}: DISABLE" if key else "shows DISABLE"}
    return {"status": "ok", "summary": "enabled"}


def _check_mastering(ctx: _RobotData) -> dict:
    groups = ctx.mastering()
    if groups is None:
        return _na("no SYSMAST.VA in this backup")
    if not groups:
        return _na("no $DMR_GRP record")
    problems = []
    for g in groups:
        n = g.get("group")
        counts = g.get("master_counts") or []
        if not g.get("master_done"):
            problems.append(f"group {n} not mastered")
        elif counts and not any(counts):
            problems.append(f"group {n} mastered but every count is zero")
        elif not g.get("ref_done"):
            problems.append(f"group {n} ref position not set")
    if problems:
        return {"status": "flag", "summary": "; ".join(problems)}
    return {"status": "ok",
            "summary": "mastered" + (f" ({len(groups)} groups)" if len(groups) > 1 else "")}


def _mast_vector(ctx: _RobotData) -> list | None:
    """[[group, [counts...]], ...] for the fleet-wide clone pass; None when the
    robot has no usable counts."""
    groups = ctx.mastering()
    if not groups:
        return None
    vec = [[g["group"], list(g.get("master_counts") or [])] for g in groups]
    return vec if any(c for _, counts in vec for c in counts) else None


def _check_style_broken(ctx: _RobotData) -> dict:
    table = ctx.styles()
    if table is None:
        return _na("no style table (CELLIO/SYSTEM.VA)")
    texts = ctx.program_texts()
    missing = [t for t in table if t.get("program", "").upper() not in texts]
    # only an ENABLED style pointing nowhere is a fault - a disabled slot with
    # its program absent is a normal placeholder (GM ships MOVREPR* slots
    # disabled fleet-wide), so it rides along as detail, never a flag
    live = [t for t in missing if t.get("enabled", True)]
    parked = len(missing) - len(live)
    if live:
        items = [f"style {t['style']} → {t['program']}" for t in live]
        return {"status": "flag",
                "summary": f"{len(live)} enabled style{'s' if len(live) != 1 else ''}"
                           " point at missing programs",
                "detail": _cap(items) +
                          (f" (+{parked} disabled slots missing theirs)" if parked else "")}
    if parked:
        return {"status": "ok", "summary": "all enabled style programs present",
                "detail": f"{parked} disabled placeholder style(s) point at absent programs: " +
                          _cap([f"style {t['style']} → {t['program']}" for t in missing], 5)}
    return {"status": "ok", "summary": f"all {len(table)} style programs present"}


def _check_style_orphans(ctx: _RobotData) -> dict:
    table = ctx.styles()
    if table is None:
        return _na("no style table (CELLIO/SYSTEM.VA)")
    texts = ctx.program_texts()
    graph = ctx.call_graph()
    # everything reachable from the style roots, CALL/RUN/macro edges alike
    todo = [t["program"].upper() for t in table if t.get("program", "").upper() in texts]
    reach = set(todo)
    while todo:
        prog = todo.pop()
        for e in graph["calls"].get(prog, []):
            tgt = e.get("target", "").upper()
            if tgt and tgt not in reach:
                reach.add(tgt)
                todo.append(tgt)
    orphans = sorted(p for p in texts if _SXX.match(p) and p not in reach)
    if orphans:
        return {"status": "info",
                "summary": f"{len(orphans)} S## programs never reached from a style",
                "detail": _cap(orphans, 12)}
    return {"status": "ok", "summary": "every S## program is reachable"}


def _check_broken_calls(ctx: _RobotData) -> dict:
    """CALL/RUN targets with no program in the backup. Info, not a flag: some
    backup types simply don't carry every program, and a target that exists
    only as a binary (.TP teach-pendant image, .PC / .VA+.VR KAREL) is IN the
    backup - just not as source - so those never count as missing."""
    texts = ctx.program_texts()
    if not texts:
        return _na("no .LS programs in this backup")
    graph = ctx.call_graph()
    karel = {k.upper() for k in (getattr(ctx.s, "karel_programs", None) or {})}
    missing: dict[str, set] = {}
    sites = 0
    for prog, edges in graph["calls"].items():
        for e in edges:
            if e.get("kind") not in ("call", "run") or e.get("exists"):
                continue
            tgt = (e.get("target") or "").upper()
            if not tgt or tgt in karel:
                continue
            if ctx.s.find(tgt + ".TP") or ctx.s.find(tgt + ".PC"):
                continue
            missing.setdefault(tgt, set()).add(prog)
            sites += e.get("count") or 1
    if not missing:
        return {"status": "ok", "summary": "every CALL/RUN target is in the backup"}
    items = [t + " ← " + _cap(sorted(callers), 2) for t, callers in sorted(missing.items())]
    return {"status": "info",
            "summary": f"{len(missing)} called program{'s' if len(missing) != 1 else ''}"
                       f" not in the backup ({sites} call site{'s' if sites != 1 else ''})",
            "detail": _cap(items, 8)}


def _check_sw_version(ctx: _RobotData) -> dict:
    """Info inventory: the EDITION (techs quote editions - V8.33P/16), with the
    raw version / servo / DCS versions in the detail. The line-drift verdicts
    are handed out fleet-wide in _version_pass after the loop."""
    ident = ctx.identity()
    if ident is None:
        return _na("no SUMMARY.DG in this backup")
    ed = (ident.get("software_edition") or "").strip()
    ver = (ident.get("version") or "").strip()
    if not ed and not ver:
        return _na("no version info in SUMMARY.DG")
    bits = []
    if ed and ver:
        bits.append("version " + ver)      # when there's no edition, ver IS the summary
    if ident.get("servo_code"):
        bits.append("servo " + ident["servo_code"])
    if ident.get("dcs_version"):
        bits.append("DCS " + ident["dcs_version"])
    out = {"status": "info", "summary": ed or ver}
    if bits:
        out["detail"] = " · ".join(bits)
    return out


def _check_battery(ctx: _RobotData) -> dict:
    """BLAL / SRVO-065 (motor battery low) anywhere in the alarm history. The
    precursor to mastering loss: batteries that die take the encoder counts
    with them. Conservative - only a real battery-low code counts."""
    rows = ctx.alarms()
    if rows is None:
        return _na("no alarm history in this backup")
    hits = [a for a in rows
            if (a.get("facility") == "SRVO" and a.get("number") == 65)
            or _BLAL.search(a.get("message") or "")]
    if not hits:
        return {"status": "ok", "summary": "no low-battery alarms in history"}
    last = max(hits, key=lambda a: a.get("seq") or 0)
    active = any(a.get("active") for a in hits)
    summary = f"low-battery alarm in history ×{len(hits)}" + (" — ACTIVE at backup" if active else "")
    detail = (last.get("code") or "SRVO-065") + " " + (last.get("message") or "BLAL alarm")
    if last.get("datetime"):
        detail += " — last " + last["datetime"]
    return {"status": "flag", "summary": summary, "detail": detail}


def _check_payload(ctx: _RobotData) -> dict:
    """Flag ONLY when every schedule in every group is still factory-fresh
    (no comment, no CG, no inertia, mass at the 100 default / -9999 sentinel).
    Which schedule is ACTIVE is guesswork from a backup, so one configured
    schedule anywhere = ok - conservative wins."""
    groups = ctx.payload_groups()
    if groups is None:
        return _na("no SYMOTN.VA in this backup")
    scheds = [s for g in sorted(groups) for s in groups[g]]
    if not scheds:
        return _na("no payload records in SYMOTN.VA")
    # a non-default mass counts as configured even without a comment/CG (the
    # parser's uninit only weighs comment+CG+inertia - the tab hides slots on it)
    is_set = [s for s in scheds
              if not s.get("uninit") or s.get("mass") not in (None, 100, 100.0)]
    if not is_set:
        return {"status": "flag", "summary": "no payload schedule is set",
                "detail": f"all {len(scheds)} schedules across {len(groups)} group"
                          f"{'s' if len(groups) != 1 else ''} are at the factory default"
                          " (mass 100, no CG/inertia)"}
    named = [s["comment"] for s in is_set if s.get("comment")]
    out = {"status": "ok",
           "summary": f"{len(is_set)} of {len(scheds)} schedules set"}
    if named:
        out["detail"] = _cap(named, 6)
    return out


_CHECK_FNS = {
    "adv_dcs": _check_adv_dcs,
    "sig_mismatch": _check_sigs,
    "cip_safety": _check_cip,
    "mastering": _check_mastering,
    "style_broken": _check_style_broken,
    "style_orphans": _check_style_orphans,
    "broken_calls": _check_broken_calls,
    "software_version": _check_sw_version,
    "battery_alarm": _check_battery,
    "payload_unset": _check_payload,
}


def norm_queries(queries) -> list[str]:
    """Find queries as the job runs them: a bare string still works (the old
    single-query call shape), blanks drop, duplicates keep their first spot."""
    if isinstance(queries, str):
        queries = [queries]
    out, seen = [], set()
    for q in queries or []:
        q = (q or "").strip() if isinstance(q, str) else ""
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def _find_row(res: dict) -> dict:
    """Squash a backup-wide search result into one scan row (caller sets id)."""
    total = res.get("total", 0)
    if not total:
        return {"status": "ok", "summary": "no hits"}
    parts = []
    progs = res.get("programs") or []
    if progs:
        lines = sum(p.get("count", 0) for p in progs)
        parts.append(f"{len(progs)} programs ({lines} lines)")
    for k in ("io", "registers", "frames", "macros", "files"):
        n = len(res.get(k) or [])
        if n:
            parts.append(f"{n} {k}")
    top = _cap([p["program"] for p in progs], 5)
    return {"status": "info",
            "summary": f"{total} hits — " + " · ".join(parts),
            "detail": top}


# -- the job -----------------------------------------------------------------------

class HealthScanJob(_ScanJob):
    """Walk the selected robots sequentially on a worker thread. snapshot() is
    the poll payload: results grow as robots finish, so the UI can show
    findings live; the cloned-mastering + version-drift verdicts land just
    before "done". Each find query is its own row id ("find:0", "find:1"...)
    so the report can give every query its own section - the search corpus is
    built once per robot, so queries after the first are nearly free."""
    kind = "health"

    def __init__(self, robots: list[dict], check_ids: list[str], queries=None, *,
                 session_factory=BackupSession, search_fn=None):
        super().__init__()
        self.robots = robots
        self.check_ids = valid_ids(check_ids)
        self.queries = norm_queries(queries)
        self._session_factory = session_factory
        self._search_fn = search_fn

    def _row_ids(self) -> list[str]:
        return self.check_ids + [f"find:{i}" for i in range(len(self.queries))]

    def run(self):
        try:
            self._set(status="scanning", total=len(self.robots))
            results: list[dict] = []
            for e in self.robots:
                if self.cancelled:
                    self._set(status="cancelled")
                    return
                self._bump(current=e.get("robot") or "(unnamed)")
                results.append(self._scan_one(e))
                self._set_results(results)
            if "cloned_mastering" in self.check_ids:
                _cloned_pass(results)
            if "software_version" in self.check_ids:
                _version_pass(results)
            self._set_results(results)
            self._set(status="done")
        except Exception as ex:  # noqa: BLE001 - job thread boundary
            log.exception("health scan failed")
            self._set(status="error", error=f"{type(ex).__name__}: {ex}")

    def _scan_one(self, e: dict) -> dict:
        out = {"robot_id": e.get("id", ""), "robot": e.get("robot", ""),
               "line": e.get("line", ""), "plant": e.get("plant", ""), "checks": []}

        def all_na(why: str):
            for cid in self._row_ids():
                out["checks"].append({"id": cid, "status": "na", "summary": why})

        path = library.resolve_open_path(e)
        if not path or not Path(path).is_dir():
            all_na("backup folder missing on disk" if e.get("backups") or e.get("latest_path")
                   else "no backup taken yet")
            return out
        try:
            ctx = _RobotData(self._session_factory(Path(path)))
        except Exception as ex:  # noqa: BLE001 - one broken backup must not kill the scan
            all_na(f"backup unreadable: {ex}")
            return out

        for cid in self.check_ids:
            if cid == "cloned_mastering":
                out["_mast"] = _mast_vector(ctx)   # judged fleet-wide after the loop
                continue
            try:
                row = _CHECK_FNS[cid](ctx)
            except Exception as ex:  # noqa: BLE001
                log.exception("check %s failed on %s", cid, out["robot"])
                row = _na(f"check failed: {type(ex).__name__}: {ex}")
            row["id"] = cid
            out["checks"].append(row)
            if cid == "software_version":
                # the drift pass groups on the edition alone (a raw-version
                # fallback would cross-compare "V8.33" with "V8.33P/16")
                try:
                    out["_swv"] = ((ctx.identity() or {}).get("software_edition") or "").strip() or None
                except Exception:  # noqa: BLE001
                    out["_swv"] = None

        for i, q in enumerate(self.queries):
            if not self._search_fn:
                break
            try:
                row = _find_row(self._search_fn(ctx.s, q))
            except Exception as ex:  # noqa: BLE001
                log.exception("fleet find %r failed on %s", q, out["robot"])
                row = {"status": "na", "summary": f"search failed: {ex}"}
            row["id"] = f"find:{i}"
            out["checks"].append(row)
        return out


def _cloned_pass(results: list[dict]) -> None:
    """Fleet-wide verdicts for cloned_mastering: group the collected count
    vectors; identical non-trivial vectors on 2+ robots flag every member.
    A result without a "_mast" key was never scanned (unopenable backup) and
    already carries its n/a row - leave it alone."""
    groups: dict[str, list[dict]] = {}
    for r in results:
        if "_mast" not in r:
            continue
        vec = r.pop("_mast")
        if vec is None:
            r["checks"].append({"id": "cloned_mastering", "status": "na",
                                "summary": "no mastering data"})
            continue
        groups.setdefault(repr(vec), []).append(r)
    for members in groups.values():
        clones = len(members) >= 2
        for r in members:
            if clones:
                others = [(x["robot"] or "?") + (f" ({x['line']})" if x.get("line") and
                          x.get("line") != r.get("line") else "")
                          for x in members if x is not r]
                r["checks"].append({
                    "id": "cloned_mastering", "status": "flag",
                    "summary": "same master counts as " + _cap(others, 5),
                    "detail": "identical encoder counts almost always mean copied mastering data",
                })
            else:
                r["checks"].append({"id": "cloned_mastering", "status": "ok",
                                    "summary": "no matching counts in this scan"})


def _version_pass(results: list[dict]) -> None:
    """Fleet-wide verdicts for software_version: group the scanned robots by
    (plant, line) and compare editions WITHIN each line only - different lines
    legitimately run different editions. A minority edition upgrades that
    robot's info row to a flag; an even split flags the whole line (drift is
    certain, the odd one out isn't). A result without "_swv" was never scanned
    and already carries its n/a row; None means no edition to compare."""
    by_line: dict[tuple, list[tuple[dict, str]]] = {}
    for r in results:
        if "_swv" not in r:
            continue
        ed = r.pop("_swv")
        if ed and r.get("line"):
            by_line.setdefault((r.get("plant") or "", r["line"]), []).append((r, ed))
    for (_, line), members in by_line.items():
        eds: dict[str, int] = {}
        for _, ed in members:
            eds[ed] = eds.get(ed, 0) + 1
        if len(eds) < 2:
            continue
        top_n = max(eds.values())
        tops = [e for e, n in eds.items() if n == top_n]
        inventory = " · ".join(f"{e} ×{n}" for e, n in
                               sorted(eds.items(), key=lambda kv: (-kv[1], kv[0])))
        for r, ed in members:
            if len(tops) == 1 and ed == tops[0]:
                continue                          # the majority stays info
            row = next((c for c in r["checks"] if c.get("id") == "software_version"), None)
            if row is None:
                continue
            row["status"] = "flag"
            if len(tops) == 1:
                rest = "rest" if len(eds) == 2 else "most"
                row["summary"] = f"{ed} — {rest} of {line} runs {tops[0]}"
            else:
                row["summary"] = f"{ed} — {line} is split: {inventory}"
            row["detail"] = ((row["detail"] + " — ") if row.get("detail") else "") + \
                f"editions on {line}: {inventory}"
