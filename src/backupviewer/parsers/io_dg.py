"""IO signal tables from IOCONFIG.DG (definitions + rack/slot/port) and
IOSTATE.DG (live states at backup time).

IOCONFIG.DG lines come in three shapes:

    DIN[   1]  *IMSTP                                          signal definition
    DOUT 2001  -2040   RACK:  33   SLOT:   3   PORT: 866       range assignment
    GIN    1   RACK:  89   SLOT:   1   PORT:  25   #NUM:   8   group assignment

IOSTATE.DG state columns are right-aligned and can run flush against the
bracket (GOUT[  93]20752  IDNSTotalSignA) or float two spaces off it
(DIN[   3]  ON  *SFSPD), so: state = first token after ']', comment =
everything after the 2+ spaces that follow the state.

FLG rows pack TWO signals per line:
    FLG[   1]  ON  Capwear Complete          FLG[ 513] OFF
so state lines are pre-split wherever 2+ spaces are followed by a full
'TYPE[ n] STATE' entry. The strict state-token requirement keeps comments
like 'UserGI[26]Bit 1' from being mistaken for a second column.
"""
from __future__ import annotations

import re

_DEF = re.compile(r"^([A-Z]+)\[\s*(\d+)\]\s+(.*?)\s*$")
_DEF_EMPTY = re.compile(r"^([A-Z]+)\[\s*(\d+)\]\s*$")
_STATE = re.compile(r"^([A-Z]+)\[\s*(\d+)\]\s*(\S+)\s\s+(.*?)\s*$")
_STATE_NOCMT = re.compile(r"^([A-Z]+)\[\s*(\d+)\]\s*(\S*)\s*$")
_COLSPLIT = re.compile(r"\s{2,}(?=[A-Z]+\[\s*\d+\]\s*(?:ON|OFF|-?\d+)(?:\s\s|\s*$))")
_RANGE_ASG = re.compile(
    r"^([A-Z]+)\s+(\d+)\s+-\s*(\d+)\s+RACK:\s*(\d+)\s+SLOT:\s*(\d+)\s+PORT:\s*(\d+)"
)
_GROUP_ASG = re.compile(
    r"^([A-Z]+)\s+(\d+)\s+RACK:\s*(\d+)\s+SLOT:\s*(\d+)\s+PORT:\s*(\d+)(?:\s+#NUM:\s*(\d+))?"
)

# .DG files use long type names; the pendant (and TP programs) use the short
# ones - the viewer displays short names exclusively (DI[279], never DIN[279])
PENDANT_NAMES = {
    "DIN": "DI", "DOUT": "DO",
    "GIN": "GI", "GOUT": "GO",
    "AIN": "AI", "AOUT": "AO",
    "FLG": "F",
}


def pendant_type(raw: str) -> str:
    return PENDANT_NAMES.get(raw, raw)


def parse_io_config(text: str) -> dict:
    """{"signals": {type: [{index, comment}]}, "ranges": [...], "groups": {(type, idx): {...}}}"""
    signals: dict[str, list[dict]] = {}
    ranges: list[dict] = []
    group_asg: dict[str, dict] = {}
    started = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not started:
            if line.strip().endswith("::"):
                started = True
            continue
        if not line.strip():
            continue
        m = _DEF.match(line) or _DEF_EMPTY.match(line)
        if m:
            comment = m.group(3) if m.lastindex >= 3 else ""
            signals.setdefault(m.group(1), []).append(
                {"index": int(m.group(2)), "comment": comment}
            )
            continue
        m = _RANGE_ASG.match(line)
        if m:
            ranges.append({
                "type": m.group(1), "start": int(m.group(2)), "end": int(m.group(3)),
                "rack": int(m.group(4)), "slot": int(m.group(5)), "port": int(m.group(6)),
            })
            continue
        m = _GROUP_ASG.match(line)
        if m:
            group_asg[f"{m.group(1)}:{m.group(2)}"] = {
                "rack": int(m.group(3)), "slot": int(m.group(4)),
                "port": int(m.group(5)),
                "num_bits": int(m.group(6)) if m.group(6) else None,
            }
    return {"signals": signals, "ranges": ranges, "groups": group_asg}


def parse_io_state(text: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    started = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not started:
            if line.strip().endswith("::"):
                started = True
            continue
        for segment in _COLSPLIT.split(line):
            segment = segment.rstrip()
            m = _STATE.match(segment)
            if m:
                out.setdefault(m.group(1), []).append({
                    "index": int(m.group(2)),
                    "state": m.group(3).strip(),
                    "comment": m.group(4),
                })
                continue
            m = _STATE_NOCMT.match(segment)
            if m:
                out.setdefault(m.group(1), []).append({
                    "index": int(m.group(2)), "state": m.group(3), "comment": "",
                })
    return out


def merge_io(config: dict | None, state: dict[str, list[dict]] | None) -> dict:
    """One table per signal type (pendant short names): definition + state +
    physical assignment, plus the assignment jump-list rows."""
    cfg_signals = (config or {}).get("signals", {})
    ranges = (config or {}).get("ranges", [])
    group_asg = (config or {}).get("groups", {})
    state = state or {}

    def assignment(sig_type: str, index: int) -> dict:
        key = f"{sig_type}:{index}"
        if key in group_asg:
            return group_asg[key]
        for r in ranges:
            if r["type"] == sig_type and r["start"] <= index <= r["end"]:
                return {
                    "rack": r["rack"], "slot": r["slot"],
                    "port": r["port"] + (index - r["start"]),
                    "num_bits": None,
                }
        return {}

    raw_types = sorted(set(cfg_signals) | set(state))
    signals: list[dict] = []
    for t in raw_types:
        disp = pendant_type(t)
        by_index: dict[int, dict] = {}
        for e in cfg_signals.get(t, []):
            by_index[e["index"]] = {
                "type": disp, "index": e["index"], "comment": e["comment"], "state": "",
            }
        for e in state.get(t, []):
            row = by_index.setdefault(
                e["index"], {"type": disp, "index": e["index"], "comment": "", "state": ""}
            )
            row["state"] = e.get("state", "")
            if e.get("comment") and not row["comment"]:
                row["comment"] = e["comment"]
        for i in sorted(by_index):
            row = by_index[i]
            asg = assignment(t, i)
            row["rack"] = asg.get("rack")
            row["slot"] = asg.get("slot")
            row["port"] = asg.get("port")
            row["num_bits"] = asg.get("num_bits")
            signals.append(row)

    types = sorted({s["type"] for s in signals})
    counts = {t: sum(1 for s in signals if s["type"] == t) for t in types}

    assignments = [
        {"type": pendant_type(r["type"]), "start": r["start"], "end": r["end"],
         "rack": r["rack"], "slot": r["slot"], "port": r["port"], "num_bits": None}
        for r in ranges
    ]
    for key, asg in group_asg.items():
        t, idx = key.split(":")
        assignments.append({
            "type": pendant_type(t), "start": int(idx), "end": int(idx),
            "rack": asg["rack"], "slot": asg["slot"], "port": asg["port"],
            "num_bits": asg.get("num_bits"),
        })
    assignments.sort(key=lambda a: (a["type"], a["start"]))

    return {"types": types, "counts": counts, "signals": signals, "assignments": assignments}
