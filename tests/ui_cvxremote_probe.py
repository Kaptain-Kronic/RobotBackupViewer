"""Hidden-window probe for the remote views + the top-bar phone button.

On real DOM:
  A. the main window's top bar carries 📱 between compare and ⚙, and it opens
     the viewfinder for THIS window;
  B. the CV-X view's bar matches the Matrox one - reload, open in window,
     phone, zoom, fullscreen, close - and reload redials under the SAME
     session id (the controller has one remote slot);
  B1. view zoom is LOCAL: ctrl+wheel grows the screen box and forwards
     nothing to the camera (a plain wheel does reach it), a pinch-sized tick
     steps in proportion and no single event exceeds a notch, ctrl+0 resets;
  D. the WINDOW's own browser zoom is locked - IsZoomControlEnabled and
     IsPinchZoomEnabled off, ZoomFactor pinned at 1 - read from the real
     CoreWebView2 settings, in the main window (before A) and the pop-out (C);
  B2/B3. the remote rides a SESSION-BAR CHIP: the panel sits below the top
     chrome, esc parks it (hidden, session alive, chip stays), the chip
     click brings it back, a route parks it, re-opening the same ip focuses
     instead of redialling, and the chip's ✕ is what disconnects;
  C. a popped-out CV-X window (#cvx= fragment) boots into the overlay alone,
     ADOPTS the running session instead of dialling again, and drops its own
     "open in window" button (chipless there - it IS the remote).

Fully offline: CvxRemoteSession is faked, so nothing dials a camera.
Fully synthetic and identifier-clean: TEST-NET ip, empty library in a temp
folder, APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_cvxremote_probe.py
"""
import json
import sys
import time
from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_cvxbar_probe_")

import webview  # noqa: E402

from backupviewer import cvx_remote  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

CAM_IP = "192.0.2.31"          # TEST-NET


class FakeSession:
    """A CV-X session that never opens a socket and never yields a frame."""
    dials = []

    def __init__(self, ip, **kw):
        self.ip = ip
        self.alive = True
        self.frames = 0
        self.handshake_done = True
        self.error = None
        self.mouse = []          # event ids the frontend forwarded

    def start(self):
        FakeSession.dials.append(self.ip)
        return True

    def latest_frame(self):
        return None

    def wait_frame(self, last, timeout):
        # the real MJPEG server streams this fake during the probe; idle politely
        time.sleep(min(timeout, 0.05))
        return False

    def stop(self):
        self.alive = False

    def send_mouse(self, event_id, x, y):
        self.mouse.append(event_id)

    def queue_mouse(self, seq, event_id, x, y):
        self.mouse.append(event_id)


def bar_buttons(window):
    return json.loads(poll(window, """(function(){
        var b = document.querySelector('.cvx-remote .cvx-bar');
        if (!b) return '';
        return JSON.stringify([].map.call(b.querySelectorAll('button'),
            function(x){ return x.textContent.trim(); }));
    })()""") or "[]")


def browser_zoom_state(window):
    """The window's REAL WebView2 zoom knobs (not a JS mirror of them), read on
    the UI thread the control is affine to. None for a knob the SDK lacks."""
    from System import Action     # pythonnet - loaded by pywebview's winforms backend
    out = {}

    def read():
        wv = window.native.browser.webview
        s = wv.CoreWebView2.Settings
        out["control"] = bool(s.IsZoomControlEnabled)
        out["pinch"] = getattr(s, "IsPinchZoomEnabled", None)
        out["factor"] = float(wv.ZoomFactor)
    window.native.Invoke(Action(read))
    return out


