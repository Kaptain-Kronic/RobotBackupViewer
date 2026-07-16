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
    software_version: "#overview", payload_unset: "#frames",
  };

  function pill(status) {
    var p = PILL[status] || PILL.na;
    return BV.pill(p[0], p[1]);
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
          if (BV.state.settings) BV.state.settings.scan_checks = picked;
          BV.api.call("set_setting", "scan_checks", picked).catch(function () {});
          go.disabled = true;    /* no double-submit */
          BV.api.call("health_scan_start", robots.map(function (r) { return r.id; }),
            picked, queries)
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

      /* ---- view 3: the report ---- */
      function reportView(results, checks, queries) {
        host.innerHTML = "";
        var flags = 0;
        results.forEach(function (r) {
          (r.checks || []).forEach(function (c) { if (c.status === "flag") flags++; });
        });
        var head = BV.el("div", { class: "hs-report-head" });
        head.appendChild(BV.el("span", { class: "hs-info" },
          results.length + " robots scanned · " +
          (flags ? flags + " flag" + (flags === 1 ? "" : "s") : "no flags")));
        /* the report opens TIGHT: ok/n·a rows and per-finding detail lines both
           start hidden (they bury the flags on a big scan) - two toggles bring
           them back. Buttons say what they'll DO, like hide/unhide. */
        var quietBtn = BV.el("button", { class: "btn", title: "show or hide the ok / n/a rows" },
          "show all");
        var detailBtn = BV.el("button", { class: "btn", title: "show the full finding text under each robot" },
          "details");
        var copyBtn = BV.el("button", { class: "btn", title: "copy this report as text" }, "copy report");
        var againBtn = BV.el("button", { class: "btn", title: "back to the check picker" }, "scan again");
        head.appendChild(quietBtn);
        head.appendChild(detailBtn);
        head.appendChild(copyBtn);
        head.appendChild(againBtn);
        host.appendChild(head);

        var list = BV.el("div", { class: "hs-results hide-quiet hide-details" });
        host.appendChild(list);

        quietBtn.addEventListener("click", function () {
          var hidden = list.classList.toggle("hide-quiet");
          quietBtn.textContent = hidden ? "show all" : "flags only";
        });
        detailBtn.addEventListener("click", function () {
          var hidden = list.classList.toggle("hide-details");
          detailBtn.textContent = hidden ? "details" : "hide details";
        });

        /* sections in registry order; each find query rides last, in order */
        var secs = checks.filter(function (c) {
          return results.some(function (r) {
            return (r.checks || []).some(function (x) { return x.id === c.id; });
          });
        }).map(function (c) { return { id: c.id, label: c.label }; });
        (queries || []).forEach(function (fq, i) {
          secs.push({ id: "find:" + i, label: 'find: "' + fq + '"', query: fq });
        });

        secs.forEach(function (sec) {
          var rows = [];
          results.forEach(function (r) {
            (r.checks || []).forEach(function (x) {
              if (x.id === sec.id) rows.push({ r: r, c: x });
            });
          });
          if (!rows.length) return;
          rows.sort(function (a, b) {
            var d = (ORDER[a.c.status] || 9) - (ORDER[b.c.status] || 9);
            return d || (a.r.robot || "").localeCompare(b.r.robot || "");
          });
          var counts = { flag: 0, info: 0, ok: 0, na: 0 };
          rows.forEach(function (x) { counts[x.c.status] = (counts[x.c.status] || 0) + 1; });

          var node = BV.el("div", { class: "hs-sec" });
          var headEl = BV.el("div", { class: "hs-sec-head" },
            '<span class="hs-sec-title">' + BV.esc(sec.label) + "</span>" +
            '<span class="hs-sec-counts">' +
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
          /* every section starts folded - the header pills carry the verdict */
          BV.collapsible(node, headEl, body, { open: false });
          list.appendChild(node);
        });

        copyBtn.addEventListener("click", function () {
          copyText(reportText(results, checks, queries));
        });
        againBtn.addEventListener("click", function () { pickView(checks); });
      }

      function resultRow(r, c, sec) {
        var row = BV.el("div", { class: "hs-row st-" + c.status, title: "open this robot" },
          pill(c.status) +
          '<span class="hs-robot">' + BV.esc(r.robot || "(unnamed)") + "</span>" +
          (r.line ? '<span class="hs-line">' + BV.esc(r.line) + "</span>" : "") +
          '<span class="hs-sum">' + BV.esc(c.summary || "") + "</span>" +
          (c.detail ? '<span class="hs-detail">' + BV.esc(c.detail) + "</span>" : ""));
        row.addEventListener("click", function () {
          BV.api.call("lib_open", r.robot_id, "latest").then(function (m) {
            BV.state.setManifest(m);
            modal.close(true);
            location.hash = sec && sec.query
              ? "#search/" + encodeURIComponent(sec.query)
              : (TARGET[c.id] || "#overview");
          }).catch(function (e) { BV.toast(e.message); });
        });
        return row;
      }

      function reportText(results, checks, queries) {
        var lines = ["BackupViewer fleet scan — " + results.length + " robots"];
        var mark = { flag: "✗", info: "•", ok: "✓", na: "-" };
        var secs = checks.map(function (c) { return { id: c.id, label: c.label }; });
        (queries || []).forEach(function (fq, i) {
          secs.push({ id: "find:" + i, label: 'find: "' + fq + '"' });
        });
        secs.forEach(function (sec) {
          var rows = [];
          results.forEach(function (r) {
            (r.checks || []).forEach(function (x) {
              if (x.id === sec.id) rows.push({ r: r, c: x });
            });
          });
          if (!rows.length) return;
          rows.sort(function (a, b) {
            var d = (ORDER[a.c.status] || 9) - (ORDER[b.c.status] || 9);
            return d || (a.r.robot || "").localeCompare(b.r.robot || "");
          });
          lines.push("");
          lines.push("[" + sec.label + "]");
          rows.forEach(function (x) {
            var who = (x.r.robot || "(unnamed)") + (x.r.line ? " (" + x.r.line + ")" : "");
            lines.push("  " + (mark[x.c.status] || "-") + " " + who + " — " + (x.c.summary || "") +
              (x.c.detail ? " — " + x.c.detail : ""));
          });
        });
        return lines.join("\n");
      }

      function copyText(text) {
        function fallback() {
          var ta = BV.el("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy"); BV.toast("report copied"); }
          catch (e) { BV.toast("copy failed"); }
          document.body.removeChild(ta);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text)
            .then(function () { BV.toast("report copied"); }, fallback);
        } else fallback();
      }

      /* boot: fetch the registry, then show the picker */
      host.innerHTML = '<div class="dim" style="padding:.5rem 0">loading checks…</div>';
      BV.api.call("health_checks").then(pickView).catch(function (e) {
        host.innerHTML = '<div class="hs-info">could not load checks: ' + BV.esc(e.message) + "</div>";
      });
    },
  };
})();
