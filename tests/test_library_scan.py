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


def _make_robot(root, plant, line, robot, snaps, *, rid="", notes="", ips=None, mirror=False,
                ftp=None):
    """snaps = [(date, time, mtime)]. Writes SUMMARY.DG + backup.json + notes.txt
    per snapshot, a robot.json at the robot folder, and optionally a Latest/
    mirror whose backup.json claims to be the newest (to prove it's excluded).
    The sidecar is deliberately LEGACY schema-1 (identity fields included) —
    the shape still sitting in every field tree, which the scan must ignore."""
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
        "ftp": ftp or {"user": "", "passive": True}, "notes": notes,
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


def test_scan_attaches_camera_snapshot(monkeypatch, tmp_path):
    """A camera snapshot carries NO FANUC files (.LS/.VA/...) - just a CAM<n>/
    tree + backup.json. The scan must still see it as a backup (the field bug:
    'the program doesn't see the actual backup'), attach it to the entry, and
    carry the camera device_type through."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    snap = root / "Test Cell" / "Test1" / "192.0.2.117" / "2026_07_14" / "17_20_24"
    (snap / "CAM1" / "cv-x" / "setting").mkdir(parents=True)
    (snap / "CAM1" / "cv-x" / "setting" / "env.dat").write_text("blob", encoding="utf-8")
    (snap / "backup.json").write_text(json.dumps({
        "robot": "192.0.2.117", "line": "Test1", "plant": "Test Cell",
        "taken": "2026-07-14T17:20:24", "type": "keyence cv-x setting",
        "device_type": "camera-keyence", "files": 1, "bytes": 4, "source": "ftp",
    }), encoding="utf-8")
    (snap / "notes.txt").write_text("cvx pull\n", encoding="utf-8")

    robots = library.scan_library_root(root)["robots"]
    assert len(robots) == 1
    e = robots[0]
    assert (e["robot"], e["line"], e["plant"]) == ("192.0.2.117", "Test1", "Test Cell")
    assert e["device_type"] == "camera-keyence"       # carried from backup.json
    assert len(e["backups"]) == 1
    assert e["latest_path"].endswith(os.path.join("2026_07_14", "17_20_24"))


def test_rescan_preserves_config_but_identity_follows_disk(monkeypatch, tmp_path):
    """Files are law: the folder's location/name IS the identity, so a rescan
    reverts a registry-only rename (real renames go through relocate_robot,
    which moves the folder). User CONFIG (notes, hidden) survives, and the
    registry-only name is kept as an alias."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    library.scan_library_root(root)

    e = library.list_robots()["robots"][0]
    library.update_robot(e["id"], {"robot": "R1-RENAMED", "notes": "field note", "hidden": True})

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1
    e2 = data["robots"][0]
    assert e2["robot"] == "R1"                 # the folder name wins
    assert {(a["robot"]) for a in e2["aliases"]} >= {"R1-RENAMED"}
    assert e2["notes"] == "field note"
    assert e2["hidden"] is True
    assert len(e2["backups"]) == 1             # disk authoritative for history


