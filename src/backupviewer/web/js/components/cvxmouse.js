/* components/cvxmouse.js - BV.cvxMouse: the mouse path to a live CV-X.

   The ONLY place in the app that drives a real controller. Every line of it
   was paid for against hardware, which is why it lives here once instead of
   being copied into a second view:

     * Keyence's own VapiMouseEventId values (Vapi.Net.dll). A held button must
       send the dedicated DRAG id, not MOVE - the controller ignores plain
       MOVEs while pressed, so a drag would only snap at release.
     * a 4 px click-vs-drag dead zone. Hand jitter while a button is held must
       not read as a drag: a jittered right-click drag-cancels the controller's
       context menu instead of opening it.
     * a 45 ms move throttle (~22 moves/s).
     * a wheel accumulator - 100 px is one notch, at most 3 an event, and a
       direction change resets it so a trackpad fling cannot zoom forever.
     * mouseup bound on WINDOW, so releasing outside the picture still lands.
     * contextmenu suppressed, so the CONTROLLER's menu is the one you see.
     * a client sequence number on every send. pywebview runs each api call on
       its own Python thread, so calls can ARRIVE out of order (a press before
       its positioning move) and Python reorders by seq before touching the
       socket. Input is never chained on the bridge promises instead: one lost
       call would stall every later event.

   Coordinates come from the LIVE rect every time, so a view zoom never enters
   the math and this never has to know one exists.

   opts:
     sid()     -> the session id, or falsy when there is nothing to drive
     size()    -> {w,h} of the controller's screen, for the coordinate map
     enabled() -> false parks the whole thing (a float that is not armed)
   Returns { release(), destroy() }. release() lifts whatever is held down -
   call it whenever control is handed over, so no camera is left mid-drag. */
(function () {
  "use strict";

  /* Keyence's own VapiMouseEventId values: 5/6 are the wheel BUTTON (middle),
     10/11 wheel rotation (zoom), 14/15 the drag ids. */
  var EV_MOVE = 0, EV_LDOWN = 1, EV_LUP = 2, EV_RDOWN = 3, EV_RUP = 4,
      EV_MDOWN = 5, EV_MUP = 6, EV_WHEEL_UP = 10, EV_WHEEL_DOWN = 11,
      EV_DRAGGED = 14, EV_WHEEL_DRAGGED = 15;
  var DOWN_EV = { 0: EV_LDOWN, 1: EV_MDOWN, 2: EV_RDOWN };
  var UP_EV = { 0: EV_LUP, 1: EV_MUP, 2: EV_RUP };
  var DRAG_EV = { 0: EV_DRAGGED, 1: EV_WHEEL_DRAGGED, 2: EV_DRAGGED };

  BV.cvxMouse = function (screen, opts) {
    opts = opts || {};
    var seq = 0, lastMove = 0, downBtn = null, pressPt = null;
    var dragging = false, wheelAcc = 0;

    function on() {
      return !!(opts.sid && opts.sid()) &&
             (!opts.enabled || opts.enabled() !== false);
    }
    function toScreen(e) {
      var s = opts.size ? opts.size() : { w: 1024, h: 768 };
      var r = screen.getBoundingClientRect();
      return {
        x: Math.round((e.clientX - r.left) / r.width * s.w),
        y: Math.round((e.clientY - r.top) / r.height * s.h),
      };
    }
    function sendMouse(ev, p) {
      var sid = opts.sid && opts.sid();
      if (!sid) return;
      BV.api.call("cvx_remote_mouse", sid, ev, p.x, p.y, seq++).catch(function () {});
    }

    function onMove(e) {
      if (!on()) return;
      var p = toScreen(e);
      if (downBtn !== null && !dragging) {
        /* click-vs-drag dead-zone: hand jitter while a button is held must not
           read as a drag - a jittered right-click would drag-cancel the
           controller's context menu instead of opening it. */
        if (Math.abs(p.x - pressPt.x) < 4 && Math.abs(p.y - pressPt.y) < 4) return;
        dragging = true;
      }
      var now = Date.now();
      if (now - lastMove < 45) return;    /* throttle: ~22 moves/s */
      lastMove = now;
      /* held button -> the button's DRAG id; the controller pans on those and
         ignores plain MOVEs while pressed (hover stays MOVE) */
      sendMouse(dragging && downBtn !== null ? DRAG_EV[downBtn] : EV_MOVE, p);
    }
    function onDown(e) {
      if (!on() || !(e.button in DOWN_EV)) return;
      e.preventDefault();
      var p = toScreen(e);
      downBtn = e.button; pressPt = p; dragging = false;
      sendMouse(EV_MOVE, p);              /* position the cursor, then press */
      sendMouse(DOWN_EV[e.button], p);
    }
    /* on WINDOW: a release outside the picture still has to land, or the
       controller is left holding a button nobody is pressing */
    function onUp(e) {
      if (!on() || downBtn === null) return;
      var p = dragging ? toScreen(e) : pressPt;   /* a click releases where it pressed */
      sendMouse(UP_EV[downBtn], p);
      downBtn = null; dragging = false;
    }
    function onWheel(e) {
      if (e.ctrlKey) return;   /* view zoom - the caller owns it, the camera never hears it */
      if (!on()) return;
      e.preventDefault();
      var d = e.deltaY;
      if (e.deltaMode === 1) d *= 33;     /* lines -> px */
      else if (e.deltaMode === 2) d *= 300;
      if (wheelAcc !== 0 && (d > 0) !== (wheelAcc > 0)) wheelAcc = 0;
      wheelAcc += d;
      var p = toScreen(e), sent = 0;
      while (Math.abs(wheelAcc) >= 100 && sent < 3) {   /* 100 px = one notch */
        if (!sent) sendMouse(EV_MOVE, p);               /* zoom centers on the cursor */
        sendMouse(wheelAcc > 0 ? EV_WHEEL_DOWN : EV_WHEEL_UP, p);
        wheelAcc -= (wheelAcc > 0 ? 100 : -100);
        sent++;
      }
      if (sent === 3) wheelAcc = 0;       /* a trackpad fling must not zoom forever */
    }
    function onMenu(e) { e.preventDefault(); }

    screen.addEventListener("mousemove", onMove);
    screen.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    screen.addEventListener("wheel", onWheel, { passive: false });
    screen.addEventListener("contextmenu", onMenu);

    return {
      /* lift whatever is held, without needing an event to do it. Handing
         control from one view to another mid-drag would otherwise leave the
         controller pinned under a button nobody is pressing any more. */
      release: function () {
        if (downBtn !== null && pressPt) sendMouse(UP_EV[downBtn], pressPt);
        downBtn = null; dragging = false; wheelAcc = 0;
      },
      destroy: function () {
        screen.removeEventListener("mousemove", onMove);
        screen.removeEventListener("mousedown", onDown);
        window.removeEventListener("mouseup", onUp);
        screen.removeEventListener("wheel", onWheel);
        screen.removeEventListener("contextmenu", onMenu);
      },
    };
  };
})();
