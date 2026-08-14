r"""Import existing backup trees into the library — the drop-import engine.

The library's supported "add an existing backup" path has always been: copy
the tree into the library folder with Explorer and the scan/watcher lists it
(files are law). This module is a UI for exactly that path, nothing more: a
dropped folder that lives OUTSIDE the library root is scanned for backup
roots, grouped into robots, and copied — file for file, source untouched —
into <root>\[plant\]<line>\<robot>\<YYYY_MM_DD>\<HH_MM_SS>, where the normal
rescan adopts it like any Explorer copy. No registry writes, no fabricated
sidecars, no Latest mirror (the scan derives latest from the dated snapshots).

Layer notes: everything here is UI-free and window-free so it unit-tests on
tmp trees; api.py owns the endpoints, threads, and progress dict. The copy
follows the app's crash-safety contract (stage to .__part, verify, os.replace
— library._move_tree's shape) but never deletes or rewrites a source folder:
_merge_into's "drop the redundant source copy" rule exists for moves INSIDE
the root and must not touch a user's dropped folder.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import shutil
from pathlib import Path

from . import ftpbackup, keyencebackup, library, mtxbackup, session

log = logging.getLogger(__name__)

_CAM_DIR_RE = re.compile(r"^CAM\d+$", re.IGNORECASE)

# device_type vocabulary is the library's (library._normalize): robot |
# camera-mtx | camera-keyence. backup.json only carries the key for cameras.
_DEVICE_TYPES = {"robot", "camera-mtx", "camera-keyence"}


def tree_size(root) -> tuple:
    r"""(file count, total bytes) under `root`, walked through the \\?\ prefix so
    a deep camera tree is measured rather than silently reported as empty (the
    MAX_PATH trap that once emptied the photos index). Best effort: an
    unreadable file is skipped, never raised - this only feeds a size label.
    (Promoted here from api.py so the import scan and the size labels share
    one walk.)"""
    files = total = 0
    try:
        for dirpath, _dirs, names in os.walk(ftpbackup.long_path(root)):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, n))
                    files += 1
                except OSError:
                    continue
    except OSError:
        pass
    return files, total


def _reserved_name(robot: str) -> bool:
    """A robot whose _safe_name'd folder the library walk would skip or
    misread: date/time-shaped names, the Latest mirror word, and the transient
    staging suffixes (library's _skip_name set). Importing one would create a
    folder the home screen can never show — refuse it honestly instead."""
    n = ftpbackup._safe_name(robot)
    return (n.lower() == "latest" or n.endswith((".__part", ".__tmp"))
            or bool(library._DATE_RE.match(n)) or bool(library._TIME_RE.match(n)))


def _stamp_from(snap: Path) -> tuple[str, str, bool]:
    """(YYYY_MM_DD, HH_MM_SS, synthesized) for a bare snapshot folder. A
    backup.json `taken` is real data; the folder's mtime is a stand-in and is
    LABELED as such all the way to the UI (synthesized=True) — nothing written
    to disk ever claims a time the backup didn't record."""
    meta = library._read_json(snap / "backup.json")
    if meta.get("taken"):
        try:
            when = _dt.datetime.fromisoformat(str(meta["taken"]))
            return when.strftime("%Y_%m_%d"), when.strftime("%H_%M_%S"), False
        except ValueError:
            pass
    try:
        when = _dt.datetime.fromtimestamp(snap.stat().st_mtime)
    except OSError:
        when = _dt.datetime(2000, 1, 1)
    return when.strftime("%Y_%m_%d"), when.strftime("%H_%M_%S"), True


def _device_type(snap: Path) -> str:
    """The library device_type read off a snapshot's shape. backup.json is
    authoritative when it names one (app-taken camera snapshots do); otherwise
    the same markers session.looks_like_backup trusts: cv-x/ (Keyence), the
    da/+Documents/ pair (Matrox), and a CAM<n>/ wrapper examined one level
    down (the app wraps each camera: CAM1/SD1/cv-x vs CAM1/da)."""
    claimed = library._read_json(snap / "backup.json").get("device_type", "")
    if claimed in _DEVICE_TYPES:
        return claimed

    def probe(d: Path) -> str:
        names = set()
        try:
            names = {p.name.lower() for p in d.iterdir() if p.is_dir()}
        except OSError:
            pass
        if "cv-x" in names or "sd1" in names:
            return "camera-keyence"
        if {"da", "documents"} <= names or ("da" in names and _CAM_DIR_RE.match(d.name)):
            return "camera-mtx"    # da/ alone counts only inside a CAM wrapper
        return ""

    dt = probe(snap)
    if dt:
        return dt
    try:
        cams = [p for p in snap.iterdir()
                if p.is_dir() and _CAM_DIR_RE.match(p.name)]
    except OSError:
        cams = []
    for cam in cams:
        dt = probe(cam)
        if dt:
            return dt
    if cams:
        return "camera-mtx"    # CAM wrapper with neither marker readable
    return "robot"


def _camera_name(snap: Path, device_type: str) -> str:
    """Best-effort real camera name out of a pulled snapshot (the offline
    naming both backup engines already do); blank when the tree doesn't say."""
    try:
        if device_type == "camera-mtx":
            return (mtxbackup.name_from_backup(snap) or {}).get("name", "") or ""
        if device_type == "camera-keyence":
            return (keyencebackup.name_from_backup(snap) or {}).get("name", "") or ""
    except Exception:  # noqa: BLE001 - naming must never sink a scan
        log.exception("camera naming failed for %s", snap)
    return ""


def _group_roots(roots: list) -> list:
    """Fold find_backup_roots' per-snapshot roots into per-robot groups.

    Three shapes: a dated <robot>/<date>/<time> root groups under its
    grandparent robot folder; a Latest/<robot> mirror child is a duplicate of
    that robot's newest dated snapshot and is DROPPED when the same robot name
    also grouped dated (kept as a single bare snapshot when the mirror is all
    the drop holds); any other root is its own robot with one bare snapshot.
    Returns [{dir, robot, snaps: [(src, date, time, synthesized)], bare}]."""
    groups: dict = {}          # key -> group; insertion order = sorted roots
    latest_only: dict = {}     # robot name (lower) -> group, resolved last

    def group_for(robot_dir: Path, robot: str, bare: bool) -> dict:
        key = os.path.normcase(str(robot_dir))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"dir": robot_dir, "robot": robot,
                               "snaps": [], "bare": bare}
        return g

    for r in sorted(set(roots)):
        r = Path(r)
        if library._is_dated(r):
            g = group_for(r.parent.parent, r.parent.parent.name, bare=False)
            g["snaps"].append((r, r.parent.name, r.name, False))
        elif r.parent.name.lower() == "latest":
            date, time_, synth = _stamp_from(r)
            latest_only[r.name.lower()] = {
                "dir": r, "robot": r.name,
                "snaps": [(r, date, time_, synth)], "bare": True}
        else:
            date, time_, synth = _stamp_from(r)
            g = group_for(r, r.name, bare=True)
            g["snaps"].append((r, date, time_, synth))

    named = {g["robot"].lower() for g in groups.values()}
    for name, g in latest_only.items():
        if name not in named:   # the mirror is all we have of this robot
            groups[os.path.normcase(str(g["dir"]))] = g
    for g in groups.values():
        g["snaps"].sort(key=lambda s: (s[1], s[2]))
    return list(groups.values())


