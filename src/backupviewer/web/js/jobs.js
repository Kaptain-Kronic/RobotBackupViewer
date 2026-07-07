/* jobs.js - route-independent backup-job watching + the global progress strip.

   Jobs run server-side on daemon threads; this module only WATCHES: one shared
   500ms poller (one list_backup_jobs bridge call per tick, however many jobs),
   a "jobs" event other screens subscribe to for their own painting (#home's
   per-row bars), and the #jobstrip footer strip that stays visible on EVERY
   screen — leaving the library no longer hides a running backup. Also hosts
   BV.jobs.busy()/done(), the indeterminate "working…" indicator api.js raises
   around slow synchronous calls. Seeds itself from the backend at boot, so a
   reloaded page re-discovers jobs it never started. */
(function () {
  "use strict";

  var tracked = {};       /* jobId -> {robotId} local metadata (which library row) */
  var last = {};          /* jobId -> latest snapshot */
  var seenTerminal = {};  /* jobId -> true once its terminal state was announced */
  var timer = null;
  var busyCount = 0;
  var busyLabel = "";

  function isTerminal(p) {
    return !p || p.status === "done" || p.status === "error" || p.status === "cancelled";
  }
  function activeIds() {
    return Object.keys(last).filter(function (id) { return !isTerminal(last[id]); });
  }

  BV.jobs = {
    /* remember which library row a just-started job belongs to, and watch it */
    track: function (jobId, meta) {
      tracked[jobId] = meta || {};
      if (!last[jobId]) last[jobId] = { id: jobId, status: "pending", total: 0, done: 0 };
      ensureTimer();
      render();
    },
    meta: function (jobId) { return tracked[jobId] || {}; },
    latest: function () { return last; },
    activeCount: function () { return activeIds().length; },
    cancelAll: function () {
      activeIds().forEach(function (id) {
        BV.api.call("cancel_backup", id).catch(function () {});
      });
      BV.toast("cancelling backups…");
    },
    busy: function (label) { busyCount++; if (label) busyLabel = label; render(); },
    done: function () {
      busyCount = Math.max(0, busyCount - 1);
      if (!busyCount) busyLabel = "";
      render();
    },
  };

  /* boot seed: re-discover jobs after a reload (in-flight ones resume in the
     strip; already-finished ones are old news, no toast) */
  BV.api.ready.then(function (ok) {
    if (!ok) return;
    BV.api.call("list_backup_jobs").then(function (res) {
      (res.jobs || []).forEach(function (p) {
        last[p.id] = p;
        if (isTerminal(p)) seenTerminal[p.id] = true;
      });
      if (activeIds().length) ensureTimer();
      render();
    }).catch(function () {});
  });

  function ensureTimer() {
    if (!timer) timer = setInterval(tick, 500);
  }

  function tick() {
    if (!activeIds().length) {
      clearInterval(timer);
      timer = null;
      render();
      return;
    }
    BV.api.call("list_backup_jobs").then(function (res) {
      var wasActive = activeIds().length > 0;
      (res.jobs || []).forEach(function (p) { last[p.id] = p; });
      var newlyDone = [];
      Object.keys(last).forEach(function (id) {
        if (isTerminal(last[id]) && !seenTerminal[id]) {
          seenTerminal[id] = true;
          newlyDone.push(id);
        }
      });
      BV.state.emit("jobs", { jobs: last, newlyDone: newlyDone });
      if (wasActive && !activeIds().length) BV.toast("backups finished");
      render();
    }).catch(function () {});
  }

  /* ---- the strip (footer row above #statusbar; router never touches it) ---- */

  function jobLabel(p) {
    return (p.robot || p.host || "backup") + " · " +
      (p.total ? (p.done || 0) + "/" + p.total : (p.status || ""));
  }

  function render() {
    var el = document.getElementById("jobstrip");
    if (!el) return;
    var act = activeIds();
    if (!act.length && !busyCount) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    el.innerHTML = "";
    el.classList.remove("hidden");

    if (act.length) {
      var done = 0, total = 0;
      act.forEach(function (id) { done += last[id].done || 0; total += last[id].total || 0; });
      var pct = total ? Math.round(100 * done / total) : 8;
      var bar = BV.el("div", { class: "membar" },
        '<div class="mb-label"><span>backing up ' + act.length +
        " robot" + (act.length === 1 ? "" : "s") + "</span><span>" +
        (total ? done + " / " + total + " files" : "starting…") + "</span></div>" +
        '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div>');
      el.appendChild(bar);
      var details = BV.el("button", { class: "btn", title: "per-robot progress" }, "details ▾");
      details.addEventListener("click", function () {
        BV.menu(details, activeIds().map(function (id) {
          var p = last[id];
          return { label: jobLabel(p) + "  ·  ✕ cancel", onClick: function () {
            BV.api.call("cancel_backup", id).catch(function () {});
          } };
        }));
      });
      el.appendChild(details);
      var cancel = BV.el("button", { class: "btn jobstrip-cancel" }, "cancel all");
      cancel.addEventListener("click", BV.jobs.cancelAll);
      el.appendChild(cancel);
    }

    if (busyCount) {
      var busy = BV.el("div", { class: "jobstrip-busy" },
        '<span class="busy-pulse"></span>' + BV.esc(busyLabel || "working…"));
      el.appendChild(busy);
    }
  }
})();
