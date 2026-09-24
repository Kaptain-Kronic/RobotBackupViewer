"""TP program listings (.LS).

    /PROG  ATPOUNCE	  Macro
    /ATTR
    OWNER		= MNEDITOR;
    COMMENT		= "AT POUNCE V4.1";
    CREATE		= DATE 92-10-06  TIME 14:02:28;
    TCD:  STACK_SIZE	= 0,            <- multi-line attr, folded
    /APPL
    /MN
       1:  DO[71:Process1TaskOk]=OFF ;
    /POS
    P[1]{
       GP1:
        UF : 0, UT : 1,		CONFIG : 'N U T, 0, 0, 0',
        X =  1115.514  mm, ...          <- or J1=  .000 deg, or ******** (masked)
        E1=   900.000  mm               <- extended axis (a rail): the same
    };                                     "E1=" line in BOTH representations
    /END

parse_ls_header() is cheap (stops at /MN) and powers the program list view;
parse_ls_program() decodes everything.
"""
from __future__ import annotations

import re

from .common import MASKED, parse_fanuc_datetime

_PROG = re.compile(r"^/PROG\s+(\S+)[ \t]*(.*?)\s*$")
_ATTR = re.compile(r"^(\w+)\s*=\s*(.*?);?\s*$")
_DATE_TIME = re.compile(r"DATE\s+(\S+)\s+TIME\s+(\S+)")
_BODY_LINE = re.compile(r"^\s*(\d+):\s{0,2}(.*?)\s*;?\s*$")
# the same stream with the UNNUMBERED continuation rows of circular moves
# ("    :  P[3] 500mm/sec FINE ;") kept - mn_stream() needs them, body does not
_MN_LINE = re.compile(r"^\s*(\d+)?\s*:\s{0,2}(.*?)\s*;?\s*$")
_POS_START = re.compile(r'^P\[(\d+)(?::"([^"]*)")?\]\s*\{')
# just the ids, for the cheap census in count_positions - no comment, no body
_POS_ID = re.compile(r"^P\[(\d+)", re.M)
_GP = re.compile(r"^\s*GP(\d+):")
_UF_UT = re.compile(r"UF\s*:\s*(\S+?),\s*UT\s*:\s*(\S+?),")
_CONFIG = re.compile(r"CONFIG\s*:\s*'([^']*)'")
_AXIS = re.compile(r"([XYZWPR])\s*=\s*(\S+)\s*(?:mm|deg)")
_JOINT = re.compile(r"J(\d+)\s*=\s*(\S+)\s*deg")
# extended axes print as "E1=   900.000  mm" after the six joints or after
# W/P/R - never as J7. Verified on real rail-robot listings (178 E-lines, 26
# of them in joint representation). Unit kept as printed (rail = mm).
_EXT = re.compile(r"\bE(\d)\s*=\s*(\S+)\s*(mm|deg)")

_INT_ATTRS = {"PROG_SIZE", "LINE_COUNT", "MEMORY_SIZE", "VERSION"}

# a body line that IS a label definition: LBL[5] / LBL[5:HOME] (own statement)
_LBL_DEF = re.compile(r"^LBL\[\s*(\d+)\s*(?::([^\]]*))?\]$")
# a jump anywhere in a line: JMP LBL[5] (also mid-line: IF DI[1]=ON,JMP LBL[5])
_JMP_REF = re.compile(r"\bJMP\s+LBL\[\s*(\d+)")


def _pos_value(s: str):
    if s == MASKED:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_ls_header(text: str) -> dict:
    """Program name/type + /ATTR fields. Cheap: stops at /MN."""
    out: dict = {"name": "", "prog_type": "", "attrs": {}}
    section = None
    for line in text.splitlines():
        if line.startswith("/PROG"):
            m = _PROG.match(line)
            if m:
                out["name"] = m.group(1)
                out["prog_type"] = m.group(2).strip()
            continue
        if line.startswith("/ATTR"):
            section = "attr"
            continue
        if line.startswith(("/APPL", "/MN", "/POS", "/END")):
            break
        if section != "attr":
            continue
        m = _ATTR.match(line.strip())
        if not m:
            continue  # TCD continuation lines etc.
        key, val = m.group(1), m.group(2).strip()
        if key in ("CREATE", "MODIFIED"):
            dm = _DATE_TIME.search(val)
            out["attrs"][key.lower()] = (
                parse_fanuc_datetime(dm.group(1), dm.group(2)) if dm else val
            )
        elif key in _INT_ATTRS:
            try:
                out["attrs"][key.lower()] = int(val)
            except ValueError:
                out["attrs"][key.lower()] = val
        elif key == "COMMENT":
            out["attrs"]["comment"] = val.strip('"')
        else:
            out["attrs"][key.lower()] = val
    return out


def count_positions(text: str) -> int:
    """How many taught points a listing carries, without decoding any of them.

    parse_ls_header deliberately stops at /MN and so cannot answer this, and
    parse_ls_program decodes every axis of every point to do it - far too much
    to run over a controller's whole program library just to ask which listings
    have positions at all. This anchors at /POS and counts ids: ~19 ms across
    660 programs, against the ~10 ms the header pass that already reads them
    costs. Anchoring matters - a P[1] in a MOTION line is a reference, not a
    taught point, and counting those would call every program positional.
    """
    i = text.find(chr(10) + "/POS")
    return 0 if i < 0 else len(_POS_ID.findall(text, i))