def scan_paths(paths: list, library_root, existing: list | None = None) -> dict:
    """What did the user drop? -> {"drafts", "ignored", "in_library",
    "truncated", "notes"}. Pure read: nothing is copied or written here.

    `existing` is the library's current robots ([{plant, line, robot, id}]) so
    drafts can carry an honest "exists at ..." hint and flag a sidecar id the
    library already holds — passed in rather than read here so the scan tests
    on bare tmp trees."""
    root_r = library._safe_resolve(Path(library_root)) if library_root else None
    existing = existing or []
    by_name: dict = {}
    ids = set()
    for e in existing:
        n = (e.get("robot") or "").lower()
        if n and n not in by_name:
            by_name[n] = "/".join(p for p in (e.get("plant"), e.get("line")) if p)
        if e.get("id"):
            ids.add(e["id"])

    drafts: list = []
    ignored: list = []
    in_library: list = []
    truncated = False
    all_roots: list = []
    parents: set = set()

    for raw in dict.fromkeys(str(p) for p in (paths or [])):   # de-dup, keep order
        p = Path(raw)
        if not p.is_dir():
            ignored.append({"path": raw, "reason": "not a folder"})
            continue
        if root_r is not None:
            pr = library._safe_resolve(p)
            if pr == root_r or library._within(pr, root_r):
                in_library.append(raw)   # already listed - Explorer/scan owns it
                continue
        st: dict = {}
        roots = session.find_backup_roots(p, stats=st)
        truncated = truncated or bool(st.get("truncated"))
        if not roots:
            ignored.append({"path": raw, "reason": "no backups found inside"})
            continue
        parents.add(os.path.normcase(str(p)))
        all_roots.extend(roots)

    for g in _group_roots(all_roots):
        newest = g["snaps"][-1][0]
        dtype = _device_type(newest)
        name = g["robot"]
        if g["bare"]:
            name = library._read_json(newest / "backup.json").get("robot") or name
        if dtype != "robot":
            name = _camera_name(newest, dtype) or name
        files = bytes_ = 0
        for src, _d, _t, _s in g["snaps"]:
            f, b = tree_size(src)
            files += f
            bytes_ += b
        warnings: list = []
        if _reserved_name(name):
            warnings.append("reserved name - the library cannot show a robot "
                            "named like a date or a mirror; rename the folder first")
        if any(s[3] for s in g["snaps"]):
            warnings.append("date from folder timestamp")
        sidecar_id = library._read_json(g["dir"] / library.SIDECAR).get("id", "")
        if sidecar_id and sidecar_id in ids:
            warnings.append("sidecar id already in the library - robot.json "
                            "will not be copied")
        drafts.append({
            "src": str(g["dir"]), "robot": name, "device_type": dtype,
            "bare": g["bare"],   # robot folder IS the snapshot (no dated tree)
            "snapshots": [{"src": str(s), "date": d, "time": t, "synthesized": syn}
                          for s, d, t, syn in g["snaps"]],
            "files": files, "bytes": bytes_, "warnings": warnings,
            "exists_at": by_name.get(name.lower(), ""),
        })

    drafts.sort(key=lambda d: d["robot"].lower())
    notes: list = []
    if len(parents) > 1:
        notes.append("robots came from %d different folders - all will land "
                     "under one plant/line" % len(parents))
    return {"drafts": drafts, "ignored": ignored, "in_library": in_library,
            "truncated": truncated, "notes": notes}