def test_rescan_adopts_sidecar_ftp_passive(monkeypatch, tmp_path):
    """A sidecar recording passive=False must survive a RESCAN, not just a
    merge: when _apply_disk adopts the sidecar's ftp user it carries the
    passive flag too, unless the overlay already recorded one - the same
    rule _merge_pair applies (the pre-fix drift lost the flag on rescan)."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)],
                rid="rid-1", ftp={"user": "bob", "passive": False})
    # a pre-ftp-era overlay entry: no ftp recorded at all (load() never
    # normalizes, so this shape genuinely occurs in old library.json files)
    data = library.load()
    data["robots"].append({"id": "rid-1", "plant": "P", "line": "L", "robot": "R1",
                           "notes": "keep me"})
    library.save(data)

    e = library.scan_library_root(root)["robots"][0]
    assert e["notes"] == "keep me"             # matched the overlay entry, not recreated
    assert e["ftp"]["user"] == "bob"
    assert e["ftp"]["passive"] is False        # the rescan carried the flag

    # but an overlay that HAS recorded a passive preference keeps it
    data = library.load()
    data["robots"][0]["ftp"] = {"user": "", "passive": True}
    library.save(data)
    e = library.scan_library_root(root)["robots"][0]
    assert e["ftp"]["user"] == "bob"           # user still adopted
    assert e["ftp"]["passive"] is True         # overlay's own flag never clobbered


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


def test_stale_sidecar_identity_loses_to_folder_location(monkeypatch, tmp_path):
    """A copied-in tree can carry legacy robot.json / backup.json files claiming
    the plant/line they lived in years ago. The folder's LOCATION is the identity
    (files are law) - the sidecar supplies config (id/IPs) ONLY and its stale
    claim is ignored outright (not even kept as an alias: a claim is not a
    recorded rename). The robot displays where its folder actually sits (the
    'my robots scattered outside the plant I copied them into' field bug)."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    # folder lives at NewPlant/L9/R1; sidecar+backup.json claim OldPlant/L1
    _make_robot(root, "NewPlant", "L9", "R1",
                [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    d = root / "NewPlant" / "L9" / "R1"
    (d / "robot.json").write_text(json.dumps({
        "schema": 1, "id": "rid-1", "plant": "OldPlant", "line": "L1", "robot": "R1",
        "ips": ["10.1.1.5"], "ftp": {"user": "", "passive": True}, "notes": ""}),
        encoding="utf-8")
    bj = d / "2026_01_01" / "12_00_00" / "backup.json"
    meta = json.loads(bj.read_text(encoding="utf-8"))
    meta.update({"plant": "OldPlant", "line": "L1"})
    bj.write_text(json.dumps(meta), encoding="utf-8")

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1
    e = data["robots"][0]
    assert (e["plant"], e["line"], e["robot"]) == ("NewPlant", "L9", "R1")
    assert e["id"] == "rid-1"                  # sidecar still supplies id + config
    assert e["ips"] == ["10.1.1.5"]
    # the stale claim is NOT imported as an alias — it never becomes match bait
    assert ("OldPlant", "L1") not in {(a["plant"], a["line"]) for a in e["aliases"]}


def test_schema2_sidecar_presence_marks_robot_at_any_depth(monkeypatch, tmp_path):
    """A schema-2 sidecar has NO identity fields, so the skeleton scan keys on
    the FILE's presence: a folder carrying robot.json is a robot wherever it
    sits — normal depth, and the legacy layouts that park robots at line depth
    (<root>/<line>/<robot>) or even at the root. Identity comes from the path."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    for folder in [root / "P" / "L" / "FRESH2",       # normal plant/line/robot depth
                   root / "LEGACYLINE" / "OLDBOT",    # legacy <root>/<line>/<robot>
                   root / "ROOTBOT"]:                 # parked at the root itself
        folder.mkdir(parents=True)
        (folder / "robot.json").write_text(json.dumps({
            "schema": 2, "id": "rid-" + folder.name.lower(), "ips": ["10.2.2.2"],
            "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")

    data = library.scan_library_root(root)
    by = {e["robot"]: e for e in data["robots"]}
    assert set(by) == {"FRESH2", "OLDBOT", "ROOTBOT"}
    assert (by["FRESH2"]["plant"], by["FRESH2"]["line"]) == ("P", "L")
    assert (by["OLDBOT"]["plant"], by["OLDBOT"]["line"]) == ("", "LEGACYLINE")
    assert (by["ROOTBOT"]["plant"], by["ROOTBOT"]["line"]) == ("", "")
    assert by["FRESH2"]["id"] == "rid-fresh2"          # id + config still carried
    assert by["FRESH2"]["ips"] == ["10.2.2.2"]
    # none of them misread as plant/line structure
    assert data["empty_folders"]["plants"] == []
    assert data["empty_folders"]["lines"] == []


def test_empty_folder_skeleton_surfaces(monkeypatch, tmp_path):
    """The tree the user builds in Explorer IS the library: an empty folder at
    root is a plant, an empty folder inside a plant is a line, and a folder at
    robot depth is a robot with no backups yet - even with nothing inside."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    (root / "EmptyPlant").mkdir(parents=True)
    (root / "P" / "EmptyLine").mkdir()
    (root / "P" / "L" / "NEWBOT").mkdir()      # empty robot folder, no sidecar

    data = library.scan_library_root(root)
    by = {e["robot"]: e for e in data["robots"]}
    assert set(by) == {"R1", "NEWBOT"}
    nb = by["NEWBOT"]
    assert (nb["plant"], nb["line"]) == ("P", "L")
    assert nb["backups"] == [] and nb["latest_path"] == "" and nb["stale"] is False
    assert nb["id"]                            # gets a usable id for the UI
    assert data["empty_folders"]["plants"] == ["EmptyPlant"]
    assert data["empty_folders"]["lines"] == [{"plant": "P", "line": "EmptyLine"}]

    # empty-robot entries keep the SAME id across rescans (matched by identity)
    data2 = library.scan_library_root(root)
    by2 = {e["robot"]: e for e in data2["robots"]}
    assert by2["NEWBOT"]["id"] == nb["id"]

    # skeleton never invents robots out of dated/mirror/staging dirs
    (root / "P" / "L" / "Latest").mkdir()
    (root / "P" / "L2").mkdir()
    (root / "P" / "L2" / "2026_01_01").mkdir()
    data3 = library.scan_library_root(root)
    assert {e["robot"] for e in data3["robots"]} == {"R1", "NEWBOT"}


def test_same_names_in_two_plants_stay_separate(monkeypatch, tmp_path):
    """ERBU-style short names (010R01) repeat in every line, and line names can
    repeat across plants - two folders in two RECORDED plants are two robots."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "PlantA", "L1", "010R01",
                [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-a")
    _make_robot(root, "PlantB", "L1", "010R01",
                [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-b")

    data = library.scan_library_root(root)
    plants = sorted((e["plant"], e["robot"]) for e in data["robots"])
    assert plants == [("PlantA", "010R01"), ("PlantB", "010R01")]


def _settled_signature(root):
    """NTFS flushes directory-mtime updates lazily; the first walk after writes
    can observe pre-flush values. The production settle loop (promoted for the
    signature-claim path) is the one implementation of that wait."""
    return library.settled_signature(root)


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
    # schema 3: identity lives in the tree, NEVER in the sidecar — nothing to go
    # stale when the folder is later moved/renamed in Explorer
    assert rj["schema"] == 3
    assert rj["device_type"] == "robot"          # default; cameras record camera-mtx
    assert "plant" not in rj and "line" not in rj and "robot" not in rj

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


def test_set_favorite_survives_rescan(monkeypatch, tmp_path):
    """favorite mirrors hidden: overlay-only, preserved across a rescan, and
    NEVER written into the tree — a star click must not touch robot.json
    (that write dirties the tree and turns the next lib_list into a rescan)."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]
    assert e["favorite"] is False                   # normalized default

    sidecar = root / "P" / "L" / "R1" / "robot.json"
    before = sidecar.read_text(encoding="utf-8")
    library.set_favorite(e["id"], True)
    assert sidecar.read_text(encoding="utf-8") == before   # tree untouched
    assert library.get_robot(e["id"])["favorite"] is True
    data = library.scan_library_root(root)          # rescan keeps the overlay flag
    assert data["robots"][0]["favorite"] is True

    library.set_favorite(e["id"], False)
    assert library.get_robot(e["id"])["favorite"] is False


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

    # a brand-new library (fresh machine / first line: the root folder was never
    # created) is BUILT by the add, not refused — the field new-line fix
    fresh = tmp_path / "brand" / "new" / "root"
    settings.set_value("library_root", str(fresh))
    ok = Api().lib_bulk_add([{"robot": "D3", "ips": ["10.0.0.3"]}], "P2", "L9")["data"]
    assert len(ok["added"]) == 1
    assert (fresh / "P2" / "L9" / "D3" / "robot.json").is_file()   # whole tree created


def _join_scan(api, timeout=30):
    """Wait out the background library scan, if one is running (slice B: a
    changed tree serves the cache instantly and scans behind it)."""
    t = api._scan_thread
    if t is not None:
        t.join(timeout=timeout)
    assert not api._scan_alive()


def test_lib_list_rescans_when_tree_changes(monkeypatch, tmp_path):
    """The Explorer-add field bug: a folder copied into the library shows up on
    the next listing - no manual rescan, no app restart. Since slice B the
    changed-tree listing answers cache-first (scanning:true) and the fresh
    truth follows from the background scan."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    api = Api()
    assert [e["robot"] for e in api.lib_list()["data"]["robots"]] == ["R1"]

    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")
    out = api.lib_list()["data"]
    assert out.get("scanning") is True                             # cache now, scan behind it
    assert [e["robot"] for e in out["robots"]] == ["R1"]
    _join_scan(api)
    names = sorted(e["robot"] for e in api.lib_list()["data"]["robots"])
    assert names == ["R1", "R2"]                                   # picked up automatically

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root", lambda r: calls.append(r) or real(r))
    api.lib_list()                                                 # unchanged tree
    assert calls == []                                             # -> served from cache, no scan


def test_lib_list_persisted_sig_skips_boot_scan(monkeypatch, tmp_path):
    """The 1000-robot boot fix: the tree signature survives restarts, so a fresh
    process (a new Api) on an UNCHANGED tree serves the cached library without
    the full rescan. Any tree change still takes the scan path."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    assert [e["robot"] for e in Api().lib_list()["data"]["robots"]] == ["R1"]   # first run scans

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root",
                        lambda r, progress=None, on_entry=None:
                        calls.append(r) or real(r, progress=progress, on_entry=on_entry))
    api2 = Api()                                       # "next boot"
    assert [e["robot"] for e in api2.lib_list()["data"]["robots"]] == ["R1"]
    assert calls == []                                 # unchanged tree -> no scan at all

    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")
    api3 = Api()                                       # another boot, tree changed meanwhile
    out = api3.lib_list()["data"]
    assert out.get("scanning") is True                 # last-known cache served instantly
    assert [e["robot"] for e in out["robots"]] == ["R1"]
    _join_scan(api3)
    names = sorted(e["robot"] for e in api3.lib_list()["data"]["robots"])
    assert names == ["R1", "R2"]
    assert len(calls) == 1                             # -> scanned exactly once, off-thread


def test_lib_list_persisted_sig_rejected_when_root_or_cache_differ(monkeypatch, tmp_path):
    """The persisted signature is a shortcut ONLY for the exact root it was
    stamped for, with the cache still present — a wiped library.json or a
    switched root must rescan, never serve stale or empty."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    Api().lib_list()                                   # scan + persist the signature

    # hand-wiped cache: the sig still matches, but an empty library.json must
    # NOT be served as "no robots" — the seed is refused and the tree rescanned
    (settings.app_dir() / "library.json").unlink()
    assert [e["robot"] for e in Api().lib_list()["data"]["robots"]] == ["R1"]

    # switched root: the stamp doesn't transfer
    other = tmp_path / "lib2"
    _make_robot(other, "P", "L", "RX", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-x")
    settings.set_value("library_root", str(other))
    assert [e["robot"] for e in Api().lib_list()["data"]["robots"]] == ["RX"]


def test_lib_list_background_scan_settles_and_pushes(monkeypatch, tmp_path):
    """Slice B end-to-end: changed tree -> the cached library IMMEDIATELY with
    scanning:true, the rescan on a background thread, a 'library-updated' push
    when it lands, and the follow-up listing served from the fresh cache with
    no scan at all."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    api = Api()
    api.lib_list()                                     # cold path: nothing cached -> blocks
    api._set_lib_sig(root, _settled_signature(root))

    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")
    _settled_signature(root)

    pushed = []

    class _Win:
        def evaluate_js(self, js):
            pushed.append(js)

    api._window = _Win()
    out = api.lib_list()["data"]
    assert out.get("scanning") is True
    assert [e["robot"] for e in out["robots"]] == ["R1"]     # last-known, instantly

    _join_scan(api)
    assert any("library-updated" in js for js in pushed)     # the settle push fired

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root",
                        lambda r, progress=None, on_entry=None:
                        calls.append(r) or real(r, progress=progress, on_entry=on_entry))
    out2 = api.lib_list()["data"]
    assert sorted(e["robot"] for e in out2["robots"]) == ["R1", "R2"]
    assert out2.get("scanning") is None                      # settled: plain cache
    assert calls == []                                       # ...and no scan


