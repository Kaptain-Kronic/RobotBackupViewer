"""The adoption contract: a library seeded by libraryimporter.core must
surface in BackupViewer's scanner with path identity, IPs, and stable ids -
exactly what a coworker's first rescan will do. Synthetic data only."""
import json

from backupviewer import library, settings
from libraryimporter import core


def test_scanner_adopts_a_seeded_library(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)   # isolate library.json

    src = tmp_path / "robots.json"
    src.write_text(json.dumps({
        "RBB01": {"010R01": "192.0.2.10", "020R01": "192.0.2.11"},
        "RCB02": {"005R01": "192.0.2.20"},
    }), encoding="utf-8")
    model = core.parse_source(src)
    assert model["ok"]

    root = tmp_path / "RobotBackups"
    dest = root / "FakePlant"
    dest.mkdir(parents=True)
    res = core.seed(model, dest, {"RBB01": ["010R01", "020R01"], "RCB02": ["005R01"]})
    assert res["created"] == 3 and res["errors"] == []

    robots = library.scan_library_root(root)["robots"]
    by_ident = {(e["plant"], e["line"], e["robot"]): e for e in robots}
    assert set(by_ident) == {
        ("FakePlant", "RBB01", "RB010R01B01"),
        ("FakePlant", "RBB01", "RB020R01B01"),
        ("FakePlant", "RCB02", "RC005R01B02"),
    }
    # the seeded IP rides along (ips[0] is what the backup engine dials)
    assert by_ident[("FakePlant", "RBB01", "RB010R01B01")]["ips"] == ["192.0.2.10"]
    assert by_ident[("FakePlant", "RCB02", "RC005R01B02")]["ips"] == ["192.0.2.20"]
    # sidecar ids are adopted verbatim: stable, unique, rename-survivable
    ids = {e["id"] for e in robots}
    assert len(ids) == 3 and all(len(i) == 32 for i in ids)


def test_rescan_after_seeding_is_stable(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)

    src = tmp_path / "robots.json"
    src.write_text(json.dumps({"RBB01": {"010R01": "192.0.2.10"}}), encoding="utf-8")
    model = core.parse_source(src)
    root = tmp_path / "RobotBackups"
    dest = root / "FakePlant"
    dest.mkdir(parents=True)
    core.seed(model, dest, {"RBB01": ["010R01"]})

    first = library.scan_library_root(root)["robots"]
    second = library.scan_library_root(root)["robots"]
    assert [e["id"] for e in first] == [e["id"] for e in second]

    # and a rerun of the importer against the now-populated tree adds nothing
    res = core.seed(model, dest, {"RBB01": ["010R01"]})
    assert res["created"] == 0 and res["skipped"] == 1
