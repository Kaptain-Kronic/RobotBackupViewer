"""Keyence CV-X camera backup taker: pull a CV-X vision controller's settings off
the camera over plain FTP.

Discovered live (2026-07-13, CV-X482D on the shop floor — see
keyence-helper/CVX_FTP_LAYOUT.md): a CV-X exposes an **anonymous** FTP server
(`220 CV-X482D FTP server ready`), the login lands on the SD card at `/SD1/`, and
the settings live under `cv-x/setting/` (config `.dat`/`.tbd` + master `.bmp`
images, a `recovery/` dir, and numbered program dirs). `cv-x/box/` holds large
saved-set blobs. This means Keyence backup is plain FTP after all — the
proprietary `Vapi.Net.dll` C# helper the old plan assumed is NOT required.

Backup scope (matches the shop's existing Terminal-Software backup and the parked
helper's default): the whole `cv-x/setting/` tree; `cv-x/box/` is optional (big).

Mirrors mtxbackup.CameraBackupJob's shape (uuid id, lock-guarded progress dict,
snapshot()/cancel()/run() on a worker thread) so the api layer polls it through
the same endpoints, and reuses ftpbackup's gentle transfer primitives.

CV-X FTP quirk handled here: `LIST <path>` / `RETR <path-with-dirs>` are refused
("550 Bad path"); you must CWD into a directory and then act with bare names. So
the downloader positions CWD per directory and RETRs basenames — unlike the Linux
Matrox server, which resolves full relpaths directly.

ftplib is injected via ftp_factory so the whole flow is testable offline against a
FakeFTP (see tests/test_keyencebackup.py) - live-camera testing is never the first
validation.
"""
from __future__ import annotations

import datetime as _dt
import ftplib
import json
import logging
import threading
import uuid
from pathlib import Path

from . import ftpbackup
from .ftpbackup import _names, _walk   # shared read-only FTP listing helpers

log = logging.getLogger(__name__)

# CV-X FTP takes an anonymous login (empty user/pass); the SD card is the login dir.
KEYENCE_USER = ""
KEYENCE_PASS = ""

SETTING_DIR = "cv-x/setting"       # relative to the FTP login dir (/SD1)
BOX_DIR = "cv-x/box"
BACKUP_TYPE = "keyence cv-x setting"
WALK_MAX_FILES = 20_000


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def keyence_enumerate(ftp, home: str, *, include_box: bool = False, cancel=None) -> list[str]:
    """Relpaths (from `home`) to pull for one CV-X: the whole `cv-x/setting/` tree,
    plus `cv-x/box/` when include_box. Read-only; restores CWD to `home`."""
    out: list = []

    def _home():
        if home:
            try:
                ftp.cwd(home)
            except ftplib.all_errors:
                pass

    targets = [SETTING_DIR] + ([BOX_DIR] if include_box else [])
    for target in targets:
        _home()
        try:
            for part in target.split("/"):
                ftp.cwd(part)
            _walk(ftp, target, out, cancel)
        except ftplib.all_errors:
            log.info("no %s tree on this CV-X", target)
    _home()
    return out


