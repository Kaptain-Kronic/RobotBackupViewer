"""FTP backup taker: pull an "all of above" backup from a FANUC
controller over FTP and lay it down in the Latest-mirror + dated-history tree.

Scope (v1, confirmed with the user): the controller's MD: device only - the
ASCII set (.SV/.TP/.VR/.IO/.DG/.VA) the viewer already parses, which the
controller synthesises on the fly when you GET from MD:. A true IMAGE backup
needs TFTP + a controller reboot into the boot menu and is deliberately OUT of
scope here. FR:/FRA: recursion is supported but off by default.

Engine ethics (it may be pointed at a running production robot):
  - ONE connection, no parallel GETs against a single controller
  - a small throttle between files
  - retry a failed file at most twice, with backoff
  - write each file to <name>.part then rename, so a crash never leaves a
    half-file that looks complete
  - skip .IMG/.IMR image artifacts (not an FTP backup's job) with a logged note
  - the dated snapshot is the immutable source of truth; the Latest mirror is
    swapped in last and a failed mirror never corrupts the only good copy

ftplib is injected via ftp_factory so the whole flow is testable offline against
a FakeFTP (see tests/test_ftpbackup.py) - live-controller testing is never the first
validation.
"""
from __future__ import annotations

import datetime as _dt
import ftplib
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DEVICES = ["MD:"]
SKIP_EXTS = (".IMG", ".IMR")  # image artifacts: not an FTP backup's job
FR_MAX_DEPTH = 6
FR_MAX_FILES = 5000
CONNECT_TIMEOUT = 20
RETRIES = 2


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _safe_name(part: str) -> str:
    """A path-safe folder name (robot/line/plant may carry odd characters)."""
    keep = "".join(c if c.isalnum() or c in " ._-" else "_" for c in (part or "").strip())
    return keep.strip(" .") or "_"


def dated_dir(root: Path, plant: str, line: str, robot: str, when: _dt.datetime) -> Path:
    """<root>\\<plant>\\<line>\\<robot>\\<YYYY_MM_DD>\\<HH_MM_SS> - PLANT is
    omitted from the path when blank so the tree degrades to LINE/ROBOT."""
    parts = [root]
    if plant:
        parts.append(_safe_name(plant))
    parts += [_safe_name(line), _safe_name(robot),
              when.strftime("%Y_%m_%d"), when.strftime("%H_%M_%S")]
    return Path(*parts)


def latest_dir(root: Path, plant: str, line: str, robot: str) -> Path:
    """<root>\\<plant>\\<line>\\Latest\\<robot> - the overwritten newest mirror."""
    parts = [root]
    if plant:
        parts.append(_safe_name(plant))
    parts += [_safe_name(line), "Latest", _safe_name(robot)]
    return Path(*parts)


# -- reusable transfer primitives -----------------------------------------------
# Shared by the FANUC BackupJob and the Matrox CameraBackupJob (mtxbackup.py) so
# the gentle, crash-safe download + mirror behaviour is written once.

def long_path(p) -> str:
    r"""A Windows extended-length path string (\\?\...) so a file whose full path
    exceeds the legacy 260-char MAX_PATH still opens. Camera trees blow past it
    easily (deep dated snapshot + `CAM1\Documents\Matrox Design Assistant\
    SavedImages\<date>\` + a long inspection filename + the `.part` suffix).
    No-op off Windows or when already prefixed; needs an absolute path."""
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):            # UNC \\host\share -> \\?\UNC\host\share
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s                # local C:\... -> \\?\C:\...


