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

import datetime as _dt
import filecmp
import json
import logging
import os
import re
import shutil
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
    data.setdefault("empty_folders", {"plants": [], "lines": []})
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
    # "robot" (FANUC) or "camera-mtx" (Matrox) - drives the backup source + which
    # viewer tabs light up. Defaults to robot so every existing entry is a robot.
    e.setdefault("device_type", "robot")
    # a camera can be linked to the robot it inspects (its Cameras tab); the id of
    # that robot entry, or "" for a robot / an unlinked camera.
    e.setdefault("linked_robot_id", "")
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
    e["hidden"] = bool(e.get("hidden", False))
    e["favorite"] = bool(e.get("favorite", False))
    backups = e.get("backups")
    e["backups"] = backups if isinstance(backups, list) else []
    aliases = e.get("aliases")
    e["aliases"] = aliases if isinstance(aliases, list) else []
    return e


def _find_match(data: dict, match: dict):
    """The existing entry a backup belongs to: same robot name, and same
    line/plant when given (a robot name can repeat across lines, and a line
    name across plants — ERBU-style short names like 010R01 repeat in EVERY
    line). Falls back to an entry's recorded aliases so a stray folder under a
    robot's *old* name re-merges into it after a rename/merge instead of
    spawning a duplicate."""
    rname = (match.get("robot") or match.get("robot_name") or "").upper()
    line = (match.get("line") or "").upper()
    plant = (match.get("plant") or "").upper()
    if not rname:
        return None
    for e in data["robots"]:                                   # tier 1: current identity
        if e.get("robot", "").upper() != rname:
            continue
        if line and e.get("line", "").upper() != line:
            continue
        # plant is strict only when BOTH sides record one: a pre-plant-era
        # entry (blank plant) is still the same robot when it gains a plant
        eplant = e.get("plant", "").upper()
        if plant and eplant and eplant != plant:
            continue
        return e
    for e in data["robots"]:                                   # tier 2: a recorded alias
        for a in e.get("aliases", []) or []:
            if (a.get("robot", "") or "").upper() != rname:
                continue
            if line and (a.get("line", "") or "").upper() != line:
                continue
            aplant = (a.get("plant", "") or "").upper()
            if plant and aplant and aplant != plant:
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
    """The whole library (last scanned state), stale flags freshly reconciled.
    Read-only: stale flags are derived, so nothing is written back — routine
    listings must not churn library.json."""
    with _LOCK:
        data = load()
        _reconcile(data)
        return data


def add_robot(entry: dict) -> dict:
    with _LOCK:
        data = load()
        e = _normalize(entry)
        data["robots"].append(e)
        _reconcile(data)
        _write(data)
        _persist_sidecar(e)
        return e


def bulk_add(entries: list, plant: str = "", line: str = "") -> dict:
    """Add many drafts at once under one plant/line, skipping any robot already
    present (matched by (robot, line) like register_backup). Used by the
    bulk-folder import and the network-discover flows. Returns the added entries
    (with ids) and the names skipped as duplicates."""
    added: list[dict] = []
    skipped: list[str] = []
    with _LOCK:
        data = load()
        for entry in entries or []:
            e = dict(entry or {})
            if plant:
                e["plant"] = plant
            if line:
                e["line"] = line
            match = _find_match(data, e)
            if match is not None:
                skipped.append(match.get("robot", "") or e.get("robot", ""))
                continue
            ne = _normalize(e)
            data["robots"].append(ne)  # appended in-loop, so in-batch dupes also skip
            added.append(ne)
        if added:
            _reconcile(data)
            _write(data)
    return {"added": added, "skipped": skipped}


def update_robot(robot_id: str, patch: dict, *, sidecar: bool = True) -> dict | None:
    """sidecar=False skips the robot.json rewrite — for overlay-only flags
    (hidden/favorite) that the sidecar doesn't carry anyway. Touching the
    sidecar bumps the folder tree, which trips the library watcher and makes
    the NEXT lib_list a full rescan — seconds of churn for a per-machine bit."""
    with _LOCK:
        data = load()
        for e in data["robots"]:
            if e.get("id") == robot_id:
                patch = dict(patch or {})
                # Never downgrade a valid latest_path to one that no longer
                # exists: the edit modal re-sends its (readonly) folder field,
                # which is stale right after a relocate has retargeted the entry.
                lp = patch.get("latest_path")
                cur = e.get("latest_path", "")
                if lp is not None and lp != cur and cur \
                        and Path(cur).is_dir() and not Path(lp).is_dir():
                    patch.pop("latest_path", None)
                for k, v in patch.items():
                    if k != "id":
                        e[k] = v
                _normalize(e)  # re-shape in place in case ips/ftp changed type
                _reconcile(data)
                _write(data)
                if sidecar:
                    _persist_sidecar(e)
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


def set_hidden(robot_id: str, hidden: bool) -> dict | None:
    """Toggle a robot's hidden flag (an overlay-only, per-machine preference the
    folder scan preserves - the everyday alternative to deleting). sidecar=False:
    the flag isn't in robot.json, and rewriting it would dirty the tree."""
    return update_robot(robot_id, {"hidden": bool(hidden)}, sidecar=False)


def set_favorite(robot_id: str, favorite: bool) -> dict | None:
    """Toggle a robot's favorite flag (overlay-only, like hidden): favorites are
    pinned in a section at the top of the library view. sidecar=False so a star
    click can't dirty the tree and trigger a full rescan."""
    return update_robot(robot_id, {"favorite": bool(favorite)}, sidecar=False)


# -- camera <-> robot linking ----------------------------------------------------
# A camera inspects a robot; linking the two lets the robot's viewer show its
# cameras' photos. Matrox camera names encode the station + robot (the camera
# 'CELL-01RB172-R01CAM02' and the robot 'RB172R01B01' both key to 'RB172R01'), so
# most links can be auto-made; Keyence CV-X (named by IP) and misses are assigned
# by hand. The link is a config field (linked_robot_id), persisted in the sidecar.

_STATION_RE = re.compile(r"([A-Z]{2}\d{2,4})")
_ROBOT_RE = re.compile(r"(R\d{1,2})(?!\d)")


def _station_robot_key(name: str) -> str:
    """A normalized STATION+ROBOT key ('RB172R01') from a robot or Matrox camera
    name. '' when it can't be read (e.g. a CV-X named by IP)."""
    up = (name or "").upper()
    st = _STATION_RE.search(up)
    rb = _ROBOT_RE.search(up, st.end() if st else 0)
    return (st.group(1) + rb.group(1)) if (st and rb) else ""


def link_camera(camera_id: str, robot_id: str) -> dict | None:
    """Point a camera entry at the robot it inspects (robot_id='' clears it)."""
    return update_robot(camera_id, {"linked_robot_id": robot_id or ""})


_IP_NAME_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _is_placeholder_name(e: dict) -> bool:
    """True when a camera entry is 'named' by fallback only - blank, or the bare
    IP the discover flow uses when the live name read came back empty."""
    n = (e.get("robot") or "").strip()
    return not n or bool(_IP_NAME_RE.match(n)) or n in (e.get("ips") or [])


