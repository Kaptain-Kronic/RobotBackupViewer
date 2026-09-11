"""pytest for the cam-lens tile sessions at the api: view-only leases behind
the same remote registry, reaped when the wall stops watching, and adopted -
never re-dialled - when a tile is clicked open into the full remote."""
import time

import pytest

from backupviewer import api as api_mod
from backupviewer import cvx_remote


class FakeSession:
    """Stands in for CvxRemoteSession: records dials and mouse, no sockets."""
    started = []          # every ip dialled, in order, across instances
    fail = False          # flip on the class to make the next dial fail

    def __init__(self, ip, **kw):
        self.ip = ip
        self.alive = True
        self.stopped = False
        self.frames = 3
        self.handshake_done = True
        self.error = None
        self.mouse = []

    def start(self):
        FakeSession.started.append(self.ip)
        if FakeSession.fail:
            self.error = "camera busy"
            return False
        return True

    def stop(self):
        self.stopped = True
        self.alive = False

    def wait_frame(self, last, timeout):
        time.sleep(min(timeout, 0.02))
        return False

    def send_mouse(self, *a):
        self.mouse.append(a)

    def queue_mouse(self, *a):
        self.mouse.append(a)


@pytest.fixture
def api(monkeypatch):
    FakeSession.started = []
    FakeSession.fail = False
    monkeypatch.setattr(cvx_remote, "CvxRemoteSession", FakeSession)
    a = api_mod.Api()
    yield a
    if a._cvx_server is not None:
        a._cvx_server.shutdown()


def _tile(api, ip="192.0.2.61"):
    r = api.cvx_tile_start({"ip": ip})
    assert r["ok"], r
    return r["data"]


# -- start: shape, idempotency, busy, failure ---------------------------------------

def test_start_shape_and_video_only(api):
    d = _tile(api)
    sid = d["session_id"]
    assert d["stream_url"].endswith("/cvx/" + sid)
    assert d["screen"] == {"w": cvx_remote.SCREEN_W, "h": cvx_remote.SCREEN_H}
    assert FakeSession.started == ["192.0.2.61"]
    assert api._cvx[sid].video_only is True
    assert sid in api._cvx_tiles


def test_start_is_idempotent_per_ip(api):
    a = _tile(api)
    b = _tile(api)
    assert a["session_id"] == b["session_id"]
    assert FakeSession.started == ["192.0.2.61"]      # still just the one dial


def test_start_refuses_an_ip_the_overlay_holds(api):
    api.cvx_remote_start({"ip": "192.0.2.61"})
    r = api.cvx_tile_start({"ip": "192.0.2.61"})
    assert r["error"]["code"] == "CVX_BUSY"
    assert FakeSession.started == ["192.0.2.61"]      # busy never dials


def test_start_reports_a_failed_dial(api):
    FakeSession.fail = True
    r = api.cvx_tile_start({"ip": "192.0.2.61"})
    assert r["error"]["code"] == "CVX_CONNECT"
    assert api._cvx == {} and api._cvx_tiles == {}


# -- view-only ----------------------------------------------------------------------

def test_tile_sessions_refuse_mouse(api):
    sid = _tile(api)["session_id"]
    r = api.cvx_remote_mouse(sid, 0, 10, 10, 1)
    assert r["error"]["code"] == "VIEW_ONLY"
    assert api._cvx[sid].mouse == []


# -- sync + reap: the lease ---------------------------------------------------------

def test_sync_renews_the_lease_and_reports_dead_sids(api):
    sid = _tile(api)["session_id"]
    api._cvx_tiles[sid] = {"main": time.monotonic() - 100}   # long overdue...
    r = api.cvx_tile_sync([sid, "ghost"])
    assert r["data"][sid] == {"alive": True, "frames": 3}
    assert r["data"]["ghost"] == {"alive": False}
    api._reap_cvx_tiles(time.monotonic())                 # ...but the sync renewed it
    assert sid in api._cvx and not api._cvx[sid].stopped


def test_reap_stops_stale_leases_and_spares_fresh_ones(api):
    stale = _tile(api, "192.0.2.61")["session_id"]
    fresh = _tile(api, "192.0.2.62")["session_id"]
    stale_sess = api._cvx[stale]
    api._cvx_tiles[stale] = {"main": time.monotonic() - (api_mod.CVX_TILE_TTL + 1)}
    api._reap_cvx_tiles(time.monotonic())
    assert stale_sess.stopped
    assert stale not in api._cvx and stale not in api._cvx_tiles
    assert fresh in api._cvx and not api._cvx[fresh].stopped


