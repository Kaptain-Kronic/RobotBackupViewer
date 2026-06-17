"""Persistent robot library in %APPDATA%\\BackupViewer\\library.json.

The library is the saved set of robots the user cares about (organised
PLANT / LINE / ROBOT) plus, per robot, the history of backups taken or imported.
It is the home screen's data and the backup engine's registration target.

Why a sibling module instead of a key in settings.json: settings.json is
hot-rewritten on every UI pref change (font, scale, theme, per-tab layout).
Parking the robot library + its backup history there would race those writes.
So this mirrors settings.py's pattern (a module-level lock + atomic
temp->replace) against its own file.

Identity: a robot is keyed by (robot name, line) when matching a freshly-taken
backup to an existing entry; each entry carries a stable uuid `id` the UI uses.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from . import settings

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
VERSION = 1


def _library_file() -> Path:
    return settings.app_dir() / "library.json"


def _empty() -> dict:
    return {"version": VERSION, "roots": [], "robots": []}


def load() -> dict:
    try:
        with open(_library_file(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty()
    except (OSError, ValueError):
        return _empty()
    data.setdefault("version", VERSION)
    data.setdefault("roots", [])
    data.setdefault("robots", [])
    return data


def _write(data: dict) -> None:
    f = _library_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)


def save(data: dict) -> None:
    with _LOCK:
        _write(data)


# -- entry shaping --------------------------------------------------------------

def _normalize(entry: dict) -> dict:
    """Fill a partial entry with every field the UI/engine expect. Keeps any
    extra keys the caller supplied. Generates an id if missing."""
    e = dict(entry or {})
    e["id"] = e.get("id") or uuid.uuid4().hex
    e.setdefault("plant", "")
    e.setdefault("line", "")
    e["robot"] = e.get("robot") or e.get("robot_name") or ""
    e.pop("robot_name", None)
    e.setdefault("model", "")
    e.setdefault("f_number", "")
    e.setdefault("ips", [])
    if not isinstance(e["ips"], list):
        e["ips"] = [e["ips"]] if e["ips"] else []
    ftp = e.get("ftp") if isinstance(e.get("ftp"), dict) else {}
    e["ftp"] = {"user": ftp.get("user", ""), "passive": ftp.get("passive", True)}
    e.setdefault("notes", "")
    e.setdefault("latest_path", "")
    e.setdefault("history_root", "")
    e.setdefault("last_backup", "")
    backups = e.get("backups")
    e["backups"] = backups if isinstance(backups, list) else []
    return e


def _find_match(data: dict, match: dict):
    """The existing entry a backup belongs to: same robot name, and same line
    when a line is given (a robot name can repeat across lines)."""
    rname = (match.get("robot") or match.get("robot_name") or "").upper()
    line = (match.get("line") or "").upper()
    if not rname:
        return None
    for e in data["robots"]:
        if e.get("robot", "").upper() != rname:
            continue
        if line and e.get("line", "").upper() != line:
            continue
        return e
    return None


def _reconcile(data: dict) -> None:
    """Refresh `stale` flags by stat-checking paths. Never deletes: an archive
    may live on a network drive that is simply disconnected right now."""
    for e in data.get("robots", []):
        lp = e.get("latest_path")
        e["stale"] = bool(lp) and not Path(lp).is_dir()
        for b in e.get("backups", []):
            p = b.get("path")
            b["stale"] = bool(p) and not Path(p).is_dir()


# -- public api -----------------------------------------------------------------

def list_robots() -> dict:
    """The whole library, with stale flags freshly reconciled."""
    with _LOCK:
        data = load()
        _reconcile(data)
        _write(data)
        return data


def add_robot(entry: dict) -> dict:
    with _LOCK:
        data = load()
        e = _normalize(entry)
        data["robots"].append(e)
        _reconcile(data)
        _write(data)
        return e


def update_robot(robot_id: str, patch: dict) -> dict | None:
    with _LOCK:
        data = load()
        for e in data["robots"]:
            if e.get("id") == robot_id:
                for k, v in (patch or {}).items():
                    if k != "id":
                        e[k] = v
                _normalize(e)  # re-shape in place in case ips/ftp changed type
                _reconcile(data)
                _write(data)
                return e
        return None


def remove_robot(robot_id: str) -> bool:
    with _LOCK:
        data = load()
        before = len(data["robots"])
        data["robots"] = [e for e in data["robots"] if e.get("id") != robot_id]
        changed = len(data["robots"]) != before
        if changed:
            _write(data)
        return changed


def get_robot(robot_id: str) -> dict | None:
    for e in load()["robots"]:
        if e.get("id") == robot_id:
            return e
    return None


def register_backup(match: dict, backup: dict, *, latest_path: str = "") -> dict:
    """Attach a completed backup to its robot, creating the entry if none
    matches. `match` carries identity+config (robot/line/plant/model/ips/...);
    `backup` is one history record. Used by the backup engine and import paths.
    Newest backup first. Returns the (possibly new) entry."""
    with _LOCK:
        data = load()
        e = _find_match(data, match)
        if e is None:
            e = _normalize(match)
            data["robots"].append(e)
        else:
            # fold in any newly-learned identity/config without clobbering
            for k in ("plant", "line", "model", "f_number", "history_root"):
                if match.get(k) and not e.get(k):
                    e[k] = match[k]
            ips = e.get("ips", [])
            for ip in match.get("ips", []):
                if ip and ip not in ips:
                    ips.append(ip)
            e["ips"] = ips
        e["backups"].insert(0, backup)
        e["last_backup"] = backup.get("taken", e.get("last_backup", ""))
        if latest_path:
            e["latest_path"] = latest_path
        _reconcile(data)
        _write(data)
        return e
