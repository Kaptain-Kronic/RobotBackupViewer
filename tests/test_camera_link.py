"""Camera <-> robot linking: Matrox names encode station+robot so most cameras
auto-link to their robot; CV-X (named by IP) and misses are assigned by hand."""
from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)


def test_station_robot_key():
    # a robot and its Matrox cameras all key to the same station+robot
    assert library._station_robot_key("RB172R01B01") == "RB172R01"
    assert library._station_robot_key("FAB-01RB172-R01CAM02") == "RB172R01"
    assert library._station_robot_key("FAB-01RB172-R01CAM01") == "RB172R01"
    assert library._station_robot_key("FAB-01RB172-R02CAM01") == "RB172R02"   # a different robot
    assert library._station_robot_key("AD200R02B01") == "AD200R02"
    assert library._station_robot_key("192.0.2.117") == ""                 # CV-X by IP
    assert library._station_robot_key("") == ""


def test_auto_link_and_cameras_for_robot(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    robot = library.add_robot({"robot": "RB172R01B01", "line": "RBB01", "device_type": "robot"})
    other = library.add_robot({"robot": "RB172R02B01", "line": "RBB01", "device_type": "robot"})
    library.add_robot({"robot": "FAB-01RB172-R01CAM02", "line": "RBB01", "device_type": "camera-mtx"})
    library.add_robot({"robot": "FAB-01RB172-R01CAM01", "line": "RBB01", "device_type": "camera-mtx"})
    library.add_robot({"robot": "FAB-01RB172-R02CAM01", "line": "RBB01", "device_type": "camera-mtx"})
    cvx = library.add_robot({"robot": "192.0.2.117", "line": "RBB01", "device_type": "camera-keyence"})

    res = library.auto_link_cameras()
    assert len(res["linked"]) == 3                       # the 3 Matrox cams
    assert "192.0.2.117" in res["unmatched"]          # CV-X can't be keyed

    r01_cams = {c["robot"] for c in library.cameras_for_robot(robot["id"])}
    assert r01_cams == {"FAB-01RB172-R01CAM02", "FAB-01RB172-R01CAM01"}
    r02_cams = {c["robot"] for c in library.cameras_for_robot(other["id"])}
    assert r02_cams == {"FAB-01RB172-R02CAM01"}

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
    library.add_robot({"robot": "FAB-01RB172-R02CAM01", "plant": "Test Cell", "line": "Test1", "device_type": "camera-mtx"})

    res = library.auto_link_cameras()
    assert len(res["linked"]) == 1
    assert res["ambiguous"] == []                        # same-cell resolved the 3-way tie
    assert [c["robot"] for c in library.cameras_for_robot(test1["id"])] == ["FAB-01RB172-R02CAM01"]


def test_auto_link_still_ambiguous_when_no_same_cell(monkeypatch, tmp_path):
    """A camera whose cell has no matching robot twin stays ambiguous (manual)."""
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB01", "device_type": "robot"})
    library.add_robot({"robot": "RB172R02B01", "plant": "FakePlant", "line": "RBB02", "device_type": "robot"})
    library.add_robot({"robot": "FAB-01RB172-R02CAM01", "plant": "Other", "line": "X", "device_type": "camera-mtx"})
    res = library.auto_link_cameras()
    assert res["linked"] == []
    assert "FAB-01RB172-R02CAM01" in res["ambiguous"]


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

    cam_dir = root / "P" / "RBB01" / "FAB-01RB172-R01CAM02"
    cam_dir.mkdir(parents=True)
    (cam_dir / "robot.json").write_text(json.dumps({
        "schema": 3, "id": "cam-id-1", "device_type": "camera-mtx",
        "linked_robot_id": robot["id"], "ips": ["10.0.0.7"]}), encoding="utf-8")

    data = library.scan_library_root(root)
    cam = next(e for e in data["robots"] if e["robot"] == "FAB-01RB172-R01CAM02")
    assert cam["linked_robot_id"] == robot["id"]         # link read back from sidecar
    assert len(library.cameras_for_robot(robot["id"])) == 1
