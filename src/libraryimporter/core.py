r"""The seeding core: parse a robot list, plan it against a destination,
create the skeleton. Stateless and standard-library-only on purpose - the GUI
calls it today, BackupViewer itself can import it later (the 2.0 "import a
plant" idea), and a future DCDL parser is one more PARSERS entry emitting the
same normalized model.

The contract with BackupViewer's scanner (files are law):
    <dest>\<LINE>\<FULL NAME>\robot.json      (dest IS the plant folder)
robot.json is schema 2 - id + config only; the folder path is the identity.

The name-expansion, sidecar shape, and skip-what-they-already-have rules are
lifted from tools/seed_library.py (the CLI seeder, which stays as-is) and must
keep matching what src/backupviewer/library.py writes and scans.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from pathlib import Path

# ---- lifted from tools/seed_library.py --------------------------------------

LINE_RE = re.compile(r"^([A-Z]{2})(B\d{2,})$", re.IGNORECASE)      # RBB01 -> RB + B01
ROBOT_RE = re.compile(r"^\d{3}R\d{2}$", re.IGNORECASE)             # 080R01
FULL_RE = re.compile(r"^[A-Z]{2}\d{2,4}R\d{2}B\d{2}$", re.IGNORECASE)


def full_name(line: str, robot: str) -> str:
    """Expand a short robot key to the plant convention: line RBB01 + 080R01
    -> RB080R01B01. Already-full names and one-offs (labs etc.) pass through."""
    if FULL_RE.match(robot):
        return robot
    m = LINE_RE.match(line or "")
    if m and ROBOT_RE.match(robot or ""):
        return m.group(1).upper() + robot.upper() + m.group(2).upper()
    return robot


def read_json(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def line_ip_claims(line_dir: Path) -> set:
    """Every IP any robot.json in this line folder already claims."""
    ips: set = set()
    if not line_dir.is_dir():
        return ips
    try:
        for rdir in line_dir.iterdir():
            if rdir.is_dir():
                for ip in read_json(rdir / "robot.json").get("ips") or []:
                    if ip:
                        ips.add(ip)
    except OSError:
        pass
    return ips


def sidecar(ip: str) -> dict:
    """id + config only (schema 2): the folder's location/name IS the robot's
    plant/line/name - the app derives identity from the tree, never from here."""
    return {
        "schema": 2, "id": uuid.uuid4().hex,
        "model": "", "f_number": "", "ips": [ip],
        "ftp": {"user": "", "passive": True},
        "notes": "", "aliases": [],
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def configured_library_root() -> str:
    """The machine's BackupViewer library folder, if the app is installed and
    has one configured - '' otherwise (unlike the CLI seeder, no guessing:
    dest_warnings needs to know the difference)."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        root = read_json(Path(appdata) / "BackupViewer" / "settings.json").get("library_root", "")
        if root:
            return str(root)
    return ""


# ---- source parsing ----------------------------------------------------------

def _bad(p: Path, why: str) -> dict:
    return {"ok": False, "error": why, "name": p.name, "path": str(p),
            "lines": [], "robots": 0, "warnings": []}


# Line/robot keys become folder names verbatim - anything Windows can't take
# (or that would change the tree depth, like a slash) is skipped, not created.
_BAD_NAME = re.compile(r'[\\/:*?"<>|]')


def parse_robots_json(path) -> dict:
    """robots.json ({"LINE": {"ROBOT": "ip"}}) -> the normalized source model
    every parser must emit:
        {ok, error, name, path,
         lines: [{line, robots: [{robot, full, ip}]}],   # both levels sorted
         robots: total, warnings: [str]}
    Blank/non-string IPs and entries that aren't robot maps are skipped into
    warnings rather than failing the whole list."""
    p = Path(path)
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as ex:
        return _bad(p, f"could not read the file ({ex})")
    except ValueError:
        return _bad(p, "not valid JSON")
    if not isinstance(raw, dict) or not raw:
        return _bad(p, 'expected {"LINE": {"ROBOT": "ip"}}')

    lines, total, blank, bad_names, junk = [], 0, 0, 0, []
    for line in sorted(raw, key=str):
        members = raw[line]
        lname = str(line).strip()
        if not isinstance(members, dict):
            junk.append(lname or repr(line))
            continue
        if not lname or _BAD_NAME.search(lname):
            bad_names += 1
            continue
        rows = []
        for robot in sorted(members, key=str):
            rname = str(robot).strip()
            ip = members[robot].strip() if isinstance(members[robot], str) else ""
            if not rname or _BAD_NAME.search(rname):
                bad_names += 1
                continue
            if not ip:
                blank += 1
                continue
            rows.append({"robot": rname, "full": full_name(lname, rname), "ip": ip})
        if rows:
            lines.append({"line": lname, "robots": rows})
            total += len(rows)
    warnings = []
    if blank:
        warnings.append(f"{blank} robot(s) with a blank IP skipped")
    if bad_names:
        warnings.append(f"{bad_names} name(s) skipped (illegal folder characters)")
    if junk:
        shown = ", ".join(junk[:4]) + ("…" if len(junk) > 4 else "")
        warnings.append(f"{len(junk)} entr{'y' if len(junk) == 1 else 'ies'} skipped (not a robot map): {shown}")
    if not total:
        return _bad(p, "no robots with IPs found in the list")
    return {"ok": True, "error": "", "name": p.name, "path": str(p),
            "lines": lines, "robots": total, "warnings": warnings}


