/* jobs.js - route-independent job watching + the global progress strip.

   Jobs run server-side on daemon threads; this module only WATCHES: one shared
   500ms poller (one list_backup_jobs bridge call per tick, plus one
   list_scan_jobs while a scan is live), a "jobs" event other screens subscribe
   to for their own painting (#home's per-row bars), a "scan-jobs" event the
   scan windows' detached-finish watchers listen on, and the #jobstrip footer
   strip that stays visible on EVERY screen — leaving the library no longer
   hides a running backup, and closing the scan window no longer cancels the
   scan: the strip carries it, its ✕ is the only cancel left, and its "open"
   re-attaches the window (scans used to die with their modal — minutes of
   fleet scan lost to a stray Esc). Also hosts BV.jobs.busy()/done(), the
   indeterminate "working…" indicator api.js raises around slow synchronous
   calls. Seeds itself from the backend at boot, so a reloaded page
   re-discovers jobs it never started.

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
  var scans = {};         /* scan jobId -> latest LIGHT snapshot (no results) */
  var scanSeen = {};      /* scan jobId -> true once its terminal state was announced */
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
  function activeScanIds() {
    return Object.keys(scans).filter(function (id) { return !isTerminal(scans[id]); });
  }
  /* fallback row copy for the tick between trackScan() and the first poll —
     the server's own `label` (which carries the cidr) wins once it arrives */
  var KIND_LABEL = { health: "fleet scan", network: "network sweep" };
  function scanLabel(p) {
    return p.label || KIND_LABEL[p.kind] || "scan";
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
        scanning: "scanning…",
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
    /* watch a just-started scan job (health scan / network sweep). kind fills
       the row label until the first poll returns the server's own. */
    trackScan: function (jobId, kind) {
      if (!scans[jobId]) {
        scans[jobId] = { id: jobId, kind: kind || "", status: "pending",
                         total: 0, scanned: 0, found: 0 };
      }
      ensureTimer();
      render();
    },
    /* the scan windows' attach path: the live job of my kind, else the newest
       one either way (a finished sweep still holds its results server-side) */
    scanLatest: function () { return scans; },
    activeScan: function (kind) {
      var best = null;
      activeScanIds().forEach(function (id) {
        var p = scans[id];
        if (p.kind !== kind) return;
        if (!best || (p.started || "") > (best.started || "")) best = p;
      });
      return best;
    },
    newestScan: function (kind) {
      var best = null;
      Object.keys(scans).forEach(function (id) {
        var p = scans[id];
        if (p.kind !== kind) return;
        if (!best || (p.started || "") > (best.started || "")) best = p;
      });
      return best;
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
    /* deliberately BACKUPS-only: a bulk cancel exists because a backup run is
       one thing (many robots, one intent). Scans are killed one at a time via
       their own ✕ — an accidental "cancel all" must never eat a fleet scan. */
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
    BV.api.call("list_scan_jobs").then(function (res) {
      (res.jobs || []).forEach(function (p) {
        scans[p.id] = p;
        if (isTerminal(p)) scanSeen[p.id] = true;
      });
      if (activeScanIds().length) ensureTimer();
      render();
    }).catch(function () {});
  });

  function ensureTimer() {
    if (!timer) timer = setInterval(tick, 500);
  }

  /* one tick = one poll per family that still has live jobs, then ONE paint.
     The "jobs" event keeps firing every tick (home's per-row bars repaint from
     it); "scan-jobs" fires only on terminal transitions — nothing paints live
     scan bars from the event (the strip renders directly, an attached window
     polls scan_progress itself), the listeners only care that a scan ENDED. */
  function tick() {
    if (!activeIds().length && !activeScanIds().length) {
      clearInterval(timer);
      timer = null;
      render();
      return;
    }
    var polls = [];
    if (activeIds().length) polls.push(pollBackups());
    if (activeScanIds().length) polls.push(pollScans());
    Promise.all(polls).then(render);
  }

  function pollBackups() {
    return BV.api.call("list_backup_jobs").then(function (res) {
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
    }).catch(function () {});
  }

  function pollScans() {
    return BV.api.call("list_scan_jobs").then(function (res) {
      var seen = {};
      (res.jobs || []).forEach(function (p) { scans[p.id] = p; seen[p.id] = true; });
      /* reconcile: a tracked id the server does not list is a stub that never
         became a real job (a failed start, a probe's faked start, a pre-reload
         relic). A few polls' grace - a brand-new track can race one in-flight
         response - then it drops, or it pins the timer and poisons
         activeScan() forever. */
      Object.keys(scans).forEach(function (id) {
        if (seen[id]) return;
        scans[id]._misses = (scans[id]._misses || 0) + 1;
        if (scans[id]._misses >= 3) { delete scans[id]; delete scanSeen[id]; }
      });
      var newlyDone = [];
      Object.keys(scans).forEach(function (id) {
        if (isTerminal(scans[id]) && !scanSeen[id]) {
          scanSeen[id] = true;
          newlyDone.push(id);
        }
      });
      if (newlyDone.length) BV.state.emit("scan-jobs", { jobs: scans, newlyDone: newlyDone });
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
    /* the scan rows' box: clicks are DELEGATED to the stable parent (a row
       whose button is rebuilt mid-click misfires - the details-button lesson) */
    var scanBox = BV.el("div", { class: "jobstrip-scans hidden" });
    scanBox.addEventListener("click", function (e) {
      var t = e.target && e.target.closest
        ? e.target.closest("[data-cancel-scan],[data-open-scan]") : null;
      if (!t || !scanBox.contains(t)) return;
      var cid = t.getAttribute("data-cancel-scan");
      if (cid) {
        BV.api.call("cancel_scan", cid).catch(function () {});
        return;
      }
      var p = scans[t.getAttribute("data-open-scan")];
      if (!p) return;
      if (p.kind === "network" && BV.discover) BV.discover();
      else if (BV.scanUI) BV.scanUI.open([]);   /* attaches to the live scan */
    });
    var busy = BV.el("div", { class: "jobstrip-busy hidden" },
      '<span class="busy-pulse"></span><span class="jobstrip-busy-label"></span>');
    var panel = BV.el("div", { class: "jobstrip-panel hidden" });
    panel.addEventListener("click", function (e) {
      var b = e.target && e.target.closest ? e.target.closest("[data-cancel]") : null;
      if (!b || !panel.contains(b)) return;
      BV.api.call("cancel_backup", b.getAttribute("data-cancel")).catch(function () {});
    });
    el.appendChild(run);
    el.appendChild(scanBox);
    el.appendChild(busy);
    el.appendChild(panel);
    dom = {
      root: el, run: run, panel: panel, details: details, busy: busy,
      scanBox: scanBox, scanRows: {},
      labL: bar.querySelector(".js-lab-l"),
      labR: bar.querySelector(".js-lab-r"),
      fill: bar.querySelector(".mb-fill"),
      busyLab: busy.querySelector(".jobstrip-busy-label"),
    };
    return dom;
  }

  /* one scan row = its own mini membar + open + ✕, updated in place like the
     run bar (rebuilding every tick would snap the fill animation too) */
  function scanRow(id) {
    var row = BV.el("div", { class: "jobstrip-scan" });
    var bar = BV.el("div", { class: "membar" },
      '<div class="mb-label"><span class="js-slab-l"></span><span class="js-slab-r"></span></div>' +
      '<div class="mb-track"><div class="mb-fill"></div></div>');
    row.appendChild(bar);
    row.appendChild(BV.el("button", { class: "btn", "data-open-scan": id,
      title: "reopen this scan's window - the scan keeps running either way" }, "open"));
    row.appendChild(BV.el("button", { class: "btn jobstrip-cancel", "data-cancel-scan": id,
      title: "stop this scan" }, "✕"));
    return { el: row,
             labL: bar.querySelector(".js-slab-l"),
             labR: bar.querySelector(".js-slab-r"),
             fill: bar.querySelector(".mb-fill") };
  }

  function renderScans(d) {
    var act = activeScanIds();
    Object.keys(d.scanRows).forEach(function (id) {
      if (act.indexOf(id) === -1) {
        d.scanBox.removeChild(d.scanRows[id].el);
        delete d.scanRows[id];
      }
    });
    act.forEach(function (id) {
      var r = d.scanRows[id];
      if (!r) {
        r = d.scanRows[id] = scanRow(id);
        d.scanBox.appendChild(r.el);
      }
      var p = scans[id];
      r.labL.textContent = scanLabel(p);
      r.labR.textContent = p.total
        ? (p.scanned || 0) + " / " + p.total +
          (p.kind === "network" && p.found ? " · " + p.found + " found" : "")
        : BV.jobs.statusText(p);
      var pct = p.total ? Math.round(100 * (p.scanned || 0) / p.total) : 0;
      if (pct < 2) pct = 2;   /* the same sliver of life the run bar keeps */
      r.fill.style.width = pct + "%";
    });
    d.scanBox.classList.toggle("hidden", !act.length);
  }

  function render() {
    var el = document.getElementById("jobstrip");
    if (!el) return;
    var act = activeIds();
    if (!act.length && !activeScanIds().length && !busyCount) {
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
    renderScans(d);

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