def label_xref(body: list[dict]) -> list[dict]:
    """LBL definitions with the lines that JMP to them, in program order:
    [{id, name, line, jumps: [line, ...]}]. A JMP whose label is never
    defined becomes a trailing entry with line=None - a broken jump the UI
    shows honestly instead of dropping. Commented-out lines (!) count for
    neither side."""
    defs: list[dict] = []
    by_id: dict[int, dict] = {}
    for ln in body:
        m = _LBL_DEF.match(ln["text"].strip())
        if m:
            e = {"id": int(m.group(1)), "name": (m.group(2) or "").strip(),
                 "line": ln["n"], "jumps": []}
            defs.append(e)
            # duplicate definitions each get listed; jumps credit the first
            by_id.setdefault(e["id"], e)
    broken: dict[int, dict] = {}
    for ln in body:
        if ln["text"].lstrip().startswith("!"):
            continue
        for m in _JMP_REF.finditer(ln["text"]):
            i = int(m.group(1))
            e = by_id.get(i)
            if e is None:
                e = broken.setdefault(i, {"id": i, "name": "", "line": None, "jumps": []})
            e["jumps"].append(ln["n"])
    return defs + [broken[k] for k in sorted(broken)]


def mn_stream(text: str) -> list[dict]:
    """Every /MN instruction as the raw listing shows it.

    -> [{"n", "text", "active", "cont"}]

    Unlike parse_ls_program()["body"] this KEEPS the unnumbered continuation
    rows of circular moves - they carry the second P[..] of the move, so a
    motion reader cannot see a C move without them. A continuation belongs to
    the numbered line above it and inherits its remark state:

        7:C P[1]              -> n=7 cont=False
         :  P[4] 300mm/sec ;  -> n=7 cont=True

    active is False on "!" comment lines, "//" remarked lines, and every
    continuation of a remarked line - the robot runs none of them.
    """
    rows: list[dict] = []
    body = text.split("/MN", 1)
    body = body[1] if len(body) > 1 else ""
    for sec in ("/POS", "/END"):
        body = body.split(sec, 1)[0]
    n, active = 0, True
    for raw in body.splitlines():
        m = _MN_LINE.match(raw)
        if not m or not m.group(2):
            continue
        t = m.group(2).lstrip()      # listings pad remarks unevenly
        cont = not m.group(1)
        if not cont:                 # a numbered line sets the state
            n = int(m.group(1))
            active = not (t.startswith("!") or t.startswith("//"))
        rows.append({"n": n, "text": t, "cont": cont,
                     "active": active and not t.startswith("//")})
    return rows


def parse_ls_program(text: str) -> dict:
    out = parse_ls_header(text)
    out["appl"] = []
    out["body"] = []
    out["positions"] = []
    out["source"] = text

    section = None
    pos: dict | None = None
    grp: dict | None = None

    for line in text.splitlines():
        if line.startswith("/"):
            tag = line.split()[0] if line.split() else line
            section = {"/APPL": "appl", "/MN": "mn", "/POS": "pos", "/END": None}.get(tag, section)
            if line.startswith(("/PROG", "/ATTR")):
                section = None
            continue

        if section == "appl":
            s = line.strip().rstrip(";").strip()
            if s:
                out["appl"].append(s)
        elif section == "mn":
            m = _BODY_LINE.match(line)
            if m:
                out["body"].append({"n": int(m.group(1)), "text": m.group(2)})
        elif section == "pos":
            m = _POS_START.match(line.strip())
            if m:
                pos = {"id": int(m.group(1)), "comment": m.group(2) or "", "groups": []}
                out["positions"].append(pos)
                grp = None
                continue
            if pos is None:
                continue
            m = _GP.match(line)
            if m:
                grp = {"gp": int(m.group(1))}
                pos["groups"].append(grp)
                continue
            if grp is None:
                continue
            m = _UF_UT.search(line)
            if m:
                grp["uf"], grp["ut"] = m.group(1), m.group(2)
            m = _CONFIG.search(line)
            if m:
                grp["config"] = m.group(1)
            for en, v, unit in _EXT.findall(line):
                grp.setdefault("ext", []).append(
                    {"n": int(en), "value": _pos_value(v), "unit": unit})
                if v == MASKED:
                    grp["masked"] = True
            joints = _JOINT.findall(line)
            if joints:
                grp.setdefault("joints", {})
                for jn, v in joints:
                    grp["joints"][int(jn)] = _pos_value(v)
                grp["kind"] = "joint"
                if any(v == MASKED for _, v in joints):
                    grp["masked"] = True
                continue
            axes = _AXIS.findall(line)
            if axes:
                grp["kind"] = "cartesian"
                for ax, v in axes:
                    grp[ax.lower()] = _pos_value(v)
                    if v == MASKED:
                        grp["masked"] = True

    for p in out["positions"]:
        for g in p["groups"]:
            if "joints" in g:
                jd = g["joints"]
                g["joints"] = [jd.get(k) for k in sorted(jd)]
    out["labels"] = label_xref(out["body"])
    return out
