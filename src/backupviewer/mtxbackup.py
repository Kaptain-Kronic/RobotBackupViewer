"""Matrox (MTX) camera backup taker: pull a Matrox Design Assistant camera's
settings + latest inspection images off the camera over SMB (Windows file share).

A Matrox smart camera runs embedded Linux with a **Samba** server (confirmed live
2026-07-14: `\\\\<ip>\\mtxuser` is the MTXuser home share). It does NOT expose FTP
(port 21 closed) or accept the DA login over SSH - the way the shop reaches it is
exactly what a tech does in Explorer: `\\\\<ip>`, then the mtxuser / Matrox
credentials (both case-sensitive - see MTX_USER/MTX_PASS below). The `mtxuser` share is `/home/MTXuser`, which holds:

  da/                                    the Design Assistant project data (the
                                         "DA folder" - Projects, Calibrations,
                                         DCFs, Protocols, AgentSettings, Web, ...)
  Documents/Matrox Design Assistant/
      SavedImages/<YYYY-MM-DD>/          runtime inspection photos (jpg+png+txt
                                         triples), one dated folder per day

Backup scope (confirmed with the owner): the WHOLE `da/` tree plus the newest N
photos from SavedImages (N = the `mtx_photos` setting, default 25) - newest date
folder first, then back through the older ones until the count is met. Only the
newest day's photos come whole (jpg + png + txt); older ones come as jpg + txt,
since the png is the same 1920x1200 frame at ~2.3 MB against the jpeg's ~213 KB
and the photos tab renders either. Small, fast snapshots that carry a RUN of
recent photos rather than the one the camera happened to be holding.

A re-run whose `da/` tree is unchanged does not stack a second near-identical
snapshot: its new photos are folded into the snapshot they match, which records
the top-up as `updated` + `topups` in its own backup.json (see _settle). That is
the single place this app writes into an existing backup folder, it only ever
ADDS the camera's own new evidence, and it never rewrites a byte of what was
already pulled - the ruling is recorded in docs/subsystems/backup-capture.md.

Because SMB makes the share a normal filesystem path once authenticated, this is
the simplest transport in the app: authenticate with WNetAddConnection2, then
walk + `shutil.copy2` UNC paths - no protocol client, no extra dependency (SMB is
native to Windows, the only platform the app ships on). Crash-safety matches the
FTP engine (copy to `<name>.part`, then atomic rename) and it reuses ftpbackup's
path rules (dated_dir / latest_dir / mirror_latest) and BackupJob's public shape
(uuid id, lock-guarded progress, snapshot()/cancel()/run()).

A station can carry several cameras: the job takes a `cameras` list [{label,host}]
and pulls each - sequentially - into its own CAM<label> subfolder of one snapshot.

The SMB `mount` is injected (default `smb_mount`) so the whole enumerate+copy flow
is testable offline against a local temp dir (see tests/test_mtxbackup.py) - live
testing is never the first validation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from . import ftpbackup

log = logging.getLogger(__name__)

# Default credentials burned into every Matrox DA camera on the shop floor, and
# the Samba share that maps to the camera's home dir. BOTH are case-sensitive - the
# camera runs Linux/Samba, which compares them exactly:
#   * user `mtxuser` all-lowercase (what a tech types in Explorer) - NOT "MTXuser":
#     mixed case is rejected on a programmatic login.
#   * pass `Matrox` Title-case - NOT "MATROX" all-caps. Live-verified 2026-07-20
#     against two cameras: `Matrox` authenticates, `MATROX`/`matrox` are refused
#     server-side (STATUS_LOGON_FAILURE). The old all-caps default is why a FIRST
#     backup always failed - the only "working" pulls were riding an Explorer
#     session a tech had opened by hand with the correctly-cased password.
MTX_USER = "mtxuser"
MTX_PASS = "Matrox"
MTX_SHARE = "mtxuser"

# SavedImages path under the share, as path segments (contains a space).
IMAGES_PARTS = ("Documents", "Matrox Design Assistant", "SavedImages")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")     # SavedImages/<YYYY-MM-DD>
BACKUP_TYPE = "matrox da + recent images"

# How many photos a pull carries back. The default a site never changes; the
# `mtx_photos` setting is what the settings screen writes (api hands it to the
# job). A photo is a triple, so this is a count of INSPECTIONS, not of files.
MAX_PHOTOS = 25


# -- SMB session (native Windows, via mpr.dll) -----------------------------------

_RESOURCETYPE_DISK = 0x00000001
_CONNECT_TEMPORARY = 0x00000004
_ERROR_BAD_NET_NAME = 67               # name won't resolve (often a bad user qualifier)
_ERROR_ALREADY_ASSIGNED = 85           # this exact share already mapped (same creds)
_ERROR_INVALID_PASSWORD = 86           # a conflicting session already used other creds
_ERROR_LOGON_FAILURE = 1326            # bad username/password
_ERROR_SESSION_CREDENTIAL_CONFLICT = 1219   # one credential set per server (Windows rule)


def _unc(host: str) -> str:
    return "\\\\" + host + "\\" + MTX_SHARE


def _wnet_add(unc: str, user: str, passwd: str) -> int:
    """WNetAddConnection2 with explicit creds (passed in-process, never on a
    command line). Returns the raw Windows error code (0 = success)."""
    import ctypes
    from ctypes import wintypes

    class _NETRESOURCE(ctypes.Structure):
        _fields_ = [
            ("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
            ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
            ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
            ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR),
        ]

    nr = _NETRESOURCE()
    nr.dwType = _RESOURCETYPE_DISK
    nr.lpRemoteName = unc
    return ctypes.windll.mpr.WNetAddConnection2W(
        ctypes.byref(nr), passwd, user, _CONNECT_TEMPORARY)


def _wnet_cancel(name: str) -> None:
    import ctypes
    try:
        ctypes.windll.mpr.WNetCancelConnection2W(name, 0, True)
    except Exception:  # noqa: BLE001 - teardown is best-effort
        pass


_CRED_TYPE_DOMAIN_PASSWORD = 2
_CRED_PERSIST_SESSION = 1                  # cleared on logoff - password never hits disk


def _cred_write(host: str, user: str, passwd: str) -> bool:
    """Stage an SMB credential for `host` in Windows Credential Manager (session-
    only) - exactly what Explorer's "remember my credentials" does - so a later
    connect with NO explicit creds authenticates as `user`. This is the reliable
    way to make a programmatic login behave like the tech's interactive one.
    Best-effort; returns True if it was stored."""
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    try:
        blob = (passwd or "").encode("utf-16-le")
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = _CREDENTIAL()
        cred.Type = _CRED_TYPE_DOMAIN_PASSWORD
        cred.TargetName = host
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        cred.Persist = _CRED_PERSIST_SESSION
        cred.UserName = user
        return bool(ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0))
    except Exception:  # noqa: BLE001 - staging is best-effort
        return False


def _cred_delete(host: str) -> None:
    import ctypes
    try:
        ctypes.windll.advapi32.CredDeleteW(host, _CRED_TYPE_DOMAIN_PASSWORD, 0)
    except Exception:  # noqa: BLE001
        pass


def _smb_connect(unc: str, user: str, passwd: str) -> tuple[bool, bool]:
    """Authenticate the SMB share as `user`. Returns (created, staged): created is
    True if WE mapped the share (caller must disconnect it); staged is True if we
    wrote a session credential (caller must delete it when done - a camera
    password must never outlive the pull). Raises OSError with tech-facing
    guidance if every strategy fails.

    The camera's Samba is fussy about programmatic logins in ways Explorer's own
    prompt isn't, so this tries, in order: (1) plain mtxuser/Matrox; (2) the user
    qualified with the server (`<ip>\\mtxuser`) - a WORKGROUP PC otherwise sends
    its OWN name as the domain, which the camera rejects with WinError 86/1326;
    (3) NO creds, to ride an existing server session (the tech's Explorer login -
    Windows allows only one credential set per server); (4) clear a stale/
    conflicting session and retry plain."""
    server = unc.rsplit("\\", 1)[0]               # \\host  from  \\host\share
    host = server.lstrip("\\")
    _RETRYABLE = (_ERROR_INVALID_PASSWORD, _ERROR_LOGON_FAILURE,
                  _ERROR_SESSION_CREDENTIAL_CONFLICT, _ERROR_BAD_NET_NAME)

    # 0. stage the credential (Explorer's "remember me") then connect with NO
    #    explicit creds - the redirector uses the staged/any existing session.
    #    This is the automation that lets a fresh camera back up without the tech
    #    opening it in Explorer first. smb_mount deletes it once the pull is done.
    staged = _cred_write(host, user, passwd)
    rc = _wnet_add(unc, None, None)
    if rc in (0, _ERROR_ALREADY_ASSIGNED):
        return rc == 0, staged

    rc = _wnet_add(unc, user, passwd)                       # 1. explicit plain creds
    if rc == 0:
        return True, staged
    if rc == _ERROR_ALREADY_ASSIGNED:
        return False, staged
    if rc in _RETRYABLE:
        rc = _wnet_add(unc, host + "\\" + user, passwd)     # 2. server-qualified user
        if rc == 0:
            return True, staged
        if rc == _ERROR_ALREADY_ASSIGNED:
            return False, staged
    _wnet_cancel(unc)                                       # 3. clear + retry plain
    _wnet_cancel(server + "\\IPC$")
    _wnet_cancel(server)
    rc = _wnet_add(unc, user, passwd)
    if rc == 0:
        return True, staged
    if rc == _ERROR_ALREADY_ASSIGNED:
        return False, staged
    if staged:                                             # bad cred -> don't leave it staged
        _cred_delete(host)
    raise OSError(
        f"SMB login to {unc} was refused (WinError {rc}). Open \\\\{host} in File "
        f"Explorer and sign in as {user} first, then retry the backup.")


def _smb_disconnect(unc: str) -> None:
    _wnet_cancel(unc)


def smb_mount(host: str, user: str, passwd: str):
    """Default mount: return (base_path, cleanup) for `\\\\host\\mtxuser`.

    Ride an existing session first - if the share is already reachable (the tech
    signed into the camera in Explorer, the proven-working path), use it and touch
    nothing. Only authenticate ourselves when it isn't, and clear any stale
    conflicting session inside _smb_connect. Runs in the app's interactive desktop
    session, the same context where an Explorer login to the camera succeeds.

    cleanup disconnects any share WE mapped and deletes any credential WE staged,
    so the camera password lives only for the pull - never persisted past it."""
    unc = _unc(host)
    base = Path(unc)
    try:
        if (base / "da").exists() or (base / "Documents").exists():
            return base, lambda: None            # ride the existing session, touch nothing
    except OSError:
        pass
    created, staged = _smb_connect(unc, user, passwd)

    def _cleanup():
        if created:
            _smb_disconnect(unc)
        if staged:
            _cred_delete(host)

    return base, _cleanup


# -- enumerate + copy ------------------------------------------------------------

def _mtime(p: Path) -> int:
    """A file's mtime, 0 when it can't be read - the tie-break under a filename
    stamp, never a reason to fail."""
    try:
        return int(os.stat(ftpbackup.long_path(p)).st_mtime)
    except OSError:
        return 0


def _shot_name(group: dict) -> str:
    """The one file that IS the photo: the jpg when the camera wrote one, else
    the png. "" for a group that is only a sidecar."""
    return group.get("jpg") or group.get("png") or ""


def _photo_files(si: Path, limit: int) -> list[Path]:
    """The newest `limit` photos under a SavedImages folder, newest first.

    Walks the `<YYYY-MM-DD>` date folders newest-first and stops the moment the
    count is met, so a camera holding a year of images is listed for a handful of
    days, not walked whole. Within a day the camera's own filename timestamp
    orders the shots (mtime breaks ties).

    The newest day yields whole triples; every older photo yields its jpg + txt
    and leaves the png behind - same 1920x1200 frame, a tenth of the bytes. A
    photo with no jpg falls back to its png rather than vanishing: a listed photo
    the snapshot cannot show would be the dishonest kind of small."""
    from .parsers import mtx_saved_image      # the triple grouping + sort key

    if limit <= 0 or not si.is_dir():
        return []
    try:
        dates = sorted((d for d in si.iterdir() if d.is_dir() and _DATE_RE.match(d.name)),
                       key=lambda d: d.name, reverse=True)
    except OSError:
        return []

    out: list[Path] = []
    taken = 0
    for rank, day in enumerate(dates):
        if taken >= limit:
            break
        try:                       # scandir's stat comes free with the listing
            with os.scandir(day) as it:
                stamped = {e.name: int(e.stat().st_mtime) for e in it if e.is_file()}
        except OSError:            # a day folder that vanished mid-listing
            continue
        groups = mtx_saved_image.group_photo_files(list(stamped))
        order = sorted(groups, key=lambda s: mtx_saved_image.photo_sort_key(
            s, stamped.get(_shot_name(groups[s]), 0)), reverse=True)
        for stem in order:
            if taken >= limit:
                break
            g = groups[stem]
            shot = _shot_name(g)
            if not shot:
                continue           # a stray sidecar with no image is not a photo
            taken += 1
            wanted = (g.get("jpg"), g.get("png"), g.get("txt")) if rank == 0 \
                else (shot, g.get("txt"))
            out.extend(day / n for n in wanted if n)
    return out


def _enumerate_files(base: Path, max_photos: int = MAX_PHOTOS) -> list[Path]:
    """Absolute paths (under `base`) to pull: the whole `da/` tree + the newest
    `max_photos` photos out of SavedImages (see _photo_files). `base` is a
    mounted share, walked as a normal filesystem. Photos come newest-first, so a
    pull that is cancelled or dies partway still got the most useful ones."""
    out: list[Path] = []
    da = base / "da"
    if da.is_dir():
        out.extend(p for p in sorted(da.rglob("*")) if p.is_file())
    out.extend(_photo_files(base.joinpath(*IMAGES_PARTS), max_photos))
    return out


def _copy_file(src: Path, dest: Path, *, retries: int = 2) -> int:
    r"""Copy one file with the .part-then-rename protocol (a crash never leaves a
    half-file that looks complete). Bounded retries for a flaky share. The DEST
    (a deep local library tree) uses the \\?\ extended-length path so it never
    trips the 260-char MAX_PATH - the halfway-through 'cannot find the path'
    failure. The source share path is shallow, so it's left as-is. Returns bytes."""
    os.makedirs(ftpbackup.long_path(dest.parent), exist_ok=True)
    part_l = ftpbackup.long_path(dest.with_name(dest.name + ".part"))
    dest_l = ftpbackup.long_path(dest)
    last = None
    for attempt in range(retries + 1):
        try:
            shutil.copy2(str(src), part_l)   # src share path is short; dest is \\?\
            os.replace(part_l, dest_l)
            return os.stat(dest_l).st_size
        except OSError as e:
            last = e
            if os.path.exists(part_l):
                try:
                    os.remove(part_l)
                except OSError:
                    pass
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
    raise last if last else OSError("copy failed: " + str(src))


