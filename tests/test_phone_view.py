"""Phone live view: the share server exercised over real HTTP on loopback with
the camera fetch faked, plus the address-ranking logic. No camera, no network
beyond 127.0.0.1."""
import base64
import time
import types
import urllib.error
import urllib.request

import pytest

from backupviewer import phoneview
from backupviewer.phoneview import PhoneShare, lan_urls, rank_ip

JPEG = b"\xff\xd8\xff\xe0FAKEJPEG\xff\xd9"


@pytest.fixture(autouse=True)
def _test_port_range(monkeypatch):
    """Tests live in their own port range: a real app instance on this machine
    holds 8756+, and a Windows wildcard listener SHADOWS a closed 127.0.0.1
    bind on the same port - a stopped test server would look alive."""
    monkeypatch.setattr(phoneview, "PORT_BASE", 18756)


def _share(fetches=None, fail=False):
    def fetch(ip, timeout=3.0):
        if fetches is not None:
            fetches.append(ip)
        if fail:
            raise OSError("camera unreachable")
        return JPEG
    return PhoneShare(fetch=fetch, bind="127.0.0.1")


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def _get_err(port, path) -> int:
    try:
        _get(port, path)
    except urllib.error.HTTPError as e:
        return e.code
    raise AssertionError("expected an HTTP error")


