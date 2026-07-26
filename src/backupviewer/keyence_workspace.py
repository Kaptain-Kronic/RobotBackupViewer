"""Keyence CV-X *simulator workspace*: the `workspace.xml` + `SD1/` wrapper that
makes a pulled CV-X settings tree openable in the CV-X Series Simulator.

Why this exists: our FTP backup already pulls the exact tree the simulator wants
(the login dir IS `/SD1`, and `cv-x/setting/` under it is the whole config). The
only thing missing was the one-file manifest the simulator reads to find a
workspace. Write that, lay the pull down under `SD1/`, and a backup folder IS a
simulator workspace - no export step, no second copy.

Format reverse-engineered from 52 real workspace.xml files (5 simulator saves the
user made from live cameras + 47 from the shop's Keyence backup archive,
2026-07-25). `render_workspace_xml` reproduces **43 of the 52 byte-for-byte**; the
9 others differ ONLY in the remembered window-geometry fields (SimulatorSize*/
Position*, ImageBar*), which a freshly-created workspace writes as -1 and the
simulator overwrites the first time you open and move the window. So -1 is both
what a new workspace looks like and what we want: geometry is the user's, not the
backup's.

Byte details that matter (the simulator wrote them, so we do too):
  - UTF-8 **with BOM**, **CRLF** line endings, and **no trailing newline**
  - `<ControllerName />` is self-closed with a space, and empty on all 52 samples

Two dialects exist. The PC simulator writes the full 20-field document above. The
camera writes a SHORT 8-field one (no BOM, LF, a real ControllerID) into its own
`/SD1/cv-x/workspace/<NAME>/` - the camera stores workspaces on its SD card too.
We write the PC dialect (that is the one the simulator creates and expects), but
`read_workspace_xml` reads both, so a workspace copied off a camera still parses.

CONTROLLER IDENTITY, and what we can honestly claim: `env.dat` carries NEITHER the
IP nor the type/grade (searched, all three sample cameras), so those cannot be
derived from the backup - the IP comes from the job that dialed the camera. Every
one of the 50 real-camera samples reports ControllerType 12 and SoftwareGrade
1078219315, so those ship as verified defaults, overridable per call. The
simulator's own "New Workspace" default is type 1 / grade 1078021196.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import struct
from pathlib import Path

log = logging.getLogger(__name__)

WORKSPACE_XML = "workspace.xml"
SD_DIR = "SD1"                      # the CV-X FTP login dir, and the wrapper name
SETTING_REL = ("cv-x", "setting")   # under SD_DIR

# Observed on all 50 real-camera samples; the simulator's new-workspace default is
# (1, 1078021196) = "@ALL". Overridable per call - never guessed from the tree.
CONTROLLER_TYPE = 12
SOFTWARE_GRADE = 1078219315         # "@DR3" - see grade_text()
NEW_WORKSPACE_GRADE = 1078021196    # "@ALL" - the simulator's unlocked-everything default

# Fields the simulator remembers about ITS window, not about the camera. A new
# workspace writes these; the simulator rewrites them on first open.
_GEOMETRY = (
    ("SimulatorSizeHeight", "-1"), ("SimulatorSizeWidth", "-1"),
    ("SimulatorPositionX", "-1"), ("SimulatorPositionY", "-1"),
    ("ImageBarSizeHeight", "-1"), ("ImageBarSizeWidth", "-1"),
    ("ImageBarPositionX", "-1"), ("ImageBarPositionY", "-1"),
    ("ImageBarSticking", "false"), ("ImageBarFormMode", "0"),
    ("ImageBarViewMode", "0"), ("TumbnailSizeType", "2"),   # sic: Tumbnail
)

# spelled as bytes, never as an invisible literal in source
_BOM = b"\xef\xbb\xbf"

_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\r\n'
         '<Workspace xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
         'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n')


def grade_text(value) -> str:
    """A SoftwareGrade int as the 4-char ASCII it packs: 1078219315 -> "@DR3",
    1078021196 -> "@ALL" (big-endian). Returns "" when it isn't printable ASCII,
    so an unknown grade shows as its number rather than as mojibake."""
    try:
        raw = struct.pack(">I", int(value) & 0xFFFFFFFF)
    except (TypeError, ValueError):
        return ""
    return raw.decode("ascii") if all(0x20 <= b < 0x7F for b in raw) else ""


def grade_value(text) -> int:
    """The inverse of grade_text: "@DR3" -> 1078219315. Raises ValueError on
    anything that isn't 4 ASCII characters."""
    raw = str(text).encode("ascii", "strict")
    if len(raw) != 4:
        raise ValueError(f"a software grade is 4 characters, got {text!r}")
    return struct.unpack(">I", raw)[0]