# -- topping up a snapshot instead of stacking a near-twin ------------------------
# A camera's da/ tree barely ever changes; its SavedImages roll forward every
# shift. Backing one up twice in a day therefore used to leave two ~360-file
# folders differing by a single photo. So: pull exactly as before, then, if what
# came back matches the camera's newest snapshot EVERYWHERE except the photos,
# fold the new photos into that snapshot and drop the folder this run made.
#
# This is the one place the app writes inside an existing backup folder, so the
# rules are narrow: it only ADDS files the camera itself produced, it never
# rewrites or removes one, the snapshot keeps its original `taken` and records the
# visit as `updated` + `topups`, and the fold happens only after this run has
# already landed as a complete snapshot of its own - a crash mid-fold leaves two
# honest folders, never a hole.

SIDECARS = ("backup.json", "notes.txt")
_DAY_RE = re.compile(r"^\d{4}_\d{2}_\d{2}$")      # <robot>/<YYYY_MM_DD>/
_TIME_RE = re.compile(r"^\d{2}_\d{2}_\d{2}$")     #                     <HH_MM_SS>


def _is_photo(rel: str) -> bool:
    """True for a pulled rel-path sitting in a `SavedImages/<date>/` folder."""
    parts = rel.split("/")
    return (len(parts) >= 3 and parts[-3] == IMAGES_PARTS[-1]
            and bool(_DATE_RE.match(parts[-2])))


