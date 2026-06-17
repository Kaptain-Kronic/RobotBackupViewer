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
    };
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
_POS_START = re.compile(r'^P\[(\d+)(?::"([^"]*)")?\]\s*\{')
_GP = re.compile(r"^\s*GP(\d+):")
_UF_UT = re.compile(r"UF\s*:\s*(\S+?),\s*UT\s*:\s*(\S+?),")
_CONFIG = re.compile(r"CONFIG\s*:\s*'([^']*)'")
_AXIS = re.compile(r"([XYZWPR])\s*=\s*(\S+)\s*(?:mm|deg)")
_JOINT = re.compile(r"J(\d+)\s*=\s*(\S+)\s*deg")

_INT_ATTRS = {"PROG_SIZE", "LINE_COUNT", "MEMORY_SIZE", "VERSION"}


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
    return out