def retrieve(ftp, retr_arg: str, dest: Path, *, retries: int = RETRIES) -> int:
    r"""Download one file to `dest` via the .part-then-rename protocol with
    bounded retries + backoff, so a crash never leaves a half-file that looks
    complete. `retr_arg` is the RETR argument - a bare basename when the caller
    has already positioned CWD (the flat FANUC MD: case), or a path the server
    resolves relative to CWD (the nested Matrox case). Returns bytes written;
    raises the last ftplib error if every attempt fails. Long dest paths use the
    \\?\ prefix so a deep camera tree never trips the 260-char limit."""
    os.makedirs(long_path(dest.parent), exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part_l, dest_l = long_path(part), long_path(dest)
    last_err = None
    for attempt in range(retries + 1):
        try:
            counter = {"n": 0}
            with open(part_l, "wb") as fh:
                def _w(chunk, _fh=fh, _c=counter):
                    _fh.write(chunk)
                    _c["n"] += len(chunk)
                ftp.retrbinary("RETR " + retr_arg, _w)
            os.replace(part_l, dest_l)
            return counter["n"]
        except ftplib.all_errors as e:
            last_err = e
            if os.path.exists(part_l):
                try:
                    os.remove(part_l)
                except OSError:
                    pass
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
    raise last_err if last_err else RuntimeError("download failed: " + retr_arg)


def mirror_latest(dated: Path, latest: Path, *, label: str = "") -> Path | None:
    """Overwrite `latest` with a copy of the `dated` snapshot, built in a sibling
    .__tmp dir then atomically swapped, so a half-written mirror is never visible
    and a failure here leaves the (good) dated snapshot untouched. Returns the
    mirror path, or None on failure (logged)."""
    tmp = latest.with_name(latest.name + ".__tmp")
    try:
        latest.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(dated, tmp)
        if latest.exists():
            shutil.rmtree(latest, ignore_errors=True)
        os.replace(tmp, latest)
        return latest
    except OSError:
        log.exception("Latest mirror failed for %s (dated snapshot is intact)",
                      label or latest.name)
        shutil.rmtree(tmp, ignore_errors=True)
        return None


# -- read-only FTP tree walk (shared by the Keyence CV-X camera backup) ----------
# The Matrox side moved to SMB, but Keyence CV-X cameras speak real FTP, so these
# gentle listing helpers live here next to the transfer primitives.

WALK_MAX_FILES = 20_000        # guard against a runaway card


def _names(ftp) -> list[str]:
    """Basenames in the current FTP directory (nlst tolerated to fail on an empty
    or odd dir; some servers return full paths, so strip to the basename)."""
    try:
        raw = ftp.nlst()
    except ftplib.all_errors:
        return []
    out = []
    for n in raw:
        base = n.rsplit("/", 1)[-1]
        if base and base not in (".", ".."):
            out.append(base)
    return out


def _walk(ftp, rel: str, out: list, cancel=None) -> None:
    """Recursively collect file relpaths under the CURRENT directory (whose path
    from the walk root is `rel`). Directories are detected by descending into them
    (cwd succeeds) and files fall through. Leaves CWD where it started (each
    descent is matched by a cwd('..'))."""
    for name in sorted(_names(ftp)):
        if cancel is not None and cancel():
            return
        if len(out) >= WALK_MAX_FILES:
            log.warning("ftp walk hit file cap %d under %s", WALK_MAX_FILES, rel)
            return
        child = f"{rel}/{name}" if rel else name
        try:
            ftp.cwd(name)                 # success => it's a directory, now inside it
        except ftplib.all_errors:
            out.append(child)             # not a directory => a file to pull
            continue
        _walk(ftp, child, out, cancel)
        try:
            ftp.cwd("..")
        except ftplib.all_errors:
            return


class BackupJob:
    """One backup run. Construct, then .run() on a worker thread; poll .snapshot()
    for live progress; .cancel() requests a graceful stop between files."""

    def __init__(self, host, dest_root, plant, line, robot, *,
                 user="", passwd="", passive=True, port=21,
                 devices=None, note="", recurse_fr=False,
                 ftp_factory=ftplib.FTP, throttle=0.03, on_complete=None):
        self.id = uuid.uuid4().hex
        self.host = host
        self.port = int(port or 21)
        self.dest_root = Path(dest_root)
        self.plant = plant or ""
        self.line = line or ""
        self.robot = robot or ""
        self.user = user or ""
        self.passwd = passwd or ""
        self.passive = passive
        self.devices = list(devices) if devices else list(DEFAULT_DEVICES)
        self.note = note or ""
        self.recurse_fr = recurse_fr
        self._ftp_factory = ftp_factory
        self._throttle = throttle
        self._on_complete = on_complete  # callback(job) after a successful run

        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._p = {
            "id": self.id, "status": "pending", "host": host,
            "robot": self.robot, "line": self.line, "plant": self.plant,
            "total": 0, "done": 0, "bytes": 0, "current": "",
            "skipped": [], "error": "", "dated_path": "", "latest_path": "",
            "started": "", "finished": "",
        }

    # -- progress ------------------------------------------------------------

    def _set(self, **kw):
        with self._lock:
            self._p.update(kw)

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._p)
            s["skipped"] = list(self._p["skipped"])
        return s

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- run -----------------------------------------------------------------

    def run(self) -> dict:
        self._set(status="connecting", started=_now().isoformat(timespec="seconds"))
        ftp = None
        try:
            ftp = self._connect()
            files = self._enumerate(ftp)
            if self.cancelled:
                return self._finish("cancelled")
            self._set(status="downloading", total=len(files))

            when = _now()
            dated = dated_dir(self.dest_root, self.plant, self.line, self.robot, when)
            dated.mkdir(parents=True, exist_ok=True)
            self._set(dated_path=str(dated))

            done = 0
            nbytes = 0
            for dev, rel in files:
                if self.cancelled:
                    return self._finish("cancelled")
                self._set(current=rel)
                nbytes += self._download_one(ftp, dev, rel, dated)
                done += 1
                self._set(done=done, bytes=nbytes)
                if self._throttle:
                    time.sleep(self._throttle)

            self._write_sidecars(dated, when, done, nbytes)
            latest = self._mirror_latest(dated)
            self._set(latest_path=str(latest) if latest else "")
            result = self._finish("done")
            if self._on_complete:
                try:
                    self._on_complete(self)
                except Exception:  # noqa: BLE001 - registration must not fail the backup
                    log.exception("on_complete callback failed for %s", self.robot)
            return result
        except ftplib.all_errors as e:
            log.warning("ftp backup of %s@%s failed: %s", self.robot, self.host, e)
            return self._finish("error", error=f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface, never crash the worker
            log.exception("backup of %s failed", self.robot)
            return self._finish("error", error=f"{type(e).__name__}: {e}")
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:  # noqa: BLE001
                    try:
                        ftp.close()
                    except Exception:  # noqa: BLE001
                        pass

    def _finish(self, status, error="") -> dict:
        self._set(status=status, error=error, current="",
                  finished=_now().isoformat(timespec="seconds"))
        return self.snapshot()

    # -- ftp -----------------------------------------------------------------

    def _connect(self):
        ftp = self._ftp_factory(timeout=CONNECT_TIMEOUT)
        ftp.connect(self.host, self.port)
        ftp.login(self.user, self.passwd)  # blank user/pass = anonymous
        try:
            ftp.set_pasv(self.passive)
        except Exception:  # noqa: BLE001 - some fakes/servers ignore PASV toggles
            pass
        return ftp

    def _cwd_root(self, ftp):
        for path in ("/", ""):
            try:
                ftp.cwd(path)
                return
            except ftplib.all_errors:
                continue

    def _enumerate(self, ftp) -> list:
        """[(device, relpath)] for every file to pull. MD: is flat; FR: recurses
        when enabled. Image artifacts are skipped and logged."""
        self._set(status="listing")
        out: list = []
        for dev in self.devices:
            self._cwd_root(ftp)
            try:
                ftp.cwd(dev)
            except ftplib.all_errors:
                # some servers root straight at MD: - if the only device can't be
                # entered, fall back to listing the current dir
                if len(self.devices) == 1:
                    self._guard_not_camera(ftp)
                else:
                    log.info("device %s not available on %s", dev, self.host)
                    continue
            recurse = self.recurse_fr and dev.upper().startswith(("FR", "FRA"))
            self._list_into(ftp, dev, "", out, recurse, depth=0)
        return out

    def _list_into(self, ftp, dev, rel, out, recurse, depth):
        try:
            names = ftp.nlst()
        except ftplib.all_errors:
            return
        for name in names:
            base = name.rsplit("/", 1)[-1]
            if base in (".", ".."):
                continue
            child = f"{rel}/{base}" if rel else base
            if base.upper().endswith(SKIP_EXTS):
                with self._lock:
                    self._p["skipped"].append(child)
                continue
            if recurse and depth < FR_MAX_DEPTH and self._is_dir(ftp, base):
                if len(out) < FR_MAX_FILES:
                    self._list_into(ftp, dev, child, out, recurse, depth + 1)
                    self._cwd_up(ftp)
                continue
            if len(out) >= FR_MAX_FILES:
                log.warning("file cap %d hit on %s", FR_MAX_FILES, dev)
                return
            out.append((dev, child))

    def _guard_not_camera(self, ftp):
        """The FANUC fallback (no MD: device -> flat-list the login dir) pointed
        at a Matrox camera would 'succeed' with a couple of loose shell scripts
        and then choke on the da/ directory - a junk backup that LOOKS like it
        ran (field bug: 'the backup didn't grab the camera data'). If the login
        dir carries the camera signature, refuse loudly instead."""
        try:
            names = {n.rsplit("/", 1)[-1].lower() for n in ftp.nlst()}
        except ftplib.all_errors:
            return
        if "da" in names and "documents" in names:
            raise RuntimeError(
                "this host looks like a Matrox camera (da/ + Documents/ at its "
                "FTP root), not a FANUC robot - set its device type to 'matrox "
                "camera' in the library and back it up again")
        if "cv-x" in names:
            raise RuntimeError(
                "this host looks like a Keyence CV-X camera (cv-x/ at its FTP "
                "root), not a FANUC robot - set its device type to 'keyence "
                "camera' in the library and back it up again")

    def _is_dir(self, ftp, name) -> bool:
        try:
            ftp.cwd(name)
            return True
        except ftplib.all_errors:
            return False

    def _cwd_up(self, ftp):
        try:
            ftp.cwd("..")
        except ftplib.all_errors:
            pass

    def _download_one(self, ftp, dev, rel, dated: Path) -> int:
        # MD: is flat, so CWD is already the device and a bare basename RETRs;
        # the dest keeps the (possibly nested FR:) relpath.
        return retrieve(ftp, rel.rsplit("/", 1)[-1], dated / rel.replace("/", os.sep))

    # -- on disk -------------------------------------------------------------

    def _write_sidecars(self, dated: Path, when: _dt.datetime, files: int, nbytes: int):
        note = self.note.strip() or f"backup of {self.robot} taken {when.strftime('%Y-%m-%d %H:%M:%S')}"
        (dated / "notes.txt").write_text(note + "\n", encoding="utf-8")
        import json
        meta = {
            "robot": self.robot, "line": self.line, "plant": self.plant,
            "host": self.host, "taken": when.isoformat(timespec="seconds"),
            "type": "all of above", "devices": self.devices,
            "files": files, "bytes": nbytes, "source": "ftp",
        }
        (dated / "backup.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _mirror_latest(self, dated: Path) -> Path | None:
        """Overwrite <...>/Latest/<robot> with this snapshot (see mirror_latest)."""
        latest = latest_dir(self.dest_root, self.plant, self.line, self.robot)
        return mirror_latest(dated, latest, label=self.robot)

    # -- library record ------------------------------------------------------

    def library_match(self) -> dict:
        dated = self.snapshot().get("dated_path", "")
        # <root>/<plant>/<line>/<robot>/<date>/<time> -> the robot folder
        history_root = str(Path(dated).parent.parent) if dated else ""
        return {"robot": self.robot, "line": self.line, "plant": self.plant,
                "ips": [self.host] if self.host else [], "history_root": history_root}

    def library_backup(self) -> dict:
        s = self.snapshot()
        return {
            "path": s["dated_path"], "taken": s["started"],
            "type": "all of above", "files": s["done"], "bytes": s["bytes"],
            "source": "ftp", "note": self.note,
        }


def probe_controller(host, *, user="", passwd="", passive=True, port=21,
                     ftp_factory=ftplib.FTP) -> dict:
    """Pre-flight: connect + login + sniff devices. NO writes, NO downloads -
    so a tech can confirm reachability before touching a live robot."""
    out = {"reachable": False, "banner": "", "has_md": False, "has_fr": False, "error": ""}
    ftp = None
    try:
        ftp = ftp_factory(timeout=CONNECT_TIMEOUT)
        ftp.connect(host, int(port or 21))
        ftp.login(user or "", passwd or "")
        try:
            ftp.set_pasv(passive)
        except Exception:  # noqa: BLE001
            pass
        out["reachable"] = True
        try:
            out["banner"] = (ftp.getwelcome() or "").strip()
        except Exception:  # noqa: BLE001
            pass
        for dev, key in (("MD:", "has_md"), ("FR:", "has_fr")):
            try:
                ftp.cwd("/")
            except ftplib.all_errors:
                pass
            try:
                ftp.cwd(dev)
                out[key] = True
            except ftplib.all_errors:
                pass
    except ftplib.all_errors as e:
        out["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                try:
                    ftp.close()
                except Exception:  # noqa: BLE001
                    pass
    return out
