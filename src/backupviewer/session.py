"""BackupSession: one opened backup folder.

- Recursive, case-insensitive index. MD backups are flat, but "maintenance
  data" backups are hierarchical (mnt_data/md_ls/*.ls programs, mnt_data/mdb/
  summary.dg + alarm reports, dlog/, rsch/, _backup_/).
- self.files is keyed by UPPER posix relpath; self.by_name resolves a bare
  basename to the best copy when duplicates exist across subfolders
  (shallowest path wins, then the mdb/ controller-dump dir, then alphabetic).
- .LS files are classified by content, not name: TP programs start with
  '/PROG'; report dumps (ERRALL.LS, LOGBOOK.LS, ...) start with
  '<NAME>.LS   Robot Name <host> <date>'.
- Nothing heavy is parsed at open; each tab's data is built lazily on first
  request and cached for the life of the session (per-key locks so two
  concurrent JS calls parse once).
"""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .parsers import TAB_REQUIREMENTS
from .parsers.common import read_text

log = logging.getLogger(__name__)

_REPORT_HEADER = re.compile(r"^(\S+)\s+Robot Name\s+(\S+)\s*(.*)$")
_KAREL_HEADER = re.compile(r"^\[([^\]]+)\]")

MAX_SCAN_FILES = 20_000  # guard against opening C:\ by accident
_BINARY_PROGRAM_EXTS = (".TP", ".PC", ".MR")
_IMAGE_EXTS = (".JPG", ".JPEG", ".PNG", ".BMP")

# signatures that mark a folder as a FANUC backup root (used by the bulk-add
# walker - kept in sync with _detect_type's markers)
_BACKUP_MARKER_DIRS = {"mnt_data", "md_ls", "mdb"}
_BACKUP_FILE_EXTS = (".LS", ".VA", ".TP", ".PC", ".MR", ".DG", ".SV", ".IO", ".DT")
# camera backups hold none of the FANUC file types - they're marked by their
# content dir (da/ = Matrox Design Assistant, cv-x/ = Keyence) or the CAM<n>
# multi-camera wrapper the backup engine lays down.
_CAMERA_MARKER_DIRS = {"da", "cv-x"}
_CAM_DIR_RE = re.compile(r"^CAM\d+$", re.IGNORECASE)


def looks_like_backup(d: Path) -> bool:
    """Conservative, NON-recursive test: does this folder directly look like a
    backup root? True when it holds any FANUC backup file (.LS/.VA/.TP/.DG/...),
    a maintenance-data marker subfolder (mnt_data/md_ls/mdb), the app's own
    snapshot sidecar (backup.json - the reliable marker for ANY app-taken backup,
    robot or camera), or a camera content dir (da/ · cv-x/ · a CAM<n>/ wrapper).
    Deliberately strict so the scan never mistakes an ordinary folder for a backup."""
    try:
        for p in d.iterdir():
            try:
                if p.is_dir():
                    n = p.name.lower()
                    if n in _BACKUP_MARKER_DIRS or n in _CAMERA_MARKER_DIRS:
                        return True
                    if _CAM_DIR_RE.match(p.name):
                        return True
                elif p.name.lower() == "backup.json":
                    return True
                elif p.suffix.upper() in _BACKUP_FILE_EXTS:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def find_backup_roots(parent: Path, max_depth: int = 7, cap: int = 5000,
                      stats: dict | None = None) -> list[Path]:
    """Every backup root at or beneath `parent`. A folder that looks_like_backup
    is a root and is NOT descended into (its dated <date>/<time> subfolders are
    snapshots of the same backup, not separate ones). Handles a per-line Latest/
    mirror (each child is a root), a LINE/ROBOT/<date>/<time> tree (the time
    folder is the root), and a flat folder of backups. Bounded by depth + count;
    when a bound trips, stats["truncated"] is set so callers can SAY SO instead
    of silently listing a partial library (each dated snapshot counts as one
    root, so a plant-scale tree runs to thousands)."""
    parent = Path(parent)
    if not parent.is_dir():
        return []
    if looks_like_backup(parent):
        return [parent]
    roots: list[Path] = []
    stack = [(parent, 0)]
    while stack and len(roots) < cap:
        d, depth = stack.pop()
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            continue
        for c in children:
            if len(roots) >= cap:
                break
            if c.name.endswith((".__part", ".__tmp")):
                continue    # transient staging dir (crash residue mid move/mirror-regen)
            if looks_like_backup(c):
                roots.append(c)
            elif depth + 1 < max_depth:
                stack.append((c, depth + 1))
    if len(roots) >= cap:
        log.warning("find_backup_roots hit cap=%d under %s", cap, parent)
        if stats is not None:
            stats["truncated"] = True
    return sorted(roots)


