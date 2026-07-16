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

  /* ---- mh valves + magnet: collapsible entries for the combined overview card ----
     headers are colored, values plain, and DI/DO/R designations are lit pills
     (the same .pill.on used by running/mastered). */
  function titleCase(s) {
    return String(s || "").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function ovEntry(nameHtml, bodyEl) {
    var node = BV.el("div", { class: "mhv-ov-entry" });
    var head = BV.el("div", { class: "mhv-ov-head" }, nameHtml);
    node.appendChild(head);
    node.appendChild(bodyEl);
    BV.collapsible(node, head, bodyEl, { open: false });
    return node;
  }

  function ovRegRows(regs) {
    var wrap = BV.el("div", { class: "mhv-ov-reglist" });
    (regs || []).forEach(function (r) {
      var val = (r.value === null || r.value === undefined || r.value === "") ? "—" : r.value;
      wrap.insertAdjacentHTML("beforeend",
        '<div class="mhv-ov-reg"><span class="mhv-ov-reglabel">' +
        BV.esc(r.comment || ("R[" + r.index + "]")) + '</span><span class="mhv-ov-regval">' +
        BV.esc(val) + "</span>" + BV.pill("R[" + r.index + "]", "on") + "</div>");
    });
    return wrap;
  }

  function magnetEntry(mag) {
    var body = BV.el("div", { class: "mhv-ov-body" });
    var g = mag.groups || { general: mag.registers || [], magnets: [] };
    body.appendChild(ovRegRows(g.general));
    (g.magnets || []).forEach(function (m) {
      var sub = BV.el("div", { class: "mhv-ov-body" });
      sub.appendChild(ovRegRows(m.registers));
      body.appendChild(ovEntry('<span class="mhv-ov-name">Mag ' + m.n + "</span>", sub));
    });
    return ovEntry('<span class="mhv-ov-name">Magnet</span>', body);
  }

  /* one Outputs/Inputs section: functional-role labels (#n when a role repeats)
     + the resolved DI/DO as a lit pill; each row links into the io tab */
  function ovSigSection(title, sigs) {
    if (!sigs.length) return "";
    var counts = {}, seen = {};
    sigs.forEach(function (s) { counts[s.role] = (counts[s.role] || 0) + 1; });
    var html = '<div class="mhv-ov-sec">' + BV.esc(title) + "</div>";
    sigs.forEach(function (s) {
      var label = titleCase(s.role);
      if (counts[s.role] > 1) { seen[s.role] = (seen[s.role] || 0) + 1; label += " #" + seen[s.role]; }
      html += '<a class="mhv-ov-sig" href="#io/jump/' + s.kind + "/" + s.number + '">' +
        '<span class="mhv-ov-siglabel">' + BV.esc(label) + "</span>" +
        BV.pill(s.kind + "[" + s.number + "]", "on") + "</a>";
    });
    return html;
  }

  function valveEntry(v) {
    var body = BV.el("div", { class: "mhv-ov-body" });
    body.innerHTML = ovSigSection("Outputs", v.outputs || []) + ovSigSection("Inputs", v.inputs || []);
    if (!body.innerHTML) body.innerHTML = '<div class="dim">no wired signals</div>';
    return ovEntry('<span class="mhv-ov-name">' + BV.esc(v.name) +
      '</span><span class="mhv-ov-type">' + BV.esc(v.type) + "</span>", body);
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

  /* a dated-backup timeline picker - only when a library robot (with history) is
     open. Switching re-opens the chosen snapshot and re-renders in place. */
  function fmtBackupDate(taken) {
    if (!taken) return "(undated)";
    return String(taken).replace("T", " ").slice(0, 16);
  }

  function switchBackup(path) {
    var man = BV.state.manifest || {};
    if (!man.robot_id) return;
    BV.api.call("lib_open", man.robot_id, path).then(function (m) {
      BV.state.setManifest(m);
      BV.route();
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* diff the open backup against another dated snapshot of the same robot -
     the "ran friday, broke monday - what changed?" one-click. The picked date
     loads as the compare session (side b) and we land on #compare. */
  function compareWith(b) {
    var man = BV.state.manifest || {};
    if (!man.robot_id) return;
    BV.api.call("lib_open", man.robot_id, b.path, "b").then(function (cm) {
      BV.state.compare = cm;
      BV.toast("comparing with " + fmtBackupDate(b.taken));
      location.hash = "#compare";
    }).catch(function (e) { BV.toast(e.message); });
  }

  function buildDatePicker() {
    var man = BV.state.manifest || {};
    var backups = man.backups || [];
    if (!man.robot_id || backups.length < 2) return null;   // nothing to switch between
    var current = man.current_path || "";
    var cur = null;
    backups.forEach(function (b) { if (b.path === current) cur = b; });
    var label = fmtBackupDate((cur || backups[0]).taken);
    var btn = BV.el("button", { class: "btn ov-datepick", title: "switch to another dated backup" },
      "🕓 " + BV.esc(label));
    btn.addEventListener("click", function () {
      BV.menu(btn, backups.map(function (b, i) {
        return {
          label: fmtBackupDate(b.taken) + (i === 0 ? "  · latest" : "") + (b.path === current ? "  ✓" : ""),
          onClick: function () { if (b.path !== current) switchBackup(b.path); },
          /* every row except the open one gets a "vs" pill */
          action: b.path === current ? undefined : {
            label: "vs", title: "compare the open backup with this date",
            onClick: function () { compareWith(b); },
          },
        };
      }));
    });
    return btn;
  }

  /* "open backup location" - reveal the currently-open snapshot folder in Explorer.
     Only shown for backups opened from the library (which carry current_path). */
  function buildOpenLocation() {
    var man = BV.state.manifest || {};
    var path = man.current_path || "";
    if (!path) return null;
    var btn = BV.el("button", { class: "btn ov-openloc", title: "open this backup's folder in Explorer" },
      "📂 open location");
    btn.addEventListener("click", function () {
      if (BV.openLocation) BV.openLocation(path);
      else BV.api.call("open_path", path).catch(function (e) { BV.toast(e.message); });
    });
    return btn;
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
    var datePick = buildDatePicker();
    if (datePick) toolbar.appendChild(datePick);
    var openLoc = buildOpenLocation();
    if (openLoc) toolbar.appendChild(openLoc);

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
      /* ov-host makes the slot a fixed-height column: the hero stays pinned at the
         top while main + side each own their scroll (no single shared scrollbar) */
      view.classList.add("ov-host");
      view.appendChild(dash);

      /* an emptied sidebar reclaims its column when idle, but is revealed as a drop
         target again the moment a drag starts (so cards can be put back) */
      function updateNoSide() {
        dash.classList.toggle("no-side",
          side.querySelectorAll(":scope > .card[data-card-id]").length === 0);
      }

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
        autoScroll: [main, side],   /* edge-scroll each pane while dragging */
        onDragState: function (active) { dash.classList.toggle("drag-active", active); },
        onDrop: function () { persistLayout(); updateNoSide(); },
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

      /* MH valves + magnet (GM end-effector): ONE card of collapsible entries -
         the magnet first (if present), then each configured valve. Entries start
         collapsed; expanding shows organized detail (Mag registers / Outputs +
         Inputs) with lit DI/DO/R pills. Present when MHGRIPDT.VA or a magnet is. */
      var mhShow = BV.state.manifest && BV.state.manifest.tabs && BV.state.manifest.tabs.mhvalves;
      var magShow = BV.state.manifest && BV.state.manifest.magnet;
      if (mhShow || magShow) {
        var grBody = BV.el("div", { class: "mhv-ov" });
        grBody.innerHTML = '<div class="dim" style="font-size:.78rem">loading…</div>';
        card("mhvalves", "mh valves").appendChild(grBody);
        Promise.all([
          mhShow ? BV.api.call("get_mhvalves") : Promise.resolve(null),
          magShow ? BV.api.call("get_magnet").catch(function () { return null; }) : Promise.resolve(null),
        ]).then(function (res) {
          var mh = res[0], mag = res[1];
          grBody.innerHTML = "";
          if (mag && mag.is_magnet) grBody.appendChild(magnetEntry(mag));
          var valves = mh ? (mh.tools || []).reduce(function (a, t) {
            return a.concat(t.valves || []);
          }, []) : [];
          valves.forEach(function (v) { grBody.appendChild(valveEntry(v)); });
          if (!grBody.children.length) grBody.innerHTML = '<div class="dim">no grippers</div>';
        }).catch(function (err) {
          grBody.innerHTML = '<div class="dim">' + BV.esc(err.message) + "</div>";
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
      updateNoSide();
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">overview unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "overview", label: "overview", render: render });
})();