def _walk_rels(root: Path):
    """(rel posix path, full long path) for every file in a snapshot, its
    sidecars and any half-written `.part` aside. One walker, so "what is in this
    snapshot" has a single answer. Long-path safe: a library tree plus a Matrox
    SavedImages filename goes past MAX_PATH on its own."""
    top = ftpbackup.long_path(root)
    for dirpath, _dirs, names in os.walk(top):
        for n in names:
            if n.endswith(".part"):
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, top).replace(os.sep, "/")
            if rel not in SIDECARS:
                yield rel, full


def _tree_sig(root: Path) -> dict:
    """{rel-path: size} for a snapshot minus its photos - the part that must match
    for two pulls to be the same snapshot holding different photos. An unreadable
    file is -1: it is not absent, and it matches no real size either."""
    out: dict[str, int] = {}
    for rel, full in _walk_rels(root):
        if _is_photo(rel):
            continue
        try:
            out[rel] = os.stat(full).st_size
        except OSError:
            out[rel] = -1
    return out


def _fold_photos(dated: Path, prev: Path) -> tuple[int, int]:
    """Copy the photos `dated` holds and `prev` does not into `prev`, oldest
    first. Same rel-path = the same inspection (the camera stamps the filename to
    the millisecond), so a photo already there is never rewritten. Returns
    (files, bytes) added."""
    have = {rel for rel, _full in _walk_rels(prev)}
    added = nbytes = 0
    for rel, full in sorted(_walk_rels(dated)):
        if not _is_photo(rel) or rel in have:
            continue
        nbytes += _copy_file(Path(full), prev.joinpath(*rel.split("/")))
        added += 1
    return added, nbytes


