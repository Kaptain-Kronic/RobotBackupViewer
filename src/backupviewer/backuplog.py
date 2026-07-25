"""Durable backup-run log: which robots succeeded / failed / got cancelled in
the last backup runs. The per-row check marks in the library are transient UI
state that the post-backup refresh wipes; this file is the memory that
survives - it powers the "last run" summary and the retry-failed button in
Manage backups.

One RUN = one user action (a bulk line backup or a single robot), grouped by
the run_id the frontend stamps on every start_backup spec of that click -
except that backups fired while a run is still going JOIN that run (the API
reuses the in-flight run_id), so a mid-run retry of a few refused robots
lands in the same "last run" report instead of burying it. A joining job
reopens the run, and a re-fire of a robot the run already settled replaces
that robot's row (attempts counts the tries) rather than duplicating it.
Jobs are recorded when they start and finished with their final snapshot, so
a crash mid-run leaves honest "running" rows rather than nothing.

Passwords are NEVER written here - retry re-prompts, exactly like the bulk
flow does.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import threading

from . import settings

log = logging.getLogger(__name__)

_FILE = "backup_log.json"
_MAX_RUNS = 20
_LOCK = threading.Lock()

# what a job record remembers of its start spec - enough to re-fire it, never
# a password
_SPEC_KEYS = ("host", "robot", "line", "plant", "robot_id", "user", "passive",
              "port", "note")


def _file():
    return settings.app_dir() / _FILE


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def load() -> dict:
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("runs"), list):
                return data
    except (OSError, ValueError):
        pass
    return {"runs": []}


def _write(data: dict) -> None:
    try:
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(_file())
    except OSError:
        log.exception("could not write %s", _FILE)


def sanitize_spec(spec: dict) -> dict:
    return {k: spec[k] for k in _SPEC_KEYS if k in spec}


def _same_target(a: dict, b: dict) -> bool:
    """Do two job records point at the same physical robot? host (the IP we
    dialed) is the strongest key, then the library row id, then the visible
    name - compared at the FIRST key both records carry, because two robots
    that differ there are different no matter what the weaker keys say
    (FANUC's default name is literally "ROBOT")."""
    for k in ("host", "robot_id", "robot"):
        if a.get(k) and b.get(k):
            return a[k] == b[k]
    return False


def start_job(run_id: str, job_id: str, spec: dict) -> None:
    """Record a job the moment it is fired (status 'running'). Creates the run
    on first sight; runs list stays newest-first and capped. A job joining an
    existing run reopens it, and if the run already SETTLED this robot (its
    previous try errored / finished), the old row is replaced in place with
    attempts counted - a retry must not double-count the robot. A row still
    running is a real concurrent job and is never clobbered."""
    with _LOCK:
        data = load()
        run = next((r for r in data["runs"] if r.get("id") == run_id), None)
        if run is None:
            run = {"id": run_id, "started": _now(), "finished": "", "jobs": []}
            data["runs"].insert(0, run)
            del data["runs"][_MAX_RUNS:]
        rec = dict(sanitize_spec(spec))
        rec.update({"job_id": job_id, "status": "running", "error": "",
                    "dated_path": "", "started": _now(), "finished": ""})
        run["finished"] = ""
        prev = next((j for j in run["jobs"]
                     if j.get("status") != "running" and _same_target(j, rec)), None)
        if prev is None:
            run["jobs"].append(rec)
        else:
            rec["attempts"] = int(prev.get("attempts", 1)) + 1
            run["jobs"][run["jobs"].index(prev)] = rec
        _write(data)


def finish_job(run_id: str, job_id: str, snap: dict) -> None:
    """Stamp a job's final snapshot onto its record; the run is finished when
    no job is left running."""
    with _LOCK:
        data = load()
        run = next((r for r in data["runs"] if r.get("id") == run_id), None)
        if run is None:
            return
        rec = next((j for j in run["jobs"] if j.get("job_id") == job_id), None)
        if rec is None:
            return
        rec["status"] = snap.get("status", "error")
        rec["error"] = snap.get("error", "")
        rec["dated_path"] = snap.get("dated_path", "")
        rec["finished"] = _now()
        if all(j.get("status") != "running" for j in run["jobs"]):
            run["finished"] = _now()
        _write(data)


def failed_specs(run_id: str | None = None) -> list[dict]:
    """Sanitized start specs of every FAILED job in the given run (default: the
    newest run). Cancelled jobs were a user's choice, not a failure - excluded."""
    data = load()
    run = None
    if run_id:
        run = next((r for r in data["runs"] if r.get("id") == run_id), None)
    elif data["runs"]:
        run = data["runs"][0]
    if run is None:
        return []
    return [sanitize_spec(j) for j in run["jobs"] if j.get("status") == "error"]