# One entry per source format; a DCDL parser slots in here later and the GUI
# doesn't change - it only ever sees the normalized model.
PARSERS = {".json": parse_robots_json}


def parse_source(path) -> dict:
    p = Path(path)
    fn = PARSERS.get(p.suffix.lower())
    if not fn:
        return _bad(p, f"no parser for '{p.suffix or p.name}' files (expected .json)")
    return fn(p)


# ---- planning ------------------------------------------------------------------

def plan(model: dict, dest) -> dict:
    """Diff the model against the destination plant folder: a robot is already
    PRESENT when its folder exists, when any robot in that line's folder
    already claims its IP (their tree may use different names - never
    duplicate), or when an earlier robot in the list claims the same IP.
    dest=None -> nothing present except in-list duplicates (the list renders
    before a destination is picked)."""
    base = Path(dest) if dest else None
    out, selectable, present = [], 0, 0
    for ln in model.get("lines") or []:
        line_dir = base / ln["line"] if base else None
        claims = line_ip_claims(line_dir) if line_dir else set()
        seen: set = set()
        rows = []
        for r in ln["robots"]:
            why = ""
            if line_dir is not None and (line_dir / r["full"]).exists():
                why = "folder"
            elif r["ip"] in claims:
                why = "ip"
            elif r["ip"] in seen:
                why = "dup"
            if why:
                present += 1
            else:
                seen.add(r["ip"])
                selectable += 1
            rows.append({**r, "present": bool(why), "why": why})
        out.append({"line": ln["line"], "robots": rows})
    return {"lines": out, "selectable": selectable, "present": present}


# ---- seeding -------------------------------------------------------------------

def seed(model: dict, dest, selection: dict, progress=None) -> dict:
    """Create <dest>/<LINE>/<FULL NAME>/robot.json for every selected robot.
    selection = {line: [robot keys as parsed]}. Presence is re-checked at
    write time (same rules as plan) so a stale page never duplicates anything;
    each sidecar is written atomically (tmp + replace). progress(done, total,
    line) is called once up front and once per finished line."""
    base = Path(dest)
    picked = []
    for ln in model.get("lines") or []:
        want = set(selection.get(ln["line"]) or [])
        rows = [r for r in ln["robots"] if r["robot"] in want]
        if rows:
            picked.append((ln["line"], rows))
    created = skipped = 0
    errors, by_line = [], []
    if progress:
        progress(0, len(picked), "")
    for done, (line, rows) in enumerate(picked, start=1):
        line_dir = base / line
        claims = line_ip_claims(line_dir)
        c0, s0 = created, skipped
        for r in rows:
            folder = line_dir / r["full"]
            if folder.exists() or r["ip"] in claims:
                skipped += 1
                continue
            try:
                folder.mkdir(parents=True, exist_ok=True)
                tmp = folder / "robot.json.tmp"
                tmp.write_text(json.dumps(sidecar(r["ip"]), indent=2), encoding="utf-8")
                tmp.replace(folder / "robot.json")
                claims.add(r["ip"])
                created += 1
            except OSError as ex:
                errors.append({"path": str(folder), "error": str(ex)})
        by_line.append({"line": line, "created": created - c0, "skipped": skipped - s0})
        if progress:
            progress(done, len(picked), line)
    return {"created": created, "skipped": skipped, "errors": errors,
            "by_line": by_line, "dest": str(base)}


# ---- destination sanity ----------------------------------------------------------

# The scanner's skip rules (src/backupviewer/library.py _skip_name): folders
# named like these are never read as a plant/line/robot. Copied, not imported -
# this module must stay standalone.
_DATE_RE = re.compile(r"^(?:\d{4}|\d{2})_\d{2}_\d{2}$")
_TIME_RE = re.compile(r"^\d{2}_\d{2}_\d{2}$")


def _invisible_name(n: str) -> bool:
    return (n.endswith((".__part", ".__tmp")) or n.lower() in ("latest", "_staged")
            or bool(_DATE_RE.match(n)) or bool(_TIME_RE.match(n)))


def dest_warnings(dest) -> list:
    """Soft honesty checks on the chosen plant folder - never blocking."""
    out = []
    d = Path(dest)
    if _invisible_name(d.name):
        out.append(f'BackupViewer skips folders named "{d.name}" (looks like backup data) - '
                   "pick a real plant name")
    root = configured_library_root()
    if root:
        try:
            dr, rr = d.resolve(), Path(root).resolve()
        except OSError:
            return out
        if dr == rr:
            out.append("that IS the library folder itself - pick (or create) the "
                       "plant folder inside it")
        elif rr not in dr.parents:
            out.append(f"heads up: this machine's BackupViewer library is {root} - "
                       "robots created here won't show up until the app's library "
                       "folder points at them")
        elif dr.parent != rr:
            out.append("that folder is nested deeper than library\\plant - robots "
                       "would appear under the wrong plant/line names")
    return out


def default_dest_start() -> str:
    """Where the destination folder dialog opens: the configured library if
    there is one, else the RobotBackups convention, else somewhere sane."""
    root = configured_library_root()
    if root and Path(root).is_dir():
        return root
    home = Path(os.environ.get("USERPROFILE", "") or Path.home())
    for cand in (home / "Documents" / "RobotBackups", home / "RobotBackups",
                 home / "Documents"):
        if cand.is_dir():
            return str(cand)
    return str(home)
