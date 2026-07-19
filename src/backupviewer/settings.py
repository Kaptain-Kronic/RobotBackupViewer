"""Persistent app settings in %APPDATA%\\BackupViewer.

Settings live Python-side (not localStorage) because pywebview defaults to
private_mode=True and WebView2 storage is unreliable under a onefile exe.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

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
    try:
        with open(_settings_file(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(settings: dict) -> None:
    with _LOCK:
        _write(settings)


def _write(settings: dict) -> None:
    tmp = _settings_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    # Windows: replace() throws WinError 5 while ANY other handle has the target
    # open - a concurrent reader thread (load() runs unlocked), a second app
    # instance, or an AV scan. Seen in the field as a burst of start_backups all
    # dying on this rename. Those holds clear in milliseconds - retry briefly.
    for attempt in range(4):
        try:
            tmp.replace(_settings_file())
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def get(key: str, default=None):
    return load().get(key, default)


def library_root() -> str:
    """The single folder that is BOTH the FTP backup destination and the scanned
    library source. Back-compat: falls back to the legacy `backup_root`, then a
    default under the home dir."""
    s = load()
    return s.get("library_root") or s.get("backup_root") or str(Path.home() / "RobotBackups")


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
