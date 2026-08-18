"""Hidden-window probe for the cam wall at PLANT SCALE - many CV-X tiles at once.

Every other CV-X test puts exactly ONE camera on the wall: ui_batch_probe
renders two tiles and dials one controller, test_cvx_stream drives a single
stream. A real line carries eight or more, and that is where the wall broke -
so this probe's whole job is the plural case.

What it pins, on real DOM in a real WebView2:

  A. N CV-X cameras tile, and EVERY tile paints a frame. This is the
     regression guard for the socket-starvation bug: while a tile streamed
     multipart/x-mixed-replace, all of them shared the one 127.0.0.1:PORT
     origin, and a never-ending response holds its connection open - so
     Chromium's six-per-host cap meant the seventh tile onward could never
     connect, never fired load OR error, and latched "no image - not
     answering" forever. Any per-origin connection limit is invisible with
     one tile and fatal with eight, which is exactly why nothing caught it.
  B. no tile is left in the .cam-off (no-image) state.
  C. a tile is always retriable - _camDue never latches at Infinity, so the
     wall heals itself instead of staying dark until the app restarts.

Fully offline: CvxRemoteSession is faked, so nothing dials a camera. The fake
does serve real JPEG bytes (latest_frame/wait_frame), because the REAL frame
server and the REAL <img> path are the things under test.
Fully synthetic and identifier-clean: TEST-NET ips, CELL fakes, an empty
library in a temp folder, APPDATA redirected there BEFORE importing the app.

Run: python tests/ui_camwall_probe.py
"""
import json
import sys
import time

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_camwall_probe_")

import webview  # noqa: E402

from backupviewer import cvx_remote  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from cvx_sim import _TINY_JPEG  # noqa: E402

# Comfortably past Chromium's six-connections-per-host cap. The count is the
# POINT of this probe, not an incidental: at <=6 the old streaming wall passed.
# Every assertion below counts against CAMS, never against a frozen literal.
CAMS = ["192.0.2.%d" % (70 + i) for i in range(9)]      # TEST-NET


class FakeCvxSession:
    """A CV-X session with no sockets that still yields a frame, so the real
    frame server has something to serve and the <img> really decodes."""
    dials = []

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
        self.alive = False

    def latest_frame(self):
        return _TINY_JPEG

    def wait_frame(self, last, timeout):
        time.sleep(min(timeout, 0.05))
        return False


TILES_JS = "document.querySelectorAll('img.cam-live[data-cvx]').length"


def _kick_all(window):
    """Drive every CV-X tile's load. A hidden window pauses the shared tick
    (document.hidden detaches by design), so the probe plays the tick's part."""
    return js(window, """(function(){
        var n=0;
        [].forEach.call(document.querySelectorAll('img.cam-live[data-cvx]'),
            function(img){ if (img._camLoad) { img._camLoad(); n++; } });
        return n;
    })()""")


def _wall(window):
    return json.loads(js(window, """JSON.stringify((function(){
        var imgs=[].slice.call(document.querySelectorAll('img.cam-live[data-cvx]'));
        return {
          tiles: imgs.length,
          painted: imgs.filter(function(i){ return i.naturalWidth > 0; }).length,
          off: imgs.filter(function(i){
                 var t=i.closest('.cam-tile');
                 return t && t.classList.contains('cam-off'); }).length,
          latched: imgs.filter(function(i){ return i._camDue === Infinity; }).length
        };
    })())""") or "{}")


def probe(window, api):
    try:
        time.sleep(4)  # boot

        # ---- a line of CV-X cameras ----
        js(window, """window.__added=0;
            var ips=%s;
            ips.forEach(function(ip, i){
              BV.api.call('lib_add', {robot:'CELL-01CVX'+(10+i), plant:'FakePlant',
                line:'LINE01', device_type:'camera-keyence', ips:[ip],
                model:'', notes:'', latest_path:'', ftp:{user:'', passive:true}})
                .then(function(){ window.__added++; });
            });""" % json.dumps(CAMS))
        check("wall.cameras_added",
              poll(window, "window.__added===%d ? 'y' : ''" % len(CAMS)) == "y",
              "(got %s of %d)" % (js(window, "window.__added"), len(CAMS)))

        js(window, "BV.state.emit('library-dirty')")
        js(window, "document.getElementById('cube-cam').click()")
        check("wall.lens_flips", bool(poll(window,
              "!!document.querySelector('.home-library.cam-mode')")))

        # lines start folded by design; open them so this is a real wall
        js(window, """[].forEach.call(document.querySelectorAll('.lib-line-h'),
             function(h){
               if(!h.closest('.lib-line').classList.contains('open')) h.click();
             })""")
        check("wall.all_cameras_tile",
              poll(window, "%s===%d ? 'y' : ''" % (TILES_JS, len(CAMS))) == "y",
              "(got %s of %d)" % (js(window, TILES_JS), len(CAMS)))

        # ---- A + B: every tile paints ----
        # Several rounds: the first kick dials (async), later kicks fetch the
        # frame once the lease exists. Generous, because a plant PC is slower.
        deadline = time.time() + 40
        w = {}
        while time.time() < deadline:
            _kick_all(window)
            time.sleep(1.0)
            w = _wall(window)
            if w.get("painted") == len(CAMS) and w.get("off") == 0:
                break

        check("wall.every_tile_dialled",
              len(set(FakeCvxSession.dials)) == len(CAMS),
              "(dialled %d of %d)" % (len(set(FakeCvxSession.dials)), len(CAMS)))
        check("wall.every_tile_paints", w.get("painted") == len(CAMS),
              "(painted %s of %d - a stall at ~6 is the per-host connection cap)"
              % (w.get("painted"), len(CAMS)))
        check("wall.no_tile_says_no_image", w.get("off") == 0,
              "(%s tiles left in cam-off)" % w.get("off"))

        # ---- C: no tile latches out of the retry loop ----
        check("wall.no_tile_latches_forever", w.get("latched") == 0,
              "(%s tiles pinned at _camDue=Infinity - the tick can never "
              "re-kick those)" % w.get("latched"))

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

    api = Api()
    window = webview.create_window(
        "probe",
        url=resource_path("web/index.html").as_uri(),
        js_api=api,
        width=1280,
        height=900,
        hidden=True,
    )
    api.bind(window)
    webview.start(lambda: probe(window, api), gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
