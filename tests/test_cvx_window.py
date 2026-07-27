"""The CV-X remote's session lifecycle at the api layer: adopt (info), reload
in place, and popping the remote into its own OS window. No controller and no
GUI - the session class and pywebview's create_window are both faked.

The rules these pin, all of them about the controller's ONE remote slot:
reload hangs up before it redials and keeps the same session id; popping out
does NOT dial again; and closing the window (or the app) stops the session."""
import pytest

from backupviewer import api as api_mod
from backupviewer import cvx_remote


class FakeSession:
    """Stands in for CvxRemoteSession: records the dial, never opens a socket."""
    started = []          # every ip dialled, in order, across instances
    fail = False          # flip on the class to make the next dial fail

    def __init__(self, ip, **kw):
        self.ip = ip
        self.alive = True
        self.stopped = False
        self.frames = 0
        self.handshake_done = True
        self.error = None

    def start(self):
        FakeSession.started.append(self.ip)
        if self.fail:
            self.error = "camera busy"
            return False
        return True

    def stop(self):
        self.stopped = True
        self.alive = False


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        for fn in list(self.handlers):
            fn()


class FakeWindow:
    def __init__(self, title, url, **kw):
        self.title = title
        self.url = url
        self.destroyed = False
        self.fronted = False
        self.fs_calls = 0
        self.events = type("E", (), {"closed": _Event()})()

    def toggle_fullscreen(self):
        self.fs_calls += 1

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()

    def restore(self):
        self.fronted = True

    def show(self):
        self.fronted = True


@pytest.fixture
def api(monkeypatch):
    import webview

    FakeSession.started = []
    monkeypatch.setattr(cvx_remote, "CvxRemoteSession", FakeSession)
    monkeypatch.setattr(webview, "create_window",
                        lambda title, url, **kw: FakeWindow(title, url, **kw))
    a = api_mod.Api()
    yield a
    if a._cvx_server is not None:
        a._cvx_server.shutdown()


def _open(api, ip="192.0.2.31"):
    return api.cvx_remote_start({"ip": ip})["data"]["session_id"]


# -- adopt: the pop-out takes over instead of dialling again ------------------------

def test_info_returns_the_stream_without_reconnecting(api):
    sid = _open(api)
    d = api.cvx_remote_info(sid)["data"]
    assert d["session_id"] == sid and d["ip"] == "192.0.2.31"
    assert d["stream_url"].endswith("/cvx/" + sid)
    assert d["screen"] == {"w": cvx_remote.SCREEN_W, "h": cvx_remote.SCREEN_H}
    assert FakeSession.started == ["192.0.2.31"]      # still just the one dial


def test_info_on_an_unknown_session_is_an_error(api):
    assert api.cvx_remote_info("nope")["error"]["code"] == "NO_SESSION"


# -- reload: hang up, then redial, under the same id -------------------------------

def test_reload_keeps_the_session_id_and_redials(api):
    sid = _open(api)
    first = api._cvx[sid]
    d = api.cvx_remote_reload(sid)["data"]
    assert d["session_id"] == sid                     # the id survives
    assert first.stopped is True                      # the old slot was let go
    assert api._cvx[sid] is not first                 # a genuinely new session
    assert FakeSession.started == ["192.0.2.31", "192.0.2.31"]


def test_reload_lets_go_before_it_dials(api, monkeypatch):
    """The controller only frees its one remote slot when we hang up, so the
    stop must land BEFORE the new connect - never the other way around."""
    order = []
    sid = _open(api)
    monkeypatch.setattr(api._cvx[sid], "stop", lambda: order.append("stop"))
    real_start = FakeSession.start
    monkeypatch.setattr(FakeSession, "start",
                        lambda self: (order.append("start"), real_start(self))[1])
    api.cvx_remote_reload(sid)
    assert order == ["stop", "start"]


def test_a_failed_reload_leaves_no_session_behind(api, monkeypatch):
    sid = _open(api)
    monkeypatch.setattr(FakeSession, "fail", True)
    r = api.cvx_remote_reload(sid)
    assert r["ok"] is False and r["error"]["code"] == "CVX_CONNECT"
    assert sid not in api._cvx                        # nothing stranded on the camera


