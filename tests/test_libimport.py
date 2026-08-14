"""Drop-import engine - scan/grouping and the copy path, on tmp trees only.

Every fixture is synthetic and identifier-clean (RB../RC.. robots, FakePlant,
no real paths). The scan tests assert the grouping law (dated snapshots fold
under their robot, Latest mirrors dedup, bare folders synthesize a labeled
stamp); the copy tests assert the trust contract (source never modified,
conflicts never overwritten, cancel leaves no .__part, mtimes survive)."""
import datetime as dt
import json
import os
import threading
from pathlib import Path

from backupviewer import ftpbackup, libimport, library

TAKEN = "2026-08-01T07:00:00"


def _fanuc(d: Path, body: str = "[*NUMREG*]\r\n[1] = 10\r\n") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "NUMREG.VA").write_text(body, encoding="cp1252")
    (d / "SUMMARY.DG").write_text("SUMMARY.DG\r\n", encoding="cp1252")


def _meta(d: Path, **kw) -> None:
    (d / "backup.json").write_text(json.dumps(kw), encoding="utf-8")


def _dated_robot(parent: Path, name: str, stamps=(("2026_08_01", "07_00_00"),)) -> Path:
    robot = parent / name
    for date, time_ in stamps:
        _fanuc(robot / date / time_)
    return robot


def _scan(paths, root, existing=None):
    return libimport.scan_paths([str(p) for p in paths], str(root), existing)


# -- scan / grouping --------------------------------------------------------------

def test_scan_groups_dated_robot(tmp_path):
    drop = tmp_path / "drop"
    _dated_robot(drop, "RB010R01B01",
                 [("2026_08_01", "07_00_00"), ("2026_08_02", "08_00_00")])
    out = _scan([drop], tmp_path / "root")
    assert out["ignored"] == [] and out["in_library"] == []
    (d,) = out["drafts"]
    assert d["robot"] == "RB010R01B01" and d["device_type"] == "robot"
    assert not d["bare"] and d["warnings"] == []
    assert [(s["date"], s["time"]) for s in d["snapshots"]] == [
        ("2026_08_01", "07_00_00"), ("2026_08_02", "08_00_00")]
    assert all(not s["synthesized"] for s in d["snapshots"])
    assert d["files"] == 4 and d["bytes"] > 0


def test_scan_bare_folder_reads_backup_json(tmp_path):
    src = tmp_path / "MYBOT"
    _fanuc(src)
    _meta(src, robot="RB020R02B02", taken=TAKEN)
    (d,) = _scan([src], tmp_path / "root")["drafts"]
    assert d["robot"] == "RB020R02B02" and d["bare"]
    (s,) = d["snapshots"]
    assert (s["date"], s["time"], s["synthesized"]) == ("2026_08_01", "07_00_00", False)
    assert "date from folder timestamp" not in " ".join(d["warnings"])


def test_scan_bare_folder_synthesizes_labeled_stamp(tmp_path):
    src = tmp_path / "RB030R03B03"
    _fanuc(src)
    when = dt.datetime(2026, 8, 3, 6, 30, 0).timestamp()
    os.utime(src, (when, when))
    (d,) = _scan([src], tmp_path / "root")["drafts"]
    (s,) = d["snapshots"]
    assert s["synthesized"] and (s["date"], s["time"]) == ("2026_08_03", "06_30_00")
    assert any("timestamp" in w for w in d["warnings"])


def test_scan_parent_dedups_latest_mirror(tmp_path):
    drop = tmp_path / "slice"
    line = drop / "LINE1"
    _dated_robot(line, "RB010R01B01")
    _dated_robot(line, "RB040R04B04")
    _fanuc(line / "Latest" / "RB010R01B01")     # mirror of the newest snapshot
    out = _scan([drop], tmp_path / "root")
    names = sorted(d["robot"] for d in out["drafts"])
    assert names == ["RB010R01B01", "RB040R04B04"]
    by = {d["robot"]: d for d in out["drafts"]}
    assert len(by["RB010R01B01"]["snapshots"]) == 1   # the mirror added nothing


def test_scan_latest_only_robot_survives_as_bare(tmp_path):
    drop = tmp_path / "slice"
    mirror = drop / "Latest" / "RB050R05B05"
    _fanuc(mirror)
    _meta(mirror, taken=TAKEN)
    (d,) = _scan([drop], tmp_path / "root")["drafts"]
    assert d["robot"] == "RB050R05B05" and d["bare"]
    assert d["snapshots"][0]["date"] == "2026_08_01"