def teach_camera_name(camera_id: str, name: str, model: str = "") -> dict | None:
    """Give a placeholder-named camera its real name (read from a completed
    backup's saved-image sidecar - see mtxbackup.name_from_backup), the same way
    a robot self-names from SUMMARY.DG. A real name someone typed (or discovery
    resolved) is NEVER overwritten; the old placeholder is remembered as an alias
    so anything recorded under it still re-merges. Model fills only when blank.
    The rename goes through relocate_robot so the camera's FOLDER moves with it -
    files are law: a name patched only into the registry was reverted by the next
    scan (the folder still said the IP). May raise what relocate_robot raises
    (ValueError/PathGuard/OSError) when the folder can't move - the entry then
    keeps its placeholder honestly instead of flickering back on the next scan.
    Returns the entry (unchanged when there was nothing to teach), None on a bad
    id / non-camera."""
    name = (name or "").strip()
    if not name:
        return None
    with _LOCK:
        data = load()
        e = next((x for x in data["robots"] if x.get("id") == camera_id), None)
        if e is None or not str(e.get("device_type", "")).startswith("camera"):
            return None
        if model and not e.get("model"):
            e["model"] = model
            _reconcile(data)
            _write(data)
            _persist_sidecar(e)
        teach = _is_placeholder_name(e) and e.get("robot", "") != name
        plant, line = e.get("plant", ""), e.get("line", "")
    if not teach:
        return e
    # outside _LOCK (it is not reentrant): relocate re-finds the entry and does
    # the identity + folder + alias + sidecar change under its own hold. It may
    # also MERGE into an entry that already owns the taught name's folder - the
    # result's id says which entry carries the name now.
    res = relocate_robot(camera_id, plant, line, name)
    return get_robot(res.get("id") or camera_id)


def cameras_for_robot(robot_id: str) -> list:
    """Camera entries linked to a robot (newest-backup first)."""
    if not robot_id:
        return []
    cams = [e for e in list_robots()["robots"]
            if e.get("linked_robot_id") == robot_id
            and str(e.get("device_type", "")).startswith("camera")]
    cams.sort(key=lambda e: e.get("last_backup", ""), reverse=True)
    return cams


def auto_link_cameras() -> dict:
    """Link every UNLINKED camera to a robot whose station+robot key matches its
    name - falling back to a robot with the SAME name (a camera deliberately
    named after its robot, which the key regex can't always parse) - manual
    links are left alone. Only links on an unambiguous single match.
    Returns {linked:[{camera,robot}], ambiguous:[names], unmatched:[names]}."""
    with _LOCK:
        data = load()
        by_key: dict = {}
        by_name: dict = {}
        for e in data["robots"]:
            if e.get("device_type", "robot") == "robot":
                k = _station_robot_key(e.get("robot", ""))
                if k:
                    by_key.setdefault(k, []).append(e)
                n = (e.get("robot") or "").strip().upper()
                if n:
                    by_name.setdefault(n, []).append(e)
        linked, ambiguous, unmatched = [], [], []
        touched = []
        for e in data["robots"]:
            if not str(e.get("device_type", "")).startswith("camera"):
                continue
            if e.get("linked_robot_id"):
                continue                                # keep an existing (manual) link
            k = _station_robot_key(e.get("robot", ""))
            matches = by_key.get(k, []) if k else []
            if not matches:
                # same-name fallback: robot + camera(s) sharing one name across
                # device types link even when the key can't be read
                n = (e.get("robot") or "").strip().upper()
                matches = by_name.get(n, []) if n else []
            if len(matches) > 1:
                # the same robot name lives under several lines (test-cell copies,
                # legacy folders). Prefer the robot in the CAMERA's own cell -
                # they're physically together - which usually resolves it uniquely.
                same_cell = [r for r in matches
                             if (r.get("plant", ""), r.get("line", "")) ==
                                (e.get("plant", ""), e.get("line", ""))]
                if len(same_cell) == 1:
                    matches = same_cell
            if len(matches) == 1:
                e["linked_robot_id"] = matches[0]["id"]
                linked.append({"camera": e.get("robot", ""), "robot": matches[0].get("robot", "")})
                touched.append(e)
            elif len(matches) > 1:
                ambiguous.append(e.get("robot", ""))
            else:
                unmatched.append(e.get("robot", ""))
        if touched:
            _reconcile(data)
            _write(data)
            for e in touched:
                _persist_sidecar(e)
        return {"linked": linked, "ambiguous": ambiguous, "unmatched": unmatched}


def resolve_open_path(e: dict, which: str = "latest") -> str:
    """The folder to open for a robot. 'latest' prefers the Latest mirror but
    falls back to the newest dated snapshot still on disk - a stale or missing
    mirror must not make a robot unopenable (the dated tree is the truth, the
    mirror is a convenience copy). Any other value is an explicit backup-folder
    path picked from the robot's history. Returns "" when nothing exists."""
    if which != "latest":
        return which
    lp = e.get("latest_path", "")
    if lp and Path(lp).is_dir():
        return lp
    # fall back to the newest COMPLETE snapshot; a partial (died mid-download)
    # only opens when it is literally all the robot has - better than nothing,
    # and the UI pills it as partial either way
    for want_partial in (False, True):
        for b in e.get("backups", []):      # newest-first
            if bool(b.get("partial")) != want_partial:
                continue
            p = b.get("path", "")
            if p and Path(p).is_dir():
                return p
    return ""


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# (delete_robot_files was removed with the v0.98 files-are-law pivot: the app
# never deletes backup data. Hiding covers the everyday case; a true delete is
# done in Explorer, and the next scan simply reflects it.)


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
            dt = match.get("device_type")
            if dt and dt != "robot" and e.get("device_type", "robot") == "robot":
                e["device_type"] = dt          # a camera pull teaches an old entry its type
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
        _persist_sidecar(e)
        return e


# -- robot.json sidecar (portable id + config) -----------------------------------
# The library.json index is local + per-machine. To let a copied folder tree
# carry its robots to another PC, we drop a robot.json at each robot folder
# holding the stable id + config (NEVER a password — and NEVER identity: the
# folder's own location and name say which plant/line/robot this is, and storing
# that here too would only create a second source of truth that goes stale the
# moment someone moves the folder in Explorer). The id is what makes an Explorer
# move a MOVE instead of a delete+add. notes.txt / backup.json (written by the
# backup engine) carry the per-snapshot note + stats alongside.

SIDECAR = "robot.json"
# YYYY_MM_DD dated snapshot; the ERBU-era tools wrote 2-digit years (YY_MM_DD),
# and those imported snapshots are the same shape - accept both
_DATE_RE = re.compile(r"^(?:\d{4}|\d{2})_\d{2}_\d{2}$")
_TIME_RE = re.compile(r"^\d{2}_\d{2}_\d{2}$")     # HH_MM_SS


