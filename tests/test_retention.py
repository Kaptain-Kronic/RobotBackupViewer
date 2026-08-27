"""retention_verdicts / set_pinned / staging_status - the cleanup engine.

Verdict tests build entry dicts by hand (the engine is a pure function of the
listing - no disk at all). Pin-survival and staging-status tests touch a tmp
tree the test_library_relocate way. Fully synthetic, identifier-clean."""
import json

from backupviewer import library, settings

NOW = "2026-08-11T12:00:00"       # verdicts take `now`, so tests never drift
# days=90 from NOW -> cutoff 2026-05-13T12:00:00


def _iso(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)


def _rec(taken, *, partial=False, stale=False, bytes_=1000):
    r = {"path": "p-" + taken, "taken": taken, "type": "", "files": 1,
         "bytes": bytes_, "source": "ftp", "note": ""}
    if partial:
        r["partial"] = True
    if stale:
        r["stale"] = True
    return r


def _entry(backups, *, pins=None, hidden=False, rid="rid-1", robot="R1"):
    e = {"id": rid, "plant": "P", "line": "L", "robot": robot,
         "device_type": "robot", "backups": backups, "hidden": hidden}
    if pins:
        e["pins"] = pins
    return e


def _by_taken(items):
    return {it["taken"]: it for it in items}


def test_latest_keep_and_superseded():
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2026-08-01T10:00:00"),          # newest-first, like the scan emits
        _rec("2026-05-01T10:00:00"),
        _rec("2025-06-01T10:00:00"),
        _rec("2025-01-01T10:00:00"),
    ])], 90, 2, now=NOW))
    assert v["2026-08-01T10:00:00"]["reason"] == "recent"  # fresh latest: recency wins,
    assert "warn" not in v["2026-08-01T10:00:00"]          # so the fold stays OLD-only
    assert v["2026-05-01T10:00:00"]["reason"] == "kept"    # old but within keep=2
    assert v["2025-06-01T10:00:00"]["verdict"] == "candidate"
    assert v["2025-06-01T10:00:00"]["group"] == "superseded"
    assert v["2025-01-01T10:00:00"]["verdict"] == "candidate"


def test_old_but_latest_warns():
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2024-01-01T10:00:00"),
        _rec("2023-01-01T10:00:00"),
    ])], 90, 1, now=NOW))
    top = v["2024-01-01T10:00:00"]
    assert top["reason"] == "latest" and top["warn"] is True   # the take-a-fresh-backup bridge
    assert v["2023-01-01T10:00:00"]["verdict"] == "candidate"  # keep=1 = latest only


def test_keep_zero_still_protects_latest():
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2024-01-01T10:00:00"),
        _rec("2023-01-01T10:00:00"),
    ])], 90, 0, now=NOW))
    assert v["2024-01-01T10:00:00"]["reason"] == "latest"      # unconditionally shielded
    assert v["2023-01-01T10:00:00"]["verdict"] == "candidate"


def test_partials_always_candidates():
    """A pull that died is junk from day one — partials skip the age gate
    entirely (the UI shows each one's age instead of gating on it)."""
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2026-08-01T10:00:00"),
        _rec("2026-07-20T10:00:00", partial=True),   # days-fresh, still a candidate
        _rec("2024-02-02T10:00:00", partial=True),
    ])], 90, 2, now=NOW))
    assert v["2026-07-20T10:00:00"]["verdict"] == "candidate"
    assert v["2026-07-20T10:00:00"]["group"] == "partial"
    assert v["2024-02-02T10:00:00"]["group"] == "partial"


def test_partial_newest_flag_marks_broken():
    """newest=True on a partial = the robot's most recent snapshot is a dead
    pull, so its current state was never captured (the backup-broken button's
    target, and what auto-stage deliberately skips)."""
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2026-08-10T10:00:00", partial=True),   # newer than every completed
        _rec("2026-08-01T10:00:00"),
        _rec("2024-01-01T10:00:00", partial=True),
    ])], 90, 2, now=NOW))
    assert v["2026-08-10T10:00:00"]["verdict"] == "candidate"
    assert v["2026-08-10T10:00:00"]["newest"] is True
    assert v["2024-01-01T10:00:00"]["newest"] is False


def test_sole_snapshot_and_last_trace():
    sole_complete = library.retention_verdicts(
        [_entry([_rec("2024-01-01T10:00:00")])], 90, 2, now=NOW)
    assert sole_complete[0]["reason"] == "latest"              # latest outranks only
    assert sole_complete[0]["warn"] is True                    # latest is old by construction

    sole_partial = library.retention_verdicts(
        [_entry([_rec("2024-01-01T10:00:00", partial=True)])], 90, 2, now=NOW)
    assert sole_partial[0]["reason"] == "only"
    assert sole_partial[0]["warn"] is True                     # broken + irreplaceable

    fresh_sole = library.retention_verdicts(                   # age changes nothing here
        [_entry([_rec("2026-08-10T10:00:00", partial=True)])], 90, 2, now=NOW)
    assert fresh_sole[0]["reason"] == "only" and fresh_sole[0]["warn"] is True

    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2024-06-01T10:00:00", partial=True),
        _rec("2024-01-01T10:00:00", partial=True),
    ])], 90, 2, now=NOW))
    assert v["2024-06-01T10:00:00"]["reason"] == "last"        # last trace, broken or not
    assert v["2024-06-01T10:00:00"]["warn"] is True
    assert v["2024-01-01T10:00:00"]["verdict"] == "candidate"


