"""stage_backups / lib_stage - the cleanup move (moves, never deletes).

Fully synthetic. The verdict engine itself is covered in test_retention; these
tests are about DISK truth: mirrored moves into _staged, the staged.log
ledger, index rebuilds, refusals that leave sources untouched, and the
no-rescan claim at the Api seam."""
import json
import shutil
import threading

import pytest

from backupviewer import library, settings


@pytest.fixture(autouse=True)
def _reap_scan_thread(monkeypatch):
    """Every background library scan dies with its own test (see
    test_library_scan for the full why — depending on `monkeypatch` orders
    this teardown before the un-patching)."""
    yield
    for t in threading.enumerate():
        if t.name in ("libscan", "libstage"):
            t.join(timeout=30)
            assert not t.is_alive(), "a library worker thread outlived its test"


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)


def _lib(tmp_path):
    root = tmp_path / "lib"
    settings.set_value("library_root", str(root))
    return root


def _snap(robot_dir, date, time, *, robot, line="L", plant="P", bytes_=1000):
    d = robot_dir / date / time
    d.mkdir(parents=True, exist_ok=True)
    (d / "SUMMARY.DG").write_text("x", encoding="utf-8")
    (d / "backup.json").write_text(json.dumps({
        "robot": robot, "line": line, "plant": plant,
        "taken": date.replace("_", "-") + "T" + time.replace("_", ":"),
        "type": "all of above", "files": 1, "bytes": bytes_, "source": "ftp",
    }), encoding="utf-8")
    return d


