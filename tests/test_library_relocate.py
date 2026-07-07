"""relocate_robot / merge_robots: rename a robot (moving its folder tree with it),
merge duplicates, and tidy - always inside library_root(), transactionally, with the
old identity recorded as an alias so a stray old-named folder re-merges on scan.

Fully synthetic (a SUMMARY.DG marks each snapshot a backup), so nothing here needs
SampleBackup or a network. Every disk test sets library_root or the guards refuse."""
import json
import os

import pytest

from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)


def _snap(robot_dir, date, time, *, files=1, bytes_=10, taken=None,
          robot="", line="", plant="", ts=None):
    """One dated <date>/<time> snapshot with precise files/bytes (so the duplicate
    vs conflict rule can be exercised exactly)."""
    d = robot_dir / date / time
    d.mkdir(parents=True, exist_ok=True)
    (d / "SUMMARY.DG").write_text("x", encoding="utf-8")
    (d / "backup.json").write_text(json.dumps({
        "robot": robot, "line": line, "plant": plant,
        "taken": taken or (date.replace("_", "-") + "T" + time.replace("_", ":")),
        "type": "all of above", "files": files, "bytes": bytes_, "source": "ftp",
    }), encoding="utf-8")
    (d / "notes.txt").write_text("note " + time + "\n", encoding="utf-8")
    if ts:
        os.utime(d, (ts, ts))
    return d


def _sidecar(robot_dir, rid, plant, line, robot, **extra):
    robot_dir.mkdir(parents=True, exist_ok=True)
    data = {"schema": 1, "id": rid, "plant": plant, "line": line, "robot": robot,
            "model": "", "f_number": "", "ips": [],
            "ftp": {"user": "", "passive": True}, "notes": ""}
    data.update(extra)
    (robot_dir / "robot.json").write_text(json.dumps(data), encoding="utf-8")


def _lib(tmp_path):
    root = tmp_path / "lib"
    settings.set_value("library_root", str(root))
    return root


def _summary(name):
    return '<H2><A NAME="1">Ethernet</A></H2><PRE>\n$HOSTNAME : ' + name + '\n</PRE>\n'


def _ip_robot(root, plant, line, ipname, realname, rid):
    """A robot stored under an IP-ish name whose latest backup's SUMMARY.DG knows
    its real $HOSTNAME (so resolve_names can propose the real name)."""
    base = root / plant / line / ipname
    d = base / "2026_01_01" / "12_00_00"
    d.mkdir(parents=True)
    (d / "SUMMARY.DG").write_text(_summary(realname), encoding="cp1252")
    (d / "backup.json").write_text(json.dumps({
        "robot": ipname, "line": line, "plant": plant,
        "taken": "2026-01-01T12:00:00", "files": 1, "bytes": 10}), encoding="utf-8")
    _sidecar(base, rid, plant, line, ipname)
    return base


# -- clean rename ---------------------------------------------------------------

def test_relocate_clean_rename_moves_folder_and_mirror(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=3, robot="R1", line="L", plant="P", ts=1_600_000_000)
    _snap(r1, "2026_02_02", "09_30_00", files=4, robot="R1", line="L", plant="P", ts=1_700_000_000)
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    res = library.relocate_robot(e["id"], "P", "L", "R1NEW")
    assert res["action"] == "renamed" and res["id"] == e["id"]   # id preserved
    assert not (root / "P" / "L" / "R1").exists()                # old folder gone
    assert (root / "P" / "L" / "R1NEW").is_dir()                 # new folder present
    assert (root / "P" / "L" / "Latest" / "R1NEW").is_dir()      # mirror regenerated at new name
    assert not (root / "P" / "L" / "Latest" / "R1").exists()     # old mirror gone

    e2 = library.get_robot(e["id"])
    assert e2["robot"] == "R1NEW"
    assert len(e2["backups"]) == 2
    for b in e2["backups"]:
        assert os.path.join("P", "L", "R1NEW") in b["path"]      # history retargeted
    assert e2["latest_path"].endswith(os.path.join("2026_02_02", "09_30_00"))
    assert "Latest" not in e2["latest_path"]
    assert os.path.join("P", "L", "R1NEW") in e2["history_root"]