def test_lib_list_stays_quiet_while_backups_run(monkeypatch, tmp_path):
    """A running backup writes thousands of files into the watched tree — a
    listing during the run serves the cache without kicking a scan under it
    (the watcher pauses for the same reason; the run's end refreshes)."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    api = Api()
    api.lib_list()
    api._set_lib_sig(root, _settled_signature(root))
    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")

    class _Job:
        def snapshot(self):
            return {"status": "downloading", "run_id": "run-1"}

    api._jobs["j1"] = _Job()
    out = api.lib_list()["data"]
    assert [e["robot"] for e in out["robots"]] == ["R1"]     # cache, honestly stale
    assert out.get("scanning") is None                       # and no scan was kicked
    assert api._scan_thread is None


def test_scan_runner_retries_until_tree_quiesces(monkeypatch, tmp_path):
    """A tree that MOVED while the walk ran (rename mid-scan, copy landing)
    must not have its possibly-stale merge stamped as the current baseline:
    the runner walks again until start and end signatures agree."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    settings.set_value("library_root", str(tmp_path / "lib"))
    api = Api()

    scans = []
    monkeypatch.setattr(api, "_scan_with_progress", lambda root: scans.append(root))
    sigs = iter(["s1", "s2"])                 # start of attempt 1, start of attempt 2
    monkeypatch.setattr(library, "scan_signature", lambda root: next(sigs))
    monkeypatch.setattr(library, "settled_signature", lambda root, tries=5: "s2")

    api._scan_runner(str(tmp_path / "lib"))
    assert len(scans) == 2                    # attempt 1 unstamped, attempt 2 quiesced
    assert api._lib_sig == "s2"               # only the settled result became the baseline