def test_tile_stop_never_touches_an_overlay_session(api):
    overlay = api.cvx_remote_start({"ip": "192.0.2.61"})["data"]["session_id"]
    tile = _tile(api, "192.0.2.62")["session_id"]
    assert api.cvx_tile_stop(overlay)["ok"]               # quiet no-op
    assert overlay in api._cvx and not api._cvx[overlay].stopped
    assert api.cvx_tile_stop(tile)["ok"]
    assert tile not in api._cvx and tile not in api._cvx_tiles


# -- adopt: click a tile, get the full remote, never a second dial ------------------

def test_adopt_promotes_and_drops_the_lease(api):
    sid = _tile(api)["session_id"]
    d = api.cvx_tile_adopt(sid)["data"]
    assert d["session_id"] == sid and d["ip"] == "192.0.2.61"
    assert d["stream_url"].endswith("/cvx/" + sid)
    assert FakeSession.started == ["192.0.2.61"]          # adopt never dials
    assert api._cvx[sid].video_only is False
    assert sid not in api._cvx_tiles
    # the reaper must keep its hands off an adopted session forever
    api._reap_cvx_tiles(time.monotonic() + 10 * api_mod.CVX_TILE_TTL)
    assert sid in api._cvx and not api._cvx[sid].stopped
    # and the mouse works now - it is a real remote
    assert api.cvx_remote_mouse(sid, 0, 10, 10, 1)["ok"]
    assert api._cvx[sid].mouse


def test_adopt_of_a_reaped_tile_is_gone(api):
    sid = _tile(api)["session_id"]
    api._cvx_tiles[sid] = {"main": time.monotonic() - (api_mod.CVX_TILE_TTL + 1)}
    api._reap_cvx_tiles(time.monotonic())
    r = api.cvx_tile_adopt(sid)
    assert r["error"]["code"] == "NO_SESSION"


def test_adopt_retires_a_leased_corpse(api):
    sid = _tile(api)["session_id"]
    api._cvx[sid].alive = False                            # died, lease still held
    r = api.cvx_tile_adopt(sid)
    assert r["error"]["code"] == "NO_SESSION"
    assert sid not in api._cvx and sid not in api._cvx_tiles


# -- two windows, one camera: a viewer count, not a single lease --------------------

def test_a_second_window_joins_the_session(api):
    """The wall tiles a camera small while the camera window shows it big. A
    CV-X has ONE session, so the second window joins it - it must never dial
    beside it and get CVX_BUSY from its own app."""
    a = api.cvx_tile_start({"ip": "192.0.2.61"})["data"]["session_id"]
    b = api.cvx_tile_start({"ip": "192.0.2.61", "viewer": "camwin"})["data"]["session_id"]
    assert a == b
    assert FakeSession.started == ["192.0.2.61"]          # one dial, not two
    assert set(api._cvx_tiles[a]) == {"main", "camwin"}


def test_one_window_looking_away_does_not_black_out_the_other(api):
    """The bug this whole viewer count exists to prevent: flipping the lens in
    the main window used to hang up the session, so a camera being watched in
    the other window went dark for no reason the tech could see."""
    sid = api.cvx_tile_start({"ip": "192.0.2.61"})["data"]["session_id"]
    api.cvx_tile_start({"ip": "192.0.2.61", "viewer": "camwin"})
    api.cvx_tile_stop(sid)                                 # the main window flips away
    assert sid in api._cvx and not api._cvx[sid].stopped
    assert set(api._cvx_tiles[sid]) == {"camwin"}
    sess = api._cvx[sid]
    api.cvx_tile_stop(sid, "camwin")                       # now nobody is watching
    assert sid not in api._cvx and sid not in api._cvx_tiles
    assert sess.stopped                                    # and it really hung up


def test_the_reaper_collects_viewers_one_at_a_time(api):
    sid = api.cvx_tile_start({"ip": "192.0.2.61"})["data"]["session_id"]
    api.cvx_tile_start({"ip": "192.0.2.61", "viewer": "camwin"})
    # only the camera window keeps renewing
    api._cvx_tiles[sid]["main"] = time.monotonic() - (api_mod.CVX_TILE_TTL + 1)
    api._reap_cvx_tiles(time.monotonic())
    assert sid in api._cvx and set(api._cvx_tiles[sid]) == {"camwin"}
    # ...and when it stops too, the session goes
    api._cvx_tiles[sid]["camwin"] = time.monotonic() - (api_mod.CVX_TILE_TTL + 1)
    api._reap_cvx_tiles(time.monotonic())
    assert sid not in api._cvx and sid not in api._cvx_tiles


