"""Drop-import endpoints, driven directly on an Api() with no window -
isolated from the real %APPDATA% and library root via tmp_path. The engine's
own rules live in test_libimport; here the bridge behavior is what's under
test: envelopes, the BUSY refusals, the background thread + progress poll,
and handle_drop's path push."""
import json
from pathlib import Path

from backupviewer import settings
from backupviewer.api import Api


def _iso(monkeypatch, tmp_path) -> Path:
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)
    root = tmp_path / "root"
    settings.set_value("library_root", str(root))
    return root


def _fanuc_robot(parent: Path, name: str) -> Path:
    snap = parent / name / "2026_08_01" / "07_00_00"
    snap.mkdir(parents=True)
    (snap / "NUMREG.VA").write_text("[*NUMREG*]\r\n[1] = 10\r\n", encoding="cp1252")
    return parent / name


def _finish(api: Api) -> None:
    """Join the import thread and the rescan it kicks, so no daemon writes
    outlive the tmp fixtures."""
    t = api._import_thread
    if t is not None:
        t.join(timeout=60)
    s = api._scan_thread
    if s is not None:
        s.join(timeout=60)


def test_import_scan_envelope(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    robot = _fanuc_robot(tmp_path / "drop", "RB010R01B01")
    out = Api().import_scan([str(robot)])
    assert out["ok"], out
    assert [d["robot"] for d in out["data"]["drafts"]] == ["RB010R01B01"]


def test_import_start_validates(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    a = Api()
    out = a.import_start([{"src": "x"}], "FakePlant", "")
    assert not out["ok"] and out["error"]["code"] == "BAD_SPEC"
    out = a.import_start([], "FakePlant", "L1")
    assert not out["ok"] and out["error"]["code"] == "BAD_SPEC"


def test_import_start_refuses_while_busy(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    a = Api()
    draft = [{"src": str(tmp_path), "robot": "RB010R01B01", "snapshots": []}]
    monkeypatch.setattr(a, "_backups_active", lambda: True)
    out = a.import_start(draft, "", "L1")
    assert not out["ok"] and out["error"]["code"] == "BUSY"
    monkeypatch.setattr(a, "_backups_active", lambda: False)

    class Alive:
        def is_alive(self):
            return True

    a._import_thread = Alive()
    out = a.import_start(draft, "", "L1")
    assert not out["ok"] and out["error"]["code"] == "BUSY"


def test_import_end_to_end(monkeypatch, tmp_path):
    root = _iso(monkeypatch, tmp_path)
    robot = _fanuc_robot(tmp_path / "drop", "RB010R01B01")
    a = Api()
    drafts = a.import_scan([str(robot)])["data"]["drafts"]
    out = a.import_start(drafts, "FakePlant", "L1")
    assert out["ok"] and out["data"]["started"] == 1
    _finish(a)
    p = a.import_progress()["data"]
    assert not p["active"] and not p["cancelled"]
    (r,) = p["results"]
    assert r["status"] == "imported" and r["copied"] == 1
    assert p["bytes_done"] == p["bytes_total"] > 0
    landed = root / "FakePlant" / "L1" / "RB010R01B01" / "2026_08_01" / "07_00_00"
    assert (landed / "NUMREG.VA").is_file()
    # the rescan the import kicked has adopted the robot into the library
    lst = a.lib_list()
    assert lst["ok"], lst
    names = [e["robot"] for e in lst["data"]["robots"]]
    assert names == ["RB010R01B01"]


def test_import_cancel_without_import(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    out = Api().import_cancel()
    assert out["ok"] and out["data"]["cancelling"] is False


def test_handle_drop_pushes_paths(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    a = Api()
    calls = []

    class W:
        def evaluate_js(self, s):
            calls.append(s)

    a._window = W()
    a.handle_drop({"dataTransfer": {"files": [
        {"name": "drop", "pywebviewFullPath": str(tmp_path / "drop")},
        "not-a-dict",
        {"name": "no-path"},
    ]}})
    (js,) = calls
    assert "BV.importDrop" in js
    assert json.dumps([str(tmp_path / "drop")]) in js
    calls.clear()
    a.handle_drop({"dataTransfer": {"files": [{"name": "no-path"}]}})
    assert calls == []                      # nothing usable -> no push
    a.handle_drop(None)                     # never raises across the DOM bridge
