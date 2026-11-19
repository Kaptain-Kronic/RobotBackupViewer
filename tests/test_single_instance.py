"""One Backup Viewer per settings folder (audit 2026-11-18): two copies used to
read-modify-write the same library.json and settings.json, the last writer
silently winning. The claim is a named mutex keyed to the settings folder, so
a second launch on the same folder hands over and exits - while runs with
their own folder (every probe and test isolates APPDATA) never collide."""
import sys

import pytest

from backupviewer import app, settings

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named mutexes are Windows-only")


@pytest.fixture
def folder(monkeypatch, tmp_path):
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: d)
    yield d
    app._release_single_instance()


def test_the_second_claim_on_one_folder_is_refused(folder):
    assert app._claim_single_instance() is True
    held = app._instance_mutex
    app._instance_mutex = None             # the "other process" keeps its handle
    try:
        assert app._claim_single_instance() is False
    finally:
        app._instance_mutex = held


def test_releasing_lets_the_next_launch_in(folder):
    assert app._claim_single_instance() is True
    app._release_single_instance()         # what the rescue relaunch does first
    assert app._claim_single_instance() is True


def test_different_settings_folders_never_collide(folder, monkeypatch, tmp_path):
    assert app._claim_single_instance() is True
    other = tmp_path / "other"
    other.mkdir()
    first = app._instance_mutex
    monkeypatch.setattr(settings, "app_dir", lambda: other)
    app._instance_mutex = None
    try:
        assert app._claim_single_instance() is True
        app._release_single_instance()
    finally:
        app._instance_mutex = first


def test_a_second_launch_exits_without_opening_a_window(folder, monkeypatch):
    """main() hands over to the running copy before pywebview is even imported."""
    monkeypatch.setattr(app, "_claim_single_instance", lambda: False)
    monkeypatch.setattr(app, "_front_running_window", lambda: True)
    monkeypatch.setattr(settings, "setup_logging", lambda: None)
    monkeypatch.setitem(sys.modules, "webview", None)   # importing it would fail loudly
    assert app.main([]) == 0
