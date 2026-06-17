"""Numeric (R), position (PR) and string (SR) registers.

NUMREG.VA:  [*NUMREG*]$NUMREG ARRAY[999] OF Numeric Reg ->  [1] = 10  'Spot Count G1'
POSREG.VA:  [*POSREG*]$POSREG ARRAY[groups,200] OF Position Reg - entries are
            joint, cartesian, or Uninitialized; comment rides on the index line.
STRREG.VA:  [*STRREG*]$STRREG ARRAY[99] OF String Reg
"""
from __future__ import annotations

from .va import VaFile, parse_position_array, parse_scalar_array


def parse_numreg(text: str) -> list[dict]:
    return _scalar_list(text, "$NUMREG")


def parse_strreg(text: str) -> list[dict]:
    return _scalar_list(text, "$STRREG")


def _scalar_list(text: str, name: str) -> list[dict]:
    rec = VaFile(text).get(name)
    if rec is None:
        return []
    out = []
    for idx, entry in sorted(parse_scalar_array(rec).items()):
        out.append({
            "index": idx[0],
            "value": entry.get("value"),
            "comment": entry.get("comment", ""),
        })
    return out


def parse_posreg(text: str) -> list[dict]:
    rec = VaFile(text).get("$POSREG")
    if rec is None:
        return []
    out = []
    for idx, pos in sorted(parse_position_array(rec).items()):
        group, num = (idx if len(idx) == 2 else (1, idx[0]))
        entry: dict = {"group": group, "index": num, "comment": "", "kind": "uninit"}
        if pos is not None:
            entry["comment"] = pos.get("comment", "")
            if pos.get("kind"):
                entry.update(pos)
        out.append(entry)
    return out
