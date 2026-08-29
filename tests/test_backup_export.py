"""export_plan / export_backups / lib_export - the manage-backups export tab.

Fully synthetic. Plan-shape tests drive the PURE planner with hand-built
entries (no disk); copy tests are about DISK truth: verified .__part copies
landing at the templated targets, sources byte-untouched, robot.json riding
along, skip-not-overwrite, cancel, and the job seam on Api (no rescan is ever
paid - an export changes nothing inside the library)."""
import json
import threading

import pytest

from backupviewer import library, settings


@pytest.fixture(autouse=True)
def _reap_workers(monkeypatch):
    """Every background library worker dies with its own test (see
    test_library_scan for the full why - depending on `monkeypatch` orders
    this teardown before the un-patching)."""
    yield
    for t in threading.enumerate():
        if t.name in ("libscan", "libstage", "libexport"):
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


def _snap(robot_dir, date, time, *, robot, line="L", plant="P", bytes_=1000,
          complete=None):
    d = robot_dir / date / time
    d.mkdir(parents=True, exist_ok=True)
    (d / "SUMMARY.DG").write_text("Robot: " + robot + "\n", encoding="utf-8")
    meta = {"robot": robot, "line": line, "plant": plant,
            "taken": date.replace("_", "-") + "T" + time.replace("_", ":"),
            "type": "all of above", "files": 2, "bytes": bytes_, "source": "ftp"}
    if complete is not None:
        meta["complete"] = complete
    (d / "backup.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def _sidecar(robot_dir, rid, robot, line="L", plant="P"):
    robot_dir.mkdir(parents=True, exist_ok=True)
    (robot_dir / "robot.json").write_text(json.dumps({
        "schema": 1, "id": rid, "plant": plant, "line": line, "robot": robot,
        "model": "", "f_number": "", "ips": [],
        "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")


def _seed_r1(root):
    """R1: three completed (newest-first 2026-08, 2026-06, 2024-01) plus one
    partial newer than them all - "newest N completed" must step over it."""
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_08_20", "09_00_00", robot="R1", complete=False, bytes_=50)
    _snap(r1, "2026_08_01", "10_00_00", robot="R1")
    _snap(r1, "2026_06_01", "10_00_00", robot="R1", bytes_=700)
    _snap(r1, "2024_01_01", "10_00_00", robot="R1", bytes_=250)
    _sidecar(r1, "rid-1", "R1")
    return r1


# -- the pure planner ----------------------------------------------------------

def _entry(rid, robot, *, plant="P", line="L", backups=(), hidden=False):
    e = {"id": rid, "plant": plant, "line": line, "robot": robot,
         "backups": list(backups)}
    if hidden:
        e["hidden"] = True
    return e


def _rec(taken, *, robot="R1", line="L", plant="P", partial=False, bytes_=100):
    date = taken[:10].replace("-", "_")
    time = taken[11:19].replace(":", "_")
    r = {"taken": taken, "path": f"X:/lib/{plant}/{line}/{robot}/{date}/{time}",
         "bytes": bytes_, "files": 2, "source": "ftp"}
    if partial:
        r["partial"] = True
    return r


def test_plan_default_layout_newest_completed():
    e = _entry("a", "R1", backups=[
        _rec("2026-08-20T09:00:00", partial=True),      # newest overall: a dead pull
        _rec("2026-08-01T10:00:00"),
        _rec("2026-06-01T10:00:00", bytes_=0),
        _rec("2024-01-01T10:00:00"),
    ])
    p = library.export_plan([e], ["a"], 2, ["plant", "line", "robot", "date"])
    assert [r["rel"] for r in p["rows"]] == ["P/L/R1/2026_06_01", "P/L/R1/2026_08_01"]
    assert all(not r.get("collides") for r in p["rows"])
    # partial stepped over, never exported; row order is display order
    assert {r["taken"] for r in p["rows"]} == {"2026-08-01T10:00:00",
                                               "2026-06-01T10:00:00"}
    assert p["totals"] == {"count": 2, "bytes": 100, "unsized": 1, "existing": 0}
    # robot.json is OPT-IN: nothing rides unless asked for
    assert all(r["sidecar_rel"] == "" for r in p["rows"])
    assert p["skipped"] == [] and p["collisions"] == []
    # asked for, and robot the deepest identity level -> it rides at its folder
    p = library.export_plan([e], ["a"], 2, ["plant", "line", "robot", "date"],
                            sidecar=True)
    assert all(r["sidecar_rel"] == "P/L/R1" for r in p["rows"])


def test_plan_segments_reorder_omit_and_empty_values():
    e = _entry("a", "R1", plant="", backups=[_rec("2026-08-01T10:00:00", plant="")])
    p = library.export_plan([e], ["a"], 1, ["date", "robot"], sidecar=True)
    assert p["rows"][0]["rel"] == "2026_08_01/R1"
    # nothing after "robot" but date/time -> the sidecar still rides
    assert p["rows"][0]["sidecar_rel"] == "2026_08_01/R1"
    # plant is empty -> its level just disappears
    p = library.export_plan([e], ["a"], 1, ["plant", "robot", "date"], sidecar=True)
    assert p["rows"][0]["rel"] == "R1/2026_08_01"
    # an identity segment AFTER robot -> identity is not robot-deep, no sidecar
    p = library.export_plan([e], ["a"], 1, ["robot", "line", "date"], sidecar=True)
    assert p["rows"][0]["rel"] == "R1/L/2026_08_01"
    assert p["rows"][0]["sidecar_rel"] == ""
    # robot omitted entirely -> no sidecar either
    p = library.export_plan([e], ["a"], 1, ["line", "date"], sidecar=True)
    assert p["rows"][0]["sidecar_rel"] == ""


def test_plan_custom_segments():
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00")])
    p = library.export_plan([e], ["a"], 1,
                            ["plant", {"custom": "KIT A"}, "robot", "date"],
                            sidecar=True)
    assert p["rows"][0]["rel"] == "P/KIT A/R1/2026_08_01"
    # customs below robot keep the robot folder real - the sidecar still rides
    p = library.export_plan([e], ["a"], 1,
                            ["robot", {"custom": "V1"}, "date"], sidecar=True)
    assert p["rows"][0]["rel"] == "R1/V1/2026_08_01"
    assert p["rows"][0]["sidecar_rel"] == "R1"
    # customs are structure, not identity - the same name may repeat
    p = library.export_plan([e], ["a"], 1,
                            [{"custom": "X"}, "robot", {"custom": "X"}])
    assert p["rows"][0]["rel"] == "X/R1/X"


def test_plan_custom_name_validation():
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00")])
    for bad in ("", "  ", "a/b", "a:b", "..", "dot.", "CON", "x" * 81):
        with pytest.raises(ValueError):
            library.export_plan([e], ["a"], 1, ["robot", {"custom": bad}])


def test_plan_days_window():
    e = _entry("a", "R1", backups=[_rec("2026-08-20T09:00:00"),
                                   _rec("2026-06-01T10:00:00")])
    old = _entry("b", "R2", backups=[_rec("2024-01-01T10:00:00", robot="R2")])
    undated = _entry("c", "R3", backups=[{
        "taken": "", "path": "X:/lib/P/L/R3/2024_01_01/10_00_00",
        "bytes": 5, "files": 1, "source": "import"}])
    p = library.export_plan([e, old, undated], ["a", "b", "c"], 2,
                            ["robot", "date"], days=30,
                            now="2026-08-29T00:00:00")
    # only the inside-the-window snapshot exports; the older one stays home
    assert [r["taken"] for r in p["rows"]] == ["2026-08-20T09:00:00"]
    reasons = {s["robot_id"]: s["reason"] for s in p["skipped"]}
    assert reasons["b"] == "nothing completed in the last 30 days"
    # an undated record can't prove its age - out of any windowed export
    assert reasons["c"] == "nothing completed in the last 30 days"
    # no window -> everything eligible again
    p = library.export_plan([e, old], ["a", "b"], 2, ["robot", "date"])
    assert len(p["rows"]) == 3


def test_plan_zip_targets(tmp_path):
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00")])
    dest = tmp_path / "stick"
    dest.mkdir()
    (dest / "R1").mkdir()
    (dest / "R1" / "2026_08_01.zip").write_bytes(b"PK")   # a prior zipped export
    p = library.export_plan([e], ["a"], 1, ["robot", "date"], str(dest),
                            zip_at=-1)
    assert p["zip"] is True
    assert p["rows"][0]["rel"] == "R1/2026_08_01"          # rel stays the folder path
    assert p["rows"][0]["exists"] is True                  # …but the .zip is probed
    # zipped leaves are files: one rel being another's path-prefix can't merge
    e1 = _entry("p1", "B", plant="", line="A",
                backups=[_rec("2026-08-01T10:00:00", robot="B", line="A", plant="")])
    e2 = _entry("p2", "C", plant="A", line="B",
                backups=[_rec("2026-06-01T10:00:00", robot="C", line="B", plant="A")])
    p = library.export_plan([e1, e2], ["p1", "p2"], 1, ["plant", "line", "robot"],
                            zip_at=-1)
    assert p["collisions"] == []
    # nothing to name the archive after -> that row is skipped, said plainly
    p = library.export_plan([_entry("a", "R1", plant="", line="",
                                    backups=[_rec("2026-08-01T10:00:00")])],
                            ["a"], 1, [], zip_at=-1)
    assert p["rows"] == []
    assert "name the archive" in p["skipped"][0]["reason"]
    # a robot-last zipped leaf is a FILE - no robot folder for a sidecar
    p = library.export_plan([e], ["a"], 1, ["plant", "robot"],
                            zip_at=-1, sidecar=True)
    assert p["rows"][0]["sidecar_rel"] == ""


def test_plan_zip_at_a_chosen_level():
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00"),
                                   _rec("2026-06-01T10:00:00")])
    e2 = _entry("b", "R2", backups=[_rec("2026-08-01T11:00:00", robot="R2")])
    segs = ["plant", "line", "robot", "date"]
    # boundary at robot (index 2): both of R1's snapshots become MEMBERS of
    # one archive, their date folders the paths inside it - no collision
    p = library.export_plan([e, e2], ["a", "b"], 2, segs, zip_at=2)
    assert p["zip"] is True and p["zip_at"] == 2
    got = {(r["arel"], r["irel"]) for r in p["rows"]}
    assert got == {("P/L/R1", "2026_06_01"), ("P/L/R1", "2026_08_01"),
                   ("P/L/R2", "2026_08_01")}
    assert p["collisions"] == []
    # date unticked: R1's two members would both sit at the archive root -
    # that IS a merge, flagged on both
    p = library.export_plan([e, e2], ["a", "b"], 2, ["plant", "line", "robot"],
                            zip_at=2)
    assert p["collisions"] == ["P/L/R1"]
    flagged = [r for r in p["rows"] if r.get("collides")]
    assert len(flagged) == 2 and all(r["arel"] == "P/L/R1" for r in flagged)
    # a robot with nothing at the boundary level is skipped, said plainly
    bare = _entry("c", "R3", plant="", backups=[_rec("2026-08-01T10:00:00",
                                                     robot="R3", plant="")])
    p = library.export_plan([bare], ["c"], 1, segs, zip_at=0)
    assert p["rows"] == []
    assert "nothing at the plant level to zip at" in p["skipped"][0]["reason"]
    # the sidecar rides only while the robot folder stays REAL: boundary
    # below robot keeps it, boundary at robot swallows it
    p = library.export_plan([e], ["a"], 1, segs, zip_at=3, sidecar=True)
    assert p["rows"][0]["sidecar_rel"] == "P/L/R1"
    p = library.export_plan([e], ["a"], 1, segs, zip_at=2, sidecar=True)
    assert p["rows"][0]["sidecar_rel"] == ""
    # a boundary index outside the layout is refused
    with pytest.raises(ValueError, match="zip level"):
        library.export_plan([e], ["a"], 1, segs, zip_at=4)


def test_export_zip_at_robot_rolls_snapshots_into_one_archive(monkeypatch, tmp_path):
    import zipfile
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"

    ticks = []
    res = library.export_backups(["rid-1"], 2, ["plant", "line", "robot", "date"],
                                 str(dest), zip_at=2,
                                 progress=lambda d, t, b, c: ticks.append((d, t)))
    assert len(res["exported"]) == 2             # two snapshots, ONE archive
    assert {x["rel"] for x in res["exported"]} == {"P/L/R1"}
    assert ticks[-1] == (2, 2)
    z = dest / "P" / "L" / "R1.zip"
    assert z.is_file()
    assert not (dest / "P" / "L" / "R1").exists()
    with zipfile.ZipFile(z) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == [
            "2026_06_01/SUMMARY.DG", "2026_06_01/backup.json",
            "2026_08_01/SUMMARY.DG", "2026_08_01/backup.json"]
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]

    again = library.export_backups(["rid-1"], 2, ["plant", "line", "robot", "date"],
                                   str(dest), zip_at=2)
    assert again["exported"] == []               # the whole archive skips as one
    assert [s["reason"] for s in again["skipped"]] == ["already there"] * 2