def _copy_file(src: Path, dst: Path) -> int:
    """One file, mtime preserved, both sides through long_path. Returns bytes."""
    st = os.stat(ftpbackup.long_path(src))
    os.makedirs(ftpbackup.long_path(str(dst.parent)), exist_ok=True)
    shutil.copyfile(ftpbackup.long_path(src), ftpbackup.long_path(dst))
    os.utime(ftpbackup.long_path(dst), ns=(st.st_atime_ns, st.st_mtime_ns))
    return st.st_size


def _verify_copy(src: Path, dst: Path) -> bool:
    r"""Every file under src exists under dst at the same size — the post-copy
    sanity net, same bar as library._verify_tree's non-strict mode but walked
    through the \\?\ prefix on BOTH sides: an import's destination is exactly
    the deep-camera-tree shape that blows past MAX_PATH, and a verify that
    can't stat the file it just wrote would fail an intact copy."""
    lsrc = ftpbackup.long_path(src)
    try:
        for dirpath, _dirs, names in os.walk(lsrc):
            rel = os.path.relpath(dirpath, lsrc)
            for n in names:
                t = os.path.join(ftpbackup.long_path(dst), "" if rel == "." else rel, n)
                try:
                    if os.path.getsize(t) != os.path.getsize(os.path.join(dirpath, n)):
                        return False
                except OSError:
                    return False
    except OSError:
        return False
    return True


def copy_snapshot(src: Path, dst: Path, *, progress=None, cancel=None) -> dict:
    """Copy one snapshot folder to its final dated home. -> {"status":
    "copied" | "duplicate" | "conflict" | "cancelled" | "error", ...}.

    An existing destination is never touched: verified-identical (both trees
    byte-compare, backup.json stats agree - _merge_into's bars minus the
    both-sidecars-readable gate, which only exists there to protect a source
    DELETE this import never performs) reports "duplicate"; anything else
    reports "conflict". A fresh copy stages into <time>.__part - the reserved
    transient suffix every walk skips - verifies, then os.replace()s into
    place, so a crash can never leave a half tree at a name the scan trusts.
    `progress(byte_delta)` ticks once per file copied; `cancel.is_set()`
    between files abandons the staging dir (removed) without touching
    snapshots already landed."""
    src, dst = Path(src), Path(dst)
    if os.path.exists(ftpbackup.long_path(dst)):
        s = library._stat_pair(library._read_json(src / "backup.json"))
        d = library._stat_pair(library._read_json(dst / "backup.json"))
        # _verify_tree walks plain paths; on a tree deep enough to trip
        # MAX_PATH it reads False and the snapshot reports as a conflict -
        # degraded honesty (a dupe flagged for a human) but never destructive.
        if s == d and library._verify_tree(src, dst, strict=True):
            return {"status": "duplicate"}
        return {"status": "conflict", "src": library._statd(s), "dst": library._statd(d)}
    part = dst.with_name(dst.name + ".__part")
    if os.path.exists(ftpbackup.long_path(part)):
        shutil.rmtree(ftpbackup.long_path(part), ignore_errors=True)
    files = bytes_ = 0
    try:
        os.makedirs(ftpbackup.long_path(str(part)), exist_ok=True)
        for dirpath, _dirs, names in os.walk(ftpbackup.long_path(src)):
            rel = os.path.relpath(dirpath, ftpbackup.long_path(src))
            for n in sorted(names):
                if cancel is not None and cancel.is_set():
                    shutil.rmtree(ftpbackup.long_path(part), ignore_errors=True)
                    return {"status": "cancelled", "files": files, "bytes": bytes_}
                target = part if rel == "." else part / rel
                sz = _copy_file(Path(dirpath) / n, target / n)
                bytes_ += sz
                files += 1
                if progress is not None:
                    progress(sz)          # per-file delta - callers just add
        if not _verify_copy(src, part):
            raise OSError(f"verify failed copying {src} -> {dst}")
        os.replace(ftpbackup.long_path(part), ftpbackup.long_path(dst))
    except OSError as ex:
        shutil.rmtree(ftpbackup.long_path(part), ignore_errors=True)
        return {"status": "error", "error": f"{ex}", "files": files, "bytes": bytes_}
    return {"status": "copied", "files": files, "bytes": bytes_}


