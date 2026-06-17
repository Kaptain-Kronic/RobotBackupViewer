"""Shared helpers for FANUC backup file parsing.

Facts these helpers encode (verified against a real MD backup):
- One backup mixes line endings (SUMMARY.DG is LF, .VA/.LS are CRLF) -> always splitlines().
- Content is ASCII-ish; decode as cp1252 with replacement so no file ever fails to read.
- '********' appears in .LS position data when values are masked.
- 'Uninitialized' appears wherever a variable has no value.
"""
from __future__ import annotations

import re
from pathlib import Path

MASKED = "********"

_QUOTED = re.compile(r"^'(.*)'\s*$")
_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def read_text(path: Path) -> str:
    return path.read_bytes().decode("cp1252", errors="replace")


def is_binary(path: Path, sniff: int = 4096) -> bool:
    with open(path, "rb") as f:
        return b"\x00" in f.read(sniff)


def coerce_scalar(s: str):
    """'10' -> 10, '.500000' -> 0.5, "'txt'" -> 'txt', 'TRUE' -> True, 'Uninitialized' -> None."""
    s = s.strip()
    if not s or s == "Uninitialized":
        return None
    if s == "TRUE":
        return True
    if s == "FALSE":
        return False
    m = _QUOTED.match(s)
    if m:
        return m.group(1)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_fanuc_datetime(date_s: str, time_s: str = "") -> str | None:
    """'97-09-30', '07:16:38' -> '1997-09-30T07:16:38'. Two-digit years >= 70 are 19xx."""
    m = _DATE.match(date_s.strip())
    if not m:
        return None
    yy, mm, dd = (int(g) for g in m.groups())
    year = 1900 + yy if yy >= 70 else 2000 + yy
    t = time_s.strip() if time_s and _TIME.match(time_s.strip()) else "00:00:00"
    return f"{year:04d}-{mm:02d}-{dd:02d}T{t}"