def test_plan_collisions_flagged_both_rows():
    # date omitted with count 2 -> both snapshots map onto one folder
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00"),
                                   _rec("2026-06-01T10:00:00")])
    p = library.export_plan([e], ["a"], 2, ["plant", "line", "robot"])
    assert p["collisions"] == ["P/L/R1"]
    assert [r.get("collides") for r in p["rows"]] == [True, True]
    # same day, two pulls: date alone collides, adding time resolves it
    e2 = _entry("b", "R2", backups=[_rec("2026-08-01T11:00:00", robot="R2"),
                                    _rec("2026-08-01T10:00:00", robot="R2")])
    p = library.export_plan([e2], ["b"], 2, ["robot", "date"])
    assert p["collisions"] == ["R2/2026_08_01"]
    p = library.export_plan([e2], ["b"], 2, ["robot", "date", "time"])
    assert p["collisions"] == []
    assert {r["rel"] for r in p["rows"]} == {"R2/2026_08_01/10_00_00",
                                             "R2/2026_08_01/11_00_00"}


def test_plan_nested_target_is_a_collision():
    # a plant-less robot's rel can be a PREFIX of a plant-ful one's - the
    # deeper copy would land inside the shallower export
    e1 = _entry("a", "B", plant="", line="A",
                backups=[_rec("2026-08-01T10:00:00", robot="B", line="A", plant="")])
    e2 = _entry("b", "C", plant="A", line="B",
                backups=[_rec("2026-06-01T10:00:00", robot="C", line="B", plant="A")])
    p = library.export_plan([e1, e2], ["a", "b"], 1, ["plant", "line", "robot"])
    assert p["collisions"] == ["A/B", "A/B/C"]
    assert all(r.get("collides") for r in p["rows"])