def test_metadata_ops_serve_cache_without_rescan(monkeypatch, tmp_path):
    """Slice A of the library overhaul: an app-initiated metadata change
    (rename/relocate, note or IP edit, camera link) already updates
    library.json in place — the sidecar/folder writes it makes must not cost
    the next lib_list a full rescan (the rename-blanks-the-library bug).
    External Explorer changes still take the scan path: files are law."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    # NOT "CAM<n>": that exact shape is the camera-snapshot wrapper marker
    # (session.looks_like_backup) — a robot folder named CAM1 makes its whole
    # LINE folder read as one backup root and swallows its siblings
    cam = _make_robot(root, "P", "L", "MXCAM1", [("2026_01_02", "10_00_00", 1_600_100_000)],
                      rid="rid-c")
    sc = json.loads((cam / "robot.json").read_text(encoding="utf-8"))
    sc["device_type"] = "camera-mtx"
    (cam / "robot.json").write_text(json.dumps(sc), encoding="utf-8")
    settings.set_value("library_root", str(root))
    api = Api()
    api.lib_list()                                     # baseline scan
    api._set_lib_sig(root, _settled_signature(root))   # de-flake: settled baseline

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root",
                        lambda r, progress=None, on_entry=None:
                        calls.append(r) or real(r, progress=progress, on_entry=on_entry))

    # Each op is claim-tested from a PROVEN-clean baseline (settled, stored):
    # in production a claim that misses because NTFS flushed an earlier op's
    # mtimes late just costs one background rescan (fail-safe by design), but
    # here that timing would read as a test flake, not a finding.
    def baseline():
        api._set_lib_sig(root, _settled_signature(root))

    api.lib_update("rid-1", {"notes": "gripper rebuilt", "ips": ["192.0.2.10"]})
    assert calls == []                                 # the op itself never scans
    e1 = next(e for e in api.lib_list()["data"]["robots"] if e["id"] == "rid-1")
    assert e1["notes"] == "gripper rebuilt"            # served from the updated cache
    assert calls == []                                 # ...without a rescan

    baseline()
    api.lib_link_camera("rid-c", "rid-1")
    linked = next(e for e in api.lib_list()["data"]["robots"] if e["id"] == "rid-c")
    assert linked["linked_robot_id"] == "rid-1"
    assert calls == []

    baseline()
    api.lib_relocate("rid-1", "P", "L", "R1RENAMED")   # moves the folder tree
    names = [e["robot"] for e in api.lib_list()["data"]["robots"]]
    assert "R1RENAMED" in names and "R1" not in names
    assert calls == []                                 # the move claimed its own delta

    # an EXTERNAL change (Explorer copy) still rescans — never absorbed unseen
    # (cache-first since slice B: the scan answers from the background thread)
    baseline()
    _make_robot(root, "P", "L", "R9", [("2026_03_03", "08_00_00", 1_700_000_000)], rid="rid-9")
    _settled_signature(root)                           # the copy's mtimes settle
    assert api.lib_list()["data"].get("scanning") is True
    _join_scan(api)
    assert len(calls) == 1
    assert any(e["robot"] == "R9" for e in api.lib_list()["data"]["robots"])
    assert len(calls) == 1                             # the follow-up was pure cache


def test_metadata_op_on_dirty_tree_keeps_pending_rescan(monkeypatch, tmp_path):
    """A metadata op racing an Explorer change must not absorb that change
    unseen: the claim only re-baselines when the tree was clean BEFORE the op.
    Dirty before -> still dirty after -> the next listing rescans and finds
    what the external edit added."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    settings.set_value("library_root", str(root))
    api = Api()
    api.lib_list()
    api._set_lib_sig(root, _settled_signature(root))

    (root / "P" / "L" / "R7").mkdir()                  # external change lands first
    _settled_signature(root)                           # let its mtimes settle

    calls = []
    real = library.scan_library_root
    monkeypatch.setattr(library, "scan_library_root",
                        lambda r, progress=None, on_entry=None:
                        calls.append(r) or real(r, progress=progress, on_entry=on_entry))
    api.lib_update("rid-1", {"notes": "raced"})        # op on the now-dirty tree
    assert calls == []
    assert api.lib_list()["data"].get("scanning") is True
    _join_scan(api)
    assert len(calls) == 1                             # the pending rescan survived the op
    assert any(e["robot"] == "R7"                      # and honored the copy
               for e in api.lib_list()["data"]["robots"])


