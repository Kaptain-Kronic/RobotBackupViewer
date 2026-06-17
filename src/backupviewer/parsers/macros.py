"""Macro table from SYSMACRO.VA ($MACROTABLE ARRAY[200] OF MN_MCR_TABLE).

Used as fallback when SUMMARY.DG (whose macro section has friendlier
assign-type strings) is absent. $ASSIGN_TYPE numeric codes map to the
pendant's assignment kinds.
"""
from __future__ import annotations

from .va import VaFile, parse_struct_fields

# Observed/pendant convention; unknown codes fall back to the raw number.
ASSIGN_TYPE_NAMES = {
    1: "",       # unassigned
    2: "MF",     # manual function key
    3: "UK",     # user key
    4: "SU",     # shift+user key
    5: "SP",     # softpanel
    6: "DI",
    7: "RI",
    8: "UI",
    9: "F",      # flag
    10: "M",     # marker
}


def parse_macros(text: str) -> list[dict]:
    rec = VaFile(text).get("$MACROTABLE")
    if rec is None:
        return []
    out = []
    for idx, fields in sorted(parse_struct_fields(rec).items()):
        name = fields.get("MACRO_NAME") or ""
        if not name:
            continue
        atype = fields.get("ASSIGN_TYPE")
        out.append({
            "index": idx[0],
            "name": name,
            "prog_name": fields.get("PROG_NAME") or "",
            "assign_type": ASSIGN_TYPE_NAMES.get(atype, str(atype) if atype else ""),
            "assign_id": fields.get("ASSIGN_ID"),
            "system": False,
        })
    return out