def test_plan_skips_are_named():
    partial_only = _entry("p", "RP", backups=[_rec("2026-08-01T10:00:00",
                                                   robot="RP", partial=True)])
    flat = _entry("f", "RF", backups=[{"taken": "2026-08-01T10:00:00",
                                       "path": "X:/lib/P/L/RF",     # the robot dir itself
                                       "bytes": 5, "files": 1, "source": "import"}])
    hidden = _entry("h", "RH", hidden=True,
                    backups=[_rec("2026-08-01T10:00:00", robot="RH")])
    p = library.export_plan([partial_only, flat, hidden],
                            ["p", "f", "h", "ghost"], 1,
                            ["plant", "line", "robot", "date"])
    assert p["rows"] == []
    reasons = {s["robot_id"]: s["reason"] for s in p["skipped"]}
    assert reasons["p"] == "no completed backups"
    assert "flat import" in reasons["f"]
    assert reasons["h"] == "hidden"
    assert reasons["ghost"] == "not in the library"


def test_plan_bad_segments_refused():
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00")])
    with pytest.raises(ValueError, match="unknown layout segment"):
        library.export_plan([e], ["a"], 1, ["plant", "folder"])
    with pytest.raises(ValueError, match="repeats"):
        library.export_plan([e], ["a"], 1, ["robot", "robot"])