def test_relocate_records_alias_in_entry_and_sidecar(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    library.relocate_robot(e["id"], "P", "L", "R1NEW")
    e2 = library.get_robot(e["id"])
    keys = {(a["plant"], a["line"], a["robot"]) for a in e2["aliases"]}
    assert ("P", "L", "R1") in keys                              # old identity remembered
    rj = json.loads((root / "P" / "L" / "R1NEW" / "robot.json").read_text(encoding="utf-8"))
    assert any(a["robot"] == "R1" for a in rj.get("aliases", []))   # and travels in the sidecar


def test_relocate_change_line_moves_under_new_line(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    res = library.relocate_robot(e["id"], "P", "L2", "R1")
    assert res["action"] == "renamed"
    assert (root / "P" / "L2" / "R1").is_dir()
    assert (root / "P" / "L2" / "Latest" / "R1").is_dir()
    assert not (root / "P" / "L" / "R1").exists()
    assert not (root / "P" / "L").exists()                       # emptied old line pruned
    assert library.get_robot(e["id"])["line"] == "L2"


def test_relocate_noop_when_identity_unchanged(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    res = library.relocate_robot(e["id"], "P", "L", "R1")
    assert res["action"] == "noop"
    assert (root / "P" / "L" / "R1").is_dir()                    # nothing moved


def test_relocate_no_folder_entry_just_renames_metadata(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    _lib(tmp_path)
    e = library.add_robot({"robot": "GHOST", "plant": "P", "line": "L"})   # no folder on disk
    res = library.relocate_robot(e["id"], "P", "L", "GHOST2")
    assert res["action"] == "renamed"
    e2 = library.get_robot(e["id"])
    assert e2["robot"] == "GHOST2"
    assert ("P", "L", "GHOST") in {(a["plant"], a["line"], a["robot"]) for a in e2["aliases"]}


# -- merge ----------------------------------------------------------------------

def test_relocate_into_existing_merges_skipping_duplicate_datetime(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=3, robot="R1", line="L", plant="P")   # unique
    _snap(r1, "2026_02_02", "09_30_00", files=9, robot="R1", line="L", plant="P")   # dup of R2's
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=9, robot="R2", line="L", plant="P")   # identical
    _snap(r2, "2026_03_03", "10_00_00", files=5, robot="R2", line="L", plant="P")   # unique
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.relocate_robot("rid-1", "P", "L", "R2")        # rename R1 -> R2 (collision)
    assert res["action"] == "merged"
    assert res["id"] == "rid-2" and res["removed_id"] == "rid-1"  # survivor = destination owner
    assert len(res["moved"]) == 1 and len(res["skipped"]) == 1 and res["conflicts"] == []
    assert library.get_robot("rid-1") is None
    surv = library.get_robot("rid-2")
    assert len(surv["backups"]) == 3                             # 3 distinct snapshots
    assert not (root / "P" / "L" / "R1").exists()               # emptied source removed
    assert "R1" in {a["robot"] for a in surv["aliases"]}        # alias recorded
    assert (root / "P" / "L" / "Latest" / "R2").is_dir()       # mirror regenerated


def test_merge_robots_explicit_two_entries(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=1, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=2, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-1", "rid-2")                 # fold R2 into R1
    assert res["action"] == "merged" and res["id"] == "rid-1"
    assert library.get_robot("rid-2") is None
    surv = library.get_robot("rid-1")
    assert len(surv["backups"]) == 2                            # both snaps now under R1
    assert surv["backups"][0]["taken"] == "2026-02-02T09:30:00"  # newest first
    assert not (root / "P" / "L" / "R2").exists()              # secondary folder gone
    assert "R2" in {a["robot"] for a in surv["aliases"]}
    assert (root / "P" / "L" / "Latest" / "R1").is_dir()      # primary mirror regenerated


def test_merge_conflicting_snapshot_flagged_not_dropped(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_02_02", "09_30_00", files=9, bytes_=900, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=7, bytes_=700, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")                 # try to fold R1 into R2
    assert res["action"] == "merged"
    assert len(res["conflicts"]) == 1 and res["moved"] == [] and res["skipped"] == []
    c = res["conflicts"][0]
    assert c["src"]["files"] == 9 and c["dst"]["files"] == 7     # both stats reported
    # the differing source copy is RETAINED for manual resolution, not silently dropped
    assert (root / "P" / "L" / "R1" / "2026_02_02" / "09_30_00").is_dir()
    # and because real data remains, the secondary entry is KEPT (never orphaned)
    assert res["secondary_removed"] is False and res["removed_id"] is None
    assert library.get_robot("rid-1") is not None
    assert (root / "P" / "L" / "Latest" / "R1").is_dir()        # its mirror kept too


def test_merge_drops_folderless_secondary(monkeypatch, tmp_path):
    """Merging an empty placeholder entry (no folder on disk) just drops it - no
    crash, and the primary survives."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    ghost = library.add_robot({"robot": "GHOST", "plant": "P", "line": "L"})   # no folder

    res = library.merge_robots("rid-1", ghost["id"])
    assert res["action"] == "merged" and res["secondary_removed"] is True
    assert library.get_robot(ghost["id"]) is None
    assert library.get_robot("rid-1") is not None


def test_merge_refuses_cross_line(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L1" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L1", plant="P")
    _sidecar(r1, "rid-1", "P", "L1", "R1")
    r2 = root / "P" / "L2" / "R2"
    _snap(r2, "2026_01_01", "12_00_00", robot="R2", line="L2", plant="P")
    _sidecar(r2, "rid-2", "P", "L2", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-1", "rid-2")
    assert res["action"] == "refused" and res["reason"] == "cross-line"
    assert library.get_robot("rid-1") and library.get_robot("rid-2")   # both intact
    assert r1.is_dir() and r2.is_dir()                                  # disk untouched


# -- alias re-match on scan -----------------------------------------------------

def test_alias_rematch_on_scan_does_not_duplicate(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]
    library.relocate_robot(e["id"], "P", "L", "R1B")            # rename (records alias R1)

    # a coworker drops a stray copy of the OLD-named folder back into the tree
    stray = root / "P" / "L" / "R1"
    _snap(stray, "2025_12_31", "23_00_00", robot="R1", line="L", plant="P")   # no sidecar

    data = library.scan_library_root(root)
    assert len(data["robots"]) == 1                            # NOT duplicated
    e2 = data["robots"][0]
    assert e2["robot"] == "R1B"
    paths = " ".join(b["path"] for b in e2["backups"])
    assert os.path.join("L", "R1B") in paths                  # the renamed history
    assert os.path.join("L", "R1", "2025_12_31") in paths     # AND the stray, folded in


def test_find_match_prefers_current_name_over_alias():
    data = {"robots": [
        {"id": "a", "robot": "R1", "line": "L", "aliases": []},
        {"id": "b", "robot": "R2", "line": "L", "aliases": [{"plant": "", "line": "L", "robot": "R1"}]},
    ]}
    m = library._find_match(data, {"robot": "R1", "line": "L"})
    assert m["id"] == "a"                                       # tier-1 current name wins
    m2 = library._find_match(data, {"robot": "R2OLD", "line": "L"})
    assert m2 is None
    data["robots"][1]["aliases"].append({"plant": "", "line": "L", "robot": "R2OLD"})
    assert library._find_match(data, {"robot": "R2OLD", "line": "L"})["id"] == "b"   # tier-2 alias


# -- guards ---------------------------------------------------------------------

def test_relocate_refuses_target_outside_root(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inside = tmp_path / "lib"
    inside.mkdir()
    settings.set_value("library_root", str(inside))
    outside = tmp_path / "elsewhere" / "R9"
    snap = _snap(outside, "2026_01_01", "12_00_00", robot="R9", line="L", plant="P")
    e = library.add_robot({"robot": "R9", "plant": "P", "line": "L",
                           "history_root": str(outside), "latest_path": str(snap),
                           "backups": [{"path": str(snap), "taken": "2026-01-01T12:00:00"}]})

    with pytest.raises(library.PathGuard):
        library.relocate_robot(e["id"], "P", "L", "R9NEW")
    assert outside.is_dir() and snap.is_dir()                   # nothing moved


def test_move_tree_cross_volume_fallback(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]

    def boom(a, b):
        raise OSError("simulated cross-device rename")
    monkeypatch.setattr(library.os, "rename", boom)             # force the copy-verify-delete path

    res = library.relocate_robot(e["id"], "P", "L", "R1NEW")
    assert res["action"] == "renamed"
    assert not (root / "P" / "L" / "R1").exists()
    assert (root / "P" / "L" / "R1NEW" / "2026_01_01" / "12_00_00" / "SUMMARY.DG").is_file()


def test_merge_preserves_non_dated_content_in_source(monkeypatch, tmp_path):
    """A merge must move the dated snapshots but NEVER blow away stray non-dated
    content in the source folder; the entry is kept so that data isn't orphaned."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    (r1 / "readme.txt").write_text("keep me", encoding="utf-8")   # stray non-dated content
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_03_03", "10_00_00", robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")                  # fold R1 into R2
    assert res["secondary_removed"] is False                      # not fully consumed
    assert r1.is_dir() and (r1 / "readme.txt").is_file()          # stray content preserved
    assert library.get_robot("rid-1") is not None                # entry kept, data not orphaned
    surv = library.get_robot("rid-2")
    assert any("2026_01_01" in b["path"] for b in surv["backups"])   # the dated snap did merge


def test_relocate_refuses_sanitization_collision(monkeypatch, tmp_path):
    """Two different robot names that sanitize to the same folder must NOT merge
    into each other - that would fold one robot into an unrelated one."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    a = root / "P" / "L" / "R2000_1"        # an existing robot at this sanitized folder
    _snap(a, "2026_01_01", "12_00_00", robot="R2000_1", line="L", plant="P")
    _sidecar(a, "rid-a", "P", "L", "R2000_1")
    b = root / "P" / "L" / "BBB"
    _snap(b, "2026_02_02", "09_30_00", robot="BBB", line="L", plant="P")
    _sidecar(b, "rid-b", "P", "L", "BBB")
    library.scan_library_root(root)

    with pytest.raises(ValueError):
        library.relocate_robot("rid-b", "P", "L", "R2000:1")     # ":" sanitizes to R2000_1 (A's folder)
    assert library.get_robot("rid-a") and library.get_robot("rid-b")   # both intact
    assert a.is_dir() and b.is_dir()                                    # nothing moved


def test_relocate_refuses_reserved_latest_name(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    with pytest.raises(ValueError):
        library.relocate_robot("rid-1", "P", "L", "Latest")      # reserved mirror token


# -- api endpoints --------------------------------------------------------------

def test_open_path_guarded(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    inside = root / "P" / "L" / "R1"
    inside.mkdir(parents=True)
    calls = []
    monkeypatch.setattr("os.startfile", lambda s: calls.append(s), raising=False)
    api = Api()

    ok = api.open_path(str(inside))
    assert ok["ok"] is True and calls == [str(inside.resolve())]    # opened the real folder

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bad = api.open_path(str(outside))
    assert bad["ok"] is False and bad["error"]["code"] == "BAD_PATH"  # outside root refused

    missing = api.open_path(str(inside / "nope"))
    assert missing["ok"] is False and missing["error"]["code"] == "BAD_PATH"   # non-existent
    assert calls == [str(inside.resolve())]                          # only the valid call fired


def test_lib_resolve_names_classifies_clean_vs_collision(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    _ip_robot(root, "P", "L", "10_1_1_1", "ALPHA", "rid-a")        # resolves to ALPHA (clean)
    _ip_robot(root, "P", "L", "10_1_1_2", "BETA", "rid-b")         # resolves to BETA, collides:
    bx = root / "P" / "L" / "BETA"                                 # an existing BETA on line L
    _snap(bx, "2026_01_01", "12_00_00", robot="BETA", line="L", plant="P")
    _sidecar(bx, "rid-c", "P", "L", "BETA")
    library.scan_library_root(root)

    res = Api().lib_resolve_names(["rid-a", "rid-b"])["data"]
    items = {it["id"]: it for it in res["items"]}
    assert items["rid-a"]["proposed"] == "ALPHA" and items["rid-a"]["action"] == "rename"
    assert items["rid-b"]["proposed"] == "BETA" and items["rid-b"]["action"] == "merge"
    assert items["rid-b"]["merge_into"] == "rid-c"


def test_lib_relocate_endpoint_returns_merge_action(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)
    assert len(library.list_robots()["robots"]) == 2

    res = Api().lib_relocate("rid-1", "P", "L", "R2")["data"]      # rename onto an existing name
    assert res["action"] == "merged" and res["id"] == "rid-2"
    assert len(library.list_robots()["robots"]) == 1               # the duplicate collapsed


def test_lib_merge_endpoint(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = Api().lib_merge("rid-1", ["rid-2"])["data"]
    assert len(res["merged"]) == 1 and res["refused"] == [] and res["failed"] == []
    assert library.get_robot("rid-2") is None
    assert len(library.get_robot("rid-1")["backups"]) == 2


def test_lib_apply_renames_batch(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "OLD1"
    _snap(r1, "2026_01_01", "12_00_00", robot="OLD1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "OLD1")
    library.scan_library_root(root)

    res = Api().lib_apply_renames([{"id": "rid-1", "plant": "P", "line": "L", "robot": "NEW1"}])["data"]
    assert len(res["renamed"]) == 1 and res["failed"] == [] and res["merged"] == []
    assert library.get_robot("rid-1")["robot"] == "NEW1"
    assert (root / "P" / "L" / "NEW1").is_dir() and not (root / "P" / "L" / "OLD1").exists()


def test_lib_apply_renames_bulk_move_to_new_line(monkeypatch, tmp_path):
    """The bulk 'move to' flow: same names, new plant/line — folders and dated
    backups travel (structure created as needed); a same-named robot already at
    the destination merges by the standard duplicate rules."""
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    for i in (1, 2):
        r = root / "P" / "OLD" / f"R{i}"
        _snap(r, f"2026_01_0{i}", "12_00_00", files=i, robot=f"R{i}", line="OLD", plant="P")
        _sidecar(r, f"rid-{i}", "P", "OLD", f"R{i}")
    dst2 = root / "P2" / "NEW" / "R2"                 # a same-named R2 already at the destination
    _snap(dst2, "2026_03_03", "10_00_00", files=9, robot="R2", line="NEW", plant="P2")
    _sidecar(dst2, "rid-d2", "P2", "NEW", "R2")
    library.scan_library_root(root)

    items = [{"id": "rid-1", "plant": "P2", "line": "NEW", "robot": "R1"},
             {"id": "rid-2", "plant": "P2", "line": "NEW", "robot": "R2"}]
    res = Api().lib_apply_renames(items)["data"]
    assert len(res["renamed"]) == 1 and len(res["merged"]) == 1 and res["failed"] == []
    assert (root / "P2" / "NEW" / "R1" / "2026_01_01" / "12_00_00").is_dir()   # moved whole
    assert not (root / "P" / "OLD").exists()          # emptied source line pruned
    surv = library.get_robot("rid-d2")                # destination owner survived the merge
    assert len(surv["backups"]) == 2
    assert library.get_robot("rid-1")["line"] == "NEW"


def test_lib_merge_endpoint_multi_secondary_and_string_coercion(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    for i, (date, time) in enumerate([("2026_01_01", "12_00_00"),
                                      ("2026_02_02", "09_30_00"),
                                      ("2026_03_03", "10_00_00")], start=1):
        r = root / "P" / "L" / f"R{i}"
        _snap(r, date, time, files=i, robot=f"R{i}", line="L", plant="P")
        _sidecar(r, f"rid-{i}", "P", "L", f"R{i}")
    library.scan_library_root(root)

    res = Api().lib_merge("rid-1", ["rid-2", "rid-3"])["data"]           # multi-secondary batch
    assert len(res["merged"]) == 2 and res["refused"] == [] and res["failed"] == []
    assert library.get_robot("rid-2") is None and library.get_robot("rid-3") is None
    assert len(library.get_robot("rid-1")["backups"]) == 3

    r4 = root / "P" / "L" / "R4"
    _snap(r4, "2026_04_04", "11_00_00", files=4, robot="R4", line="L", plant="P")
    _sidecar(r4, "rid-4", "P", "L", "R4")
    library.scan_library_root(root)
    res2 = Api().lib_merge("rid-1", "rid-4")["data"]                     # bare-string coercion
    assert len(res2["merged"]) == 1 and library.get_robot("rid-4") is None


# -- merge data-safety (2026-07-06 field-feedback fixes) --------------------------
# The duplicate-skip branch is the ONLY place a merge deletes source data. It must
# never fire on metadata equality alone: missing/corrupt backup.json on both sides
# used to compare (0,0)==(0,0) -> "identical" -> intact source deleted. Same for a
# partial destination left by an interrupted copy. These pin the strict rule:
# skip (and drop the source copy) ONLY when both sidecars are readable, stats
# match, AND the trees verify file-for-file in both directions.

def test_merge_duplicate_missing_backup_json_is_conflict_not_deleted(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    d1 = _snap(r1, "2026_02_02", "09_30_00", files=9, robot="R1", line="L", plant="P")
    (d1 / "backup.json").unlink()
    (d1 / "REAL_DATA.LS").write_text("precious program", encoding="utf-8")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    d2 = _snap(r2, "2026_02_02", "09_30_00", files=9, robot="R2", line="L", plant="P")
    (d2 / "backup.json").unlink()
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")                 # fold R1 into R2
    assert len(res["conflicts"]) == 1 and res["skipped"] == []
    assert (d1 / "REAL_DATA.LS").is_file()                       # intact source retained
    assert library.get_robot("rid-1") is not None                # entry kept with its leftovers


def test_merge_retry_after_partial_destination_keeps_source(monkeypatch, tmp_path):
    """A partial destination snapshot (interrupted copy) that already holds a
    matching backup.json must be treated as a conflict, never as an identical
    duplicate whose source can be dropped."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    d1 = _snap(r1, "2026_02_02", "09_30_00", files=9, robot="R1", line="L", plant="P")
    (d1 / "REAL_DATA.LS").write_text("precious program", encoding="utf-8")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_03_03", "10_00_00", files=5, robot="R2", line="L", plant="P")
    part = r2 / "2026_02_02" / "09_30_00"                        # partial: ONLY backup.json arrived
    part.mkdir(parents=True)
    part.joinpath("backup.json").write_bytes((d1 / "backup.json").read_bytes())
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")
    assert len(res["conflicts"]) == 1 and res["skipped"] == []
    assert (d1 / "REAL_DATA.LS").is_file()                       # source survives the retry


def test_merge_verified_identical_duplicate_still_skipped(monkeypatch, tmp_path):
    """The useful dedup behavior stays: a genuinely identical duplicate (readable
    stats, equal, contents verify) is skipped and its redundant source dropped."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_02_02", "09_30_00", files=9, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=9, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")
    assert len(res["skipped"]) == 1 and res["conflicts"] == []
    assert res["secondary_removed"] is True
    assert library.get_robot("rid-1") is None                    # fully consumed
    assert not (root / "P" / "L" / "R1").exists()


def test_merge_same_size_different_content_is_conflict(monkeypatch, tmp_path):
    """Equal backup.json stats with subtly different real contents (an edited
    program of the same byte size) must be a conflict - content is compared, not
    just sizes, before a source copy may be dropped."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    d1 = _snap(r1, "2026_02_02", "09_30_00", files=9, robot="R1", line="L", plant="P")
    (d1 / "PROG.LS").write_text("R[1]=1 ;", encoding="utf-8")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    d2 = _snap(r2, "2026_02_02", "09_30_00", files=9, robot="R2", line="L", plant="P")
    (d2 / "PROG.LS").write_text("R[1]=2 ;", encoding="utf-8")    # same size, different byte
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    res = library.merge_robots("rid-2", "rid-1")
    assert len(res["conflicts"]) == 1 and res["skipped"] == []
    assert (d1 / "PROG.LS").is_file()                            # differing source retained


def test_merge_copy_failure_leaves_source_and_registry_intact(monkeypatch, tmp_path):
    """A copy failure mid-merge must leave the source snapshot on disk, nothing at
    the destination's FINAL snapshot name (a partial there would fool a retry),
    and both entries still in the library."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=3, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_03_03", "10_00_00", files=5, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)

    def boom_rename(a, b):
        raise OSError("simulated cross-device rename")
    from pathlib import Path as _P

    def partial_copy(src, dst, **kw):                            # copies a bit, then dies
        _P(dst).mkdir(parents=True, exist_ok=True)
        (_P(dst) / "half.txt").write_text("partial", encoding="utf-8")
        raise OSError("simulated copy failure")
    monkeypatch.setattr(library.os, "rename", boom_rename)
    monkeypatch.setattr(library.shutil, "copytree", partial_copy)

    with pytest.raises(OSError):
        library.merge_robots("rid-2", "rid-1")
    assert (r1 / "2026_01_01" / "12_00_00" / "SUMMARY.DG").is_file()   # source intact
    assert not (r2 / "2026_01_01" / "12_00_00").exists()               # nothing at the final name
    assert library.get_robot("rid-1") and library.get_robot("rid-2")   # registry untouched


def test_scan_ignores_transient_staging_dirs(monkeypatch, tmp_path):
    """A crash between copy and replace can leave <name>.__part (move staging) or
    <name>.__tmp (mirror regen) dirs on disk. The scanner must never register
    them as robots/backups — the next move cleans them up."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    ghost = root / "P" / "L" / "R2.__part"                       # crash residue
    _snap(ghost, "2026_02_02", "09_30_00", robot="R2", line="L", plant="P")
    tmpd = root / "P" / "L" / "R3.__tmp"
    _snap(tmpd, "2026_03_03", "10_00_00", robot="R3", line="L", plant="P")

    data = library.scan_library_root(root)
    names = [e.get("robot", "") for e in data["robots"]]
    assert names == ["R1"]                                       # no phantom robots
    assert ghost.is_dir() and tmpd.is_dir()                      # residue untouched


def test_verify_tree_semantics(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello", encoding="utf-8")
    (src / "sub" / "b.txt").write_text("world", encoding="utf-8")
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    (dst / "a.txt").write_text("hello", encoding="utf-8")
    (dst / "sub" / "b.txt").write_text("world", encoding="utf-8")

    assert library._verify_tree(src, dst) is True
    assert library._verify_tree(src, dst, strict=True) is True

    (dst / "extra.txt").write_text("x", encoding="utf-8")        # dst-only file
    assert library._verify_tree(src, dst) is True                # move-path check: src ⊆ dst
    assert library._verify_tree(src, dst, strict=True) is False  # pre-delete bar: sets must match
    (dst / "extra.txt").unlink()

    (dst / "a.txt").write_text("HELLO", encoding="utf-8")        # same size, different content
    assert library._verify_tree(src, dst) is True                # size check alone can't see it
    assert library._verify_tree(src, dst, strict=True) is False  # content compare does
    (dst / "a.txt").write_text("hello", encoding="utf-8")

    (dst / "sub" / "b.txt").unlink()                             # missing file
    assert library._verify_tree(src, dst) is False
    assert library._verify_tree(src, dst, strict=True) is False


# -- merge metadata survival ------------------------------------------------------

def test_merge_folds_secondary_metadata_into_primary(monkeypatch, tmp_path):
    """Merging must not lose the secondary's IPs/notes/model/ftp user - in the
    fleet-typical case the discovery-created entry (holding the real IP) is the
    one folded in. hidden must NOT propagate (a visible robot can't vanish)."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=1, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=2, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)
    library.update_robot("rid-2", {"ips": ["10.1.1.2"], "model": "R-2000iC", "f_number": "F123",
                                   "notes": "cell 4 door side",
                                   "ftp": {"user": "robot", "passive": True}, "hidden": True})

    res = library.merge_robots("rid-1", "rid-2")
    assert res["action"] == "merged"
    surv = library.get_robot("rid-1")
    assert "10.1.1.2" in surv["ips"]                             # the only recorded IP survives
    assert surv["model"] == "R-2000iC" and surv["f_number"] == "F123"
    assert surv["ftp"]["user"] == "robot"
    assert "cell 4 door side" in surv["notes"]
    assert surv["hidden"] is False                               # sec's hidden not propagated


def test_merge_metadata_never_clobbers_primary_values(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", files=1, robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    r2 = root / "P" / "L" / "R2"
    _snap(r2, "2026_02_02", "09_30_00", files=2, robot="R2", line="L", plant="P")
    _sidecar(r2, "rid-2", "P", "L", "R2")
    library.scan_library_root(root)
    library.update_robot("rid-1", {"ips": ["10.1.1.1"], "model": "M-900iB",
                                   "notes": "prim note", "ftp": {"user": "admin", "passive": True}})
    library.update_robot("rid-2", {"ips": ["10.1.1.2"], "model": "R-2000iC",
                                   "notes": "sec note", "ftp": {"user": "robot", "passive": True}})

    library.merge_robots("rid-1", "rid-2")
    surv = library.get_robot("rid-1")
    assert surv["model"] == "M-900iB"                            # prim's own value kept
    assert surv["ftp"]["user"] == "admin"
    assert surv["ips"] == ["10.1.1.1", "10.1.1.2"]               # union, prim's first
    assert "prim note" in surv["notes"] and "sec note" in surv["notes"]   # neither note lost


# -- update_robot latest_path guard ------------------------------------------------

def test_update_robot_keeps_valid_latest_path_over_stale_patch(monkeypatch, tmp_path):
    """The edit modal re-sends its (readonly, pre-rename) folder field after a
    relocate has already retargeted the entry. A patch must never downgrade a
    valid latest_path to one that no longer exists on disk."""
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    r1 = root / "P" / "L" / "R1"
    _snap(r1, "2026_01_01", "12_00_00", robot="R1", line="L", plant="P")
    _sidecar(r1, "rid-1", "P", "L", "R1")
    library.scan_library_root(root)
    e = library.list_robots()["robots"][0]
    stale_path = e["latest_path"]                                # pre-rename snapshot path

    library.relocate_robot(e["id"], "P", "L", "R1NEW")
    library.update_robot(e["id"], {"notes": "edited", "latest_path": stale_path})

    e2 = library.get_robot(e["id"])
    assert e2["notes"] == "edited"                               # rest of the patch applied
    assert "R1NEW" in e2["latest_path"]                          # retargeted path kept
    import pathlib
    assert pathlib.Path(e2["latest_path"]).is_dir()
    assert library.list_robots()["robots"][0]["stale"] is False  # no phantom 'missing' pill


def test_update_robot_applies_latest_path_when_current_blank_or_gone(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = _lib(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    e = library.add_robot({"robot": "GHOST", "plant": "P", "line": "L"})   # no folder, blank path
    somewhere = root / "P" / "L" / "GHOST" / "2026_01_01" / "12_00_00"
    library.update_robot(e["id"], {"latest_path": str(somewhere)})
    assert library.get_robot(e["id"])["latest_path"] == str(somewhere)     # blank current -> applied
