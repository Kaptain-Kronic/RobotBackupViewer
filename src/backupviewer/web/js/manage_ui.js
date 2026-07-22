/* manage_ui.js - the "manage backups" modal behind the library's manage
   button. Two halves:

   BACKUP HEALTH (needs no selection): the durable last-run summary with
   retry-failed (backup_log / retry_failed_backups - outcomes survive the
   post-backup library refresh), the partial-backup review (snapshots whose
   pull died mid-download; the rescan already keeps them out of "latest"),
   and the stale-backup check (newest completed backup older than N days -
   library metadata only, no backup files touched).

   SELECTED ROBOTS: fix names / merge / move to..., the tidy-up flows that
   used to crowd the library toolbar. They live in home.js (BV.libActions);
   picking one simply opens its own modal in this one's place. */
(function () {
  "use strict";

  var STALE_DEFAULT = 30;

  function fmtWhen(iso) {
    return (iso || "").replace("T", " ");
  }

  function head(title) {
    var h = BV.el("div", { class: "hs-cat-head" });
    h.appendChild(BV.el("span", { class: "hs-cat-title" }, BV.esc(title)));
    return h;
  }

  BV.manageUI = {
    open: function () {
      var host = BV.el("div", { class: "mb-host" });
      var modal = BV.modal("manage backups", host);
      modal.el.classList.add("mb-modal");

      host.innerHTML = '<div class="dim" style="padding:.5rem 0">loading…</div>';
      Promise.all([BV.api.call("backup_log"), BV.api.call("lib_list")])
        .then(function (rs) { render(rs[0] || {}, rs[1] || {}); })
        .catch(function (e) {
          host.innerHTML = '<div class="hs-info">could not load: ' + BV.esc(e.message) + "</div>";
        });

      function openRobot(robotId) {
        BV.api.call("lib_open", robotId, "latest").then(function (m) {
          BV.state.setManifest(m);
          modal.close(true);
          location.hash = "#overview";
        }).catch(function (e) { BV.toast(e.message); });
      }

      function robotRowEl(r, sum, detail) {
        var row = BV.el("div", { class: "hs-row", title: "open this robot" },
          '<span class="hs-robot">' + BV.esc(r.robot || "(unnamed)") + "</span>" +
          (r.line ? '<span class="hs-line">' + BV.esc(r.line) + "</span>" : "") +
          '<span class="hs-sum">' + BV.esc(sum || "") + "</span>" +
          (detail ? '<span class="hs-detail">' + BV.esc(detail) + "</span>" : ""));
        row.addEventListener("click", function () { openRobot(r.id); });
        return row;
      }

      function render(logData, lib) {
        host.innerHTML = "";
        var robots = ((lib && lib.robots) || []).filter(function (r) { return !r.hidden; });
        var byId = {};
        robots.forEach(function (r) { byId[r.id] = r; });

        /* ---- actions bar: the tidy-up flows, on top and ready to fire (grey
           without a selection - they take no room and never hide) ---- */
        var sel = (BV.libActions && BV.libActions.selected()) || [];
        var bar = BV.el("div", { class: "mb-actbar" });
        bar.appendChild(BV.el("span", { class: "hs-cat-title" },
          BV.esc("selected robots (" + sel.length + ")")));
        function tidyBtn(label, title, disabled, fn) {
          var b = BV.el("button", { class: "btn", title: title }, label);
          b.disabled = disabled;
          b.addEventListener("click", function () { fn(); });   /* their modal replaces this one */
          bar.appendChild(b);
        }
        tidyBtn("fix names", "fix the selected robots' names from their backup contents",
          !sel.length, function () { BV.libActions.fixNames(); });
        tidyBtn("merge", "merge 2 selected duplicate robots into one",
          sel.length !== 2, function () { BV.libActions.merge(); });
        tidyBtn("move to…", "move the selected robots (and their backups) to another plant/line",
          !sel.length, function () { BV.libActions.moveTo(); });
        /* library-wide (no selection): pair cameras with their robot by name */
        var lk = BV.el("button", { class: "btn mb-link-cams",
          title: "auto-link cameras to the robot they inspect (by name) — whole library, no selection needed" },
          "link cameras");
        lk.addEventListener("click", function () {
          lk.disabled = true;
          BV.libActions.autoLink()
            .catch(function (e) { BV.toast(e.message); })
            .finally(function () { lk.disabled = false; });
        });
        bar.appendChild(lk);
        host.appendChild(bar);

        /* ---- partial backups: a slim full-width strip - one quiet line when
           clean, the first thing in view when a pull died mid-download ---- */
        var pwrap = BV.el("div", { class: "mb-partial" });
        pwrap.appendChild(head("partial backups"));
        var partial = robots.filter(function (r) {
          return (r.backups || []).some(function (b) { return b.partial; });
        });
        if (!partial.length) {
          pwrap.appendChild(BV.el("div", { class: "mb-none" },
            "none — every snapshot on disk completed"));
        } else {
          /* a robot being backed up RIGHT NOW has a partial snapshot by design
             (the complete-marker lands last) — say that instead of implying a
             dead pull */
          var live = (BV.jobs && BV.jobs.activeTargets) ? BV.jobs.activeTargets() : null;
          var plist = BV.el("div", { class: "mb-list" });
          partial.forEach(function (r) {
            var parts = (r.backups || []).filter(function (b) { return b.partial; });
            var mid = live && BV.jobs.isRobotActive(r, live);
            plist.appendChild(robotRowEl(r,
              parts.length + " partial snapshot" + (parts.length === 1 ? "" : "s") +
              " — newest " + fmtWhen(parts[0].taken) +
              (mid ? " · backing up right now" : ""),
              mid ? "a pull is running — its snapshot completes when the pull finishes"
                : "a pull that died mid-download; never opened as latest, never auto-deleted"));
          });
          pwrap.appendChild(plist);
        }
        host.appendChild(pwrap);

        /* ---- the two heavy lists side by side; only THEY scroll ---- */
        var cols = BV.el("div", { class: "mb-cols" });
        var colRun = BV.el("div", { class: "mb-col" });
        var colStale = BV.el("div", { class: "mb-col" });
        cols.appendChild(colRun);
        cols.appendChild(colStale);
        host.appendChild(cols);

        /* ---- left: last backup run (the durable log, not the wiped marks) ---- */
        colRun.appendChild(head("last backup run"));
        var runs = (logData && logData.runs) || [];
        var run = runs[0];
        if (!run) {
          colRun.appendChild(BV.el("div", { class: "mb-none" },
            "no backup runs recorded yet — select robots in the library and hit backup"));
        } else {
          var jobs = run.jobs || [];
          var n = { done: 0, error: 0, cancelled: 0, running: 0 };
          jobs.forEach(function (j) { n[j.status] = (n[j.status] || 0) + 1; });
          var bits = [n.done + " ok"];
          if (n.error) bits.push(n.error + " failed");
          if (n.cancelled) bits.push(n.cancelled + " cancelled");
          if (n.running) bits.push(n.running + " still running");
          colRun.appendChild(BV.el("div", { class: "mb-sum" },
            BV.esc("started " + fmtWhen(run.started) + " — " + jobs.length + " robot" +
              (jobs.length === 1 ? "" : "s") + ": " + bits.join(" · "))));

          var failed = jobs.filter(function (j) { return j.status === "error"; });
          if (failed.length) {
            var flist = BV.el("div", { class: "mb-list" });
            failed.forEach(function (j) {
              var r = byId[j.robot_id];
              var row = BV.el("div", { class: "hs-row st-flag" + (r ? "" : " mb-static") },
                BV.pill("failed", "err") +
                '<span class="hs-robot">' + BV.esc(j.robot || j.host || "?") + "</span>" +
                (j.line ? '<span class="hs-line">' + BV.esc(j.line) + "</span>" : "") +
                '<span class="hs-sum">' + BV.esc((j.error || "failed") +
                  (j.attempts > 1 ? " · try " + j.attempts : "")) + "</span>");
              if (r) {
                row.title = "open this robot";
                row.addEventListener("click", function () { openRobot(r.id); });
              }
              flist.appendChild(row);
            });
            colRun.appendChild(flist);
          }

          var acts = BV.el("div", { class: "mb-acts" });
          var retry = BV.el("button", {
            class: "btn" + (failed.length ? " primary" : ""),
            title: "re-fire exactly the failed robots as a fresh run",
          }, "retry failed" + (failed.length ? " (" + failed.length + ")" : ""));
          retry.disabled = !failed.length;
          retry.addEventListener("click", function () {
            var needsPw = failed.some(function (j) { return j.user; });
            var ask = BV.promptSharedPassword ||
              function (need, cont) { cont(""); };
            ask(needsPw, function (pw) {
              BV.api.call("retry_failed_backups", run.id, pw).then(function (res) {
                (res.jobs || []).forEach(function (j) {
                  if (BV.jobs) BV.jobs.track(j.job_id, { robotId: j.robot_id });
                });
                BV.toast("retrying " + (res.jobs || []).length + " backup(s)");
                modal.close(true);        /* the library rows show the progress */
              }).catch(function (e) { BV.toast(e.message); });
            });
          });
          var copyBtn = BV.el("button", { class: "btn", title: "copy this run as text" }, "copy log");
          copyBtn.addEventListener("click", function () {
            BV.copyText(runText(run), "log copied");
          });
          acts.appendChild(retry);
          acts.appendChild(copyBtn);
          colRun.appendChild(acts);
        }

        /* ---- right: stale backups (newest completed older than N days) ---- */
        var sh = head("stale backups");
        var lab = BV.el("label", { class: "scan-selall", title: "how old counts as stale" });
        lab.appendChild(BV.el("span", null, "older than"));
        var days = BV.el("input", { class: "mb-stale-days", type: "text", spellcheck: "false" });
        days.value = String((BV.state.settings && BV.state.settings.stale_days) || STALE_DEFAULT);
        lab.appendChild(days);
        lab.appendChild(BV.el("span", null, "days"));
        sh.appendChild(lab);
        colStale.appendChild(sh);
        var shost = BV.el("div", { class: "mb-colfill" });
        colStale.appendChild(shost);

        function paintStale() {
          var nd = parseInt(days.value, 10);
          if (!(nd > 0)) nd = STALE_DEFAULT;
          var cut = Date.now() - nd * 86400000;
          var stale = [];
          var never = 0;
          robots.forEach(function (r) {
            if (!r.last_backup) {
              never++;               /* no COMPLETED backup: never taken, or partial-only */
              return;
            }
            var t = Date.parse(r.last_backup);
            if (!isNaN(t) && t < cut) stale.push({ r: r, t: t });
          });
          stale.sort(function (a, b) { return a.t - b.t; });   /* oldest first */
          shost.innerHTML = "";
          shost.appendChild(BV.el("div", { class: "mb-sum" },
            BV.esc(stale.length + " robot" + (stale.length === 1 ? "" : "s") +
              " with no completed backup in " + nd + " days" +
              (never ? " · " + never + " with none at all" : ""))));
          if (stale.length) {
            var slist = BV.el("div", { class: "mb-list" });
            stale.forEach(function (x) {
              slist.appendChild(robotRowEl(x.r, "last " + fmtWhen(x.r.last_backup)));
            });
            shost.appendChild(slist);
          }
        }
        days.addEventListener("input", function () {
          paintStale();
          var nd = parseInt(days.value, 10);
          if (nd > 0) {
            if (BV.state.settings) BV.state.settings.stale_days = nd;
            BV.api.call("set_setting", "stale_days", nd).catch(function () {});
          }
        });
        paintStale();
      }

      function runText(run) {
        var mark = { done: "✓", error: "✗", cancelled: "-", running: "…" };
        var lines = ["BackupViewer backup run — started " + fmtWhen(run.started) +
                     (run.finished ? " · finished " + fmtWhen(run.finished) : " · still running")];
        (run.jobs || []).forEach(function (j) {
          lines.push("  " + (mark[j.status] || "?") + " " +
            (j.robot || j.host || "?") + (j.line ? " (" + j.line + ")" : "") +
            " — " + j.status + (j.attempts > 1 ? " (try " + j.attempts + ")" : "") +
            (j.error ? " — " + j.error : ""));
        });
        return lines.join("\n");
      }
    },
  };
})();