def test_scan_reports_junk_and_non_folders(tmp_path):
    junk = tmp_path / "notes"
    junk.mkdir()
    (junk / "readme.txt").write_text("hi", encoding="utf-8")
    f = tmp_path / "loose.txt"
    f.write_text("hi", encoding="utf-8")
    out = _scan([junk, f], tmp_path / "root")
    assert out["drafts"] == []
    reasons = {i["path"]: i["reason"] for i in out["ignored"]}
    assert reasons[str(junk)] == "no backups found inside"
    assert reasons[str(f)] == "not a folder"


def test_scan_refuses_paths_already_in_library(tmp_path):
    root = tmp_path / "root"
    robot = root / "LINE1" / "RB010R01B01"
    _fanuc(robot / "2026_08_01" / "07_00_00")
    out = _scan([robot], root)
    assert out["drafts"] == [] and out["in_library"] == [str(robot)]


def test_scan_device_types(tmp_path):
    kx = tmp_path / "CVX-STATION"
    (kx / "cv-x" / "setting").mkdir(parents=True)
    mtx = tmp_path / "MTXCAM"
    (mtx / "da").mkdir(parents=True)
    (mtx / "Documents").mkdir()
    wrap = tmp_path / "STATION2" / "CAM1" / "SD1"
    wrap.mkdir(parents=True)
    out = _scan([kx, mtx, tmp_path / "STATION2"], tmp_path / "root")
    by = {d["robot"]: d["device_type"] for d in out["drafts"]}
    # no readable program/image names inside -> folder name stays the identity
    assert by == {"CVX-STATION": "camera-keyence", "MTXCAM": "camera-mtx",
                  "STATION2": "camera-keyence"}
    assert out["notes"] and "3 different folders" in out["notes"][0]


def test_scan_flags_reserved_name(tmp_path):
    src = tmp_path / "2026_08_01"      # a robot named like a date would vanish
    _fanuc(src)
    (d,) = _scan([src], tmp_path / "root")["drafts"]
    assert any("reserved" in w for w in d["warnings"])
    out = libimport.run_import([d], "FakePlant", "L1", tmp_path / "root")
    assert out["results"][0]["status"] == "refused"


def test_scan_exists_at_and_duplicate_sidecar_id(tmp_path):
    drop = tmp_path / "drop"
    robot = _dated_robot(drop, "RB010R01B01")
    (robot / library.SIDECAR).write_text(json.dumps({"schema": 3, "id": "abc123"}),
                                         encoding="utf-8")
    existing = [{"plant": "FakePlant", "line": "L1", "robot": "RB010R01B01", "id": "abc123"}]
    (d,) = _scan([drop], tmp_path / "root", existing)["drafts"]
    assert d["exists_at"] == "FakePlant/L1"
    assert any("sidecar id" in w for w in d["warnings"])


def test_scan_of_dropped_snapshot_dir_finds_its_robot(tmp_path):
    robot = _dated_robot(tmp_path / "drop", "RB060R06B06")
    (d,) = _scan([robot / "2026_08_01" / "07_00_00"], tmp_path / "root")["drafts"]
    assert d["robot"] == "RB060R06B06" and len(d["snapshots"]) == 1


# -- copy engine ------------------------------------------------------------------

def test_run_import_lands_the_tree(tmp_path):
    root = tmp_path / "root"
    robot = _dated_robot(tmp_path / "drop", "RB010R01B01")
    (d,) = _scan([robot], root)["drafts"]
    out = libimport.run_import([d], "FakePlant", "L1", root)
    (r,) = out["results"]
    assert r["status"] == "imported" and r["copied"] == 1 and not r["errors"]
    dst = root / "FakePlant" / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00"
    src = robot / "2026_08_01" / "07_00_00"
    assert (dst / "NUMREG.VA").read_text(encoding="cp1252") == \
        (src / "NUMREG.VA").read_text(encoding="cp1252")
    # evidence still dates itself: the copy keeps the source's modified time
    assert (dst / "NUMREG.VA").stat().st_mtime_ns == (src / "NUMREG.VA").stat().st_mtime_ns
    assert (src / "NUMREG.VA").is_file()          # source untouched
    assert not list(dst.parent.glob("*.__part"))  # staging cleaned up


