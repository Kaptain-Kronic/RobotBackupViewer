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
