"""scan_library_root: the backup folder tree is the source of truth; library.json
is a local overlay. Fully synthetic (a SUMMARY.DG marks each snapshot a backup),
so nothing here needs SampleBackup or a network."""
import json
import os
import shutil

from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)


def _make_robot(root, plant, line, robot, snaps, *, rid="", notes="", ips=None, mirror=False):
    """snaps = [(date, time, mtime)]. Writes SUMMARY.DG + backup.json + notes.txt
    per snapshot, a robot.json at the robot folder, and optionally a Latest/
    mirror whose backup.json claims to be the newest (to prove it's excluded)."""
    base = root / plant / line / robot
    for i, (date, time, ts) in enumerate(snaps):
        d = base / date / time
        d.mkdir(parents=True, exist_ok=True)
        (d / "SUMMARY.DG").write_text("x", encoding="utf-8")
        (d / "backup.json").write_text(json.dumps({
            "robot": robot, "line": line, "plant": plant,
            "taken": date.replace("_", "-") + "T" + time.replace("_", ":"),
            "type": "all of above", "files": 3 + i, "bytes": 100, "source": "ftp",
        }), encoding="utf-8")
        (d / "notes.txt").write_text("snap note " + time + "\n", encoding="utf-8")
        os.utime(d, (ts, ts))
    (base / "robot.json").write_text(json.dumps({
        "schema": 1, "id": rid, "plant": plant, "line": line, "robot": robot,
        "model": "", "f_number": "", "ips": ips or [],
        "ftp": {"user": "", "passive": True}, "notes": notes,
    }), encoding="utf-8")
    if mirror:
        md = root / plant / line / "Latest" / robot
        md.mkdir(parents=True, exist_ok=True)
        (md / "SUMMARY.DG").write_text("x", encoding="utf-8")
        (md / "backup.json").write_text(json.dumps(
            {"robot": robot, "line": line, "plant": plant, "taken": "2099-01-01T00:00:00"}),
            encoding="utf-8")
    return base


def test_scan_builds_history_newest_first_excluding_mirror(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "PlantA", "Line1", "ROBO1", [
        ("2026_01_01", "12_00_00", 1_600_000_000),
        ("2026_02_02", "09_30_00", 1_700_000_000),
    ], rid="id-robo1", notes="binpicker notes", ips=["1.2.3.4"], mirror=True)

    robots = library.scan_library_root(root)["robots"]
    assert len(robots) == 1
    e = robots[0]
    assert (e["robot"], e["line"], e["plant"]) == ("ROBO1", "Line1", "PlantA")
    assert e["id"] == "id-robo1"               # id carried from robot.json
    assert e["notes"] == "binpicker notes"
    assert e["ips"] == ["1.2.3.4"]
    assert len(e["backups"]) == 2              # two dated snaps; Latest/ mirror excluded
    assert e["backups"][0]["taken"] == "2026-02-02T09:30:00"   # newest first
    assert e["backups"][0]["note"] == "snap note 09_30_00"     # per-snap notes.txt
    assert e["latest_path"].endswith(os.path.join("2026_02_02", "09_30_00"))
    assert "Latest" not in e["latest_path"]    # never the mirror


