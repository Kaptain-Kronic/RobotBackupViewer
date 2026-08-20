"""TP motion instructions (the /MN stream) -> structured moves.

    5:J P[1] 100% FINE ;
    6:L P[2:WELD START] 500mm/sec CNT50 Offset,PR[7] ;
    7:C P[3]
     :  P[4] 300mm/sec FINE ;          <- circular: via on line 7, end on the
                                          continuation, ONE move
    8:J PR[1:Home] 100% FINE ;
    9://L P[9] 100mm/sec FINE ;        <- remarked: the robot skips it

This reads instruction TEXT only - it never touches the /POS grammar, so
unlike a change to positions it owes ls_edit.py no twin (see
docs/subsystems/parsing.md section 2 for why the two .LS readers exist).

Two rules are load-bearing and easy to get wrong:

* **The destination is the reference immediately after the motion letter**,
  not "the first P[..] in the line". Options carry their own references -
  `Offset,PR[7]`, `Skip,LBL[3]`, `TIME BEFORE 0.5sec,DO[1]=ON` - and reading
  one of those as the destination sends the arm somewhere the robot never
  went.
* **Bracket contents nest.** `P[R[4]]` is an indirect reference whose inner
  `]` is not the end of the outer one, so the reference is scanned by depth,
  never by a non-greedy character class.

What this module does NOT claim: option semantics. An option's tokens are
kept verbatim and only the handful whose PRESENCE changes what a viewer may
honestly draw (offset, tool offset, incremental) are flagged - the rest ride
along as text. Nothing is dropped.
"""
from __future__ import annotations

import re

# a move whose destination reference opens right after the motion letter
_HEAD = re.compile(r"^([JLCA])\s+(.*)$", re.S)
# a continuation row leads with its own reference (the circular end point)
_REF_START = re.compile(r"^(A?PR?)\s*\[", re.I)

_SPEED = re.compile(
    r"^(?:max_speed|(R\[[^\]]*\]|\d+(?:\.\d+)?)\s*(%|mm/sec|cm/min|inch/min|deg/sec|msec|sec))$",
    re.I)
_TERM_ONE = re.compile(r"^(?:FINE|CNT\s*(?:\d+|R\[[^\]]*\]))$", re.I)
_TERM_OPEN = re.compile(r"^CNT$", re.I)          # "CNT R[282]" arrives as two tokens
# a plain position id, optionally with its pendant comment. ONLY the ":"
# comment form - "PR[7,3]" addresses one ELEMENT of a register, which is a
# number and not a position, so it falls through to "indirect" rather than
# quietly becoming PR[7]
_ID = re.compile(r"^\s*(\d+)\s*(?::\s*(.*?))?\s*$", re.S)
_INDIRECT = re.compile(r"^\s*(?:A?R\[|GP\d)", re.I)
_ANON = re.compile(r"^\s*\.{2,}\s*$")

# Almost every option is one whitespace token (`Offset,PR[7]`, `INC`, `ACC80`).
# These few are written WITH a space, so a plain split would shred them.
# _TAKES_ONE absorbs exactly one following token; a TIME trigger takes the
# REST of the tail, because its payload can itself contain a space
# ("TIME AFTER 0.2sec,CALL FOO") and nothing follows a trigger anyway.
_TAKES_ONE = {"DB", "ACC", "PSPD", "CD", "CR"}
_TAKES_REST = {("TIME", "BEFORE"), ("TIME", "AFTER")}

# mm/sec per one unit of each linear speed unit
_LINEAR = {"mm/sec": 1.0, "cm/min": 10.0 / 60.0, "inch/min": 25.4 / 60.0}

# A joint move is programmed as a PERCENTAGE of an axis speed the backup does
# not record anywhere - no .VA, .DG or listing in a FANUC backup carries the
# per-model maximum joint rates. So a joint move's duration cannot be derived,
# only assumed. This constant is the assumption, and every duration built on
# it is tagged "assumed" so the UI can say so.
ASSUMED_JOINT_DEG_S = 100.0

