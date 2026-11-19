"""settings._write robustness: on Windows, Path.replace onto settings.json can
throw WinError 5 while another handle (a concurrent unlocked reader, a second
app instance, an AV scan) has the file open. The field failure was a burst of
start_backups all dying on that rename. _write must retry the transient hold
and still raise when the file stays locked."""
import json
from pathlib import Path

import pytest

from backupviewer import durable, library, settings


def _iso(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)


def _flaky_replace(monkeypatch, fail_times):
    """Path.replace that raises PermissionError the first `fail_times` calls."""
    real = Path.replace
    calls = {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise PermissionError(5, "Access is denied", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    return calls


def test_write_retries_through_transient_lock(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    monkeypatch.setattr(durable.time, "sleep", lambda s: None)    # no real waiting
    calls = _flaky_replace(monkeypatch, fail_times=2)
    settings.set_value("library_root", r"D:\Backups")
    assert calls["n"] == 3                                        # 2 failures + 1 success
    assert settings.get("library_root") == r"D:\Backups"


def test_write_still_raises_when_lock_never_clears(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    monkeypatch.setattr(durable.time, "sleep", lambda s: None)
    _flaky_replace(monkeypatch, fail_times=99)
    with pytest.raises(PermissionError):
        settings.set_value("library_root", r"D:\Backups")


# -- a damaged file never wipes the others (audit 2026-11-18, reproduced) ------
# The old load() read an unreadable settings.json as {} and the next ordinary
# set_value saved {that one key} over it: every setting gone, and library_root
# fell back to the home-folder default - the app quietly showed another library.

def _fresh_process(monkeypatch):
    """What a restart after a crash looks like to durable: nothing read yet."""
    monkeypatch.setattr(durable, "_state", {})


def test_damaged_settings_read_the_last_good_copy(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    settings.set_value("library_root", r"D:\Plant Backups")
    settings.set_value("theme", "nord")                # 2nd save: the 1st is now .bak
    (tmp_path / "appdata" / "settings.json").write_bytes(b"")   # what a hard crash can leave
    _fresh_process(monkeypatch)
    assert settings.library_root() == r"D:\Plant Backups"


def test_damaged_settings_survive_the_next_save(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    settings.set_value("library_root", r"D:\Plant Backups")
    settings.set_value("staging_mode", "folder")
    settings.set_value("theme", "nord")
    main = tmp_path / "appdata" / "settings.json"
    main.write_text('{"library_root": "D:', encoding="utf-8")   # torn mid-write
    _fresh_process(monkeypatch)
    settings.set_value("last_export_folder", r"C:\export")       # any ordinary click
    after = settings.load()
    assert after["library_root"] == r"D:\Plant Backups"
    assert after["staging_mode"] == "folder"
    assert after["last_export_folder"] == r"C:\export"
    # the damaged file was set aside for a human, never written over
    kept = list((tmp_path / "appdata").glob("settings.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"library_root": "D:'


def test_save_reaches_the_disk_before_the_rename(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    synced = []
    real = durable.os.fsync
    monkeypatch.setattr(durable.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    settings.set_value("theme", "nord")
    assert synced, "the temp file must be flushed to disk before it replaces settings.json"


def test_damaged_library_reads_its_robots_back(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    data = library.load()
    data["robots"].append({"id": "r1", "robot": "RB010R01B01", "line": "L1", "plant": ""})
    library.save(data)
    data["robots"].append({"id": "r2", "robot": "RB010R02B01", "line": "L1", "plant": ""})
    library.save(data)
    (tmp_path / "appdata" / "library.json").write_bytes(bytes(64))   # zeroed sectors
    _fresh_process(monkeypatch)
    ids = [e["id"] for e in library.load()["robots"]]
    assert ids == ["r1"]                         # the copy before the last save, not []
    library.save(library.load())
    assert list((tmp_path / "appdata").glob("library.json.corrupt-*"))


# -- the edit workspace lives in its own files (audit 2026-11-18) --------------
# 657 workspace programs were 291 KB of one PC's 337 KB settings.json, re-read
# on every get() and rewritten on every click.

def test_workspace_keys_never_ride_in_settings_json(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    entries = [{"root": "C:/lib/RB010R01B01", "file": f"P{i}.LS"} for i in range(50)]
    settings.set_value("theme", "nord")
    settings.set_value("ws_entries", entries)
    settings.set_value("ws_drafts", {"x": {"t": "unsaved work"}})
    main = json.loads((tmp_path / "appdata" / "settings.json").read_text(encoding="utf-8"))
    assert "ws_entries" not in main and "ws_drafts" not in main
    assert settings.get("ws_entries") == entries                 # transparent to readers
    assert settings.load_all()["ws_drafts"] == {"x": {"t": "unsaved work"}}
    assert settings.load_all()["theme"] == "nord"


def test_an_old_settings_json_moves_its_workspace_out_on_the_next_save(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    old = {"theme": "nord", "ws_entries": [{"root": "r", "file": "A.LS"}]}
    (tmp_path / "appdata" / "settings.json").write_text(json.dumps(old), encoding="utf-8")
    assert settings.load_all()["ws_entries"] == old["ws_entries"]   # readable before
    settings.set_value("theme", "dracula")                          # any ordinary save
    main = json.loads((tmp_path / "appdata" / "settings.json").read_text(encoding="utf-8"))
    assert "ws_entries" not in main and main["theme"] == "dracula"
    assert settings.get("ws_entries") == old["ws_entries"]          # nothing lost


def test_api_hands_the_page_every_key(monkeypatch, tmp_path):
    from backupviewer.api import Api
    _iso(monkeypatch, tmp_path)
    settings.set_value("ws_entries", [{"root": "r", "file": "A.LS"}])
    settings.set_value("theme", "nord")
    got = Api().get_settings()["data"]
    assert got["ws_entries"] == [{"root": "r", "file": "A.LS"}] and got["theme"] == "nord"


def test_app_log_rotates(monkeypatch, tmp_path):
    import logging
    from logging.handlers import RotatingFileHandler
    _iso(monkeypatch, tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    for h in before:
        root.removeHandler(h)
    try:
        settings.setup_logging()
        mine = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert mine and mine[0].maxBytes == settings.LOG_MAX_BYTES
        assert mine[0].backupCount == settings.LOG_BACKUPS
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in before:
            root.addHandler(h)
