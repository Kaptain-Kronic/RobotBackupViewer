"""Generic tokenizer for FANUC .VA ASCII variable dumps.

A .VA file is a sequence of variable records:

    [*SYSTEM*]$MNUFRAME  Storage: SHADOW  Access: RW  : ARRAY[2,20] OF POSITION
    [MDIO_MAIN]ASG_RACK_NO  Storage: DRAM  Access: RW  : ARRAY[47] OF BYTE
    [COMSET]IFC  Storage: DRAM  Access: RW  : INTEGER = Uninitialized

Everything until the next record-start line is the record body. Three body
shapes cover all files we read (NUMREG/POSREG/STRREG/SYSFRAME/FRAMEVAR/
SYSMACRO/DIOCFGSV):

  scalar arrays      [1] = 10  'Spot Count G1'
  position arrays    [1,1] =  ['comment'] [Group: n | Uninitialized]
                       Group: 1   Config: N U T, 0, 0, 0
                       X: ..  Y: ..  Z: ..   /   W: ..  P: ..  R: ..
                     or joint lines:  J1 =    -5.999 deg   J2 = ...
  struct fields      Field: SETUP_DATA[1,1,1].$COMMENT Access: RW: STRING[29] = 'PH02 PIN'
                     (only scalar-valued fields are captured; POSITION-valued
                     and nested-array fields are skipped - nothing we render
                     today needs them)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .common import coerce_scalar

_RECORD = re.compile(
    r"^\[([^\]]+)\](\S+)\s+Storage:\s+(\w+)(?:\s+Access:\s+(\w+))?\s*:\s*(.*)$"
)
_ARRAY_DECL = re.compile(r"^ARRAY\[([\d,\s]+)\]\s+OF\s+(.*?)\s*$")
_INDEXED = re.compile(r"^\s*\[([\d,\s]+)\]\s*=\s*(.*)$")
_TRAILING_COMMENT = re.compile(r"^(.*?)\s*'([^']*)'\s*$")
# '$' optional so KAREL struct fields (plain-named, no $) parse too; the index
# stays required - every record we run through parse_struct_fields is an array.
_FIELD = re.compile(
    r"^\s*Field:\s+\$?\w+\[([\d,\s]+)\]\.\$?(\w+)(?:\s+Access:\s+\w+)?:?\s*(.*)$"
)
_GROUP_CONFIG = re.compile(r"^\s*Group:\s*(\d+)\s*(?:Config:\s*(.*?)\s*)?$")
_AXIS_LINE = re.compile(r"([XYZWPR]):\s*(-?[\d.]+)")
_JOINT_LINE = re.compile(r"J(\d+)\s*=\s*(-?[\d.]+)\s*deg")


@dataclass
class VaRecord:
    section: str
    name: str
    storage: str
    access: str
    typedecl: str
    lines: list[str] = field(default_factory=list)

    @property
    def dims(self) -> tuple[int, ...]:
        m = _ARRAY_DECL.match(self.typedecl)
        if not m:
            return ()
        return tuple(int(x) for x in m.group(1).replace(" ", "").split(","))

    @property
    def base_type(self) -> str:
        m = _ARRAY_DECL.match(self.typedecl)
        if m:
            return m.group(2)
        return self.typedecl.split("=")[0].strip()

    @property
    def scalar_value(self):
        if "=" in self.typedecl:
            return coerce_scalar(self.typedecl.split("=", 1)[1])
        return None


def iter_records(text: str) -> Iterator[VaRecord]:
    rec: VaRecord | None = None
    for line in text.splitlines():
        m = _RECORD.match(line)
        if m:
            if rec is not None:
                yield rec
            rec = VaRecord(
                section=m.group(1),
                name=m.group(2),
                storage=m.group(3),
                access=m.group(4) or "",
                typedecl=m.group(5).strip(),
            )
        elif rec is not None:
            rec.lines.append(line)
    if rec is not None:
        yield rec


class VaFile:
    def __init__(self, text: str):
        self.records: list[VaRecord] = list(iter_records(text))
        self._by_name: dict[str, VaRecord] = {}
        for r in self.records:
            self._by_name.setdefault(r.name.upper(), r)

    def get(self, name: str) -> VaRecord | None:
        return self._by_name.get(name.upper())


def _parse_index(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.replace(" ", "").split(","))


def parse_scalar_array(rec: VaRecord) -> dict[tuple[int, ...], dict]:
    """{(1,): {"value": 10, "comment": "Spot Count G1"}}.

    A trailing quoted string is the COMMENT when something precedes it
    ([1] = 10  'Spot Count G1') but the VALUE when nothing does - string-typed
    arrays dump their values quoted ([4] = 'STYLE04').
    """
    out: dict[tuple[int, ...], dict] = {}
    for line in rec.lines:
        m = _INDEXED.match(line)
        if not m:
            continue
        idx = _parse_index(m.group(1))
        rest = m.group(2).strip()
        entry: dict = {}
        cm = _TRAILING_COMMENT.match(rest)
        if cm and cm.group(1).strip():
            entry["comment"] = cm.group(2)
            entry["value"] = coerce_scalar(cm.group(1))
        elif cm:
            entry["value"] = cm.group(2)
        else:
            entry["value"] = coerce_scalar(rest)
        out[idx] = entry
    return out


def parse_position_array(rec: VaRecord) -> dict[tuple[int, ...], dict | None]:
    """Decode POSITION-array bodies (SYSFRAME $MNUTOOL/$MNUFRAME, POSREG).

    Entry value is None for Uninitialized, else:
      {"comment": str?, "group": int?, "config": str?,
       "kind": "cartesian"|"joint", "x".."r": float?  |  "joints": [floats]}
    """
    out: dict[tuple[int, ...], dict | None] = {}
    cur: dict | None = None

    for line in rec.lines:
        m = _INDEXED.match(line)
        if m:
            idx = _parse_index(m.group(1))
            rest = m.group(2).strip()
            cur = {}
            cm = re.match(r"^'([^']*)'\s*(.*)$", rest)
            if cm:
                cur["comment"] = cm.group(1)
                rest = cm.group(2).strip()
            if "Uninitialized" in rest:
                cur["uninit"] = True
            gm = re.match(r"^Group:\s*(\d+)", rest)
            if gm:
                cur["group"] = int(gm.group(1))
            out[idx] = cur
            continue
        if cur is None:
            continue
        gm = _GROUP_CONFIG.match(line)
        if gm and "Group:" in line:
            cur["group"] = int(gm.group(1))
            if gm.group(2) is not None:
                cur["config"] = gm.group(2).strip(" ,")
            continue
        joints = _JOINT_LINE.findall(line)
        if joints:
            cur.setdefault("joints", {})
            for jn, val in joints:
                cur["joints"][int(jn)] = float(val)
            continue
        axes = _AXIS_LINE.findall(line)
        if axes and re.match(r"^\s*[XW]:", line):
            for ax, val in axes:
                cur[ax.lower()] = float(val)

    result: dict[tuple[int, ...], dict | None] = {}
    for idx, entry in out.items():
        if entry is None or entry.get("uninit"):
            kept = {"comment": entry.get("comment")} if entry and entry.get("comment") else None
            result[idx] = kept
            continue
        if "joints" in entry:
            joints = entry.pop("joints")
            entry["kind"] = "joint"
            entry["joints"] = [joints[k] for k in sorted(joints)]
        elif "x" in entry or "w" in entry:
            entry["kind"] = "cartesian"
        else:
            # index line seen but no data lines: treat as uninitialized
            result[idx] = {"comment": entry["comment"]} if entry.get("comment") else None
            continue
        result[idx] = entry
    return result


def parse_struct_fields(rec: VaRecord) -> dict[tuple[int, ...], dict]:
    """{(1,1,1): {"COMMENT": "PH02 PIN", "METHOD": 3}} from Field: lines.

    Scalar 'TYPE = value' fields are captured. A field declared as
    'ARRAY[n] OF ...' (no '=') is followed by indented '[i] = v' lines and
    becomes a plain list (e.g. SYSMAST $DMR_GRP[1].$MASTER_COUN).
    POSITION-valued fields are skipped.
    """
    out: dict[tuple[int, ...], dict] = {}
    pending: list | None = None  # [idx, fname, values]

    def flush():
        nonlocal pending
        if pending is not None:
            out.setdefault(pending[0], {})[pending[1]] = pending[2]
            pending = None

    for line in rec.lines:
        m = _FIELD.match(line)
        if m:
            flush()
            idx = _parse_index(m.group(1))
            fname = m.group(2)
            rest = m.group(3)
            if "=" not in rest:
                if "ARRAY[" in rest:
                    pending = [idx, fname, []]
                continue
            typedecl, _, value = rest.partition("=")
            if typedecl.strip().startswith("POSITION"):
                continue
            out.setdefault(idx, {})[fname] = coerce_scalar(value)
            continue
        if pending is not None:
            im = _INDEXED.match(line)
            if im:
                pending[2].append(coerce_scalar(im.group(2)))
            elif line.strip():
                flush()
    flush()
    return out
