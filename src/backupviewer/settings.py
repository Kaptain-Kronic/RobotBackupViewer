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


def get(key: str, default=None):
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
        s = load()
        s[key] = value
        _write(s)


def setup_logging() -> None:
    logging.basicConfig(
        filename=app_dir() / "app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
