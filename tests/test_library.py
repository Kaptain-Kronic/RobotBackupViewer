"""Library registry unit tests - isolated from the real %APPDATA% by pointing
settings.app_dir() at a tmp_path, so nothing here touches a user's library."""
from backupviewer import library, settings


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path)


def test_add_list_remove(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert library.list_robots()["robots"] == []

    e = library.add_robot({"robot": "R1", "plant": "P", "line": "L", "ips": ["10.0.0.1"]})
    assert e["id"]
    assert e["ftp"] == {"user": "", "passive": True}  # defaults filled

    data = library.list_robots()
    assert len(data["robots"]) == 1
    assert data["robots"][0]["robot"] == "R1"
    assert data["robots"][0]["ips"] == ["10.0.0.1"]

    assert library.remove_robot(e["id"]) is True
    assert library.list_robots()["robots"] == []
    assert library.remove_robot("nope") is False


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