def _read_json(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_dated(snap: Path) -> bool:
    return bool(_TIME_RE.match(snap.name) and _DATE_RE.match(snap.parent.name))


def _robot_folder(e: dict) -> Path | None:
    """The folder that holds a robot's snapshots (where robot.json belongs):
    history_root if known, else derived from latest_path (up from a dated
    snapshot; never the Latest/ mirror)."""
    hr = e.get("history_root")
    if hr and Path(hr).is_dir():
        return Path(hr)
    lp = e.get("latest_path")
    if lp and Path(lp).is_dir():
        p = Path(lp)
        if any(part.lower() == "latest" for part in p.parts):
            return None                      # don't write into a mirror
        return p.parent.parent if _is_dated(p) else p
    return None


def _write_robot_sidecar(e: dict, folder: Path) -> None:
    """Write robot.json for `e` into `folder`. Best-effort; no password. Schema 3
    carries NO plant/line/robot — the folder's location/name is that truth;
    legacy schema-1 identity fields are shed whenever a sidecar is rewritten.
    Schema 3 adds device_type (robot | camera-mtx); older sidecars read as robot."""
    ftp = e.get("ftp") or {}
    data = {
        "schema": 3, "id": e.get("id", ""),
        "device_type": e.get("device_type", "robot"),
        "linked_robot_id": e.get("linked_robot_id", ""),
        "model": e.get("model", ""), "f_number": e.get("f_number", ""),
        "ips": list(e.get("ips", []) or []),
        "ftp": {"user": ftp.get("user", ""), "passive": ftp.get("passive", True)},
        "notes": e.get("notes", ""),
        "aliases": list(e.get("aliases", []) or []),
        "updated": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        tmp = folder / (SIDECAR + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(folder / SIDECAR)
    except OSError:
        log.warning("could not write %s in %s", SIDECAR, folder)


def _persist_sidecar(e: dict) -> None:
    folder = _robot_folder(e)
    if folder is not None:
        _write_robot_sidecar(e, folder)


# -- folder-tree scan (the tree is the source of truth) -------------------------

def _is_latest_mirror(snap: Path, root: Path) -> bool:
    try:
        rel = snap.relative_to(root)
    except ValueError:
        return False
    return any(part.lower() == "latest" for part in rel.parts)


def _snap_taken(snap: Path, meta: dict) -> str:
    if meta.get("taken"):
        return meta["taken"]
    if _is_dated(snap):
        date = snap.parent.name
        if len(date) == 8:                    # ERBU-era 2-digit year (YY_MM_DD)
            date = "20" + date                # -> ISO-comparable with app snapshots
        return date.replace("_", "-") + "T" + snap.name.replace("_", ":")
    try:
        return _dt.datetime.fromtimestamp(snap.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _backup_record(snap: Path, meta: dict) -> dict:
    note = meta.get("note", "")
    if not note:
        try:
            nt = snap / "notes.txt"
            if nt.is_file():
                note = nt.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
    rec = {
        "path": str(snap), "taken": _snap_taken(snap, meta),
        "type": meta.get("type", "") or "", "files": meta.get("files", 0) or 0,
        "bytes": meta.get("bytes", 0) or 0,
        "source": meta.get("source", "") or ("ftp" if meta else "import"),
        "note": note,
    }
    # complete:false = OUR OWN pull that died mid-download (the backup engine
    # writes the marker first and only flips it true as its last step). Only an
    # explicit false marks a partial - a legacy sidecar without the field was
    # written by code that only wrote on success, and a sidecar-less folder is
    # a hand-import (labeled by `source` above), not a partial.
    if meta.get("complete") is False:
        rec["partial"] = True
    return rec


def _pick_latest(backups: list) -> dict | None:
    """The newest record eligible to be 'the latest'. Partial snapshots never
    become latest_path/last_backup - a pull that died mid-download would read
    as a fresh complete backup while silently missing files (the exact lie
    this app exists to prevent). backups is newest-first."""
    for b in backups:
        if not b.get("partial"):
            return b
    return None


def robot_folder_of(snap) -> Path:
    """The robot folder a snapshot belongs to (up from a <date>/<time> pair)."""
    p = Path(snap)
    return p.parent.parent if _is_dated(p) else p


def backup_record(snap) -> dict:
    """One history record for a snapshot folder (reads its backup.json + notes.txt).
    Used by the add-from-folder flow to attach a robot's full dated history."""
    p = Path(snap)
    return _backup_record(p, _read_json(p / "backup.json"))


def _path_identity(robot_dir: Path, root: Path) -> tuple[str, str, str]:
    """(plant, line, robot) from <root>/[plant/]<line>/<robot> path structure."""
    try:
        parts = robot_dir.relative_to(root).parts
    except ValueError:
        parts = (robot_dir.name,)
    robot = parts[-1] if parts else robot_dir.name
    line = parts[-2] if len(parts) >= 2 else ""
    plant = parts[-3] if len(parts) >= 3 else ""
    return plant, line, robot


def scan_signature(root: str | Path) -> str:
    """Cheap change-detection fingerprint of the library tree: directory names +
    mtimes down to the <date> level. Creating/removing/renaming a plant, line,
    robot, date folder — or adding a time folder inside a date (NTFS bumps the
    parent's mtime) — changes it. Read-only, no file contents touched. Empty
    string for an unreachable root (offline network drive)."""
    import hashlib
    root = Path(root)
    if not root.is_dir():
        return ""
    h = hashlib.md5()
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda x: x.path)
        except OSError:
            continue
        for de in entries:
            try:
                if not de.is_dir(follow_symlinks=False):
                    continue
                name = de.name
                if name.endswith((".__part", ".__tmp")):
                    continue
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            h.update(f"{de.path}|{st.st_mtime_ns}".encode("utf-8", "replace"))
            if depth + 1 < 4 and name.lower() != "latest":
                stack.append((Path(de.path), depth + 1))
    return h.hexdigest()


def _scan_disk(root: Path, stats: dict | None = None, progress=None, expect: int = 0) -> tuple:
    """Walk the tree for backup snapshots and group them by robot. Identity is
    the folder's LOCATION (<root>/[plant/]<line>/<robot> — files are law);
    sidecars supply id + config only. Also surfaces the folder skeleton: robot
    folders with no snapshots yet, empty plant folders at root, empty line
    folders inside plants. Read-only. Returns ({key: disk_entry} with backups[]
    newest-first, {"plants": [...], "lines": [{plant, line}]}).
    `progress(done, total, current)` ticks once per snapshot read; `expect` is
    the previous scan's snapshot count (0 = first ever, total unknown)."""
    from . import session  # local import: avoids any import-time coupling

    groups: dict = {}
    sidecars: dict = {}   # robot_dir -> parsed sidecar: one read per robot, not per snapshot
    done = 0

    def _tick(current: str) -> None:
        if progress:
            # never report done > total: a tree that GREW since the estimate
            # pins near 100% instead of overshooting it
            progress(done, (max(expect, done) if expect else 0), current)

    def _on_root(n: int) -> None:
        if n % 25 == 0:
            _tick("finding backup folders… %d found" % n)

    _tick("finding backup folders…")
    for snap in session.find_backup_roots(root, stats=stats,
                                          on_root=_on_root if progress else None):
        if _is_latest_mirror(snap, root):
            continue                                   # mirror = copy of a dated snap
        meta = _read_json(snap / "backup.json")
        robot_dir = snap.parent.parent if _is_dated(snap) else snap
        done += 1
        _tick(robot_dir.name)
        rjson = sidecars.get(robot_dir)
        if rjson is None:
            rjson = _read_json(robot_dir / SIDECAR)
            sidecars[robot_dir] = rjson
        # WHERE the folder sits is the identity — files are law. The sidecar
        # supplies id + config (ips/ftp/notes) and nothing else: any plant/
        # line/robot a legacy schema-1 sidecar (or a copied-along backup.json)
        # still claims is ignored outright — trusting it teleported robots out
        # of the folder the user can SEE them in.
        plant, line, robot = _path_identity(robot_dir, root)
        if not robot:
            continue
        rid = rjson.get("id") or ""
        key = rid or (plant.upper(), line.upper(), robot.upper())
        g = groups.get(key)
        if g is None:
            ftp = rjson.get("ftp") if isinstance(rjson.get("ftp"), dict) else {}
            g = {
                "id": rid, "plant": plant, "line": line, "robot": robot,
                "device_type": rjson.get("device_type") or meta.get("device_type") or "robot",
                "linked_robot_id": rjson.get("linked_robot_id", "") or "",
                "model": rjson.get("model", "") or meta.get("model", ""),
                "f_number": rjson.get("f_number", "") or "",
                "ips": list(rjson.get("ips", []) or []),
                "ftp": {"user": ftp.get("user", ""), "passive": ftp.get("passive", True)},
                "notes": rjson.get("notes", "") or "",
                "aliases": list(rjson.get("aliases", []) or []),
                "history_root": str(robot_dir), "_snaps": [],
            }
            groups[key] = g
        elif str(robot_dir) != g["history_root"]:
            # a COPY living in another folder claims this robot (same sidecar id /
            # identity) — its snapshots fold into the robot's history rather than
            # spawning a twin. Count them so the scan can SAY so; silent absorption
            # reads as "my copied folder never showed up".
            g["_absorbed"] = g.get("_absorbed", 0) + 1
        g["_snaps"].append(_backup_record(snap, meta))

    # second pass: the folder SKELETON. The tree the user built in Explorer IS
    # the library: a folder at robot depth is a robot even with no backups yet
    # (an imported ERBU skeleton / discovery-added robot awaiting its first
    # backup is a real robot), an empty folder at root is a plant, an empty
    # folder inside a plant is a line. The PRESENCE of a robot.json anywhere
    # marks a robot decisively (legacy layouts park robots at other depths) —
    # presence, not contents: a schema-2 sidecar carries no identity to key on.
    # Never descends into dated/mirror/staging dirs or folders already grouped
    # as robots.
    seen_dirs = {Path(g["history_root"]) for g in groups.values()}
    empty_plants: list = []
    empty_lines: list = []

    def _skip_name(n: str) -> bool:
        return (n.endswith((".__part", ".__tmp")) or n.lower() == "latest"
                or bool(_DATE_RE.match(n)) or bool(_TIME_RE.match(n)))

    def _dir_children(d: Path) -> list:
        try:
            with os.scandir(d) as it:   # scandir: is_dir comes with the listing, no per-entry stat
                return [Path(e.path) for e in it
                        if not _skip_name(e.name) and session._entry_is_dir(e)]
        except OSError:
            return []

    def _add_skeleton_robot(c: Path, rj: dict) -> None:
        plant, line, robot = _path_identity(c, root)
        if not robot:
            return
        rid = rj.get("id") or ""
        key = rid or (plant.upper(), line.upper(), robot.upper())
        if key in groups:
            return
        ftp = rj.get("ftp") if isinstance(rj.get("ftp"), dict) else {}
        groups[key] = {
            "id": rid, "plant": plant, "line": line, "robot": robot,
            "device_type": rj.get("device_type") or "robot",
            "linked_robot_id": rj.get("linked_robot_id", "") or "",
            "model": rj.get("model", ""), "f_number": rj.get("f_number", "") or "",
            "ips": list(rj.get("ips", []) or []),
            "ftp": {"user": ftp.get("user", ""), "passive": ftp.get("passive", True)},
            "notes": rj.get("notes", "") or "",
            "aliases": list(rj.get("aliases", []) or []),
            "history_root": str(c), "_snaps": [],
        }

    def _sidecar(d: Path) -> dict | None:
        """The folder's robot.json ({} when unreadable) — None when the file is
        absent. Presence alone marks a robot folder; contents never carry
        identity."""
        return _read_json(d / SIDECAR) if (d / SIDECAR).is_file() else None

    for p1 in _dir_children(root):                     # tier 1: plants
        if p1 in seen_dirs:
            continue                                   # a robot folder sitting at root
        rj = _sidecar(p1)
        if rj is not None:
            _add_skeleton_robot(p1, rj)
            continue
        line_dirs = _dir_children(p1)
        if not line_dirs:
            empty_plants.append(p1.name)
            continue
        for p2 in line_dirs:                           # tier 2: lines
            if p2 in seen_dirs:
                continue                               # legacy <root>/<line>/<robot>
            rj = _sidecar(p2)
            if rj is not None:
                _add_skeleton_robot(p2, rj)
                continue
            robot_dirs = _dir_children(p2)
            if not robot_dirs:
                empty_lines.append({"plant": p1.name, "line": p2.name})
                continue
            for p3 in robot_dirs:                      # tier 3: robots (even empty)
                if p3 not in seen_dirs:
                    _add_skeleton_robot(p3, _sidecar(p3) or {})

    out: dict = {}
    for key, g in groups.items():
        snaps = sorted(g.pop("_snaps"), key=lambda b: b.get("taken", ""), reverse=True)
        g["backups"] = snaps
        newest = _pick_latest(snaps)
        g["latest_path"] = newest["path"] if newest else ""
        g["last_backup"] = newest["taken"] if newest else ""
        out[key] = g
    return out, {"plants": sorted(empty_plants),
                 "lines": sorted(empty_lines, key=lambda x: (x["plant"], x["line"]))}


def _apply_disk(e: dict, disk: dict) -> None:
    """Fold a disk-scanned robot onto an overlay entry. Disk is authoritative
    for what exists on disk — backups/latest_path/history_root AND identity:
    the folder's location says which plant/line the robot is in and its folder
    name IS its name (files are law; renames/moves in the app go through
    relocate, which moves the folder, so the two never disagree). The entry's
    old identity is remembered as an alias. The overlay (user edits) wins for
    config, filled only where empty."""
    e["backups"] = disk["backups"]
    e["latest_path"] = disk["latest_path"]
    e["history_root"] = disk["history_root"]
    if disk.get("last_backup"):
        e["last_backup"] = disk["last_backup"]
    old = (e.get("plant", ""), e.get("line", ""), e.get("robot", ""))
    e["plant"], e["line"], e["robot"] = disk["plant"], disk["line"], disk["robot"]
    if old[2]:
        _add_alias(e, *old)                            # no-op when identity unchanged
    if disk.get("device_type"):
        e["device_type"] = disk["device_type"]         # sidecar is authoritative
    if disk.get("linked_robot_id"):
        e["linked_robot_id"] = disk["linked_robot_id"]
    for k in ("model", "f_number", "notes"):
        if not e.get(k) and disk.get(k):
            e[k] = disk[k]
    ips = e.get("ips", []) or []
    for ip in disk.get("ips", []) or []:
        if ip and ip not in ips:
            ips.append(ip)
    e["ips"] = ips
    eftp = e.get("ftp") or {}
    dftp = disk.get("ftp") or {}
    if dftp.get("user") and not eftp.get("user"):
        # same rule as _merge_pair: adopting the sidecar's user carries its
        # passive flag too (unless the overlay already recorded one) — a
        # passive=False robot must survive a rescan, not just a merge
        eftp["user"] = dftp["user"]
        eftp.setdefault("passive", dftp.get("passive", True))
        e["ftp"] = eftp
    for a in disk.get("aliases", []) or []:                     # union: aliases are additive memory
        _add_alias(e, a.get("plant", ""), a.get("line", ""), a.get("robot", ""))


def _union_disk(e: dict, disk: dict) -> int:
    """A second disk folder maps to an already-applied entry (e.g. a stray folder
    under the robot's OLD name, re-matched via an alias). Combine the histories
    instead of letting the later folder clobber the earlier one. Returns how many
    snapshots were newly folded in (for the scan's absorption report)."""
    have = {b.get("path") for b in e.get("backups", [])}
    added = 0
    for b in disk.get("backups", []):
        if b.get("path") not in have:
            e.setdefault("backups", []).append(b)
            have.add(b.get("path"))
            added += 1
    e["backups"].sort(key=lambda b: b.get("taken", ""), reverse=True)
    newest = _pick_latest(e["backups"])
    if newest:
        e["latest_path"] = newest["path"]
        e["last_backup"] = newest.get("taken", e.get("last_backup", ""))
    for a in disk.get("aliases", []) or []:
        _add_alias(e, a.get("plant", ""), a.get("line", ""), a.get("robot", ""))
    return added


def _merge_scan(data: dict, scanned: dict, absorbed: list | None = None) -> set:
    """Fold the disk scan onto the overlay entries. Returns the id()s of every
    entry that matched (or was created from) a disk folder — the caller drops
    the rest: files are law, so a robot exists exactly as long as its folder.
    `absorbed` (if given) collects (robot, count) for snapshots folded into an
    entry from a SECOND folder (alias re-match) — the scan's absorption report."""
    by_id = {e["id"]: e for e in data["robots"] if e.get("id")}
    applied: set = set()                        # entries already filled from disk this scan
    # An entry's HOME folder (the one carrying its sidecar id) must apply
    # before any alias-matched stray: a leftover copy under the robot's OLD
    # name can sort ahead of the renamed folder, and whichever folder applies
    # first sets the identity — a stray must fold in as history, not rename
    # the robot back.
    ordered = sorted(scanned.values(),
                     key=lambda d: 0 if d.get("id") and d["id"] in by_id else 1)
    for disk in ordered:
        e = None
        did = disk.get("id")
        if did and did in by_id:
            # the sidecar travels WITH its folder, so its id is the robot even
            # after an Explorer rename/move — identity refreshes from the path
            # in _apply_disk (a same-id COPY was already absorbed in _scan_disk)
            e = by_id[did]
        if e is None:
            e = _find_match(data, {"robot": disk.get("robot"), "line": disk.get("line"),
                                   "plant": disk.get("plant")})
        if e is None:
            ne = _normalize(disk)
            data["robots"].append(ne)
            if ne.get("id"):
                by_id[ne["id"]] = ne
            applied.add(id(ne))
        elif id(e) in applied:
            n = _union_disk(e, disk)            # 2nd folder for one robot -> combine, don't clobber
            if n and absorbed is not None:
                absorbed.append((e.get("robot", "") or "", n))
        else:
            _apply_disk(e, disk)
            applied.add(id(e))
    return applied


def scan_library_root(root: str | Path, progress=None) -> dict:
    """Rebuild the library from the backup folder tree — THE source of truth.
    A robot exists because its folder exists: folders found on disk are
    added/refreshed (overlay data like the hidden flag and user edits survive
    on matched entries), and entries whose folders are GONE are dropped —
    deleting a folder in Explorer deletes the robot, exactly as the tree says.

    The one deliberate exception: an UNREACHABLE root (offline network drive /
    unplugged USB) is not the same as deleted folders — the last known library
    is served with everything marked stale instead of being wiped.

    `progress(done, total, current)`, when given, ticks as snapshots are read —
    total is the previous scan's snapshot count (an estimate; 0 = first ever),
    so a boot-time progress bar has something honest to draw."""
    root = Path(root)
    with _LOCK:
        data = load()
        absorbed_raw: list = []
        if root.is_dir():
            stats: dict = {}
            expect = sum(len(e.get("backups", []) or []) for e in data.get("robots", []))
            scanned, empty_folders = _scan_disk(root, stats, progress=progress, expect=expect)
            data["empty_folders"] = empty_folders
            # snapshots folded into a robot by IDENTITY while living in another
            # folder (a copied tree carrying its robot.json) — pull the counts
            # out before the groups become entries, so they never hit the cache
            for disk in scanned.values():
                n = disk.pop("_absorbed", 0)
                if n:
                    absorbed_raw.append((disk.get("robot", "") or "", n))
            keep = _merge_scan(data, scanned, absorbed=absorbed_raw)
            data["robots"] = [e for e in data["robots"] if id(e) in keep]
            data["scan_truncated"] = bool(stats.get("truncated"))
        _reconcile(data)
        _write(data)
        if absorbed_raw:
            # report-only, set AFTER the write: the toast belongs to THIS scan,
            # not to every later cache-served listing
            agg: dict = {}
            for name, n in absorbed_raw:
                agg[name] = agg.get(name, 0) + n
            data["scan_absorbed"] = [{"robot": k, "count": v} for k, v in sorted(agg.items())]
        return data


# -- rename / merge / relocate (folders move WITH the entry) ---------------------
# Cody's library is full of legacy robots backed up before auto-naming worked, so
# many are IP-named and some duplicated. These primitives let the UI fix a name
# (and physically move the folder tree), merge duplicates, and tidy up - always
# inside library_root(), always transactionally (os.rename, or copy-verify-delete
# across volumes), recording the old identity as an alias so a stray old-named
# folder re-merges on the next scan instead of spawning a duplicate.

class PathGuard(Exception):
    """A relocate/merge target resolved outside the configured library root."""


def _root() -> Path:
    try:
        return Path(settings.library_root()).resolve()
    except OSError:
        return Path(settings.library_root())


def _safe_resolve(p) -> Path:
    try:
        return Path(p).resolve()
    except OSError:
        return Path(p)


def _alias_key(plant: str, line: str, robot: str) -> tuple[str, str, str]:
    return ((plant or "").upper(), (line or "").upper(), (robot or "").upper())


def _alias_record(plant: str, line: str, robot: str) -> dict:
    return {"plant": plant or "", "line": line or "", "robot": robot or ""}


def _add_alias(e: dict, plant: str, line: str, robot: str) -> None:
    """Remember a robot's former identity so a stray folder under the old name
    re-merges into this entry. No-ops for the entry's current identity + dups."""
    if not (robot or "").strip():
        return
    new = _alias_key(plant, line, robot)
    if new == _alias_key(e.get("plant", ""), e.get("line", ""), e.get("robot", "")):
        return
    aliases = e.setdefault("aliases", [])
    have = {_alias_key(a.get("plant", ""), a.get("line", ""), a.get("robot", "")) for a in aliases}
    if new not in have:
        aliases.append(_alias_record(plant, line, robot))


def _ident(t) -> dict:
    return {"plant": t[0], "line": t[1], "robot": t[2]}


def _ident_e(e: dict) -> dict:
    return _ident((e.get("plant", ""), e.get("line", ""), e.get("robot", "")))


def _robot_dir_for(root: Path, plant: str, line: str, robot: str) -> Path:
    """The robot folder <root>/<plant?>/<line>/<robot>, built through the backup
    engine's own path rules (blank-plant omission + _safe_name) via a sentinel
    timestamp so it always matches where a real backup would land."""
    from . import ftpbackup
    return ftpbackup.dated_dir(root, plant, line, robot, _dt.datetime(2000, 1, 1)).parent.parent


def _latest_dir_for(root: Path, plant: str, line: str, robot: str) -> Path:
    from . import ftpbackup
    return ftpbackup.latest_dir(root, plant, line, robot)


def _verify_tree(src: Path, dst: Path, *, strict: bool = False) -> bool:
    """Every file under src exists under dst at the same size (the post-copy
    sanity net). strict=True is the pre-delete bar: both-direction file-set
    compare plus byte-for-byte contents - backup.json excluded, because the
    engine's own metadata legitimately carries a different robot label on each
    side of a duplicate; its files/bytes stats are compared by the caller."""
    src, dst = Path(src), Path(dst)
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        t = dst / f.relative_to(src)
        try:
            if not t.is_file() or t.stat().st_size != f.stat().st_size:
                return False
            if strict and f.name != "backup.json" and not filecmp.cmp(f, t, shallow=False):
                return False
        except OSError:
            return False
    if strict:
        for f in dst.rglob("*"):
            if f.is_file():
                try:
                    if not (src / f.relative_to(dst)).is_file():
                        return False
                except OSError:
                    return False
    return True


def _move_tree(src, dst) -> None:
    """Move a folder: atomic os.rename on the same volume, else copy to a .__part
    staging dir, verify, rename into place, then delete the source. A crash
    mid-copy can only ever leave a .__part dir - never a partial tree at the
    destination's final name, which a later merge could mistake for a complete
    snapshot. Raises OSError (source intact) if anything short of the final
    source delete fails."""
    src, dst = Path(src), Path(dst)
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass
    part = dst.with_name(dst.name + ".__part")
    if part.exists():
        shutil.rmtree(part, ignore_errors=True)    # stale leftover from a prior crash
    try:
        shutil.copytree(src, part)
        if not _verify_tree(src, part):
            raise OSError(f"verify failed copying {src} -> {dst}")
        os.replace(part, dst)
    except OSError:
        shutil.rmtree(part, ignore_errors=True)
        raise
    shutil.rmtree(src)


def _prune_empty_dirs(start, root) -> None:
    """Walk up from `start` removing now-empty folders, stopping at the first
    non-empty folder or the library root. Tolerates `start` already gone."""
    root_r = _safe_resolve(Path(root))
    d = Path(start)
    while d != d.parent:
        dr = _safe_resolve(d)
        if dr == root_r or not _within(dr, root_r):
            break
        if d.exists():
            if not d.is_dir():
                break
            try:
                next(d.iterdir())
                break                                  # not empty -> stop pruning
            except StopIteration:
                pass
            try:
                d.rmdir()
            except OSError:
                break
        d = d.parent


def _entry_for_folder(data: dict, folder) -> dict | None:
    """The entry, if any, whose history_root IS this folder (so a relocate onto an
    existing robot folder merges into that robot rather than clobbering it)."""
    target = _safe_resolve(Path(folder))
    for e in data["robots"]:
        hr = e.get("history_root")
        if hr and _safe_resolve(Path(hr)) == target:
            return e
    return None


def _scan_robot_backups(robot_dir) -> list:
    """Newest-first history records for one robot folder, read from disk (its
    dated <date>/<time> snapshots' backup.json + notes.txt). Disk is the source
    of truth for what exists, so we rebuild backups[] from it after a move."""
    robot_dir = Path(robot_dir)
    out: list = []
    if not robot_dir.is_dir():
        return out
    for date_dir in robot_dir.iterdir():
        if not (date_dir.is_dir() and _DATE_RE.match(date_dir.name)):
            continue
        for time_dir in date_dir.iterdir():
            if time_dir.is_dir() and _TIME_RE.match(time_dir.name):
                out.append(_backup_record(time_dir, _read_json(time_dir / "backup.json")))
    out.sort(key=lambda b: b.get("taken", ""), reverse=True)
    return out


def _rebuild_backups(e: dict, robot_dir, root) -> None:
    """Recompute e's backups[]/latest_path/last_backup from its (new) robot folder
    on disk, preserving any still-present non-dated 'flat' imports that live
    outside the dated tree."""
    dated = _scan_robot_backups(robot_dir)
    seen = {b["path"] for b in dated}
    extra: list = []
    for b in e.get("backups", []):
        p = b.get("path", "")
        if not p or p in seen or _is_dated(Path(p)):
            continue                                   # dated history is rebuilt from disk above
        # keep non-dated 'flat' imports even when currently offline (a removable /
        # network drive may just be disconnected) - _reconcile marks them stale, in
        # line with the "never auto-delete an offline backup" policy.
        seen.add(p)
        extra.append(b)
    out = dated + extra
    out.sort(key=lambda b: b.get("taken", ""), reverse=True)
    e["backups"] = out
    newest = _pick_latest(out)
    e["latest_path"] = newest["path"] if newest else ""
    e["last_backup"] = newest.get("taken", "") if newest else \
        ("" if out else e.get("last_backup", ""))


def _regen_latest(e: dict, root: Path):
    """Rebuild the Latest/<robot> mirror from the newest existing dated snapshot
    (temp dir -> copytree -> atomic replace). Newest missing -> leave any existing
    mirror untouched (don't destroy a good-but-currently-offline mirror)."""
    newest = None
    for b in e.get("backups", []):                     # newest-first
        if b.get("partial"):
            continue                                   # a partial never becomes the mirror
        p = Path(b.get("path", ""))
        if p.is_dir():
            newest = p
            break
    if newest is None:
        return None
    latest = _latest_dir_for(root, e.get("plant", ""), e.get("line", ""), e.get("robot", ""))
    tmp = latest.with_name(latest.name + ".__tmp")
    try:
        latest.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(newest, tmp)
        if latest.exists():
            shutil.rmtree(latest, ignore_errors=True)
        os.replace(tmp, latest)
        return latest
    except OSError:
        log.exception("Latest mirror regen failed for %s (dated snapshot intact)", e.get("robot"))
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def _rel(p, root) -> str:
    try:
        return str(_safe_resolve(Path(p)).relative_to(_safe_resolve(Path(root))))
    except (OSError, ValueError):
        return str(p)


def _stat_pair(meta: dict) -> tuple[int, int]:
    return (int(meta.get("files", 0) or 0), int(meta.get("bytes", 0) or 0))


def _statd(t) -> dict:
    return {"files": t[0], "bytes": t[1]}


def _merge_into(src_dir, dst_dir, root, src_latest) -> dict:
    """Fold src_dir's dated snapshots into dst_dir. A snapshot whose <date>/<time>
    already exists in dst is a DUPLICATE: skipped (the redundant source copy is
    dropped) when identical, flagged as a conflict (never moved, never deleted)
    when its backup.json files/bytes differ. Moves one snapshot at a time.

    The source robot folder is removed ONLY when nothing but its (now-stale)
    robot.json sidecar is left - i.e. it held nothing but the moved-away dated
    snapshots. Anything else (a conflicting snapshot, a flat/non-dated import, a
    stray user file) is preserved, and its Latest mirror is kept too. Returns
    {moved, skipped, conflicts, source_removed}."""
    moved: list = []
    skipped: list = []
    conflicts: list = []
    if src_dir is None or not Path(src_dir).exists():
        return {"moved": moved, "skipped": skipped, "conflicts": conflicts, "source_removed": False}
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    date_dirs = sorted(p for p in src_dir.iterdir() if p.is_dir() and _DATE_RE.match(p.name))
    for date_dir in date_dirs:
        time_dirs = sorted(p for p in date_dir.iterdir() if p.is_dir() and _TIME_RE.match(p.name))
        for time_dir in time_dirs:
            target = dst_dir / date_dir.name / time_dir.name
            rel = _rel(target, root)
            if target.exists():
                smeta = _read_json(time_dir / "backup.json")
                dmeta = _read_json(target / "backup.json")
                s, d = _stat_pair(smeta), _stat_pair(dmeta)
                # "identical" must clear three independent bars before the
                # redundant source copy may be dropped: both sidecars readable,
                # equal stats, and the trees verify file-for-file (both
                # directions, byte contents). Missing sidecars compare (0,0) and
                # a partial destination can hold a matching backup.json - both
                # used to pass the stats-only check and delete an intact source.
                if smeta and dmeta and s == d and _verify_tree(time_dir, target, strict=True):
                    skipped.append(rel)
                    shutil.rmtree(time_dir, ignore_errors=True)   # verified identical -> drop the redundant source copy
                else:
                    conflicts.append({"path": rel, "src": _statd(s), "dst": _statd(d)})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _move_tree(time_dir, target)
            moved.append(rel)
        _prune_empty_dirs(date_dir, root)
    # Remove the source robot folder ONLY when nothing but its sidecar remains;
    # never blow away conflicts or non-dated (flat-import / user) content.
    source_removed = False
    if src_dir.exists() and _within(_safe_resolve(src_dir), Path(root)):
        remaining = [p for p in src_dir.iterdir() if p.name != SIDECAR]
        if not remaining:
            shutil.rmtree(src_dir, ignore_errors=True)
            source_removed = True
    # the source Latest mirror is only stale once the source is actually gone
    if source_removed and src_latest is not None and Path(src_latest).exists() \
            and _within(_safe_resolve(Path(src_latest)), Path(root)):
        shutil.rmtree(src_latest, ignore_errors=True)
        _prune_empty_dirs(src_latest, root)
    if source_removed:
        _prune_empty_dirs(src_dir, root)
    return {"moved": moved, "skipped": skipped, "conflicts": conflicts, "source_removed": source_removed}


def _merge_pair(data: dict, prim: dict, sec: dict, root: Path) -> dict:
    """Survivor = prim; fold sec's folders + history in, alias sec's identity onto
    prim, drop sec, regenerate prim's mirror. Caller holds _LOCK and does the
    _reconcile/_write/_persist_sidecar."""
    prim_dir = _robot_folder(prim) or _robot_dir_for(
        root, prim.get("plant", ""), prim.get("line", ""), prim.get("robot", ""))
    sec_dir = _robot_folder(sec)
    for d in (prim_dir, sec_dir):
        if d is not None and Path(d).exists() and not _within(_safe_resolve(Path(d)), root):
            raise PathGuard(f"merge target escapes library root: {d}")
    sec_latest = _latest_dir_for(root, sec.get("plant", ""), sec.get("line", ""), sec.get("robot", "")) \
        if sec_dir is not None else None
    result = _merge_into(sec_dir, prim_dir, root, sec_latest)
    if sec_dir is not None and not result["moved"] and not result["skipped"] \
            and not result["conflicts"] and not result["source_removed"]:
        # The fold was a total no-op: sec's folder holds only non-dated content
        # (a flat import / stray files) that a merge never moves. Report it and
        # change NOTHING - an alias/config fold here would leave half-merged
        # state behind a result that claims "merged" while both robots visibly
        # survive untouched.
        result["blocked"] = "no dated snapshots to fold in (non-dated files are never moved by a merge)"
        return result
    # Record sec's identity as an alias on prim either way, so a future scan of any
    # leftover sec folder re-merges into prim instead of duplicating.
    _add_alias(prim, sec.get("plant", ""), sec.get("line", ""), sec.get("robot", ""))
    for a in sec.get("aliases", []) or []:
        _add_alias(prim, a.get("plant", ""), a.get("line", ""), a.get("robot", ""))
    # Fold sec's non-identity config into prim (same rules as register_backup):
    # union IPs, fill prim's blanks - sec is about to be dropped and may hold the
    # only recorded address/notes for this physical robot. prim's own values are
    # never clobbered, and sec's hidden flag never propagates (a visible robot
    # must not vanish because a hidden duplicate was folded in).
    ips = prim.get("ips") or []
    for ip in sec.get("ips") or []:
        if ip and ip not in ips:
            ips.append(ip)
    prim["ips"] = ips
    for k in ("model", "f_number"):
        if sec.get(k) and not prim.get(k):
            prim[k] = sec[k]
    pftp = dict(prim.get("ftp") or {})
    sftp = sec.get("ftp") or {}
    if sftp.get("user") and not pftp.get("user"):
        pftp["user"] = sftp["user"]
        pftp.setdefault("passive", sftp.get("passive", True))
        prim["ftp"] = pftp
    snotes = (sec.get("notes") or "").strip()
    pnotes = (prim.get("notes") or "").strip()
    if snotes and not pnotes:
        prim["notes"] = snotes
    elif snotes and snotes not in pnotes:
        prim["notes"] = pnotes + " · [" + (sec.get("robot") or "merged") + "] " + snotes
    prim["history_root"] = str(prim_dir)
    if sec_dir is None:
        # an empty placeholder entry (never had a folder) -> drop it
        data["robots"] = [x for x in data["robots"] if x.get("id") != sec["id"]]
        result["secondary_removed"] = True
    elif result.get("source_removed"):
        # sec's folder was fully folded in and removed -> drop its entry
        data["robots"] = [x for x in data["robots"] if x.get("id") != sec["id"]]
        result["secondary_removed"] = True
    else:
        # conflicts / flat imports / an offline folder remain -> KEEP sec's entry
        # pointing at the leftovers (never orphan or auto-delete real data). Only
        # refresh from disk when the folder is actually reachable.
        if Path(sec_dir).exists():
            _rebuild_backups(sec, sec_dir, root)
            _regen_latest(sec, root)
        result["secondary_removed"] = False
    _rebuild_backups(prim, prim_dir, root)
    _regen_latest(prim, root)
    return result


def relocate_robot(robot_id: str, plant: str, line: str, robot: str) -> dict:
    """Rename/relocate a robot, MOVING its on-disk folder tree with it.

    No collision at the destination -> a single os.rename (atomic) of the robot
    folder + a regenerated Latest mirror. Destination already exists -> a MERGE
    (snapshot-by-snapshot, duplicate <date>/<time> skipped/flagged). The id is
    preserved; the old identity is recorded as an alias; the sidecar is rewritten.

    Returns {"action": "noop"|"renamed"|"merged"|"blocked", ...} - "blocked"
    when a collision-merge had nothing to fold (see merge_robots). Raises
    ValueError (bad args / unknown id), PathGuard (escapes root), OSError
    (move failure)."""
    plant = (plant or "").strip()
    line = (line or "").strip()
    robot = (robot or "").strip()
    if not robot:
        raise ValueError("robot name required")
    if robot.lower() == "latest":
        raise ValueError("'Latest' is reserved (it names the mirror folder)")
    with _LOCK:
        data = load()
        e = next((x for x in data["robots"] if x.get("id") == robot_id), None)
        if e is None:
            raise ValueError("robot not in library")
        old = (e.get("plant", ""), e.get("line", ""), e.get("robot", ""))
        if _alias_key(*old) == _alias_key(plant, line, robot):
            return {"action": "noop", "id": robot_id, "from": _ident(old), "to": _ident(old)}

        root = _root()
        src = _robot_folder(e)
        dst = _robot_dir_for(root, plant, line, robot)
        if not _within(_safe_resolve(dst), root):
            raise PathGuard(f"destination escapes library root: {dst}")
        if src is not None and src.exists() and not _within(_safe_resolve(src), root):
            raise PathGuard(f"source escapes library root: {src}")

        same_path = src is not None and _safe_resolve(src) == _safe_resolve(dst)
        merge = dst.exists() and not same_path

        if merge:
            owner = _entry_for_folder(data, dst)
            if owner is not None and owner is not e:
                # Only fold into the destination entry when it is genuinely the SAME
                # robot identity we were asked for. A different identity that merely
                # sanitizes to the same folder name is a name conflict, not a
                # duplicate - refuse rather than merge into the wrong robot.
                if _alias_key(owner.get("plant", ""), owner.get("line", ""), owner.get("robot", "")) \
                        != _alias_key(plant, line, robot):
                    raise ValueError(
                        f"destination folder name collides with a different robot ({owner.get('robot', '')})")
                res = _merge_pair(data, owner, e, root)
                if res.get("blocked"):
                    # nothing was folded and e keeps its identity: surface the
                    # block instead of claiming a merge (nothing to persist)
                    return {"action": "blocked", "reason": res["blocked"], "id": e["id"],
                            "from": _ident(old), "to": _ident_e(owner)}
                _reconcile(data)
                _write(data)
                _persist_sidecar(owner)
                res.update({"action": "merged", "id": owner["id"],
                            "removed_id": e["id"] if res.get("secondary_removed") else None,
                            "from": _ident(old), "to": _ident_e(owner)})
                return res
            # the destination is an orphan folder (no entry) -> e adopts + folds into it
            src_latest = _latest_dir_for(root, *old) if src is not None else None
            res = _merge_into(src, dst, root, src_latest)
            e["plant"], e["line"], e["robot"] = plant, line, robot
            _add_alias(e, *old)                                # after the rename: old != current
            e["history_root"] = str(dst)
            _rebuild_backups(e, dst, root)
            _regen_latest(e, root)
            _reconcile(data)
            _write(data)
            _persist_sidecar(e)
            res.update({"action": "merged", "id": e["id"], "removed_id": None,
                        "from": _ident(old), "to": _ident((plant, line, robot))})
            return res

        # ---- clean rename: destination is free ----
        moving = src is not None and src.exists() and not same_path
        if moving:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _move_tree(src, dst)
            src_latest = _latest_dir_for(root, *old)
            if src_latest.exists() and _within(_safe_resolve(src_latest), root):
                shutil.rmtree(src_latest, ignore_errors=True)
            _prune_empty_dirs(src_latest, root)
            _prune_empty_dirs(src, root)
        e["plant"], e["line"], e["robot"] = plant, line, robot
        _add_alias(e, *old)                                    # after the rename: old != current
        if moving:
            e["history_root"] = str(dst)
            _rebuild_backups(e, dst, root)
            _regen_latest(e, root)
        _reconcile(data)
        _write(data)
        _persist_sidecar(e)
        return {"action": "renamed", "id": robot_id,
                "from": _ident(old), "to": _ident((plant, line, robot))}


def merge_robots(primary_id: str, secondary_id: str) -> dict:
    """Explicitly merge secondary INTO primary (folders + history). Refuses a
    cross-line merge (a robot name can legitimately repeat across lines). A
    secondary whose folder gives the merge nothing to fold (only non-dated
    content) comes back "blocked" with a reason - never a claimed merge that
    was a silent no-op. Returns {"action": "merged"|"refused"|"blocked", ...}."""
    if primary_id == secondary_id:
        raise ValueError("cannot merge a robot into itself")
    with _LOCK:
        data = load()
        prim = next((x for x in data["robots"] if x.get("id") == primary_id), None)
        sec = next((x for x in data["robots"] if x.get("id") == secondary_id), None)
        if prim is None or sec is None:
            raise ValueError("robot not in library")
        if (prim.get("line", "") or "").upper() != (sec.get("line", "") or "").upper():
            return {"action": "refused", "reason": "cross-line",
                    "primary": _ident_e(prim), "secondary": _ident_e(sec)}
        root = _root()
        res = _merge_pair(data, prim, sec, root)
        if res.get("blocked"):
            # a total no-op is NOT a merge: report it honestly, persist nothing
            return {"action": "blocked", "reason": res["blocked"],
                    "primary": _ident_e(prim), "secondary": _ident_e(sec)}
        _reconcile(data)
        _write(data)
        _persist_sidecar(prim)
        if not res.get("secondary_removed"):
            _persist_sidecar(sec)                  # sec kept its leftovers -> refresh its sidecar
        res.update({"action": "merged", "id": primary_id,
                    "removed_id": secondary_id if res.get("secondary_removed") else None,
                    "primary": _ident_e(prim), "secondary": _ident_e(sec)})
        return res
