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


def set_pinned(robot_id: str, taken: str, pinned: bool) -> dict | None:
    """Pin/unpin one snapshot as a permanent cleanup exemption (overlay-only,
    like hidden/favorite). Pins live on the ENTRY, keyed by the snapshot's
    `taken` stamp — a flag inside backups[] would be erased by the next scan,
    which rebuilds that list wholesale from disk, and `taken` survives
    relocates where paths don't. Never written into the backup folder itself
    (backups are read-only evidence)."""
    taken = (taken or "").strip()
    if not taken:
        return None
    with _LOCK:
        data = load()
        for e in data["robots"]:
            if e.get("id") == robot_id:
                if pinned and not any(b.get("taken") == taken
                                      for b in e.get("backups") or []):
                    return None    # pin-on needs a real snapshot; unpin always
                                   # works so a stale pin can be cleared
                pins = [p for p in (e.get("pins") or []) if p]
                if pinned and taken not in pins:
                    pins.append(taken)
                elif not pinned and taken in pins:
                    pins.remove(taken)
                e["pins"] = pins
                _write(data)      # pins touch no paths - skip the _reconcile stat sweep
                return e
        return None


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
        # One row per folder: a run that topped an existing snapshot up (rather
        # than stacking a near-twin beside it) REPLACES that snapshot's row -
        # two rows for one path would double-count the same files forever.
        path = backup.get("path", "")
        if path:
            e["backups"] = [b for b in e.get("backups", []) if b.get("path") != path]
        e["backups"].insert(0, backup)
        e["last_backup"] = _when(backup) or e.get("last_backup", "")
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


# <root>/_staged/ — the cleanup staging area: snapshots moved out of the library
# wait here (mirrored plant/line/robot/date/time) until a human deletes the
# folder in Explorer. Reserved + invisible to every walker, same contract as
# the Latest mirror: its contents must never read back as robots/backups, and
# changes inside it must never perturb scan_signature.
STAGED_NAME = "_staged"


def _is_staged(snap: Path, root: Path) -> bool:
    try:
        rel = snap.relative_to(root)
    except ValueError:
        return False
    return any(part.lower() == STAGED_NAME for part in rel.parts)


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
    # a snapshot a later run added photos to rather than stacking a near-twin
    # beside it (mtxbackup._settle) - `taken` still means when it was pulled
    if meta.get("updated"):
        rec["updated"] = meta["updated"]
    # complete:false = OUR OWN pull that died mid-download (the backup engine
    # writes the marker first and only flips it true as its last step). Only an
    # explicit false marks a partial - a legacy sidecar without the field was
    # written by code that only wrote on success, and a sidecar-less folder is
    # a hand-import (labeled by `source` above), not a partial.
    if meta.get("complete") is False:
        rec["partial"] = True
    return rec


def _when(b: dict | None) -> str:
    """When a snapshot last GAINED files: its top-up time if it has one, else
    when it was taken. "Last backup" means the freshest evidence, and a camera
    snapshot that today's run added photos to is fresher than its own take time
    (mtxbackup._settle) - reading `taken` alone would report a camera visited an
    hour ago as weeks stale."""
    return (b.get("updated") or b.get("taken", "")) if b else ""


def _pick_latest(backups: list) -> dict | None:
    """The newest record eligible to be 'the latest'. Partial snapshots never
    become latest_path/last_backup - a pull that died mid-download would read
    as a fresh complete backup while silently missing files (the exact lie
    this app exists to prevent). backups is newest-first."""
    for b in backups:
        if not b.get("partial"):
            return b
    return None


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
                if name.endswith((".__part", ".__tmp")) or name.lower() == STAGED_NAME:
                    continue
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            h.update(f"{de.path}|{st.st_mtime_ns}".encode("utf-8", "replace"))
            if depth + 1 < 4 and name.lower() != "latest":
                stack.append((Path(de.path), depth + 1))
    return h.hexdigest()


def settled_signature(root: str | Path, tries: int = 5) -> str:
    """scan_signature re-walked until two consecutive reads agree. NTFS flushes
    directory-mtime updates lazily, so the first walk right after writes can
    observe pre-flush values (the walk itself nudges the flush along). Callers
    that STORE a signature as a baseline want the settled value — the one
    future listings will see — or the very next look reads as a phantom tree
    change and pays a rescan for nothing."""
    s = scan_signature(root)
    for _ in range(tries):
        s2 = scan_signature(root)
        if s2 == s:
            return s
        s = s2
    return s


def _finalize_group(g: dict) -> dict:
    """A walk group shaped for consumers: `_snaps` becomes backups[] newest-
    first with latest_path/last_backup derived (partials never latest). Copies
    — the walk's own group keeps accumulating (an absorbed twin folder later
    in the walk may still add snapshots)."""
    out = {k: v for k, v in g.items() if k != "_snaps"}
    snaps = sorted(g.get("_snaps", []), key=lambda b: b.get("taken", ""), reverse=True)
    out["backups"] = snaps
    newest = _pick_latest(snaps)
    out["latest_path"] = newest["path"] if newest else ""
    out["last_backup"] = _when(newest)
    return out


def _favorite_preview(e: dict) -> dict | None:
    """A display-ready copy of a favorite's last-known entry, its backups
    freshly re-read from its own folder — published ahead of the walk so
    starred robots land first on a cold/blocking scan. Preview only: the
    walk republishes this folder authoritatively and the final merge is
    unaffected. None when the folder is unreachable (preview must not claim
    a robot the walk may be about to drop)."""
    d = Path(e.get("history_root") or "")
    if not d.is_dir():
        return None
    snaps = _scan_robot_backups(d)
    g = {
        "id": e.get("id", ""), "plant": e.get("plant", ""), "line": e.get("line", ""),
        "robot": e.get("robot", ""), "device_type": e.get("device_type", "robot"),
        "linked_robot_id": e.get("linked_robot_id", ""), "model": e.get("model", ""),
        "f_number": e.get("f_number", ""), "ips": list(e.get("ips", []) or []),
        "notes": e.get("notes", ""), "history_root": str(d),
        "hidden": bool(e.get("hidden")), "favorite": True,
        "backups": snaps,
    }
    newest = _pick_latest(snaps)
    g["latest_path"] = newest["path"] if newest else ""
    g["last_backup"] = _when(newest)
    return g


