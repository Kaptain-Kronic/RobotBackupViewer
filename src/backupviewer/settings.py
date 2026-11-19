"""Persistent app settings in %APPDATA%\\BackupViewer.

Settings live Python-side (not localStorage) because pywebview defaults to
private_mode=True and WebView2 storage is unreliable under a onefile exe.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from . import durable

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "BackupViewer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_themes_dir() -> Path:
    d = app_dir() / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_file() -> Path:
    return app_dir() / "settings.json"


def load() -> dict:
    """Every setting, {} when none were ever saved. A damaged settings.json
    reads its last good copy instead of coming back empty (see durable) - the
    empty read was how one ordinary save used to wipe every other key."""
    return durable.load(_settings_file()) or {}


def save(settings: dict) -> None:
    with _LOCK:
        _write(settings)


def _write(settings: dict) -> None:
    # durable.save flushes before the rename, keeps settings.json.bak, sets a
    # damaged file aside, and retries the WinError 5 rename race (a concurrent
    # reader, an AV scan) that once killed a burst of start_backups
    durable.save(_settings_file(), settings, indent=2)


# Keys big enough to make every settings read and save drag them along live in
# their own files beside settings.json: the edit workspace's program list and
# its unsaved drafts (657 programs were 291 KB of one PC's 337 KB settings.json,
# re-read on every get() and rewritten on every click - audit 2026-11-18).
# get() and load_all() read them back transparently; the page sees no change.
_SIDE_KEYS = ("ws_entries", "ws_drafts")


def _side_file(key: str) -> Path:
    return app_dir() / f"{key}.json"


def _side_value(key: str):
    """(found, value) for a side key's own file."""
    side = durable.load(_side_file(key))
    if side is not None and "value" in side:
        return True, side["value"]
    return False, None


def load_all() -> dict:
    """Every setting INCLUDING the side keys - what the page is handed."""
    s = load()
    for key in _SIDE_KEYS:
        found, value = _side_value(key)
        if found:
            s[key] = value
    return s


def get(key: str, default=None):
    if key in _SIDE_KEYS:
        found, value = _side_value(key)
        if found:
            return value
    return load().get(key, default)


def library_root() -> str:
    """The single folder that is BOTH the FTP backup destination and the scanned
    library source. Back-compat: falls back to the legacy `backup_root`, then a
    default under the home dir."""
    s = load()
    return s.get("library_root") or s.get("backup_root") or str(Path.home() / "RobotBackups")


def sim_root() -> str:
    """The flat folder the CV-X simulator's base path points at: one workspace
    folder per camera, side by side, because the simulator does not walk a nested
    tree. Deliberately NOT the library root - the library keeps plant/line/dated
    history, this keeps only what you asked to load into the simulator."""
    s = load()
    return s.get("sim_root") or str(Path.home() / "Documents" / "KEYENCE" / "BackupViewer")


def set_value(key: str, value) -> None:
    # read-modify-write under one lock: js_api calls run on separate threads,
    # and concurrent set_value calls must not clobber each other's keys
    with _LOCK:
        if key in _SIDE_KEYS:
            durable.save(_side_file(key), {"value": value}, indent=None)
            s = load()
            if key in s:                  # the one-time move out of settings.json
                del s[key]
                _write(s)
            return
        s = load()
        for side in _SIDE_KEYS:           # carry an unmoved side key out on this save
            if side in s:
                if not _side_value(side)[0]:
                    durable.save(_side_file(side), {"value": s[side]}, indent=None)
                del s[side]
        s[key] = value
        _write(s)


# app.log rotates instead of growing forever (4.2 MB on one PC, audit
# 2026-11-18). Three files of 2 MB keep weeks of history; the boot check still
# reads the CURRENT app.log for its three lines.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 2


def setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(app_dir() / "app.log", maxBytes=LOG_MAX_BYTES,
                                  backupCount=LOG_BACKUPS, encoding="utf-8",
                                  delay=True)
    logging.basicConfig(
        handlers=[handler],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