def test_scan_progress_ticks(monkeypatch, tmp_path):
    """progress(done, total, current) ticks once per snapshot; total is the
    previous scan's snapshot count (0 = first ever, unknown) and never less
    than done; current names the robot folder being read."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000),
                                       ("2026_02_02", "09_30_00", 1_700_000_000)])
    _make_robot(root, "P", "L", "R2", [("2026_03_03", "10_00_00", 1_710_000_000)])

    first: list = []
    library.scan_library_root(root, progress=lambda d, t, c: first.append((d, t, c)))
    snaps = [x for x in first if x[0] > 0]
    assert [x[0] for x in snaps] == [1, 2, 3]          # one tick per snapshot
    assert all(t == 0 for _d, t, _c in snaps)          # first ever: no estimate yet

    second: list = []
    library.scan_library_root(root, progress=lambda d, t, c: second.append((d, t, c)))
    snaps2 = [x for x in second if x[0] > 0]
    assert [x[0] for x in snaps2] == [1, 2, 3]
    assert all(t == 3 for _d, t, _c in snaps2)         # estimate = last scan's 3 snapshots
    assert {c for _d, _t, c in snaps2} == {"R1", "R2"}


def test_scan_streams_entries_per_robot(monkeypatch, tmp_path):
    """Slice C: on_entry streams a finalized copy of each robot as the walk
    leaves its folder — backups newest-first, latest derived, walk bookkeeping
    stripped — plus every skeleton robot. The stream only adds/grows entries;
    drops belong to the final merge alone."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000),
                                       ("2026_02_02", "09_30_00", 1_700_000_000)], rid="rid-1")
    _make_robot(root, "P", "L", "R2", [("2026_03_03", "10_00_00", 1_710_000_000)], rid="rid-2")
    (root / "P" / "L" / "R3").mkdir()                  # skeleton robot, no backups yet

    seen = []
    library.scan_library_root(root, on_entry=seen.append)
    by_robot = {}
    for g in seen:
        by_robot[g["robot"]] = g                       # same robot again = newer copy
    assert set(by_robot) == {"R1", "R2", "R3"}
    r1 = by_robot["R1"]
    assert [b["taken"][:4] for b in r1["backups"]] == ["2026", "2026"]
    assert r1["backups"][0]["taken"] > r1["backups"][1]["taken"]   # newest first
    assert r1["latest_path"] == r1["backups"][0]["path"]
    assert "_snaps" not in r1 and "_absorbed" not in r1
    assert by_robot["R3"]["backups"] == []             # skeleton streamed honestly empty