MOTION_NAMES = {"J": "joint", "L": "linear", "C": "circular", "A": "arc"}


def _take_ref(s: str):
    """Scan a bracketed reference off the FRONT of s, by bracket depth.

    -> (raw, rest) or (None, s). Handles the nested `P[R[4]]` case that a
    `\\[[^\\]]*\\]` pattern silently truncates.
    """
    m = _REF_START.match(s)
    if not m:
        return None, s
    i = s.index("[", m.start(1))
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return s[:j + 1], s[j + 1:].lstrip()
    return None, s        # unbalanced - the listing is malformed, say nothing


def _classify(raw: str) -> dict:
    """A destination reference -> {kind, id, comment, raw}.

    kind: "P" | "PR" | "anon" (P[...], never taught) | "indirect" (P[R[1]] -
    a listing cannot say which position that is).
    """
    head, _, _ = raw.partition("[")
    inner = raw[len(head) + 1:-1]
    kind = head.strip().upper()
    out = {"kind": kind, "id": None, "comment": "", "raw": raw}
    if _ANON.match(inner):
        out["kind"] = "anon"
        return out
    if _INDIRECT.match(inner):
        out["kind"] = "indirect"
        return out
    m = _ID.match(inner)
    if not m:
        out["kind"] = "indirect"      # something we cannot resolve statically
        return out
    out["id"] = int(m.group(1))
    out["comment"] = (m.group(2) or "").strip().strip('"')
    return out


def _speed(tok: str) -> dict | None:
    m = _SPEED.match(tok)
    if not m:
        return None
    if m.group(1) is None:            # max_speed
        return {"raw": tok, "value": None, "unit": "max_speed"}
    val, unit = m.group(1), m.group(2).lower()
    if val.upper().startswith("R["):  # register speed - unknowable from a listing
        return {"raw": tok, "value": None, "unit": unit, "register": val}
    return {"raw": tok, "value": float(val), "unit": unit}


def _term(toks: list[str], i: int):
    """-> (term dict|None, next index)."""
    if i >= len(toks):
        return None, i
    t = toks[i]
    if _TERM_OPEN.match(t) and i + 1 < len(toks):
        raw = t + " " + toks[i + 1]
        return {"raw": raw, "kind": "cnt", "value": _cnt_value(toks[i + 1])}, i + 2
    if _TERM_ONE.match(t):
        if t.upper() == "FINE":
            return {"raw": t, "kind": "fine", "value": None}, i + 1
        return {"raw": t, "kind": "cnt", "value": _cnt_value(t[3:])}, i + 1
    return None, i


def _cnt_value(s: str):
    s = s.strip()
    return int(s) if s.isdigit() else None      # CNT R[282] - a register, unknowable


def _options(toks: list[str], i: int) -> list[str]:
    """Remaining tokens as options, re-joining the few that span a space.

    Best effort by design: options_raw always carries the tail verbatim, so
    a shape this rule has never seen is still shown in full, never lost.
    """
    out = []
    while i < len(toks):
        pair = (toks[i].upper(), toks[i + 1].upper()) if i + 1 < len(toks) else None
        if pair in _TAKES_REST:
            out.append(" ".join(toks[i:]))
            break
        span = 2 if (toks[i].upper() in _TAKES_ONE and i + 1 < len(toks)) else 1
        out.append(" ".join(toks[i:i + span]))
        i += span
    return out


def _tail(rest: str) -> dict:
    """speed / termination / options off the text after the destination."""
    toks = rest.split()
    i = 0
    speed = None
    if i < len(toks):
        speed = _speed(toks[i])
        if speed:
            i += 1
    term, i = _term(toks, i)
    return {"speed": speed, "term": term, "options": _options(toks, i),
            "options_raw": " ".join(toks[i:])}


