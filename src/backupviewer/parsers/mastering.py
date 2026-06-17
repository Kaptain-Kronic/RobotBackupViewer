"""Mastering data from SYSMAST.VA.

    [*SYSTEM*]$DMR_GRP  Storage: SHADOW  Access: RW  : ARRAY[2] OF DMR_GRP_T
         Field: $DMR_GRP[1].$MASTER_DONE Access: RW: BOOLEAN = TRUE
         Field: $DMR_GRP[1].$MASTER_COUN  ARRAY[9] OF INTEGER
          [1] = -503786
          ...

Master counts are the encoder reference values techs transfer between robots -
exactly what you'd copy off the pendant's master/cal screen.
"""
from __future__ import annotations

from .va import VaFile, parse_struct_fields


def _trim_trailing_zeros(values: list, keep_min: int = 1) -> list:
    n = len(values)
    while n > keep_min and not values[n - 1]:
        n -= 1
    return values[:n]


def parse_mastering(text: str) -> list[dict]:
    rec = VaFile(text).get("$DMR_GRP")
    if rec is None:
        return []
    out = []
    for idx, fields in sorted(parse_struct_fields(rec).items()):
        counts = fields.get("MASTER_COUN") or []
        ref_counts = fields.get("REF_COUNT") or []
        entry = {
            "group": idx[0],
            "master_done": fields.get("MASTER_DONE") is True,
            "ref_done": fields.get("REF_DONE") is True,
            "master_counts": _trim_trailing_zeros(counts),
            "ref_counts": _trim_trailing_zeros(ref_counts),
        }
        # a group with no counts at all (all zero, not mastered) is still listed
        out.append(entry)
    return out