def test_plan_marks_existing_targets(tmp_path):
    e = _entry("a", "R1", backups=[_rec("2026-08-01T10:00:00"),
                                   _rec("2026-06-01T10:00:00")])
    dest = tmp_path / "stick"
    (dest / "P" / "L" / "R1" / "2026_08_01").mkdir(parents=True)
    (dest / "P" / "L" / "R1" / "2026_08_01" / "x.txt").write_text("x")
    (dest / "P" / "L" / "R1" / "2026_06_01").mkdir(parents=True)   # EMPTY: not "there"
    p = library.export_plan([e], ["a"], 2, ["plant", "line", "robot", "date"],
                            str(dest))
    got = {r["rel"]: bool(r.get("exists")) for r in p["rows"]}
    assert got == {"P/L/R1/2026_08_01": True, "P/L/R1/2026_06_01": False}
    assert p["totals"]["existing"] == 1


# -- the copy itself -----------------------------------------------------------

def test_export_copies_skips_and_never_touches_sources(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"
    dest.mkdir()
    already = dest / "P" / "L" / "R1" / "2026_06_01"
    already.mkdir(parents=True)
    (already / "old.txt").write_text("an earlier export", encoding="utf-8")

    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    ticks = []
    res = library.export_backups(["rid-1"], 2, ["plant", "line", "robot", "date"],
                                 str(dest), sidecar=True,
                                 progress=lambda d, t, b, c: ticks.append((d, t)))

    assert [x["rel"] for x in res["exported"]] == ["P/L/R1/2026_08_01"]
    assert [(s["taken"], s["reason"]) for s in res["skipped"]] == [
        ("2026-06-01T10:00:00", "already there")]
    assert res["failed"] == [] and res["cancelled"] is False
    assert ticks[-1] == (2, 2)
    # the copy landed, contents intact, marker sidecar included
    out = dest / "P" / "L" / "R1" / "2026_08_01"
    assert (out / "SUMMARY.DG").read_text(encoding="utf-8") == "Robot: R1\n"
    assert json.loads((out / "backup.json").read_text(encoding="utf-8"))["robot"] == "R1"
    # the skipped target was NOT overwritten
    assert (already / "old.txt").read_text(encoding="utf-8") == "an earlier export"
    # identity rides along at the robot level
    assert json.loads((dest / "P" / "L" / "R1" / "robot.json")
                      .read_text(encoding="utf-8"))["id"] == "rid-1"
    # no .__part leftovers anywhere at the destination
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]
    # and the library is byte-for-byte untouched
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert after == before
    assert len(library.get_robot("rid-1")["backups"]) == 4