def test_reload_on_an_unknown_session_is_an_error(api):
    assert api.cvx_remote_reload("nope")["error"]["code"] == "NO_SESSION"


# -- the pop-out window ------------------------------------------------------------

def test_window_boots_pinned_to_the_session_and_does_not_redial(api):
    sid = _open(api)
    r = api.cvx_remote_window({"session_id": sid, "label": "cell cam"})["data"]
    assert r["title"] == "CV-X remote · cell cam"
    w = api._cvx_windows[sid]
    assert "#cvx=" + sid in w.url and "label=cell%20cam" in w.url
    assert FakeSession.started == ["192.0.2.31"]      # adopted, not re-dialled
    assert api._cvx[sid].alive is True                # the remote kept running


def test_window_for_an_unknown_session_is_an_error(api):
    assert api.cvx_remote_window({"session_id": "nope"})["error"]["code"] == "NO_SESSION"


def test_popping_out_twice_fronts_the_window(api):
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    w = api._cvx_windows[sid]
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    assert api._cvx_windows[sid] is w and w.fronted is True


def test_closing_the_window_stops_the_session(api):
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    sess = api._cvx[sid]
    api._cvx_windows[sid].events.closed.fire()        # the user hit the window's X
    assert sess.stopped is True
    assert sid not in api._cvx and sid not in api._cvx_windows


def test_window_close_endpoint_destroys_the_window(api):
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    w = api._cvx_windows[sid]
    sess = api._cvx[sid]
    assert api.cvx_remote_window_close(sid)["ok"] is True
    assert w.destroyed is True and sess.stopped is True


def test_window_close_after_a_failed_reload_still_closes(api, monkeypatch):
    """The overlay's ✕ names the id it was popped out under, which outlives a
    reload that failed - the window must still go away."""
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    w = api._cvx_windows[sid]
    monkeypatch.setattr(FakeSession, "fail", True)
    api.cvx_remote_reload(sid)
    assert api.cvx_remote_window_close(sid)["ok"] is True and w.destroyed is True


# -- fullscreen: the HOST window, not the web fullscreen api -----------------------
# WebView2 grants requestFullscreen but only stretches the element inside the
# same window, and the overlays are already inset:0 - so the bars' fullscreen
# button did visibly nothing until it went through pywebview.

def test_fullscreen_toggles_the_app_window_and_tracks_state(api):
    api._window = FakeWindow("FANUC Backup Viewer", "")
    assert api.toggle_fullscreen()["data"] == {"fullscreen": True}
    assert api._window.fs_calls == 1
    assert api.toggle_fullscreen({"window": "main"})["data"] == {"fullscreen": False}
    assert api._window.fs_calls == 2


def test_fullscreen_targets_the_popped_out_window(api):
    api._window = FakeWindow("FANUC Backup Viewer", "")
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    assert api.toggle_fullscreen({"window": sid})["data"]["fullscreen"] is True
    assert api._cvx_windows[sid].fs_calls == 1
    assert api._window.fs_calls == 0            # the main window stayed put


def test_fullscreen_needs_a_window_we_opened(api):
    api._window = FakeWindow("FANUC Backup Viewer", "")
    r = api.toggle_fullscreen({"window": "Some Other App"})
    assert r["ok"] is False and r["error"]["code"] == "NO_WINDOW"


def test_closing_a_fullscreen_window_forgets_its_state(api):
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    api.toggle_fullscreen({"window": sid})
    api._cvx_windows[sid].events.closed.fire()
    assert sid not in api._fullscreen


def test_app_close_takes_popped_out_remotes_with_it(api):
    sid = _open(api)
    api.cvx_remote_window({"session_id": sid, "label": "cam"})
    w = api._cvx_windows[sid]
    sess = api._cvx[sid]
    api._destroy_popouts()                            # the main window closed
    assert w.destroyed is True and sess.stopped is True