def test_pinned_stale_and_undated_never_candidates():
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2026-08-01T10:00:00"),
        _rec("2026-06-01T10:00:00"),
        _rec("2024-03-03T10:00:00"),                # old, 3rd completed -> pinned shields it
        _rec("2024-02-02T10:00:00", stale=True),    # offline drive? never move blind
        _rec("", bytes_=0),                         # undated: age unprovable
    ], pins=["2024-03-03T10:00:00"])], 90, 2, now=NOW))
    assert v["2024-03-03T10:00:00"]["reason"] == "pinned"
    assert v["2024-03-03T10:00:00"]["pinned"] is True
    assert v["2024-02-02T10:00:00"]["reason"] == "offline"
    assert v[""]["reason"] == "undated"


def test_cutoff_is_strictly_older_than():
    v = _by_taken(library.retention_verdicts([_entry([
        _rec("2026-08-01T10:00:00"),
        _rec("2026-05-13T12:00:00"),               # exactly the 90-day cutoff
        _rec("2026-05-13T11:59:59"),               # one second older
    ])], 90, 1, now=NOW))
    assert v["2026-05-13T12:00:00"]["reason"] == "recent"
    assert v["2026-05-13T11:59:59"]["verdict"] == "candidate"


def test_hidden_and_empty_entries_skipped():
    items = library.retention_verdicts([
        _entry([_rec("2024-01-01T10:00:00")], hidden=True),
        _entry([], rid="rid-2", robot="R2"),
    ], 90, 2, now=NOW)
    assert items == []


# -- pins on the real registry ----------------------------------------------------

def _seed(root, rid="rid-1"):
    r1 = root / "P" / "L" / "R1"
    for date, time in (("2026_01_01", "12_00_00"), ("2024_01_01", "12_00_00")):
        d = r1 / date / time
        d.mkdir(parents=True)
        (d / "SUMMARY.DG").write_text("x", encoding="utf-8")
        (d / "backup.json").write_text(json.dumps({
            "robot": "R1", "line": "L", "plant": "P",
            "taken": date.replace("_", "-") + "T" + time.replace("_", ":"),
            "type": "all of above", "files": 1, "bytes": 10, "source": "ftp",
        }), encoding="utf-8")
    (r1 / "robot.json").write_text(json.dumps({
        "schema": 1, "id": rid, "plant": "P", "line": "L", "robot": "R1",
        "model": "", "f_number": "", "ips": [],
        "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")
    return r1


def test_pin_survives_rescan(monkeypatch, tmp_path):
    """backups[] is rebuilt wholesale from disk every scan - a pin that lived
    inside it would silently vanish. Pins ride the ENTRY, like favorite."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    settings.set_value("library_root", str(root))
    _seed(root)
    library.scan_library_root(root)

    assert library.set_pinned("rid-1", "2024-01-01T12:00:00", True) is not None
    assert library.set_pinned("rid-1", "2999-01-01T00:00:00", True) is None  # no such snapshot
    assert library.set_pinned("rid-x", "2024-01-01T12:00:00", True) is None  # no such robot

    library.scan_library_root(root)                       # the wholesale rebuild
    e = library.get_robot("rid-1")
    assert e["pins"] == ["2024-01-01T12:00:00"]           # rode through

    v = _by_taken(library.retention_verdicts([e], 90, 1, now=NOW))
    assert v["2024-01-01T12:00:00"]["reason"] == "pinned"

    assert library.set_pinned("rid-1", "2024-01-01T12:00:00", False) is not None
    assert library.get_robot("rid-1")["pins"] == []       # unpin always works


# -- staging status ---------------------------------------------------------------

def test_staging_status(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)      # staging_dest reads settings now
    root = tmp_path / "lib"
    root.mkdir()
    staged = root / "_staged"
    empty = {"mode": "library", "present": False, "count": 0, "bytes": 0,
             "path": str(staged)}
    assert library.staging_status(root) == empty

    staged.mkdir()
    assert library.staging_status(root) == dict(empty, present=True)

    (staged / "P" / "L" / "R1" / "2024_01_01" / "12_00_00").mkdir(parents=True)
    (staged / "P" / "L" / "R2" / "2024_02_02" / "09_00_00").mkdir(parents=True)
    lines = [
        {"when": NOW, "rel": "P/L/R1/2024_01_01/12_00_00", "bytes": 100},
        {"when": NOW, "rel": "P/L/R1/2024_01_01/12_00_00", "bytes": 100},  # re-staged: count once
        {"when": NOW, "rel": "P/L/R2/2024_02_02/09_00_00", "bytes": 25},
        {"when": NOW, "rel": "P/L/R3/2024_03_03/08_00_00", "bytes": 999},  # hand-deleted: gone
    ]
    text = "\n".join(json.dumps(x) for x in lines) + '\n{"torn'   # crash mid-append
    (staged / "staged.log").write_text(text, encoding="utf-8")
    assert library.staging_status(root) == {"mode": "library", "present": True,
                                           "count": 2, "bytes": 125,
                                           "path": str(staged)}
