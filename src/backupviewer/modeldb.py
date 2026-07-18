"""The robot-kinematics registry: built-in types + user imports.

Two layers, imports win:
- kinematics_builtin.py ships WITH the app: the dimension-sheet numbers
  of every FANUC robot type (a `validated` block marks the ones whose
  chains matched real controllers' own position reports). Robots pose
  with zero setup; unvalidated types still self-verify at runtime when
  the backup carries a position report.
- %APPDATA%\\BackupViewer\\kinematics.json holds what the user imported
  from a Roboguide install's Robot Library (`*.def` files - FANUC's own
  kinematics as XML, parsed by parsers/roboguidedef) for anything newer
  than the built-in table. No vendor files ship with the app.

Matching a backup's robot type ("R-2000iC/210F-IF", from the DCS verify
report) to an entry uses normalize_type() keys: exact match first, then
exact match after dropping a trailing DRESS token from a short whitelist
("-IF" - fleet-validated as the same arm plus a flange adapter, which is
measured separately per robot from the backup's own CURPOS; see
parsers/kinematics.measure_flange). Anything looser would silently borrow
a different arm (210F for a missing 210FS, the plain 120iD wrist for a
/35), so unknown variants stay honestly unmatched.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import ParseError

from . import settings
from .kinematics_builtin import BUILTIN
from .parsers import roboguidedef

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _db_file() -> Path:
    return settings.app_dir() / "kinematics.json"


def load() -> dict:
    """The user-imported layer only:
    {norm_key: {"name", "source", "imported", "kin": {...}}}"""
    try:
        with open(_db_file(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def merged() -> dict:
    """Built-ins under user imports (imports win), each entry tagged with
    source_kind = "builtin" | "imported"."""
    out = {k: dict(v, source_kind="builtin") for k, v in BUILTIN.items()}
    for k, v in load().items():
        out[k] = dict(v, source_kind="imported")
    return out


def _save(db: dict) -> None:
    tmp = _db_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=1), encoding="utf-8")
    tmp.replace(_db_file())


def import_folder(folder: str) -> dict:
    """Scan a folder (the Roboguide 'Robot Library') for robot-arm defs and
    add/update registry entries. Non-robot defs (EOATs, fixtures) and
    unparseable files are counted, not fatal."""
    root = Path(folder)
    imported: list[str] = []
    skipped = 0
    with _LOCK:
        db = load()
        for p in sorted(root.glob("*.def")):
            try:
                kin = roboguidedef.parse_def(p.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, ParseError, OSError):
                skipped += 1
                continue
            # the FILENAME is the type identity - envelope names are shared
            # reach shells (one RANGE file covers 165F/210F/240F) and are
            # sometimes plain wrong (CRX-30iA points at the 25iA shell).
            # Underscores become spaces for display only.
            name = roboguidedef.def_name_from_filename(p.name).replace("_", " ")
            key = roboguidedef.normalize_type(name)
            if not key:
                skipped += 1
                continue
            db[key] = {
                "name": name,
                "source": p.name,
                "imported": date.today().isoformat(),
                "kin": kin,
            }
            imported.append(name)
        if imported:
            _save(db)
    log.info("kinematics import: %d types from %s (%d skipped)",
             len(imported), folder, skipped)
    return {"imported": len(imported), "skipped": skipped, "names": imported}


# dress-package suffixes proven to leave the arm identical (their flange
# adapter is measured per robot, never assumed)
_DRESS_TOKENS = {"IF"}

_TRAILING_TOKEN = re.compile(r"^(.*?)[-/_ ]+([A-Za-z0-9]+)\s*$")


def match(robot_type: str) -> dict | None:
    """Registry entry (built-in or imported) for a backup's robot type, or
    None. Exact normalized match, else exact after dropping a whitelisted
    dress suffix. Never a loose prefix - a near-miss type must not
    silently borrow a different arm."""
    key = roboguidedef.normalize_type(robot_type)
    if not key:
        return None
    db = merged()
    if key in db:
        return db[key]
    m = _TRAILING_TOKEN.match(robot_type.strip())
    if m and m.group(2).upper() in _DRESS_TOKENS:
        return db.get(roboguidedef.normalize_type(m.group(1)))
    return None


def counts() -> dict:
    return {"builtin": len(BUILTIN), "imported": len(load())}


def default_library() -> str:
    """The standard Roboguide robot-library folder on THIS machine, when it
    exists and actually holds defs - so the UI can offer a one-click import
    instead of a bare folder dialog (ProgramData is hidden in Explorer;
    nobody should have to know that path by heart)."""
    base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    p = Path(base) / "FANUC" / "ROBOGUIDECore" / "Robot Library"
    try:
        if p.is_dir() and next(p.glob("*.def"), None) is not None:
            return str(p)
    except OSError:
        pass
    return ""
