/* cvxremote.js - live CV-X remote-desktop overlay (screen mirror + mouse).

   BV.openCvxRemote(ip, label) opens a fullscreen-capable panel that mirrors a
   Keyence CV-X controller's live 1024x768 screen and forwards mouse input.

   The Python side (cvx_remote.py, via api.cvx_remote_*) speaks the controller's
   own TCP protocol and re-streams frames as MJPEG over a localhost HTTP server,
   so the live screen is just an <img> - no JS decoding. Mouse events map from the
   rendered image rect back to 1024x768 controller pixels. Wholly separate from
   the CV-X anon-FTP backup path. */
(function () {
  "use strict";

  var SCREEN_W = 1024, SCREEN_H = 768;
  var EV_MOVE = 0, EV_LDOWN = 1, EV_LUP = 2, EV_RDOWN = 3, EV_RUP = 4;
  var open = false;   /* one session at a time */

  BV.openCvxRemote = function (ip, label) {
    if (open) { BV.toast("a remote session is already open"); return; }
    ip = (ip || "").trim();
    if (!ip) { BV.toast("this camera has no IP on record"); return; }
    open = true;

    var sid = null, statusTimer = null, lastMove = 0, downBtn = null;

    /* --- overlay chrome ------------------------------------------------- */
    var overlay = BV.el("div", { class: "cvx-remote" });
    var bar = BV.el("div", { class: "cvx-bar" });
    var title = BV.el("span", { class: "cvx-title" },
      "CV-X remote · " + BV.esc(label || ip));
    var status = BV.el("span", { class: "cvx-status" }, "connecting…");
    var spacer = BV.el("span", { style: "margin-left:auto" });
    var fsBtn = BV.el("button", { class: "btn", title: "fullscreen (f)" }, "fullscreen");
    var closeBtn = BV.el("button", { class: "btn", title: "close (esc)" }, "✕ close");
    bar.appendChild(title); bar.appendChild(status); bar.appendChild(spacer);
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
    function close() {
      if (!open) return;
      open = false;
      clearInterval(statusTimer);
      window.removeEventListener("resize", fit);
      document.removeEventListener("keydown", onKey);
      img.src = "";                       /* drop the MJPEG connection */
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      overlay.remove();
      if (sid) BV.api.call("cvx_remote_stop", sid).catch(function () {});
    }
    closeBtn.addEventListener("click", close);

    function toggleFs() {
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      else { try { overlay.requestFullscreen(); } catch (e) {} }
    }
    fsBtn.addEventListener("click", toggleFs);

    function onKey(e) {
      if (e.key === "Escape") {
        if (document.fullscreenElement) return;   /* let the browser exit fs first */
        e.preventDefault(); close();
      } else if (e.key === "f" || e.key === "F") {
        toggleFs();
      }
    }
    document.addEventListener("keydown", onKey);

    /* --- mouse forwarding ----------------------------------------------- */
    function toScreen(e) {
      var r = screen.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width * SCREEN_W;
      var y = (e.clientY - r.top) / r.height * SCREEN_H;
      return { x: Math.round(x), y: Math.round(y) };
    }
    function sendMouse(ev, p) {
      if (!sid) return;
      BV.api.call("cvx_remote_mouse", sid, ev, p.x, p.y).catch(function () {});
    }
    screen.addEventListener("mousemove", function (e) {
      if (!sid) return;
      var now = Date.now();
      if (now - lastMove < 45) return;    /* throttle: ~22 moves/s */
      lastMove = now;
      sendMouse(EV_MOVE, toScreen(e));
    });
    screen.addEventListener("mousedown", function (e) {
      if (!sid) return;
      e.preventDefault();
      var p = toScreen(e);
      sendMouse(EV_MOVE, p);              /* position the cursor, then press */
      if (e.button === 2) { downBtn = 2; sendMouse(EV_RDOWN, p); }
      else { downBtn = 0; sendMouse(EV_LDOWN, p); }
    });
    window.addEventListener("mouseup", function (e) {
      if (!sid || downBtn === null) return;
      var p = toScreen(e);
      if (downBtn === 2) sendMouse(EV_RUP, p); else sendMouse(EV_LUP, p);
      downBtn = null;
    });
    screen.addEventListener("contextmenu", function (e) { e.preventDefault(); });

    /* --- connect + stream ----------------------------------------------- */
    img.addEventListener("load", function () { hint.style.display = "none"; });

    BV.api.call("cvx_remote_start", { ip: ip }).then(function (r) {
      sid = r.session_id;
      if (r.screen) { SCREEN_W = r.screen.w; SCREEN_H = r.screen.h; fit(); }
      img.src = r.stream_url;
      statusTimer = setInterval(pollStatus, 1000);
    }).catch(function (e) {
      hint.textContent = "";
      status.textContent = "connection failed";
      status.classList.add("err");
      screen.appendChild(BV.el("div", { class: "cvx-error" },
        '<div class="big">could not connect</div><div class="hint">' + BV.esc(e.message) +
        "</div><div class=\"hint\">the camera may be off, or the Terminal / an operator is already on it.</div>"));
    });

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