class BackupSession:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise NotADirectoryError(str(root))

        self.files: dict[str, Path] = {}             # UPPER posix relpath -> Path
        self.by_name: dict[str, list[Path]] = {}     # UPPER basename -> priority-sorted
        self.truncated_scan = False
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            if len(self.files) >= MAX_SCAN_FILES:
                self.truncated_scan = True
                log.warning("scan capped at %d files under %s", MAX_SCAN_FILES, self.root)
                break
            self.files[p.relative_to(self.root).as_posix().upper()] = p
            self.by_name.setdefault(p.name.upper(), []).append(p)
        for lst in self.by_name.values():
            lst.sort(key=self._priority)

        self._cache: dict[str, object] = {}
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._classify_ls()
        self._classify_karel()
        self.backup_type = self._detect_type()

    def _priority(self, p: Path) -> tuple:
        rel = p.relative_to(self.root)
        return (
            len(rel.parts),                                # shallowest first
            0 if p.parent.name.lower() == "mdb" else 1,    # controller dump dir next
            rel.as_posix().upper(),                        # deterministic tiebreak
        )

    # -- file access ------------------------------------------------------

    def find(self, name: str) -> Path | None:
        key = name.replace("\\", "/").upper()
        if "/" in key:
            return self.files.get(key)
        hits = self.by_name.get(key)
        return hits[0] if hits else None

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def text(self, name: str) -> str | None:
        p = self.find(name)
        return read_text(p) if p else None

    def cached(self, key: str, builder: Callable[[], object]):
        if key in self._cache:
            return self._cache[key]
        with self._locks[key]:
            if key not in self._cache:
                self._cache[key] = builder()
        return self._cache[key]

    # -- .LS classification -------------------------------------------------

    def _classify_ls(self) -> None:
        """Classify the best copy of each .LS basename (duplicates across
        subfolders are snapshots of the same report - listing both would
        double every alarm file)."""
        self.program_files: list[Path] = []
        self.report_files: list[Path] = []
        self.robot_name = ""
        for name, paths in self.by_name.items():
            if not name.endswith(".LS"):
                continue
            p = paths[0]
            try:
                with open(p, "rb") as f:
                    head = f.read(120).decode("cp1252", errors="replace")
            except OSError:
                continue
            if head.startswith("/PROG"):
                self.program_files.append(p)
            else:
                m = _REPORT_HEADER.match(head.splitlines()[0] if head else "")
                if m:
                    self.report_files.append(p)
                    if not self.robot_name:
                        self.robot_name = m.group(2)

    def _classify_karel(self) -> None:
        """KAREL programs: stored as <stem>.VR (binary, called .PC on the
        pendant) with a <stem>.VA text twin holding the program's variables.
        A .VA is a KAREL program's vars when it has a same-stem .VR twin AND
        its first record's section == the stem (excludes shared dumps like
        NUMREG.VA [*NUMREG*] or FRAMEVAR.VA [TPFDEF])."""
        self.karel_programs: dict[str, dict] = {}  # STEM(upper) -> {stem, va, vr}
        for name, paths in self.by_name.items():
            if not name.endswith(".VA"):
                continue
            stem = name[:-3]
            vr = self.by_name.get(stem + ".VR")
            if not vr:
                continue
            va = paths[0]
            try:
                with open(va, "rb") as f:
                    head = f.read(160).decode("cp1252", errors="replace")
            except OSError:
                continue
            m = _KAREL_HEADER.match(head)
            if m and m.group(1).upper() == stem:
                self.karel_programs[stem] = {"stem": va.stem, "va": va, "vr": vr[0]}

    def alarm_files(self) -> list[Path]:
        return sorted(
            (p for p in self.report_files if p.name.upper().startswith("ERR")),
            key=lambda p: p.name.upper(),
        )

    def has_binary_programs(self) -> bool:
        return any(n.endswith(_BINARY_PROGRAM_EXTS) for n in self.by_name)

    # -- matrox camera --------------------------------------------------------

    def saved_image_files(self) -> list[Path]:
        """Every runtime inspection image (jpg/png/…) sitting under a
        SavedImages/ path - the Matrox camera's saved photos. Kept narrow (a
        SavedImages ancestor) so a robot backup or a UI logo never lights up the
        photos tab."""
        out = []
        for key, p in self.files.items():
            if "SAVEDIMAGES/" in key and key.endswith(_IMAGE_EXTS):
                out.append(p)
        return out

    def has_photos(self) -> bool:
        return bool(self.saved_image_files())

    def _is_keyence(self) -> bool:
        """A Keyence CV-X backup: the camera's `cv-x/` tree (setting/, box/)."""
        for key in self.files:
            if "CV-X" in key.split("/")[:-1]:
                return True
        return False

    def _is_matrox(self) -> bool:
        """A Matrox Design Assistant camera backup: a `da/` project folder, saved
        images, or the DA marker paths. (A camera carries no FANUC program/report
        files, so the FANUC branches below never fire on one.)"""
        for key in self.files:
            parts = key.split("/")
            if "DA" in parts[:-1]:                     # a 'da' folder in the path
                return True
            if "SAVEDIMAGES" in parts:
                return True
            if "MATROX DESIGN ASSISTANT" in key or ".MATROX_IMAGING" in key:
                return True
        return False

    # -- backup type ----------------------------------------------------------

    def _detect_type(self) -> str:
        if self._is_keyence():
            return "keyence camera"
        if self._is_matrox():
            return "matrox camera"
        markers = {"mnt_data", "md_ls", "mdb"}
        if self.root.name.lower() in markers or any(
            part.lower() in markers
            for key in self.files
            for part in key.split("/")[:-1]
        ):
            return "maintenance data"
        if self.program_files:
            return "MD"
        if self.has_binary_programs():
            return "all of the above"
        return "unknown"

    # -- manifest -----------------------------------------------------------

    def manifest(self) -> dict:
        f_number = ""
        summary = self.find("SUMMARY.DG")
        if summary:
            try:
                with open(summary, "rb") as f:
                    head = f.read(400).decode("cp1252", errors="replace")
                m = re.search(r"F Number:\s*(\S+)", head)
                if m:
                    f_number = m.group(1)
            except OSError:
                pass

        tabs = {}
        for tab, needs in TAB_REQUIREMENTS.items():
            if not needs:
                tabs[tab] = True
            elif needs == ["*programs"]:
                tabs[tab] = bool(self.program_files) or self.has_binary_programs()
            elif needs == ["*alarms"]:
                tabs[tab] = bool(self.alarm_files())
            elif needs == ["*photos"]:
                tabs[tab] = self.has_photos()
            else:
                tabs[tab] = any(self.find(n) for n in needs)

        return {
            "path": str(self.root),
            "name": self.root.name,
            "file_count": len(self.files),
            "robot_name": self.robot_name,
            "f_number": f_number,
            "backup_type": self.backup_type,
            "truncated_scan": self.truncated_scan,
            "tabs": tabs,
            # magnet end-effector: detected by its MAG*.PC KAREL programs (cheap,
            # gates the overview/mhvalves magnet sections; full config via get_magnet)
            "magnet": any(stem.startswith("MAG") for stem in self.karel_programs),
        }
