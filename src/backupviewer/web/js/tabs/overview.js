/* tabs/overview.js - dashboard: hero + stat chips up top, a masonry main
   area and a narrow sidebar of secondary cards. Cards are draggable by
   their title (within and between zones); the arrangement persists via the
   settings api. Card collapse state persists separately (ov_collapsed). */
(function () {
  "use strict";

  var collapsed = null;   /* card-id -> bool */
  var zones = null;       /* {side: el, cols: [el, ...]} for the current render */
  var colCount = 2;
  var dr = null;          /* the drag-reorder controller for the current render */

  var DEFAULT_SIDE = ["ethernet", "motors"];
  var DEFAULT_MAIN = ["mastering", "memory", "gmwizard", "tasks", "options", "snapshot"];

  /* saved shape v2: {version:2, side:[ids], cols:[[ids],[ids],...]}
     (v1 {zones:{main,side}} is migrated by chunking main in order) */
  function savedLayout() {
    var l = BV.state.settings && BV.state.settings.ov_layout;
    if (!l) return null;
    if (l.version === 2) return l;
    if (l.zones) {
      return { version: 2, side: l.zones.side || [], cols: [l.zones.main || []] };
    }
    return null;
  }

  function chunk(ids, n) {
    var cols = [];
    var per = Math.ceil(ids.length / n) || 1;
    for (var i = 0; i < n; i++) cols.push(ids.slice(i * per, (i + 1) * per));
    return cols;
  }

  /* {side:[ids], cols:[[ids] x colCount]} resolved for this render:
     saved placement wins; chunked/redealt in order when the column count
     differs; unknown ids fall back to defaults */
  function resolveLayout() {
    var saved = savedLayout();
    if (!saved) {
      return { side: DEFAULT_SIDE.slice(), cols: chunk(DEFAULT_MAIN, colCount) };
    }
    var side = (saved.side || []).slice();
    var cols;
    if ((saved.cols || []).length === colCount) {
      cols = saved.cols.map(function (c) { return c.slice(); });
    } else {
      cols = chunk([].concat.apply([], saved.cols || []), colCount);
    }
    var known = {};
    side.forEach(function (id) { known[id] = 1; });
    cols.forEach(function (c) { c.forEach(function (id) { known[id] = 1; }); });
    DEFAULT_SIDE.forEach(function (id) { if (!known[id]) side.push(id); });
    DEFAULT_MAIN.forEach(function (id) {
      if (!known[id]) cols[cols.length - 1].push(id);
    });
    return { side: side, cols: cols };
  }

  function zoneElFor(cardId, layout) {
    if (layout.side.indexOf(cardId) >= 0) return zones.side;
    for (var i = 0; i < layout.cols.length; i++) {
      if (layout.cols[i].indexOf(cardId) >= 0) return zones.cols[i];
    }
    return zones.cols[zones.cols.length - 1];
  }

  function saveCollapsed() {
    BV.api.call("set_setting", "ov_collapsed", collapsed).catch(function () {});
  }

  /* persist exactly what is on screen, per column; saved-but-not-rendered ids
     are kept at the end of their previous home so they reappear later */
  function persistLayout() {
    function ids(container) {
      return [].map.call(container.querySelectorAll(":scope > .card[data-card-id]"),
        function (c) { return c.dataset.cardId; });
    }
    var saved = savedLayout() || { side: [], cols: [] };
    var out = {
      version: 2,
      side: ids(zones.side),
      cols: zones.cols.map(ids),
    };
    var rendered = {};
    out.side.forEach(function (id) { rendered[id] = 1; });
    out.cols.forEach(function (c) { c.forEach(function (id) { rendered[id] = 1; }); });
    (saved.side || []).forEach(function (id) { if (!rendered[id]) out.side.push(id); });
    [].concat.apply([], saved.cols || []).forEach(function (id) {
      if (!rendered[id]) out.cols[out.cols.length - 1].push(id);
    });
    BV.state.settings.ov_layout = out;
    BV.api.call("set_setting", "ov_layout", out).catch(function () {});
  }

  /* place rendered cards into their zone/column in saved order */
  function applyLayout(layout) {
    var byId = {};
    document.querySelectorAll(".ov-dash .card[data-card-id]").forEach(function (c) {
      byId[c.dataset.cardId] = c;
    });
    layout.side.forEach(function (id) { if (byId[id]) zones.side.appendChild(byId[id]); });
    layout.cols.forEach(function (colIds, i) {
      colIds.forEach(function (id) { if (byId[id]) zones.cols[i].appendChild(byId[id]); });
    });
  }

  /* ---- cards ----
     a card is a BV.card shell placed into its saved zone; the drag-reorder
     controller (dr) makes the title a handle, and the title click toggles
     collapse unless it was really the tail of a drag */

  function card(id, title, opts) {
    opts = opts || {};
    var isCollapsed = (collapsed && id in collapsed) ? collapsed[id] : !!opts.startCollapsed;
    var co = BV.card({
      id: id,
      title: title,
      count: opts.count,
      collapsible: true,
      startCollapsed: isCollapsed,
      headTitle: "drag to move · click to collapse",
    });
    co.head.addEventListener("click", function () {
      if (dr && dr.isRecentDrag()) return; /* a sloppy drag is not a click */
      collapsed[id] = co.el.classList.toggle("collapsed");
      saveCollapsed();
    });
    dr.wire(co.el);
    zoneElFor(id, card._layout).appendChild(co.el);
    return co.el;
  }

  /* the kv list builder lives in BV.kv.html now; keep the local name for the
     ethernet / wizard / motors call sites below */
  var kvHtml = BV.kv.html;

  /* independently-collapsible section INSIDE a card */
  function subsection(parent, title, bodyEl, opts) {
    opts = opts || {};
    var wrap = BV.el("div", { class: "subsec" + (opts.startCollapsed ? " collapsed" : "") });
    var h = BV.el("h4", { class: "subsec-h" }, BV.esc(title) +
      (opts.count !== undefined ? ' <span class="count">' + opts.count + "</span>" : ""));
    var body = BV.el("div", { class: "subsec-body" });
    if (typeof bodyEl === "string") body.innerHTML = bodyEl;
    else if (bodyEl) body.appendChild(bodyEl);
    h.addEventListener("click", function () { wrap.classList.toggle("collapsed"); });
    wrap.appendChild(h);
    wrap.appendChild(body);
    parent.appendChild(wrap);
    return body;
  }

  function chipsHtml(ov) {
    var m = BV.state.manifest || {};
    var chips = [];
    if (m.backup_type && m.backup_type !== "unknown") chips.push(["type", m.backup_type]);
    if (m.file_count) chips.push(["files", m.file_count]);
    if (ov.options && ov.options.length) chips.push(["options", ov.options.length]);
    if (ov.memory && ov.memory.MAIN) {
      var worst = 0;
      ov.memory.MAIN.pools.forEach(function (p) {
        if (p.name === "TEMP" || p.name === "FROM" || !p.total_kb) return;
        worst = Math.max(worst, Math.round((p.total_kb - p.avail_kb) / p.total_kb * 100));
      });
      if (worst) chips.push(["mem", worst + "%"]);
    }
    if (ov.mastering && ov.mastering.length) {
      var done = ov.mastering.filter(function (g) { return g.master_done; }).length;
      chips.push(["mastered", done + "/" + ov.mastering.length]);
    }
    if (ov.gmwizard && ov.gmwizard.failures) chips.push(["wizard fails", ov.gmwizard.failures]);
    return chips.map(function (ch) {
      return '<span class="ov-chip"><span class="k">' + BV.esc(ch[0]) + "</span>" +
        '<span class="v">' + BV.esc(ch[1]) + "</span></span>";
    }).join("");
  }

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    collapsed = (BV.state.settings && BV.state.settings.ov_collapsed) || collapsed || {};

    var resetBtn = BV.el("button", { class: "btn", title: "restore the default card arrangement" }, "reset layout");
    resetBtn.addEventListener("click", function () {
      BV.state.settings.ov_layout = null;
      BV.api.call("set_setting", "ov_layout", null).catch(function () {});
      render(view, toolbar);
    });
    toolbar.appendChild(resetBtn);

    BV.api.call("get_overview").then(function (ov) {
      var id = ov.identity || {};
      /* edition (V8.33P/16) beats raw version here - techs quote editions */
      var hero = BV.hero({
        name: id.robot_name || BV.state.manifest.name,
        model: id.robot_model,
        sub: [id.f_number, id.application, id.software_edition || id.version, id.backup_date],
        stick: true,
        chips: '<span class="ov-chips">' + chipsHtml(ov) + "</span>",
      });
      view.appendChild(hero);

      var dash = BV.el("div", { class: "ov-dash" });
      var main = BV.el("div", { class: "ov-main" });
      var side = BV.el("div", { class: "ov-side" });
      dash.appendChild(main);
      dash.appendChild(side);
      view.appendChild(dash);

      /* the side panel is its own scroll region: cap it to the space below the
         (sticky) hero so it scrolls internally instead of riding the main
         scroll. --view-h feeds .ov-side's max-height. */
      function fitSide() {
        dash.style.setProperty("--view-h", Math.max(12, view.clientHeight - dash.offsetTop) + "px");
      }
      fitSide();

      /* explicit columns: a card stays where you drop it */
      var rootFs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
      colCount = Math.max(1, Math.min(3, Math.floor(main.clientWidth / (rootFs * 22)) || 1));
      zones = { side: side, cols: [] };
      for (var ci = 0; ci < colCount; ci++) {
        var col = BV.el("div", { class: "ov-col" });
        main.appendChild(col);
        zones.cols.push(col);
      }
      /* one reusable drag-reorder controller across every dashboard zone;
         cards register their title as the grab handle via card() -> dr.wire */
      dr = BV.dragReorder({
        zones: zones.cols.concat([side]),
        itemSelector: ".card[data-card-id]",
        handleSelector: ":scope > h3:first-of-type",
        onDrop: function () { persistLayout(); },
      });
      var layout = resolveLayout();

      /* re-deal when a resize changes how many columns fit */
      var onResize = BV.debounce(function () {
        if (!document.contains(dash)) {
          window.removeEventListener("resize", onResize);
          return;
        }
        fitSide();
        var n = Math.max(1, Math.min(3, Math.floor(main.clientWidth / (rootFs * 22)) || 1));
        if (n !== colCount) render(view, toolbar);
      }, 200);
      window.addEventListener("resize", onResize);
      card._layout = layout;

      /* identity + software boxes are gone: the stapled hero already carries
         that info (Wilson: serial no / controller id are noise) */

      /* master counts */
      if (ov.mastering && ov.mastering.length) {
        var mc = card("mastering", "master counts");
        var html = "";
        ov.mastering.forEach(function (g) {
          if (!g.master_counts.length) return;
          html += '<div style="margin-bottom:.6rem">' +
            '<div style="display:flex;gap:.5rem;align-items:center;margin-bottom:.25rem">' +
            '<span class="dim" style="font-size:.78rem">group ' + g.group + "</span>" +
            BV.pill(g.master_done ? "mastered" : "not mastered", g.master_done ? "on" : "err") +
            (g.ref_done ? BV.pill("ref ok", "ghost") : "") +
            "</div><dl class=\"kv\">";
          g.master_counts.forEach(function (c, i) {
            html += "<dt>j" + (i + 1) + "</dt><dd style=\"font-variant-numeric:tabular-nums\">" +
              BV.esc(c) + "</dd>";
          });
          html += "</dl></div>";
        });
        mc.insertAdjacentHTML("beforeend", html || '<div class="dim">no mastering data</div>');
      }

      /* memory */
      if (ov.memory && ov.memory.MAIN) {
        var mem = card("memory", "memory (main)");
        var mh = "";
        ov.memory.MAIN.pools.forEach(function (p) {
          if (!p.total_kb) return;
          var used = p.total_kb - p.avail_kb;
          var pct = Math.min(100, Math.round(used / p.total_kb * 100));
          mh += '<div class="membar' + (pct > 90 ? " hot" : "") + '">' +
            '<div class="mb-label"><span>' + BV.esc(p.name.toLowerCase()) + "</span><span>" +
            BV.fmt.kb(used) + " / " + BV.fmt.kb(p.total_kb) + " · " + pct + "%</span></div>" +
            '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div></div>';
        });
        var hw = ov.memory.MAIN.hardware || {};
        var hws = Object.keys(hw).map(function (k) { return k.toLowerCase() + " " + hw[k]; }).join(" · ");
        if (hws) mh += '<div class="mb-label" style="margin-top:.4rem"><span class="dim">' + BV.esc(hws) + "</span></div>";
        mem.insertAdjacentHTML("beforeend", mh);
      }

      /* ethernet - exactly the six fields a tech needs. port1/port2 are the
         QUICC0/QUICC1 host-table entries; the router comes from the host
         table too (TMI_ROUTER is wrong/useless - user-confirmed). */
      if (ov.ethernet && (ov.ethernet.hostname || ov.ethernet.hosts.length)) {
        var e = ov.ethernet;
        function hostAddr(name) {
          var h = (e.hosts || []).find(function (x) {
            return (x.name || "").toUpperCase() === name;
          });
          return h && h.addr ? h.addr : "";
        }
        card("ethernet", "ethernet").insertAdjacentHTML("beforeend", kvHtml([
          ["hostname", e.hostname],
          ["port1", hostAddr("QUICC0")],
          ["port2", hostAddr("QUICC1")],
          ["subnet", e.subnet],
          ["router", hostAddr("ROUTER")],
          ["mac", e.mac],
        ]));
      }

      /* MH valves (GM grippers): a compact "what signal grips what" glance -
         each gripper/vacuum with the signals that drive/sense it; click to open
         the full MH valves tab. Only present when MHGRIPDT.VA is in the backup. */
      if (BV.state.manifest && BV.state.manifest.tabs && BV.state.manifest.tabs.mhvalves) {
        var mhBody = BV.el("div");
        mhBody.innerHTML = '<div class="dim" style="font-size:.78rem">loading…</div>';
        card("mhvalves", "mh valves").appendChild(mhBody);
        BV.api.call("get_mhvalves").then(function (mh) {
          var grips = mh.grippers || [];
          var rows = grips.map(function (g) {
            var byRole = {}, order = [];
            (g.signals || []).forEach(function (s) {
              if (!(s.role in byRole)) { byRole[s.role] = []; order.push(s.role); }
              byRole[s.role].push(s.index);
            });
            var sigs = order.map(function (r) {
              return '<span class="dim">' + BV.esc(r) + "</span> " + BV.esc(byRole[r].join(", "));
            }).join(" · ");
            return '<div class="mh-ov-row"><span class="mh-ov-name">' + BV.esc(g.name) +
              (g.vacuum ? " " + BV.pill("vac", "ghost") : "") + "</span>" +
              '<span class="mh-ov-sigs">' + (sigs || '<span class="dim">—</span>') + "</span></div>";
          }).join("");
          mhBody.innerHTML = (rows || '<div class="dim">no configured grippers</div>') +
            '<a href="#mhvalves" class="mh-ov-open">open mh valves →</a>';
          mhBody.querySelectorAll(".mh-ov-row").forEach(function (el) {
            el.addEventListener("click", function () { location.hash = "#mhvalves"; });
          });
        }).catch(function (err) {
          mhBody.innerHTML = '<div class="dim">' + BV.esc(err.message) + "</div>";
        });
      }

      /* wizard q&a - one collapsible (the card itself), answers only by
         default; the wizard's processing chatter hides behind a toggle */
      if (ov.gmwizard) {
        var w = ov.gmwizard;
        var qa = w.entries.filter(function (x) { return x.kind === "qa"; });
        var wc = card("gmwizard", "wizard q&a", { count: qa.length + " answers" });
        wc.insertAdjacentHTML("beforeend", kvHtml([
          ["executed", w.header.executed_on],
          ["wizard", w.header.wizard_version],
          ["customization", w.header.custo_version],
        ]));
        if (w.failures) {
          var fails = w.entries.filter(function (x) { return x.kind === "failure"; });
          wc.insertAdjacentHTML("beforeend",
            '<div class="notice" style="margin:.5rem 0">' + w.failures + " failed steps: " +
            fails.map(function (f) { return BV.esc(f.label) + " (" + f.status + ")"; }).join(", ") +
            "</div>");
        }

        var showProcesses = false;
        var procBtn = BV.el("button", { class: "btn", style: "margin:.4rem 0;font-size:.72rem;padding:.18rem .55rem" },
          "show processes");
        wc.appendChild(procBtn);
        var scroller = BV.el("div", { class: "scrollbody" });
        var logEl = BV.el("div");
        scroller.appendChild(logEl);
        wc.appendChild(scroller);

        function drawWizard() {
          var html = "";
          w.entries.forEach(function (x) {
            if (x.kind === "qa") {
              var cls = /^yes$/i.test(x.a) ? " yes" : (/^no$/i.test(x.a) ? " no" : "");
              html += '<div class="qa-row"><span class="qa-q">' + BV.esc(x.q) + "</span>" +
                '<span class="qa-a' + cls + '">' + BV.esc(x.a || "—") + "</span></div>";
            } else if (x.kind === "failure") {
              html += '<div class="qa-fail">✕ ' + BV.esc(x.prog + " " + x.label) + " — status " + x.status + "</div>";
            } else if (showProcesses) {
              html += '<div class="qa-event" style="padding-left:' + Math.min(x.indent || 0, 6) * 0.4 + 'em">' +
                BV.esc(x.text) + "</div>";
            }
          });
          logEl.innerHTML = html;
        }
        procBtn.addEventListener("click", function () {
          showProcesses = !showProcesses;
          procBtn.textContent = showProcesses ? "hide processes" : "show processes";
          drawWizard();
        });
        drawWizard();
      }

      /* motors */
      if (ov.motors && ov.motors.length) {
        card("motors", "motors", { count: ov.motors.length })
          .insertAdjacentHTML("beforeend", kvHtml(ov.motors.map(function (m) {
            return ["g" + m.group + " a" + m.axis, m.info];
          })));
      }

      /* serial ports box removed - useless to 90% of robot programmers */

      /* tasks */
      if (ov.tasks && ov.tasks.length) {
        var tc = card("tasks", "tasks", { count: ov.tasks.length });
        tc.appendChild(BV.table([
          { key: "name", label: "task" },
          { key: "state", label: "state", render: function (t) {
              var cls = t.status === "RUNNING" ? "on" : (t.status === "ABORTED" ? "err" : "off");
              return BV.pill(t.status.toLowerCase(), cls);
            } },
          { key: "at", label: "at", dim: true, render: function (t) {
              return BV.esc(t.routine + ":" + t.line); } },
        ], ov.tasks));
      }

      /* software options */
      if (ov.options && ov.options.length) {
        var oc = card("options", "software options",
          { startCollapsed: true, count: ov.options.length });
        var holder = BV.el("div");
        var optGrid = BV.el("div");
        optGrid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:.1rem .9rem;font-size:.82rem;max-height:420px;overflow:auto";
        function renderOpts(q) {
          optGrid.innerHTML = "";
          ov.options.forEach(function (o) {
            if (q && (o.feature + " " + o.ord_no).toLowerCase().indexOf(q) < 0) return;
            optGrid.insertAdjacentHTML("beforeend",
              '<div style="display:flex;justify-content:space-between;gap:.6rem;min-width:0">' +
              '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + BV.esc(o.feature) + "</span>" +
              '<span class="dim">' + BV.esc(o.ord_no) + "</span></div>");
          });
        }
        var sb = BV.searchBox({ placeholder: "filter options…", onChange: function (q) { renderOpts(q.toLowerCase()); } });
        sb.el.style.marginBottom = "0.6rem";
        holder.appendChild(sb.el);
        holder.appendChild(optGrid);
        renderOpts("");
        oc.appendChild(holder);
      }

      /* at backup time: safety + position + alarms */
      var snap = card("snapshot", "at backup time", { startCollapsed: true });
      var sbody = BV.el("div");
      snap.appendChild(sbody);

      if (ov.safety && ov.safety.length) {
        var sh = '<h3 style="margin:.2rem 0 .4rem">stop signals</h3>' +
          '<div style="display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:.9rem">';
        ov.safety.forEach(function (s) {
          sh += BV.pill(s.signal.toLowerCase(), s.value ? "warn" : "ghost");
        });
        sbody.insertAdjacentHTML("beforeend", sh + "</div>");
      }

      var withJoints = (ov.positions || []).filter(function (g) { return g.joints.length; });
      if (withJoints.length) {
        var maxJ = Math.max.apply(null, withJoints.map(function (g) { return g.joints.length; }));
        sbody.insertAdjacentHTML("beforeend", '<h3 style="margin:.2rem 0 .4rem">position</h3>');
        /* transposed: a label column then one column per group; rows are the
           joints, then a final uf/ut row (kept dim like before) */
        var posCols = [{ key: "lbl", label: "", dim: true, render: function (r) { return BV.esc(r.lbl); } }];
        withJoints.forEach(function (g) {
          posCols.push({ label: "group " + g.group, num: true, render: function (r) { return r.cell(g); } });
        });
        var posRows = [];
        for (var j = 0; j < maxJ; j++) {
          (function (jj) {
            posRows.push({ lbl: "j" + (jj + 1), cell: function (g) {
              var jv = g.joints[jj]; return jv ? BV.fmt.num(jv.deg, 2) + "°" : "";
            } });
          })(j);
        }
        posRows.push({ lbl: "uf / ut", cell: function (g) {
          return '<span class="dim">' + (g.frame_no !== null ? g.frame_no + " / " + g.tool_no : "—") + "</span>";
        } });
        sbody.appendChild(BV.table(posCols, posRows, { maxWidth: "560px", style: "margin-bottom:.9rem;" }));
      }

      if (BV.alarms) {
        var ah = BV.el("div");
        sbody.appendChild(ah);
        BV.alarms.renderInto(ah);
      }

      applyLayout(layout);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">overview unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "overview", label: "overview", render: render });
})();