def test_scan_streams_favorites_first_with_overlay_flags(monkeypatch, tmp_path):
    """The favorites pre-pass: starred robots (from the last-known overlay)
    are published before the walk reaches anything, with backups re-read
    fresh from their folder; walked entries carry the overlay's hidden/
    favorite bits so a streaming UI can filter like the real listing."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    _make_robot(root, "P", "L", "R2", [("2026_02_02", "09_00_00", 1_700_000_000)], rid="rid-2")
    library.scan_library_root(root)                    # build the overlay
    assert library.set_favorite("rid-2", True) is not None
    assert library.set_hidden("rid-1", True) is not None

    seen = []
    library.scan_library_root(root, on_entry=seen.append)
    assert seen[0]["id"] == "rid-2"                    # the favorite lands first
    assert seen[0]["favorite"] is True
    assert seen[0]["backups"], "preview re-reads its backups from disk"
    walked_r1 = [g for g in seen if g.get("id") == "rid-1"][-1]
    assert walked_r1["hidden"] is True                 # overlay bit folded for display


def test_lib_scan_progress_offset_cursor(monkeypatch, tmp_path):
    """The poll's offset cursor: each reply carries only the tail beyond what
    the caller already holds, plus the new total."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    settings.set_value("library_root", str(tmp_path / "lib"))
    api = Api()
    with api._lib_progress_lock:
        api._lib_progress.update(active=True, entries=[{"robot": "A"}, {"robot": "B"},
                                                       {"robot": "C"}])
    p0 = api.lib_scan_progress()["data"]
    assert p0["count"] == 3 and [e["robot"] for e in p0["entries"]] == ["A", "B", "C"]
    p2 = api.lib_scan_progress(2)["data"]
    assert p2["count"] == 3 and [e["robot"] for e in p2["entries"]] == ["C"]
    p3 = api.lib_scan_progress(3)["data"]
    assert p3["count"] == 3 and p3["entries"] == []
    pbad = api.lib_scan_progress(-5)["data"]           # a hostile/garbled offset clamps
    assert [e["robot"] for e in pbad["entries"]] == ["A", "B", "C"]