def _sidecar(robot_dir, rid, robot, line="L", plant="P"):
    robot_dir.mkdir(parents=True, exist_ok=True)
    (robot_dir / "robot.json").write_text(json.dumps({
        "schema": 1, "id": rid, "plant": plant, "line": line, "robot": robot,
        "model": "", "f_number": "", "ips": [],
        "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")


def _seed_r1(root):
    """R1: four completed snapshots — 2026-08 (latest) and 2026-06 (kept at
    keep=2) shield themselves by ORDER, the two 2024s are candidates by AGE,
    so the verdicts hold whenever this suite runs."""
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_08_01", "10_00_00", robot="R1")
    _snap(r1, "2026_06_01", "10_00_00", robot="R1")
    _snap(r1, "2024_05_01", "10_00_00", robot="R1", bytes_=500)
    _snap(r1, "2024_01_01", "10_00_00", robot="R1", bytes_=250)
    _sidecar(r1, "rid-1", "R1")
    return r1


def test_stage_moves_mirror_log_and_index(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)

    ticks = []
    res = library.stage_backups([
        {"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"},
        {"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"},
        {"robot_id": "rid-1", "taken": "2026-08-01T10:00:00"},   # latest: refused
        {"robot_id": "rid-x", "taken": "2024-01-01T10:00:00"},   # unknown: refused
    ], 90, 2, progress=lambda d, t: ticks.append((d, t)))

    assert len(res["staged"]) == 2
    assert len(res["failed"]) == 2
    assert ticks[0] == (2, 4)                    # gate refusals count immediately
    assert ticks[-1] == (4, 4)                   # every pick reported, no lies
    assert all("not a current candidate" in f["error"] for f in res["failed"])

    parked = root / "_staged" / "P" / "L" / "R1"
    assert (parked / "2024_05_01" / "10_00_00" / "SUMMARY.DG").is_file()
    assert (parked / "2024_01_01" / "10_00_00" / "backup.json").is_file()
    assert not (r1 / "2024_05_01").exists()            # emptied date dir pruned
    assert not (r1 / "2024_01_01").exists()
    assert (r1 / "robot.json").is_file()               # the robot folder survives
    assert (r1 / "2026_08_01" / "10_00_00").is_dir()   # untouched history intact

    lines = [json.loads(x) for x in
             (root / "_staged" / "staged.log").read_text(encoding="utf-8").splitlines()]
    assert {x["rel"] for x in lines} == {"P/L/R1/2024_05_01/10_00_00",
                                         "P/L/R1/2024_01_01/10_00_00"}

    e = library.get_robot("rid-1")
    assert len(e["backups"]) == 2                      # index rebuilt from disk
    assert e["last_backup"] == "2026-08-01T10:00:00"
    assert e["latest_path"].endswith("10_00_00")
    assert (root / "P" / "L" / "Latest" / "R1" / "SUMMARY.DG").is_file()  # mirror regen

    assert res["staging"] == {"mode": "library", "present": True, "count": 2,
                              "bytes": 750, "path": str(root / "_staged")}
    assert library.staging_status(root) == res["staging"]


def test_stage_collision_and_vanished_source_refused(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)

    already = root / "_staged" / "P" / "L" / "R1" / "2024_05_01" / "10_00_00"
    already.mkdir(parents=True)                        # hand-restored copy left behind
    shutil.rmtree(r1 / "2024_01_01")                   # vanished outside the app

    res = library.stage_backups([
        {"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"},
        {"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"},
    ], 90, 2)

    assert res["staged"] == []
    errs = {f["taken"]: f["error"] for f in res["failed"]}
    assert "already parked" in errs["2024-05-01T10:00:00"]
    # the vanished folder reconciles to stale -> protected "offline" -> refused
    assert "not a current candidate" in errs["2024-01-01T10:00:00"]
    assert (r1 / "2024_05_01" / "10_00_00").is_dir()   # source untouched


def test_stage_refuses_flat_import_record(monkeypatch, tmp_path):
    """A non-dated record's path IS the robot folder — moving it would take the
    robot with it. The guard refuses even a forged/legacy index state."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    flat = root / "P" / "L" / "R1FLAT"
    flat.mkdir(parents=True)
    (flat / "SUMMARY.DG").write_text("x", encoding="utf-8")
    e = library.get_robot("rid-1")
    recs = [dict(b) for b in e["backups"]] + [{
        "path": str(flat), "taken": "2020-01-01T00:00:00", "type": "",
        "files": 1, "bytes": 5, "source": "import", "note": ""}]
    library.update_robot("rid-1", {"backups": recs}, sidecar=False)

    res = library.stage_backups(
        [{"robot_id": "rid-1", "taken": "2020-01-01T00:00:00"}], 90, 2)
    assert res["staged"] == []
    assert "flat import" in res["failed"][0]["error"]
    assert flat.is_dir()


def test_stage_move_failure_leaves_source_and_index(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)

    def _boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(library, "_move_tree", _boom)

    res = library.stage_backups([
        {"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"},
        {"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"},
    ], 90, 2)
    assert res["staged"] == []
    assert all("disk full" in f["error"] for f in res["failed"])
    assert (r1 / "2024_05_01" / "10_00_00").is_dir()
    assert (r1 / "2024_01_01" / "10_00_00").is_dir()
    assert len(library.get_robot("rid-1")["backups"]) == 4   # index untouched
    assert library.staging_status(root)["count"] == 0


def test_stage_measures_unsized_snapshots(monkeypatch, tmp_path):
    """A sidecar-less import has bytes=0 in its record - staging measures the
    real folder before the move so the log/status never claim '0 B' about a
    folder that (maybe) holds data."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_08_01", "10_00_00", robot="R1")
    d = r1 / "2024_01_01" / "10_00_00"           # dated, but no backup.json
    d.mkdir(parents=True)
    (d / "SUMMARY.DG").write_text("x" * 321, encoding="utf-8")
    _sidecar(r1, "rid-1", "R1")
    library.scan_library_root(root)

    res = library.stage_backups(
        [{"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"}], 90, 1)
    assert len(res["staged"]) == 1
    assert res["staged"][0]["bytes"] == 321      # measured, not the record's 0
    assert library.staging_status(root)["bytes"] == 321


def test_stage_to_custom_folder(monkeypatch, tmp_path):
    """staging_mode=folder parks the mirror (and its staged.log) anywhere
    OUTSIDE the library - a second disk actually reclaims this one."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    custom = tmp_path / "elsewhere" / "parked"
    settings.set_value("staging_mode", "folder")
    settings.set_value("staging_dir", str(custom))

    res = library.stage_backups(
        [{"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"}], 90, 2)
    assert len(res["staged"]) == 1
    assert (custom / "P" / "L" / "R1" / "2024_05_01" / "10_00_00" / "SUMMARY.DG").is_file()
    assert (custom / "staged.log").is_file()
    assert not (root / "_staged").exists()
    s = library.staging_status(root)
    assert s["mode"] == "folder" and s["count"] == 1 and s["path"] == str(custom)


def test_stage_refuses_misconfigured_folder(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)
    pick = [{"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"}]

    settings.set_value("staging_mode", "folder")
    settings.set_value("staging_dir", "")                    # never picked
    with pytest.raises(ValueError, match="no staging folder picked"):
        library.stage_backups(pick, 90, 2)

    settings.set_value("staging_dir", str(root / "Parked"))  # inside the library
    with pytest.raises(ValueError, match="inside the library"):
        library.stage_backups(pick, 90, 2)
    assert (r1 / "2024_05_01" / "10_00_00").is_dir()         # nothing moved


def test_stage_to_recycle_bin(monkeypatch, tmp_path):
    """Recycle mode hands snapshots to the shell (FOF_ALLOWUNDO) - no _staged
    folder, no staged.log (the bin is the shell's own ledger)."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    settings.set_value("staging_mode", "recycle")
    monkeypatch.setattr(library, "_volume_recycles", lambda p: True)
    sent = []

    def fake_recycle(paths):
        for p in paths:
            sent.append(str(p))
            shutil.rmtree(p)
    monkeypatch.setattr(library, "_send_to_recycle", fake_recycle)

    res = library.stage_backups([
        {"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"},
        {"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"},
    ], 90, 2)
    assert len(res["staged"]) == 2
    assert all(r.get("recycled") for r in res["staged"])
    assert len(sent) == 2
    assert not (root / "_staged").exists()
    assert len(library.get_robot("rid-1")["backups"]) == 2   # index rebuilt
    assert res["staging"]["mode"] == "recycle"
    assert res["staging"]["count"] == 0                      # the bin keeps its own books


def test_stage_recycle_refused_without_a_bin(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)
    settings.set_value("staging_mode", "recycle")
    monkeypatch.setattr(library, "_volume_recycles", lambda p: False)

    with pytest.raises(ValueError, match="no recycle bin"):
        library.stage_backups(
            [{"robot_id": "rid-1", "taken": "2024-05-01T10:00:00"}], 90, 2)
    assert (r1 / "2024_05_01" / "10_00_00").is_dir()         # source untouched


def test_lib_stage_claims_without_rescan(monkeypatch, tmp_path):
    """The api op claims its own tree delta (the relocate pattern): after a
    stage, lib_list is a cache hit — no full rescan is ever paid for it."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    api = Api()
    api._set_lib_sig(root, library.settled_signature(root))

    calls = []
    monkeypatch.setattr(library, "scan_library_root",
                        lambda r, progress=None, on_entry=None: calls.append(r))

    r = api.lib_stage([{"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"}])
    assert r["ok"] and r["data"]["total"] == 1   # a job stub, not a result
    api._stage_thread.join(timeout=30)
    assert not api._stage_thread.is_alive()
    prog = api.lib_stage_progress()
    assert prog["ok"] and prog["data"]["running"] is False
    assert len(prog["data"]["result"]["staged"]) == 1
    assert prog["data"]["done"] == prog["data"]["total"] == 1
    listed = api.lib_list()
    assert listed["ok"]
    assert not listed["data"].get("scanning")          # served from cache
    assert {e["robot"] for e in listed["data"]["robots"]} == {"R1"}
    assert calls == []                                 # the claim held


def test_lib_stage_refuses_mid_run(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)
    api = Api()
    monkeypatch.setattr(api, "_backups_active", lambda: True)

    r = api.lib_stage([{"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"}])
    assert r["ok"] is False
    assert (r1 / "2024_01_01" / "10_00_00").is_dir()   # nothing moved
