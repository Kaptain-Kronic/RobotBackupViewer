"""Camera <-> robot linking: Matrox names encode station+robot so most cameras
auto-link to their robot; CV-X (named by IP) and misses are assigned by hand."""
from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)
    # teach_camera_name renames folders via relocate, which resolves the library
    # root - point it inside tmp so no test can ever look at (or touch) a real one
    monkeypatch.setattr(settings, "library_root", lambda: str(tmp_path / "lib"))


def test_station_robot_key():
    # a robot and its Matrox cameras all key to the same station+robot
    assert library._station_robot_key("RB172R01B01") == "RB172R01"
    assert library._station_robot_key("CELL-01RB172-R01CAM02") == "RB172R01"
    assert library._station_robot_key("CELL-01RB172-R01CAM01") == "RB172R01"
    assert library._station_robot_key("CELL-01RB172-R02CAM01") == "RB172R02"   # a different robot
    assert library._station_robot_key("RC200R02B01") == "RC200R02"
    assert library._station_robot_key("192.0.2.117") == ""                 # CV-X by IP
    assert library._station_robot_key("") == ""


def test_auto_link_and_cameras_for_robot(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    robot = library.add_robot({"robot": "RB172R01B01", "line": "RBB01", "device_type": "robot"})
    other = library.add_robot({"robot": "RB172R02B01", "line": "RBB01", "device_type": "robot"})
    library.add_robot({"robot": "CELL-01RB172-R01CAM02", "line": "RBB01", "device_type": "camera-mtx"})
    library.add_robot({"robot": "CELL-01RB172-R01CAM01", "line": "RBB01", "device_type": "camera-mtx"})
    library.add_robot({"robot": "CELL-01RB172-R02CAM01", "line": "RBB01", "device_type": "camera-mtx"})
    cvx = library.add_robot({"robot": "192.0.2.117", "line": "RBB01", "device_type": "camera-keyence"})

    res = library.auto_link_cameras()
    assert len(res["linked"]) == 3                       # the 3 Matrox cams
    assert "192.0.2.117" in res["unmatched"]          # CV-X can't be keyed

    r01_cams = {c["robot"] for c in library.cameras_for_robot(robot["id"])}
    assert r01_cams == {"CELL-01RB172-R01CAM02", "CELL-01RB172-R01CAM01"}
    r02_cams = {c["robot"] for c in library.cameras_for_robot(other["id"])}
    assert r02_cams == {"CELL-01RB172-R02CAM01"}

    # manual link the CV-X to R01, and a re-run leaves existing links alone
    library.link_camera(cvx["id"], robot["id"])
    assert len(library.cameras_for_robot(robot["id"])) == 3
    res2 = library.auto_link_cameras()
    assert res2["linked"] == []                          # nothing new to link


def test_auto_link_prefers_same_cell(monkeypatch, tmp_path):
    """Duplicate robot names across lines (test-cell copies): a camera auto-links
    to the robot in ITS OWN plant+line, not an arbitrary twin."""
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB01", "device_type": "robot"})
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB02", "device_type": "robot"})
    test1 = library.add_robot({"robot": "RB172R02B01", "plant": "Test Cell", "line": "Test1", "device_type": "robot"})
    library.add_robot({"robot": "CELL-01RB172-R02CAM01", "plant": "Test Cell", "line": "Test1", "device_type": "camera-mtx"})

    res = library.auto_link_cameras()
    assert len(res["linked"]) == 1
    assert res["ambiguous"] == []                        # same-cell resolved the 3-way tie
    assert [c["robot"] for c in library.cameras_for_robot(test1["id"])] == ["CELL-01RB172-R02CAM01"]


def test_auto_link_still_ambiguous_when_no_same_cell(monkeypatch, tmp_path):
    """A camera whose cell has no matching robot twin stays ambiguous (manual)."""
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB01", "device_type": "robot"})
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB02", "device_type": "robot"})
    library.add_robot({"robot": "CELL-01RB172-R02CAM01", "plant": "Other", "line": "X", "device_type": "camera-mtx"})
    res = library.auto_link_cameras()
    assert res["linked"] == []
    assert "CELL-01RB172-R02CAM01" in res["ambiguous"]


def test_auto_link_same_name_fallback(monkeypatch, tmp_path):
    """A robot and a camera sharing one name (different device types) link even
    when the station+robot key can't read the name. Case-insensitive."""
    _iso(monkeypatch, tmp_path)
    robot = library.add_robot({"robot": "GLUECELL-EYE", "line": "RBB01", "device_type": "robot"})
    cam = library.add_robot({"robot": "GlueCell-Eye", "line": "RBB01", "device_type": "camera-mtx"})
    res = library.auto_link_cameras()
    assert res["linked"] == [{"camera": "GlueCell-Eye", "robot": "GLUECELL-EYE"}]
    assert [c["id"] for c in library.cameras_for_robot(robot["id"])] == [cam["id"]]


def test_auto_link_same_name_still_guards_ambiguity(monkeypatch, tmp_path):
    """The same-name fallback keeps the ambiguity rules: a twin name in two
    lines with no same-cell tiebreak stays unlinked (manual)."""
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "EYE-STATION", "plant": "P", "line": "L1", "device_type": "robot"})
    library.add_robot({"robot": "EYE-STATION", "plant": "P", "line": "L2", "device_type": "robot"})
    library.add_robot({"robot": "EYE-STATION", "plant": "Q", "line": "X", "device_type": "camera-mtx"})
    res = library.auto_link_cameras()
    assert res["linked"] == []
    assert "EYE-STATION" in res["ambiguous"]