def probe(window, api):
    try:
        time.sleep(4)  # boot

        # ---- D. the window's own browser zoom is locked (api._lock_browser_zoom) ----
        # WebView2 boots with page zoom ON (pywebview never reads `zoomable`);
        # the app turns it off once the page loads, or a ctrl+wheel over the
        # top bar scales the whole app under the remote's local zoom. Read
        # from the real CoreWebView2 settings, not from anything the page says.
        z = browser_zoom_state(window)
        check("zoomlock.control_off", z.get("control") is False, f"({z})")
        check("zoomlock.pinch_off", z.get("pinch") in (False, None), f"({z})")
        check("zoomlock.factor_is_1", z.get("factor") == 1.0, f"({z})")

        # ---- A. the top bar's phone button ----
        check("topbar.phone_present", js(window, "!!document.getElementById('btn-phone')"))
        order = json.loads(js(window, """JSON.stringify(
            [].map.call(document.querySelectorAll('#topbar-right .icon-btn'),
                        function(b){ return b.id; }))""") or "[]")
        check("topbar.trio_order",
              order == ["btn-phone", "btn-cog", "btn-help"], f"({order})")
        # the trio is drawn svg now (icons.js data-icon injection), not emoji
        check("topbar.trio_is_svg", js(window, """
            ['btn-phone','btn-cog','btn-help'].every(function(id){
              return !!document.querySelector('#' + id + ' svg.bv-ico'); })"""))
        check("topbar.window_key_is_main", js(window, "BV.windowKey()") is None)
        js(window, "BV._vfCalls = 0; BV.openViewfinder = function(){ BV._vfCalls++; };")
        js(window, "document.getElementById('btn-phone').click()")
        check("topbar.phone_opens_viewfinder", js(window, "BV._vfCalls") == 1)

        # ---- B. the CV-X view's own bar ----
        js(window, f"BV.openCvxRemote('{CAM_IP}', 'probe cam')")
        btns = bar_buttons(window)
        check("cvx.bar_matches_matrox",
              btns == ["⟳ reload", "open in window", "phone", "100%", "fullscreen", "✕ close"],
              f"({btns})")
        check("cvx.phone_btn_has_icon", js(window,
            "!!document.querySelectorAll('.cvx-bar .btn')[2].querySelector('svg.bv-ico')"))
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

        # the chip: the remote rides the session bar like an open backup
        check("chip.appears",
              js(window, "document.querySelectorAll('#sessionbar .stab.remote').length") == 1)
        check("chip.has_icon",
              js(window, "!!document.querySelector('#sessionbar .stab.remote svg.bv-ico-remote')"))
        check("chip.named",
              (js(window, "document.querySelector('#sessionbar .stab.remote .stab-label')"
                          ".textContent") or "") == "probe cam")
        check("chip.active_while_shown",
              js(window, "document.querySelector('#sessionbar .stab.remote')"
                         ".classList.contains('active')"))
        # the panel sits BELOW the topbar, so the strip stays clickable
        top_px = js(window, "parseFloat(document.querySelector('.cvx-remote').style.top) || 0")
        check("chip.panel_below_topbar", (top_px or 0) > 0, f"(top={top_px})")

        # ---- B1. view zoom: local, never forwarded ----
        check("zoom.starts_100",
              js(window, "document.querySelector('.cvx-bar .cvx-zoom').textContent") == "100%")
        w0 = js(window, "document.querySelector('.cvx-screen').getBoundingClientRect().width")
        js(window, """document.querySelector('.cvx-screen').dispatchEvent(
            new WheelEvent('wheel', {ctrlKey: true, deltaY: -100, bubbles: true, cancelable: true}))""")
        time.sleep(0.4)
        w1 = js(window, "document.querySelector('.cvx-screen').getBoundingClientRect().width")
        check("zoom.ctrl_wheel_grows", bool(w0) and bool(w1) and w1 > w0 * 1.15,
              f"({w0} -> {w1})")
        check("zoom.button_reads_125",
              js(window, "document.querySelector('.cvx-bar .cvx-zoom').textContent") == "125%")
        # a trackpad pinch is a burst of small ctrl+wheels: each steps in
        # proportion to its delta (a tenth of a notch = a tenth of the step),
        # and no single event - a fling, a high-res wheel - exceeds one notch
        js(window, """document.querySelector('.cvx-screen').dispatchEvent(
            new WheelEvent('wheel', {ctrlKey: true, deltaY: -10, bubbles: true, cancelable: true}))""")
        time.sleep(0.4)
        tick = 1.25 * 1.25 ** 0.1
        check("zoom.pinch_tick_is_proportional",
              js(window, "document.querySelector('.cvx-bar .cvx-zoom').textContent")
              == f"{round(tick * 100)}%", f"(expected {round(tick * 100)}%)")
        js(window, """document.querySelector('.cvx-screen').dispatchEvent(
            new WheelEvent('wheel', {ctrlKey: true, deltaY: -5000, bubbles: true, cancelable: true}))""")
        time.sleep(0.4)
        check("zoom.one_event_caps_at_a_notch",
              js(window, "document.querySelector('.cvx-bar .cvx-zoom').textContent")
              == f"{round(tick * 1.25 * 100)}%", f"(expected {round(tick * 1.25 * 100)}%)")
        check("zoom.nothing_forwarded", api._cvx[sid].mouse == [],
              f"({api._cvx[sid].mouse})")
        # a PLAIN wheel is camera input and does go through (async bridge - poll)
        js(window, """document.querySelector('.cvx-screen').dispatchEvent(
            new WheelEvent('wheel', {deltaY: 240, bubbles: true, cancelable: true}))""")
        deadline = time.time() + 4
        while time.time() < deadline and not api._cvx[sid].mouse:
            time.sleep(0.2)
        check("zoom.plain_wheel_reaches_camera", len(api._cvx[sid].mouse) >= 1,
              f"({api._cvx[sid].mouse})")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown',
            {key: '0', ctrlKey: true, bubbles: true, cancelable: true}))""")
        time.sleep(0.4)
        check("zoom.ctrl0_resets",
              js(window, "document.querySelector('.cvx-bar .cvx-zoom').textContent") == "100%")

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
        # inside the same window, so the button must go through pywebview.
        js(window, "document.querySelectorAll('.cvx-bar .btn')[4].click()")
        time.sleep(1.5)
        check("fs.window_went_fullscreen", "main" in api._fullscreen)
        check("fs.js_state_agrees", js(window, "BV.fullscreen.active()") is True)
        # esc in fullscreen backs out the WINDOW, it does not park the remote
        js(window, """document.dispatchEvent(
            new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))""")
        time.sleep(1.5)
        check("fs.esc_leaves_fullscreen_first", "main" not in api._fullscreen)
        check("fs.esc_kept_the_remote_open",
              (js(window, "document.querySelector('.cvx-remote').style.display") or "") != "none")

        # ---- B3. esc PARKS the remote on its chip - it does not disconnect ----
        js(window, """document.dispatchEvent(
            new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))""")
        time.sleep(1)
        check("park.panel_hidden",
              js(window, "document.querySelector('.cvx-remote').style.display") == "none")
        check("park.chip_stays",
              js(window, "document.querySelectorAll('#sessionbar .stab.remote').length") == 1
              and not js(window, "document.querySelector('#sessionbar .stab.remote')"
                                 ".classList.contains('active')"))
        check("park.session_alive", sid in api._cvx and api._cvx[sid].alive is True)
        # a parked remote must not eat keys: its esc handler is detached
        js(window, """document.dispatchEvent(
            new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))""")
        time.sleep(0.5)
        check("park.parked_hears_no_keys", sid in api._cvx and api._cvx[sid].alive is True)
        # the chip brings it back
        js(window, "document.querySelector('#sessionbar .stab.remote').click()")
        time.sleep(0.6)
        check("park.chip_restores",
              js(window, "document.querySelector('.cvx-remote').style.display") != "none"
              and js(window, "document.querySelector('#sessionbar .stab.remote')"
                             ".classList.contains('active')"))
        # any route parks it too (navigation returns to the app)
        js(window, "BV.route()")
        time.sleep(0.6)
        check("park.route_parks",
              js(window, "document.querySelector('.cvx-remote').style.display") == "none")
        # re-opening the same camera FOCUSES the chip - never a second dial
        dials_now = list(FakeSession.dials)
        js(window, f"BV.openCvxRemote('{CAM_IP}', 'probe cam')")
        time.sleep(0.8)
        check("park.reopen_focuses",
              js(window, "document.querySelectorAll('.cvx-remote').length") == 1
              and js(window, "document.querySelector('.cvx-remote').style.display") != "none"
              and FakeSession.dials == dials_now, f"({FakeSession.dials})")
        # the chip's ✕ is what disconnects
        js(window, "document.querySelector('#sessionbar .stab.remote .stab-x').click()")
        time.sleep(1.5)
        check("park.x_disconnects",
              not js(window, "!!document.querySelector('.cvx-remote')")
              and js(window, "document.querySelectorAll('#sessionbar .stab.remote').length") == 0
              and sid not in api._cvx)

        # reopen for part C (the session above was stopped - this dials fresh)
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
              btns2 == ["⟳ reload", "phone", "100%", "fullscreen", "✕ close"], f"({btns2})")
        check("popout.adopted_not_redialled",
              api._cvx.get(sid) is live and FakeSession.dials == dials_before,
              f"({FakeSession.dials})")
        check("popout.window_key_is_the_session", js(win2, "BV.windowKey()") == sid)
        check("popout.streams_that_session",
              f"/cvx/{sid}" in (js(win2, "document.querySelector('.cvx-remote img').src") or ""))
        # a pop-out is an app window too: locked the same way (D)
        z2 = browser_zoom_state(win2)
        check("zoomlock.popout_control_off", z2.get("control") is False, f"({z2})")
        check("zoomlock.popout_factor_is_1", z2.get("factor") == 1.0, f"({z2})")

        # its ✕ closes the WINDOW, which is what stops the session
        js(win2, "document.querySelector('.cvx-bar .btn:last-child').click()")
        time.sleep(2)
        check("popout.close_stops_the_session",
              sid not in api._cvx and sid not in api._cvx_windows and live.alive is False)

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
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
