/* mtxremote.js - Matrox camera live remote (its own web UI, in-app).

   BV.openMtxRemote(ip, label) probes http://<ip>/ via the bridge and embeds the
   camera's web pages in a fullscreen-capable panel (same chrome as the CV-X
   remote) with TABS: the portal home plus every DesignAssistant operator page
   the backend scraped off it - the pages the portal would otherwise pop into
   the default browser. Each tab is its own iframe, kept alive across switches.

   Like the CV-X remote, the main window parks it on a SESSION-BAR CHIP
   (BV.remotes, backuptabs.js): the panel sits under the top chrome, esc or
   any navigation hides it (iframes stay warm), the chip's ✕ closes it. Solo
   pop-outs have no strip, so the remote stays a full takeover there.

   If the home page refuses embedding (X-Frame-Options / CSP), it opens in a
   separate app window instead. The sandbox attr blocks legacy frame-busting
   scripts from hijacking the app window (no allow-top-navigation); when the
   operator pages are captured as tabs, the home tab's popups are suppressed
   too (no allow-popups), so nothing escapes to the default browser. */
(function () {
  "use strict";

  /* the portal appends a random ?pgx= cache-buster when it launches a
     DesignAssistant page - do the same so every load is fresh */
  function daUrl(url) {
    if (!/designassistant/i.test(url)) return url;
    return url + (url.indexOf("?") < 0 ? "?" : "&") + "pgx=" + Math.random();
  }

  /* The sandbox a matrox page gets. Popups are only allowed on the HOME tab
     when no operator pages were captured (then a browser popup beats a dead
     link); with pages in place everything stays in-app. No allow-top-navigation
     ever - a legacy frame-buster must not be able to hijack the app window. */
  function sandboxFor(t, popupsOk) {
    var sb = "allow-scripts allow-forms allow-same-origin allow-modals allow-downloads";
    if (!t.home || popupsOk) sb += " allow-popups";
    return sb;
  }

  /* Shared with the floating boxes (camfloat.js), which drive a matrox the
     only way a matrox can be driven: its own web page. Kept here because this
     file owns the matrox remote - a second implementation of the sandbox rule
     is exactly the kind of copy that drifts into a security hole. */
  BV.mtx = {
    /* the pages this camera serves: {url, embeddable, pages[]} */
    pages: function (ip) { return BV.api.call("mtx_remote_start", { ip: ip }); },
    /* which page a tech actually works in: the operator page when there is
       exactly one, else home */
    pick: function (r) {
      var pages = (r && r.pages) || [];
      return pages.length === 1
        ? { label: pages[0].label, url: pages[0].url, home: false, only: true }
        : { label: "home", url: r.url, home: true, only: pages.length === 0 };
    },
    frame: function (page) {
      var f = BV.el("iframe", { title: "MTX camera web UI",
                                sandbox: sandboxFor(page, !!page.only && page.home) });
      f.src = daUrl(page.url);
      return f;
    },
  };

  BV.openMtxRemote = function (ip, label) {
    ip = (ip || "").trim();
    if (!ip) { BV.toast("this camera has no IP on record"); return; }
    /* solo pop-outs hide the session bar, so a chip there could never be
       clicked - the remote stays a takeover in those windows */
    var chipless = !!BV.solo;
    var rkey = "mtx:" + ip;
    /* re-opening a camera that already has a chip brings its view back up */
    if (!chipless && BV.remotes.focus(rkey)) return;
    /* ...and a camera already up in a FLOATING box is the same story: point at
       that box rather than opening a second view of one camera */
    if (BV.camFloats && BV.camFloats.focusIp(ip)) return;
    /* per-invocation flag: a slow probe that resolves after THIS panel closed
       (and another opened) must check its OWN teardown, or it dead-ends the
       newer one */
    var closed = false;

    var overlay = BV.el("div", { class: "cvx-remote" });
    var bar = BV.el("div", { class: "cvx-bar" });
    var title = BV.el("span", { class: "cvx-title" },
      "MTX remote · " + BV.esc(label || ip));
    var tabStrip = BV.el("div", { class: "cvx-tabs" });
    var status = BV.el("span", { class: "cvx-status" }, "connecting…");
    var spacer = BV.el("span", { style: "margin-left:auto" });
    var rlBtn = BV.el("button", { class: "btn", title: "reload this tab" }, "⟳ reload");
    var winBtn = BV.el("button", { class: "btn", title: "open this tab in a separate window" }, "open in window");
    var phBtn = BV.el("button", { class: "btn",
      title: "mirror this window to your phone (QR) — watch the live image " +
        "at the lens" }, BV.icon("phone") + " phone");
    var zoomBtn = BV.el("button", { class: "btn cvx-zoom",
      title: "view zoom — ctrl+= / ctrl+- (or ctrl+scroll over this bar), " +
        "ctrl+0 resets" }, "100%");
    var fsBtn = BV.el("button", { class: "btn", title: "fullscreen" }, "fullscreen");
    var closeBtn = BV.el("button", { class: "btn", title: chipless
      ? "close (esc)"
      : "close this remote — esc only hides it" }, "✕ close");
    bar.appendChild(title); bar.appendChild(tabStrip); bar.appendChild(status);
    bar.appendChild(spacer);
    bar.appendChild(rlBtn); bar.appendChild(winBtn); bar.appendChild(phBtn);
    bar.appendChild(zoomBtn);
    bar.appendChild(fsBtn);
    bar.appendChild(closeBtn);

    var stage = BV.el("div", { class: "cvx-stage" });
    var screen = BV.el("div", { class: "cvx-screen web" });
    var hint = BV.el("div", { class: "cvx-hint" }, "loading the camera's page…");
    screen.appendChild(hint);
    stage.appendChild(screen);
    overlay.appendChild(bar); overlay.appendChild(stage);
    document.body.appendChild(overlay);

    /* --- chip lifecycle (same shape as the CV-X remote) ------------------ */
    function place() {
      if (chipless) return;               /* the whole window is the remote */
      /* below the TOPBAR (navigation + chips stay reachable), over the
         toolbar row - that row is context for the screen this panel covers.
         BV.chromeInset is the shared measurement (the float layer uses it
         too); it handles fullscreen and a missing slab. */
      var ci = BV.chromeInset();
      overlay.style.top = ci.top + "px";
      overlay.style.bottom = ci.bottom + "px";
    }
    function show() {
      overlay.style.display = "";
      document.addEventListener("keydown", onKey);
      place();
    }
    function hide() {
      document.removeEventListener("keydown", onKey);
      BV.fullscreen.exit();               /* never park a borderless window */
      overlay.style.display = "none";
    }
    window.addEventListener("resize", place);
    if (!chipless) {
      BV.remotes.add({ key: rkey, kind: "mtx", label: label || ip,
        ctl: { show: show, hide: hide, destroy: close } });
    }
    show();

    var tabs = [];      /* {label, url, home, frame, btn} */
    var current = -1;

    /* view zoom, one factor for every tab. The iframes are cross-origin, so
       a real page zoom is out of reach - instead each frame is scaled up and
       inverse-SIZED (width 100%/z at scale z), which reads exactly like
       browser zoom: bigger page, the iframe's own scrollbars take up the
       slack, and below 1 MORE of an oversized operator page fits. The frames
       also swallow wheel/key events while focused (cross-origin again), so
       the reliable paths are the % button and ctrl+= / ctrl+- with the app
       chrome focused; ctrl+scroll works over the bar. Nothing here reaches
       the camera. Lives with this overlay: a fresh open starts at 100%. */
    var zoom = 1;
    function applyZoom(f) {
      f.style.transform = zoom === 1 ? "" : "scale(" + zoom + ")";
      f.style.transformOrigin = "0 0";
      f.style.width = (100 / zoom) + "%";
      f.style.height = (100 / zoom) + "%";
    }
    function setZoom(z) {
      z = Math.round(Math.max(0.5, Math.min(3, z)) * 100) / 100;
      if (z === zoom) return;
      zoom = z;
      tabs.forEach(function (t) { if (t.frame) applyZoom(t.frame); });
      zoomBtn.textContent = Math.round(zoom * 100) + "%";
    }
    zoomBtn.addEventListener("click", function () {
      BV.menu(zoomBtn, [50, 75, 100, 150, 200, 300].map(function (p) {
        return { label: p + "%", active: Math.round(zoom * 100) === p,
                 onClick: function () { setZoom(p / 100); } };
      }));
    });
    overlay.addEventListener("wheel", function (e) {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoom(zoom * (e.deltaY < 0 ? 1.25 : 0.8));
    }, { passive: false });

    function close() {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      BV.fullscreen.exit();               /* never leave a borderless window behind */
      overlay.remove();
      if (!chipless) BV.remotes.drop(rkey);
    }
    closeBtn.addEventListener("click", close);

    /* attached while the panel shows (show/hide) - a parked remote must not
       eat keystrokes; nav keys pass through, any route parks the remote */
    function onKey(e) {
      if (e.ctrlKey && (e.key === "=" || e.key === "+")) {
        e.preventDefault(); setZoom(zoom * 1.25); return;
      }
      if (e.ctrlKey && e.key === "-") { e.preventDefault(); setZoom(zoom * 0.8); return; }
      if (e.ctrlKey && e.key === "0") { e.preventDefault(); setZoom(1); return; }
      if (e.key !== "Escape") return;
      e.preventDefault();
      /* esc backs out one step at a time: window, then the remote - which on
         a chip means HIDE (the pages stay loaded); solo still closes */
      if (BV.fullscreen.active()) BV.fullscreen.exit();
      else if (chipless) close();
      else BV.remotes.hideVisible();
    }

    fsBtn.addEventListener("click", function () { BV.fullscreen.toggle(); });
    rlBtn.addEventListener("click", function () {
      var t = tabs[current];
      if (t && t.frame) { hint.style.display = ""; t.frame.src = daUrl(t.url); }
    });
    winBtn.addEventListener("click", function () {
      var t = tabs[current];
      if (!t) return;
      BV.api.call("mtx_remote_window",
        { ip: ip, label: label || "", url: daUrl(t.url) }).then(function () {
        BV.toast("opened in its own window");
      }).catch(function (e) { BV.toast("could not open window: " + e.message); });
    });
    phBtn.addEventListener("click", function () { BV.openViewfinder(); });

    function select(i) {
      if (!tabs[i]) return;
      current = i;
      tabs.forEach(function (t, j) {
        if (t.btn) t.btn.classList.toggle("on", j === i);
        if (t.frame) t.frame.style.display = j === i ? "block" : "none";
      });
      var t = tabs[i];
      if (!t.frame) {
        hint.style.display = "";
        t.frame = BV.el("iframe", {
          title: "MTX camera web UI",
          sandbox: t.sandbox,
        });
        t.frame.addEventListener("load", function () {
          if (tabs[current] === t) hint.style.display = "none";
        });
        applyZoom(t.frame);   /* a tab opened at 150% joins at 150% */
        t.frame.src = daUrl(t.url);
        screen.appendChild(t.frame);
      } else {
        hint.style.display = "none";
      }
      status.textContent = "live · " + t.url.replace(/^https?:\/\//, "").split("?")[0];
      status.classList.remove("err");
    }

    BV.api.call("mtx_remote_start", { ip: ip }).then(function (r) {
      if (closed) return;
      if (!r.embeddable) {
        /* the page refuses framing - hand it its own window instead */
        close();
        BV.toast("this camera's page can't be embedded — opening it in a window");
        return BV.api.call("mtx_remote_window", { ip: ip, label: label || "" });
      }
      var pages = r.pages || [];
      tabs = [{ label: "home", url: r.url, home: true }].concat(pages.map(function (p) {
        return { label: p.label, url: p.url, home: false };
      }));
      tabs.forEach(function (t, i) {
        t.sandbox = sandboxFor(t, pages.length === 0);
        t.btn = BV.el("button", { class: "cvx-tab", title: t.url },
          BV.esc(t.label));
        t.btn.addEventListener("click", function () { select(i); });
        tabStrip.appendChild(t.btn);
      });
      /* land on the operator page when there is exactly one - it's what the
         tech actually works in; home stays one click away */
      select(tabs.length === 2 ? 1 : 0);
    }).catch(function (e) {
      if (closed) return;
      hint.style.display = "none";
      status.textContent = "connection failed";
      status.classList.add("err");
      screen.appendChild(BV.el("div", { class: "cvx-error" },
        '<div class="big">could not connect</div><div class="hint">' + BV.esc(e.message) +
        "</div>"));
    });
  };
})();
