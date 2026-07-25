"""Library registry unit tests - isolated from the real %APPDATA% by pointing
settings.app_dir() at a tmp_path, so nothing here touches a user's library."""
from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path)


def test_add_and_list(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert library.list_robots()["robots"] == []

    e = library.add_robot({"robot": "R1", "plant": "P", "line": "L", "ips": ["10.0.0.1"]})
    assert e["id"]
    assert e["ftp"] == {"user": "", "passive": True}  # defaults filled

    data = library.list_robots()
    assert len(data["robots"]) == 1
    assert data["robots"][0]["robot"] == "R1"
    assert data["robots"][0]["ips"] == ["10.0.0.1"]
    # NOTE: there is deliberately no remove_robot. Files are law - dropping the
    # overlay entry does nothing durable because the next scan rebuilds it from
    # the folder. set_hidden is the real, honest mechanism (and is wired).


def test_robot_name_alias_and_scalar_ip(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    e = library.add_robot({"robot_name": "R9", "ips": "1.2.3.4"})
    assert e["robot"] == "R9" and "robot_name" not in e
    assert e["ips"] == ["1.2.3.4"]


def test_update(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    e = library.add_robot({"robot": "R1"})
    u = library.update_robot(e["id"], {"line": "BODY-1", "ips": ["1.1.1.1"]})
    assert u["line"] == "BODY-1"
    assert u["ips"] == ["1.1.1.1"]
    assert library.update_robot("nope", {"x": 1}) is None


def test_reconcile_stale(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    library.add_robot({"robot": "R1", "latest_path": str(real)})
    library.add_robot({"robot": "R2", "latest_path": str(tmp_path / "gone")})
    by = {e["robot"]: e for e in library.list_robots()["robots"]}
    assert by["R1"]["stale"] is False
    assert by["R2"]["stale"] is True


def test_register_backup_matches_then_creates(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "R1", "line": "L1"})

    library.register_backup(
        {"robot": "R1", "line": "L1", "model": "R-2000iC"},
        {"path": str(tmp_path / "b1"), "taken": "2026-06-16T10:00:00", "files": 5},
        latest_path=str(tmp_path / "latest"),
    )
    data = library.list_robots()
    assert len(data["robots"]) == 1  # matched, not duplicated
    e = data["robots"][0]
    assert len(e["backups"]) == 1
    assert e["last_backup"] == "2026-06-16T10:00:00"
    assert e["model"] == "R-2000iC"  # learned identity folded in

    # no match -> a new entry is created
    library.register_backup({"robot": "R2", "line": "L9"}, {"path": "x", "taken": "t"})
    assert len(library.list_robots()["robots"]) == 2


def test_bulk_add_dedupes(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    library.add_robot({"robot": "R1", "line": "L1"})  # already present

    res = library.bulk_add(
        [{"robot": "R1"}, {"robot": "R2"}, {"robot": "R3"}, {"robot": "R2"}],
        plant="P", line="L1",
    )
    assert len(res["added"]) == 2          # R2 + R3 (R1 exists; the 2nd R2 is an in-batch dup)
    assert "R1" in res["skipped"]
    names = {e["robot"] for e in library.list_robots()["robots"]}
    assert names == {"R1", "R2", "R3"}
    for e in res["added"]:
        assert e["plant"] == "P" and e["line"] == "L1"


def _snap(root, plant, line, robot, date, hhmmss, meta=None):
    """One dated snapshot folder; meta=None means no backup.json (a hand-import).
    Carries a SUMMARY.DG so the scan's looks_like_backup test recognises it."""
    import json
    d = root / plant / line / robot / date / hhmmss
    d.mkdir(parents=True)
    (d / "SUMMARY.DG").write_text("x", encoding="utf-8")
    if meta is not None:
        (d / "backup.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_partial_backup_never_becomes_latest(monkeypatch, tmp_path):
    """A pull that died mid-download (backup.json complete:false) is listed but
    never adopted as latest_path/last_backup - the 'library says fresh backup,
    files are missing' lie from the field. Sidecar-less imports keep today's
    behavior, and a legacy sidecar without the field counts as complete."""
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    old = _snap(root, "P", "L1", "R1", "2026_07_10", "08_00_00",
                {"taken": "2026-07-10T08:00:00", "source": "ftp"})       # legacy = complete
    part = _snap(root, "P", "L1", "R1", "2026_07_15", "09_00_00",
                 {"taken": "2026-07-15T09:00:00", "source": "ftp", "complete": False})
    imp = _snap(root, "P", "L1", "R2", "2026_07_14", "07_00_00", None)   # hand-import

    data = library.scan_library_root(root)
    by = {e["robot"]: e for e in data["robots"]}
    r1 = by["R1"]
    assert [b["path"] for b in r1["backups"]] == [str(part), str(old)]   # listed, newest first
    assert r1["backups"][0].get("partial") is True
    assert "partial" not in r1["backups"][1]
    assert r1["backups"][0]["source"] == "ftp"       # no more "import" mislabel
    assert r1["latest_path"] == str(old)             # the partial never becomes latest
    assert r1["last_backup"] == "2026-07-10T08:00:00"

    r2 = by["R2"]
    assert "partial" not in r2["backups"][0]         # imports are not partials
    assert r2["backups"][0]["source"] == "import"
    assert r2["latest_path"] == str(imp)             # and stay fully eligible


def test_partial_only_robot_still_opens(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    root = tmp_path / "lib"
    part = _snap(root, "P", "L1", "R1", "2026_07_15", "09_00_00",
                 {"taken": "2026-07-15T09:00:00", "source": "ftp", "complete": False})
    data = library.scan_library_root(root)
    e = next(x for x in data["robots"] if x["robot"] == "R1")
    assert e["latest_path"] == "" and e["last_backup"] == ""   # nothing ever completed
    assert len(e["backups"]) == 1
    # ...but files are law: the robot still opens, falling back to the partial
    assert library.resolve_open_path(e) == str(part)


def test_resolve_open_path_prefers_complete(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    new_part = tmp_path / "R1" / "2026_07_15" / "09_00_00"
    old_ok = tmp_path / "R1" / "2026_07_10" / "08_00_00"
    new_part.mkdir(parents=True)
    old_ok.mkdir(parents=True)
    e = {"latest_path": "", "backups": [
        {"path": str(new_part), "taken": "2026-07-15 09:00", "partial": True},
        {"path": str(old_ok), "taken": "2026-07-10 08:00"},
    ]}
    assert library.resolve_open_path(e) == str(old_ok)


def test_resolve_open_path(monkeypatch, tmp_path):
    """'latest' falls back to the newest dated snapshot that exists on disk when
    the Latest mirror is missing/stale; explicit paths pass through untouched."""
    _iso(monkeypatch, tmp_path)
    live_new = tmp_path / "R1" / "2026_07_10" / "08_00_00"
    live_old = tmp_path / "R1" / "2026_07_01" / "08_00_00"
    live_new.mkdir(parents=True)
    live_old.mkdir(parents=True)
    e = {
        "latest_path": str(tmp_path / "Latest" / "R1"),   # does not exist
        "backups": [
            {"path": str(tmp_path / "R1" / "2026_07_12" / "08_00_00"), "taken": "2026-07-12 08:00"},  # gone
            {"path": str(live_new), "taken": "2026-07-10 08:00"},
            {"path": str(live_old), "taken": "2026-07-01 08:00"},
        ],
    }
    # dead mirror -> newest dated snapshot still on disk
    assert library.resolve_open_path(e) == str(live_new)
    # live mirror wins
    mirror = tmp_path / "Latest" / "R1"
    mirror.mkdir(parents=True)
    assert library.resolve_open_path(e) == str(mirror)
    # explicit history path passes through even if missing (caller validates)
    assert library.resolve_open_path(e, str(live_old)) == str(live_old)
    # nothing on disk at all -> ""
    assert library.resolve_open_path({"latest_path": "", "backups": []}) == ""
