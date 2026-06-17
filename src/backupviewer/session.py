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

    # -- backup type ----------------------------------------------------------

    def _detect_type(self) -> str:
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
        }
