"""WebView2 boot resilience (the field 0x8007139F failure): pywebview swallows
a failed controller creation, so app.py watches its logger, relaunches once in
fallback mode (software rendering + stable profile), and only then gives up
with a useful dialog. Pure-logic tests - no GUI, no webview import."""
import logging
import sys

from backupviewer import app, settings


class _FakeWindow:
    def __init__(self):
        self.destroyed = 0

    def destroy(self):
        self.destroyed += 1


def _record(msg, level=logging.ERROR):
    return logging.LogRecord("pywebview", level, __file__, 1, msg, None, None)


def test_file_server_backlog_is_widened_before_start():
    """The page is served by pywebview's wsgiref server; its five-deep accept
    queue overflowed under the page's 62 script requests and Windows reset the
    extras (v1.7: jobs.js/theme.js/bgfx.js lost on five boots in six). The
    widening is a class attribute read at listen() time, so it has to reach
    the stdlib base every server subclass inherits from - mirrored here with
    pywebview's own adapter shape, without importing webview."""
    import socketserver
    from wsgiref.simple_server import WSGIServer

    app._widen_server_backlog()

    class _LikePywebviews(socketserver.ThreadingMixIn, WSGIServer):
        pass

    assert _LikePywebviews.request_queue_size >= 128
    # idempotent, and never narrows a queue someone else deepened further
    socketserver.TCPServer.request_queue_size = 512
    app._widen_server_backlog()
    assert socketserver.TCPServer.request_queue_size == 512


def test_failure_watch_fires_only_on_the_init_failure():
    w = _FakeWindow()
    watch = app._WebView2FailureWatch(w)

    watch.emit(_record("some other pywebview error"))
    assert watch.failed is False and w.destroyed == 0

    watch.emit(_record("WebView2 initialization failed with exception:\n boom"))
    assert watch.failed is True and w.destroyed == 1

    # the cleanup warning that follows a dead init must not re-fire anything
    watch.emit(_record("Failed to delete user data folder: x"))
    assert w.destroyed == 1


def test_failure_watch_survives_a_dead_window():
    class _Boom:
        def destroy(self):
            raise RuntimeError("window already gone")

    watch = app._WebView2FailureWatch(_Boom())
    watch.emit(_record("WebView2 initialization failed with exception: x"))
    assert watch.failed is True                      # flag set despite destroy() raising


def test_next_boot_action_ladder():
    # healthy boots do nothing, whatever the mode
    assert app._next_boot_action(False, "normal", False) == "ok"
    assert app._next_boot_action(False, "fallback", True) == "ok"
    # first failure rescues into the OTHER mode
    assert app._next_boot_action(True, "normal", False) == "relaunch-fallback"
    assert app._next_boot_action(True, "fallback", False) == "relaunch-normal"
    # a rescue that failed never chains another relaunch
    assert app._next_boot_action(True, "fallback", True) == "give-up"
    assert app._next_boot_action(True, "normal", True) == "give-up"


def test_relaunch_cmd_dev_and_frozen(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "--debug"])
    monkeypatch.setattr(sys, "executable", r"C:\Python\python.exe")
    assert app._relaunch_cmd() == [r"C:\Python\python.exe", "run.py", "--debug"]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\BackupViewer.exe")
    monkeypatch.setattr(sys, "argv", [r"C:\Apps\BackupViewer.exe", "--debug"])
    assert app._relaunch_cmd() == [r"C:\Apps\BackupViewer.exe", "--debug"]


def test_apply_fallback_env_arms_software_rendering(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    stale = tmp_path / "BackupViewer" / "webview2" / "stale"
    stale.mkdir(parents=True)                        # a corrupt old profile

    import os
    kwargs = app._apply_fallback_env()
    assert "--disable-gpu" in os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    d = tmp_path / "BackupViewer" / "webview2"
    assert kwargs == {"storage_path": str(d), "private_mode": False}
    assert d.is_dir() and not stale.exists()         # profile recreated fresh


def test_fallback_storage_dir_survives_missing_localappdata(monkeypatch, tmp_path):
    appdir = tmp_path / "appdata"
    appdir.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdir)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert str(app._fallback_storage_dir()).startswith(str(appdir))