def test_teach_camera_name(monkeypatch, tmp_path):
    """A placeholder(IP)-named camera takes its taught name (old name kept as an
    alias, model fills); a real name is never overwritten; non-cameras refuse."""
    _iso(monkeypatch, tmp_path)
    cam = library.add_robot({"robot": "192.0.2.7", "line": "RBB01",
                             "device_type": "camera-mtx", "ips": ["192.0.2.7"]})
    e = library.teach_camera_name(cam["id"], "CELL-01RB172-R01CAM02", "Matrox GTX2000")
    assert e["robot"] == "CELL-01RB172-R01CAM02"
    assert e["model"] == "Matrox GTX2000"
    assert {"plant": "", "line": "RBB01", "robot": "192.0.2.7"} in e["aliases"]

    e2 = library.teach_camera_name(cam["id"], "SOMETHING-ELSE")
    assert e2["robot"] == "CELL-01RB172-R01CAM02"           # real name kept

    rob = library.add_robot({"robot": "RB172R01B01", "device_type": "robot"})
    assert library.teach_camera_name(rob["id"], "X") is None    # robots refuse
    assert library.teach_camera_name("nope", "X") is None       # unknown id
    assert library.teach_camera_name(cam["id"], "") is None     # blank name


def test_taught_name_survives_rescan(monkeypatch, tmp_path):
    """The self-taught name must STICK: teach renames the camera's FOLDER (via
    relocate), so the next scan re-derives the same name. Regression: a name
    patched only into the registry reverted to the IP on the first rescan after
    the very backup that taught it (the folder still carried the IP, and the
    folder is identity - files are law)."""
    _iso(monkeypatch, tmp_path)
    import json
    root = tmp_path / "lib"
    cam_home = root / "P" / "RBB01" / "192.0.2.7"
    snap = cam_home / "2026_07_16" / "12_00_00"
    snap.mkdir(parents=True)
    (snap / "backup.json").write_text("{}", encoding="utf-8")
    (cam_home / "robot.json").write_text(json.dumps({
        "schema": 3, "id": "cam-1", "device_type": "camera-mtx",
        "ips": ["192.0.2.7"]}), encoding="utf-8")
    library.scan_library_root(root)
    cam = next(e for e in library.list_robots()["robots"] if e["robot"] == "192.0.2.7")

    e = library.teach_camera_name(cam["id"], "CELL-01RB172-R01CAM02", "Matrox GTX2000")
    assert e["robot"] == "CELL-01RB172-R01CAM02"
    # the folder moved with the name (id + snapshot history along with it) ...
    assert not cam_home.exists()
    moved = root / "P" / "RBB01" / "CELL-01RB172-R01CAM02"
    assert (moved / "2026_07_16" / "12_00_00" / "backup.json").is_file()
    # ... so a rescan re-derives the SAME name instead of reverting to the IP
    library.scan_library_root(root)
    e2 = next(x for x in library.list_robots()["robots"] if x["id"] == cam["id"])
    assert e2["robot"] == "CELL-01RB172-R01CAM02"
    assert {"plant": "P", "line": "RBB01", "robot": "192.0.2.7"} in e2["aliases"]


def test_link_persists_across_rescan(monkeypatch, tmp_path):
    """A camera's link survives a folder rescan (it's carried in robot.json)."""
    _iso(monkeypatch, tmp_path)
    import json
    root = tmp_path / "lib"
    # a robot + a camera snapshot on disk, camera sidecar carries the link
    rob_dir = root / "P" / "RBB01" / "RB172R01B01" / "2026_07_16" / "12_00_00"
    rob_dir.mkdir(parents=True)
    (rob_dir / "SUMMARY.DG").write_text("x", encoding="utf-8")
    (rob_dir / "backup.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "library_root", lambda: str(root))
    library.scan_library_root(root)
    robot = next(e for e in library.list_robots()["robots"] if e["robot"] == "RB172R01B01")

    cam_dir = root / "P" / "RBB01" / "CELL-01RB172-R01CAM02"
    cam_dir.mkdir(parents=True)
    (cam_dir / "robot.json").write_text(json.dumps({
        "schema": 3, "id": "cam-id-1", "device_type": "camera-mtx",
        "linked_robot_id": robot["id"], "ips": ["10.0.0.7"]}), encoding="utf-8")

    data = library.scan_library_root(root)
    cam = next(e for e in data["robots"] if e["robot"] == "CELL-01RB172-R01CAM02")
    assert cam["linked_robot_id"] == robot["id"]         # link read back from sidecar
    assert len(library.cameras_for_robot(robot["id"])) == 1
