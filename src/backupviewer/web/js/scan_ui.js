/* scan_ui.js - the library's fleet scan: pick the robots in the library, hit
   "scan", pick what to look for (advanced DCS, signature mismatches, unused
   S## programs, free-text finds...), and read back one report across the
   whole selection. Backend: healthscan.py via health_checks /
   health_scan_start, polled with the shared scan_progress / cancel_scan.

   Three views swapped inside one modal: pick -> running -> report. Checks are
   fetched from the backend registry, so a new check in healthscan.py shows up
   here with zero UI work; the registry's "category" field groups the picker
   under plain headers (no accordions - everything stays visible). The picked
   check ids persist in settings ("scan_checks", default NOTHING selected);
   find queries are add-to-list chips, each its own report section. */
(function () {
  "use strict";

  var _lastQueries = [];   /* find chips survive close/reopen within this app run */

  var PILL = { flag: ["flag", "err"], info: ["info", "acc"], ok: ["ok", "ok-soft"], na: ["n/a", "ghost"] };
  var ORDER = { flag: 0, info: 1, ok: 2, na: 3 };
  /* where a result row lands when clicked - the tab that shows that check's data */
  var TARGET = {
    adv_dcs: "#overview", sig_mismatch: "#dcs", cip_safety: "#dcs",
    mastering: "#overview", cloned_mastering: "#overview", battery_alarm: "#overview",
    style_broken: "#programs", style_orphans: "#programs", broken_calls: "#programs",
    remarked_positions: "#programs", remarked_logic: "#programs",
    pause_used: "#programs", cnt_logic: "#programs",
    uninit_points: "#programs", uninit_prs: "#registers",
    software_version: "#overview", payload_unset: "#frames",
    override_low: "#sysvars", clock_drift: "#overview",
  };

  function pill(status) {
    var p = PILL[status] || PILL.na;
    return BV.pill(p[0], p[1]);
  }

  function stamp(when) {
    var d = new Date(when);
    var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    var today = new Date();
    return d.toDateString() === today.toDateString()
      ? hm
      : d.getDate() + "-" + ["jan", "feb", "mar", "apr", "may", "jun", "jul",
                             "aug", "sep", "oct", "nov", "dec"][d.getMonth()] + " " + hm;
  }

  function stemUp(name) {
    return String(name || "").replace(/\.[Ll][Ss]$/, "").toUpperCase();
  }

  /* a section rule that survives being pasted into chat/mail: fixed width,
     the label centred in it, so sections stay findable in a long paste */
  function banner(label) {
    var text = " [" + label + "] ";
    var pad = Math.max(0, 62 - text.length);
    var left = Math.floor(pad / 2);
    return new Array(left + 1).join("-") + text +
           new Array(pad - left + 1).join("-");
  }

  /* resolve (robot_id -> wanted program names) into workspace entries and add
     them; every add-to-editor path in the report funnels through here */
  function addProgsToEditor(pairs) {
    var byRobot = {};
    pairs.forEach(function (p) {
      if (!p.robot_id || !p.prog) return;
      var b = byRobot[p.robot_id] = byRobot[p.robot_id] || {};
      b[p.prog.toUpperCase()] = true;
    });
    var ids = Object.keys(byRobot);
    if (!ids.length) { BV.toast("nothing to add"); return; }
    BV.toast("reading " + ids.length + " backup" + (ids.length === 1 ? "" : "s") + "…");
    Promise.all(ids.map(function (id) {
      return BV.api.call("ws_robot_programs", id).then(function (res) {
        var want = byRobot[id];
        var entries = (res.programs || []).filter(function (p) {
          return want[stemUp(p.name)];
        }).map(function (p) {
          return { root: res.root, label: res.label, file: p.file, name: p.name };
        });
        return BV.workspace.addMany(entries);
      }).catch(function () { return null; });   /* one unopenable backup: skip, count below */
    })).then(function (counts) {
      var n = 0, failed = 0;
      counts.forEach(function (c) { if (c === null) failed++; else n += c; });
      BV.toast("added " + n + " program" + (n === 1 ? "" : "s") + " to the workspace" +
        (failed ? " · " + failed + " backup" + (failed === 1 ? "" : "s") + " unreadable" : "") +
        (n ? " — open it from the topbar anvil" : ""), 4000);
    });
  }

  BV.scanUI = {
    open: function (robots) {
      var host = BV.el("div", { class: "hs-host" });
      var stop = null;          /* live poller's stop() while a scan runs */
      var jobId = null;
      var modal = BV.modal("scan " + robots.length + " robot" + (robots.length === 1 ? "" : "s"),
        host, {
          onClose: function () {
            if (stop) stop();
            if (jobId) BV.api.call("cancel_scan", jobId).catch(function () {});
          },
        });
      modal.el.classList.add("hs-modal");   /* reports need width - see .hs-modal */

      /* ---- view 1: pick checks (grouped by category) + find chips ---- */
      function pickView(checks) {
        host.innerHTML = "";
        host.appendChild(BV.el("div", { class: "hs-info" },
          "scans each robot's saved backup — nothing touches the network"));

        /* one shared checklist controller = the same select-all / tri-state
           behavior as every other list in the app. Default = NOTHING picked;
           the saved settings selection carries across app runs. */
        var cl = BV.checklist();
        ((BV.state.settings && BV.state.settings.scan_checks) || []).forEach(function (id) {
          cl.set(id, true);
        });

        var cats = [], byCat = {};
        checks.forEach(function (c) {
          var k = c.category || "checks";
          if (!byCat[k]) { byCat[k] = []; cats.push(k); }
          byCat[k].push(c);
        });

        /* per-check inputs (the clock tolerance): the registry declares them,
           values persist in settings (scan_params) like the picks do */
        var paramInputs = {};
        var savedParams = (BV.state.settings && BV.state.settings.scan_params) || {};

        var wrap = BV.el("div", { class: "hs-cats" });
        cats.forEach(function (cat) {
          var block = BV.el("div", { class: "hs-cat" });
          var head = BV.el("div", { class: "hs-cat-head" });
          head.appendChild(BV.el("span", { class: "hs-cat-title" }, BV.esc(cat)));
          var sel = BV.el("label", { class: "scan-selall", title: "select all / clear " + cat });
          sel.appendChild(cl.group(BV.el("input", { type: "checkbox", class: "lf-check" }),
            function () { return byCat[cat].map(function (c) { return c.id; }); }, cat));
          sel.appendChild(BV.el("span", null, "all"));
          head.appendChild(sel);
          block.appendChild(head);
          byCat[cat].forEach(function (c) {
            var row = BV.el("label", { class: "hs-check" });
            row.appendChild(cl.bind(BV.el("input", { type: "checkbox", class: "lf-check" }), c.id));
            row.appendChild(BV.el("div", null,
              '<div class="hs-lbl">' + BV.esc(c.label) + "</div>" +
              '<div class="hs-desc">' + BV.esc(c.desc) + "</div>"));
            if (c.input) {
              var pin = BV.el("input", { type: "text", class: "hs-param", spellcheck: "false",
                placeholder: c.input.hint || "", title: c.input.label || "" });
              pin.value = savedParams[c.id] !== undefined
                ? savedParams[c.id] : (c.input.default || "");
              /* a text input inside the row's <label> must never toggle the box */
              pin.addEventListener("click", function (e) { e.stopPropagation(); });
              paramInputs[c.id] = pin;
              row.appendChild(pin);
            }
            block.appendChild(row);
          });
          wrap.appendChild(block);
        });
        host.appendChild(wrap);
        cl.sync();               /* reflect the restored picks onto the boxes */

        /* find: its own block - a list of query chips, each one becoming its
           own report section. Enter adds; whatever's left in the input when
           scan is clicked rides along (type-one-thing-then-scan is the
           common case - never force Enter). */
        var findBlock = BV.el("div", { class: "hs-cat hs-findblock" });
        var fhead = BV.el("div", { class: "hs-cat-head" });
        fhead.appendChild(BV.el("span", { class: "hs-cat-title" }, "find"));
        fhead.appendChild(BV.el("span", { class: "hs-desc" },
          "each query gets its own report section"));
        findBlock.appendChild(fhead);
        var queries = _lastQueries.slice();
        var chips = BV.el("div", { class: "hs-chips" });
        function renderChips() {
          chips.innerHTML = "";
          queries.forEach(function (q, i) {
            var chip = BV.el("span", { class: "hs-chip" });
            chip.appendChild(BV.el("span", { class: "hs-chip-q" }, BV.esc(q)));
            var x = BV.el("button", { class: "hs-chip-x", title: "remove this query" },
              String.fromCharCode(0x2715));
            x.addEventListener("click", function () {
              queries.splice(i, 1);
              renderChips();
            });
            chip.appendChild(x);
            chips.appendChild(chip);
          });
        }
        renderChips();
        findBlock.appendChild(chips);
        var findRow = BV.el("div", { class: "hs-find" });
        var q = BV.el("input", { type: "text", spellcheck: "false",
          placeholder: "optional — DI[279], R[151], a program name… Enter adds it" });
        findRow.appendChild(q);
        findBlock.appendChild(findRow);
        host.appendChild(findBlock);

        /* true = the input text is now IN the list (added or already there) */
        function addQuery(text, silent) {
          var v = (text || "").trim();
          if (!v) return false;
          var dup = queries.some(function (x) { return x.toLowerCase() === v.toLowerCase(); });
          if (dup) {
            if (!silent) BV.toast("already in the list");
            return true;
          }
          queries.push(v);
          renderChips();
          return true;
        }
        q.addEventListener("keydown", function (e) {
          if (e.key === "Enter" && addQuery(q.value)) q.value = "";
        });

        var actions = BV.el("div", { class: "lf-actions" });
        var go = BV.el("button", { class: "btn primary" }, "scan");
        var closeBtn = BV.el("button", { class: "btn" }, "close");
        closeBtn.addEventListener("click", function () { modal.close(); });
        actions.appendChild(go);
        actions.appendChild(closeBtn);
        host.appendChild(actions);

        go.addEventListener("click", function () {
          if (addQuery(q.value, true)) q.value = "";     /* pending text rides along */
          var picked = checks.map(function (c) { return c.id; })
            .filter(function (id) { return cl.has(id); });
          if (!picked.length && !queries.length) {
            BV.toast("pick at least one check (or add a find)");
            return;
          }
          _lastQueries = queries.slice();
          /* persist everything typed (picked or not), send only the picked */
          var allParams = {}, params = {};
          Object.keys(paramInputs).forEach(function (id) {
            allParams[id] = paramInputs[id].value.trim();
            if (cl.has(id)) params[id] = allParams[id];
          });
          if (BV.state.settings) {
            BV.state.settings.scan_checks = picked;
            BV.state.settings.scan_params = allParams;
          }
          BV.api.call("set_setting", "scan_checks", picked).catch(function () {});
          BV.api.call("set_setting", "scan_params", allParams).catch(function () {});
          go.disabled = true;    /* no double-submit */
          BV.api.call("health_scan_start", robots.map(function (r) { return r.id; }),
            picked, queries, params)
            .then(function (res) { runView(res.job_id, res.total, checks, queries.slice()); })
            .catch(function (e) { go.disabled = false; BV.toast(e.message); });
        });
      }

      /* ---- view 2: progress ---- */
      function runView(id, total, checks, queries) {
        jobId = id;
        host.innerHTML = "";
        var bar = BV.el("div");
        var current = BV.el("div", { class: "bf-current dim" });
        host.appendChild(bar);
        host.appendChild(current);
        var actions = BV.el("div", { class: "lf-actions" });
        var cancelBtn = BV.el("button", { class: "btn" }, "cancel");
        cancelBtn.addEventListener("click", function () {
          BV.api.call("cancel_scan", id).catch(function () {});
        });
        actions.appendChild(cancelBtn);
        host.appendChild(actions);

        function paint(p) {
          var pct = p.total ? Math.round((p.scanned / p.total) * 100) : 0;
          var flags = 0;
          (p.results || []).forEach(function (r) {
            (r.checks || []).forEach(function (c) { if (c.status === "flag") flags++; });
          });
          bar.innerHTML = '<div class="membar"><div class="mb-label"><span>scanning</span><span>' +
            p.scanned + " / " + (p.total || total) +
            (flags ? " · " + flags + " flag" + (flags === 1 ? "" : "s") : "") + "</span></div>" +
            '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div></div>';
          current.textContent = p.current || "";
        }

        /* setTimeout chain so a slow snapshot can never stack polls */
        var live = true;
        stop = function () { live = false; };
        function tick() {
          if (!live) return;
          BV.api.call("scan_progress", id).then(function (p) {
            if (!live) return;
            paint(p);
            if (p.status === "done") { stop(); jobId = null; reportView(p.results, checks, queries); }
            else if (p.status === "cancelled") { stop(); jobId = null; BV.toast("scan cancelled"); pickView(checks); }
            else if (p.status === "error") { stop(); jobId = null; BV.toast("scan failed: " + (p.error || "?")); pickView(checks); }
            else setTimeout(tick, 400);
          }).catch(function (e) { stop(); jobId = null; BV.toast(e.message); pickView(checks); });
        }
        paint({ scanned: 0, total: total });
        tick();
      }

      /* ---- view 3: the report (a tree, filtered by report-scoped ignores) ---- */
      function reportView(results, checks, queries, keep) {
        /* the finished report is KEPT module-wide with its view filters, so
           closing the modal loses nothing; a fresh scan replaces it */
        var rep = keep || { results: results, checks: checks, queries: queries,
                            when: Date.now(),
                            flt: { rows: {}, items: {}, progs: {}, progRows: {} } };
        _lastScan = rep;
        results = rep.results;
        checks = rep.checks;
        queries = rep.queries;
        var flt = rep.flt;
        flt.progRows = flt.progRows || {};
        /* what is EXPANDED lives on the report too: a filter repaint (or a
           last-scan reopen) rebuilds to exactly the view being worked in -
           losing your place to every right-click made the filters unusable */
        var view = rep.view = rep.view || { secs: {}, rows: {}, progs: {} };

        /* -- filter model: everything is a VIEW filter on this report only -- */
        function rowKey(r, c) { return (r.robot_id || r.robot || "?") + "|" + c.id; }
        function itemKey(r, c, it) {
          return rowKey(r, c) + "|" + (it.prog || "") + "|" + (it.line || "") + "|" + (it.text || "");
        }
        function visibleItems(r, c) {
          return (c.items || []).filter(function (it) {
            if (it.prog && flt.progs[it.prog.toUpperCase()]) return false;
            if (it.prog && flt.progRows[rowKey(r, c) + "|" + it.prog.toUpperCase()]) return false;
            return !flt.items[itemKey(r, c, it)];
          });
        }
        function rowHidden(r, c) {
          if (flt.rows[rowKey(r, c)]) return true;
          /* a row whose every finding is filtered away goes with them; rows
             without structured items hide only by their own ignore */
          return !!(c.items || []).length && !visibleItems(r, c).length;
        }
        function filtersActive() {
          return Object.keys(flt.rows).length + Object.keys(flt.items).length +
                 Object.keys(flt.progs).length + Object.keys(flt.progRows).length;
        }
        /* FINDINGS, not rows: what the headers and the copy must both report,
           so a filtered report never quotes its pre-filter numbers */
        function tally(rows) {
          var t = { shown: 0, total: 0 };
          rows.forEach(function (x) {
            var all = (x.c.items || []).length;
            if (!all) return;              /* item-less rows count as rows only */
            t.total += all;
            t.shown += rowHidden(x.r, x.c) ? 0 : visibleItems(x.r, x.c).length;
          });
          return t;
        }
        function allRows() {
          var out = [];
          buildSecs().forEach(function (sec) { out = out.concat(secRows(sec)); });
          return out;
        }
        /* the row's headline, honest about filtering: the backend summary is
           prose we must not rewrite, so the shown-count rides beside it */
        function shownNote(r, c) {
          var all = (c.items || []).length;
          if (!all) return "";
          var vis = visibleItems(r, c).length;
          return vis === all ? "" : vis + " of " + all + " shown";
        }

        function progGroups(items) {
          var order = [], by = {};
          items.forEach(function (it) {
            var k = (it.prog || "(no program)").toUpperCase();
            if (!by[k]) { by[k] = { prog: it.prog || "(no program)", items: [] }; order.push(k); }
            by[k].items.push(it);
          });
          return order.map(function (k) { return by[k]; });
        }

        /* sections in registry order; each find query rides last, in order */
        function buildSecs() {
          var secs = (checks || []).filter(function (c) {
            return results.some(function (r) {
              return (r.checks || []).some(function (x) { return x.id === c.id; });
            });
          }).map(function (c) { return { id: c.id, label: c.label }; });
          (queries || []).forEach(function (fq, i) {
            secs.push({ id: "find:" + i, label: 'find: "' + fq + '"', query: fq });
          });
          return secs;
        }
        function secRows(sec) {
          var rows = [];
          results.forEach(function (r) {
            (r.checks || []).forEach(function (x) {
              if (x.id === sec.id) rows.push({ r: r, c: x });
            });
          });
          rows.sort(function (a, b) {
            var d = (ORDER[a.c.status] || 9) - (ORDER[b.c.status] || 9);
            return d || (a.r.robot || "").localeCompare(b.r.robot || "");
          });
          return rows;
        }

        function openBackup(r, c, sec) {
          BV.api.call("lib_open", r.robot_id, "latest").then(function (m) {
            BV.session.open(m);
            BV.state.setManifest(m);
            modal.close(true);
            location.hash = sec && sec.query
              ? "#search/" + encodeURIComponent(sec.query)
              : (TARGET[c.id] || "#overview");
          }).catch(function (e) { BV.toast(e.message); });
        }

        /* right-click is the ACTION layer; what it offers depends on what was
           hit. `it` = one finding line, `prog` = a program header (with its
           visible count `progN`). */
        function rowMenu(at, r, c, sec, it, prog, progN) {
          var out = [];
          if (it) {
            out.push({ label: "ignore this finding", onClick: function () {
              flt.items[itemKey(r, c, it)] = true;
              repaint();
            } });
          } else if (prog) {
            /* the group's own ignore: hides THIS program's findings on THIS
               robot only - exclude below is the every-robot sweep */
            out.push({ label: progN === 1 ? "ignore this finding"
                              : "ignore these " + progN + " findings",
              onClick: function () {
                flt.progRows[rowKey(r, c) + "|" + prog.toUpperCase()] = true;
                repaint();
              } });
          } else {
            out.push({ label: "ignore flag", onClick: function () {
              flt.rows[rowKey(r, c)] = true;
              repaint();
            } });
          }
          var progName = prog || (it && it.prog) || null;
          if (progName && progName !== "(no program)") {
            out.push({ label: "exclude program " + progName + " from scan", danger: true,
              onClick: function () {
                flt.progs[progName.toUpperCase()] = true;
                repaint();
              } });
            out.push({ label: "add " + progName + " to editor", onClick: function () {
              addProgsToEditor([{ robot_id: r.robot_id, prog: progName }]);
            } });
          }
          out.push({ label: "open this backup", onClick: function () { openBackup(r, c, sec); } });
          BV.menu(at, out);
        }

        host.innerHTML = "";
        var head = BV.el("div", { class: "hs-report-head" });
        var headInfo = BV.el("span", { class: "hs-info" });
        head.appendChild(headInfo);
        var resetBtn = BV.el("button", { class: "btn hs-reset",
          title: "bring back everything ignored or excluded in this view" });
        var quietBtn = BV.el("button", { class: "btn", title: "show or hide the ok / n/a rows" },
          "show all");
        var expandBtn = BV.el("button", { class: "btn", title: "open every robot's findings at once" },
          "expand all");
        var copyBtn = BV.el("button", { class: "btn",
          title: "copy every finding, line by line — follows the current filters" },
          "copy full");
        var listBtn = BV.el("button", { class: "btn",
          title: "copy the short list: robot, count, and which programs — " +
                 "the version you paste to someone" }, "copy list");
        var againBtn = BV.el("button", { class: "btn", title: "back to the check picker" }, "scan again");
        head.appendChild(resetBtn);
        head.appendChild(quietBtn);
        head.appendChild(expandBtn);
        head.appendChild(listBtn);
        head.appendChild(copyBtn);
        head.appendChild(againBtn);
        host.appendChild(head);

        var list = BV.el("div", { class: "hs-results hide-quiet" });
        host.appendChild(list);

        var allOpen = false;
        quietBtn.addEventListener("click", function () {
          var hidden = list.classList.toggle("hide-quiet");
          quietBtn.textContent = hidden ? "show all" : "flags only";
        });
        expandBtn.addEventListener("click", function () {
          allOpen = !allOpen;
          expandBtn.textContent = allOpen ? "collapse all" : "expand all";
          list.querySelectorAll(".hs-row").forEach(function (row) {
            if (!row.querySelector(".hs-rowbody")) return;
            setRowOpen(row, allOpen);
            if (row._bvKey) {
              if (allOpen) view.rows[row._bvKey] = true;
              else delete view.rows[row._bvKey];
            }
          });
        });
        resetBtn.addEventListener("click", function () {
          rep.flt = flt = { rows: {}, items: {}, progs: {}, progRows: {} };
          repaint();
          saveLast();
        });
        copyBtn.addEventListener("click", function () {
          BV.copyText(reportText(), "full report copied");
        });
        listBtn.addEventListener("click", function () {
          BV.copyText(quickText(), "list copied");
        });
        againBtn.addEventListener("click", function () { pickView(checks); });

        function setRowOpen(row, open) {
          var b = row.querySelector(".hs-rowbody");
          if (!b) return;
          b.style.display = open ? "" : "none";
          row.classList.toggle("open", open);
          var ca = row.querySelector(".hs-rowhead .caret");
          if (ca && ca.textContent) ca.textContent = open ? "▾" : "▸";
        }

        function resultRow(r, c, sec) {
          var rk = rowKey(r, c);
          var vis = visibleItems(r, c);
          var hasTree = vis.length > 0;
          var expandable = hasTree || !!c.detail;
          var open = expandable && !!view.rows[rk];
          var row = BV.el("div", { class: "hs-row st-" + c.status + (open ? " open" : "") });
          row._bvKey = rk;    /* a JS property, never a data attribute (ids
                                 are not attribute-safe) */
          var total = (c.items || []).length;
          var headEl = BV.el("div",
            { class: "hs-rowhead" + (expandable ? " expandable" : ""),
              title: expandable ? "expand · right-click for actions" : "right-click for actions" },
            '<span class="caret">' + (expandable ? (open ? "▾" : "▸") : "") + "</span>" +
            pill(c.status) +
            '<span class="hs-robot">' + BV.esc(r.robot || "(unnamed)") + "</span>" +
            (r.line ? '<span class="hs-line">' + BV.esc(r.line) + "</span>" : "") +
            '<span class="hs-sum">' + BV.esc(c.summary || "") + "</span>" +
            (shownNote(r, c)
              ? '<span class="hs-shown">' + shownNote(r, c) + "</span>" : ""));
          row.appendChild(headEl);

          var bodyEl = null;
          if (hasTree) {
            bodyEl = BV.el("div", { class: "hs-rowbody" });
            progGroups(vis).forEach(function (g) {
              var pk = rk + "|" + g.prog.toUpperCase();
              var folded = !!view.progs[pk];
              var pg = BV.el("div", { class: "hs-prog" });
              var pb = BV.el("div", { class: "hs-prog-b" });
              if (folded) pb.style.display = "none";
              var ph = BV.el("div", { class: "hs-prog-h",
                title: "fold · right-click for program actions" },
                '<span class="caret">' + (folded ? "▸" : "▾") + '</span><span class="nm">' +
                BV.esc(g.prog) + "</span>" +
                '<span class="n">' + g.items.length + "</span>");
              ph.addEventListener("click", function (ev) {
                ev.stopPropagation();
                var wasFolded = pb.style.display === "none";
                pb.style.display = wasFolded ? "" : "none";
                ph.querySelector(".caret").textContent = wasFolded ? "▾" : "▸";
                if (wasFolded) delete view.progs[pk];
                else view.progs[pk] = true;
                saveLast();
              });
              ph.addEventListener("contextmenu", function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                rowMenu({ x: ev.clientX, y: ev.clientY }, r, c, sec, null, g.prog, g.items.length);
              });
              g.items.forEach(function (it) {
                /* the program NAME is the whole finding for some checks
                   (unused S## programs) - no empty row under the header */
                if (!it.line && !it.text && !it.after) return;
                var ln = BV.el("div", { class: "hs-item", title: "right-click for actions" },
                  '<span class="ln">' + (it.line ? "line " + it.line : "") + "</span>" +
                  '<span class="tx">' + BV.esc(it.text || "") + "</span>" +
                  (it.after ? '<span class="af">→ ' + BV.esc(it.after) + "</span>" : ""));
                ln.addEventListener("contextmenu", function (ev) {
                  ev.preventDefault();
                  ev.stopPropagation();
                  rowMenu({ x: ev.clientX, y: ev.clientY }, r, c, sec, it, null);
                });
                pb.appendChild(ln);
              });
              pg.appendChild(ph);
              pg.appendChild(pb);
              bodyEl.appendChild(pg);
            });
          } else if (c.detail && !(c.items || []).length) {
            /* only a findings-less row falls back to prose - a row whose
               findings were all filtered away is already hidden, and one
               with survivors shows THEM, never the pre-filter blob */
            bodyEl = BV.el("div", { class: "hs-rowbody" });
            bodyEl.appendChild(BV.el("div", { class: "hs-detail" }, BV.esc(c.detail)));
          }
          if (bodyEl) {
            bodyEl.style.display = open ? "" : "none";
            row.appendChild(bodyEl);
            headEl.addEventListener("click", function () {
              var nowOpen = bodyEl.style.display === "none";
              setRowOpen(row, nowOpen);
              if (nowOpen) view.rows[rk] = true;
              else delete view.rows[rk];
              saveLast();
            });
          }
          headEl.addEventListener("contextmenu", function (ev) {
            ev.preventDefault();
            rowMenu({ x: ev.clientX, y: ev.clientY }, r, c, sec, null, null);
          });
          return row;
        }

        function repaint() {
          /* a repaint is a FILTER change, never a navigation: hold the scroll
             so the view stays under the pointer that right-clicked */
          var mb = modal.el.querySelector(".modal-body");
          var keepScroll = mb ? mb.scrollTop : 0;
          list.innerHTML = "";
          var flags = 0, hiddenN = filtersActive();
          buildSecs().forEach(function (sec) {
            var all = secRows(sec);
            var rows = all.filter(function (x) { return !rowHidden(x.r, x.c); });
            if (!rows.length) return;
            var counts = { flag: 0, info: 0, ok: 0, na: 0 };
            rows.forEach(function (x) { counts[x.c.status] = (counts[x.c.status] || 0) + 1; });
            flags += counts.flag;
            /* the section's own findings tally, measured over EVERY row it
               has (hidden ones included) so "N of M" tells the whole truth */
            var st = tally(all);

            var node = BV.el("div", { class: "hs-sec" });
            var headEl = BV.el("div", { class: "hs-sec-head",
              title: "right-click to act on the whole section" },
              '<span class="hs-sec-title">' + BV.esc(sec.label) + "</span>" +
              '<span class="hs-sec-counts">' +
              (st.total && st.shown !== st.total
                ? '<span class="hs-shown">' + st.shown + " of " + st.total + " findings</span>"
                : (st.total ? '<span class="dim">' + st.total + " findings</span>" : "")) +
              (counts.flag ? BV.pill(counts.flag + " flag", "err") : "") +
              (counts.info ? BV.pill(counts.info + " info", "acc") : "") +
              (counts.ok ? '<span class="dim">' + counts.ok + " ok</span>" : "") +
              (counts.na ? '<span class="dim">' + counts.na + " n/a</span>" : "") +
              "</span>");
            var body = BV.el("div", { class: "hs-sec-body" });
            rows.forEach(function (x) { body.appendChild(resultRow(x.r, x.c, sec)); });
            /* when the quiet rows are hidden, an expanded section still says
               where the rest went (only rendered by CSS in hide-quiet mode) */
            if (counts.ok + counts.na) {
              body.appendChild(BV.el("div", { class: "hs-quiet-note" },
                (counts.ok ? counts.ok + " ok" : "") +
                (counts.ok && counts.na ? " · " : "") +
                (counts.na ? counts.na + " n/a" : "") + " hidden — “show all” to see them"));
            }
            node.appendChild(headEl);
            node.appendChild(body);
            /* the bulk-add menu registers BEFORE BV.collapsible wires the
               head, so stopImmediatePropagation can keep collapsible's own
               contextmenu (subtree-toggle) out of the way */
            headEl.addEventListener("contextmenu", function (ev) {
              ev.preventDefault();
              ev.stopImmediatePropagation();
              var uniq = {};
              rows.forEach(function (x) {
                if (x.c.status !== "flag") return;
                visibleItems(x.r, x.c).forEach(function (it) {
                  if (it.prog && it.prog !== "(no program)") {
                    uniq[(x.r.robot_id || "") + "|" + it.prog.toUpperCase()] =
                      { robot_id: x.r.robot_id, prog: it.prog };
                  }
                });
              });
              var pairs = Object.keys(uniq).map(function (k) { return uniq[k]; });
              if (!pairs.length) return;
              BV.menu({ x: ev.clientX, y: ev.clientY }, [
                { label: "add all " + pairs.length + " flagged program" +
                         (pairs.length === 1 ? "" : "s") + " to editor",
                  onClick: function () { addProgsToEditor(pairs); } },
              ]);
            });
            /* a section with flags opens itself - the flags are the report;
               all-quiet sections stay folded behind their counts. A fold the
               user chose outranks the default and survives repaints. */
            BV.collapsible(node, headEl, body, {
              open: view.secs[sec.id] !== undefined ? view.secs[sec.id] : counts.flag > 0,
              onToggle: function (o) { view.secs[sec.id] = o; saveLast(); },
            });
            list.appendChild(node);
          });
          var tt = tally(allRows());
          headInfo.textContent = results.length + " robots scanned · " +
            (flags ? flags + " flag" + (flags === 1 ? "" : "s") : "no flags") +
            (tt.total
              ? " · " + (tt.shown === tt.total
                  ? tt.total + " findings"
                  : tt.shown + " of " + tt.total + " findings shown")
              : "") +
            " · " + stamp(rep.when);
          resetBtn.style.display = hiddenN ? "" : "none";
          resetBtn.textContent = "reset filters (" + hiddenN + ")";
          allOpen = false;
          expandBtn.textContent = "expand all";
          if (mb) mb.scrollTop = keepScroll;
          saveLast();          /* filters and the report itself outlive the app */
        }

        /* the COPY mirrors the view: tree-shaped, indented, honoring the
           ignore/exclude filters and the flags-only toggle - paste beats a
           screenshot the moment the receiver wants to search or quote it */
        /* the paste IS the filtered report: same rows, same findings, same
           numbers as the headers, and a filtered row never quotes its
           pre-filter summary. A row with findings prints ONLY the surviving
           ones; a row with none (clock drift, mastering, payload - per-robot
           facts, nothing to filter) keeps its detail. That split is why every
           program-shaped check must emit "items": a check that enumerates
           programs in prose alone would smuggle an excluded name back in.
           (Fold state is deliberately NOT consulted - folding is skimming,
           filtering is intent.) */
        function reportText() {
          var hideQuiet = list.classList.contains("hide-quiet");
          var filtered = filtersActive();
          var mark = { flag: "x", info: "*", ok: "ok", na: "-" };
          var tt = tally(allRows());
          var out = ["BackupViewer fleet scan — " + results.length + " robots" +
            (tt.total ? " · " + (tt.shown === tt.total ? tt.total + " findings"
                                 : tt.shown + " of " + tt.total + " findings") : "") +
            " · " + stamp(rep.when)];
          buildSecs().forEach(function (sec) {
            var rows = secRows(sec).filter(function (x) { return !rowHidden(x.r, x.c); });
            if (hideQuiet) {
              rows = rows.filter(function (x) {
                return x.c.status === "flag" || x.c.status === "info";
              });
            }
            if (!rows.length) return;
            out.push("");
            out.push(banner(sec.label));
            rows.forEach(function (x) {
              var who = (x.r.robot || "(unnamed)") + (x.r.line ? " (" + x.r.line + ")" : "");
              var note = shownNote(x.r, x.c);
              out.push("");        /* robots breathe - a wall of lines is unreadable */
              out.push("  " + (mark[x.c.status] || "-") + " " + who + " — " +
                (x.c.summary || "") + (note ? "  [" + note + "]" : ""));
              var vis = visibleItems(x.r, x.c);
              if (vis.length) {
                progGroups(vis).forEach(function (g) {
                  out.push("      " + g.prog + " (" + g.items.length + ")");
                  g.items.forEach(function (it) {
                    if (!it.line && !it.text && !it.after) return;   /* name-only finding */
                    out.push("        " + (it.line ? "line " + it.line + ": " : "") +
                      (it.text || "") + (it.after ? "  → " + it.after : ""));
                  });
                });
              } else if (x.c.detail && !(x.c.items || []).length) {
                out.push("      " + x.c.detail);
              }
            });
          });
          if (filtered) {
            out.push("");
            out.push("(" + filtered + " filter" + (filtered === 1 ? "" : "s") +
              " applied — ignored and excluded findings are not shown)");
          }
          return out.join("\n");
        }

        /* the SHORT form: who, how many, and which programs - no lines. What
           you actually paste to someone ("these 6 robots, these programs"),
           where the full text is what you paste to work from. Same rows, same
           filters, same order as the screen. */
        function quickText() {
          var hideQuiet = list.classList.contains("hide-quiet");
          var mark = { flag: "x", info: "*", ok: "ok", na: "-" };
          var out = [];
          buildSecs().forEach(function (sec) {
            var rows = secRows(sec).filter(function (x) { return !rowHidden(x.r, x.c); });
            if (hideQuiet) {
              rows = rows.filter(function (x) {
                return x.c.status === "flag" || x.c.status === "info";
              });
            }
            if (!rows.length) return;
            if (out.length) out.push("");
            out.push(banner(sec.label));
            rows.forEach(function (x) {
              var who = (x.r.robot || "(unnamed)") + (x.r.line ? " (" + x.r.line + ")" : "");
              var vis = visibleItems(x.r, x.c);
              out.push("");
              out.push("  " + (mark[x.c.status] || "-") + " " + who +
                (vis.length ? " — " + vis.length : ""));
              progGroups(vis).forEach(function (g) {
                out.push("      " + g.prog + " (" + g.items.length + ")");
              });
            });
          });
          if (!out.length) return "(nothing to list)";
          if (filtersActive()) {
            out.push("");
            out.push("(" + filtersActive() + " filter" + (filtersActive() === 1 ? "" : "s") +
              " applied)");
          }
          return out.join("\n");
        }

        repaint();
      }

      /* boot: registry + the kept report together. With nothing selected the
         window is a REPORT VIEWER - going straight to the last scan is the
         whole reason it opens without a selection. */
      host.innerHTML = '<div class="dim" style="padding:.5rem 0">loading checks…</div>';
      Promise.all([BV.api.call("health_checks"), loadLast()])
        .then(function (both) {
          var checks = both[0];
          if (!robots.length && _lastScan) reportView(null, null, null, _lastScan);
          else pickView(checks);
        })
        .catch(function (e) {
          host.innerHTML = '<div class="hs-info">could not load checks: ' +
            BV.esc(e.message) + "</div>";
        });
    },
  };
})();
