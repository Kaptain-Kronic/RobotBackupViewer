"""Hidden-window probe for the remote bars + the top-bar phone button.

Three things, on real DOM:
  A. the main window's top bar carries 📱 between compare and ⚙, and it opens
     the viewfinder for THIS window;
  B. the CV-X overlay's bar now matches the Matrox one - reload, open in
     window, phone, fullscreen, close - and reload redials under the SAME
     session id (the controller has one remote slot);
  C. a popped-out CV-X window (#cvx= fragment) boots into the overlay alone,
     ADOPTS the running session instead of dialling again, and drops its own
     "open in window" button.

Fully offline: CvxRemoteSession is faked, so nothing dials a camera.
Fully synthetic and identifier-clean: TEST-NET ip, empty library in a temp
folder, APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_cvxremote_probe.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = Path(tempfile.mkdtemp(prefix="bv_cvxbar_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import cvx_remote  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

CAM_IP = "192.0.2.31"          # TEST-NET
FAILURES = []


class FakeSession:
    """A CV-X session that never opens a socket and never yields a frame."""
    dials = []

    def __init__(self, ip, **kw):
        self.ip = ip
        self.alive = True
        self.frames = 0
        self.handshake_done = True
        self.error = None

    def start(self):
        FakeSession.dials.append(self.ip)
        return True

    def latest_frame(self):
        return None

    def stop(self):
        self.alive = False


def check(name, cond, detail=""):
    print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=24, delay=0.25):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def bar_buttons(window):
    return json.loads(poll(window, """(function(){
        var b = document.querySelector('.cvx-remote .cvx-bar');
        if (!b) return '';
        return JSON.stringify([].map.call(b.querySelectorAll('button'),
            function(x){ return x.textContent; }));
    })()""") or "[]")


def probe(window, api):
    try:
        time.sleep(4)  # boot

        # ---- A. the top bar's phone button ----
        check("topbar.phone_present", js(window, "!!document.getElementById('btn-phone')"))
        order = json.loads(js(window, """JSON.stringify(
            [].map.call(document.querySelectorAll('#topbar-right .icon-btn'),
                        function(b){ return b.id; }))""") or "[]")
        check("topbar.phone_sits_after_compare",
              order == ["btn-compare", "btn-phone", "btn-cog", "btn-help"], f"({order})")
        check("topbar.window_key_is_main", js(window, "BV.windowKey()") is None)
        js(window, "BV._vfCalls = 0; BV.openViewfinder = function(){ BV._vfCalls++; };")
        js(window, "document.getElementById('btn-phone').click()")
        check("topbar.phone_opens_viewfinder", js(window, "BV._vfCalls") == 1)

        # ---- B. the CV-X overlay's own bar ----
        js(window, f"BV.openCvxRemote('{CAM_IP}', 'probe cam')")
        btns = bar_buttons(window)
        check("cvx.bar_matches_matrox",
              btns == ["⟳ reload", "open in window", "📱 phone", "fullscreen", "✕ close"],
              f"({btns})")
        sid = poll(window, """(function(){
            var i = document.querySelector('.cvx-remote img');
            var m = i && /\\/cvx\\/([^?]+)/.exec(i.src);
            return m ? m[1] : '';
        })()""")
        check("cvx.connected", bool(sid) and sid in api._cvx, f"(sid={sid})")
        check("cvx.dialled_once", FakeSession.dials == [CAM_IP], f"({FakeSession.dials})")
        js(window, """BV._vf2 = 0; BV.openViewfinder = function(){ BV._vf2++; };
                      document.querySelectorAll('.cvx-bar .btn')[2].click();""")
        check("cvx.phone_opens_viewfinder", js(window, "BV._vf2") == 1)

        # reload: same id, new session under it, the <img> re-pointed
        was = api._cvx.get(sid)
        src0 = js(window, "document.querySelector('.cvx-remote img').src")
        js(window, "document.querySelectorAll('.cvx-bar .btn')[0].click()")
        time.sleep(2.5)
        src1 = js(window, "document.querySelector('.cvx-remote img').src")
        check("cvx.reload_keeps_the_id", sid in api._cvx and api._cvx[sid] is not was)
        check("cvx.reload_hung_up_first", was is not None and was.alive is False)
        check("cvx.reload_redialled", FakeSession.dials == [CAM_IP, CAM_IP],
              f"({FakeSession.dials})")
        check("cvx.reload_repointed_the_stream", src1 != src0 and f"/cvx/{sid}" in src1)

        # ---- B2. fullscreen reaches the WINDOW (the web api can't) ----
        # requestFullscreen is granted here but only stretches the element
        # inside the same window - and the overlay is already inset:0, so the
        # button used to do visibly nothing. It must go through pywebview.
        js(window, "document.querySelectorAll('.cvx-bar .btn')[3].click()")
        time.sleep(1.5)
        check("fs.window_went_fullscreen", "main" in api._fullscreen)
        check("fs.js_state_agrees", js(window, "BV.fullscreen.active()") is True)
        # esc in fullscreen backs out the WINDOW, it does not close the remote
        js(window, """document.dispatchEvent(
            new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))""")
        time.sleep(1.5)
        check("fs.esc_leaves_fullscreen_first", "main" not in api._fullscreen)
        check("fs.esc_kept_the_remote_open",
              js(window, "!!document.querySelector('.cvx-remote')"))
        # and now a second esc really does close it
        js(window, """document.dispatchEvent(
            new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))""")
        time.sleep(1)
        check("fs.second_esc_closes", not js(window, "!!document.querySelector('.cvx-remote')"))
        # reopen for part C, adopting nothing (the session above was stopped)
        js(window, f"BV.openCvxRemote('{CAM_IP}', 'probe cam')")
        sid = poll(window, """(function(){
            var i = document.querySelector('.cvx-remote img');
            var m = i && /\\/cvx\\/([^?]+)/.exec(i.src);
            return m ? m[1] : '';
        })()""")
        check("fs.reopened_for_popout", bool(sid) and sid in api._cvx)

        # ---- C. the popped-out window adopts, and drops its own pop-out button ----
        live = api._cvx[sid]
        dials_before = list(FakeSession.dials)
        r = api.cvx_remote_window({"session_id": sid, "label": "probe cam"})
        check("popout.opened", r["ok"] is True, f"({r})")
        win2 = api._cvx_windows.get(sid)
        time.sleep(4)
        check("popout.chrome_hidden", js(win2, "document.body.classList.contains('cvxwin')")
              and js(win2, "getComputedStyle(document.getElementById('app')).display") == "none")
        btns2 = bar_buttons(win2)
        check("popout.bar_drops_open_in_window",
              btns2 == ["⟳ reload", "📱 phone", "fullscreen", "✕ close"], f"({btns2})")
        check("popout.adopted_not_redialled",
              api._cvx.get(sid) is live and FakeSession.dials == dials_before,
              f"({FakeSession.dials})")
        check("popout.window_key_is_the_session", js(win2, "BV.windowKey()") == sid)
        check("popout.streams_that_session",
              f"/cvx/{sid}" in (js(win2, "document.querySelector('.cvx-remote img').src") or ""))

        # its ✕ closes the WINDOW, which is what stops the session
        js(win2, "document.querySelector('.cvx-bar .btn:last-child').click()")
        time.sleep(2)
        check("popout.close_stops_the_session",
              sid not in api._cvx and sid not in api._cvx_windows and live.alive is False)

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
    bv_settings.set_value("library_root", str(lib))
    cvx_remote.CvxRemoteSession = FakeSession      # nothing dials a camera

    api = Api()
    window = webview.create_window(
        "probe",
        url=resource_path("web/index.html").as_uri(),
        js_api=api,
        width=1280,
        height=860,
        hidden=True,
    )
    api.bind(window)
    webview.start(lambda: probe(window, api), gui="edgechromium")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
