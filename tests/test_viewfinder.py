"""The window viewfinder: a phone share whose frames are the live client area
of a window (the Matrox = the app window). Server behavior over real loopback
HTTP with the capture faked; the api endpoint with the GDI layer stubbed."""
import urllib.error
import urllib.request

import pytest

from backupviewer import phoneview, screengrab
from backupviewer.phoneview import PhoneShare, lan_urls

PNG1 = screengrab.png_encode(1, 1, b"\x10\x20\x30\xff")
PNG2 = screengrab.png_encode(1, 1, b"\x40\x50\x60\xff")


@pytest.fixture(autouse=True)
def _test_port_range(monkeypatch):
    """Own port range for tests - see test_phone_view.py's twin fixture."""
    monkeypatch.setattr(phoneview, "PORT_BASE", 18756)


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def _get_err(port, path):
    try:
        _get(port, path)
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    raise AssertionError("expected an HTTP error")


@pytest.fixture
def share():
    s = PhoneShare(bind="127.0.0.1")
    yield s
    s.stop_session(None)


# -- the window session on the share server ----------------------------------------

def test_window_session_is_a_singleton_and_repoints(share):
    a = share.start_window_session("Matrox", lambda: PNG1)
    b = share.start_window_session("Matrox", lambda: PNG2)
    assert a["token"] == b["token"]
    s = share.status()["sessions"][0]
    assert s["kind"] == "window" and s["ip"] == ""
    # the rejoin re-pointed the fetch at the fresh window
    _, _, body = _get(share.port, f"/v/{a['token']}/frame")
    assert body == PNG2


def test_window_frame_streams_png(share):
    r = share.start_window_session("Matrox", lambda: PNG1)
    status, headers, body = _get(share.port, f"/v/{r['token']}/frame")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == PNG1
    assert "X-Frame-Age" in headers


def test_window_frame_cache_rides_one_grab(share):
    calls = []
    share.start_window_session("Matrox", lambda: (calls.append(1), PNG1)[1])
    tok = share.status()["sessions"][0]["token"]
    for _ in range(4):                          # inside MIN_FETCH_GAP
        _get(share.port, f"/v/{tok}/frame")
    assert calls == [1]


def test_window_capture_failure_is_reported(share):
    def dead():
        raise OSError("the 'FANUC Backup Viewer' window is minimized")
    r = share.start_window_session("Matrox", dead)
    code, body = _get_err(share.port, f"/v/{r['token']}/frame")
    assert code == 503 and b"Matrox window" in body
    assert "minimized" in share.status()["sessions"][0]["fetch_err"]


def test_pick_route_is_gone(share):
    r = share.start_window_session("Matrox", lambda: PNG1)
    assert _get_err(share.port, f"/v/{r['token']}/pick.png")[0] == 404


def test_camera_and_window_sessions_coexist(share):
    cam = share.start_session("192.0.2.10", "cam")
    win = share.start_window_session("Matrox", lambda: PNG1)
    assert cam["token"] != win["token"]
    kinds = {s["kind"] for s in share.status()["sessions"]}
    assert kinds == {"camera", "window"}


def test_lan_urls_accepts_no_camera():
    urls = lan_urls(None, 8756, "tok")
    for u in urls:
        assert u["kind"] in ("hotspot", "lan")              # nothing camera-facing
        assert u["url"] == f"http://{u['ip']}:8756/v/tok"


# -- the api endpoint (GDI stubbed) ------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    from backupviewer.api import Api
    monkeypatch.setattr(phoneview, "BIND", "127.0.0.1")
    monkeypatch.setattr(screengrab, "window_is_open", lambda title: True)
    monkeypatch.setattr(screengrab, "grab_window_png", lambda title: PNG1)
    a = Api()
    yield a
    if a._phone_share is not None:
        a._phone_share.stop_session(None)


def test_viewfinder_start_shares_the_window_immediately(api):
    r = api.viewfinder_start()
    assert r["ok"] is True
    d = r["data"]
    assert d["token"] and d["urls"]
    st = api.phone_view_status()["data"]["sessions"][0]
    assert st["kind"] == "window"
    # QR is live right away - no pick step; the frame is the (stubbed) window
    status, headers, body = _get(d["port"], f"/v/{d['token']}/frame")
    assert status == 200 and headers["Content-Type"] == "image/png" and body == PNG1


def test_viewfinder_start_errors_when_window_missing(api, monkeypatch):
    monkeypatch.setattr(screengrab, "window_is_open", lambda title: False)
    r = api.viewfinder_start()
    assert r["ok"] is False and r["error"]["code"] == "PHONE_VIEW"


def test_viewfinder_start_rejoins_same_session(api):
    a = api.viewfinder_start()["data"]
    b = api.viewfinder_start()["data"]
    assert a["token"] == b["token"]
    assert len(api.phone_view_status()["data"]["sessions"]) == 1


def test_viewfinder_qr_only_for_active_url(api):
    d = api.viewfinder_start()["data"]
    good = api.phone_view_qr({"text": d["urls"][0]["url"]})
    assert good["ok"] is True and set("".join(good["data"]["rows"])) <= {"0", "1"}
    assert api.phone_view_qr({"text": "http://evil.example/x"})["error"]["code"] == "BAD_SPEC"


def test_viewfinder_stop(api):
    d = api.viewfinder_start()["data"]
    assert api.phone_view_stop({"token": d["token"]})["data"] == 0
    assert api.phone_view_status()["data"]["running"] is False
