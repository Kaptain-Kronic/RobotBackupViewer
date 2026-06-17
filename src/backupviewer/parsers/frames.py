"""Tool (UTOOL) and user (UFRAME) frame data.

SYSFRAME.VA:  $MNUTOOL / $MNUFRAME  ARRAY[ngroups,20] OF POSITION,
              $MNUTOOLNUM / $MNUFRAMENUM  ARRAY[ngroups] OF BYTE (active numbers)
FRAMEVAR.VA:  [TPFDEF]SETUP_DATA ARRAY[8,3,61] OF FRMSET_T holds the names:
              SETUP_DATA[group, type, index].$COMMENT
              type dim: 1=tool, 2=jog, 3=uframe (FANUC pendant convention).
"""
from __future__ import annotations

from .va import VaFile, parse_position_array, parse_scalar_array, parse_struct_fields

FRAMEVAR_TYPE = {1: "tool", 2: "jog", 3: "uframe"}


def parse_framevar_comments(text: str) -> dict[str, dict[tuple[int, int], str]]:
    """{"tool": {(group, index): "PH02 PIN"}, "jog": {...}, "uframe": {...}}"""
    va = VaFile(text)
    rec = va.get("SETUP_DATA")
    out: dict[str, dict[tuple[int, int], str]] = {v: {} for v in FRAMEVAR_TYPE.values()}
    if rec is None:
        return out
    for idx, fields in parse_struct_fields(rec).items():
        if len(idx) != 3:
            continue
        group, ftype, num = idx
        comment = fields.get("COMMENT")
        if comment and ftype in FRAMEVAR_TYPE:
            out[FRAMEVAR_TYPE[ftype]][(group, num)] = comment
    return out


def build_frames_model(sysframe_text: str, framevar_text: str | None = None) -> dict:
    va = VaFile(sysframe_text)
    framevar = VaFile(framevar_text) if framevar_text else None
    comments = parse_framevar_comments(framevar_text) if framevar_text else {
        "tool": {}, "jog": {}, "uframe": {}
    }

    def entries(rec_name: str, kind: str, source: VaFile | None = None) -> dict[str, list[dict]]:
        rec = (source or va).get(rec_name)
        if rec is None:
            return {}
        groups: dict[str, list[dict]] = {}
        for idx, pos in parse_position_array(rec).items():
            if len(idx) != 2:
                continue
            group, num = idx
            entry = {"index": num, "comment": comments[kind].get((group, num), "")}
            if pos is None:
                entry["uninit"] = True
            else:
                entry.update(pos)
                if pos.get("comment"):
                    entry["comment"] = pos["comment"]
            groups.setdefault(str(group), []).append(entry)
        for lst in groups.values():
            lst.sort(key=lambda e: e["index"])
        return groups

    def active(rec_name: str) -> dict[str, int]:
        rec = va.get(rec_name)
        if rec is None:
            return {}
        return {
            str(idx[0]): entry["value"]
            for idx, entry in parse_scalar_array(rec).items()
            if isinstance(entry.get("value"), int)
        }

    return {
        "tools": entries("$MNUTOOL", "tool"),
        "frames": entries("$MNUFRAME", "uframe"),
        "jogs": entries("JOGFRAMES", "jog", framevar) if framevar else {},
        "active_tool": active("$MNUTOOLNUM"),
        "active_frame": active("$MNUFRAMENUM"),
    }