class KeyenceBackupJob:
    """One Keyence CV-X station backup run. Construct, then .run() on a worker
    thread; poll .snapshot(); .cancel() stops gracefully between files."""

    def __init__(self, host, dest_root, plant, line, station, *,
                 cameras=None, user=KEYENCE_USER, passwd=KEYENCE_PASS, passive=True,
                 port=21, include_box=False, note="", ftp_factory=ftplib.FTP,
                 throttle=0.03, on_complete=None):
        self.id = uuid.uuid4().hex
        self.host = host
        self.port = int(port or 21)
        self.dest_root = Path(dest_root)
        self.plant = plant or ""
        self.line = line or ""
        self.station = station or ""
        self.cameras = list(cameras) if cameras else [{"label": "CAM1", "host": host}]
        self.user = user or ""
        self.passwd = passwd or ""
        self.passive = passive
        self.include_box = include_box
        self.note = note or ""
        self._ftp_factory = ftp_factory
        self._throttle = throttle
        self._on_complete = on_complete

        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._p = {
            "id": self.id, "status": "pending", "host": host,
            "robot": self.station, "line": self.line, "plant": self.plant,
            "device_type": "camera-keyence", "cameras": [c["label"] for c in self.cameras],
            "total": 0, "done": 0, "bytes": 0, "current": "",
            "skipped": [], "error": "", "dated_path": "", "latest_path": "",
            "started": "", "finished": "",
        }

    def _set(self, **kw):
        with self._lock:
            self._p.update(kw)

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._p)
            s["skipped"] = list(self._p["skipped"])
            s["cameras"] = list(self._p["cameras"])
        return s

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> dict:
        self._set(status="connecting", started=_now().isoformat(timespec="seconds"))
        when = _now()
        dated = ftpbackup.dated_dir(self.dest_root, self.plant, self.line, self.station, when)
        try:
            dated.mkdir(parents=True, exist_ok=True)
            self._set(dated_path=str(dated))

            done = 0
            nbytes = 0
            errors: list[str] = []
            for cam in self.cameras:
                if self.cancelled:
                    return self._finish("cancelled")
                label = ftpbackup._safe_name(cam.get("label") or "CAM1")
                chost = cam.get("host") or self.host
                try:
                    done, nbytes = self._pull_camera(chost, label, dated, done, nbytes)
                except ftplib.all_errors as e:
                    msg = f"{label}@{chost}: {type(e).__name__}: {e}"
                    log.warning("keyence pull failed %s", msg)
                    errors.append(msg)

            if self.cancelled:
                return self._finish("cancelled")
            if done == 0:
                return self._finish("error", error=errors[0] if errors else "no files pulled")

            self._write_sidecars(dated, when, done, nbytes, errors)
            latest = ftpbackup.mirror_latest(
                dated, ftpbackup.latest_dir(self.dest_root, self.plant, self.line, self.station),
                label=self.station)
            self._set(latest_path=str(latest) if latest else "")
            result = self._finish("done", error="; ".join(errors))
            if self._on_complete:
                try:
                    self._on_complete(self)
                except Exception:  # noqa: BLE001 - registration must not fail the backup
                    log.exception("on_complete callback failed for %s", self.station)
            return result
        except Exception as e:  # noqa: BLE001 - surface, never crash the worker
            log.exception("keyence backup of %s failed", self.station)
            return self._finish("error", error=f"{type(e).__name__}: {e}")

    def _finish(self, status, error="") -> dict:
        self._set(status=status, error=error, current="",
                  finished=_now().isoformat(timespec="seconds"))
        return self.snapshot()

    def _pull_camera(self, host, label, dated: Path, done, nbytes):
        """Connect to one CV-X, enumerate cv-x/setting (+box), and download each
        file into <dated>/<label>/…. The CV-X FTP refuses pathful RETR, so we CWD
        into each remote directory and RETR bare basenames."""
        ftp = None
        try:
            self._set(status="listing", current=f"{label} @ {host}")
            ftp = self._connect(host)
            try:
                home = ftp.pwd()
            except ftplib.all_errors:
                home = ""
            rels = keyence_enumerate(ftp, home, include_box=self.include_box,
                                     cancel=lambda: self.cancelled)
            with self._lock:
                self._p["total"] += len(rels)
            self._set(status="downloading")

            cur_dir = None
            for rel in sorted(rels):
                if self.cancelled:
                    return done, nbytes
                self._set(current=f"{label}/{rel}")
                parts = rel.split("/")
                rdir, base = "/".join(parts[:-1]), parts[-1]
                if rdir != cur_dir:
                    self._cwd_into(ftp, home, rdir)   # reposition only on dir change
                    cur_dir = rdir
                dest = dated.joinpath(label, *parts)
                try:
                    nbytes += ftpbackup.retrieve(ftp, base, dest)   # bare-name RETR at CWD
                    done += 1
                except ftplib.all_errors as e:   # tuple incl. OSError (long-path/os ops)
                    # a single missing/locked file must not sink the whole pull
                    with self._lock:
                        self._p["skipped"].append(f"{label}/{rel}")
                    log.info("skipped %s (%s)", rel, e)
                self._set(done=done, bytes=nbytes)
                if self._throttle:
                    import time
                    time.sleep(self._throttle)
            return done, nbytes
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:  # noqa: BLE001
                    try:
                        ftp.close()
                    except Exception:  # noqa: BLE001
                        pass

    def _cwd_into(self, ftp, home, rdir):
        """Position CWD at home/<rdir> segment by segment (the CV-X FTP rejects a
        pathful cwd/RETR, so walk the segments)."""
        if home:
            ftp.cwd(home)
        for seg in rdir.split("/"):
            if seg:
                ftp.cwd(seg)

    def _connect(self, host):
        ftp = self._ftp_factory(timeout=ftpbackup.CONNECT_TIMEOUT)
        ftp.connect(host, self.port)
        ftp.login(self.user, self.passwd)   # anonymous
        try:
            ftp.set_pasv(self.passive)
        except Exception:  # noqa: BLE001
            pass
        return ftp

    def _write_sidecars(self, dated: Path, when: _dt.datetime, files: int,
                        nbytes: int, errors: list):
        note = self.note.strip() or (
            f"keyence backup of {self.station} taken {when.strftime('%Y-%m-%d %H:%M:%S')}")
        (dated / "notes.txt").write_text(note + "\n", encoding="utf-8")
        meta = {
            "robot": self.station, "line": self.line, "plant": self.plant,
            "host": self.host, "taken": when.isoformat(timespec="seconds"),
            "type": BACKUP_TYPE, "device_type": "camera-keyence",
            "cameras": [c.get("label") for c in self.cameras],
            "files": files, "bytes": nbytes, "source": "ftp",
        }
        if errors:
            meta["errors"] = errors
        (dated / "backup.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def library_match(self) -> dict:
        dated = self.snapshot().get("dated_path", "")
        history_root = str(Path(dated).parent.parent) if dated else ""
        ips = [c.get("host") for c in self.cameras if c.get("host")]
        return {"robot": self.station, "line": self.line, "plant": self.plant,
                "device_type": "camera-keyence", "ips": ips, "history_root": history_root}

    def library_backup(self) -> dict:
        s = self.snapshot()
        return {
            "path": s["dated_path"], "taken": s["started"],
            "type": BACKUP_TYPE, "files": s["done"], "bytes": s["bytes"],
            "source": "ftp", "note": self.note,
        }


# -- pre-flight probe + read-only diagnose ---------------------------------------

def probe_keyence(host, *, user=KEYENCE_USER, passwd=KEYENCE_PASS, passive=True,
                  port=21, ftp_factory=ftplib.FTP) -> dict:
    """Pre-flight: connect + anonymous login + confirm the cv-x/setting tree. NO
    writes. `has_setting` is what marks a real CV-X (a bare ftpd that accepts
    anonymous login but has no cv-x/ is not a camera)."""
    out = {"reachable": False, "banner": "", "home": "",
           "has_cvx": False, "has_setting": False, "error": ""}
    ftp = None
    try:
        ftp = ftp_factory(timeout=ftpbackup.CONNECT_TIMEOUT)
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
        try:
            out["home"] = ftp.pwd() or ""
        except Exception:  # noqa: BLE001
            pass
        names = {n.lower() for n in _names(ftp)}
        out["has_cvx"] = "cv-x" in names
        if out["has_cvx"]:
            try:
                for part in SETTING_DIR.split("/"):
                    ftp.cwd(part)
                out["has_setting"] = True
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


def diagnose_keyence(host, *, user=KEYENCE_USER, passwd=KEYENCE_PASS, passive=True,
                     port=21, ftp_factory=ftplib.FTP) -> dict:
    """Read-only probe of a live CV-X: banner, home dir, and the cv-x / setting /
    box listings. ZERO writes. Also log.info'd as JSON for app.log."""
    port = int(port or 21)
    out: dict = {"host": host, "port": port, "banner": "", "home": "",
                 "home_list": [], "cvx_list": [], "setting_list": [], "box_list": [],
                 "error": ""}
    ftp = None
    try:
        ftp = ftp_factory(timeout=ftpbackup.CONNECT_TIMEOUT)
        ftp.connect(host, port)
        ftp.login(user or "", passwd or "")
        try:
            ftp.set_pasv(passive)
        except Exception:  # noqa: BLE001
            pass
        try:
            out["banner"] = (ftp.getwelcome() or "").strip()
        except Exception as e:  # noqa: BLE001
            out["banner"] = f"<{type(e).__name__}: {e}>"
        try:
            out["home"] = ftp.pwd() or ""
        except Exception:  # noqa: BLE001
            pass
        out["home_list"] = _names(ftp)[:100]

        def _relist(path, key):
            if out["home"]:
                try:
                    ftp.cwd(out["home"])
                except ftplib.all_errors:
                    pass
            try:
                for part in path.split("/"):
                    ftp.cwd(part)
                out[key] = _names(ftp)[:100]
            except ftplib.all_errors:
                pass

        _relist("cv-x", "cvx_list")
        _relist(SETTING_DIR, "setting_list")
        _relist(BOX_DIR, "box_list")
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
    try:
        log.info("diagnose_keyence %s -> %s", host, json.dumps(out)[:4000])
    except Exception:  # noqa: BLE001
        pass
    return out
