"""Roboguide robot-library `.def` files: FANUC's own kinematics as XML.

A `.def` (RobotCadFolder) describes one robot type. What we keep is the
kinematic chain — it is NOT Denavit-Hartenberg, it is simpler: every
`UnitLink Type="Axis"` carries `OffsetCADToAxis`, the ABSOLUTE placement
(position + FANUC W/P/R) of that joint's axis frame in CAD coordinates at
the home pose, and the joint rotates about that frame's local Z. The
`FacePlate/OffsetPost` node is the flange's home placement. `General
ZeroOffset` is the CAD→FANUC-world shift (its Z = the J2 shoulder height,
d1). Flags: `NegDirection="true"` = pendant-positive runs negative about
the frame Z (J2 on every FANUC arm we parsed); `ParallelLink
JointNumber="2"` on J3 = the pendant J3 value is slaved to J2 (physical
rotation uses q3+q2).

The convention (which flag means what, the coupling sign, the world
shift) was solved by brute force against a controller's own CURPOS.DG
report and validated on five robot families to <0.15 mm / <0.03 deg —
see parsers/kinematics.py for the chain math itself.

The type name is NOT in the XML; the filename carries it
("R2000iC_210F-3d_NEW.def"). normalize_type() maps both spellings of a
type (backup's "R-2000iC/210F-IF" vs the file's "R2000iC_210F") onto one
key: uppercase alphanumerics only, matched exact-then-prefix so dress
suffixes like "-IF" fall away.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_AXES = "XYZWPR"


def normalize_type(s: str) -> str:
    """'R-2000iC/210F-IF' -> 'R2000IC210FIF' (match key)."""
    return re.sub(r"[^A-Za-z0-9]+", "", s or "").upper()


def def_name_from_filename(filename: str) -> str:
    """'R2000iC_210F-3d_NEW.def' -> 'R2000iC_210F' (display fallback)."""
    base = re.sub(r"\.def$", "", filename, flags=re.I)
    return re.sub(r"-3d(_NEW)?$", "", base, flags=re.I)


def _offsets(node) -> tuple[list, list]:
    a = {k: float(node.get(k, 0)) for k in _AXES} if node is not None \
        else {k: 0.0 for k in _AXES}
    return [a["X"], a["Y"], a["Z"]], [a["W"], a["P"], a["R"]]


def parse_def(xml_text: str) -> dict:
    """.def XML -> the kinematic chain (joints sorted by number).

    {"zero": [x,y,z,w,p,r], "init_angles": [...], "envelope_name": str|"",
     "joints": [{"n", "p":[xyz], "wpr":[wpr], "neg": bool, "parallel": int|None}],
     "faceplate": {"p": [xyz], "wpr": [wpr]}}
    Raises ValueError when the file has no joints or no faceplate (not a
    robot arm def - EOAT/fixture defs share the schema).
    """
    root = ET.fromstring(xml_text)
    g = root.find("General")
    zero = [float(v) for v in (g.get("ZeroOffset") or "0,0,0,0,0,0").split(",")] \
        if g is not None else [0.0] * 6
    init = [float(v) for v in (g.get("InitJointAngles") or "").split(",") if v.strip()] \
        if g is not None and g.get("InitJointAngles") else []

    joints: dict[int, dict] = {}
    faceplate: dict | None = None
    envelope = ""

    def walk(node):
        nonlocal faceplate, envelope
        for u in node.findall("UnitLink"):
            jn = u.get("JointNumber")
            if u.get("Type") == "Axis" and jn and u.get("CadUnitIndex"):
                p, wpr = _offsets(u.find("OffsetCADToAxis"))
                par = u.find("ParallelLink")
                joints[int(jn)] = {
                    "n": int(jn), "p": p, "wpr": wpr,
                    "neg": u.get("NegDirection") == "true",
                    "parallel": int(par.get("JointNumber")) if par is not None else None,
                }
            if u.get("Type") == "FacePlate" and faceplate is None:
                p, wpr = _offsets(u.find("OffsetPost"))
                faceplate = {"p": p, "wpr": wpr}
            cad = u.get("CadFileName") or ""
            m = re.search(r"RANGE_([^\\/.]+)\.rcf", cad, re.I)
            if m and not envelope:
                envelope = m.group(1).replace("_", " ")
            walk(u)

    unit = root.find(".//RobotUnit")
    if unit is not None:
        walk(unit)
    if not joints or faceplate is None:
        raise ValueError("not a robot arm def (no joint chain / faceplate)")

    return {
        "zero": zero,
        "init_angles": init,
        "envelope_name": envelope,
        "joints": [joints[k] for k in sorted(joints)],
        "faceplate": faceplate,
    }
