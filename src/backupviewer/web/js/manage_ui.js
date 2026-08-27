/* manage_ui.js - the "manage backups" modal behind the library's functions…
   menu. Two tabs:

   REPORT - taking backups: the durable last-run summary with retry-failed
   (backup_log / retry_failed_backups - outcomes survive the post-backup
   library refresh) and the stale check (newest completed backup older than
   N days) with the never-backed-up robots behind a fold. Stale/never robots
   are selectable; backup fires through the library's own engine
   (BV.startBackups - one shared run, shared password prompt).

   CLEANUP - keeping backups: retention verdicts computed by the Python
   engine (lib_retention - this file renders them and never re-derives; the
   same engine re-judges every pick inside lib_stage), grouped checklists,
   and the stage action: candidates MOVE to <library>/_staged for a
   one-folder delete in Explorer. The app never deletes - restoring is moving
   a folder back and rescanning (files are law).

   Every long list folds by plant -> line (state remembered per modal open);
   protected pins live in their own "pinned" category, favorites-strip style,
   NOT duplicated into their plant/line. */
(function () {
  "use strict";

  var STALE_DEFAULT = 30;
  var CLEAN_DAYS_DEFAULT = 90;
  var CLEAN_KEEP_DEFAULT = 2;

  function fmtWhen(iso) {
    return (iso || "").replace("T", " ");
  }

  function intSetting(key, dflt) {
    var v = parseInt((BV.state.settings || {})[key], 10);
    return v >= 0 ? v : dflt;
  }

  function saveSetting(key, v) {
    if (BV.state.settings) BV.state.settings[key] = v;
    BV.api.call("set_setting", key, v).catch(function () {});
  }

  BV.manageUI = {
    /* opts.tab: "report" (default) | "cleanup" - otherwise the last-used tab */
    open: function (opts) {
      var host = BV.el("div", { class: "mb-host" });
      var changed = false;   /* staged something -> home repaints on close */
      /* no title: the tab row IS the header (browser-tab style, like .stab) */
      var modal = BV.modal("", host, {
        onClose: function () {
          if (changed && BV.state && BV.state.emit) BV.state.emit("library-updated");
        },
      });
      modal.el.classList.add("mb-modal");
      var reportCache = null;   /* one fetch per open; dropped after a stage */
      var folds = {};           /* fold-state by key - survives repaints this open */
      var autoStaged = false;   /* the auto-stage pass runs once per open */

      var tab = ((BV.state.settings || {}).manage_tab === "cleanup") ? "cleanup" : "report";
      if (opts && opts.tab) tab = opts.tab === "cleanup" ? "cleanup" : "report";
      var slot = BV.el("div", { class: "mb-slot" });
      var tabs = BV.el("div", { class: "mb-tabs" });
      var tabBtns = {};
      [{ id: "report", label: "report" }, { id: "cleanup", label: "cleanup" }]
        .forEach(function (t) {
          var b = BV.el("button", { class: "mb-tab" }, t.label);
          b.addEventListener("click", function () {
            if (tab === t.id) return;
            tab = t.id;
            saveSetting("manage_tab", t.id);
            paintTabs();
            build();
          });
          tabBtns[t.id] = b;
          tabs.appendChild(b);
        });
      function paintTabs() {
        Object.keys(tabBtns).forEach(function (id) {
          tabBtns[id].classList.toggle("active", id === tab);
        });
      }
      paintTabs();
      host.appendChild(tabs);
      host.appendChild(slot);

      function build() {
        if (tab === "cleanup") buildCleanup();
        else buildReport();
      }

      function loadFail(e) {
        slot.innerHTML = '<div class="hs-info">could not load: ' + BV.esc(e.message) + "</div>";
      }

      function openRobot(robotId) {
        if (BV.session.focusRobot(robotId)) { modal.close(true); return; }
        BV.api.call("lib_open", robotId, "latest").then(function (m) {
          BV.session.open(m);
          BV.state.setManifest(m);
          modal.close(true);
          location.hash = "#overview";
        }).catch(function (e) { BV.toast(e.message); });
      }

      /* ---- shared fold/tree scaffolding (every long list uses these) ---- */

      function catHead(title) {
        var h = BV.el("div", { class: "hs-cat-head" });
        h.appendChild(BV.el("span", { class: "hs-cat-title" }, BV.esc(title)));
        return h;
      }

      function selAll(cl, keysFn, gkey) {
        var lab = BV.el("label", { class: "scan-selall", title: "select all / clear" });
        /* the box lives on a fold head - ticking it must not toggle the fold */
        lab.addEventListener("click", function (e) { e.stopPropagation(); });
        lab.appendChild(cl.group(
          BV.el("input", { type: "checkbox", class: "lf-check" }), keysFn, gkey));
        lab.appendChild(BV.el("span", null, "all"));
        return lab;
      }

      function fold(key, headEl, bodyEl, dflt, cls) {
        var wrap = BV.el("div", { class: "mb-fold" + (cls ? " " + cls : "") });
        wrap.appendChild(headEl);
        wrap.appendChild(bodyEl);
        BV.collapsible(wrap, headEl, bodyEl, {
          open: (key in folds) ? folds[key] : !!dflt,
          onToggle: function (o) { folds[key] = o; },
        });
        return wrap;
      }

      /* plant -> line (-> robot) fold tree. o: {key, row(it, bare), label(rows)
         -> head suffix, cl + selKey(it) to grow select-alls on every fold
         head, byRobot to group one robot's many backups under its name}. A
         lone plant opens itself (a single wrapper fold would only cost a
         click); robot folds stay closed - the count IS the summary. */
      function treeByPlantLine(rows, o) {
        var plants = {};
        rows.forEach(function (it) {
          var p = it.plant || "(no plant)";
          var l = it.line || "(no line)";
          (plants[p] = plants[p] || {})[l] = plants[p][l] || [];
          plants[p][l].push(it);
        });
        function groupSel(h, rs, key) {
          if (o.selKey) {
            h.appendChild(selAll(o.cl, function () { return rs.map(o.selKey); }, key));
          }
        }
        var pnames = Object.keys(plants).sort();
        var single = pnames.length === 1;
        var box = BV.el("div", { class: "mb-tree" });
        pnames.forEach(function (p) {
          var pbody = BV.el("div");
          var prows = [];
          Object.keys(plants[p]).sort().forEach(function (l) {
            var rs = plants[p][l];
            prows = prows.concat(rs);
            var lkey = o.key + "|" + p + "|" + l;
            var lbody = BV.el("div");
            if (o.byRobot) {
              var byR = {};
              rs.forEach(function (it) {
                (byR[it.robot] = byR[it.robot] || []).push(it);
              });
              Object.keys(byR).sort().forEach(function (rn) {
                var rrows = byR[rn];
                var rbody = BV.el("div");
                rrows.forEach(function (it) { rbody.appendChild(o.row(it, true)); });
                var rh = catHead(rn + o.label(rrows));
                groupSel(rh, rrows, lkey + "|" + rn);
                lbody.appendChild(fold(lkey + "|" + rn, rh, rbody, false));
              });
            } else {
              rs.forEach(function (it) { lbody.appendChild(o.row(it)); });
            }
            var lh = catHead(l + o.label(rs));
            groupSel(lh, rs, lkey);
            pbody.appendChild(fold(lkey, lh, lbody, false));
          });
          var ph = catHead(p + o.label(prows));
          groupSel(ph, prows, o.key + "|" + p);
          box.appendChild(fold(o.key + "|" + p, ph, pbody, single));
        });
        return box;
      }

      /* start a stage job and resolve when the WORKER finishes - never when
         the request returns. Cross-volume staging copies every file, which
         takes real minutes at plant scale; the one time completion was
         implied early, a mid-move app exit followed. */
      function runStage(picks, days, keep, onProg) {
        return BV.api.call("lib_stage", picks, days, keep).then(function () {
          return new Promise(function (resolve, reject) {
            (function tick() {
              BV.api.call("lib_stage_progress").then(function (p) {
                if (p && p.running) {
                  if (onProg) onProg(p);
                  setTimeout(tick, 500);
                } else if (p && p.error) {
                  reject(new Error(p.error));
                } else {
                  resolve((p && p.result) || { staged: [], failed: [] });
                }
              }).catch(reject);
            })();
          });
        });
      }

      function countLabel(rows) {
        return " — " + rows.length.toLocaleString();
      }

      /* ================= report: what ran, what's due ================= */

      function buildReport() {
        slot.innerHTML = '<div class="dim" style="padding:.5rem 0">loading…</div>';
        if (!reportCache) {
          reportCache = Promise.all([BV.api.call("backup_log"), BV.api.call("lib_list")]);
        }
        reportCache.then(function (rs) {
          if (tab === "report") paintReport(rs[0] || {}, rs[1] || {});
        }).catch(loadFail);
      }

      function paintReport(logData, lib) {
        slot.innerHTML = "";
        var robots = ((lib && lib.robots) || []).filter(function (r) { return !r.hidden; });
        var byId = {};
        robots.forEach(function (r) { byId[r.id] = r; });

        /* ---- top: last backup run (the durable log, not the wiped marks) ---- */
        var runPane = BV.el("div", { class: "mb-runpane" });
        runPane.appendChild(catHead("last backup run"));
        slot.appendChild(runPane);
        var runs = (logData && logData.runs) || [];
        var run = runs[0];
        if (!run) {
          runPane.appendChild(BV.el("div", { class: "mb-none" },
            "no backup runs recorded yet — select robots in the library and hit backup"));
        } else {
          var jobs = run.jobs || [];
          var n = { done: 0, error: 0, cancelled: 0, running: 0 };
          jobs.forEach(function (j) { n[j.status] = (n[j.status] || 0) + 1; });
          var bits = [n.done + " ok"];
          if (n.error) bits.push(n.error + " failed");
          if (n.cancelled) bits.push(n.cancelled + " cancelled");
          if (n.running) bits.push(n.running + " still running");
          runPane.appendChild(BV.el("div", { class: "mb-sum" },
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
            runPane.appendChild(flist);
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
          runPane.appendChild(acts);
        }

        /* ---- below: stale backups, one scroller of plant/line folds ---- */
        var stalePane = BV.el("div", { class: "mb-stalepane" });
        var sh = catHead("stale backups");
        var lab = BV.el("label", { class: "scan-selall", title: "how old counts as stale" });
        lab.appendChild(BV.el("span", null, "older than"));
        var days = BV.el("input", { class: "mb-stale-days", type: "text", spellcheck: "false" });
        days.value = String(intSetting("stale_days", STALE_DEFAULT) || STALE_DEFAULT);
        lab.appendChild(days);
        lab.appendChild(BV.el("span", null, "days"));
        sh.appendChild(lab);
        stalePane.appendChild(sh);
        var shost = BV.el("div", { class: "mb-colfill" });
        stalePane.appendChild(shost);
        slot.appendChild(stalePane);

        var rcl = BV.checklist({ onChange: updateBackupBar });

        function robotRow(r, sum) {
          var row = BV.el("div", { class: "hs-row mb-cl-row", title: "open this robot" });
          row.appendChild(rcl.bind(
            BV.el("input", { type: "checkbox", class: "lf-check" }), r.id));
          row.appendChild(BV.el("span", { class: "hs-robot" }, BV.esc(r.robot || "(unnamed)")));
          row.appendChild(BV.el("span", { class: "hs-sum" }, BV.esc(sum || "")));
          row.addEventListener("click", function () { openRobot(r.id); });
          return row;
        }

        function paintStale() {
          var nd = parseInt(days.value, 10);
          if (!(nd > 0)) nd = STALE_DEFAULT;
          var cut = Date.now() - nd * 86400000;
          var stale = [];
          var never = [];
          robots.forEach(function (r) {
            if (!r.last_backup) {
              never.push(r);         /* no COMPLETED backup: never taken, or partial-only */
              return;
            }
            var t = Date.parse(r.last_backup);
            if (!isNaN(t) && t < cut) stale.push(r);
          });
          stale.sort(function (a, b) {
            return a.last_backup < b.last_backup ? -1 : 1;   /* oldest first */
          });
          shost.innerHTML = "";
          shost.appendChild(BV.el("div", { class: "mb-sum" },
            BV.esc(stale.length + " robot" + (stale.length === 1 ? "" : "s") +
              " with no completed backup in " + nd + " days")));
          if (never.length) {
            var nh = catHead(never.length + " with none at all — never taken, or partial-only");
            var nb = BV.el("div");
            nb.appendChild(treeByPlantLine(never, {
              key: "nv", cl: rcl, selKey: function (r) { return r.id; },
              label: countLabel,
              row: function (r) {
                return robotRow(r, (r.backups || []).length ? "partial-only" : "never taken");
              },
            }));
            shost.appendChild(fold("nv", nh, nb, false, "mb-sec-never"));
          }
          if (stale.length) {
            var st = BV.el("div", { class: "mb-sec-stale" });
            st.appendChild(treeByPlantLine(stale, {
              key: "st", cl: rcl, selKey: function (r) { return r.id; },
              label: countLabel,
              row: function (r) { return robotRow(r, "last " + fmtWhen(r.last_backup)); },
            }));
            shost.appendChild(st);
          }
          rcl.sync();
        }
        days.addEventListener("input", function () {
          paintStale();
          var nd = parseInt(days.value, 10);
          if (nd > 0) saveSetting("stale_days", nd);
        });

        /* ---- chrome: the backup bar (silent until something is picked) ---- */
        var bar = BV.el("div", { class: "mb-stagebar" });
        var bsum = BV.el("span", { class: "mb-sum" }, "");
        var bkup = BV.el("button", { class: "btn",
          title: "back up the selected robots now (one run, shared password prompt)" },
          "backup");
        bkup.disabled = true;
        bar.appendChild(bsum);
        bar.appendChild(bkup);
        slot.appendChild(bar);

        function updateBackupBar() {
          var ks = rcl.selected();
          bsum.textContent = ks.length ? ks.length.toLocaleString() + " selected" : "";
          bkup.disabled = !ks.length;
          bkup.textContent = ks.length ? "backup (" + ks.length.toLocaleString() + ")" : "backup";
        }
        bkup.addEventListener("click", function () {
          var sel = rcl.selected().map(function (id) { return byId[id]; })
            .filter(Boolean);
          if (!sel.length || !BV.startBackups) return;
          BV.startBackups(sel, {
            onFired: function (runnable) {
              BV.toast("backing up " + runnable.length + " robot" +
                (runnable.length === 1 ? "" : "s"));
              modal.close(true);      /* the library rows show the progress */
            },
          });
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

      /* ================= cleanup: what can go ================= */

      function buildCleanup() {
        var note = BV.el("div", { class: "dim", style: "padding:.5rem 0" }, "loading…");
        slot.innerHTML = "";
        slot.appendChild(note);
        var days = intSetting("cleanup_days", CLEAN_DAYS_DEFAULT);
        var keep = intSetting("cleanup_keep", CLEAN_KEEP_DEFAULT);

        function loadRetention() {
          BV.api.call("lib_retention", days, keep).then(function (d) {
            if (tab !== "cleanup") return;
            d = d || {};
            /* auto-stage: dead pulls that are NOT a robot's newest go straight
               to staging (once per open). Newest ones stay - they are the
               out-of-date evidence the backup-broken button acts on. */
            if (!autoStaged && (BV.state.settings || {}).auto_stage_partials === true) {
              autoStaged = true;
              var auto = (d.items || []).filter(function (it) {
                return it.verdict === "candidate" && it.group === "partial" && !it.newest;
              });
              if (auto.length) {
                runStage(
                  auto.map(function (it) { return { robot_id: it.robot_id, taken: it.taken }; }),
                  days, keep,
                  function (p) { note.textContent = "auto-staging… " + p.done + "/" + p.total; }
                ).then(function (res) {
                  changed = true;
                  reportCache = null;
                  var ns = (res.staged || []).length;
                  if (ns) {
                    BV.toast("auto-staged " + ns + " partial backup" + (ns === 1 ? "" : "s"));
                  }
                  buildCleanup();
                }).catch(function (e) { BV.toast(e.message); paintCleanup(d); });
                return;
              }
            }
            paintCleanup(d);
          }).catch(loadFail);
        }

        /* a move may already be running (started before a close/reopen) -
           reattach to its progress instead of hanging behind the lock */
        BV.api.call("lib_stage_progress").then(function (p) {
          if (!(p && p.running)) { loadRetention(); return; }
          (function tick() {
            BV.api.call("lib_stage_progress").then(function (q) {
              if (tab !== "cleanup") return;
              if (q && q.running) {
                note.textContent = "staging… " + q.done + "/" + q.total;
                setTimeout(tick, 500);
              } else {
                changed = true;
                reportCache = null;
                buildCleanup();
              }
            }).catch(function () { loadRetention(); });
          })();
        }).catch(function () { loadRetention(); });
      }

      function paintCleanup(d) {
        slot.innerHTML = "";
        var items = d.items || [];
        var totals = d.totals || {};
        var libT = totals.library || {};
        var days = d.days;
        var keep = d.keep;

        var byTaken = function (a, b) { return a.taken < b.taken ? -1 : a.taken > b.taken ? 1 : 0; };
        var partials = items.filter(function (it) {
          return it.verdict === "candidate" && it.group === "partial";
        }).sort(function (a, b) {          /* broken (newest) pulls surface first */
          return ((b.newest ? 1 : 0) - (a.newest ? 1 : 0)) || byTaken(a, b);
        });
        var superseded = items.filter(function (it) {
          return it.verdict === "candidate" && it.group === "superseded";
        }).sort(byTaken);
        var prot = items.filter(function (it) {
          return it.verdict === "protected" && it.reason !== "recent";
        }).sort(function (a, b) {
          return ((b.warn ? 1 : 0) - (a.warn ? 1 : 0)) || byTaken(a, b);
        });
        var pinned = prot.filter(function (it) { return it.reason === "pinned"; });
        var protTree = prot.filter(function (it) { return it.reason !== "pinned"; });
        var keyOf = function (it) { return it.robot_id + "|" + it.taken; };
        var byKey = {};
        items.forEach(function (it) { byKey[keyOf(it)] = it; });

        /* one terse descriptor per protection, the full why in its hover title
           - the engine sends ids, these tables only word them (never decide) */
        var REASONS = {
          pinned: "pinned", offline: "offline", latest: "latest", kept: "kept",
          only: "only backup", last: "last trace", undated: "undated",
        };
        var REASON_TIPS = {
          pinned: "exempt until unpinned",
          offline: "folder unreachable right now (offline drive?)",
          latest: "old but latest — take a fresh backup first",
          kept: "within the newest " + keep + " completed",
          only: "the robot's only backup — irreplaceable",
          last: "no completed backup exists — its last trace",
          undated: "no date recorded — age unprovable",
        };

        function fmtB(n) { return BV.fmt.bytes(n); }

        function sizeLabel(rows) {
          var b = 0;
          rows.forEach(function (it) { b += it.bytes || 0; });
          return " — " + rows.length.toLocaleString() + (b ? " · " + fmtB(b) : "");
        }

        /* ---- chrome: totals + staging + knobs ---- */
        var top = BV.el("div", { class: "mb-cleantop" });
        var t1 = BV.el("div", { class: "mb-cleanrow mb-sum" });
        var totalsEl = BV.el("span", null,
          BV.esc((libT.count || 0).toLocaleString() + " backup" +
            (libT.count === 1 ? "" : "s") + " · " +
            (libT.unsized ? "~" : "") + fmtB(libT.bytes || 0)));
        if (libT.unsized) {           /* the ~ says "floor"; the title says why */
          totalsEl.title = libT.unsized + " backups have no recorded size";
        }
        t1.appendChild(totalsEl);
        var stg = d.staging || {};
        var stgSpan = BV.el("span", { class: "mb-stg" });
        if (stg.error) {
          stgSpan.textContent = stg.error;                 /* misconfig, said plainly */
        } else if (stg.mode === "recycle") {
          stgSpan.textContent = "staging: recycle bin";
        } else if (stg.count) {
          stgSpan.textContent = "staged: " + stg.count.toLocaleString() + " · " +
            fmtB(stg.bytes || 0);
          stgSpan.title = "delete the staging folder in Explorer to reclaim the space";
        }
        t1.appendChild(stgSpan);
        if (!stg.error && (stg.present || stg.mode === "recycle")) {
          var ob = BV.el("button", { class: "btn",
            title: "reveal wherever staging points right now" },
            stg.mode === "recycle" ? "open recycle bin" : "open staging");
          ob.addEventListener("click", function () {
            BV.api.call("open_staging").catch(function (e) { BV.toast(e.message); });
          });
          t1.appendChild(ob);
        }
        top.appendChild(t1);

        var t2 = BV.el("div", { class: "mb-cleanrow" });
        function knobEl(before, key, dflt, after, min) {
          var lab = BV.el("label", { class: "scan-selall" });
          lab.appendChild(BV.el("span", null, before));
          var inp = BV.el("input", { class: "mb-stale-days", type: "text", spellcheck: "false" });
          inp.value = String(intSetting(key, dflt));
          lab.appendChild(inp);
          if (after) lab.appendChild(BV.el("span", null, after));
          var t = null;
          inp.addEventListener("input", function () {
            var v = parseInt(inp.value, 10);
            if (!(v >= min)) return;
            saveSetting(key, v);
            if (t) clearTimeout(t);
            t = setTimeout(function () { if (tab === "cleanup") buildCleanup(); }, 350);
          });
          return lab;
        }
        t2.appendChild(knobEl("older than", "cleanup_days",
          CLEAN_DAYS_DEFAULT, "days", 1));
        t2.appendChild(knobEl("· keep newest", "cleanup_keep",
          CLEAN_KEEP_DEFAULT, "completed", 0));
        top.appendChild(t2);
        slot.appendChild(top);

        /* ---- the one scroller: three sections of plant/line folds ---- */
        var list = BV.el("div", { class: "mb-cleanlist" });
        slot.appendChild(list);

        var cl = BV.checklist({ onChange: updateBar });

        function pinBtn(it) {
          var isPinned = it.reason === "pinned";
          var b = BV.el("button", { class: "btn mb-pin",
            title: isPinned ? "remove the keep-forever mark"
              : "keep forever — never a cleanup candidate" },
            isPinned ? "unpin" : "pin");
          b.addEventListener("click", function (e) {
            e.preventDefault();               /* inside a label: don't toggle the box */
            e.stopPropagation();
            b.disabled = true;
            BV.api.call("lib_set_pin", it.robot_id, it.taken, !isPinned)
              .then(function () { buildCleanup(); })
              .catch(function (e2) { BV.toast(e2.message); b.disabled = false; });
          });
          return b;
        }

        function candRow(it, bare) {
          var row = BV.el("label", { class: "mb-cl-row hs-check" });
          row.appendChild(cl.bind(
            BV.el("input", { type: "checkbox", class: "lf-check" }), keyOf(it)));
          if (!bare) row.appendChild(BV.el("span", { class: "hs-robot" }, BV.esc(it.robot)));
          row.appendChild(BV.el("span", { class: "mb-cl-when" }, BV.esc(fmtWhen(it.taken))));
          if (it.partial) {
            row.insertAdjacentHTML("beforeend", BV.pill("partial", "warn"));
            if (it.newest) {
              /* the robot's newest backup IS this dead pull - the broken set */
              row.insertAdjacentHTML("beforeend", BV.pill("latest", "err"));
              row.title = "this robot's newest backup is a dead pull — its current " +
                "state was never captured (backup broken targets these)";
            }
            /* partials skip the age knob, so each says its age itself */
            var age = Math.floor((Date.now() - Date.parse(it.taken)) / 864e5);
            if (isFinite(age) && age >= 0) {
              row.appendChild(BV.el("span", { class: "mb-cl-when" }, age + "d old"));
            }
          }
          row.appendChild(BV.el("span", { class: "mb-cl-size" },
            it.bytes ? fmtB(it.bytes) : ""));
          row.appendChild(pinBtn(it));
          return row;
        }

        function protRow(it, showLine, bare) {
          var row = BV.el("div", { class: "mb-cl-row prot" + (it.warn ? " warn" : "") });
          if (!bare) row.appendChild(BV.el("span", { class: "hs-robot" }, BV.esc(it.robot)));
          if (showLine && it.line) {
            row.appendChild(BV.el("span", { class: "hs-line" }, BV.esc(it.line)));
          }
          row.appendChild(BV.el("span", { class: "mb-cl-when" }, BV.esc(fmtWhen(it.taken))));
          var why = BV.el("span", { class: "mb-cl-reason" },
            BV.esc(REASONS[it.reason] || it.reason));
          why.title = REASON_TIPS[it.reason] || "";
          row.appendChild(why);
          if (it.reason === "pinned") row.appendChild(pinBtn(it));
          return row;
        }

        function candSection(cls, key, title, tip, arr, dflt, headExtra) {
          var h = catHead(title + sizeLabel(arr));
          h.title = tip || "";                    /* the why lives on hover, not on screen */
          if (headExtra) h.appendChild(headExtra);          /* left of the all-box */
          h.appendChild(selAll(cl, function () { return arr.map(keyOf); }, key));
          var body = BV.el("div");
          body.appendChild(treeByPlantLine(arr, {
            key: key, cl: cl, selKey: keyOf, label: sizeLabel, row: candRow,
            byRobot: true,        /* one robot, many old backups: one fold */
          }));
          return fold(key, h, body, dflt, cls);
        }

        /* robots whose NEWEST snapshot is a dead pull: their current state was
           never captured. The engine flags them (item.newest); this button
           backs exactly those up - and a fresh completed backup is also what
           frees their partial for cleanup. */
        function backupBrokenBtn() {
          var ids = [];
          partials.forEach(function (it) {
            if (it.newest && ids.indexOf(it.robot_id) < 0) ids.push(it.robot_id);
          });
          if (!ids.length) return null;
          var b = BV.el("button", { class: "btn mb-pin",
            title: "these robots' newest snapshot is a dead pull — their current " +
              "state was never captured. back them up now (a fresh backup also " +
              "frees the partial for cleanup)" },
            "backup broken (" + ids.length + ")");
          b.addEventListener("click", function (e) {
            e.stopPropagation();                           /* rides the fold head */
            b.disabled = true;
            BV.api.call("lib_list").then(function (lib) {
              var byId = {};
              ((lib && lib.robots) || []).forEach(function (r) { byId[r.id] = r; });
              var sel = ids.map(function (id) { return byId[id]; }).filter(Boolean);
              if (!sel.length || !BV.startBackups) { b.disabled = false; return; }
              var fired = BV.startBackups(sel, {
                onFired: function (runnable) {
                  BV.toast("backing up " + runnable.length + " robot" +
                    (runnable.length === 1 ? "" : "s"));
                  modal.close(true);      /* the library rows show the progress */
                },
              });
              if (!fired) b.disabled = false;              /* nothing runnable (no IP) */
            }).catch(function (e2) { BV.toast(e2.message); b.disabled = false; });
          });
          return b;
        }

        if (!partials.length && !superseded.length) {
          list.appendChild(BV.el("div", { class: "mb-none" },
            "nothing to clean up at these settings"));
        } else {
          if (partials.length) {
            list.appendChild(candSection("mb-sec-partial", "cp",
              "partial backups", "pulls that died mid-download — never opened as "
              + "latest, never auto-deleted", partials, true, backupBrokenBtn()));
          }
          if (superseded.length) {
            list.appendChild(candSection("mb-sec-super", "cs",
              "superseded", "newer completed backups exist", superseded, false));
          }
        }

        if (prot.length) {
          var ph = catHead("protected — " + prot.length.toLocaleString());
          ph.title = "old backups being kept — each row says why";
          var pb = BV.el("div");
          if (pinned.length) {
            /* favorites-strip style: pinned rows live HERE, a sibling of the
               plant folds, and are not repeated inside their plant/line */
            var pinBody = BV.el("div");
            pinned.forEach(function (it) { pinBody.appendChild(protRow(it, true)); });
            pb.appendChild(fold("pr|pinned", catHead("pinned" + countLabel(pinned)),
              pinBody, true, "mb-pin-cat"));
          }
          if (protTree.length) {
            pb.appendChild(treeByPlantLine(protTree, {
              key: "pr", label: countLabel, byRobot: true,
              row: function (it, bare) { return protRow(it, false, bare); },
            }));
          }
          list.appendChild(fold("pr", ph, pb, false, "mb-sec-prot"));
        }

        /* ---- chrome: the stage bar (silent until something is picked) ---- */
        var bar = BV.el("div", { class: "mb-stagebar" });
        var sum = BV.el("span", { class: "mb-sum" }, "");
        var stage = BV.el("button", { class: "btn",
          title: "MOVE the checked backups into staging — nothing is deleted; " +
            "restore = move a folder back and rescan" }, "move to staging");
        stage.disabled = true;
        bar.appendChild(sum);
        bar.appendChild(stage);
        slot.appendChild(bar);

        var armedUntil = 0;
        function resetStage() {
          armedUntil = 0;
          stage.textContent = "move to staging";
          stage.classList.remove("armed");
        }
        function updateBar() {
          var keys = cl.selected();
          var b = 0, unsized = 0;
          keys.forEach(function (k) {
            var it = byKey[k];
            if (!it) return;
            if (it.bytes) b += it.bytes; else unsized++;
          });
          sum.textContent = keys.length
            ? keys.length.toLocaleString() + " selected · " + (unsized ? "~" : "") + fmtB(b)
            : "";
          stage.disabled = !keys.length;
          resetStage();
        }

        stage.addEventListener("click", function () {
          var keys = cl.selected();
          if (!keys.length) return;
          if (Date.now() > armedUntil) {      /* two clicks, no modal-on-modal */
            armedUntil = Date.now() + 4000;
            stage.textContent = "confirm move (" + keys.length.toLocaleString() + ")";
            stage.classList.add("armed");
            setTimeout(function () {
              if (armedUntil && Date.now() > armedUntil) resetStage();
            }, 4300);
            return;
          }
          var picks = keys.map(function (k) {
            var it = byKey[k];
            return { robot_id: it.robot_id, taken: it.taken };
          });
          stage.disabled = true;
          stage.textContent = "staging… 0/" + picks.length.toLocaleString();
          runStage(picks, days, keep, function (p) {
            stage.textContent = "staging… " + p.done.toLocaleString() +
              "/" + p.total.toLocaleString();
          }).then(function (res) {
            changed = true;
            reportCache = null;               /* report's library snapshot is stale now */
            var ns = (res.staged || []).length;
            var nf = (res.failed || []).length;
            BV.toast("staged " + ns + " backup" + (ns === 1 ? "" : "s") +
              (nf ? " · " + nf + " refused" : ""));
            cl.clear();
            buildCleanup();
          }).catch(function (e) {
            BV.toast(e.message);
            stage.disabled = false;
            resetStage();
          });
        });

        cl.sync();
      }

      build();
    },
  };
})();
