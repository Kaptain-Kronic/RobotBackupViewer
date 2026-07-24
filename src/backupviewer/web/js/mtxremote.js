/* mtxremote.js - Matrox camera live remote (its own web UI, in-app).

   BV.openMtxRemote(ip, label) probes http://<ip>/ via the bridge and embeds the
   camera's web pages in a fullscreen-capable overlay (same chrome as the CV-X
   remote) with TABS: the portal home plus every DesignAssistant operator page
   the backend scraped off it - the pages the portal would otherwise pop into
   the default browser. Each tab is its own iframe, kept alive across switches.

   If the home page refuses embedding (X-Frame-Options / CSP), it opens in a
   separate app window instead. The sandbox attr blocks legacy frame-busting
   scripts from hijacking the app window (no allow-top-navigation); when the
   operator pages are captured as tabs, the home tab's popups are suppressed
   too (no allow-popups), so nothing escapes to the default browser. */
(function () {
  "use strict";

  var open = false;

  /* the portal appends a random ?pgx= cache-buster when it launches a
     DesignAssistant page - do the same so every load is fresh */
  function daUrl(url) {
    if (!/designassistant/i.test(url)) return url;
    return url + (url.indexOf("?") < 0 ? "?" : "&") + "pgx=" + Math.random();
  }

  BV.openMtxRemote = function (ip, label) {
    if (open) { BV.toast("a remote session is already open"); return; }
    ip = (ip || "").trim();
    if (!ip) { BV.toast("this camera has no IP on record"); return; }
    open = true;
    /* per-invocation flag: `open` only gates "one overlay at a time" - a slow
       probe that resolves after THIS overlay closed (and another opened) must
       check its OWN teardown, not the shared flag, or it dead-ends the newer one */
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
        "at the lens" }, "📱 phone");
    var fsBtn = BV.el("button", { class: "btn", title: "fullscreen" }, "fullscreen");
    var closeBtn = BV.el("button", { class: "btn", title: "close (esc)" }, "✕ close");
    bar.appendChild(title); bar.appendChild(tabStrip); bar.appendChild(status);
    bar.appendChild(spacer);
    bar.appendChild(rlBtn); bar.appendChild(winBtn); bar.appendChild(phBtn);
    bar.appendChild(fsBtn);
    bar.appendChild(closeBtn);

    var stage = BV.el("div", { class: "cvx-stage" });
    var screen = BV.el("div", { class: "cvx-screen web" });
    var hint = BV.el("div", { class: "cvx-hint" }, "loading the camera's page…");
    screen.appendChild(hint);
    stage.appendChild(screen);
    overlay.appendChild(bar); overlay.appendChild(stage);
    document.body.appendChild(overlay);

    var tabs = [];      /* {label, url, home, frame, btn} */
    var current = -1;

    function close() {
      if (closed) return;
      closed = true;
      open = false;
      document.removeEventListener("keydown", onKey);
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      overlay.remove();
    }
    closeBtn.addEventListener("click", close);

    function onKey(e) {
      if (e.key === "Escape" && !document.fullscreenElement) { e.preventDefault(); close(); }
    }
    document.addEventListener("keydown", onKey);

    fsBtn.addEventListener("click", function () {
      if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
      else { try { overlay.requestFullscreen(); } catch (e) {} }
    });
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

    /* popups are only allowed on the home tab when no operator pages were
       captured (then a browser popup beats a dead link); with tabs in place
       everything stays in-app */
    function sandboxFor(t, popupsOk) {
      var sb = "allow-scripts allow-forms allow-same-origin allow-modals allow-downloads";
      if (!t.home || popupsOk) sb += " allow-popups";
      return sb;
    }

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
