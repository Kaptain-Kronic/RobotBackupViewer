"""Backup-run log tests - isolated from %APPDATA% by pointing settings.app_dir
at a tmp_path (same pattern as test_library)."""
from backupviewer import backuplog, settings


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path)


def _last_run():
    """Newest run, or None. backuplog carried this as a public last_run()
    helper that no source file ever called - runs are already newest-first."""
    runs = backuplog.load()["runs"]
    return runs[0] if runs else None


SPEC_A = {"host": "192.0.2.11", "robot": "R1", "line": "L1", "plant": "P",
          "robot_id": "id-r1", "user": "", "passive": True, "note": "",
          "passwd": "SECRET", "run_id": "run-1"}
SPEC_B = {"host": "192.0.2.12", "robot": "R2", "line": "L1", "plant": "P",
          "robot_id": "id-r2", "user": "ftpuser", "passive": False,
          "passwd": "SECRET", "run_id": "run-1"}


def test_run_lifecycle_and_no_password(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    backuplog.start_job("run-1", "j1", SPEC_A)
    backuplog.start_job("run-1", "j2", SPEC_B)

    run = _last_run()
    assert run["id"] == "run-1" and run["started"] and not run["finished"]
    assert [j["status"] for j in run["jobs"]] == ["running", "running"]
    # the password never touches disk - not in records, not anywhere in the file
    assert "SECRET" not in (tmp_path / "backup_log.json").read_text(encoding="utf-8")

    backuplog.finish_job("run-1", "j1", {"status": "done", "dated_path": "x/y"})
    run = _last_run()
    assert not run["finished"]                      # j2 still running
    backuplog.finish_job("run-1", "j2", {"status": "error", "error": "timed out"})
    run = _last_run()
    assert run["finished"]                          # all terminal -> run closed
    by = {j["job_id"]: j for j in run["jobs"]}
    assert by["j1"]["status"] == "done" and by["j1"]["dated_path"] == "x/y"
    assert by["j2"]["status"] == "error" and by["j2"]["error"] == "timed out"

    # unknown run/job ids are ignored, never raise
    backuplog.finish_job("nope", "j1", {"status": "done"})
    backuplog.finish_job("run-1", "nope", {"status": "done"})


def test_failed_specs_only_errors(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    backuplog.start_job("run-1", "j1", SPEC_A)
    backuplog.start_job("run-1", "j2", SPEC_B)
    backuplog.start_job("run-1", "j3", dict(SPEC_A, host="192.0.2.13", robot="R3"))
    backuplog.finish_job("run-1", "j1", {"status": "done"})
    backuplog.finish_job("run-1", "j2", {"status": "error", "error": "boom"})
    backuplog.finish_job("run-1", "j3", {"status": "cancelled"})

    specs = backuplog.failed_specs()                # default = newest run
    assert [s["robot"] for s in specs] == ["R2"]    # done + cancelled excluded
    assert specs[0]["user"] == "ftpuser" and specs[0]["passive"] is False
    assert "passwd" not in specs[0] and "run_id" not in specs[0]

    assert backuplog.failed_specs("run-1") == specs
    assert backuplog.failed_specs("missing") == []


def test_late_joiner_reopens_run_and_replaces_settled_row(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    backuplog.start_job("run-1", "j1", SPEC_A)
    backuplog.start_job("run-1", "j2", SPEC_B)
    backuplog.finish_job("run-1", "j1", {"status": "error", "error": "refused"})
    backuplog.finish_job("run-1", "j2", {"status": "done"})
    assert _last_run()["finished"]

    # R1's retry fired back into the same run: the run reopens, R1's settled
    # row is replaced (not duplicated), attempts counts both tries
    backuplog.start_job("run-1", "j3", SPEC_A)
    run = _last_run()
    assert run["id"] == "run-1" and not run["finished"]
    assert len(run["jobs"]) == 2
    rec = next(j for j in run["jobs"] if j["job_id"] == "j3")
    assert rec["status"] == "running" and rec["attempts"] == 2
    assert not any(j["job_id"] == "j1" for j in run["jobs"])

    backuplog.finish_job("run-1", "j3", {"status": "done"})
    run = _last_run()
    assert run["finished"]
    assert sorted(j["status"] for j in run["jobs"]) == ["done", "done"]
    assert backuplog.failed_specs("run-1") == []


def test_running_row_never_clobbered_and_hosts_differ(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    backuplog.start_job("run-1", "j1", SPEC_A)
    # the same robot fired again while its first job still runs: two real jobs,
    # two rows - only SETTLED rows are ever replaced
    backuplog.start_job("run-1", "j2", SPEC_A)
    assert len(_last_run()["jobs"]) == 2

    # the same NAME on a different host is a different robot - never replaced
    backuplog.finish_job("run-1", "j1", {"status": "error", "error": "x"})
    backuplog.finish_job("run-1", "j2", {"status": "error", "error": "x"})
    backuplog.start_job("run-1", "j4", dict(SPEC_A, host="192.0.2.99", robot_id="id-r9"))
    assert len(_last_run()["jobs"]) == 3


def test_runs_newest_first_and_capped(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    for i in range(25):
        backuplog.start_job(f"run-{i}", f"j{i}", dict(SPEC_A, run_id=f"run-{i}"))
    data = backuplog.load()
    assert len(data["runs"]) == 20                  # capped
    assert data["runs"][0]["id"] == "run-24"        # newest first
    assert _last_run()["id"] == "run-24"
    # failed_specs of an evicted run finds nothing
    assert backuplog.failed_specs("run-0") == []