def _wait_down(port, deadline=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline:
        try:
            _get(port, "/v/x")
        except urllib.error.HTTPError:
            pass                                    # still answering
        except OSError:
            return                                  # refused - down
        time.sleep(0.05)
    raise AssertionError("server still answering after stop")


@pytest.fixture
def share():
    s = _share()
    yield s
    s.stop_session(None)


def test_page_and_frame_roundtrip(share):
    r = share.start_session("192.0.2.10", "RB172 CAM 1")
    port, token = share.port, r["token"]
    status, headers, body = _get(port, f"/v/{token}")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert "RB172 CAM 1" in body.decode()
    status, headers, body = _get(port, f"/v/{token}/frame?t=123")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body == JPEG
    assert "X-Frame-Age" in headers
    assert headers["Cache-Control"] == "no-store"


def test_frame_cache_rides_one_camera_fetch(share):
    fetches = []
    share._fetch = lambda ip, timeout=3.0: (fetches.append(ip), JPEG)[1]
    r = share.start_session("192.0.2.10", "cam")
    for _ in range(4):                      # well inside MIN_FETCH_GAP
        _get(share.port, f"/v/{r['token']}/frame")
    assert fetches == ["192.0.2.10"]


def test_label_is_html_escaped(share):
    r = share.start_session("192.0.2.10", "<script>alert(1)</script>")
    _, _, body = _get(share.port, f"/v/{r['token']}")
    assert b"<script>alert(1)" not in body
    assert b"&lt;script&gt;" in body


def test_unknown_paths_are_404(share):
    r = share.start_session("192.0.2.10", "cam")
    port, token = share.port, r["token"]
    assert _get_err(port, "/v/WRONGTOKEN") == 404
    assert _get_err(port, "/v/WRONGTOKEN/frame") == 404
    assert _get_err(port, "/") == 404
    assert _get_err(port, "/admin") == 404
    assert _get_err(port, f"/v/{token}/other") == 404
    assert _get_err(port, f"/v/{token}/frame/deeper") == 404


def test_same_camera_rejoins_same_session(share):
    a = share.start_session("192.0.2.10", "cam")
    b = share.start_session("192.0.2.10", "cam again")
    assert a["token"] == b["token"]
    assert len(share.status()["sessions"]) == 1


def test_two_cameras_two_sessions_one_server(share):
    a = share.start_session("192.0.2.10", "cam a")
    b = share.start_session("192.0.2.11", "cam b")
    assert a["token"] != b["token"]
    assert a["port"] == b["port"] == share.port


def test_stop_last_session_stops_server(share):
    r = share.start_session("192.0.2.10", "cam")
    port = share.port
    assert share.stop_session(r["token"]) == 0
    _wait_down(port)
    assert share.status()["running"] is False
    assert share.status()["port"] is None


def test_stop_one_of_two_keeps_serving(share):
    a = share.start_session("192.0.2.10", "cam a")
    b = share.start_session("192.0.2.11", "cam b")
    assert share.stop_session(a["token"]) == 1
    status, _, _ = _get(share.port, f"/v/{b['token']}")
    assert status == 200
    assert _get_err(share.port, f"/v/{a['token']}") == 404


def test_camera_down_no_cache_is_503():
    share = _share(fail=True)
    try:
        r = share.start_session("192.0.2.10", "cam")
        assert _get_err(share.port, f"/v/{r['token']}/frame") == 503
    finally:
        share.stop_session(None)


def test_camera_down_serves_stale_frame_with_honest_age(share, monkeypatch):
    r = share.start_session("192.0.2.10", "cam")
    _get(share.port, f"/v/{r['token']}/frame")                   # prime the cache
    monkeypatch.setattr(phoneview, "MIN_FETCH_GAP", 0.0)         # force a refetch

    def dead(ip, timeout=3.0):
        raise OSError("camera unreachable")
    share._fetch = dead
    time.sleep(0.02)
    status, headers, body = _get(share.port, f"/v/{r['token']}/frame")
    assert status == 200 and body == JPEG                        # stale but honest
    sess = share.status()["sessions"][0]
    assert sess["fetch_err"] is not None


def test_status_counts_phones_and_pulls(share):
    r = share.start_session("192.0.2.10", "cam")
    _get(share.port, f"/v/{r['token']}/frame")
    s = share.status()
    assert s["running"] is True and s["port"] == share.port
    sess = s["sessions"][0]
    assert sess["ip"] == "192.0.2.10" and sess["phones"] == 1
    assert sess["pulls"] >= 1 and sess["frame_age_ms"] is not None


def test_expired_session_is_gone_and_server_stops(share, monkeypatch):
    """TTL'd shares don't just 404 - when the LAST one ages out the server
    stops listening, so a forgotten app instance goes fully quiet."""
    r = share.start_session("192.0.2.10", "cam")
    port = share.port
    monkeypatch.setattr(phoneview, "SESSION_TTL", 0)
    assert _get_err(port, f"/v/{r['token']}") == 404
    _wait_down(port)
    assert share.status()["running"] is False


def test_port_conflict_moves_up():
    a = _share()
    b = _share()
    try:
        a.start_session("192.0.2.10", "cam")
        b.start_session("192.0.2.11", "cam")
        assert a.port != b.port
        assert phoneview.PORT_BASE <= b.port < phoneview.PORT_BASE + phoneview.PORT_TRIES
    finally:
        a.stop_session(None)
        b.stop_session(None)


# -- address ranking ---------------------------------------------------------------

def test_rank_hotspot_first_camera_net_last():
    facing = "10.5.1.20"
    ips = ["10.5.1.20", "192.168.137.1", "192.168.4.7", "172.20.0.5", "203.0.113.9"]
    ranked = sorted(ips, key=lambda a: rank_ip(a, facing))
    assert ranked[0] == "192.168.137.1"                  # hotspot wins
    assert ranked[-1] == "10.5.1.20"                     # robot network last
    assert rank_ip("192.168.137.1", facing)[1] == "hotspot"
    assert rank_ip("10.5.1.20", facing)[1] == "camera network"
    assert rank_ip("192.168.4.7", facing)[1] == "lan"
    assert rank_ip("172.20.0.5", facing)[0] == 1         # 172.16-31 is private
    assert rank_ip("172.32.0.5", facing)[0] == 2         # 172.32 is not


def test_lan_urls_are_well_formed():
    urls = lan_urls("192.0.2.10", 8756, "t0k3n")
    for u in urls:
        assert u["url"] == f"http://{u['ip']}:8756/v/t0k3n"
        assert u["kind"] in ("hotspot", "lan", "camera network")
        assert u["ip"] != "127.0.0.1"


# -- the api endpoints -------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    from backupviewer.api import Api
    monkeypatch.setattr(phoneview, "BIND", "127.0.0.1")
    a = Api()
    yield a
    if a._phone_share is not None:
        a._phone_share.stop_session(None)


def test_api_start_returns_ranked_urls(api):
    r = api.phone_view_start({"ip": "192.0.2.10", "label": "RB172 CAM"})
    assert r["ok"] is True
    d = r["data"]
    assert d["token"] and d["port"]
    assert d["urls"], "expected at least one local address"
    for u in d["urls"]:
        assert u["url"].startswith("http://") and u["url"].endswith("/v/" + d["token"])


def test_api_start_rejects_bad_ip(api):
    assert api.phone_view_start({"ip": "not-an-ip"})["error"]["code"] == "BAD_SPEC"


def test_api_qr_renders_only_active_share_urls(api):
    d = api.phone_view_start({"ip": "192.0.2.10", "label": "cam"})["data"]
    r = api.phone_view_qr({"text": d["urls"][0]["url"]})
    assert r["ok"] is True
    m = r["data"]
    assert m["size"] == len(m["rows"]) == len(m["rows"][0])
    assert set("".join(m["rows"])) <= {"0", "1"}
    # not a general-purpose QR maker
    assert api.phone_view_qr({"text": "http://evil.example/x"})["error"]["code"] == "BAD_SPEC"
    assert api.phone_view_qr({"text": ""})["error"]["code"] == "BAD_SPEC"


def test_api_qr_refuses_when_nothing_shared(api):
    assert api.phone_view_qr({"text": "http://10.0.0.1:8756/v/x"})["error"]["code"] == "BAD_SPEC"


def test_api_stop_and_status(api):
    assert api.phone_view_status()["data"] == {"running": False, "port": None, "sessions": []}
    d = api.phone_view_start({"ip": "192.0.2.10", "label": "cam"})["data"]
    st = api.phone_view_status()["data"]
    assert st["running"] is True and len(st["sessions"]) == 1
    assert api.phone_view_stop({"token": d["token"]})["data"] == 0
    assert api.phone_view_status()["data"]["running"] is False
    assert api.phone_view_stop()["data"] == 0            # idempotent, no share running


# -- the firewall helper (powershell stubbed) --------------------------------------

def test_firewall_command_shape(api):
    """The copy/paste command must open the whole port range inbound on ALL
    profiles - a Public-only rule is exactly what left the phone blocked on a
    Private hotspot."""
    cmd = api._fw_command()
    assert "New-NetFirewallRule" in cmd and "-Direction Inbound" in cmd
    assert "-Action Allow" in cmd and "-Protocol TCP" in cmd
    assert "-Profile Any" in cmd
    lo = phoneview.PORT_BASE
    assert f"{lo}-{lo + phoneview.PORT_TRIES - 1}" in cmd


def test_firewall_status_reads_rule_presence(api, monkeypatch):
    import backupviewer.api as api_mod

    def fake_run(args, **kw):
        return types.SimpleNamespace(stdout="yes\n", returncode=0)
    monkeypatch.setattr(api_mod.subprocess, "run", fake_run)
    r = api.phone_view_firewall_status()
    assert r["ok"] is True
    assert r["data"]["rule_present"] is True
    assert r["data"]["command"] == api._fw_command()


def test_firewall_status_absent_and_failure_are_honest(api, monkeypatch):
    import backupviewer.api as api_mod
    monkeypatch.setattr(api_mod.subprocess, "run",
                        lambda args, **kw: types.SimpleNamespace(stdout="no\n"))
    assert api.phone_view_firewall_status()["data"]["rule_present"] is False

    def boom(args, **kw):
        raise OSError("powershell not found")
    monkeypatch.setattr(api_mod.subprocess, "run", boom)
    r = api.phone_view_firewall_status()
    assert r["ok"] is True                       # still returns, just no rule
    assert r["data"]["rule_present"] is False
    assert r["data"]["command"]                  # command always offered


def test_firewall_fix_launches_elevated(api, monkeypatch):
    import backupviewer.api as api_mod
    seen = {}

    def fake_popen(args, **kw):
        seen["args"] = args
        seen["kw"] = kw
        return types.SimpleNamespace()
    monkeypatch.setattr(api_mod.subprocess, "Popen", fake_popen)
    r = api.phone_view_firewall_fix()
    assert r["ok"] is True and r["data"]["launched"] is True
    outer = " ".join(seen["args"])
    assert "RunAs" in outer and "-EncodedCommand" in outer
    # the encoded payload really adds our rule (utf-16-le base64, PS convention)
    enc = outer.split("-EncodedCommand','")[1].split("'")[0]
    inner = base64.b64decode(enc).decode("utf-16-le")
    assert "New-NetFirewallRule" in inner and api._FW_RULE_NAME in inner
    assert "Remove-NetFirewallRule" in inner    # idempotent replace


def test_firewall_fix_reports_launch_failure(api, monkeypatch):
    import backupviewer.api as api_mod

    def boom(args, **kw):
        raise OSError("no shell")
    monkeypatch.setattr(api_mod.subprocess, "Popen", boom)
    assert api.phone_view_firewall_fix()["error"]["code"] == "PHONE_VIEW"
