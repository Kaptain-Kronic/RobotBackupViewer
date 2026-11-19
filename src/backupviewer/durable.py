"""Crash-safe JSON for the app's own state files in %APPDATA%\\BackupViewer -
settings, the library registry, the backup log, imported kinematics.

What the plain temp->replace pattern missed (audit 2026-11-18, reproduced):
- the temp file was never flushed to disk before the rename, so a hard
  power-off could leave the renamed file EMPTY;
- an unreadable file read back as {} - and the next ordinary save wrote
  {that one key} over it, wiping every other setting (library_root fell back
  to the home-folder default and the app quietly showed a different library).

So every save flushes the temp file to disk before the rename and keeps the
previous good copy as <name>.bak; a load that cannot parse the file says so in
app.log and reads the .bak instead; and the next save moves the damaged file
aside as <name>.corrupt-<stamp> rather than writing over it. Nothing is ever
silently lost - at worst one save goes back to the copy before it."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

_GOOD, _BAD, _LOCKED = "good", "bad", "locked"
# what the last read of each file found, so a save knows whether the file on
# disk is a good copy to keep as .bak or a damaged one to set aside
_state: dict[str, str] = {}


class _Unreadable(Exception):
    def __init__(self, why: str, locked: bool = False):
        super().__init__(why)
        self.locked = locked


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def backup_path(path) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".bak")


def _read(path: Path):
    """The JSON object stored at `path`, None when there is no file. Raises
    _Unreadable when the file exists but is not a JSON object - after a short
    retry for a transient lock (an AV scan, the other window mid-replace)."""
    for attempt in range(4):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            break
        except FileNotFoundError:
            return None
        except PermissionError as e:
            if attempt == 3:
                raise _Unreadable(f"locked: {e}", locked=True) from e
            time.sleep(0.05 * (attempt + 1))
        except (OSError, ValueError) as e:     # UnicodeDecodeError is a ValueError
            raise _Unreadable(f"{type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise _Unreadable("not a JSON object")
    return data


def load(path) -> dict | None:
    """The dict stored at `path`, or None when there is no such file. A file
    that exists but cannot be read is logged (once) and its .bak is returned
    instead (None when that is unreadable too); the next save() then sets the
    damaged file aside rather than writing over it."""
    path = Path(path)
    key = _key(path)
    try:
        data = _read(path)
    except _Unreadable as e:
        state = _LOCKED if e.locked else _BAD
        if _state.get(key) != state:
            log.warning("%s could not be read (%s) - using its last good copy", path.name, e)
        _state[key] = state
        try:
            return _read(backup_path(path))
        except _Unreadable:
            log.error("%s and its .bak are both unreadable - defaults until the "
                      "next save sets the damaged file aside", path.name)
            return None
    _state[key] = _GOOD
    return data


def _keep_backup(path: Path) -> None:
    bak = backup_path(path)
    staging = bak.with_name(bak.name + ".tmp")
    try:
        shutil.copyfile(path, staging)
        os.replace(staging, bak)
    except OSError:
        log.warning("could not refresh %s (the save itself goes ahead)", bak.name,
                    exc_info=True)


def _set_aside(path: Path) -> None:
    dest = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(path, dest)
        log.warning("kept the unreadable %s as %s", path.name, dest.name)
    except OSError:
        log.warning("could not set the unreadable %s aside", path.name, exc_info=True)


def save(path, data, *, indent=2, keep_backup: bool = True) -> None:
    """Write `data` as JSON at `path`: a temp file flushed to disk, then renamed
    over the old one, so a crash leaves the old file or the new one - never a
    torn or empty one. The previous good file becomes <name>.bak; a damaged one
    is set aside as <name>.corrupt-<stamp>. The rename retries briefly: Windows
    refuses it while any other handle has the target open (WinError 5), and
    those holds clear in milliseconds."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=indent))
        f.flush()
        os.fsync(f.fileno())
    key = _key(path)
    if path.exists():
        state = _state.get(key)
        if state is None:                  # never read by this process: judge it first
            try:
                _read(path)
                state = _GOOD
            except _Unreadable as e:
                state = _LOCKED if e.locked else _BAD
        if state == _BAD:
            _set_aside(path)
        elif state == _GOOD and keep_backup:
            _keep_backup(path)
    for attempt in range(4):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))
    _state[key] = _GOOD
