"""CURPOS.DG (pose snapshot) and FRAME.DG (taught frames) - pure parsers.

CURPOS.DG is the controller's own position report at backup time: pendant
joint angles per group, the active Tool/Frame numbers, and the world TCP
(X..R plus config). It is what lets the 3D view pose the robot exactly as
it stood when the backup ran - and, together with the taught tool from
FRAME.DG, what validates the kinematic chain against the controller's own
numbers (see parsers/kinematics.py measure_flange).

FRAME.DG prints the taught frames sections ("Tool Frame", "Jog Frame",
"User Frame"); rows are one frame each: x y z w p r [comment], 1-decimal.
Row order = frame number 1..N. Only the tool table is parsed.
"""
from __future__ import annotations

import re

_JOINT = re.compile(r"^Joint\s+(\d+):\s+(-?[\d.]+)\s*$")
_GROUP = re.compile(r"^Group #:\s*(\d+)")
_TOOL_WORLD = re.compile(r"Tool #:\s*(\d+)\s*\nCURRENT WORLD POSITION:")
_AXVAL = re.compile(r"^\s*([XYZWPR]):\s*(-?[\d.]+)\s*$")
_DATE = re.compile(r"^DATE:\s*(.+?)\s*$", re.M)
_TOOLROW = re.compile(
    r"^\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*(.*)$")


def parse_curpos(text: str) -> dict:
    """-> {"date", "groups": [{"group", "joints": [deg...], "tool": int|None,
    "world": [x,y,z,w,p,r]|None}]} - group 1 first. Joints keep pendant
    order (J1..Jn, extended axes included when printed)."""
    date = ""
    m = _DATE.search(text)
    if m:
        date = m.group(1)

    groups: list[dict] = []
    cur: dict | None = None
    world_pending = False
    for raw in text.splitlines():
        line = raw.rstrip()
        g = _GROUP.match(line.strip())
        if g:
            cur = {"group": int(g.group(1)), "joints": [], "tool": None, "world": None}
            groups.append(cur)
            world_pending = False
            continue
        if cur is None:
            continue
        jm = _JOINT.match(line.strip())
        if jm:
            cur["joints"].append(float(jm.group(2)))
            continue
        if "CURRENT WORLD POSITION" in line:
            world_pending = True
            cur["world"] = []
            continue
        if world_pending:
            am = _AXVAL.match(line)
            if am:
                cur["world"].append(float(am.group(2)))
                if len(cur["world"]) == 6:
                    world_pending = False
            continue

    # the active tool number sits on the line right above the world block
    for gm, grp in zip(_TOOL_WORLD.finditer(text), groups):
        grp["tool"] = int(gm.group(1))
    for grp in groups:
        if grp["world"] is not None and len(grp["world"]) != 6:
            grp["world"] = None
    return {"date": date, "groups": groups}


def parse_tool_frames(text: str) -> list:
    """FRAME.DG -> [{"n", "xyzwpr": [6 floats], "comment"}] from the Tool
    Frame table (row order = tool number)."""
    out = []
    in_tools = False
    n = 0
    for raw in text.splitlines():
        s = raw.rstrip()
        if s.strip() == "Tool Frame":
            in_tools = True
            n = 0
            continue
        if not in_tools:
            continue
        if not s.strip() or s.strip().endswith("Frame"):
            if n:
                break  # table ended at the blank line / next section
            continue
        m = _TOOLROW.match(s)
        if not m:
            break
        n += 1
        out.append({
            "n": n,
            "xyzwpr": [float(m.group(i)) for i in range(1, 7)],
            "comment": m.group(7).strip(),
        })
    return out
