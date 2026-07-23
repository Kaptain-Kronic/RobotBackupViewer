/* update.js - the release check's UI half (BV.update).
   Boot path: T+3s after the bridge is ready it asks python check_update(true);
   python's policy makes that a network no-op outside the packaged exe, so dev
   runs and hidden-window probes never phone home. A found newer release is a
   quiet toast + a persistent "update: vX" pill in the statusbar (the pill html
   is rendered by router.js's rightStatusHtml from BV.state.updateInfo). The
   about box gets an updates row whose "check for updates" button always really
   checks, wherever it runs. "skip this version" persists via set_setting and
   only silences the boot reminder - a manual check still tells the truth. */
(function () {
  "use strict";

  function remember(data) {
    BV.state.updateInfo = data || null;
    BV.state.emit("update", BV.state.updateInfo);
  }

  function skipped(d) {
    var s = BV.state.settings || {};
    return !!(d && d.latest && s.update_skip === d.latest);
  }

  BV.update = {
    /* statusbar pill: only for a real, un-skipped newer release */
    pillHtml: function () {
      var d = BV.state.updateInfo;
      if (!d || d.status !== "update" || skipped(d)) return "";
      return '<span class="pill ok-soft update-pill" ' +
        'title="a newer release is on github — click for details">update: ' +
        BV.esc(d.latest) + "</span> ";
    },

    autocheck: function () {
      setTimeout(function () {
        BV.api.call("check_update", true).then(function (d) {
          if (!d || d.status === "skipped") return;
          remember(d);
          if (d.status === "update" && !skipped(d)) {
            BV.toast("update: " + d.latest + " is on github", 3200);
          }
        }).catch(function () { /* a background check never bothers anyone */ });
      }, 3000);
    },

    /* the about box's updates row: current state + a manual check */
    aboutSection: function () {
      var wrap = BV.el("div", { class: "about-upd" });
      var line = BV.el("div", { class: "about-line dim" }, "");
      var acts = BV.el("div", { class: "lf-actions" });
      var checkBtn = BV.el("button",
        { class: "btn", title: "ask github for the newest release" },
        "check for updates");
      var getBtn = BV.el("button",
        { class: "btn primary", title: "open the release page in your browser" }, "");
      var skipBtn = BV.el("button",
        { class: "btn", title: "stop the boot reminder for this one version" },
        "skip this version");

      function render(d, busy) {
        getBtn.style.display = "none";
        skipBtn.style.display = "none";
        if (busy) { line.innerHTML = "checking github…"; return; }
        if (!d || d.status === "skipped") {
          line.innerHTML = "updates: not checked yet";
          return;
        }
        var cur = BV.esc(d.current || "");
        var latest = BV.esc(d.latest || "");
        if (d.status === "update") {
          line.innerHTML = "you're on " + cur + ' · <span class="accent">' +
            latest + "</span> is on github" +
            (skipped(d) ? " (skipped — no boot reminders)" : "");
          getBtn.textContent = "get " + (d.latest || "");
          getBtn.style.display = "";
          if (!skipped(d)) skipBtn.style.display = "";
        } else if (d.status === "current") {
          line.innerHTML = "up to date — " + cur;
        } else if (d.status === "ahead") {
          line.innerHTML = "this build (" + cur +
            ") is ahead of the newest release (" + latest + ")";
        } else if (d.status === "norelease") {
          line.innerHTML = "no releases published on github yet";
        } else if (d.status === "offline") {
          line.innerHTML = "couldn't reach github — offline?";
        } else if (d.status === "unknown") {
          line.innerHTML = "newest on github: " + latest +
            " — version tags aren't comparable";
        } else {
          line.innerHTML = "check failed" +
            (d.detail ? " — " + BV.esc(d.detail) : "");
        }
      }

      checkBtn.addEventListener("click", function () {
        checkBtn.disabled = true;
        render(null, true);
        BV.api.call("check_update").then(function (d) {
          checkBtn.disabled = false;
          remember(d);
          render(d, false);
        }, function (e) {
          checkBtn.disabled = false;
          render({ status: "error", detail: e && e.message }, false);
        });
      });

      getBtn.addEventListener("click", function () {
        var d = BV.state.updateInfo;
        if (!d || !d.url) return;
        BV.api.call("open_url", d.url).catch(function () {
          BV.copyText(d.url, "link copied");
        });
      });

      skipBtn.addEventListener("click", function () {
        var d = BV.state.updateInfo;
        if (!d || !d.latest) return;
        BV.api.call("set_setting", "update_skip", d.latest).catch(function () {});
        (BV.state.settings = BV.state.settings || {}).update_skip = d.latest;
        BV.state.emit("update", d);   /* statusbar drops the pill */
        render(d, false);
      });

      acts.appendChild(checkBtn);
      acts.appendChild(getBtn);
      acts.appendChild(skipBtn);
      wrap.appendChild(line);
      wrap.appendChild(acts);
      render(BV.state.updateInfo, false);
      return wrap;
    },
  };

  BV.api.ready.then(function (ok) { if (ok) BV.update.autocheck(); });
})();
