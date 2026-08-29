/* manage_ui.js - the "manage backups" modal behind the library's functions…
   menu. Three tabs:

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

   EXPORT - handing backups off: COPY the newest N completed backups of
   picked robots/cameras to a folder/USB stick, laid out by a drag-to-reorder
   template of path segments (plant/line/robot/date/time, each omittable,
   plus typed-name custom folders that carry ✕ instead of a checkbox).
   Options: an age window (only the last X days), robots/cameras/both,
   opt-in robot.json ride-along, and zip-each-backup (the leaf folder
   becomes a CRC-verified .zip; its parents stay real folders). The plan
   comes from the Python engine (lib_export_plan - this file renders the
   preview, foldable with right-click subtree toggle, and never re-derives
   it; lib_export re-plans at copy time), sources are only ever read, and
   nothing at the destination is overwritten - a target already holding
   data is skipped, honestly marked.

   Every long list folds by plant -> line (state remembered per modal open);
   protected pins live in their own "pinned" category, favorites-strip style,
   NOT duplicated into their plant/line. */
(function () {
  "use strict";

  var STALE_DEFAULT = 30;
  var CLEAN_DAYS_DEFAULT = 90;
  var CLEAN_KEEP_DEFAULT = 2;
  var EXPORT_COUNT_DEFAULT = 1;
  var EXPORT_DAYS_DEFAULT = 30;
  /* the export layout segments, in offer order; date/time use the library's
     own folder naming so a full plant/line/robot/date/time export IS a
     rescannable library tree */
  var EXPORT_SEGS = ["plant", "line", "robot", "date", "time"];

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

      var TAB_IDS = ["report", "cleanup", "export"];
      var tab = (BV.state.settings || {}).manage_tab;
      if (TAB_IDS.indexOf(tab) < 0) tab = "report";
      if (opts && opts.tab && TAB_IDS.indexOf(opts.tab) >= 0) tab = opts.tab;
      var slot = BV.el("div", { class: "mb-slot" });
      var tabs = BV.el("div", { class: "mb-tabs" });
      var tabBtns = {};
      TAB_IDS.map(function (id) { return { id: id, label: id }; })
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
        else if (tab === "export") buildExport();
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
          BV.el("input", { type: "checkbox", class: "lf-check" }),
          /* falsy keys = rows with nothing to select (an export row with no
             completed backup) - never counted, never phantom-selected */
          function () { return (keysFn() || []).filter(Boolean); }, gkey));
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

      /* start a library worker job and resolve when the WORKER finishes -
         never when the request returns. Cross-volume staging/exporting copies
         every file, which takes real minutes at plant scale; the one time
         completion was implied early, a mid-move app exit followed. */
      function runJob(start, progEndpoint, onProg) {
        return start.then(function () {
          return new Promise(function (resolve, reject) {
            (function tick() {
              BV.api.call(progEndpoint).then(function (p) {
                if (p && p.running) {
                  if (onProg) onProg(p);
                  setTimeout(tick, 500);
                } else if (p && p.error) {
                  reject(new Error(p.error));
                } else {
                  resolve((p && p.result) || null);
                }
              }).catch(reject);
            })();
          });
        });
      }

      function runStage(picks, days, keep, onProg) {
        return runJob(BV.api.call("lib_stage", picks, days, keep),
                      "lib_stage_progress", onProg)
          .then(function (res) { return res || { staged: [], failed: [] }; });
      }

      /* a knob: "<before> [n] <after>", persisted immediately, onChange
         debounced (350ms) so mid-typing never fires a rebuild */
      function knobEl(before, key, dflt, after, min, onChange) {
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
          t = setTimeout(onChange, 350);
        });
        return lab;
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
        function reclean() { if (tab === "cleanup") buildCleanup(); }
        t2.appendChild(knobEl("older than", "cleanup_days",
          CLEAN_DAYS_DEFAULT, "days", 1, reclean));
        t2.appendChild(knobEl("· keep newest", "cleanup_keep",
          CLEAN_KEEP_DEFAULT, "completed", 0, reclean));
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

      /* ================= export: copies to hand off ================= */

      /* the persisted layout, JSON: [{"id":"plant","on":true}, …,
         {"id":"custom","name":"KIT"}] - order is chip order, known segments
         carry on/off, custom (typed-name) folders exist only while wanted
         (their ✕ removes them, so they have no off state). The v1 comma
         string ("plant,line,-time") still parses; garbage or missing known
         segments fall back to defaults (date on, time off). uids are
         per-paint identity for drag-reorder, never persisted. */
      function parseSegs() {
        var raw = (BV.state.settings || {}).export_segs;
        var out = [];
        var seen = {};
        var uid = 0;
        function known(id, on) {
          if (EXPORT_SEGS.indexOf(id) >= 0 && !seen[id]) {
            seen[id] = true;
            out.push({ id: id, on: on, uid: id });
          }
        }
        var arr = null;
        if (typeof raw === "string" && raw.charAt(0) === "[") {
          try { arr = JSON.parse(raw); } catch (e) { arr = null; }
        }
        if (Array.isArray(arr)) {
          arr.forEach(function (s) {
            if (!s) return;
            if (s.id === "custom") {
              if (typeof s.name === "string" && s.name) {
                out.push({ id: "custom", name: s.name, on: true, uid: "c" + (uid++) });
              }
            } else known(s.id, s.on !== false);
          });
        } else if (typeof raw === "string" && raw) {
          raw.split(",").forEach(function (t) {
            t = t.trim();
            var on = t.charAt(0) !== "-";
            known(on ? t : t.slice(1), on);
          });
        }
        EXPORT_SEGS.forEach(function (id) { known(id, id !== "time"); });
        return out;
      }

      function buildExport() {
        var note = BV.el("div", { class: "dim", style: "padding:.5rem 0" }, "loading…");
        slot.innerHTML = "";
        slot.appendChild(note);

        function load() {
          if (!reportCache) {
            reportCache = Promise.all([BV.api.call("backup_log"), BV.api.call("lib_list")]);
          }
          reportCache.then(function (rs) {
            if (tab === "export") paintExport(rs[1] || {});
          }).catch(loadFail);
        }

        /* a copy may already be running (started before a close/reopen) -
           reattach to its progress instead of double-starting */
        BV.api.call("lib_export_progress").then(function (p) {
          if (!(p && p.running)) { load(); return; }
          (function tick() {
            BV.api.call("lib_export_progress").then(function (q) {
              if (tab !== "export") return;
              if (q && q.running) {
                note.textContent = "exporting… " + q.done + "/" + q.total;
                setTimeout(tick, 500);
              } else {
                buildExport();
              }
            }).catch(function () { load(); });
          })();
        }).catch(function () { load(); });
      }

      function paintExport(lib) {
        slot.innerHTML = "";
        var robots = ((lib && lib.robots) || []).filter(function (r) { return !r.hidden; });
        var segs = parseSegs();
        var destPath = String((BV.state.settings || {}).export_dir || "");
        var lastPlan = null;
        var lastFails = [];         /* the previous run's per-snapshot errors */
        var planSeq = 0;
        var running = false;

        function saveSegs() {
          saveSetting("export_segs", JSON.stringify(segs.map(function (s) {
            return s.id === "custom" ? { id: "custom", name: s.name }
                                     : { id: s.id, on: !!s.on };
          })));
        }
        function segsOn() {
          var out = [];
          segs.forEach(function (s) {
            if (s.id === "custom") out.push({ custom: s.name });
            else if (s.on) out.push(s.id);
          });
          return out;
        }
        function countVal() {
          var v = intSetting("export_count", EXPORT_COUNT_DEFAULT);
          return v >= 1 ? v : EXPORT_COUNT_DEFAULT;
        }
        function boolOpt(key) { return (BV.state.settings || {})[key] === true; }
        function daysVal() {
          if (!boolOpt("export_days_on")) return 0;
          var v = intSetting("export_days", EXPORT_DAYS_DEFAULT);
          return v >= 1 ? v : EXPORT_DAYS_DEFAULT;
        }
        /* which folder level becomes the archive. Persisted as "data" (one
           zip per backup), a known segment id, or "~name" for a custom;
           resolved against the SENT layout at call time - a choice whose
           level got unticked or removed falls back to "data", and the button
           label shows the resolved truth. */
        function zipChoice() {
          return String((BV.state.settings || {}).export_zip_at || "data");
        }
        function zipAtIndex() {
          if (!boolOpt("export_zip")) return null;
          var want = zipChoice();
          if (want === "data") return -1;
          var arr = segsOn();
          for (var i = 0; i < arr.length; i++) {
            if (typeof arr[i] === "string" ? arr[i] === want
                : ("~" + arr[i].custom) === want) return i;
          }
          return -1;
        }
        var dev = (BV.state.settings || {}).export_devices;
        if (["both", "robot", "camera"].indexOf(dev) < 0) dev = "both";

        /* ---- top: knobs+filters · layout chips · destination+options ---- */
        var top = BV.el("div", { class: "mb-exptop" });
        var t1 = BV.el("div", { class: "mb-cleanrow" });
        t1.appendChild(knobEl("copy the newest", "export_count",
          EXPORT_COUNT_DEFAULT, "completed backups per robot", 1, refreshPlan));
        /* the age window: unchecked = any age */
        var dLab = BV.el("label", { class: "scan-selall mb-opt-days",
          title: "only snapshots taken inside this window export; older ones stay home" });
        var dCb = BV.el("input", { type: "checkbox", class: "lf-check" });
        dCb.checked = boolOpt("export_days_on");
        var dInp = BV.el("input", { class: "mb-stale-days", type: "text", spellcheck: "false" });
        dInp.value = String(intSetting("export_days", EXPORT_DAYS_DEFAULT));
        dInp.disabled = !dCb.checked;
        dCb.addEventListener("change", function () {
          saveSetting("export_days_on", dCb.checked);
          dInp.disabled = !dCb.checked;
          refreshPlan();
        });
        var dT = null;
        dInp.addEventListener("input", function () {
          var v = parseInt(dInp.value, 10);
          if (!(v >= 1)) return;
          saveSetting("export_days", v);
          if (dT) clearTimeout(dT);
          dT = setTimeout(refreshPlan, 350);
        });
        dLab.appendChild(dCb);
        dLab.appendChild(BV.el("span", null, "only the last"));
        dLab.appendChild(dInp);
        dLab.appendChild(BV.el("span", null, "days"));
        t1.appendChild(dLab);
        /* robots, cameras, or the whole fleet */
        var devSeg = BV.segmented([
          { id: "both", label: "both" },
          { id: "robot", label: "robots" },
          { id: "camera", label: "cameras" },
        ], { value: dev, onChange: function (id) {
          dev = id;
          saveSetting("export_devices", id);
          repaintPicker();
          refreshPlan();
        } });
        devSeg.el.classList.add("mb-exp-devseg");
        t1.appendChild(devSeg.el);
        top.appendChild(t1);

        var t2 = BV.el("div", { class: "mb-cleanrow" });
        t2.appendChild(BV.el("span", { class: "mb-sum" }, "folders:"));
        var strip = BV.el("div", { class: "mb-seg-strip" });
        t2.appendChild(strip);
        var addBtn = BV.el("button", { class: "btn mb-seg-add",
          title: "add a fixed folder level with a name you type" }, "+");
        t2.appendChild(addBtn);
        /* where the files themselves land - not draggable, not omittable */
        var dataChip = BV.el("span", { class: "mb-seg mb-seg-fixed",
          title: "the backup's own files go inside the last folder" }, "backup data");
        t2.appendChild(dataChip);
        top.appendChild(t2);
        function paintDataChip() {
          dataChip.textContent = (boolOpt("export_zip") && zipAtIndex() === -1)
            ? "backup data → .zip" : "backup data";
        }
        /* the chip whose folder becomes the archive wears the marker */
        function markZipChip() {
          var idx = zipAtIndex();
          var uid = null;
          if (idx !== null && idx >= 0) {
            var n = -1;
            for (var i = 0; i < segs.length; i++) {
              if (segs[i].id === "custom" || segs[i].on) n++;
              if (n === idx) { uid = segs[i].uid; break; }
            }
          }
          [].forEach.call(strip.querySelectorAll(".mb-seg"), function (ch) {
            ch.classList.toggle("zip-here", ch.dataset.uid === uid);
          });
          dataChip.classList.toggle("zip-here", idx === -1);
          paintDataChip();
        }

        var addSeq = 0;
        var dr = BV.dragReorder({
          zones: [strip], itemSelector: ".mb-seg", axis: "x",
          onDrop: function () {
            var byUid = {};
            segs.forEach(function (s) { byUid[s.uid] = s; });
            segs = [].map.call(strip.querySelectorAll(".mb-seg"), function (ch) {
              return byUid[ch.dataset.uid];
            }).filter(Boolean);
            saveSegs();
            paintZipAt();
            refreshPlan();
          },
        });
        function buildStrip() {
          strip.innerHTML = "";
          segs.forEach(function (s) {
            var custom = s.id === "custom";
            var chip = BV.el("label", { class: "mb-seg" + (s.on ? "" : " off"),
              "data-uid": s.uid, title: custom
                ? "a fixed folder, the same name for every robot - drag to reorder"
                : "drag to reorder · untick to omit this level" });
            chip.appendChild(BV.el("span", { class: "mb-seg-grip" }, "⋮⋮"));
            if (custom) {
              chip.appendChild(BV.el("span", { class: "mb-seg-name" }, BV.esc(s.name)));
              /* customs carry ✕, not a checkbox: removal IS their off */
              var x = BV.el("button", { class: "mb-seg-x", title: "remove this folder" }, "✕");
              x.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                segs = segs.filter(function (o) { return o !== s; });
                saveSegs();
                buildStrip();
                paintZipAt();
                refreshPlan();
              });
              chip.appendChild(x);
            } else {
              var cb = BV.el("input", { type: "checkbox", class: "lf-check" });
              cb.checked = s.on;
              cb.addEventListener("change", function () {
                if (dr.isRecentDrag()) { cb.checked = s.on; return; }   /* sloppy drop */
                s.on = cb.checked;
                chip.classList.toggle("off", !s.on);
                saveSegs();
                paintZipAt();
                refreshPlan();
              });
              chip.appendChild(cb);
              chip.appendChild(BV.el("span", { class: "mb-seg-name" }, BV.esc(s.id)));
            }
            strip.appendChild(chip);
            dr.wire(chip);
          });
          markZipChip();
        }
        buildStrip();

        /* + : an inline name field (no modal-on-modal); Enter adds, esc backs
           out without closing the modal, blur just cancels */
        addBtn.addEventListener("click", function () {
          if (t2.querySelector(".mb-seg-new")) return;
          var inp = BV.el("input", { class: "mb-stale-days mb-seg-new", type: "text",
            spellcheck: "false", placeholder: "folder name" });
          t2.insertBefore(inp, addBtn);
          inp.focus();
          function done(commit) {
            var name = inp.value.trim();
            inp.remove();
            if (!commit || !name) return;
            /* a light local gate; the engine re-validates and is the authority */
            if (/[<>:"/\\|?*]/.test(name) || name === "." || name === "..") {
              BV.toast("not a usable folder name");
              return;
            }
            segs.push({ id: "custom", name: name, on: true, uid: "n" + (addSeq++) });
            saveSegs();
            buildStrip();
            paintZipAt();
            refreshPlan();
          }
          inp.addEventListener("keydown", function (e) {
            if (e.key === "Enter") done(true);
            else if (e.key === "Escape") { e.stopPropagation(); done(false); }
          });
          inp.addEventListener("blur", function () { done(false); });
        });

        var t3 = BV.el("div", { class: "mb-cleanrow" });
        var pickBtn = BV.el("button", { class: "btn",
          title: "where the copies go - a USB stick, a share, any folder outside the library" },
          "choose folder…");
        pickBtn.addEventListener("click", function () {
          BV.api.call("pick_export_dest").then(function (p) {
            if (!p) return;
            destPath = p;
            saveSetting("export_dir", p);
            paintDest();
            refreshPlan();
          }).catch(function (e) { BV.toast(e.message); });
        });
        t3.appendChild(pickBtn);
        var destEl = BV.el("span", { class: "mb-exp-dest" });
        t3.appendChild(destEl);
        var openBtn = BV.el("button", { class: "btn", title: "reveal the destination in Explorer" }, "open");
        openBtn.addEventListener("click", function () {
          BV.api.call("open_path", destPath).catch(function (e) { BV.toast(e.message); });
        });
        t3.appendChild(openBtn);
        function paintDest() {
          destEl.textContent = destPath || "no folder picked - the preview still shows the layout";
          destEl.title = destPath;
          destEl.classList.toggle("dim", !destPath);
          openBtn.style.display = destPath ? "" : "none";
        }
        paintDest();
        function optCheck(text, key, tip, extra) {
          var lab = BV.el("label", { class: "scan-selall mb-opt-" + key, title: tip });
          var cb = BV.el("input", { type: "checkbox", class: "lf-check" });
          cb.checked = boolOpt(key);
          cb.addEventListener("change", function () {
            saveSetting(key, cb.checked);
            if (extra) extra();
            refreshPlan();
          });
          lab.appendChild(cb);
          lab.appendChild(BV.el("span", null, text));
          return lab;
        }
        t3.appendChild(optCheck("include robot.json", "export_sidecar",
          "copy each robot's identity sidecar beside its folders - off = backup data only"));
        t3.appendChild(optCheck("zip", "export_zip",
          "the folder at the chosen level becomes a .zip archive - "
          + "its parent folders stay real folders", paintZipAt));
        /* which level: everything under the chosen folder goes inside its zip */
        var zipAtBtn = BV.el("button", { class: "btn mb-zipat",
          title: "which folder level becomes the archive - "
            + "everything under it goes inside" });
        zipAtBtn.addEventListener("click", function () {
          var items = [{ label: "backup data (one zip per backup)",
            onClick: function () { pickZipAt("data"); } }];
          segs.forEach(function (s) {
            if (s.id === "custom") {
              items.push({ label: s.name,
                onClick: function () { pickZipAt("~" + s.name); } });
            } else if (s.on) {
              items.push({ label: s.id,
                onClick: function () { pickZipAt(s.id); } });
            }
          });
          BV.menu(zipAtBtn, items);
        });
        t3.appendChild(zipAtBtn);
        function pickZipAt(v) {
          saveSetting("export_zip_at", v);
          paintZipAt();
          refreshPlan();
        }
        function paintZipAt() {
          var on = boolOpt("export_zip");
          zipAtBtn.style.display = on ? "" : "none";
          var idx = zipAtIndex();
          var lab = "backup data";
          if (idx !== null && idx >= 0) {
            var s = segsOn()[idx];
            lab = typeof s === "string" ? s : s.custom;
          }
          zipAtBtn.textContent = "at: " + lab + " ▾";
          markZipChip();
        }
        paintZipAt();
        top.appendChild(t3);
        slot.appendChild(top);

        /* ---- middle: robot picker | destination preview, own scrollers ---- */
        var main = BV.el("div", { class: "mb-exp-main" });
        slot.appendChild(main);

        var xcl = BV.checklist({ onChange: function () { refreshPlan(); updateBar(); } });
        var pick = BV.el("div", { class: "mb-exp-pick" });
        var ph = catHead("");
        var phTitle = ph.querySelector(".hs-cat-title");
        ph.appendChild(selAll(xcl, function () {
          return currentRobots().map(selKey);
        }, "xp-all"));
        pick.appendChild(ph);
        var pickHost = BV.el("div", { class: "mb-colfill" });
        pick.appendChild(pickHost);
        main.appendChild(pick);

        function isCam(r) { return (r.device_type || "").indexOf("camera") === 0; }
        function currentRobots() {
          if (dev === "robot") return robots.filter(function (r) { return !isCam(r); });
          if (dev === "camera") return robots.filter(isCam);
          return robots;
        }
        function selKey(r) { return r.last_backup ? r.id : ""; }
        function expRow(r) {
          var can = !!r.last_backup;
          var row = BV.el("label", { class: "mb-cl-row" + (can ? " hs-check" : " mb-exp-none") });
          if (can) {
            row.appendChild(xcl.bind(
              BV.el("input", { type: "checkbox", class: "lf-check" }), r.id));
          }
          row.appendChild(BV.el("span", { class: "hs-robot" }, BV.esc(r.robot || "(unnamed)")));
          if (isCam(r)) row.insertAdjacentHTML("beforeend", '<span class="pill acc">cam</span>');
          row.appendChild(BV.el("span", { class: "mb-cl-when" },
            can ? BV.esc("newest " + fmtWhen(r.last_backup))
                : "no completed backup"));
          return row;
        }
        function repaintPicker() {
          var rs = currentRobots();
          phTitle.textContent = ({ both: "robots + cameras", robot: "robots",
            camera: "cameras" })[dev] + countLabel(rs);
          pickHost.innerHTML = "";
          pickHost.appendChild(treeByPlantLine(rs, {
            key: "xp", cl: xcl, selKey: selKey, label: countLabel, row: expRow,
          }));
          /* a pick that fell out of the filter must never export invisibly */
          var visible = {};
          rs.forEach(function (r) { if (selKey(r)) visible[r.id] = true; });
          xcl.selected().forEach(function (k) {
            if (!visible[k]) xcl.set(k, false);
          });
          xcl.sync();
        }

        var prev = BV.el("div", { class: "mb-exp-prev" });
        prev.appendChild(catHead("preview"));
        var prevHost = BV.el("div", { class: "mb-colfill mb-exp-tree" });
        prev.appendChild(prevHost);
        main.appendChild(prev);

        /* ---- the plan: engine-computed, this side only renders it ---- */
        var debouncedPlan = BV.debounce(doPlan, 250);
        function refreshPlan() { debouncedPlan(); }
        function doPlan() {
          if (tab !== "export") return;
          var ids = xcl.selected();
          if (!ids.length) {
            lastPlan = null;
            prevHost.innerHTML = '<div class="mb-none">pick robots to preview the layout</div>';
            updateBar();
            return;
          }
          var seq = ++planSeq;
          BV.api.call("lib_export_plan", ids, countVal(), segsOn(), destPath,
                      daysVal(), boolOpt("export_sidecar"), zipAtIndex())
            .then(function (p) {
              if (seq !== planSeq || tab !== "export") return;
              lastPlan = p;
              paintPreview(p);
              updateBar();
            }).catch(function (e) {
              if (seq !== planSeq) return;
              lastPlan = null;
              prevHost.innerHTML = '<div class="mb-none">' + BV.esc(e.message) + "</div>";
              updateBar();
            });
        }

        var PREVIEW_CAP = 400;      /* rows drawn; the bar always counts ALL */
        function paintPreview(p) {
          prevHost.innerHTML = "";
          if (p.collisions.length) {
            prevHost.appendChild(BV.el("div", { class: "mb-exp-warn" },
              BV.esc(p.collisions.length + " folder" + (p.collisions.length === 1 ? "" : "s") +
                " would receive two backups at once - re-tick date/time, or copy fewer")));
          }
          if (lastFails.length) {
            var fbox = BV.el("div", { class: "mb-exp-fails" });
            lastFails.forEach(function (f) {
              fbox.appendChild(BV.el("div", null,
                BV.esc((f.robot || f.robot_id) + " " + f.taken + " — " + f.error)));
            });
            prevHost.appendChild(fbox);
          }
          /* nest rows by their rel parts. Plain mode: a row annotates the dir
             its rel names (files go INSIDE it). zip mode: the last part
             becomes a <name>.zip leaf under its parent dir (the plan already
             skipped any row with nothing to name an archive after). */
          var root = { dirs: {}, rows: [], zips: {} };
          p.rows.forEach(function (r) {
            var n = root;
            function into(part) {
              n = n.dirs[part] = n.dirs[part] || { dirs: {}, rows: [], zips: {} };
            }
            if (p.zip) {
              /* the archive's parents are real dirs; rows sharing an arel
                 are one archive's members and annotate one line together */
              var aparts = String(r.arel || "").split("/").filter(Boolean);
              var leaf = aparts.pop();
              aparts.forEach(into);
              (n.zips[leaf] = n.zips[leaf] || []).push(r);
            } else {
              String(r.rel || "").split("/").filter(Boolean).forEach(into);
              n.rows.push(r);
            }
          });
          var drawn = 0;
          var box = BV.el("div");
          function annotate(line, rows) {
            rows.forEach(function (r) {
              var a = BV.el("span", { class: "mb-exp-data" },
                BV.esc("← " + (r.robot || "?") + " · " + fmtWhen(r.taken) +
                  (r.bytes ? " · " + BV.fmt.bytes(r.bytes) : "")));
              if (r.collides) {
                line.classList.add("collide");
                a.insertAdjacentHTML("beforeend", BV.pill("collision", "err"));
              }
              if (r.exists) {
                line.classList.add("exists");
                a.insertAdjacentHTML("beforeend", BV.pill("already there", "warn"));
              }
              line.appendChild(a);
            });
          }
          /* every dir with children is a fold: left-click toggles it,
             right-click toggles the whole subtree (BV.collapsible's app-wide
             convention, free with the modal's fold() helper), state in the
             modal's shared per-open map */
          function draw(node, host, path) {
            Object.keys(node.zips).sort().forEach(function (name) {
              if (drawn >= PREVIEW_CAP) return;
              drawn++;
              var line = BV.el("div", { class: "mb-exp-dir mb-exp-zipleaf" });
              line.appendChild(BV.el("span", { class: "n" }, BV.esc(name + ".zip")));
              annotate(line, node.zips[name]);
              host.appendChild(line);
            });
            Object.keys(node.dirs).sort().forEach(function (name) {
              if (drawn >= PREVIEW_CAP) return;
              drawn++;
              var child = node.dirs[name];
              var line = BV.el("div", { class: "mb-exp-dir" });
              line.appendChild(BV.el("span", { class: "n" }, BV.esc(name + "/")));
              annotate(line, child.rows);
              if (Object.keys(child.dirs).length + Object.keys(child.zips).length) {
                var body = BV.el("div");
                draw(child, body, path + "/" + name);
                host.appendChild(fold("pv|" + path + "/" + name, line, body, true));
              } else {
                host.appendChild(line);
              }
            });
          }
          if (destPath || root.rows.length) {
            var head = BV.el("div", { class: "mb-exp-dir mb-exp-root" },
              BV.esc(destPath || "(destination)") + " ");
            annotate(head, root.rows);           /* every-segment-off rows land here */
            box.appendChild(head);
          }
          draw(root, box, "");
          if (drawn >= PREVIEW_CAP) {
            box.appendChild(BV.el("div", { class: "mb-none" },
              "…the preview stops at " + PREVIEW_CAP + " folders - the export does not"));
          }
          prevHost.appendChild(box);
          if (p.skipped.length) {
            var sk = BV.el("div", { class: "mb-exp-skip" });
            p.skipped.forEach(function (s) {
              sk.appendChild(BV.el("div", null,
                BV.esc((s.robot || s.robot_id) + " — " + s.reason)));
            });
            prevHost.appendChild(sk);
          }
        }

        /* ---- chrome: the export bar (silent until something is picked) ---- */
        var bar = BV.el("div", { class: "mb-stagebar" });
        var sum = BV.el("span", { class: "mb-sum" }, "");
        var cancelBtn = BV.el("button", { class: "btn",
          title: "stop before the next file - finished copies stay" }, "cancel");
        cancelBtn.style.display = "none";
        cancelBtn.addEventListener("click", function () {
          cancelBtn.disabled = true;
          cancelBtn.textContent = "cancelling…";
          BV.api.call("lib_export_cancel").catch(function () {});
        });
        var exp = BV.el("button", { class: "btn",
          title: "COPY the previewed backups to the destination - sources are " +
            "only read; folders already holding data are skipped" }, "export");
        exp.disabled = true;
        bar.appendChild(sum);
        bar.appendChild(cancelBtn);
        bar.appendChild(exp);
        slot.appendChild(bar);

        var armedUntil = 0;
        function resetExp() {
          armedUntil = 0;
          exp.textContent = "export";
          exp.classList.remove("armed");
        }
        function toCopy() {
          /* display arithmetic over engine rows, never a re-derived verdict */
          if (!lastPlan) return { n: 0, b: 0, unsized: 0 };
          var n = 0, b = 0, u = 0;
          lastPlan.rows.forEach(function (r) {
            if (r.exists) return;
            n++;
            if (r.bytes) b += r.bytes; else u++;
          });
          return { n: n, b: b, unsized: u };
        }
        function updateBar() {
          if (running) return;
          var ids = xcl.selected();
          var c = toCopy();
          var bits = [];
          if (lastPlan && ids.length) {
            bits.push(c.n + " backup" + (c.n === 1 ? "" : "s") + " · " +
              (c.unsized ? "~" : "") + BV.fmt.bytes(c.b));
            if (lastPlan.totals.existing) {
              bits.push(lastPlan.totals.existing + " already there");
            }
            if (lastPlan.collisions.length) bits.push("resolve the collisions first");
            else if (!destPath) bits.push("pick a destination folder");
          }
          sum.textContent = bits.join(" · ");
          exp.disabled = !(lastPlan && !lastPlan.collisions.length && destPath && c.n > 0);
          resetExp();
        }

        exp.addEventListener("click", function () {
          if (running || exp.disabled) return;
          var c = toCopy();
          if (Date.now() > armedUntil) {      /* two clicks, no modal-on-modal */
            armedUntil = Date.now() + 4000;
            exp.textContent = "confirm export (" + c.n.toLocaleString() + ")";
            exp.classList.add("armed");
            setTimeout(function () {
              if (armedUntil && Date.now() > armedUntil && !running) resetExp();
            }, 4300);
            return;
          }
          running = true;
          lastFails = [];
          exp.disabled = true;
          exp.classList.remove("armed");
          exp.textContent = "exporting… 0/" + c.n.toLocaleString();
          cancelBtn.disabled = false;
          cancelBtn.textContent = "cancel";
          cancelBtn.style.display = "";
          runJob(BV.api.call("lib_export", xcl.selected(), countVal(), segsOn(), destPath,
                             daysVal(), boolOpt("export_sidecar"), zipAtIndex()),
                 "lib_export_progress", function (p) {
            exp.textContent = "exporting… " + p.done.toLocaleString() +
              "/" + p.total.toLocaleString() +
              (p.bytes ? " · " + BV.fmt.bytes(p.bytes) : "");
          }).then(function (res) {
            res = res || { exported: [], failed: [], skipped: [] };
            running = false;
            cancelBtn.style.display = "none";
            lastFails = res.failed || [];
            var ne = (res.exported || []).length;
            BV.toast("exported " + ne + " backup" + (ne === 1 ? "" : "s") +
              ((res.skipped || []).length ? " · " + res.skipped.length + " skipped" : "") +
              (lastFails.length ? " · " + lastFails.length + " failed" : "") +
              (res.cancelled ? " · cancelled" : ""));
            resetExp();
            doPlan();               /* repaint: fresh copies now read "already there" */
          }).catch(function (e) {
            running = false;
            cancelBtn.style.display = "none";
            BV.toast(e.message);
            resetExp();
            updateBar();
          });
        });

        repaintPicker();
        doPlan();
      }

      build();
    },
  };
})();
