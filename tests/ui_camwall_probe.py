"""Hidden-window probe for the cam wall at PLANT SCALE - many tiles at once.

Every other CV-X test puts exactly ONE camera on the wall: ui_batch_probe
renders two tiles and dials one controller, test_cvx_stream drives a single
stream. A real line carries eight or more, and that is where the wall broke -
so this probe's whole job is the plural case.

TWO separate plural bugs have now been found here, and the second one got past
the first version of this file in three different ways at once. Both are
guarded below, and the shape of the miss is worth keeping written down:

  1. SOCKET STARVATION (fixed earlier). While a tile streamed
     multipart/x-mixed-replace, all tiles shared the one 127.0.0.1:PORT origin,
     and a never-ending response holds its connection open - so Chromium's
     six-per-host cap meant the seventh tile onward could never connect, never
     fired load OR error, and latched "no image - not answering" forever.

  2. BUDGET STARVATION (fixed now). The refresh tick spends at most six NEW
     loads a beat. It used to walk the tiles in DOM order and stop at six, so
     the same prefix won the budget every beat: exactly TWELVE tiles were ever
     fed, whatever the wall's size. On a 56-camera library, 44 tiles could
     never paint - and a tile that is never ASKED never errors either, so it
     sat blank with no note at all, indistinguishable from a dead camera.

Why version 1 of this probe could not have caught bug 2 - all three at once:

  * it used NINE cameras. Comfortably past the six-per-host cap of bug 1, and
    comfortably UNDER the twelve-tile ceiling of bug 2. So: size the wall
    against the CEILING, not against the last bug's number.
  * it called img._camLoad() on every tile by hand, which BYPASSES the budget
    the tick hands out - the broken code was never executed. So: this version
    defeats document.hidden and lets the REAL tick drive.
  * it tiled CV-X cameras only - and CV-X is the one vendor bug 2 spares,
    because only a CV-X tile's first DIAL costs a slot and every frame after
    it is a free loopback read. A MATROX tile pays a slot per frame forever,
    so Matrox is where the wall actually went dark. So: both vendors tile here.

What it pins, on real DOM in a real WebView2:

  A. N cameras of BOTH vendors tile, and EVERY tile paints a frame.
  B. no tile is left in the .cam-off (no-image) state.
  C. a tile is always retriable - _camDue never latches at Infinity, so the
     wall heals itself instead of staying dark until the app restarts.
  D. the budget ROTATES: over a few laps every tile fetches again, so no tile
     is merely painted once and then starved out of the refresh.
  E. no tile is ever a SILENT black box - a tile with no picture carries a
     note saying so, because "blank" is what a dead camera looks like.
  F. the CV-X live switch really switches CV-X OFF: the tiles leave the grid
     AND every session is hung up, because the point of the switch is handing
     each controller's single remote slot back to whoever is at the HMI.

The tick only fetches a tile it believes is ON SCREEN, so this probe has to
open the tree's folds and wait for the tiles to have real boxes before it
asserts anything. A hidden window lays out perfectly well - measured - but a
FOLDED line does not: its tiles report checkVisibility()===false and a
zero-size box, the real tick skips every one of them, and the wall reads
"painted 0 of 24" whether the scheduler is fixed or broken. Clicking the fold
headers is therefore polled to completion, not fired once and trusted.

Fully offline: CvxRemoteSession is faked, so nothing dials a camera, and the
Matrox cameras are local HTTP servers on their own ports (their own ORIGINS,
as real cameras have) serving real JPEG bytes.
Fully synthetic and identifier-clean: TEST-NET ips, CELL fakes, an empty
library in a temp folder, APPDATA redirected there BEFORE importing the app.

Run: python tests/ui_camwall_probe.py
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_camwall_probe_")

import webview  # noqa: E402

from backupviewer import cvx_remote  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from cvx_sim import _TINY_JPEG  # noqa: E402

# Sized against the CEILING the scheduler can feed (12), not against the
# per-host socket cap (6) that version 1 of this probe was written for. Both
# vendors are represented because only one of them had the budget bug, and it
# was not the one this file used to test. Every assertion counts against these
# lists, never against a frozen literal.
CVX_CAMS = ["192.0.2.%d" % (70 + i) for i in range(8)]     # TEST-NET
MTX_COUNT = 16
TOTAL = len(CVX_CAMS) + MTX_COUNT                          # 24 tiles


class _ShotHandler(BaseHTTPRequestHandler):
    """A Design Assistant camera's one useful URL: SavedImages/HMIImage.jpg."""

    def do_GET(self):
        if not self.path.split("?")[0].endswith("/SavedImages/HMIImage.jpg"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(_TINY_JPEG)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        try:
            self.wfile.write(_TINY_JPEG)
        except OSError:
            pass

    def log_message(self, *a):
        pass


def start_fake_matrox(n):
    """n fake Matrox cameras, each on its OWN port so each tile has its own
    origin exactly as real cameras do - sharing one origin would re-introduce
    the six-per-host cap and confound this probe with bug 1."""
    hosts = []
    for _ in range(n):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _ShotHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        hosts.append("127.0.0.1:%d" % srv.server_address[1])
    return hosts


class FakeCvxSession:
    """A CV-X session with no sockets that still yields a frame, so the real
    frame server has something to serve and the <img> really decodes."""
    dials = []
    stops = []

    def __init__(self, ip, **kw):
        self.ip = ip
        self.alive = True
        self.frames = 1
        self.handshake_done = True
        self.error = None

    def start(self):
        FakeCvxSession.dials.append(self.ip)
        return True

    def stop(self):
        FakeCvxSession.stops.append(self.ip)
        self.alive = False

    def latest_frame(self):
        return _TINY_JPEG

    def wait_frame(self, last, timeout):
        time.sleep(min(timeout, 0.05))
        return False


TILES_JS = "document.querySelectorAll('img.cam-live').length"

# Pin document.hidden false so the tick never pauses (a window that loses
# focus or gets minimised mid-run would otherwise stall the wall and time
# this probe out), and let startCamRefresh's own pass() drive it.
# Version 1 hand-called
# _camLoad() on every tile instead, which bypasses the per-beat budget - so
# the code that was broken never ran under test. Everything else about the
# tick stays real here, the cap included, because the cap is what is on trial.
_REAL_TICK_JS = """(function(){
    Object.defineProperty(document, 'hidden',
      { configurable: true, get: function(){ return false; } });
    return document.hidden === false;
})()"""

# One read of the whole wall. `silent` is the state that must never exist:
# no picture AND no visible note, which is what a starved tile looked like
# and is indistinguishable from a dead camera.
_WALL_JS = """JSON.stringify((function(){
    var imgs=[].slice.call(document.querySelectorAll('img.cam-live'));
    function blank(i){
      var t=i.closest('.cam-tile');
      if (!t || i.naturalWidth > 0) return false;
      var n=t.querySelector('.cam-tile-note');
      return !n || getComputedStyle(n).display === 'none';
    }
    /* Assert against tiles the user can actually SEE. That is a strictly
       narrower set than the tick's own near-screen window, so the assertion
       never quietly restates the predicate it is testing - and "if I can see
       it, it must paint" is the property the wall exists to keep. */
    function onScreen(i){
      var r=i.getBoundingClientRect();
      return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight;
    }
    var vis = imgs.filter(onScreen);
    return {
      tiles: imgs.length,
      onscreen: vis.length,
      painted: vis.filter(function(i){ return i.naturalWidth > 0; }).length,
      off: vis.filter(function(i){
             var t=i.closest('.cam-tile');
             return t && t.classList.contains('cam-off'); }).length,
      latched: imgs.filter(function(i){ return i._camDue === Infinity; }).length,
      silent: vis.filter(blank).length,
      srcs: vis.map(function(i){ return i.src; })
    };
})())"""

_ADD_CAMS_JS = """window.__added=0;
    var cvx=%s, mtx=%s;
    function add(name, dt, ip){
      BV.api.call('lib_add', {robot:name, plant:'FakePlant', line:'LINE01',
        device_type:dt, ips:[ip], model:'', notes:'', latest_path:'',
        ftp:{user:'', passive:true}}).then(function(){ window.__added++; });
    }
    cvx.forEach(function(ip,i){ add('CELL-01CVX'+(10+i),'camera-keyence',ip); });
    mtx.forEach(function(h,i){ add('CELL-01MTX'+(10+i),'camera-mtx',h); });"""

_OPEN_LINES_JS = """[].forEach.call(document.querySelectorAll('.lib-line-h'),
     function(h){
       if(!h.closest('.lib-line').classList.contains('open')) h.click();
     })"""


def _wall(window):
    return json.loads(js(window, _WALL_JS) or "{}")


def probe(window, api, mtx_hosts):
    try:
        time.sleep(4)  # boot

        # ---- a line of each vendor ----
        js(window, _ADD_CAMS_JS % (json.dumps(CVX_CAMS), json.dumps(mtx_hosts)))
        check("wall.cameras_added",
              poll(window, "window.__added===%d ? 'y' : ''" % TOTAL) == "y",
              "(got %s of %d)" % (js(window, "window.__added"), TOTAL))

        js(window, "BV.state.emit('library-dirty')")
        js(window, "document.getElementById('cube-cam').click()")
        check("wall.lens_flips", bool(poll(window,
              "!!document.querySelector('.home-library.cam-mode')")))

        check("wall.all_cameras_tile",
              poll(window, "%s===%d ? 'y' : ''" % (TILES_JS, TOTAL)) == "y",
              "(got %s of %d)" % (js(window, TILES_JS), TOTAL))

        # Lines start folded by design, and a folded tile has NO box - so the
        # tick skips it and the whole wall reads as unpainted. Firing the fold
        # clicks once raced the tree's own render; poll instead (the click is
        # idempotent) until the tiles actually have boxes.
        poll(window, "document.querySelectorAll('.lib-line-h').length ? 'y' : ''")
        opened = False
        for _ in range(24):
            js(window, _OPEN_LINES_JS)
            time.sleep(0.4)
            if (_wall(window).get("onscreen") or 0) > 0:
                opened = True
                break
        check("wall.folds_open", opened,
              "(no tile has a box - every line is still folded, and a folded "
              "wall is never asked to paint)")

        # ---- A + B: every tile paints, driven by the REAL tick ----
        check("wall.real_tick_drives", js(window, _REAL_TICK_JS) is True,
              "(document.hidden not defeated - the tick would stay paused and "
              "this probe would test nothing)")

        deadline = time.time() + 90
        w = _wall(window)
        while time.time() < deadline:
            time.sleep(1.0)
            w = _wall(window)
            if w.get("onscreen") and w["painted"] == w["onscreen"] and not w["off"]:
                break

        # The ceiling the old scheduler could feed was TWELVE tiles. A wall
        # that fits twelve or fewer on screen cannot tell a fixed scheduler
        # from a broken one - which is exactly how version 1 of this probe
        # passed for months. Fail loudly rather than assert against a wall
        # that is too small to mean anything.
        onscreen = w.get("onscreen") or 0
        check("wall.exceeds_the_old_ceiling", onscreen > 12,
              "(only %d tiles on screen - at or under the 12-tile ceiling this "
              "probe exists to exceed; give the window more room)" % onscreen)
        check("wall.every_tile_dialled",
              len(set(FakeCvxSession.dials)) == len(CVX_CAMS),
              "(dialled %d of %d)" % (len(set(FakeCvxSession.dials)), len(CVX_CAMS)))
        check("wall.every_tile_paints", onscreen and w.get("painted") == onscreen,
              "(painted %s of %s on screen - a stall at 12 is the per-beat load "
              "budget being spent on a DOM-order prefix instead of rotating)"
              % (w.get("painted"), onscreen))
        check("wall.no_tile_says_no_image", w.get("off") == 0,
              "(%s tiles left in cam-off)" % w.get("off"))

        # ---- C: no tile latches out of the retry loop ----
        check("wall.no_tile_latches_forever", w.get("latched") == 0,
              "(%s tiles pinned at _camDue=Infinity - the tick can never "
              "re-kick those)" % w.get("latched"))

        # ---- E: a tile with no picture must never be a silent black box ----
        check("wall.no_silent_blank_tile", w.get("silent") == 0,
              "(%s tiles show neither a picture nor a note - that is exactly "
              "what a starved tile looked like)" % w.get("silent"))

        # ---- D: the budget rotates, so nothing is painted once then starved.
        # Read by cache-buster: fetchFrame stamps ?t=<now> per fetch, so a src
        # that never changes is a tile the rotation never came back to. Long
        # enough for several laps of TOTAL tiles at CAM_MAX_LOADS a beat.
        before = _wall(window).get("srcs") or []
        time.sleep(24)
        after = _wall(window).get("srcs") or []
        stale = sum(1 for a, b in zip(before, after) if a == b)
        check("wall.budget_rotates",
              bool(before) and len(before) == len(after) and stale == 0,
              "(%d of %d on-screen tiles never fetched again in 24s - the "
              "refresh budget is not circulating)" % (stale, len(before)))

        # ---- F: the CV-X live switch ----
        check("cvxswitch.on_cam_lens",
              js(window, "!!document.getElementById('lib-cvx-live') && "
                         "!document.getElementById('lib-cvx-live')"
                         ".classList.contains('hidden')") is True,
              "(the switch is missing from the cam lens toolbar)")

        FakeCvxSession.stops = []
        js(window, "document.getElementById('lib-cvx-live').click()")
        gone = poll(window,
                    "document.querySelectorAll('img.cam-live[data-cvx]').length"
                    "===0 ? 'y' : ''") == "y"
        check("cvxswitch.off_drops_cvx_tiles", gone,
              "(%s CV-X tiles still on the wall)"
              % js(window, "document.querySelectorAll('img.cam-live[data-cvx]').length"))
        check("cvxswitch.off_keeps_matrox",
              js(window, "document.querySelectorAll('img.cam-live').length")
              == MTX_COUNT,
              "(expected the %d matrox tiles to stay)" % MTX_COUNT)
        # the whole point: the controllers get their one remote slot back at
        # once, rather than after CVX_TILE_TTL of a wall nobody is watching
        check("cvxswitch.off_frees_the_slots",
              len(set(FakeCvxSession.stops)) == len(CVX_CAMS),
              "(hung up %d of %d CV-X sessions)"
              % (len(set(FakeCvxSession.stops)), len(CVX_CAMS)))

        js(window, "document.getElementById('lib-cvx-live').click()")
        back = poll(window,
                    "document.querySelectorAll('img.cam-live[data-cvx]').length"
                    "===%d ? 'y' : ''" % len(CVX_CAMS)) == "y"
        check("cvxswitch.on_restores_cvx_tiles", back,
              "(CV-X tiles did not come back)")
        deadline = time.time() + 40
        w2 = {}
        while time.time() < deadline:
            time.sleep(1.0)
            w2 = _wall(window)
            if w2.get("onscreen") and w2["painted"] == w2["onscreen"]:
                break
        check("cvxswitch.on_repaints", w2.get("painted") == w2.get("onscreen"),
              "(painted %s of %s after switching CV-X back on)"
              % (w2.get("painted"), w2.get("onscreen")))

        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
    bv_settings.set_value("library_root", str(lib))
    cvx_remote.CvxRemoteSession = FakeCvxSession    # nothing dials a camera
    mtx_hosts = start_fake_matrox(MTX_COUNT)

    api = Api()
    window = webview.create_window(
        "probe",
        url=resource_path("web/index.html").as_uri(),
        js_api=api,
        width=1600,
        height=1000,     # room for >12 tiles on screen: see wall.exceeds_the_old_ceiling
        hidden=True,
    )
    api.bind(window)
    webview.start(lambda: probe(window, api, mtx_hosts), gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
