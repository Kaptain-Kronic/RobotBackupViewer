"""Keyence CV-X `inspect.dat` - the program (inspection) name.

A CV-X carries no controller name we can read over FTP: the FTP banner gives the
MODEL only (`CV-X482D`), and `env.dat` holds paths and command templates but no
identity. What the camera DOES carry is the name of each inspection program, and
on a plant floor those are written like station tags - `RB130R01B01CAM1`,
`RB172R01CAM01DOORHINGE` - because that is what a tech types when creating the
program. So the program names are the camera's own account of what it is, and
they are already inside every backup we pull.

Format, established against 154 real `inspect.dat` files across 13 cameras
(2026-07-25):

  0x004c  program name, NUL-terminated, cp1252
  0x0398  the SAME name again - a byte-for-byte second copy

The two copies agreed in 154/154 files, so this parser REQUIRES them to agree.
That makes the read self-validating: a file we have mis-seeked or that isn't
really an inspect.dat disagrees and yields nothing, rather than handing the
library a plausible-looking wrong name. (Nearby fields hold localized factory
defaults - `Neu115`, `Nouveau115` - which is exactly the sort of string a looser
"first text run" scan would have grabbed by mistake.)

Pure: bytes in, str/dict out. No file I/O, no state.
"""
from __future__ import annotations

import collections
import re

NAME_OFFSET = 0x4C
NAME_ECHO_OFFSET = 0x398
NAME_MAX = 64            # generous: the longest real name seen was 38 bytes

# a name that is really a factory default in some UI language, not a station tag
_DEFAULT_NAME_RE = re.compile(r"^(?:neu|nouveau|nuevo|nuovo|new|program|prog)\s*\d*$",
                              re.IGNORECASE)


def _field(data: bytes, off: int) -> str:
    raw = data[off:off + NAME_MAX]
    if len(raw) < 1:
        return ""
    return raw.split(b"\x00")[0].decode("cp1252", "replace").strip()


def program_name(data: bytes) -> str:
    """The inspection program's name, or "" when this doesn't prove to be one.

    Requires the file to be long enough to hold both copies and the two to
    match - see the module docstring for why that check is the point."""
    if not data or len(data) < NAME_ECHO_OFFSET + NAME_MAX:
        return ""
    name = _field(data, NAME_OFFSET)
    if not name or name != _field(data, NAME_ECHO_OFFSET):
        return ""
    return name


def is_default_name(name: str) -> bool:
    """True for the controller's own untouched default (`Neu115`, `Nouveau004`),
    which names the program slot rather than the camera."""
    return bool(_DEFAULT_NAME_RE.match((name or "").strip()))


def camera_name(names_by_program: dict) -> str:
    """One name for the camera, out of {program dir -> program name}.

    A camera holds several programs and their names drift: dated saves
    (`RB200R01B01CAM1_20250513`), variants (`RB160R1B1CAM1 TEST`), typos
    (`RB130R01B01CAM2og`). The name that repeats across programs is the camera's
    steady identity, so the most frequent one wins; ties break toward the LOWEST
    program number, which is the oldest slot and in practice the plainest name.
    Factory defaults are ignored. Verified to pick the right tag for all 13
    cameras in the sample set."""
    counts = collections.Counter()
    first_seen: dict = {}
    for prog in sorted(names_by_program or {}):
        name = (names_by_program.get(prog) or "").strip()
        if not name or is_default_name(name):
            continue
        counts[name] += 1
        first_seen.setdefault(name, str(prog))
    if not counts:
        return ""
    best = max(counts, key=lambda n: (counts[n], -_prog_sort(first_seen[n])))
    return best


def _prog_sort(prog: str) -> int:
    """Program dirs are numbered (`000`, `001`, `011`); anything unnumbered
    sorts last so a real slot always wins a tie."""
    try:
        return int(prog)
    except (TypeError, ValueError):
        return 1 << 30