def run_import(drafts: list, plant: str, line: str, root, *,
               progress=None, cancel=None, existing_ids=None) -> dict:
    """Copy the chosen drafts under <root>/[plant/]<line>/. -> {"results":
    [...], "cancelled": bool} with one result per draft: {"robot", "dest",
    "status": imported|merged|refused|cancelled|error, copied, duplicates,
    conflicts, errors}.

    Reserved names are re-refused here (scan warnings travel through the UI
    and back - the engine re-checks its own law). A dest robot folder that
    already exists means this import MERGES into that robot (folder position
    IS identity); its snapshots land beside the existing ones under the same
    duplicate/conflict rules. The source robot.json travels with a shape-a
    robot when the dest has none and the library doesn't already own its id
    (a duplicated id would fold two folders into one entry at the next scan).
    `progress(event)` fires per robot ({"robot", "dest"}) and per file
    ({"bytes": byte_delta})."""
    root = Path(root)
    existing_ids = set(existing_ids or [])
    results: list = []
    cancelled = False
    for draft in drafts or []:
        robot = (draft.get("robot") or "").strip()
        src_dir = Path(draft.get("src") or "")
        res = {"robot": robot, "dest": "", "status": "error", "copied": 0,
               "duplicates": 0, "conflicts": 0, "errors": []}
        results.append(res)
        if cancel is not None and cancel.is_set():
            res["status"] = "cancelled"
            cancelled = True
            continue
        if not robot or not src_dir.is_dir():
            res["errors"].append("dropped folder is gone")
            continue
        if _reserved_name(robot):
            res["status"] = "refused"
            res["errors"].append("reserved name")
            continue
        dest = library._robot_dir_for(root, plant, line, robot)
        res["dest"] = str(dest)
        merged = dest.is_dir()
        if progress is not None:
            progress({"robot": robot, "dest": str(dest)})
        for snap in draft.get("snapshots") or []:
            snap_src = Path(snap.get("src") or "")
            date, time_ = snap.get("date") or "", snap.get("time") or ""
            if not (snap_src.is_dir() and library._DATE_RE.match(date)
                    and library._TIME_RE.match(time_)):
                res["errors"].append("bad snapshot spec: %s" % snap_src)
                continue
            out = copy_snapshot(
                snap_src, dest / date / time_,
                progress=(lambda b: progress({"bytes": b})) if progress else None,
                cancel=cancel)
            if out["status"] == "copied":
                res["copied"] += 1
            elif out["status"] == "duplicate":
                res["duplicates"] += 1
            elif out["status"] == "conflict":
                res["conflicts"] += 1
            elif out["status"] == "cancelled":
                cancelled = True
                break
            else:
                res["errors"].append(out.get("error", "copy failed"))
        if cancelled and res["copied"] == 0 and not res["errors"]:
            res["status"] = "cancelled"
            continue
        # the source's own sidecar travels with the robot (portable identity),
        # never overwriting one the dest folder already owns, never duplicating
        # an id the library holds
        side = src_dir / library.SIDECAR
        if (not draft.get("bare") and side.is_file()
                and not (dest / library.SIDECAR).exists()):
            sid = library._read_json(side).get("id", "")
            if not (sid and sid in existing_ids):
                try:
                    _copy_file(side, dest / library.SIDECAR)
                except OSError:
                    res["errors"].append("robot.json could not be copied")
        if res["errors"]:
            res["status"] = "error"
        elif merged:
            res["status"] = "merged"
        else:
            res["status"] = "imported"
    return {"results": results, "cancelled": cancelled}