def test_rescan_preserves_overlay_edits(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    library.scan_library_root(root)

    e = library.list_robots()["robots"][0]
    library.update_robot(e["id"], {"robot": "R1-RENAMED", "notes": "field note", "hidden": True})

    data = library.scan_library_root(root)     # a second scan must not clobber edits
    assert len(data["robots"]) == 1
    e2 = data["robots"][0]
    assert e2["robot"] == "R1-RENAMED"         # overlay/user wins for identity
    assert e2["notes"] == "field note"
    assert e2["hidden"] is True
    assert len(e2["backups"]) == 1             # disk still authoritative for history


def test_vanished_folder_dropped_on_rescan(monkeypatch, tmp_path):
    """Files are law: deleting a robot's folder in Explorer deletes the robot -
    the next scan simply reflects the tree."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    _make_robot(root, "P", "L", "R2", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-2")
    library.scan_library_root(root)

    shutil.rmtree(root / "P" / "L" / "R2")     # someone deleted R2 in Explorer
    by = {e["robot"]: e for e in library.scan_library_root(root)["robots"]}
    assert set(by) == {"R1"}                   # R2 gone, exactly as the tree says
    assert by["R1"]["stale"] is False


def test_unreachable_root_serves_last_known_library_stale(monkeypatch, tmp_path):
    """An offline network drive is NOT the same as deleted folders: the last
    known library is kept (marked stale), never wiped."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    library.scan_library_root(root)

    shutil.rmtree(root)                        # the whole root vanishes (drive unplugged)
    data = library.scan_library_root(root)
    assert [e["robot"] for e in data["robots"]] == ["R1"]   # nothing dropped
    assert data["robots"][0]["stale"] is True


def test_root_change_drops_old_roots_entries(monkeypatch, tmp_path):
    """Pointing the app at a new library folder shows THAT folder's contents -
    the previous root's robots do not tag along (the v0.97 field bug)."""
    _iso(monkeypatch, tmp_path)
    root_a = tmp_path / "libA"
    _make_robot(root_a, "P", "L", "OLDBOT", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-a")
    settings.set_value("library_root", str(root_a))
    library.scan_library_root(root_a)
    assert [e["robot"] for e in library.list_robots()["robots"]] == ["OLDBOT"]

    root_b = tmp_path / "libB"
    _make_robot(root_b, "P", "L", "NEWBOT", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-b")
    settings.set_value("library_root", str(root_b))            # user re-points the library
    data = library.scan_library_root(root_b)
    assert [e["robot"] for e in data["robots"]] == ["NEWBOT"]  # old entries gone with their root


def test_sidecar_only_folder_discovered_as_robot(monkeypatch, tmp_path):
    """A robot folder holding just a robot.json (no snapshots yet - e.g. a
    discovery-added robot awaiting its first backup) is a real robot: it scans
    in with its identity + IP and an empty history."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    d = root / "P" / "L" / "FRESH"
    d.mkdir(parents=True)
    (d / "robot.json").write_text(json.dumps({
        "schema": 1, "id": "rid-fresh", "plant": "P", "line": "L", "robot": "FRESH",
        "model": "", "f_number": "", "ips": ["10.9.9.9"],
        "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1
    e = data["robots"][0]
    assert e["robot"] == "FRESH" and e["id"] == "rid-fresh"
    assert e["ips"] == ["10.9.9.9"]            # the IP lives on disk, not just this machine
    assert e["backups"] == [] and e["latest_path"] == ""
    assert e["stale"] is False                 # 'no backup yet', not 'missing'


def _settled_signature(root):
    """NTFS flushes directory-mtime updates lazily; the first walk after writes
    can observe pre-flush values. Walk until two consecutive reads agree — the
    production watcher absorbs this settle via its debounce."""
    s = library.scan_signature(root)
    for _ in range(5):
        s2 = library.scan_signature(root)
        if s2 == s:
            return s
        s = s2
    return s


def test_scan_signature_tracks_tree_changes(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")

    s1 = _settled_signature(root)
    assert s1 and library.scan_signature(root) == s1            # stable when unchanged
    (root / "P" / "L" / "R2").mkdir(parents=True)               # Explorer copy begins
    s2 = _settled_signature(root)
    assert s2 != s1                                             # ...and the signature sees it
    assert library.scan_signature(tmp_path / "nope") == ""      # unreachable root


def test_sidecar_round_trip_carries_identity_to_a_fresh_library(monkeypatch, tmp_path):
    """The portability promise: add a robot -> robot.json is written (no password);
    wipe the index (a coworker's machine) and a scan rebuilds the same robot."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    snap = root / "P" / "L" / "R7" / "2026_03_03" / "08_00_00"
    snap.mkdir(parents=True)
    (snap / "SUMMARY.DG").write_text("x", encoding="utf-8")
    (snap / "backup.json").write_text(json.dumps(
        {"robot": "R7", "line": "L", "plant": "P", "taken": "2026-03-03T08:00:00"}),
        encoding="utf-8")
    robot_dir = root / "P" / "L" / "R7"

    e = library.add_robot({"robot": "R7", "line": "L", "plant": "P",
                           "notes": "travels with the folder", "ips": ["10.0.0.7"],
                           "ftp": {"user": "fanuc", "passive": True},
                           "history_root": str(robot_dir)})
    rj = json.loads((robot_dir / "robot.json").read_text(encoding="utf-8"))
    assert rj["id"] == e["id"] and rj["notes"] == "travels with the folder"
    assert "passwd" not in rj and "password" not in json.dumps(rj)   # never a password
    assert rj["ftp"]["user"] == "fanuc"

    (settings.app_dir() / "library.json").unlink()      # a fresh machine
    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1
    e2 = data["robots"][0]
    assert e2["id"] == e["id"]                  # id travelled via robot.json
    assert e2["robot"] == "R7"
    assert e2["notes"] == "travels with the folder"
    assert e2["ftp"]["user"] == "fanuc"
    assert len(e2["backups"]) == 1


def test_scan_missing_root_is_a_noop(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    data = library.scan_library_root(tmp_path / "does_not_exist")
    assert data["robots"] == []


def test_set_hidden_survives_rescan(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    library.set_hidden(e["id"], True)
    assert library.get_robot(e["id"])["hidden"] is True
    data = library.scan_library_root(root)          # rescan keeps the overlay flag
    assert data["robots"][0]["hidden"] is True


def test_lib_open_augments_with_history(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    snap = tmp_path / "lib" / "P" / "L" / "R1" / "2026_01_01" / "12_00_00"
    snap.mkdir(parents=True)
    (snap / "SUMMARY.DG").write_text("x", encoding="utf-8")
    e = library.add_robot({"robot": "R1", "latest_path": str(snap),
                           "backups": [{"path": str(snap), "taken": "2026-01-01T12:00:00"}]})
    m = Api().lib_open(e["id"], "latest")["data"]
    assert m["robot_id"] == e["id"]
    assert m["current_path"] == str(snap)
    assert len(m["backups"]) == 1


# -- api: files-are-law endpoints -------------------------------------------------

def test_lib_add_materializes_folder_and_survives_rebuild(monkeypatch, tmp_path):
    """Adding a robot (manual/discovery) creates its real folder + robot.json
    immediately, so the robot AND its IP survive a registry rebuild - the
    new-line workflow is durable from second one."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    root.mkdir()
    settings.set_value("library_root", str(root))

    e = Api().lib_add({"robot": "NEWLINE1", "plant": "P", "line": "L9",
                       "ips": ["10.1.2.3"]})["data"]
    d = root / "P" / "L9" / "NEWLINE1"
    assert d.is_dir()                                              # folder exists NOW
    rj = json.loads((d / "robot.json").read_text(encoding="utf-8"))
    assert rj["id"] == e["id"] and rj["ips"] == ["10.1.2.3"]

    (settings.app_dir() / "library.json").unlink()                 # fresh machine / wiped cache
    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1
    e2 = data["robots"][0]
    assert e2["robot"] == "NEWLINE1" and e2["ips"] == ["10.1.2.3"]
    assert e2["id"] == e["id"]


def test_lib_bulk_add_materializes_folders(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    root.mkdir()
    settings.set_value("library_root", str(root))

    drafts = [{"robot": "D1", "ips": ["10.0.0.1"]}, {"robot": "D2", "ips": ["10.0.0.2"]}]
    res = Api().lib_bulk_add(drafts, "P", "L")["data"]
    assert len(res["added"]) == 2
    assert (root / "P" / "L" / "D1" / "robot.json").is_file()
    assert (root / "P" / "L" / "D2" / "robot.json").is_file()

    settings.set_value("library_root", str(tmp_path / "gone"))     # unreachable root
    bad = Api().lib_bulk_add([{"robot": "D3"}], "P", "L")
    assert bad["ok"] is False and bad["error"]["code"] == "BAD_PATH"


def test_lib_list_rescans_when_tree_changes(monkeypatch, tmp_path):
    """The Explorer-add field bug: a folder copied into the library shows up on
    the next listing - no manual rescan, no app restart."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    api = Api()
    assert [e["robot"] for e in api.lib_list()["data"]["robots"]] == ["R1"]

    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")
    names = sorted(e["robot"] for e in api.lib_list()["data"]["robots"])
    assert names == ["R1", "R2"]                                   # picked up automatically

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root", lambda r: calls.append(r) or real(r))
    api.lib_list()                                                 # unchanged tree
    assert calls == []                                             # -> served from cache, no scan


def test_scan_reports_absorbed_copies(monkeypatch, tmp_path):
    """A copied folder carrying the original's robot.json folds into that robot
    by identity (the anti-duplicate design). The scan must SAY so — silent
    absorption reads as 'my copied folder never showed up' (the WOWOW case)."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "DHB01", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    # a copy of the robot dropped under a DIFFERENT line, robot.json included
    copy = _make_robot(root, "P", "WOWOW", "R1", [
        ("2026_06_25", "16_12_02", 1_700_000_000),
        ("2026_06_30", "08_55_42", 1_700_100_000),
    ], rid="rid-1")
    assert (copy / "robot.json").is_file()

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1                             # no twin spawned
    e = data["robots"][0]
    assert e["line"] == "DHB01"                                 # the original wins
    assert len(e["backups"]) == 3                               # copy's snapshots joined
    assert data["scan_absorbed"] == [{"robot": "R1", "count": 2}]

    # report-only: the toast belongs to THIS scan — never cached to disk
    cache = json.loads((settings.app_dir() / "library.json").read_text(encoding="utf-8"))
    assert "scan_absorbed" not in cache


def test_scan_absorbed_absent_when_nothing_absorbed(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    data = library.scan_library_root(root)
    assert "scan_absorbed" not in data


def test_list_backup_jobs_endpoint(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)

    class _Job:
        def __init__(self, jid, status):
            self._s = {"id": jid, "status": status, "host": "10.0.0.9",
                       "robot": "R1", "total": 10, "done": 4}

        def snapshot(self):
            return dict(self._s)

    api = Api()
    api._jobs["j1"] = _Job("j1", "downloading")
    api._jobs["j2"] = _Job("j2", "done")
    res = api.list_backup_jobs()["data"]
    by = {j["id"]: j for j in res["jobs"]}
    assert set(by) == {"j1", "j2"}
    assert by["j1"]["status"] == "downloading" and by["j1"]["robot"] == "R1"


def test_watch_step_debounce(monkeypatch, tmp_path):
    """The watcher fires once, after a burst of changes settles: baseline tick
    never fires; each change arms; the first quiet tick fires."""
    from backupviewer.api import _watch_step
    last, pending, fire = _watch_step(None, False, "s1")           # boot baseline
    assert (last, pending, fire) == ("s1", False, False)
    last, pending, fire = _watch_step(last, pending, "s2")         # change seen
    assert (last, pending, fire) == ("s2", True, False)
    last, pending, fire = _watch_step(last, pending, "s3")         # still churning
    assert (last, pending, fire) == ("s3", True, False)
    last, pending, fire = _watch_step(last, pending, "s3")         # quiet -> fire once
    assert (last, pending, fire) == ("s3", False, True)
    last, pending, fire = _watch_step(last, pending, "s3")         # stays quiet -> silent
    assert (last, pending, fire) == ("s3", False, False)