def _retop_meta(prev: Path, when, skipped=None) -> dict:
    """Rewrite `prev`'s backup.json for a top-up: the original `taken` stands,
    `updated` + `topups` record the visit, and files/bytes are RECOUNTED off the
    folder rather than added up - files are law, and a recount also heals a count
    that had drifted. Skips seen this run join the snapshot's own list. Returns
    the meta as written, which is also the library's row for that folder."""
    try:
        meta = json.loads((prev / "backup.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    files = nbytes = 0
    for _rel, full in _walk_rels(prev):
        files += 1
        try:
            nbytes += os.stat(full).st_size
        except OSError:
            pass
    meta["files"] = files
    meta["bytes"] = nbytes
    meta["updated"] = when.isoformat(timespec="seconds")
    meta["topups"] = int(meta.get("topups") or 0) + 1
    if skipped:
        meta["skipped"] = sorted(set(meta.get("skipped") or []) | set(skipped))
    tmp = prev / "backup.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(prev / "backup.json")
    return meta


def _complete_snapshot(d: Path) -> bool:
    """A snapshot whose marker says the pull FINISHED. A partial (or unmarked)
    folder is never topped up: adding today's photos to a tree that is missing
    files would dress a half-backup as a fuller one."""
    try:
        meta = json.loads((d / "backup.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("complete") is True


def _previous_snapshot(dated: Path) -> Path | None:
    """The newest complete snapshot of this same device other than `dated`.
    Layout is `<robot>/<YYYY_MM_DD>/<HH_MM_SS>`, so name order IS time order."""
    robot = dated.parent.parent
    try:
        days = sorted((d for d in robot.iterdir() if d.is_dir() and _DAY_RE.match(d.name)),
                      key=lambda p: p.name, reverse=True)
    except OSError:
        return None
    for day in days:
        try:
            times = sorted((t for t in day.iterdir()
                            if t.is_dir() and _TIME_RE.match(t.name)),
                           key=lambda p: p.name, reverse=True)
        except OSError:
            continue
        for t in times:
            if t != dated and _complete_snapshot(t):
                return t
    return None


def _discard(dated: Path) -> None:
    """Remove the folder this run pulled into, once its photos are safe inside the
    snapshot they belong to. Only ever the directory THIS job just created, and
    only after the fold succeeded; the `<YYYY_MM_DD>` parent goes too if the run
    was the only thing in it."""
    shutil.rmtree(ftpbackup.long_path(dated), ignore_errors=True)
    try:
        os.rmdir(ftpbackup.long_path(dated.parent))
    except OSError:
        pass                        # the day holds other snapshots - leave it


# -- the job ---------------------------------------------------------------------

class CameraBackupJob(ftpbackup.CameraJobBase):
    """One Matrox-camera-station backup run. Construct, then .run() on a worker
    thread; poll .snapshot() for live progress; .cancel() requests a graceful stop
    between files. The state machine, crash-safety and library record live in
    ftpbackup.CameraJobBase; this class is the SMB transport."""

    DEVICE_TYPE = "camera-mtx"
    TYPE_STR = BACKUP_TYPE
    SOURCE = "smb"
    NOTE_PREFIX = "matrox backup of"
    LOG_LABEL = "mtx"
    PULL_ERRORS = (OSError,)

    def __init__(self, host, dest_root, plant, line, station, *,
                 cameras=None, user=MTX_USER, passwd=MTX_PASS,
                 note="", run_id="", mount=smb_mount, throttle=0.0, on_complete=None,
                 max_photos=MAX_PHOTOS, topup=True):
        super().__init__(host, dest_root, plant, line, station, cameras=cameras,
                         note=note, run_id=run_id, throttle=throttle,
                         on_complete=on_complete)
        self.user = user or ""
        self.passwd = passwd or ""
        self._mount = mount
        self.max_photos = int(max_photos)
        self.topup = bool(topup)

    def _pull_camera(self, host, label, dated: Path, done, nbytes):
        """Mount one camera's share, enumerate da/ + the newest photos, and copy
        each into <dated>/<label>/…."""
        self._set(status="listing", current=f"{label} @ {host}")
        base, cleanup = self._mount(host, self.user, self.passwd)
        try:
            files = _enumerate_files(base, self.max_photos)
            with self._lock:
                self._p["total"] += len(files)
            self._set(status="downloading")
            for src in files:
                if self.cancelled:
                    return done, nbytes
                rel = src.relative_to(base).as_posix()
                self._set(current=f"{label}/{rel}")
                dest = dated.joinpath(label, *rel.split("/"))
                try:
                    nbytes += _copy_file(src, dest)
                    done += 1
                except OSError as e:
                    # a live camera rotates SavedImages mid-backup and a single
                    # unreadable file must not sink the whole pull - skip + log
                    with self._lock:
                        self._p["skipped"].append(f"{label}/{rel}")
                    log.info("skipped %s (%s)", rel, e)
                self._set(done=done, bytes=nbytes)
                if self._throttle:
                    time.sleep(self._throttle)
            return done, nbytes
        finally:
            cleanup()

    def _settle(self, dated: Path, when):
        """Where this run's evidence finally lives: the camera's newest snapshot
        when the only thing that changed was the photos (they get folded in and
        this run's folder goes away), otherwise the folder just pulled.

        Runs after the pull has already landed as a complete snapshot of its own,
        so every failure mode here simply keeps that folder: a top-up is a
        tidiness win, and a backup that exists twice beats one that exists
        nowhere."""
        if not self.topup:
            return dated
        try:
            prev = _previous_snapshot(dated)
            if prev is None or _tree_sig(dated) != _tree_sig(prev):
                return dated
            added, _nbytes = _fold_photos(dated, prev)
            meta = _retop_meta(prev, when, skipped=self.snapshot()["skipped"])
            _discard(dated)
            self._record_extra = {
                "files": meta.get("files", 0), "bytes": meta.get("bytes", 0),
                "updated": meta.get("updated", ""),
            }
            if meta.get("taken"):
                self._record_extra["taken"] = meta["taken"]
            self._set(topup_path=str(prev), photos_added=added)
            log.info("mtx top-up: %d new photo file(s) folded into %s", added, prev)
            return prev
        except OSError:
            log.exception("photo top-up failed - keeping this run's own snapshot")
            return dated

# -- probe / diagnose / name (all over SMB) --------------------------------------

def probe_camera(host, *, user=MTX_USER, passwd=MTX_PASS, mount=smb_mount) -> dict:
    """Pre-flight: authenticate the share and confirm da/ + SavedImages. NO
    writes. `has_da` is what marks a real Matrox camera (a host that merely
    accepts the login but has no da/ is not one)."""
    out = {"reachable": False, "banner": "", "home": "",
           "has_da": False, "has_images": False, "error": ""}
    try:
        base, cleanup = mount(host, user, passwd)
    except OSError as e:
        out["error"] = str(e)
        return out
    try:
        out["reachable"] = True
        out["home"] = str(base)
        out["has_da"] = (base / "da").is_dir()
        out["has_images"] = base.joinpath(*IMAGES_PARTS).is_dir()
    except OSError as e:
        out["error"] = str(e)
    finally:
        cleanup()
    return out


def diagnose_camera(host, *, user=MTX_USER, passwd=MTX_PASS, mount=smb_mount) -> dict:
    """Read-only probe of a live Matrox camera share: the home + da listings and
    the SavedImages date folders (newest flagged). ZERO writes. Also log.info'd
    as JSON for app.log."""
    out: dict = {"host": host, "home": "", "home_list": [], "da_list": [],
                 "image_dates": [], "newest_date": "", "error": ""}
    try:
        base, cleanup = mount(host, user, passwd)
    except OSError as e:
        out["error"] = str(e)
        return out
    try:
        out["home"] = str(base)
        try:
            out["home_list"] = sorted(p.name for p in base.iterdir())[:100]
        except OSError:
            pass
        da = base / "da"
        if da.is_dir():
            out["da_list"] = sorted(p.name for p in da.iterdir())[:100]
        si = base.joinpath(*IMAGES_PARTS)
        if si.is_dir():
            dates = sorted(d.name for d in si.iterdir() if d.is_dir() and _DATE_RE.match(d.name))
            out["image_dates"] = dates[-30:]
            out["newest_date"] = dates[-1] if dates else ""
    except OSError as e:
        out["error"] = str(e)
    finally:
        cleanup()
    try:
        log.info("diagnose_camera %s -> %s", host, json.dumps(out)[:4000])
    except Exception:  # noqa: BLE001
        pass
    return out


def name_from_backup(snapshot) -> dict:
    """Best-effort {name, model} from the newest SavedImages .txt sidecar already
    PULLED into a snapshot folder - the offline twin of resolve_camera_name. This
    is how a camera that could not be named live (no images yet at scan time, a
    flaky read) still teaches the library its real name from any completed backup,
    the way a robot self-names from SUMMARY.DG. Blanks on any failure so naming
    never sinks a backup."""
    out = {"name": "", "model": ""}
    try:
        root = Path(snapshot or "")
        if not root.is_dir():
            return out
        # snapshot layout: <dated>/<CAM label>/Documents/.../SavedImages/<date>/*.txt
        txts = [p for p in root.rglob("*.txt")
                if p.parent.parent.name == IMAGES_PARTS[-1] and _DATE_RE.match(p.parent.name)]
        if not txts:
            return out
        from .parsers import mtx_saved_image
        # newest date, then the camera's own stamp inside the filename - sorting
        # those names as plain text would hand back a 9am shot over a 1pm one
        txts.sort(key=lambda p: (p.parent.name,
                                 mtx_saved_image.photo_sort_key(p.name, _mtime(p))))
        info = mtx_saved_image.parse_saved_image(
            Path(ftpbackup.long_path(txts[-1])).read_text(encoding="cp1252", errors="replace"))
        cam = info.get("camera") or {}
        out["name"] = cam.get("name", "") or ""
        out["model"] = cam.get("type", "") or ""
        return out
    except OSError:
        return out


def resolve_camera_name(host, *, user=MTX_USER, passwd=MTX_PASS, mount=smb_mount) -> dict:
    """Best-effort {name, model} from the newest SavedImages .txt sidecar (the
    camera analogue of the robot's SUMMARY.DG). Blanks on any failure so naming
    never sinks a scan."""
    out = {"name": "", "model": ""}
    try:
        base, cleanup = mount(host, user, passwd)
    except OSError:
        return out
    try:
        si = base.joinpath(*IMAGES_PARTS)
        if not si.is_dir():
            return out
        dates = sorted(d for d in si.iterdir() if d.is_dir() and _DATE_RE.match(d.name))
        if not dates:
            return out
        from .parsers import mtx_saved_image
        txts = sorted(dates[-1].glob("*.txt"),
                      key=lambda p: mtx_saved_image.photo_sort_key(p.name, _mtime(p)))
        if not txts:
            return out
        info = mtx_saved_image.parse_saved_image(
            txts[-1].read_text(encoding="cp1252", errors="replace"))
        cam = info.get("camera") or {}
        out["name"] = cam.get("name", "") or ""
        out["model"] = cam.get("type", "") or ""
        return out
    except OSError:
        return out
    finally:
        cleanup()
