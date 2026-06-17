"""Payload schedules (MENU > SYSTEM > Motion > Payload).

Stored in SYMOTN.VA as $PLST_GRP<g> ARRAY[10] OF PLST_GRP_T - one array per
motion group. Each schedule [n] carries:
  $COMMENT       schedule name
  $PAYLOAD       mass (kg)
  $PAYLOAD_X/Y/Z center of gravity (cm)
  $PAYLOAD_IX/IY/IZ moment of inertia (kg·cm²)
Values are read-only sysvars (the pendant writes them via KAREL); we present
them per the pendant mental model - one card per schedule, grouped by group.
"""
from __future__ import annotations

import re

from .va import VaFile, parse_struct_fields

_PLST = re.compile(r"^\$PLST_GRP(\d+)$")


def _num(v):
    return v if isinstance(v, (int, float)) else None


def build_payloads_model(symotn_text: str) -> dict:
    """{"groups": {"1": [{index, comment, mass, cg:[x,y,z], inertia:[ix,iy,iz],
    uninit}]}}. A schedule with no comment and all-zero mass/cg/inertia is
    flagged uninit (so the UI can hide empty slots, like the frames tab)."""
    va = VaFile(symotn_text)
    groups: dict[str, list[dict]] = {}
    for rec in va.records:
        m = _PLST.match(rec.name)
        if not m:
            continue
        schedules = []
        for idx, f in sorted(parse_struct_fields(rec).items()):
            mass = _num(f.get("PAYLOAD"))
            cg = [_num(f.get("PAYLOAD_X")), _num(f.get("PAYLOAD_Y")), _num(f.get("PAYLOAD_Z"))]
            inertia = [_num(f.get("PAYLOAD_IX")), _num(f.get("PAYLOAD_IY")), _num(f.get("PAYLOAD_IZ"))]
            comment = (f.get("COMMENT") or "").strip()
            # a real schedule has a name or a non-zero CG / inertia; mass alone
            # is not enough (FANUC's default is 100 kg, and -9999 is the "unset"
            # sentinel for groups the robot doesn't have)
            uninit = not (comment or any(cg) or any(inertia))
            schedules.append({
                "index": idx[0], "comment": comment,
                "mass": None if mass in (None, -9999.0, -9999) else mass,
                "cg": cg, "inertia": inertia, "uninit": uninit,
            })
        groups[str(int(m.group(1)))] = schedules
    return {"groups": groups}