def test_sync_renews_only_the_window_that_asked(api):
    sid = api.cvx_tile_start({"ip": "192.0.2.61"})["data"]["session_id"]
    api.cvx_tile_start({"ip": "192.0.2.61", "viewer": "camwin"})
    api._cvx_tiles[sid]["main"] = time.monotonic() - 100
    api._cvx_tiles[sid]["camwin"] = time.monotonic() - 100
    api.cvx_tile_sync([sid], "camwin")
    api._reap_cvx_tiles(time.monotonic())
    assert set(api._cvx_tiles[sid]) == {"camwin"}          # main's lease expired


# -- yield: control handed back, without letting go of the slot ---------------------

def test_yield_demotes_back_to_a_lease(api):
    """A floating box that took control and gave it up hands the picture back
    to the wall's beat. The controller's single remote slot must never be let
    go of on the way: stop-and-redial would free it and then race for it."""
    sid = _tile(api)["session_id"]
    api.cvx_tile_adopt(sid)
    d = api.cvx_tile_yield(sid)["data"]
    assert d["session_id"] == sid
    assert d["shot_url"].endswith(cvx_remote.SHOT_PATH + sid)   # back on the still
    assert api._cvx[sid].video_only is True                     # view-only again
    assert sid in api._cvx_tiles                                # leased again
    assert FakeSession.started == ["192.0.2.61"]                # and never redialled
    assert not api._cvx[sid].stopped
    # view-only means view-only: the mouse is refused again
    assert api.cvx_remote_mouse(sid, 0, 10, 10, 1)["error"]["code"] == "VIEW_ONLY"


def test_yield_puts_it_back_under_the_reaper(api):
    """The whole point of a lease is that something collects it. A session
    yielded in a run where no tile was ever dialled would otherwise sit in a
    registry with no reaper running and hold the slot until the app exits."""
    sid = _tile(api)["session_id"]
    api.cvx_tile_adopt(sid)
    api.cvx_tile_yield(sid)
    api._reap_cvx_tiles(time.monotonic() + api_mod.CVX_TILE_TTL + 1)
    assert sid not in api._cvx and sid not in api._cvx_tiles
    assert api._cvx_tile_reaper is not None


def test_yield_is_idempotent(api):
    sid = _tile(api)["session_id"]
    assert api.cvx_tile_yield(sid)["ok"]      # already leased: just re-stamps
    assert api.cvx_tile_yield(sid)["ok"]
    assert sid in api._cvx_tiles and api._cvx[sid].video_only is True


def test_yield_of_a_dead_session_retires_it(api):
    sid = _tile(api)["session_id"]
    api.cvx_tile_adopt(sid)
    api._cvx[sid].alive = False
    r = api.cvx_tile_yield(sid)
    assert r["error"]["code"] == "NO_SESSION"
    assert sid not in api._cvx and sid not in api._cvx_tiles


def test_yield_round_trip_leaves_exactly_one_session(api):
    """start -> adopt -> yield -> sync: one live session throughout, one dial."""
    sid = _tile(api)["session_id"]
    api.cvx_tile_adopt(sid)
    api.cvx_tile_yield(sid)
    alive = api.cvx_tile_sync([sid])["data"]
    assert alive[sid]["alive"] is True
    assert len(api._cvx) == 1 and len(api._cvx_tiles) == 1
    assert FakeSession.started == ["192.0.2.61"]


# -- the still url: a wall polls, it does not hold streams open ---------------------

def test_start_offers_a_still_url_beside_the_stream(api):
    """A tile POLLS a finite still. It cannot hold a stream open: every session
    streams from the one 127.0.0.1:PORT origin, a multipart response never
    completes, and the browser caps connections per origin at six - so on a
    real line the seventh tile onward could never connect and sat dark."""
    d = _tile(api)
    sid = d["session_id"]
    assert d["shot_url"].endswith(cvx_remote.SHOT_PATH + sid)
    assert d["stream_url"].endswith(cvx_remote.STREAM_PATH + sid)
    assert d["shot_url"] != d["stream_url"]


def test_adopt_hands_the_overlay_a_stream(api):
    """The tile polls, but the overlay it is adopted into is a single viewer -
    that one still gets the live stream."""
    sid = _tile(api)["session_id"]
    d = api.cvx_tile_adopt(sid)["data"]
    assert d["stream_url"].endswith(cvx_remote.STREAM_PATH + sid)
