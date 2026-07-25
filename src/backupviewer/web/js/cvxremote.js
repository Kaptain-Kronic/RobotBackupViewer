/* cvxremote.js - live CV-X remote-desktop overlay (screen mirror + mouse).

   BV.openCvxRemote(ip, label) opens a fullscreen-capable panel that mirrors a
   Keyence CV-X controller's live 1024x768 screen and forwards mouse input -
   all three buttons plus the wheel, so the controller's own zoom (scroll),
   pan (middle-drag) and context-menu (right-click) gestures work.

   The Python side (cvx_remote.py, via api.cvx_remote_*) speaks the controller's
   own TCP protocol and re-streams frames as MJPEG over a localhost HTTP server,
   so the live screen is just an <img> - no JS decoding. Mouse events map from the
   rendered image rect back to 1024x768 controller pixels. Wholly separate from
   the CV-X anon-FTP backup path.

   The bar carries the same options as the Matrox remote: reload (reconnect),
   open in window, phone, fullscreen, close. opts (3rd arg) is how a popped-out
   window boots: {adopt: sid} takes over a session that is ALREADY connected
   instead of dialling the controller again - it only has one remote slot -
   and {owned: true} means this window IS the session, so closing closes it. */
(function () {
  "use strict";

  var SCREEN_W = 1024, SCREEN_H = 768;
  /* Keyence's own VapiMouseEventId values (Vapi.Net.dll): 5/6 are the wheel
     BUTTON (middle), 10/11 wheel rotation (zoom). Moving with a button held
     must be sent as the dedicated DRAG id, not MOVE - the controller ignores
     plain MOVEs while pressed and the viewport would only snap at release. */
  var EV_MOVE = 0, EV_LDOWN = 1, EV_LUP = 2, EV_RDOWN = 3, EV_RUP = 4,
      EV_MDOWN = 5, EV_MUP = 6, EV_WHEEL_UP = 10, EV_WHEEL_DOWN = 11,
      EV_DRAGGED = 14, EV_WHEEL_DRAGGED = 15;
  var DOWN_EV = { 0: EV_LDOWN, 1: EV_MDOWN, 2: EV_RDOWN };
  var UP_EV = { 0: EV_LUP, 1: EV_MUP, 2: EV_RUP };
  var DRAG_EV = { 0: EV_DRAGGED, 1: EV_WHEEL_DRAGGED, 2: EV_DRAGGED };
  /* while the fullscreen remote is up, swallow the app's tab-switch keys so they
     don't change the tab hidden behind it (keys.js maps digits / - / = to tabs).
     the cv-x has no pc-keyboard input over its protocol - only mouse. */
  var NAV_KEYS = "0123456789-=";
  var open = false;   /* one session at a time */

  BV.openCvxRemote = function (ip, label, opts) {
    if (open) { BV.toast("a remote session is already open"); return; }
    ip = (ip || "").trim();
    opts = opts || {};
    if (!ip && !opts.adopt) { BV.toast("this camera has no IP on record"); return; }
    open = true;

    var sid = null, statusTimer = null, lastMove = 0, downBtn = null;
    var pressPt = null, dragging = false, wheelAcc = 0;
    var closed = false;   /* so a connect that resolves AFTER teardown stops the session it made */
    var adopt = opts.adopt || null;   /* the already-open session to take over */
    /* what closes THIS window when it owns the session - the id it was popped
       out under, which survives a reload (Python keeps the id) and a failure */
    var winKey = opts.adopt || null;
    var busy = false;                 /* a reconnect is in flight - don't stack them */
    var errBox = null;                /* the "could not connect" panel, cleared on retry */

    /* --- overlay chrome ------------------------------------------------- */
    var overlay = BV.el("div", { class: "cvx-remote" });
    var bar = BV.el("div", { class: "cvx-bar" });
    var title = BV.el("span", { class: "cvx-title" },
      "CV-X remote · " + BV.esc(label || ip));
    var status = BV.el("span", { class: "cvx-status" }, "connecting…");
    var spacer = BV.el("span", { style: "margin-left:auto" });
    var rlBtn = BV.el("button", { class: "btn", title: "reconnect to the camera" }, "⟳ reload");
    var winBtn = BV.el("button", { class: "btn",
      title: "move this remote into its own window" }, "open in window");
    var phBtn = BV.el("button", { class: "btn",
      title: "mirror this window to your phone (QR) — watch the live screen " +
        "at the camera" }, "📱 phone");
    var fsBtn = BV.el("button", { class: "btn", title: "fullscreen (f)" }, "fullscreen");
    var closeBtn = BV.el("button", { class: "btn", title: "close (esc)" }, "✕ close");
    bar.appendChild(title); bar.appendChild(status); bar.appendChild(spacer);
    bar.appendChild(rlBtn);
    if (!opts.owned) bar.appendChild(winBtn);   /* already in its own window */
    bar.appendChild(phBtn);
    bar.appendChild(fsBtn); bar.appendChild(closeBtn);

    var stage = BV.el("div", { class: "cvx-stage" });
    var screen = BV.el("div", { class: "cvx-screen" });
    var img = BV.el("img", { alt: "CV-X live screen", draggable: "false" });
    var hint = BV.el("div", { class: "cvx-hint" }, "waiting for the first frame…");
    screen.appendChild(img); screen.appendChild(hint);
    stage.appendChild(screen);
    overlay.appendChild(bar); overlay.appendChild(stage);
    document.body.appendChild(overlay);

    /* keep the 4:3 screen box as large as fits, so mouse coords map linearly */
    function fit() {
      var sw = stage.clientWidth, sh = stage.clientHeight, ar = SCREEN_W / SCREEN_H;
      var w = sw, h = sw / ar;
      if (h > sh) { h = sh; w = sh * ar; }
      screen.style.width = Math.round(w) + "px";
      screen.style.height = Math.round(h) + "px";
    }
    window.addEventListener("resize", fit);
    fit();

    /* --- teardown ------------------------------------------------------- */
    /* keepSession: the remote lives on elsewhere (it just moved to its own
       window) - tear the overlay down but leave the controller connected */
    function close(keepSession) {
      if (closed) return;
      closed = true;
      open = false;
      clearInterval(statusTimer);
      window.removeEventListener("resize", fit);
      window.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keydown", onKeyCapture, true);
      img.src = "";                       /* drop the MJPEG connection */
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      overlay.remove();
      if (keepSession === true) return;
      /* in its own window the session IS the window: closing it closes the
         window, which is what stops the session (api._close_cvx_window) */
      if (opts.owned && winKey) BV.api.call("cvx_remote_window_close", winKey).catch(function () {});
      else if (sid) BV.api.call("cvx_remote_stop", sid).catch(function () {});
    }
    closeBtn.addEventListener("click", function () { close(); });

    function toggleFs() {
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      else { try { overlay.requestFullscreen(); } catch (e) {} }
    }
    fsBtn.addEventListener("click", toggleFs);
    rlBtn.addEventListener("click", reconnect);
    phBtn.addEventListener("click", function () { BV.openViewfinder(); });
    winBtn.addEventListener("click", function () {
      if (!sid) { BV.toast("not connected yet"); return; }
      winBtn.disabled = true;
      BV.api.call("cvx_remote_window", { session_id: sid, label: label || "" })
        .then(function () {
          close(true);          /* the session MOVED - don't stop it on the way out */
          BV.toast("moved to its own window");
        }).catch(function (e) {
          winBtn.disabled = false;
          BV.toast("could not open window: " + e.message);
        });
    });

    function onKey(e) {
      if (e.key === "Escape") {
        if (document.fullscreenElement) return;   /* let the browser exit fs first */
        e.preventDefault(); close();
      } else if (e.key === "f" || e.key === "F") {
        toggleFs();
      }
    }
    document.addEventListener("keydown", onKey);

    /* capture phase so a nav key never leaks to keys.js and switches a tab
       behind the fullscreen remote */
    function onKeyCapture(e) {
      if (e.ctrlKey || e.altKey || e.metaKey) return;
      if (NAV_KEYS.indexOf(e.key) < 0) return;
      e.preventDefault(); e.stopPropagation();
    }
    document.addEventListener("keydown", onKeyCapture, true);

    /* --- mouse forwarding ----------------------------------------------- */
    function toScreen(e) {
      var r = screen.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width * SCREEN_W;
      var y = (e.clientY - r.top) / r.height * SCREEN_H;
      return { x: Math.round(x), y: Math.round(y) };
    }
    /* fire-and-forget with a client sequence number. pywebview runs each api
       call on its own Python thread (util.js_bridge_call), so calls can
       ARRIVE out of order (a press before its positioning move) - Python
       reorders by seq before touching the socket. Don't chain input on the
       bridge promises instead: one lost call would stall every later event. */
    var seq = 0;
    function sendMouse(ev, p) {
      if (!sid) return;
      BV.api.call("cvx_remote_mouse", sid, ev, p.x, p.y, seq++).catch(function () {});
    }
    screen.addEventListener("mousemove", function (e) {
      if (!sid) return;
      var p = toScreen(e);
      if (downBtn !== null && !dragging) {
        /* click-vs-drag dead-zone: hand jitter while a button is held must
           not read as a drag - a jittered right-click would drag-cancel the
           controller's context menu instead of opening it. */
        if (Math.abs(p.x - pressPt.x) < 4 && Math.abs(p.y - pressPt.y) < 4) return;
        dragging = true;
      }
      var now = Date.now();
      if (now - lastMove < 45) return;    /* throttle: ~22 moves/s */
      lastMove = now;
      /* held button -> the button's DRAG id; the controller pans on those
         and ignores plain MOVEs while pressed (hover stays MOVE) */
      sendMouse(dragging && downBtn !== null ? DRAG_EV[downBtn] : EV_MOVE, p);
    });
    screen.addEventListener("mousedown", function (e) {
      if (!sid || !(e.button in DOWN_EV)) return;
      e.preventDefault();
      var p = toScreen(e);
      downBtn = e.button; pressPt = p; dragging = false;
      sendMouse(EV_MOVE, p);              /* position the cursor, then press */
      sendMouse(DOWN_EV[e.button], p);
    });
    function onMouseUp(e) {
      if (!sid || downBtn === null) return;
      var p = dragging ? toScreen(e) : pressPt;   /* a click releases where it pressed */
      sendMouse(UP_EV[downBtn], p);
      downBtn = null; dragging = false;
    }
    window.addEventListener("mouseup", onMouseUp);
    screen.addEventListener("wheel", function (e) {
      if (!sid) return;
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
    }, { passive: false });
    screen.addEventListener("contextmenu", function (e) { e.preventDefault(); });

    /* --- connect + stream ----------------------------------------------- */
    img.addEventListener("load", function () { hint.style.display = "none"; });

    /* every dial lands here: start, adopt and reload all answer the same shape.
       The stream URL gets a nonce so re-pointing the <img> at the same session
       really re-opens the MJPEG connection (the server ignores the query). */
    function connected(r) {
      if (closed) {
        /* the overlay was torn down while connecting - stop the session it just
           opened so we never strand the camera's one remote slot */
        if (r && r.session_id) BV.api.call("cvx_remote_stop", r.session_id).catch(function () {});
        return;
      }
      sid = r.session_id;
      if (r.ip) ip = r.ip;
      if (r.screen) { SCREEN_W = r.screen.w; SCREEN_H = r.screen.h; fit(); }
      img.src = r.stream_url + "?t=" + Date.now();
      clearInterval(statusTimer);
      statusTimer = setInterval(pollStatus, 1000);
    }

    function failed(e) {
      if (closed) return;
      sid = null;            /* nothing is connected - the next reload dials fresh */
      hint.style.display = "none";
      status.textContent = "connection failed";
      status.classList.add("err");
      errBox = BV.el("div", { class: "cvx-error" },
        '<div class="big">could not connect</div><div class="hint">' + BV.esc(e.message) +
        "</div><div class=\"hint\">the camera may be off, or the Terminal / an operator is already on it.</div>");
      screen.appendChild(errBox);
    }

    /* reload = hang up and dial again. Python does both, in that order and
       under the SAME session id, so the controller's one remote slot is free
       before we ask for it back and nothing downstream has to re-key. */
    function reconnect() {
      if (busy || closed) return;
      busy = true;
      rlBtn.disabled = true;
      clearInterval(statusTimer);
      img.src = "";
      if (errBox) { errBox.remove(); errBox = null; }
      hint.textContent = "reconnecting…";
      hint.style.display = "";
      status.textContent = "reconnecting…";
      status.classList.remove("err");
      var call = sid ? BV.api.call("cvx_remote_reload", sid)
                     : BV.api.call("cvx_remote_start", { ip: ip });
      call.then(connected).catch(failed).then(function () {
        busy = false;
        rlBtn.disabled = false;
        hint.textContent = "waiting for the first frame…";
      });
    }

    /* a popped-out window takes over the session that is already connected;
       everything else dials the controller */
    (adopt ? BV.api.call("cvx_remote_info", adopt)
           : BV.api.call("cvx_remote_start", { ip: ip })
    ).then(connected).catch(failed);

    function pollStatus() {
      if (!sid) return;
      BV.api.call("cvx_remote_status", sid).then(function (s) {
        if (!open) return;
        if (s.error) {
          status.textContent = "error"; status.classList.add("err");
          hint.style.display = ""; hint.textContent = s.error;
        } else if (!s.alive) {
          status.textContent = "disconnected"; status.classList.add("err");
        } else if (s.frames > 0) {
          status.textContent = "live · " + s.frames + " frames"; status.classList.remove("err");
        } else {
          status.textContent = s.handshake_done ? "connected — awaiting screen…" : "connecting…";
        }
      }).catch(function () {});
    }
  };
})();
