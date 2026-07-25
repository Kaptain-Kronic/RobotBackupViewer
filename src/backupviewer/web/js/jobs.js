/* jobs.js - route-independent backup-job watching + the global progress strip.

   Jobs run server-side on daemon threads; this module only WATCHES: one shared
   500ms poller (one list_backup_jobs bridge call per tick, however many jobs),
   a "jobs" event other screens subscribe to for their own painting (#home's
   per-row bars), and the #jobstrip footer strip that stays visible on EVERY
   screen — leaving the library no longer hides a running backup. Also hosts
   BV.jobs.busy()/done(), the indeterminate "working…" indicator api.js raises
   around slow synchronous calls. Seeds itself from the backend at boot, so a
   reloaded page re-discovers jobs it never started.

   The strip measures the whole RUN: every job sharing a run_id with one still
   going — the same grouping the durable backup log keeps server-side, retries
   included. Finished robots stay in the denominator so the bar only climbs
   (it used to sum the still-active jobs only, and walked backwards every time
   one landed). The strip's DOM is built once and updated in place — the old
   rebuild-every-tick made the details button a fresh element twice a second,
   which is why clicking it misfired. The details panel is part of the strip,
   not a snapshot menu: its rows repaint from the latest snapshots every tick
   it is open. */
(function () {
  "use strict";

  var tracked = {};       /* jobId -> {robotId} local metadata (which library row) */
  var last = {};          /* jobId -> latest snapshot */
  var seenTerminal = {};  /* jobId -> true once its terminal state was announced */
  var timer = null;
  var busyCount = 0;
  var busyLabel = "";
  var detailsOpen = false;
  var dom = null;         /* the strip's persistent skeleton (built on demand) */

  function isTerminal(p) {
    return !p || p.status === "done" || p.status === "error" || p.status === "cancelled";
  }
  function activeIds() {
    return Object.keys(last).filter(function (id) { return !isTerminal(last[id]); });
  }

  /* the current RUN: active jobs plus the settled jobs of the same run_id(s).
     Derived fresh from the snapshots each time, so it survives page reloads
     and always agrees with the server's own run grouping. */
  function runIds() {
    var liveRuns = {};
    var ids = Object.keys(last);
    ids.forEach(function (id) {
      var p = last[id];
      if (!isTerminal(p) && p.run_id) liveRuns[p.run_id] = true;
    });
    return ids.filter(function (id) {
      var p = last[id];
      return !isTerminal(p) || (p.run_id && liveRuns[p.run_id]);
    });
  }

  BV.jobs = {
    /* the one home of the job-status vocabulary — a future status (say
       "timeout") lands here once instead of half-landing in screen copies */
    isTerminal: isTerminal,
    statusText: function (p) {
      return {
        connecting: "connecting…", listing: "listing files…", downloading: "downloading…",
        done: "done", error: "failed", cancelled: "cancelled", pending: "starting…",
      }[p.status] || p.status || "";
    },
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
    /* who is being pulled RIGHT NOW — {ids:{robotId:true}, hosts:{ip:true}}.
       Screens ask isRobotActive() so they can say "backing up" instead of
       piling partial/stale pills onto a snapshot that is half-written by
       design while the pull runs. */
    activeTargets: function () {
      var out = { ids: {}, hosts: {} };
      activeIds().forEach(function (id) {
        var m = tracked[id] || {};
        if (m.robotId) out.ids[m.robotId] = true;
        var host = (last[id] || {}).host;
        if (host) out.hosts[host] = true;
      });
      return out;
    },
    /* r is a library robot ({id, ips}); pass activeTargets() when testing many
       rows so the sets are built once per paint, not once per row */
    isRobotActive: function (r, targets) {
      if (!r) return false;
      var t = targets || BV.jobs.activeTargets();
      if (r.id && t.ids[r.id]) return true;
      return (r.ips || []).some(function (ip) { return !!t.hosts[ip]; });
    },
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
     strip; already-finished ones are old news, no toast). Solo pop-outs skip
     it entirely - jobs are main-window chrome, and a second 500ms poller
     against the same server would just double the traffic. */
  BV.api.ready.then(function (ok) {
    if (!ok || BV.solo) return;
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

  function ensureDom(el) {
    if (dom && dom.root === el && el.firstChild) return dom;
    el.innerHTML = "";
    var run = BV.el("div", { class: "jobstrip-run" });
    var bar = BV.el("div", { class: "membar" },
      '<div class="mb-label"><span class="js-lab-l"></span><span class="js-lab-r"></span></div>' +
      '<div class="mb-track"><div class="mb-fill"></div></div>');
    var details = BV.el("button", { class: "btn jobstrip-details",
      title: "per-robot progress (live)" }, "details ▴");
    details.addEventListener("click", function () {
      detailsOpen = !detailsOpen;
      render();
    });
    var cancel = BV.el("button", { class: "btn jobstrip-cancel" }, "cancel all");
    cancel.addEventListener("click", BV.jobs.cancelAll);
    run.appendChild(bar);
    run.appendChild(details);
    run.appendChild(cancel);
    var busy = BV.el("div", { class: "jobstrip-busy hidden" },
      '<span class="busy-pulse"></span><span class="jobstrip-busy-label"></span>');
    var panel = BV.el("div", { class: "jobstrip-panel hidden" });
    panel.addEventListener("click", function (e) {
      var b = e.target && e.target.closest ? e.target.closest("[data-cancel]") : null;
      if (!b || !panel.contains(b)) return;
      BV.api.call("cancel_backup", b.getAttribute("data-cancel")).catch(function () {});
    });
    el.appendChild(run);
    el.appendChild(busy);
    el.appendChild(panel);
    dom = {
      root: el, run: run, panel: panel, details: details, busy: busy,
      labL: bar.querySelector(".js-lab-l"),
      labR: bar.querySelector(".js-lab-r"),
      fill: bar.querySelector(".mb-fill"),
      busyLab: busy.querySelector(".jobstrip-busy-label"),
    };
    return dom;
  }

  function render() {
    var el = document.getElementById("jobstrip");
    if (!el) return;
    var act = activeIds();
    if (!act.length && !busyCount) {
      dom = null;
      detailsOpen = false;
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    var d = ensureDom(el);
    el.classList.remove("hidden");
    d.run.classList.toggle("hidden", !act.length);
    d.panel.classList.toggle("hidden", !act.length || !detailsOpen);

    if (act.length) {
      var run = runIds();
      var frac = 0, resolved = 0, failed = 0;
      run.forEach(function (id) {
        var p = last[id];
        if (isTerminal(p)) {
          frac += 1;
          resolved++;
          if (p.status === "error") failed++;
        } else if (p.total) {
          frac += (p.done || 0) / p.total;
        }
      });
      var pct = run.length ? Math.round(100 * frac / run.length) : 0;
      if (pct < 2) pct = 2;   /* a sliver of life while everyone is still listing */
      d.fill.style.width = pct + "%";
      if (run.length === 1) {
        var p1 = last[run[0]];
        d.labL.textContent = "backing up " + (p1.robot || p1.host || "1 robot");
        d.labR.textContent = p1.total
          ? (p1.done || 0) + " / " + p1.total + " files"
          : BV.jobs.statusText(p1);
      } else {
        d.labL.textContent = "backing up " + act.length + " robot" + (act.length === 1 ? "" : "s");
        d.labR.textContent = resolved + " / " + run.length + " finished" +
          (failed ? " · " + failed + " failed" : "");
      }
      d.details.textContent = detailsOpen ? "details ▾" : "details ▴";
      if (detailsOpen) renderPanel(d.panel, run);
    }

    d.busy.classList.toggle("hidden", !busyCount);
    if (busyCount) d.busyLab.textContent = busyLabel || "working…";
  }

  /* the live per-robot list: rebuilt from the latest snapshots every tick it
     is open (scroll preserved) — active first (that is what you watch), then
     failed, cancelled, done. */
  function renderPanel(panel, run) {
    var groups = { active: [], error: [], cancelled: [], done: [] };
    run.forEach(function (id) {
      var p = last[id];
      var g = isTerminal(p) ? (groups[p.status] ? p.status : "error") : "active";
      groups[g].push(p);
    });
    var html = "";
    groups.active.forEach(function (p) {
      html += panelRow(p, "↓", "",
        p.total ? (p.done || 0) + " / " + p.total : BV.jobs.statusText(p), true);
    });
    groups.error.forEach(function (p) {
      html += panelRow(p, "✗", "err", p.error || "failed", false);
    });
    groups.cancelled.forEach(function (p) {
      html += panelRow(p, "–", "dim", "cancelled", false);
    });
    /* a job can finish "done" and still have lost files — a camera station
       keeps going when one lens fails, and the pull skips what it cannot
       read. That is a partial, and it must not wear the same clean ✓ as a
       whole backup: flag it, the tech decides. */
    groups.done.forEach(function (p) {
      var lost = (p.error || "") ||
        ((p.skipped && p.skipped.length) ? p.skipped.length + " skipped" : "");
      if (lost) {
        html += panelRow(p, "!", "warn",
          (p.done || 0) + " files · " + lost, false);
      } else {
        html += panelRow(p, "✓", "ok", (p.done || 0) + " files", false);
      }
    });
    var keep = panel.scrollTop;
    panel.innerHTML = html;
    panel.scrollTop = keep;
  }

  function panelRow(p, mark, cls, detail, cancellable) {
    return '<div class="js-row' + (cls ? " " + cls : "") + '">' +
      '<span class="js-mark">' + mark + "</span>" +
      '<span class="js-robot">' + BV.esc(p.robot || p.host || "backup") + "</span>" +
      (p.line ? '<span class="js-line">' + BV.esc(p.line) + "</span>" : "") +
      '<span class="js-detail">' + BV.esc(detail || "") + "</span>" +
      (cancellable
        ? '<button class="btn js-cancel" data-cancel="' + BV.esc(p.id) +
          '" title="cancel this backup">✕</button>'
        : "") +
      "</div>";
  }
})();