def _flags(step: dict) -> None:
    """Only the options whose PRESENCE changes what may honestly be drawn."""
    blob = " ".join(step["options"]).upper()
    # "_" is a word character, so \bOFFSET\b does NOT fire inside
    # TOOL_OFFSET - the two flags stay independent with no lookbehind
    step["offset"] = bool(re.search(r"\bOFFSET\b", blob))
    step["tool_offset"] = bool(re.search(r"\bTOOL_OFFSET\b", blob))
    step["incremental"] = bool(re.search(r"\bINC\b", blob))


def parse_motions(text: str) -> list[dict]:
    """Every ACTIVE motion instruction of a .LS listing, in program order.

    -> [{i, line, text, motion, motion_name, target, via, speed, term,
         options, options_raw, offset, tool_offset, incremental}]

    Remarked (`//`) and commented (`!`) lines are not motions the robot runs
    and are not returned - healthscan already reports them as skipped
    positions, which is the honest place for that finding.
    """
    from .ls_program import mn_stream

    out: list[dict] = []
    for row in mn_stream(text):
        if not row["active"]:
            continue
        if row["cont"]:
            # the end point of the circular move opened on the line above
            if not out or out[-1]["line"] != row["n"] or out[-1]["motion"] not in "CA":
                continue
            raw, rest = _take_ref(row["text"])
            if raw is None:
                continue
            step = out[-1]
            step["via"] = step["target"]          # line 1 named the VIA point
            step["target"] = _classify(raw)
            step["text"] += " / " + row["text"]
            step.update(_tail(rest))
            _flags(step)
            continue
        m = _HEAD.match(row["text"])
        if not m:
            continue
        raw, rest = _take_ref(m.group(2))
        if raw is None:
            continue
        step = {
            "i": len(out), "line": row["n"], "text": row["text"],
            "motion": m.group(1).upper(),
            "motion_name": MOTION_NAMES[m.group(1).upper()],
            "target": _classify(raw), "via": None,
        }
        step.update(_tail(rest))
        _flags(step)
        out.append(step)
    for k, step in enumerate(out):
        step["i"] = k
    return out


def step_duration_ms(step: dict, dist_mm: float | None = None,
                     travel_deg: float | None = None) -> tuple[float | None, str]:
    """How long this move takes, and how honestly we know it.

    -> (milliseconds | None, "derived" | "assumed" | "unknown")

    "derived"  - the listing states it: a linear feedrate over a distance we
                 computed, a deg/sec rate over a joint travel we computed, or
                 a time-specified move (`3sec`) which states the answer.
    "assumed"  - a percentage move, priced at ASSUMED_JOINT_DEG_S (see above).
    "unknown"  - a register-driven speed. Its value at run time is not in the
                 backup, so no number here would be evidence.
    """
    sp = step.get("speed")
    if not sp:
        return None, "unknown"
    unit, val = sp.get("unit"), sp.get("value")
    if unit in ("sec", "msec"):
        if val is None:
            return None, "unknown"
        return (val * 1000.0 if unit == "sec" else val), "derived"
    if val is None:                      # R[n] speed, or max_speed
        if unit == "max_speed" and travel_deg is not None:
            return travel_deg / ASSUMED_JOINT_DEG_S * 1000.0, "assumed"
        return None, "unknown"
    if unit in _LINEAR:
        if dist_mm is None:
            return None, "unknown"
        mm_s = val * _LINEAR[unit]
        return (dist_mm / mm_s * 1000.0 if mm_s > 0 else None), "derived"
    if unit == "deg/sec":
        if travel_deg is None or val <= 0:
            return None, "unknown"
        return travel_deg / val * 1000.0, "derived"
    if unit == "%":
        if travel_deg is None or val <= 0:
            return None, "unknown"
        return travel_deg / (ASSUMED_JOINT_DEG_S * val / 100.0) * 1000.0, "assumed"
    return None, "unknown"