def render_workspace_xml(ip: str, *, controller_type=CONTROLLER_TYPE,
                         software_grade=SOFTWARE_GRADE, controller_id=-1,
                         controller_name: str = "") -> bytes:
    """The simulator's workspace.xml for a camera at `ip`, as the exact bytes it
    writes itself (BOM + CRLF + no trailing newline). Pure: no I/O.

    `controller_name` is empty on all 52 samples - the simulator leaves it blank -
    so it defaults to blank here and self-closes as `<ControllerName />`."""
    name = _xml_escape(controller_name)
    body = [
        f"  <ControllerIpAddress>{_xml_escape(ip)}</ControllerIpAddress>",
        f"  <ControllerName>{name}</ControllerName>" if name else "  <ControllerName />",
        f"  <ControllerType>{controller_type}</ControllerType>",
        f"  <SoftwareGrade>{software_grade}</SoftwareGrade>",
        f"  <ControllerID>{controller_id}</ControllerID>",
        "  <Version>1.0.0000</Version>",
    ]
    body += [f"  <{k}>{v}</{k}>" for k, v in _GEOMETRY]
    text = _HEAD + "".join(line + "\r\n" for line in body) + "</Workspace>"
    return _BOM + text.encode("utf-8")


def _xml_escape(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_workspace_xml(folder, ip: str, **kw) -> Path:
    """Write workspace.xml into `folder` (which should hold the `SD1/` tree) and
    return its path. Written via a .tmp + replace so a crash never leaves a
    half-manifest that the simulator would choke on."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / WORKSPACE_XML
    tmp = folder / (WORKSPACE_XML + ".tmp")
    tmp.write_bytes(render_workspace_xml(ip, **kw))
    tmp.replace(dest)
    return dest


# -- reading back ----------------------------------------------------------------

_FIELD_RE = re.compile(r"<(\w+)\s*(?:/>|>(.*?)</\1>)", re.S)


def parse_workspace_xml(text: str) -> dict:
    """A workspace.xml's fields as a flat dict of strings (empty string for a
    self-closed tag). Pure text -> dict; reads the simulator's 20-field dialect
    and the camera's short 8-field one alike. `software_grade_text` is added when
    the grade decodes to ASCII, so a caller can SHOW "@DR3" instead of a number."""
    out = {}
    for m in _FIELD_RE.finditer(text):
        tag, val = m.group(1), m.group(2)
        if tag == "Workspace":
            continue
        out[tag] = (val or "").strip()
    if "SoftwareGrade" in out:
        g = grade_text(out["SoftwareGrade"])
        if g:
            out["software_grade_text"] = g
    return out


def read_workspace_xml(path) -> dict:
    """parse_workspace_xml for a file on disk. Tolerates the BOM (the simulator
    writes one, the camera does not); returns {} when unreadable - a workspace we
    cannot read must not sink whatever is listing it."""
    try:
        return parse_workspace_xml(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as e:
        log.info("unreadable workspace.xml at %s (%s)", path, e)
        return {}


def is_workspace(folder) -> bool:
    """True when `folder` is a CV-X simulator workspace: a workspace.xml next to
    an `SD1/` directory. Both are required - workspace.xml alone is a name common
    enough (IDE project files) to misread, and SD1/ alone is just our tree."""
    folder = Path(folder)
    try:
        return (folder / WORKSPACE_XML).is_file() and (folder / SD_DIR).is_dir()
    except OSError:
        return False


# -- exporting into the simulator's flat folder ----------------------------------
#
# The simulator does NOT walk a nested tree: you give it ONE base path and it
# lists the workspace folders directly inside. Our library is the opposite shape
# (plant/line/robot/dated history), and flattening the library itself is not an
# option - the history IS the library. So loading cameras into the simulator is
# an explicit export: pick cameras, and their latest workspaces are copied side
# by side into the flat folder, overwriting the previous copy of the same camera.


def workspaces_in(folder) -> list:
    """Every simulator workspace at `folder`: the folder itself when it is one,
    otherwise its immediate workspace children (a station's CAM1/, CAM2/, …).
    Non-recursive on purpose - a backup root holds cameras, not camera trees."""
    folder = Path(folder)
    if is_workspace(folder):
        return [folder]
    try:
        return sorted((c for c in folder.iterdir() if c.is_dir() and is_workspace(c)),
                      key=lambda p: p.name)
    except OSError:
        return []


def _safe_folder(name: str) -> str:
    """A folder name safe on Windows, keeping the shop's own punctuation where it
    is legal (station names carry '-' and '_')."""
    out = "".join(c if c.isalnum() or c in " ._-" else "_" for c in str(name or "").strip())
    return out.strip(" .") or "camera"


def plan_exports(items) -> list:
    """Assign each camera its folder name in the flat simulator folder, and
    return the items with a "name" (and "why" when the name had to be qualified).

    Naming follows the shop's existing convention: the STATION name alone, plus
    the camera label only when that station has more than one camera
    (DD183 · 040R01B01CAM1 / 040R01B01CAM2). Because the flat folder mixes every
    plant and line together - and the library really does hold the same station
    name on different lines - a name already claimed by a DIFFERENT station gets
    its line appended, then its plant, then a counter. First one in (sorted by
    plant/line/station, so the answer is stable run to run) keeps the plain name.

    Pure: takes/returns plain dicts, touches no disk. Each item needs
    "station"/"line"/"plant"/"label"; anything else is carried through."""
    items = [dict(it) for it in items or []]
    # a station with >1 camera in the SAME backup needs the label to tell them apart
    counts: dict = {}
    for it in items:
        counts[_ident(it)] = counts.get(_ident(it), 0) + 1

    out, taken = [], {}
    for it in sorted(items, key=lambda i: (str(i.get("plant", "")), str(i.get("line", "")),
                                           str(i.get("station", "")), str(i.get("label", "")))):
        station = _safe_folder(it.get("station"))
        multi = counts[_ident(it)] > 1
        base = _multi_name(station, it.get("label")) if multi else station
        name, why = base, ""
        if taken.get(name.lower(), _ident(it)) != _ident(it):
            for suffix, reason in ((it.get("line"), "line"), (it.get("plant"), "plant")):
                if suffix:
                    name = f"{base}-{_safe_folder(suffix)}"
                    why = f"another station is already called {base}, so its {reason} was added"
                    if taken.get(name.lower(), _ident(it)) == _ident(it):
                        break
            n = 2
            while taken.get(name.lower(), _ident(it)) != _ident(it):
                name = f"{base}-{n}"
                why = f"another station is already called {base}"
                n += 1
        taken[name.lower()] = _ident(it)
        it["name"] = name
        it["why"] = why
        out.append(it)
    return out


_CAM_TAG_RE = re.compile(r"[ _-]*CAM\s*\d*$", re.IGNORECASE)


def _multi_name(station: str, label) -> str:
    """`<station><label>` for a station carrying more than one camera - except a
    camera that NAMED ITSELF already ends in its own CAM tag, because a CV-X
    takes its name from a program the tech called e.g. `RB130R01B01CAM1`.
    Appending there would read `RB130R01B01CAM1CAM2`, which is both ugly and a
    lie about which camera it is, so the trailing tag is REPLACED rather than
    doubled - landing on `RB130R01B01CAM1` / `RB130R01B01CAM2`, exactly the
    convention the shop's own folders already use."""
    label = _safe_folder(label)
    if not label:
        return station
    return _CAM_TAG_RE.sub("", station) + label


def _ident(item) -> tuple:
    """What makes two cameras THE SAME station (so a name clash between them is
    not a clash at all - it is the same station's second camera)."""
    return (str(item.get("plant", "")), str(item.get("line", "")), str(item.get("station", "")))


# -- the "we created this" ledger ------------------------------------------------
#
# Exporting REPLACES <sim folder>/<name>, and replacing means deleting what is
# there first. That is fine for a folder this export wrote, and unacceptable for
# one a person made by hand - and the simulator's own saves look exactly like
# ours, because they ARE the same format. So the export keeps a ledger of the
# folders it created, and refuses to delete anything not in it without being
# told twice.
#
# The ledger is ONE dotfile at the base path, never inside a workspace: the
# simulator reads the workspace folders, and we do not put unexpected files where
# it is looking. It travels with the folder (so a second PC, or a wiped
# %APPDATA%, still knows) and a missing entry always fails toward caution.

EXPORT_LEDGER = ".backupviewer-exports.json"
_LEDGER_NOTE = ("folders listed here were created by BackupViewer's simulator "
                "export and will be replaced by it. Anything not listed is left "
                "alone unless you confirm. Safe to delete - the export will then "
                "simply ask again.")


class ForeignWorkspace(Exception):
    """Raised when an export would delete a folder this export did not create."""


def read_ledger(sim_root) -> dict:
    """{folder name -> what we recorded when we wrote it}. Empty (never raises)
    when the ledger is missing or unreadable - an unknown folder is then treated
    as somebody else's, which is the safe direction."""
    try:
        raw = (Path(sim_root) / EXPORT_LEDGER).read_text(encoding="utf-8")
        data = json.loads(raw)
        got = data.get("exports") if isinstance(data, dict) else None
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def is_ours(sim_root, name: str) -> bool:
    """Did THIS export create <sim_root>/<name>?"""
    return _safe_folder(name) in read_ledger(sim_root)


def would_replace_foreign(sim_root, name: str) -> bool:
    """True when a folder is already sitting there that we did not create - the
    one case where exporting destroys something we cannot replace."""
    name = _safe_folder(name)
    try:
        if not (Path(sim_root) / name).is_dir():
            return False
    except OSError:
        return False
    return name not in read_ledger(sim_root)


def _record_export(sim_root, name: str, src) -> None:
    """Add one folder to the ledger. Best-effort: a ledger we could not write
    only costs a confirmation next time, so it must never fail the export."""
    sim_root = Path(sim_root)
    entry = {"source": str(src), "written": _dt.datetime.now().isoformat(timespec="seconds")}
    try:
        exports = read_ledger(sim_root)
        exports[_safe_folder(name)] = entry
        tmp = sim_root / (EXPORT_LEDGER + ".tmp")
        tmp.write_text(json.dumps({"note": _LEDGER_NOTE, "exports": exports}, indent=2),
                       encoding="utf-8")
        tmp.replace(sim_root / EXPORT_LEDGER)
    except (OSError, ValueError):
        log.warning("could not update the simulator export ledger in %s", sim_root)


def export_workspace(src, sim_root, name: str, *, replace_foreign: bool = False):
    """Copy one workspace into <sim_root>/<name>, replacing any PREVIOUS EXPORT
    of the same camera. Built in a sibling .__tmp and swapped in, so an
    interrupted export never leaves the simulator a half-copied workspace, and
    the source (a backup) is only ever READ.

    Refuses with ForeignWorkspace when <name> already exists and this export did
    not create it - that is somebody's hand-made simulator save, and the caller
    must confirm (replace_foreign=True) before it is destroyed.

    Returns the destination Path, or None on failure (logged)."""
    from .ftpbackup import mirror_latest   # local: the swap-in-place primitive

    src = Path(src)
    if not is_workspace(src):
        log.info("refusing to export %s - not a workspace", src)
        return None
    if not replace_foreign and would_replace_foreign(sim_root, name):
        raise ForeignWorkspace(name)
    dest = mirror_latest(src, Path(sim_root) / _safe_folder(name), label=name)
    if dest is not None:
        _record_export(sim_root, name, src)
    return dest
