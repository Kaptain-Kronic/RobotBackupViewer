"""PLC style table: style code -> TP program, from $STYLE_NAME / $STYLE_COMNT /
$STYLE_ENAB arrays (found in CELLIO.VA on GM cells; some loadouts keep them in
SYSTEM.VA). Unused slots hold '********...'.
"""
from __future__ import annotations

from .va import VaFile, parse_scalar_array


def parse_style_table(text: str) -> list[dict]:
    va = VaFile(text)
    names_rec = va.get("$STYLE_NAME")
    if names_rec is None:
        return []
    names = parse_scalar_array(names_rec)
    comments_rec = va.get("$STYLE_COMNT")
    comments = parse_scalar_array(comments_rec) if comments_rec else {}
    enab_rec = va.get("$STYLE_ENAB")
    enabled = parse_scalar_array(enab_rec) if enab_rec else {}

    out = []
    for idx, entry in sorted(names.items()):
        raw = entry.get("value")
        prog = str(raw).strip() if raw is not None else ""
        if not prog or prog.startswith("****"):
            continue
        cmt = comments.get(idx, {}).get("value")
        en = enabled.get(idx, {}).get("value")
        out.append({
            "style": idx[0],
            "program": prog,
            "comment": str(cmt).strip() if cmt else "",
            "enabled": en if isinstance(en, bool) else True,
        })
    return out