def test_run_import_blank_plant_collapses(tmp_path):
    root = tmp_path / "root"
    robot = _dated_robot(tmp_path / "drop", "RB010R01B01")
    (d,) = _scan([robot], root)["drafts"]
    libimport.run_import([d], "", "L1", root)
    assert (root / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00" / "NUMREG.VA").is_file()


def test_run_import_again_merges_and_skips_duplicates(tmp_path):
    root = tmp_path / "root"
    robot = _dated_robot(tmp_path / "drop", "RB010R01B01")
    (d,) = _scan([robot], root)["drafts"]
    libimport.run_import([d], "FakePlant", "L1", root)
    out = libimport.run_import([d], "FakePlant", "L1", root)
    (r,) = out["results"]
    assert r["status"] == "merged" and r["copied"] == 0 and r["duplicates"] == 1
    assert (robot / "2026_08_01" / "07_00_00" / "NUMREG.VA").is_file()


def test_run_import_conflict_never_overwrites(tmp_path):
    root = tmp_path / "root"
    robot = _dated_robot(tmp_path / "drop", "RB010R01B01")
    dst = root / "FakePlant" / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00"
    _fanuc(dst, body="[*NUMREG*]\r\n[1] = 999\r\n")    # same stamp, other content
    (d,) = _scan([robot], root)["drafts"]
    out = libimport.run_import([d], "FakePlant", "L1", root)
    (r,) = out["results"]
    assert r["status"] == "merged" and r["conflicts"] == 1 and r["copied"] == 0
    assert "999" in (dst / "NUMREG.VA").read_text(encoding="cp1252")


def test_run_import_copies_sidecar_unless_id_is_taken(tmp_path):
    root = tmp_path / "root"
    drop = tmp_path / "drop"
    fresh = _dated_robot(drop, "RB010R01B01")
    (fresh / library.SIDECAR).write_text(json.dumps({"schema": 3, "id": "zzz9"}),
                                         encoding="utf-8")
    taken = _dated_robot(drop, "RB040R04B04")
    (taken / library.SIDECAR).write_text(json.dumps({"schema": 3, "id": "abc123"}),
                                         encoding="utf-8")
    drafts = _scan([drop], root)["drafts"]
    libimport.run_import(drafts, "FakePlant", "L1", root, existing_ids={"abc123"})
    line = root / "FakePlant" / "L1"
    assert json.loads((line / "RB010R01B01" / library.SIDECAR)
                      .read_text(encoding="utf-8"))["id"] == "zzz9"
    assert not (line / "RB040R04B04" / library.SIDECAR).exists()


def test_run_import_cancelled_before_start(tmp_path):
    root = tmp_path / "root"
    robot = _dated_robot(tmp_path / "drop", "RB010R01B01")
    (d,) = _scan([robot], root)["drafts"]
    cancel = threading.Event()
    cancel.set()
    out = libimport.run_import([d], "FakePlant", "L1", root, cancel=cancel)
    assert out["cancelled"] and out["results"][0]["status"] == "cancelled"
    assert not (root / "FakePlant").exists()


def test_copy_snapshot_cancel_leaves_no_staging(tmp_path):
    src = tmp_path / "snap"
    src.mkdir()
    for i in range(3):
        (src / f"F{i}.VA").write_text("x" * 10, encoding="cp1252")
    dst = tmp_path / "root" / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00"
    cancel = threading.Event()
    out = libimport.copy_snapshot(src, dst, progress=lambda b: cancel.set(),
                                  cancel=cancel)
    assert out["status"] == "cancelled"
    assert not dst.exists() and not list(dst.parent.glob("*.__part"))


def test_copy_snapshot_survives_max_path(tmp_path):
    src = tmp_path / "snap"
    deep = ftpbackup.long_path(str(src / ("d" * 100)))
    os.makedirs(deep)
    fname = "f" * 100 + ".VA"
    with open(os.path.join(deep, fname), "w", encoding="cp1252") as f:
        f.write("payload")
    dst = tmp_path / ("p" * 120) / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00"
    out = libimport.copy_snapshot(src, dst)
    assert out["status"] == "copied" and out["files"] == 1
    landed = ftpbackup.long_path(str(dst / ("d" * 100) / fname))
    assert os.path.getsize(landed) == len("payload")
