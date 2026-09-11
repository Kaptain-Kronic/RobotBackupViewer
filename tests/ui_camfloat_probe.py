"""Hidden-window probe for the FLOATING camera boxes (camfloat.js, floatbox.js).

A wall tile is 150px tall, which is not big enough to read a camera's screen
from a step away. Right-clicking a tile pops it out into a float: a box you
drag, magnet to the layer's corners and edges, resize, zoom and lock.

The two things that are easy to get wrong here, and what each check is for:

  1. TWO VIEWS, ONE SESSION. A CV-X controller has exactly ONE remote slot. If
     a float and the tile it came from each fed their own picture, they would
     both dial that controller and the second would get CVX_BUSY - so popping
     a camera out has to cost ZERO dials. The wall renders a floated camera as
     a placeholder with no <img> in it at all, which is what makes "fetched
     exactly once" true by construction rather than by a guard. `popout.*` and
     `feed.*` pin both halves: the dial count does not move, and the document
     never holds two live imgs for one camera.

  2. WHERE THE LAYER PAINTS. html.frosted puts a backdrop-filter on the chrome
     slabs, which makes them stacking contexts - a layer mounted inside one
     paints UNDER the view (the cam picker's panel already learned this the
     hard way). The float layer is therefore on <html> at z 70, and `layer.*`
     reads it with elementFromPoint WITH FROST ON: above the wall, below an
     open modal. A box nobody can click is not a box.

Gestures are driven with synthetic mouse events on document, which is also how
the code is written: setPointerCapture throws in this window and there is no
requestAnimationFrame here, so a drag implemented with pointer capture would
pass by hand and fail under the probe (or worse, the reverse).

Fully offline: CvxRemoteSession is faked, so nothing dials a camera, and the
Matrox cameras are local HTTP servers on their own ports.
Fully synthetic and identifier-clean: TEST-NET ips, CELL fakes, an empty
library in a temp folder, APPDATA redirected there BEFORE importing the app.

Run: python tests/ui_camfloat_probe.py
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from probeutil import (FAILURES, check, exit_code, isolate, js, poll,  # noqa: E501
                       poller, report)

_TMP = isolate("bv_camfloat_probe_")

import webview  # noqa: E402

from backupviewer import cvx_remote  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from cvx_sim import _TINY_JPEG  # noqa: E402

CVX_CAMS = ["192.0.2.%d" % (70 + i) for i in range(2)]     # TEST-NET
MTX_COUNT = 2
TOTAL = len(CVX_CAMS) + MTX_COUNT
CVX_NAME = "CELL-01CVX10"
wait = poller(tries=40)


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
        self.mouse = []
        self.video_only = False
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

    def queue_mouse(self, seq, event_id, x, y):
        self.mouse.append(event_id)

    def latest_frame(self):
        return _TINY_JPEG

    def wait_frame(self, last, timeout):
        time.sleep(min(timeout, 0.05))
        return False


_REAL_TICK_JS = """(function(){
    Object.defineProperty(document, 'hidden',
      { configurable: true, get: function(){ return false; } });
    return document.hidden === false;
})()"""

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

# Right-click the named tile and take "pop out" off the menu - the real
# gesture, not a direct call into BV.camFloats, because the menu wiring is
# half of what is on trial here.
_POPOUT_JS = """(function(){
    var tiles=[].slice.call(document.querySelectorAll('.cam-tile'));
    var t=tiles.filter(function(n){
      var nm=n.querySelector('.lib-robot-name');
      return nm && nm.textContent===%s; })[0];
    if(!t) return 'no-tile';
    var r=t.getBoundingClientRect();
    t.dispatchEvent(new MouseEvent('contextmenu',
      {bubbles:true, cancelable:true, clientX:r.left+5, clientY:r.top+5}));
    var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
    var it=items.filter(function(b){
      return b.textContent.indexOf('pop out')>=0; })[0];
    if(!it) return 'no-menu-item:' + items.map(function(b){
      return b.textContent; }).join('|');
    it.click();
    return 'ok';
})()"""

# One read of the whole picture: how many boxes, how many live imgs live where,
# and whether any camera is being fetched twice.
_STATE_JS = """JSON.stringify((function(){
    var layer=document.getElementById('floatlayer');
    var boxes=layer?[].slice.call(layer.querySelectorAll('.fbox')):[];
    var all=[].slice.call(document.querySelectorAll('img.cam-live'));
    var byIp={};
    all.forEach(function(i){ byIp[i.dataset.ip]=(byIp[i.dataset.ip]||0)+1; });
    var dupes=Object.keys(byIp).filter(function(k){ return byIp[k]>1; });
    return {
      layer: !!layer,
      onHtml: !!layer && layer.parentNode === document.documentElement,
      hidden: !!layer && layer.hidden,
      boxes: boxes.length,
      floatImgs: layer?layer.querySelectorAll('img.cam-live').length:0,
      wallImgs: document.querySelectorAll('.cam-tile img.cam-live').length,
      placeholders: document.querySelectorAll('.cam-tile.floating').length,
      placeholderImgs:
        document.querySelectorAll('.cam-tile.floating img.cam-live').length,
      dupes: dupes,
      btn: (document.getElementById('lib-cam-float')||{}).textContent||''
    };
})())"""

# Above the wall, below a modal - read with FROST ON, because that is the case
# where a layer mounted in the wrong place disappears under the view.
_PAINT_JS = """(function(){
    var b=document.querySelector('.fbox');
    if(!b) return 'no-box';
    var frosted=document.documentElement.classList.contains('frosted');
    document.documentElement.classList.add('frosted');
    var r=b.getBoundingClientRect();
    var hit=document.elementFromPoint(r.left+r.width/2, r.top+4);
    if(!frosted) document.documentElement.classList.remove('frosted');
    return (hit && b.contains(hit)) ? 'ok' : 'covered';
})()"""

_UNDER_MODAL_JS = """(function(){
    var b=document.querySelector('.fbox');
    if(!b) return 'no-box';
    var body=document.createElement('div');
    body.style.cssText='width:40rem;height:20rem';
    var m=BV.modal('probe', body);
    var r=b.getBoundingClientRect();
    var hit=document.elementFromPoint(r.left+r.width/2, r.top+4);
    var under = !(hit && b.contains(hit));
    m.close(true);
    return under ? 'ok' : 'float-covers-the-modal';
})()"""

# Drag the first box's bar to a corner and drop it there. Mouse events on
# document, exactly as the code listens for them.
_DRAG_JS = """(function(tx, ty){
    var b=document.querySelector('.fbox');
    if(!b) return 'no-box';
    var bar=b.querySelector('.fbox-bar');
    var r=bar.getBoundingClientRect();
    var lr=document.getElementById('floatlayer').getBoundingClientRect();
    var x=lr.left+tx*lr.width, y=lr.top+ty*lr.height;
    bar.dispatchEvent(new MouseEvent('mousedown',
      {bubbles:true, cancelable:true, button:0,
       clientX:r.left+30, clientY:r.top+6}));
    document.dispatchEvent(new MouseEvent('mousemove',
      {bubbles:true, clientX:(x+r.left+30)/2, clientY:(y+r.top+6)/2}));
    var ghost=document.querySelector('.fbox-ghost');
    document.dispatchEvent(new MouseEvent('mousemove',
      {bubbles:true, clientX:x, clientY:y}));
    var ghostShown = ghost && !ghost.hidden;
    document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y}));
    return JSON.stringify({ghost:!!ghostShown,
      geom:BV.floatLayer.boxes()[0].geom()});
})(%s, %s)"""

_GEOM_JS = """JSON.stringify(BV.floatLayer.boxes().map(function(b){
    var r=b.el.getBoundingClientRect();
    var lr=document.getElementById('floatlayer').getBoundingClientRect();
    var g=b.geom();
    return {snap:g.snap, locked:g.locked,
            x:Math.round((r.left-lr.left)/lr.width*100)/100,
            y:Math.round((r.top-lr.top)/lr.height*100)/100,
            w:Math.round(r.width/lr.width*100)/100,
            h:Math.round(r.height/lr.height*100)/100};
}))"""


# why a box is not painting, in the terms the feed actually gates on
_WHY_JS = """JSON.stringify((function(){
    var i=document.querySelector('#floatlayer img.cam-live');
    if(!i) return {img:false};
    var r=i.getBoundingClientRect();
    var sc=i.parentNode.getBoundingClientRect();
    var st=i.closest('.fbox-stage').getBoundingClientRect();
    return {img:true, src:(i.src||'').slice(0,60), due:i._camDue,
            shown:i._camShown, note:i._camNote,
            vis:i.checkVisibility?i.checkVisibility():null,
            imgBox:[Math.round(r.width),Math.round(r.height)],
            screenBox:[Math.round(sc.width),Math.round(sc.height)],
            stageBox:[Math.round(st.width),Math.round(st.height)],
            lease: !!BV.camFeed.lease(i.dataset.ip)};
})())"""


# Drive the picture and count what actually reached python. Mouse events on
# the SCREEN element, which is what BV.cvxMouse listens to.
_DRIVE_JS = """(function(){
    var s=document.querySelector('#floatlayer .fbox-screen.drivable')
          || document.querySelector('#floatlayer .fbox-screen');
    if(!s) return 'no-screen';
    var r=s.getBoundingClientRect();
    s.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,
      clientX:r.left+12, clientY:r.top+12}));
    return 'sent';
})()"""


def _state(window):
    return json.loads(js(window, _STATE_JS) or "{}")


def _geoms(window):
    return json.loads(js(window, _GEOM_JS) or "[]")


def probe(window, api, mtx_hosts):
    try:
        time.sleep(4)  # boot
        js(window, _REAL_TICK_JS)

        js(window, _ADD_CAMS_JS % (json.dumps(CVX_CAMS), json.dumps(mtx_hosts)))
        check("setup.cameras_added",
              poll(window, "window.__added===%d ? 'y' : ''" % TOTAL) == "y",
              "(got %s of %d)" % (js(window, "window.__added"), TOTAL))
        js(window, "BV.state.emit('library-dirty')")
        js(window, "document.getElementById('cube-cam').click()")
        check("setup.lens_flips", bool(poll(window,
              "!!document.querySelector('.home-library.cam-mode')")))
        for _ in range(20):
            js(window, _OPEN_LINES_JS)
            time.sleep(0.3)
            if js(window, "document.querySelector('.cam-tile-box') && "
                          "document.querySelector('.cam-tile-box').getBoundingClientRect()"
                          ".height > 0 ? 'y' : ''") == "y":
                break
        check("setup.tiles_have_boxes", js(window, TILES := "%s" % (
            "document.querySelectorAll('.cam-tile img.cam-live').length")) == TOTAL,
            "(wall shows %s of %d)" % (js(window, TILES), TOTAL))

        # the wall settles first, so the dial count below means "popping out",
        # not "the wall was still starting up"
        wait(window, "document.querySelectorAll('.cam-tile img.cam-live')"
                     ".length && [].every.call("
                     "document.querySelectorAll('.cam-tile img.cam-live'),"
                     "function(i){return i.naturalWidth>0;}) ? 'y' : ''")
        dials_before = len(FakeCvxSession.dials)

        # ---- A. pop one out, by the real gesture ----------------------------
        before = _state(window)
        check("layer.absent_until_wanted", not before["layer"] or not before["boxes"],
              "(a layer with boxes in it before anything was popped out)")

        r = js(window, _POPOUT_JS % json.dumps(CVX_NAME))
        check("popout.menu_offers_it", r == "ok", "(%s)" % r)
        wait(window, "document.querySelectorAll('.fbox').length ? 'y' : ''")
        st = _state(window)
        check("popout.box_appears", st["boxes"] == 1, "(%d boxes)" % st["boxes"])
        check("layer.on_html", st["onHtml"],
              "(the layer must hang off <html>: inside a frosted chrome slab it "
              "paints under the view)")
        check("popout.tile_holds_its_place", st["placeholders"] == 1,
              "(%d placeholder tiles - the wall must not reflow under you)" %
              st["placeholders"])
        check("popout.placeholder_has_no_img", st["placeholderImgs"] == 0,
              "(a floated camera's tile still carries an <img>: that is the "
              "double-fetch this design exists to make impossible)")
        check("popout.count_is_honest", st["btn"] == "floating · 1",
              "(button reads %r)" % st["btn"])
        check("feed.no_camera_fetched_twice", st["dupes"] == [],
              "(two live imgs for %s)" % st["dupes"])
        check("feed.popping_out_costs_no_dial",
              len(FakeCvxSession.dials) == dials_before,
              "(dialled %d more times - the float must ADOPT the tile's lease, "
              "not ask the controller for a second slot)" %
              (len(FakeCvxSession.dials) - dials_before))
        painted = wait(window,
              "(function(){var i=document.querySelector('#floatlayer img.cam-live');"
              "return i && i.naturalWidth>0 ? 'y':'';})()")
        check("feed.the_box_paints", painted == "y",
              "(the floating box never got a picture: %s)" % js(window, _WHY_JS))

        # ---- B. where it paints ---------------------------------------------
        check("layer.paints_over_the_wall", js(window, _PAINT_JS) == "ok",
              "(a box the wall covers is a box nobody can click)")
        check("layer.under_a_modal", js(window, _UNDER_MODAL_JS) == "ok",
              "(a dialog has to land on top of the floats)")

        # ---- C. drag + snap ---------------------------------------------------
        for zone, tx, ty, want in [("se", 0.99, 0.99, {"x": 0.5, "y": 0.5}),
                                   ("w", 0.005, 0.5, {"x": 0.0, "y": 0.0})]:
            out = json.loads(js(window, _DRAG_JS % (tx, ty)) or "{}")
            g = out.get("geom", {})
            check("snap.%s_ghost_shown" % zone, out.get("ghost") is True,
                  "(no preview while dragging into the %s band)" % zone)
            check("snap.%s_took_the_zone" % zone, g.get("snap") == zone,
                  "(landed in %r, wanted %r)" % (g.get("snap"), zone))
            geo = _geoms(window)[0]
            check("snap.%s_rect_matches" % zone,
                  abs(geo["x"] - want["x"]) < 0.02 and abs(geo["y"] - want["y"]) < 0.02,
                  "(rect %r is not the %s zone)" % (geo, zone))

        # a drop away from every band is FREE placement, not a snap
        out = json.loads(js(window, _DRAG_JS % (0.45, 0.45)) or "{}")
        check("snap.centre_is_free_placement", out.get("geom", {}).get("snap") == "",
              "(a drop in open space snapped anyway: %r)" %
              out.get("geom", {}).get("snap"))

        # ---- D. lock ----------------------------------------------------------
        js(window, "document.querySelector('.fbox .fbox-lock').click()")
        check("lock.marks_the_box",
              js(window, "document.querySelector('.fbox').classList.contains('locked')"),
              "(no .locked class)")
        check("lock.hides_the_grips", js(window,
              "getComputedStyle(document.querySelector('.fbox-rz')).display") == "none",
              "(a locked box still offers resize grips)")
        locked_before = _geoms(window)[0]
        js(window, _DRAG_JS % (0.99, 0.01))
        check("lock.refuses_the_drag", _geoms(window)[0] == locked_before,
              "(a locked box moved: %r -> %r)" % (locked_before, _geoms(window)[0]))
        check("lock.survives_a_repaint", (js(window, "BV.state.emit('library-dirty')"),
              wait(window, "document.querySelector('.fbox') && "
                           "document.querySelector('.fbox').classList"
                           ".contains('locked') ? 'y':''") == "y")[1],
              "(the lock was lost when the library repainted)")
        js(window, "document.querySelector('.fbox .fbox-lock').click()")

        # ---- E. geometry survives a resize ------------------------------------
        js(window, _DRAG_JS % (0.99, 0.01))     # park it in the ne corner
        check("geom.snapped_before_resize", _geoms(window)[0]["snap"] == "ne",
              "(setup: expected the ne zone)")
        js(window, "window.dispatchEvent(new Event('resize'))")
        time.sleep(0.4)
        g = _geoms(window)[0]
        check("geom.snap_rederives_on_resize",
              abs(g["x"] - 0.5) < 0.02 and abs(g["y"]) < 0.02 and
              abs(g["w"] - 0.5) < 0.03,
              "(a snapped box must recompute from its zone, not from stale px: %r)" % g)
        check("geom.stays_inside_the_layer",
              g["x"] >= -0.01 and g["y"] >= -0.01 and
              g["x"] + g["w"] <= 1.02 and g["y"] + g["h"] <= 1.02,
              "(box escaped the layer: %r)" % g)

        # ---- F. parking off the library ---------------------------------------
        # With no backup open every non-home route bounces straight back to
        # #home, so parking is read at the seam the router actually uses: set
        # the hash and call syncRoute in the SAME turn, exactly as route() does
        # before it resolves the hash. Then let the bounce restore it.
        parked = json.loads(js(window, """JSON.stringify((function(){
            var l=document.getElementById('floatlayer');
            var was=location.hash;
            location.hash='#files';
            BV.camFloats.syncRoute();
            var hidden=l.hidden;
            var img=document.querySelector('#floatlayer img.cam-live');
            var fed=!!(img && img.checkVisibility && img.checkVisibility());
            location.hash=was;
            BV.camFloats.syncRoute();
            return {hidden:hidden, fed:fed, backUp:!l.hidden};
        })())""") or "{}")
        check("park.layer_hides_off_library", parked.get("hidden") is True,
              "(the floats followed you off the library screen)")
        check("park.pictures_stop_being_fed", parked.get("fed") is False,
              "(a parked picture still passes the feed's visibility gate, so its "
              "lease would go on being renewed and the slot never handed back)")
        check("park.restores_on_return", parked.get("backUp") is True,
              "(the boxes did not come back)")
        back = wait(window, "(function(){var l=document.getElementById('floatlayer');"
                            "return l && !l.hidden ? 'y':'';})()")
        g2 = _geoms(window)[0]
        check("park.same_place", g2["snap"] == "ne",
              "(parking rearranged the box: %r)" % g2)

        # ---- G. the wall knows, and the openers know --------------------------
        # the hash round-trip above re-routes home, and the wall rebuilds itself
        # from a fresh lib_list - so wait for the placeholder to come back (and
        # re-open the folds a repaint may have closed) rather than reading the
        # DOM mid-refetch. Same reason the camwall probe polls its folds.
        for _ in range(20):
            js(window, _OPEN_LINES_JS)
            if js(window, "document.querySelector('.cam-tile.floating') ? 'y':''") == "y":
                break
            time.sleep(0.3)
        clicked = js(window,
              "(function(){var t=document.querySelector('.cam-tile.floating');"
              "if(!t) return 'no-placeholder';"
              "var n=BV.floatLayer.boxes().length; t.click();"
              "return (document.querySelector('.fbox.fbox-flash') ? 'ok' :"
              " 'no-flash') + ':boxes=' + n + ':focus=' +"
              " (BV.camFloats.count());})()")
        check("wall.click_finds_the_box", (clicked or "").split(":")[0] == "ok",
              "(clicking a floated camera's tile must point at its box, never "
              "dial a controller whose one slot that box is holding) [%s]" % clicked)
        check("openers.defer_to_the_layer", js(window,
              "BV.camFloats.focusIp(%s) === true" % json.dumps(CVX_CAMS[0])),
              "(openCvxRemote would dial a camera the layer already holds)")

        # ---- H. control: take it, arm it, and only ever one at a time --------
        # a view-only box must forward NOTHING. Python refuses a video_only
        # session too (cvx_remote_mouse -> VIEW_ONLY), so the rule is enforced
        # on both sides rather than trusted on one.
        def mouse_events():
            return sum(len(getattr(s, "mouse", [])) for s in api._cvx.values())

        for s0 in api._cvx.values():
            s0.mouse = []
        js(window, _DRIVE_JS)
        time.sleep(1.0)
        check("control.view_only_forwards_nothing", mouse_events() == 0,
              "(a box that never took control sent %d mouse events - and python "
              "should have refused them too)" % mouse_events())

        js(window, "document.querySelector('.fbox .fbox-ctl').click()")
        took = wait(window, "BV.camFloats.armed() ? 'y':''")
        check("control.take_promotes_the_session", took == "y",
              "(taking control did not arm the box)")
        check("control.streams_while_driving", js(window,
              "(document.querySelector('#floatlayer img.cam-live').src||'')"
              ".indexOf('/cvx/')>=0"),
              "(a controlling box must hold the live stream, not the 2s still)")
        check("control.no_extra_dial", len(FakeCvxSession.dials) == dials_before,
              "(taking control redialled: it must ADOPT the lease it already has)")
        check("control.the_bar_says_so", js(window,
              "document.querySelector('.fbox .fbox-ctl').textContent") == "driving",
              "(nothing on the box says a controller is being driven)")

        for s0 in api._cvx.values():
            s0.mouse = []
        js(window, _DRIVE_JS)
        drove = 0
        deadline = time.time() + 4
        while time.time() < deadline and not mouse_events():
            time.sleep(0.15)
        drove = mouse_events()
        check("control.armed_drives_the_camera", drove > 0,
              "(an armed box forwarded nothing to the controller)")

        # a second box, and arming it must release the first - one camera under
        # a live cursor at a time, and never one left mid-drag
        js(window, _POPOUT_JS % json.dumps("CELL-01CVX11"))
        wait(window, "document.querySelectorAll('.fbox').length===2 ? 'y':''")
        first = js(window, "BV.camFloats.armed()")
        js(window, "[].slice.call(document.querySelectorAll('.fbox .fbox-ctl'))"
                   ".filter(function(b){return b.textContent==='control';})[0].click()")
        wait(window, "BV.camFloats.armed() && BV.camFloats.armed()!==%s ? 'y':''"
             % json.dumps(first))
        check("control.arming_is_exclusive",
              js(window, "document.querySelectorAll('.fbox.armed').length") == 1,
              "(%s boxes armed at once)" %
              js(window, "document.querySelectorAll('.fbox.armed').length"))

        # clicking away gives the mouse back to the app
        js(window, "document.body.click()")
        check("control.click_away_disarms",
              js(window, "BV.camFloats.armed()") in (None, "", False),
              "(the mouse stayed on the camera after clicking away)")
        for s0 in api._cvx.values():
            s0.mouse = []
        js(window, _DRIVE_JS)
        time.sleep(1.0)
        check("control.disarmed_forwards_nothing", mouse_events() == 0,
              "(a disarmed box is still driving a controller: %d events)"
              % mouse_events())

        # giving control back must NOT let go of the controller's slot
        stops_at_release = len(FakeCvxSession.stops)
        js(window, "[].slice.call(document.querySelectorAll('.fbox .fbox-ctl'))"
                   ".filter(function(b){return b.classList.contains('on');})"
                   ".forEach(function(b){b.click();})")
        back = wait(window, "[].every.call(document.querySelectorAll('.fbox .fbox-ctl'),"
                            "function(b){return b.textContent==='control';}) ? 'y':''")
        check("control.release_returns_to_view_only", back == "y",
              "(the boxes did not go back to view-only)")
        check("control.release_keeps_the_slot",
              len(FakeCvxSession.stops) == stops_at_release,
              "(giving control back stopped the session: yield demotes it to a "
              "lease so the slot is never let go of and raced for)")
        check("control.picture_comes_back", wait(window,
              "[].every.call(document.querySelectorAll('#floatlayer img.cam-live'),"
              "function(i){return i.naturalWidth>0;}) ? 'y':''") == "y",
              "(a box that gave control back never got its picture again)")

        # Closing a box that is DRIVING is the subtle leak: a controlling box
        # holds a promoted session, and cvx_tile_stop is a deliberate no-op for
        # a non-tile sid - so the obvious teardown would hand back nothing at
        # all, in silence, and the controller would stay held until app exit.
        js(window, "document.querySelector('.fbox .fbox-ctl').click()")
        wait(window, "BV.camFloats.armed() ? 'y':''")
        stops_driving = len(FakeCvxSession.stops)
        js(window, "document.querySelector('.fbox .fbox-x').click()")
        time.sleep(1.2)
        check("control.closing_while_driving_frees_the_slot",
              len(FakeCvxSession.stops) > stops_driving,
              "(a box closed mid-drive handed nothing back - cvx_tile_stop is a "
              "no-op on a promoted session, it needs cvx_remote_stop)")
        check("control.nothing_left_armed",
              js(window, "BV.camFloats.armed()") in (None, "", False),
              "(the armed box is gone but something still thinks it is driving)")

        js(window, "BV.camFloats.closeAll()")
        wait(window, "document.querySelectorAll('.fbox').length===0 ? 'y':''")
        for _ in range(20):
            js(window, _OPEN_LINES_JS)
            if js(window, "document.querySelectorAll('.cam-tile img.cam-live')"
                          ".length===%d ? 'y':''" % TOTAL) == "y":
                break
            time.sleep(0.3)
        # the tile has to have re-taken its lease before the box can inherit it,
        # or the close below has no session to hand back and the check would
        # pass for the wrong reason
        wait(window, "BV.camFeed.lease(%s) ? 'y':''" % json.dumps(CVX_CAMS[0]))
        js(window, _POPOUT_JS % json.dumps(CVX_NAME))
        wait(window, "document.querySelectorAll('.fbox').length===1 ? 'y':''")
        check("close.setup_has_a_lease",
              js(window, "!!BV.camFeed.lease(%s)" % json.dumps(CVX_CAMS[0])),
              "(setup: the re-popped box holds no session, so the close check "
              "below would pass without proving anything)")

        # ---- I. closing hands the slot back -----------------------------------
        stops_before = len(FakeCvxSession.stops)
        js(window, "document.querySelector('.fbox .fbox-x').click()")
        gone = wait(window, "document.querySelectorAll('.fbox').length===0 ? 'y':''")
        check("close.box_goes", gone == "y", "(the box would not close)")
        check("close.frees_the_slot",
              len(FakeCvxSession.stops) > stops_before,
              "(closing a CV-X box must hand the controller's slot back at once, "
              "not leave it to the reaper)")
        st = _state(window)
        check("close.tile_comes_back", st["placeholders"] == 0 and
              st["wallImgs"] == TOTAL,
              "(the wall did not take the camera back: %r)" % st)
        check("close.count_is_honest", st["btn"] == "floating · 0",
              "(button reads %r)" % st["btn"])

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
        width=1400,
        height=900,
        hidden=True,
    )
    api.bind(window)
    webview.start(lambda: probe(window, api, mtx_hosts), gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