def _scan_disk(root: Path, stats: dict | None = None, progress=None, expect: int = 0,
               on_entry=None) -> tuple:
    """Walk the tree for backup snapshots and group them by robot. Identity is
    the folder's LOCATION (<root>/[plant/]<line>/<robot> — files are law);
    sidecars supply id + config only. Also surfaces the folder skeleton: robot
    folders with no snapshots yet, empty plant folders at root, empty line
    folders inside plants. Read-only. Returns ({key: disk_entry} with backups[]
    newest-first, {"plants": [...], "lines": [{plant, line}]}).
    `progress(done, total, current)` ticks once per snapshot read; `expect` is
    the previous scan's snapshot count (0 = first ever, total unknown).
    `on_entry(group)`, when given, is called with a finalized COPY of each
    robot group as the walk leaves its folder (and per skeleton robot) — the
    progressive feed a cold-scan UI renders while the walk runs. Entries only
    ever appear or grow through this feed; nothing is dropped by it (a missing
    robot means "not reached yet", and deletions settle in the final merge)."""
    from . import session  # local import: avoids any import-time coupling

    groups: dict = {}
    sidecars: dict = {}   # robot_dir -> parsed sidecar: one read per robot, not per snapshot
    done = 0
    prev_g: dict | None = None   # the group the walk is currently filling (for on_entry)

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
        if _is_latest_mirror(snap, root) or _is_staged(snap, root):
            continue                # mirror = copy of a dated snap; _staged = awaiting deletion
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
        # the walk moved on to another robot: the one it just left is complete
        # (an absorbed twin later in the walk republishes it, grown)
        if on_entry is not None and prev_g is not None and g is not prev_g:
            on_entry(_finalize_group(prev_g))
        prev_g = g
        g["_snaps"].append(_backup_record(snap, meta))

    if on_entry is not None and prev_g is not None:
        on_entry(_finalize_group(prev_g))              # the walk's last robot

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
        return (n.endswith((".__part", ".__tmp")) or n.lower() in ("latest", STAGED_NAME)
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
        if on_entry is not None:
            on_entry(_finalize_group(groups[key]))     # skeleton robots stream too

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

    out = {key: _finalize_group(g) for key, g in groups.items()}
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
        e["last_backup"] = _when(newest) or e.get("last_backup", "")
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


def scan_library_root(root: str | Path, progress=None, on_entry=None) -> dict:
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
    so a boot-time progress bar has something honest to draw.

    `on_entry(entry)`, when given, streams a display-ready copy of each robot
    as the walk completes its folder — favorites (from the last-known overlay)
    are published FIRST, so a cold scan's UI fills starred robots before the
    walk reaches them. The stream only ever adds or grows entries (same key =
    newer copy); drops are decided solely by the final merged result."""
    root = Path(root)
    scanned = empty_folders = None
    stats: dict = {}
    if root.is_dir():
        # The slow disk walk runs OUTSIDE the lock: with the scan on a
        # background thread, a rename/note edit landing mid-walk must not
        # block behind the whole walk. The merge below re-loads under the
        # lock, so it folds onto the freshest overlay; a folder the walk saw
        # at its pre-move path merges stale, which the scan runner detects
        # (its start/end signatures differ) and answers with another pass —
        # a stale result is never stamped as the current baseline.
        with _LOCK:
            data0 = load()
            expect = sum(len(e.get("backups", []) or []) for e in data0.get("robots", []))
            # overlay bits the stream folds onto walked groups for DISPLAY
            # (the real merge does this authoritatively at the end)
            flags = {e["id"]: (bool(e.get("hidden")), bool(e.get("favorite")))
                     for e in data0.get("robots", []) if e.get("id")}
            favs = [e for e in data0.get("robots", [])
                    if e.get("favorite") and e.get("history_root")] if on_entry else []
        publish = None
        if on_entry is not None:
            for e in favs:                             # starred robots land first
                pv = _favorite_preview(e)
                if pv is not None:
                    on_entry(pv)

            def publish(g):
                g.pop("_absorbed", None)               # walk bookkeeping, not display
                h, f = flags.get(g.get("id") or "", (False, False))
                g["hidden"], g["favorite"] = h, f
                on_entry(g)
        scanned, empty_folders = _scan_disk(root, stats, progress=progress, expect=expect,
                                            on_entry=publish)
    with _LOCK:
        data = load()
        absorbed_raw: list = []
        if scanned is not None:
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


def _copy_tree_verified(src, dst, on_file=None) -> None:
    """Copy a folder tree: copy to a .__part sibling, verify, rename into
    place. The source is only ever READ. A crash mid-copy can only ever leave
    a .__part dir - never a partial tree at the destination's final name,
    which a later look could mistake for a complete snapshot (walkers skip
    .__part by name). `on_file(path, nbytes)` fires after each copied file
    and may raise to abort. Any failure removes the .__part and re-raises."""
    src, dst = Path(src), Path(dst)
    part = dst.with_name(dst.name + ".__part")
    if part.exists():
        shutil.rmtree(part, ignore_errors=True)    # stale leftover from a prior crash

    def _copy(s, d):
        shutil.copy2(s, d)
        if on_file:
            try:
                n = os.path.getsize(d)
            except OSError:
                n = 0
            on_file(s, n)                # may raise (cancel) - propagates out

    try:
        shutil.copytree(src, part, copy_function=_copy)
        if not _verify_tree(src, part):
            raise OSError(f"verify failed copying {src} -> {dst}")
        os.replace(part, dst)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise


def _zip_group_verified(members, dst, on_file=None, on_member=None) -> None:
    """Zip one or more source folders' CONTENTS into dst (…/<name>.zip,
    deflated): write to a .__part sibling, CRC-verify every entry, rename
    into place - the same crash contract as _copy_tree_verified (a died zip
    can only ever be a .__part file, never a complete-looking archive at the
    final name). members = [(src_dir, inner_rel)]: each source's tree lands
    under inner_rel inside the archive ("" = the archive root); a boundary
    above the snapshot level rolls several snapshots into one archive this
    way. Sources are only ever read. Empty dirs are stored too - the tree is
    evidence, all of it. `on_file(path, nbytes)` fires per archived file and
    may raise to abort; `on_member(i)` fires as each member finishes."""
    import zipfile
    dst = Path(dst)
    part = dst.with_name(dst.name + ".__part")
    if part.exists():
        try:
            part.unlink()                      # stale leftover from a prior crash
        except OSError:
            pass
    total = 0
    try:
        # strict_timestamps=False: plant files carry pre-1980 mtimes often
        # enough that a hard error would be the wrong trade - they clamp
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED,
                             strict_timestamps=False) as zf:
            for i, (src, irel) in enumerate(members):
                src = Path(src)
                prefix = (str(irel) + "/") if irel else ""
                for p in sorted(src.rglob("*")):
                    if p.is_file():
                        zf.write(p, prefix + p.relative_to(src).as_posix())
                        total += 1
                        if on_file:
                            try:
                                n = p.stat().st_size
                            except OSError:
                                n = 0
                            on_file(p, n)      # may raise (cancel) - propagates
                    elif p.is_dir():
                        try:
                            next(p.iterdir())
                        except StopIteration:
                            zf.write(p, prefix + p.relative_to(src).as_posix() + "/")
                            total += 1
                        except OSError:
                            pass
                if on_member:
                    on_member(i)
        with zipfile.ZipFile(part) as zf:
            if zf.testzip() is not None or len(zf.namelist()) != total:
                raise OSError(f"verify failed zipping into {dst}")
        os.replace(part, dst)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def _move_tree(src, dst) -> None:
    """Move a folder: atomic os.rename on the same volume, else the verified
    .__part copy (_copy_tree_verified), then delete the source. Raises OSError
    (source intact) if anything short of the final source delete fails."""
    src, dst = Path(src), Path(dst)
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass
    _copy_tree_verified(src, dst)
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
    e["last_backup"] = _when(newest) if newest else \
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
    if STAGED_NAME in (plant.lower(), line.lower(), robot.lower()):
        raise ValueError("'_staged' is reserved (it names the cleanup staging folder)")
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


# -- cleanup: retention verdicts + staging (moves, never deletes) -----------------

def retention_verdicts(robots: list, days: int, keep: int, now: str = "") -> list[dict]:
    """One cleanup verdict per snapshot, over already-indexed entries (pure -
    no disk I/O; stale flags and record fields are whatever the caller's
    listing says). Hidden robots are skipped; cameras follow the same rules.

      candidate - safe to stage by policy. group "partial" skips the age gate
                  entirely (a pull that died is junk from day one - Cody's
                  call: always listed, the UI shows its age); group
                  "superseded" means older than `days` with newer completed
                  backups shielding the robot.
      protected - reason ids (for COMPLETED snapshots recency is judged
                  FIRST, so latest/kept can only ever mark OLD snapshots -
                  without that, every robot's fresh latest would flood the
                  protected fold):
          pinned    the human said keep, forever
          offline   record is stale (folder unreachable - disconnected drive?)
          undated   no taken stamp - age unprovable, so never a candidate
          only      a robot's sole snapshot when it is a PARTIAL, any age
                    (a broken last trace); warn=True. A sole completed
                    snapshot reads recent/latest instead - truer, and it
                    keeps single-backup fleets out of the fold
          last      newest partial of a robot with NO completed backup (its
                    last trace, broken or not); warn=True
          recent    completed + newer than the cutoff (the UI omits these
                    from the fold; totals still count them)
          latest    newest completed snapshot AND old; warn=True always -
                    "old but latest - take a fresh backup first"
          kept      within the newest-`keep` completed snapshots (and old)

    The JS renders these verdicts and never re-derives them; stage_backups
    re-runs this same function and refuses anything not returned here as a
    candidate - the checkbox list is a request, not an authority."""
    days = max(0, int(days or 0))
    keep = max(0, int(keep or 0))
    if not now:
        now = _dt.datetime.now().isoformat(timespec="seconds")
    try:
        cutoff = (_dt.datetime.fromisoformat(now)
                  - _dt.timedelta(days=days)).isoformat(timespec="seconds")
    except ValueError:
        cutoff = ""
    out: list[dict] = []
    for e in robots or []:
        if e.get("hidden"):
            continue
        backups = e.get("backups") or []           # newest-first
        if not backups:
            continue
        pins = {p for p in (e.get("pins") or []) if p}
        completed = [b for b in backups if not b.get("partial")]
        latest_taken = completed[0].get("taken", "") if completed else ""
        keep_taken = {b.get("taken", "") for b in completed[:keep]}
        newest_taken = backups[0].get("taken", "")
        for b in backups:
            taken = b.get("taken", "")
            partial = bool(b.get("partial"))
            item = {
                "robot_id": e.get("id", ""), "plant": e.get("plant", ""),
                "line": e.get("line", ""), "robot": e.get("robot", ""),
                "device_type": e.get("device_type", "robot"),
                "taken": taken, "path": b.get("path", ""),
                "bytes": b.get("bytes", 0) or 0,
                "source": b.get("source", ""),
                "partial": partial, "pinned": taken in pins,
            }
            old = bool(cutoff) and bool(taken) and taken < cutoff
            if item["pinned"]:
                verdict, reason, warn = "protected", "pinned", False
            elif b.get("stale"):
                verdict, reason, warn = "protected", "offline", False
            elif not taken:
                verdict, reason, warn = "protected", "undated", False
            elif partial:
                # newest=True marks "the robot's most recent snapshot is this
                # dead pull" - its current state was never captured. The UI's
                # backup-broken button targets these, and auto-stage SKIPS
                # them: the partial IS the out-of-date evidence, and it stays
                # until a fresh completed backup replaces it.
                item["newest"] = taken == newest_taken
                if len(backups) == 1:
                    verdict, reason, warn = "protected", "only", True
                elif not completed and taken == newest_taken:
                    verdict, reason, warn = "protected", "last", True
                else:
                    verdict, reason, warn = "candidate", "", False
                    item["group"] = "partial"       # died pulls skip the age gate
            elif not old:
                verdict, reason, warn = "protected", "recent", False
            elif taken == latest_taken:
                verdict, reason, warn = "protected", "latest", True
            elif taken in keep_taken:
                verdict, reason, warn = "protected", "kept", False
            else:
                verdict, reason, warn = "candidate", "", False
                item["group"] = "superseded"
            item["verdict"] = verdict
            if reason:
                item["reason"] = reason
            if warn:
                item["warn"] = True
            out.append(item)
    return out


def staging_dest(root: str | Path) -> tuple[str, Path | None, str]:
    """Where staged snapshots go, from settings: ("library"|"folder"|"recycle",
    base-dir-or-None, error). Default is <root>/_staged. A custom folder must
    live OUTSIDE the library (the scan would re-adopt one inside - unless it
    resolves to the default itself). Recycle mode needs a volume that actually
    HAS a bin: on a network share or removable stick a "recycle" is a silent
    permanent delete, which this app never performs."""
    root = Path(root)
    mode = str(settings.get("staging_mode", "library") or "library")
    if mode == "recycle":
        if not _volume_recycles(root):
            return ("recycle", None,
                    "this library's drive has no recycle bin (network/removable) - "
                    "recycling would permanently delete; use a staging folder instead")
        return ("recycle", None, "")
    if mode == "folder":
        raw = str(settings.get("staging_dir", "") or "").strip()
        if not raw:
            return ("folder", None,
                    "no staging folder picked - choose one in settings/preferences")
        base = _safe_resolve(Path(raw))
        if base == _safe_resolve(root / STAGED_NAME):
            return ("library", root / STAGED_NAME, "")
        if _within(base, _safe_resolve(root)) or base == _safe_resolve(root):
            return ("folder", None,
                    "the staging folder cannot live inside the library "
                    "(the scan would re-adopt it) - pick somewhere else")
        return ("folder", Path(raw), "")
    return ("library", root / STAGED_NAME, "")


def _volume_recycles(root: Path) -> bool:
    """True when the path's volume is a local fixed drive - the only kind that
    reliably has a recycle bin. UNC shares and removable media do not, and
    SHFileOperation would hard-delete there despite FOF_ALLOWUNDO."""
    try:
        drive = os.path.splitdrive(str(_safe_resolve(root)))[0]
        if not drive or drive.startswith("\\\\"):
            return False
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 3  # DRIVE_FIXED
    except Exception:  # noqa: BLE001 - no ctypes/windll (non-Windows test run)
        return False


def _send_to_recycle(paths: list) -> None:
    """Send folders to the Windows Recycle Bin (SHFileOperationW +
    FOF_ALLOWUNDO). The bin keeps origin + date and offers Restore, and
    Windows' own storage policy handles expiry - that IS the point of this
    mode. Raises OSError on any failure (sources intact). NULs are built
    programmatically - never as literals through a tool layer."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]

    nul = chr(0)
    joined = nul.join(str(Path(p)) for p in paths) + nul   # ctypes adds the 2nd
    op = SHFILEOPSTRUCTW(None, 3,                          # FO_DELETE
                         joined, None,
                         0x40 | 0x10 | 0x4 | 0x400,        # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
                         False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted:
        raise OSError(f"recycle refused (shell code {rc})")


def staging_status(root: str | Path) -> dict:
    """What is parked in staging right now: {"mode", "present", "count",
    "bytes", "path"}. For folder-backed modes this is staged.log reconciled
    against the folder, so a hand-deleted staging dir (the whole point)
    honestly reads as empty again and a re-staged snapshot is never
    double-counted. Recycle mode claims no counts - the bin is the shell's
    ledger, not ours."""
    mode, base, err = staging_dest(root)
    if mode == "recycle" or base is None:
        return {"mode": mode, "present": False, "count": 0, "bytes": 0,
                "path": "", "error": err} if err else \
               {"mode": mode, "present": False, "count": 0, "bytes": 0, "path": ""}
    staged = base
    out = {"mode": mode, "present": False, "count": 0, "bytes": 0, "path": str(staged)}
    if not staged.is_dir():
        return out
    out["present"] = True
    log_file = staged / "staged.log"
    if not log_file.is_file():
        return out                 # a hand-made folder: claim nothing about it
    parked: dict[str, int] = {}    # rel -> bytes, last log line wins
    try:
        for ln in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue           # a torn line (crash mid-append) is not evidence
            rel = rec.get("rel", "")
            if rel:
                parked[rel] = int(rec.get("bytes", 0) or 0)
    except OSError:
        return out
    for rel, nbytes in parked.items():
        if (staged / rel).is_dir():
            out["count"] += 1
            out["bytes"] += nbytes
    return out


def stage_backups(picks: list, days: int, keep: int, progress=None) -> dict:
    """Move chosen snapshots into staging (see staging_dest: <root>/_staged by
    default, a custom outside-the-library folder, or the Windows Recycle Bin),
    mirrored plant/line/robot/date/time in the folder modes. MOVES only: the
    app never deletes backup data. A human empties staging in Explorer (or
    lets Windows expire the bin - that mode's whole point), and moving a
    folder back + a rescan restores it (files are law).

    Every pick is re-judged against retention_verdicts HERE — the UI's
    checkbox list is a request, not an authority; anything that is not a
    current candidate comes back in `failed`, untouched on disk. Per snapshot:
    _move_tree (atomic rename same-volume; verified copy otherwise, source
    intact on any failure) + a staged.log line; per touched robot one
    backups[]/latest rebuild + Latest-mirror regen. picks =
    [{"robot_id", "taken"}]. Returns {"staged", "failed", "staging"}.

    `progress(done, total)` fires per processed pick. Same-volume staging is
    an instant rename per snapshot; a cross-volume destination is a real
    copy-verify-delete of every file, which on a plant-scale batch takes
    MINUTES - callers must show this progress, never imply completion."""
    with _LOCK:
        data = load()
        root = _root()
        mode, staged_root, cfg_err = staging_dest(root)
        if cfg_err:
            raise ValueError(cfg_err)
        _reconcile(data)                     # verdicts lean on fresh stale flags
        allowed = {(it["robot_id"], it["taken"])
                   for it in retention_verdicts(data["robots"], days, keep)
                   if it["verdict"] == "candidate"}
        staged: list = []
        failed: list = []
        by_robot: dict[str, list[str]] = {}
        for p in picks or []:
            rid, taken = (p.get("robot_id") or ""), (p.get("taken") or "")
            if (rid, taken) not in allowed:
                failed.append({"robot_id": rid, "taken": taken,
                               "error": "not a current candidate (protected, "
                                        "unknown, or the tree changed)"})
            elif taken not in by_robot.setdefault(rid, []):
                by_robot[rid].append(taken)
        now = _dt.datetime.now().isoformat(timespec="seconds")
        total = len(failed) + sum(len(ts) for ts in by_robot.values())
        done = len(failed)                   # gate refusals count as processed
        if progress and total:
            progress(done, total)

        def _tick():
            nonlocal done
            done += 1
            if progress:
                progress(done, total)

        for rid, takens in by_robot.items():
            e = next((x for x in data["robots"] if x.get("id") == rid), None)
            robot_dir = _robot_folder(e) if e is not None else None
            if e is None or robot_dir is None:
                failed.extend({"robot_id": rid, "taken": t,
                               "error": "robot folder unknown"} for t in takens)
                for _ in takens:
                    _tick()
                continue
            moved = False
            for t in takens:
                b = next((x for x in e.get("backups") or []
                          if x.get("taken") == t), None)
                src = Path(b["path"]) if b and b.get("path") else None
                dst = None
                if src is None or not src.is_dir():
                    err = "snapshot folder missing"
                elif not _within(_safe_resolve(src), root):
                    err = "outside the library root"
                elif not _is_dated(src):
                    err = "a flat import (not a dated snapshot) - move it by hand"
                elif mode != "recycle":
                    err = ""
                    dst = (staged_root / e.get("plant", "") / e.get("line", "")
                           / e.get("robot", "") / src.parent.name / src.name)
                    if dst.exists():
                        err = "already parked in staging (restore or delete that copy first)"
                else:
                    err = ""
                if err:
                    failed.append({"robot_id": rid, "taken": t, "error": err})
                    _tick()
                    continue
                rec = {"when": now, "robot_id": rid, "plant": e.get("plant", ""),
                       "line": e.get("line", ""), "robot": e.get("robot", ""),
                       "taken": t, "bytes": b.get("bytes", 0) or 0}
                if not rec["bytes"]:
                    # sidecar-less imports have no recorded size - measure the
                    # folder itself before it moves, so staging status tells
                    # the truth (a batch of 1-file husks once read "0 B" and
                    # looked like the move had lost the data)
                    for _dp, _dn, _fn in os.walk(src):
                        for _n in _fn:
                            try:
                                rec["bytes"] += os.path.getsize(os.path.join(_dp, _n))
                            except OSError:
                                pass
                if mode == "recycle":
                    try:
                        _send_to_recycle([src])
                    except OSError as ex:
                        failed.append({"robot_id": rid, "taken": t, "error": str(ex)})
                        _tick()
                        continue
                    # the bin itself is the ledger here: origin + date +
                    # Restore live in the shell, so no staged.log is written
                    rec["rel"] = src.relative_to(root).as_posix()
                    rec["recycled"] = True
                else:
                    try:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _move_tree(src, dst)
                    except OSError as ex:
                        failed.append({"robot_id": rid, "taken": t, "error": str(ex)})
                        _tick()
                        continue
                    rec["rel"] = dst.relative_to(staged_root).as_posix()
                    try:
                        with open(staged_root / "staged.log", "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(rec) + "\n")
                    except OSError:
                        log.warning("staged.log append failed for %s (the move itself "
                                    "succeeded)", rec["rel"])
                # prune the emptied date dir; robot_dir is the boundary, so the
                # robot folder itself can never be swept up (structural, on top
                # of the latest/only/last verdict shields)
                _prune_empty_dirs(src.parent, robot_dir)
                staged.append(rec)
                moved = True
                _tick()
            if moved:
                _rebuild_backups(e, robot_dir, root)
                _regen_latest(e, root)
        if staged:
            _reconcile(data)
            _write(data)
        return {"staged": staged, "failed": failed, "staging": staging_status(root)}


# -- export: copy the newest N completed snapshots elsewhere --------------------
#
# Read-only over the library: exports COPY snapshot folders to a user-picked
# destination (USB stick, share, folder) laid out by a user-ordered template
# of path segments. Nothing in the library moves, changes, or is deleted, and
# the index is never touched - which is why none of this holds _LOCK during
# the copies.

EXPORT_SEGMENTS = ("plant", "line", "robot", "date", "time")


class ExportCancelled(Exception):
    """Raised out of the per-file hook to abort a copy mid-tree. Deliberately
    NOT an OSError: shutil.copytree collects OSErrors per-file and keeps
    going, and a cancel must stop the walk immediately."""


def _seg_value(e: dict, b: dict, seg) -> str:
    """One path component for a segment, or "" when this entry has nothing for
    it (the level is then skipped, same as a plant-less robot in the library
    tree). A custom segment ({"custom": name}) is that literal name for every
    row. date/time use the library's own folder naming (2026_08_01 /
    10_00_00) so a full plant/line/robot/date/time export IS a valid library
    tree a rescan can adopt."""
    if isinstance(seg, dict):
        return str(seg.get("custom", "") or "")
    if seg in ("plant", "line", "robot"):
        return str(e.get(seg, "") or "")
    taken = str(b.get("taken", "") or "")
    if seg == "date":
        return taken[:10].replace("-", "_") if len(taken) >= 10 else "undated"
    if seg == "time":
        return taken[11:19].replace(":", "_") if len(taken) >= 19 else "undated"
    return ""


# characters Windows refuses in a path component, plus the names it reserves
_CUSTOM_BAD_CHARS = set('<>:"/\\|?*')
_WIN_RESERVED = ({"con", "prn", "aux", "nul"}
                 | {f"com{i}" for i in range(1, 10)}
                 | {f"lpt{i}" for i in range(1, 10)})


def _check_segments(segments) -> list:
    """Normalize/validate a layout: known segment names (each at most once)
    and {"custom": <literal folder name>} entries, in caller order. Custom
    names must be usable Windows path components; customs MAY repeat - they
    are structure, not identity."""
    segs: list = []
    seen: set = set()
    for s in (segments or []):
        if isinstance(s, dict):
            name = str(s.get("custom", "")).strip()
            if not name:
                raise ValueError("a custom folder needs a name")
            if (any(c in _CUSTOM_BAD_CHARS or ord(c) < 32 for c in name)
                    or name in (".", "..") or name[-1] in (".", " ")
                    or name.lower() in _WIN_RESERVED or len(name) > 80):
                raise ValueError(f"not a usable folder name: {name!r}")
            segs.append({"custom": name})
            continue
        s = str(s)
        if s not in EXPORT_SEGMENTS:
            raise ValueError(f"unknown layout segment: {s}")
        if s in seen:
            raise ValueError("a layout segment repeats")
        seen.add(s)
        segs.append(s)
    return segs


def export_plan(robots: list, picks: list, count: int, segments: list,
                dest: str = "", days: int = 0, sidecar: bool = False,
                zip_at: int | None = None, now: str = "") -> dict:
    """What an export WOULD copy and where - the single source of truth the
    preview renders and export_backups re-runs at copy time (the UI's request
    is a request, never an authority). Pure over the passed entries except the
    dest existence probes (dest given -> each target is stat'ed so the
    preview can say "already there").

    Per picked robot: the newest `count` COMPLETED dated snapshots - within
    the last `days` days when days > 0 (undated records can't prove their age
    and drop out of a windowed export). Partials never export - a copy of a
    died pull handed to someone reads as a fresh complete backup, the exact
    lie this app exists to prevent. Flat imports (records whose path IS the
    robot folder) are skipped by name: copying one would drag the whole robot
    tree along.

    rows[].rel is the destination-relative target built from `segments` in
    order (empty values skip their level; customs are literal names). Plain
    mode (zip_at None): the snapshot's files land DIRECTLY inside the rel
    dir. Zipping: `zip_at` picks the folder level that BECOMES the archive -
    -1 = the leaf (one zip per backup), otherwise an index into `segments`;
    that level turns into `<name>.zip`, its parents stay real folders, and
    everything below it goes inside. Rows sharing the archive (a boundary
    above the snapshot level) are its members: rows[].arel is the archive's
    path, rows[].irel the member's path inside it. A row with nothing at the
    boundary (no such level, or an empty value there) is skipped, said
    plainly.

    rows[].sidecar_rel names where the robot's robot.json rides along ("" =
    it doesn't): only when `sidecar` is asked for AND the layout keeps a
    robot level below any plant/line as a REAL folder (customs and date/time
    under it are fine; a zip boundary at or above the robot level swallows
    the robot folder, so no ride-along).

    collisions: in plain mode, every rel that two rows share plus any rel
    that is a path prefix of another (the deeper copy would land INSIDE the
    shallower one). Zipping: archives are files, so nesting can't merge and
    the prefix rule stands down BETWEEN archives - instead, members of one
    archive collide when their inner paths are equal or nested (they would
    silently merge inside it). The caller refuses to export while any
    exist."""
    segs = _check_segments(segments)
    count = max(1, int(count or 1))
    days = max(0, int(days or 0))
    cutoff = ""
    if days:
        if not now:
            now = _dt.datetime.now().isoformat(timespec="seconds")
        try:
            cutoff = (_dt.datetime.fromisoformat(now)
                      - _dt.timedelta(days=days)).isoformat(timespec="seconds")
        except ValueError:
            cutoff = ""
    zip_on = zip_at is not None
    if zip_on:
        try:
            zip_at = int(zip_at)
        except (TypeError, ValueError):
            raise ValueError("the zip level is not in the layout")
        if zip_at != -1 and not (0 <= zip_at < len(segs)):
            raise ValueError("the zip level is not in the layout")
    by_id = {e.get("id"): e for e in robots or []}
    rows: list = []
    skipped: list = []
    known = [s for s in segs if not isinstance(s, dict)]
    # robot.json rides only when robot is the layout's deepest IDENTITY level
    # (customs/date/time below it keep the robot folder real; plant/line below
    # it would scatter identity) - and, zipping, only when the boundary sits
    # strictly BELOW the robot level: a boundary at or above it swallows the
    # robot folder, leaving nowhere for a sidecar to sit.
    sidecar_ok = (bool(sidecar) and "robot" in known
                  and all(s in ("date", "time")
                          for s in known[known.index("robot") + 1:]))
    if zip_on and sidecar_ok:
        bsi = len(segs) - 1 if zip_at == -1 else zip_at
        if segs.index("robot") >= bsi:
            sidecar_ok = False
    for rid in picks or []:
        e = by_id.get(rid)
        if e is None:
            skipped.append({"robot_id": rid, "robot": "", "reason": "not in the library"})
            continue
        ident = {"robot_id": rid, "robot": e.get("robot", "")}
        if e.get("hidden"):
            skipped.append({**ident, "reason": "hidden"})
            continue
        dated = [b for b in (e.get("backups") or [])          # newest-first
                 if not b.get("partial") and b.get("path") and _is_dated(Path(b["path"]))]
        if not dated:
            flat = any(not b.get("partial") for b in (e.get("backups") or []))
            skipped.append({**ident, "reason":
                            "flat import - copy its folder by hand" if flat
                            else "no completed backups"})
            continue
        if cutoff:
            eligible = [b for b in dated if (b.get("taken") or "") >= cutoff]
            if not eligible:
                skipped.append({**ident, "reason":
                                f"nothing completed in the last {days} days"})
                continue
        else:
            eligible = dated
        zip_skip = ""
        for b in eligible[:count]:
            parts: list[str] = []
            sidecar_rel = ""
            bpart = -1                         # part index the boundary landed on
            for i, s in enumerate(segs):
                v = _seg_value(e, b, s)
                if v:
                    parts.append(v)
                    if zip_on and i == zip_at:
                        bpart = len(parts) - 1
                if s == "robot" and sidecar_ok and v:
                    sidecar_rel = "/".join(parts)
            row = {
                "robot_id": rid, "plant": e.get("plant", ""),
                "line": e.get("line", ""), "robot": e.get("robot", ""),
                "taken": b.get("taken", ""), "path": b.get("path", ""),
                "bytes": b.get("bytes", 0) or 0, "files": b.get("files", 0) or 0,
                "rel": "/".join(parts), "sidecar_rel": sidecar_rel,
            }
            if zip_on:
                if zip_at == -1:
                    bpart = len(parts) - 1     # the leaf itself is the archive
                if bpart < 0:                  # no level, or an empty value there
                    if zip_at == -1:
                        zip_skip = "nothing to name the archive - add a folder level"
                    else:
                        s = segs[zip_at]
                        lab = s["custom"] if isinstance(s, dict) else s
                        zip_skip = f"nothing at the {lab} level to zip at"
                    continue
                row["arel"] = "/".join(parts[:bpart + 1])
                row["irel"] = "/".join(parts[bpart + 1:])
            rows.append(row)
        if zip_skip:
            skipped.append({**ident, "reason": zip_skip})
    rows.sort(key=lambda r: (r["plant"], r["line"], r["robot"], r["rel"]))

    if zip_on:
        # members of one archive collide when their inner paths are equal or
        # nested - they would silently merge INSIDE it. Between archives the
        # plain-mode prefix rule stands down: a/b.zip beside the a/b/ that
        # holds c.zip never merges.
        by_arel: dict[str, list] = {}
        for r in rows:
            by_arel.setdefault(r["arel"], []).append(r)
        collide = set()
        for arel, members in by_arel.items():
            irels = sorted(m["irel"] for m in members)
            for a, b in zip(irels, irels[1:]):
                if a == b or not a or b.startswith(a + "/"):
                    collide.add(arel)
                    break
            if arel in collide:
                for m in members:
                    m["collides"] = True
    else:
        by_rel: dict[str, int] = {}
        for r in rows:
            by_rel[r["rel"]] = by_rel.get(r["rel"], 0) + 1
        collide = {rel for rel, n in by_rel.items() if n > 1}
        rels = sorted(by_rel)
        for a, b in zip(rels, rels[1:]):
            # one target nested inside another: the deeper copy would land
            # INSIDE the shallower export (possible when a robot name equals
            # a plant's)
            if a and b.startswith(a + "/"):
                collide.update((a, b))
        for r in rows:
            if r["rel"] in collide:
                r["collides"] = True

    existing = 0
    if dest:
        base = Path(dest)
        for r in rows:
            try:
                if zip_on:
                    r["exists"] = (base / (r["arel"] + ".zip")).is_file()
                else:
                    # non-empty final dir = a prior export (or anything else)
                    # is already there; we will never write into it. rel ""
                    # means "straight into dest", which is legitimately
                    # non-empty - no honest exists-test exists for it, so it
                    # is never marked.
                    t = base / r["rel"] if r["rel"] else base
                    r["exists"] = bool(r["rel"]) and t.is_dir() and any(t.iterdir())
            except OSError:
                r["exists"] = False
            existing += 1 if r.get("exists") else 0

    totals = {"count": len(rows), "bytes": sum(r["bytes"] for r in rows),
              "unsized": sum(1 for r in rows if not r["bytes"]),
              "existing": existing}
    return {"rows": rows, "skipped": skipped,
            "collisions": sorted(collide), "totals": totals,
            "count": count, "segments": segs, "dest": dest,
            "days": days, "sidecar": bool(sidecar), "zip": zip_on,
            "zip_at": zip_at if zip_on else None}


def export_backups(picks: list, count: int, segments: list, dest: str,
                   days: int = 0, sidecar: bool = False, zip_at: int = None,
                   progress=None, cancel=None) -> dict:
    """Copy the planned snapshots to `dest`. The plan is re-derived HERE from
    the current index (export_plan, same days/sidecar/zip options) - and
    refused outright while any collision stands, because colliding copies
    would silently merge. Sources are only ever read; targets that already
    hold data are skipped, never overwritten.

    Per snapshot: a verified .__part copy - or, zipping, a CRC-verified
    .__part archive at the chosen boundary level (`zip_at`: -1 = one zip per
    backup, else the segment whose folder becomes the archive; a boundary
    above the snapshot level rolls that folder's snapshots into ONE archive)
    - renamed into place, so a crash or cancel can only leave a .__part at
    the destination, never a complete-looking half copy. After the
    snapshots, when `sidecar` was asked for, each exported robot's
    robot.json is copied beside its dated folders (best-effort - identity
    metadata, not evidence).

    `progress(done, total, bytes_done, current)` fires per snapshot and
    periodically during large copies. `cancel()` truthy stops before the next
    file; already-finished copies stay (they are complete and verified).
    Raises ValueError on a refused configuration; per-snapshot trouble comes
    back in `failed` instead."""
    if not dest:
        raise ValueError("no destination folder picked")
    base = _safe_resolve(Path(dest))
    root = _root()
    if base == root or _within(base, root):
        raise ValueError("the destination is inside the library - "
                         "the scan would re-adopt every exported copy")
    plan = export_plan(list_robots().get("robots") or [], picks, count, segments,
                       str(base), days=days, sidecar=sidecar, zip_at=zip_at)
    if plan["collisions"]:
        raise ValueError("two backups would land in the same folder - "
                         "add a date/time segment back, or export fewer")
    rows = plan["rows"]
    total = len(rows)
    exported: list = []
    failed: list = []
    skipped: list = [{"robot_id": s["robot_id"], "robot": s.get("robot", ""),
                      "taken": "", "reason": s["reason"]} for s in plan["skipped"]]
    done = 0
    copied_bytes = 0
    cancelled = False

    def _tick(current=""):
        if progress:
            progress(done, total, copied_bytes, current)

    _tick()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise ValueError(f"could not create the destination: {ex}")

    def _guard(target):
        if _within(_safe_resolve(target), root) or _safe_resolve(target) == root:
            return "target lands inside the library"       # belt: per-target too
        return ""

    if zip_at is not None:
        # one archive per distinct boundary path; its rows are the members
        groups: dict[str, list] = {}
        order: list[str] = []
        for r in rows:
            if r["arel"] not in groups:
                order.append(r["arel"])
            groups.setdefault(r["arel"], []).append(r)
        for arel in order:
            members = groups[arel]
            if cancelled or (cancel and cancel()):
                cancelled = True
                break
            if members[0].get("exists"):       # archive-level: all or none
                for r in members:
                    skipped.append({"robot_id": r["robot_id"], "robot": r["robot"],
                                    "taken": r["taken"], "reason": "already there"})
                done += len(members)
                _tick(arel)
                continue
            target = base / (arel + ".zip")
            err = ""
            if any(not Path(r["path"]).is_dir() for r in members):
                err = "snapshot folder missing"
            else:
                err = _guard(target)
            g_done = 0

            def _on_file(_p, n, _label=arel):
                nonlocal copied_bytes
                copied_bytes += n
                if cancel and cancel():
                    raise ExportCancelled()
                _tick(_label)

            def _member_done(i, _members=members):
                nonlocal done, g_done
                done += 1
                g_done += 1
                _tick((_members[i]["robot"] + " " + _members[i]["taken"]).strip())

            if not err:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _zip_group_verified(
                        [(Path(r["path"]), r.get("irel", "")) for r in members],
                        target, on_file=_on_file, on_member=_member_done)
                except ExportCancelled:
                    cancelled = True
                    break
                except OSError as ex:
                    err = str(ex)
            if err:
                for r in members:
                    failed.append({"robot_id": r["robot_id"], "robot": r["robot"],
                                   "taken": r["taken"], "error": err})
                done += len(members) - g_done
                _tick(arel)
                continue
            for r in members:
                exported.append({"robot_id": r["robot_id"], "robot": r["robot"],
                                 "taken": r["taken"], "rel": arel,
                                 "bytes": r["bytes"]})
            _tick(arel)
    else:
        for r in rows:
            label = (r["robot"] + " " + r["taken"]).strip()
            if cancelled or (cancel and cancel()):
                cancelled = True
                break
            if r.get("exists"):
                skipped.append({"robot_id": r["robot_id"], "robot": r["robot"],
                                "taken": r["taken"], "reason": "already there"})
                done += 1
                _tick(label)
                continue
            src = Path(r["path"])
            target = base / r["rel"] if r["rel"] else base
            err = ""
            if not src.is_dir():
                err = "snapshot folder missing"
            else:
                err = _guard(target)

            def _on_file(_p, n, _label=label):
                nonlocal copied_bytes
                copied_bytes += n
                if cancel and cancel():
                    raise ExportCancelled()
                _tick(_label)

            if not err:
                try:
                    if r["rel"]:
                        _copy_tree_verified(src, target, on_file=_on_file)
                    else:
                        # rel "": the snapshot's files go straight into dest,
                        # which already exists (and may hold unrelated things)
                        # - copy the CHILDREN, each through the same verified
                        # .__part path
                        for c in sorted(src.iterdir()):
                            t = base / c.name
                            if t.exists():
                                raise OSError(f"{c.name} already exists in the destination")
                            if c.is_dir():
                                _copy_tree_verified(c, t, on_file=_on_file)
                            else:
                                shutil.copy2(c, t)
                                _on_file(c, t.stat().st_size)
                except ExportCancelled:
                    cancelled = True
                    break
                except OSError as ex:
                    err = str(ex)
            if err:
                failed.append({"robot_id": r["robot_id"], "robot": r["robot"],
                               "taken": r["taken"], "error": err})
            else:
                exported.append({"robot_id": r["robot_id"], "robot": r["robot"],
                                 "taken": r["taken"], "rel": r["rel"],
                                 "bytes": r["bytes"]})
            done += 1
            _tick(label)

    # identity ride-along: one robot.json per exported robot level - the disk
    # copy when the robot folder has one (files are law), else materialized
    # from the entry, exactly what the library does for its own folders.
    # Never overwriting: an earlier export's copy is just as true. Best-effort
    # throughout - identity metadata, not evidence.
    seen: set = set()
    for r in rows:
        if not r["sidecar_rel"] or r["sidecar_rel"] in seen:
            continue
        if not any(x["robot_id"] == r["robot_id"] and x["taken"] == r["taken"]
                   for x in exported):
            continue
        seen.add(r["sidecar_rel"])
        e = get_robot(r["robot_id"])
        if e is None:
            continue
        src_dir = _robot_folder(e)
        sc = (src_dir / SIDECAR) if src_dir else None
        t = base / r["sidecar_rel"] / SIDECAR
        if t.exists():
            continue
        if sc is not None and sc.is_file():
            try:
                shutil.copy2(sc, t)
            except OSError:
                log.warning("could not copy %s to %s (the snapshots themselves "
                            "copied fine)", SIDECAR, t)
        else:
            _write_robot_sidecar(e, t.parent)      # logs for itself on failure
    return {"exported": exported, "failed": failed, "skipped": skipped,
            "cancelled": cancelled, "dest": str(base)}