def test_export_materializes_sidecar_when_the_folder_has_none(monkeypatch, tmp_path):
    """A scanned-in robot folder may hold no robot.json; identity still
    travels - the export writes one from the entry, exactly what the library
    does for its own folders."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = _seed_r1(root)
    library.scan_library_root(root)
    (r1 / "robot.json").unlink()
    dest = tmp_path / "stick"

    res = library.export_backups(["rid-1"], 1, ["plant", "line", "robot", "date"],
                                 str(dest), sidecar=True)
    assert len(res["exported"]) == 1
    made = json.loads((dest / "P" / "L" / "R1" / "robot.json")
                      .read_text(encoding="utf-8"))
    assert made["id"] == "rid-1"
    assert not (r1 / "robot.json").exists()      # the source stays as it was


def test_export_sidecar_stays_home_unless_asked(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"

    res = library.export_backups(["rid-1"], 1, ["plant", "line", "robot", "date"],
                                 str(dest))                # sidecar NOT asked for
    assert len(res["exported"]) == 1
    assert (dest / "P" / "L" / "R1" / "2026_08_01").is_dir()
    assert not (dest / "P" / "L" / "R1" / "robot.json").exists()


def test_export_zip_archives_and_skips(monkeypatch, tmp_path):
    """zip_leaf: the leaf folder becomes a deflated, CRC-verified .zip; parent
    folders stay real, robot.json (asked for) sits beside the archives, and a
    re-run skips every archive already there."""
    import zipfile
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"

    res = library.export_backups(["rid-1"], 2, ["plant", "line", "robot", "date"],
                                 str(dest), sidecar=True, zip_at=-1)
    assert [x["rel"] for x in res["exported"]] == ["P/L/R1/2026_06_01",
                                                   "P/L/R1/2026_08_01"]
    out = dest / "P" / "L" / "R1"
    for name in ("2026_08_01", "2026_06_01"):
        z = out / (name + ".zip")
        assert z.is_file()
        src = root / "P" / "L" / "R1" / name / "10_00_00"
        with zipfile.ZipFile(z) as zf:
            assert zf.testzip() is None
            assert sorted(zf.namelist()) == ["SUMMARY.DG", "backup.json"]
            # byte-faithful to the source file, whatever its line endings
            assert zf.read("SUMMARY.DG") == (src / "SUMMARY.DG").read_bytes()
    assert (out / "robot.json").is_file()        # beside the archives, unzipped
    assert not (out / "2026_08_01").exists()     # no unzipped copy appears
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]

    again = library.export_backups(["rid-1"], 2, ["plant", "line", "robot", "date"],
                                   str(dest), sidecar=True, zip_at=-1)
    assert again["exported"] == []
    assert {s["reason"] for s in again["skipped"]} == {"already there"}


def test_export_zip_cancel_and_verify_cleanup(monkeypatch, tmp_path):
    import zipfile
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"

    seen = {"b": 0}
    res = library.export_backups(
        ["rid-1"], 1, ["plant", "line", "robot", "date"], str(dest),
        zip_at=-1,
        progress=lambda d, t, b, c: seen.__setitem__("b", b),
        cancel=lambda: seen["b"] > 0)            # stop after the first file
    assert res["cancelled"] is True
    assert res["exported"] == []
    assert not list(dest.rglob("*.zip"))
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]

    # a verify failure fails the row and leaves no half archive behind
    real_testzip = zipfile.ZipFile.testzip
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "SUMMARY.DG")
    res = library.export_backups(["rid-1"], 1, ["plant", "line", "robot", "date"],
                                 str(dest), zip_at=-1)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", real_testzip)
    assert res["exported"] == []
    assert "verify failed" in res["failed"][0]["error"]
    assert not list(dest.rglob("*.zip"))
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]


def test_export_refuses_dest_inside_library_and_collisions(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)

    with pytest.raises(ValueError, match="inside the library"):
        library.export_backups(["rid-1"], 1, ["plant"], str(root / "out"))
    # date+time omitted with count 2 -> collision -> refused outright
    dest = tmp_path / "stick"
    with pytest.raises(ValueError, match="same folder"):
        library.export_backups(["rid-1"], 2, ["plant", "line", "robot"], str(dest))
    assert not dest.exists()                     # nothing was written


def test_export_cancel_leaves_no_half_tree(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"

    copied = []

    def cancel():
        return len(copied) >= 1                  # let snapshot 1 finish, stop in 2

    real = library._copy_tree_verified

    def spying(src, dst, on_file=None):
        real(src, dst, on_file=on_file)
        copied.append(str(dst))
    monkeypatch.setattr(library, "_copy_tree_verified", spying)

    res = library.export_backups(["rid-1"], 3, ["plant", "line", "robot", "date"],
                                 str(dest), cancel=cancel)
    assert res["cancelled"] is True
    assert len(res["exported"]) == 1             # the finished copy stays
    # nothing half-written at a final name, no .__part leftovers
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]
    dated = [p.name for p in (dest / "P" / "L" / "R1").iterdir() if p.is_dir()]
    assert len(dated) == 1


def test_export_verify_failure_fails_the_row_and_cleans_up(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    dest = tmp_path / "stick"
    monkeypatch.setattr(library, "_verify_tree", lambda s, d, **k: False)

    res = library.export_backups(["rid-1"], 1, ["plant", "line", "robot", "date"],
                                 str(dest))
    assert res["exported"] == []
    assert len(res["failed"]) == 1
    assert "verify failed" in res["failed"][0]["error"]
    assert not [p for p in dest.rglob("*") if p.name.endswith(".__part")]
    assert not (dest / "P" / "L" / "R1" / "2026_08_01").exists()


def test_move_tree_copy_path_survives_the_refactor(monkeypatch, tmp_path):
    """_move_tree's cross-volume branch now delegates to _copy_tree_verified;
    force it (os.rename refused) and prove move semantics held: dst complete,
    src gone."""
    src = tmp_path / "a" / "t"
    src.mkdir(parents=True)
    (src / "f.txt").write_text("x", encoding="utf-8")
    dst = tmp_path / "b" / "t"
    dst.parent.mkdir()
    monkeypatch.setattr(library.os, "rename",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    library._move_tree(src, dst)
    assert (dst / "f.txt").read_text(encoding="utf-8") == "x"
    assert not src.exists()


# -- the Api seam --------------------------------------------------------------

def test_lib_export_job_and_no_rescan(monkeypatch, tmp_path):
    """The job shape mirrors lib_stage - stub now, poll to the result - and
    lib_list stays a cache hit afterwards: an export changes NOTHING inside
    the library, so no scan is ever paid for it."""
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
    dest = tmp_path / "stick"

    r = api.lib_export(["rid-1"], 1, ["plant", "line", "robot", "date"], str(dest))
    assert r["ok"] and r["data"]["started"] is True
    api._export_thread.join(timeout=30)
    assert not api._export_thread.is_alive()
    prog = api.lib_export_progress()
    assert prog["ok"] and prog["data"]["running"] is False
    assert prog["data"]["error"] == ""
    assert len(prog["data"]["result"]["exported"]) == 1
    assert prog["data"]["done"] == prog["data"]["total"] == 1
    assert (dest / "P" / "L" / "R1" / "2026_08_01" / "SUMMARY.DG").is_file()
    listed = api.lib_list()
    assert listed["ok"]
    assert not listed["data"].get("scanning")          # served from cache
    assert calls == []                                 # no rescan was paid


def test_lib_export_config_error_reaches_the_poll(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    api = Api()

    r = api.lib_export(["rid-1"], 1, ["plant"], str(root / "inside"))
    assert r["ok"]
    api._export_thread.join(timeout=30)
    prog = api.lib_export_progress()
    assert "inside the library" in prog["data"]["error"]


def test_lib_export_busy_guards(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    api = Api()
    dest = tmp_path / "stick"

    monkeypatch.setattr(api, "_backups_active", lambda: True)
    r = api.lib_export(["rid-1"], 1, None, str(dest))
    assert r["ok"] is False and "backups are running" in r["error"]["message"]
    monkeypatch.setattr(api, "_backups_active", lambda: False)

    # a running stage blocks export, and a running export blocks stage
    api._stage_job = {"running": True, "done": 0, "total": 1, "result": None, "error": ""}
    r = api.lib_export(["rid-1"], 1, None, str(dest))
    assert r["ok"] is False and "staging move" in r["error"]["message"]
    api._stage_job = None
    api._export_job = {"running": True, "done": 0, "total": 1, "bytes": 0,
                       "current": "", "cancel": False, "result": None, "error": ""}
    r = api.lib_stage([{"robot_id": "rid-1", "taken": "2024-01-01T10:00:00"}])
    assert r["ok"] is False and "export is copying" in r["error"]["message"]
    r = api.lib_export(["rid-1"], 1, None, str(dest))
    assert r["ok"] is False and "already running" in r["error"]["message"]
    api._export_job = None
    assert not dest.exists()                           # every refusal wrote nothing


def test_lib_export_plan_options_ride_the_bridge(monkeypatch, tmp_path):
    """days / sidecar / zip travel positionally after dest and echo back in
    the plan - the preview trusts these echoes, never its own state."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _seed_r1(root)
    library.scan_library_root(root)
    api = Api()

    r = api.lib_export_plan(["rid-1"], 1, ["robot", {"custom": "KIT"}, "date"],
                            "", 0, True, -1)
    assert r["ok"]
    assert r["data"]["sidecar"] is True and r["data"]["zip"] is True
    assert r["data"]["zip_at"] == -1
    assert r["data"]["rows"][0]["rel"] == "R1/KIT/2026_08_01"
    r = api.lib_export_plan(["rid-1"], 5, None, "", 100000)
    assert r["ok"] and r["data"]["days"] == 100000 and len(r["data"]["rows"]) == 3
    assert r["data"]["zip"] is False and r["data"]["zip_at"] is None


def test_lib_export_cancel_endpoint(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    api = Api()
    r = api.lib_export_cancel()
    assert r["ok"] and r["data"]["cancelling"] is False
    api._export_job = {"running": True, "done": 0, "total": 1, "bytes": 0,
                       "current": "", "cancel": False, "result": None, "error": ""}
    r = api.lib_export_cancel()
    assert r["ok"] and r["data"]["cancelling"] is True
    assert api._export_job["cancel"] is True
    api._export_job = None
