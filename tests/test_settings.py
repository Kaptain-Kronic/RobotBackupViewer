"""settings._write robustness: on Windows, Path.replace onto settings.json can
throw WinError 5 while another handle (a concurrent unlocked reader, a second
app instance, an AV scan) has the file open. The field failure was a burst of
start_backups all dying on that rename. _write must retry the transient hold
and still raise when the file stays locked."""
from pathlib import Path

import pytest

from backupviewer import settings


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
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)   # no real waiting
    calls = _flaky_replace(monkeypatch, fail_times=2)
    settings.set_value("library_root", r"D:\Backups")
    assert calls["n"] == 3                                        # 2 failures + 1 success
    assert settings.get("library_root") == r"D:\Backups"


def test_write_still_raises_when_lock_never_clears(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)
    _flaky_replace(monkeypatch, fail_times=99)
    with pytest.raises(PermissionError):
        settings.set_value("library_root", r"D:\Backups")