def test_scan_reads_sidecar_once_per_robot(monkeypatch, tmp_path):
    """robot.json is read once per robot folder, not once per snapshot — on a
    plant-scale tree the per-snapshot re-reads were thousands of redundant
    file opens."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "L", "R1", [("2026_01_01", "12_00_00", 1_600_000_000),
                                       ("2026_02_02", "09_30_00", 1_700_000_000),
                                       ("2026_03_03", "10_00_00", 1_710_000_000)])
    reads: list = []
    real = library._read_json

    def counting(p):
        if p.name == library.SIDECAR:
            reads.append(p)
        return real(p)

    monkeypatch.setattr(library, "_read_json", counting)
    e = library.scan_library_root(root)["robots"][0]
    assert len(e["backups"]) == 3
    assert len(reads) == 1                             # one sidecar read for three snapshots


def test_scan_reports_absorbed_copies(monkeypatch, tmp_path):
    """A copied folder carrying the original's robot.json folds into that robot
    by identity (the anti-duplicate design). The scan must SAY so — silent
    absorption reads as 'my copied folder never showed up' (the WOWOW case)."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    _make_robot(root, "P", "LINEB01", "R1", [("2026_01_01", "12_00_00", 1_600_000_000)], rid="rid-1")
    # a copy of the robot dropped under a DIFFERENT line, robot.json included
    copy = _make_robot(root, "P", "WOWOW", "R1", [
        ("2026_06_25", "16_12_02", 1_700_000_000),
        ("2026_06_30", "08_55_42", 1_700_100_000),
    ], rid="rid-1")
    assert (copy / "robot.json").is_file()

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1                             # no twin spawned
    e = data["robots"][0]
    assert e["line"] == "LINEB01"                                 # the original wins
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
