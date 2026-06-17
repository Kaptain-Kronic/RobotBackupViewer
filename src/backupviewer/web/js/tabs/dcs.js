/* tabs/dcs.js - Dual Check Safety.

   Landing (#dcs/<file>): the signatures card (current vs latch - the quick
   health check, shown once) plus a menu of the report's sections. Click a
   section -> its own page (#dcs/<file>/<sectionId>).

   Each section renders the way the pendant shows it:
     list_detail / grouped_list -> a list; click an entry to drop down its
       details (so compare is just two lists with dropdowns)
     logic -> safety logic equations, code-styled
     table / table_grouped -> plain tables
     raw -> simple sections shown as-is

   When a comparison backup is loaded, a vs toggle puts both robots'
   copies of whatever you're viewing side by side. */
(function () {
  "use strict";

  var vsOn = false; /* persists across section navigation within the tab */

  /* ---- helpers ---- */

  /* dcs reserves bright colors for changed/NG; a screen of OK stays muted */
  function statusVariant(st) {
    return st === "OK" ? "ok-soft" : (st === "CHGD" ? "warn" : "err");
  }

  function statusPill(st) {
    return st ? BV.pill(st.toLowerCase(), statusVariant(st)) : "";
  }

  function menuBadge(sum) {
    if (!sum) return "";
    if (sum.ng) return BV.pill(sum.ng + " NG", "err");
    if (sum.chgd) return BV.pill(sum.chgd + " changed", "warn");
    if (sum.ok) return BV.pill("ok", "ok-soft");
    return "";
  }

  /* DCS safe-I/O equation: highlight signal refs (CSO[ 2]), operators, !, =.
     Split on the assignment '=' first and tokenize each side - inserting span
     tags and THEN running a global '=' replace would corrupt class="..."
     attributes (they contain '='). */
  var SIGREF = /\b([A-Z]{2,3})\[\s*\d+\]/g;
  var OPWORD = /\b(AND|OR)\b/g;
  function _tok(s) {
    s = BV.esc(s);
    s = s.replace(SIGREF, '<span class="dcs-sig">$&</span>');
    s = s.replace(OPWORD, '<span class="dcs-op">$&</span>');
    s = s.replace(/!/g, '<span class="dcs-not">!</span>');
    return s;
  }
  function highlightLogic(text) {
    var eq = text.indexOf("=");
    if (eq < 0) return _tok(text);
    return _tok(text.slice(0, eq)) + '<span class="dcs-eq">=</span>' + _tok(text.slice(eq + 1));
  }

  /* ---- per-kind section renderers (return an element) ---- */

  function entryTags(e) {
    var t = "";
    if (e.enable) {
      t += BV.pill((e.active ? e.enable : "disabled").toLowerCase(), e.active ? "acc" : "ghost");
    } else if (e.active === false) {
      t += BV.pill("disabled", "ghost");
    }
    if (e.elem_count !== undefined) {
      t += BV.pill(e.elem_count + " elem", "ghost");
    }
    t += statusPill(e.detail_status || e.status || e.row_status);
    return t;
  }

  /* a click-to-expand entry node wired into the standard collapsible (so
     right-click expands/collapses the whole subtree) */
  function entryNode(label, idx, tagsHtml, bodyEl) {
    var node = BV.el("div", { class: "dcs-entry" });
    var head = BV.el("div", { class: "dcs-entry-head" });
    head.innerHTML =
      (idx !== undefined && idx !== null ? '<span class="dcs-entry-idx">' + idx + "</span>" : "") +
      '<span class="dcs-entry-label">' + BV.esc(label) + "</span>" +
      '<span class="dcs-entry-tags">' + tagsHtml + "</span>";
    node.appendChild(head);
    node.appendChild(bodyEl);
    BV.collapsible(node, head, bodyEl);
    return node;
  }

  function renderListDetail(sec, showEmpty) {
    var box = BV.el("div");
    if (sec.preamble && sec.preamble.length) {
      var pre = BV.el("div", { class: "dcs-preamble" });
      pre.innerHTML = sec.preamble.map(function (p) {
        return "<span>" + BV.esc(p.text) + "</span>" + statusPill(p.status);
      }).join("");
      box.appendChild(pre);
    }
    sec.entries.forEach(function (e) {
      if (!showEmpty && !e.active) return;
      var body = BV.el("div", { class: "dcs-entry-body" });
      if (e.detail && e.detail.length) body.appendChild(detailList(e.detail));
      else body.innerHTML = '<div class="dim" style="padding:.3rem .2rem">no parameters</div>';
      var node = entryNode(e.label, e.index, entryTags(e), body);
      if (!e.active) node.classList.add("dim");
      box.appendChild(node);
    });
    return box;
  }

  /* CPC position(mm) sub-table: axis labels (9 X) + column headers
     (Current/Point 1/Point 2) take the label colour, the values stay plain. */
  function posTableHtml(pt) {
    var cols = (pt.headers && pt.headers.length) || (pt.rows[0] && pt.rows[0].values.length) || 1;
    var h = '<div class="dcs-pos" style="grid-column:1/-1">' +
      '<div class="dcs-detail-sub">position (mm)</div>' +
      '<div class="dcs-pos-grid" style="grid-template-columns:auto repeat(' + cols + ',auto)">' +
      "<span></span>";
    (pt.headers || []).forEach(function (c) { h += '<span class="dcs-pos-h">' + BV.esc(c) + "</span>"; });
    pt.rows.forEach(function (row) {
      h += '<span class="dcs-pos-axis">' + BV.esc(row.axis) + "</span>";
      row.values.forEach(function (v) { h += '<span class="dcs-pos-v">' + BV.esc(v) + "</span>"; });
    });
    return h + "</div></div>";
  }

  function detailList(items) {
    var dl = BV.el("dl", { class: "kv dcs-kv" });
    items.forEach(function (d) {
      if (d.axes !== undefined) {
        var ax = '<div class="dcs-axes" style="grid-column:1/-1">';
        d.axes.forEach(function (a) {
          ax += '<span><span class="ax-l">' + BV.esc(a[0]) + ':</span> ' +
            '<span class="ax-v">' + BV.esc(a[1]) + "</span></span>";
        });
        dl.insertAdjacentHTML("beforeend", ax + "</div>");
      } else if (d.pos_table !== undefined) {
        dl.insertAdjacentHTML("beforeend", posTableHtml(d.pos_table));
      } else if (d.sub !== undefined) {
        dl.insertAdjacentHTML("beforeend",
          '<dt class="dcs-detail-sub" style="grid-column:1/-1">' + BV.esc(d.sub) + "</dt>");
      } else if (d.key !== undefined) {
        dl.insertAdjacentHTML("beforeend",
          "<dt>" + BV.esc(d.key) + "</dt><dd>" + BV.esc(d.value || "—") + "</dd>");
      } else {
        dl.insertAdjacentHTML("beforeend",
          '<dt class="dcs-raw" style="grid-column:1/-1;color:var(--sub)">' + BV.esc(d.raw) + "</dt>");
      }
    });
    return dl;
  }

  /* user model: each model drops down to its elements as nested collapsible
     headers, and each element drops down to its own fields (headers on headers) */
  function renderUserModel(sec, showEmpty) {
    var box = BV.el("div");
    sec.entries.forEach(function (e) {
      if (!showEmpty && !e.active) return;
      var body = BV.el("div", { class: "dcs-entry-body" });
      if (e.detail && e.detail.length) body.appendChild(detailList(e.detail));
      (e.elements || []).forEach(function (el) {
        var ebody = BV.el("div", { class: "dcs-entry-body" });
        ebody.appendChild(detailList(el.detail));
        body.appendChild(entryNode("element " + el.num, null, statusPill(el.status), ebody));
      });
      if (!(e.elements && e.elements.length) && !(e.detail && e.detail.length)) {
        body.innerHTML = '<div class="dim" style="padding:.3rem .2rem">no elements</div>';
      }
      var node = entryNode(e.label, e.index, entryTags(e), body);
      if (!e.active) node.classList.add("dim");
      box.appendChild(node);
    });
    return box;
  }

  function renderGroupedList(sec) {
    var box = BV.el("div");
    sec.groups.forEach(function (g) {
      box.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);margin:.6rem 0 .4rem">group ' + g.group + "</h3>");
      g.entries.forEach(function (e) {
        var changed = e.detail.some(function (d) { return d.status && d.status !== "OK"; });
        var body = BV.el("div", { class: "dcs-entry-body" });
        var inner = "";
        e.detail.forEach(function (d) {
          if (d.sub) inner += '<div class="dcs-sub">' + BV.esc(d.text) + "</div>";
          else inner += '<div class="dcs-line"><span>' + BV.esc(d.text) + "</span>" + statusPill(d.status) + "</div>";
        });
        body.innerHTML = inner;
        box.appendChild(entryNode(e.label, null, changed ? BV.pill("changed", "warn") : "", body));
      });
    });
    return box;
  }

  function dcsFrameCard(f, isTool) {
    var pills = [];
    if (!f.used) pills.push(["unused", "ghost"]);
    if (f.status) pills.push([f.status.toLowerCase(), statusVariant(f.status)]);
    return BV.frameCard({
      title: (isTool ? "tool " : "uframe ") + f.index,
      pills: pills,
      subtitle: (isTool && f.signal && f.signal.indexOf("---[") !== 0) ? f.signal : undefined,
      axes: ["x", "y", "z", "w", "p", "r"].map(function (ax) { return [ax, BV.fmt.num(f[ax])]; }),
    });
  }

  function renderFrames(sec, showEmpty) {
    var box = BV.el("div");
    sec.groups.forEach(function (g) {
      var frames = g.frames.filter(function (f) { return showEmpty || f.used; });
      if (!frames.length) return;
      box.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);margin:.4rem 0 .6rem">group ' + g.group +
        ' <span style="color:var(--sub-alt)">' + frames.length + "/" + g.frames.length + "</span></h3>");
      var grid = BV.el("div", { class: "cards", style: "grid-template-columns:repeat(auto-fill,minmax(200px,1fr));margin-bottom:1rem" });
      frames.forEach(function (f) { grid.appendChild(dcsFrameCard(f, sec.is_tool)); });
      box.appendChild(grid);
    });
    if (!box.children.length) {
      box.innerHTML = '<div class="dim" style="padding:.4rem">no frames set — “show empty” to see all slots</div>';
    }
    return box;
  }

  function renderLogic(sec, showEmpty) {
    var box = BV.el("div", { class: "dcs-logic" });
    var any = false;
    sec.rows.forEach(function (r) {
      if (!showEmpty && r.empty) return;
      any = true;
      box.insertAdjacentHTML("beforeend",
        '<div class="dcs-logic-row' + (r.empty ? " dim" : "") + '">' +
        '<code>' + highlightLogic(r.text) + "</code>" + statusPill(r.status) + "</div>");
    });
    if (!any) box.innerHTML = '<div class="dim" style="padding:.4rem">no configured logic — “show empty” to see all slots</div>';
    return box;
  }

  function renderTable(sec, showEmpty) {
    var rows = sec.rows.filter(function (r) { return showEmpty || !r.empty; });
    return BV.table([
      { key: "index", label: "#", num: true, dim: true },
      { key: "sig1", label: "signal 1" },
      { key: "sig2", label: "signal 2" },
      { key: "time", label: "time (ms)", num: true },
      { key: "status", label: "status", render: function (r) { return statusPill(r.status); } },
    ], rows);
  }

  function renderTableGrouped(sec) {
    var box = BV.el("div");
    sec.groups.forEach(function (g) {
      box.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);margin:.6rem 0 .4rem">group ' + g.group + "</h3>");
      box.appendChild(BV.table([
        { key: "axis", label: "axis" },
        { key: "position", label: "position", dim: true },
        { key: "count", label: "master count", num: true },
        { key: "status", label: "status", render: function (r) { return statusPill(r.status); } },
      ], g.rows));
    });
    return box;
  }

  function renderRaw(sec) {
    var box = BV.el("div", { class: "dcs-raw-rows" });
    var kvDl = null; /* consecutive kv rows share one <dl> so they align */
    sec.rows.forEach(function (r) {
      if (r.kind === "kv") {
        if (!kvDl) { kvDl = BV.el("dl", { class: "kv dcs-kv" }); box.appendChild(kvDl); }
        kvDl.insertAdjacentHTML("beforeend",
          "<dt>" + BV.esc(r.key) + "</dt><dd>" + BV.esc(r.value || "—") +
          (r.status ? " " + statusPill(r.status) : "") + "</dd>");
        return;
      }
      kvDl = null;
      if (r.kind === "subhead") {
        box.insertAdjacentHTML("beforeend",
          '<div class="dcs-sub">' + BV.esc(r.text.toLowerCase()) + "</div>");
        return;
      }
      box.insertAdjacentHTML("beforeend",
        '<div class="dcs-line"><span>' + BV.esc(r.text) + "</span>" + statusPill(r.status) + "</div>");
    });
    return box;
  }

  function renderSection(sec, showEmpty) {
    switch (sec.kind) {
      case "list_detail": return renderListDetail(sec, showEmpty);
      case "user_model": return renderUserModel(sec, showEmpty);
      case "frames": return renderFrames(sec, showEmpty);
      case "grouped_list": return renderGroupedList(sec);
      case "logic": return renderLogic(sec, showEmpty);
      case "table": return renderTable(sec, showEmpty);
      case "table_grouped": return renderTableGrouped(sec);
      default: return renderRaw(sec);
    }
  }

  /* ---- views ---- */

  function fileSeg(files, cur, onPick) {
    return BV.segmented(
      files.map(function (f) {
        return {
          id: f.file,
          label: f.kind,
          count: f.date ? f.date.split(" ")[0].toLowerCase() : undefined,
          title: f.file,
        };
      }),
      { value: cur, controlled: true, onChange: function (id) { onPick(id); } }
    ).el;
  }

  function vsButton(onToggle) {
    if (!BV.state.compare) return null;
    var b = BV.el("button", {
      class: "btn" + (vsOn ? " primary" : ""),
      title: "show this DCS report for both robots side by side",
    }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
    b.addEventListener("click", function () { vsOn = !vsOn; onToggle(); });
    return b;
  }

  /* condensed body for a dashboard cell */
  function dashSummary(sec) {
    if (sec.id === "mastering-parameter") {
      var n = (sec.groups || []).reduce(function (a, g) { return a + g.rows.length; }, 0);
      return '<span class="dim">' + n + " axes mastered</span>";
    }
    var rows = (sec.rows || []).filter(function (r) { return r.kind === "kv"; }).slice(0, 4);
    return rows.map(function (r) {
      return '<div class="dcs-dash-line"><span class="dim">' + BV.esc(r.key) + "</span> " +
        BV.esc(r.value || "") + statusPill(r.status) + "</div>";
    }).join("") || '<span class="dim">—</span>';
  }

  /* stapled top dashboard: shrunk signatures + the small sections inline */
  function dcsDashboard(rep, curFile) {
    var dash = BV.el("div", { class: "dcs-dash stick" });
    if (rep.signatures && rep.signatures.length) {
      var cell = BV.el("div", { class: "dcs-dash-cell" });
      var h = '<div class="dcs-dash-h">signatures ' +
        (rep.all_signatures_match ? BV.pill("match ✓", "ok-soft") : BV.pill("⚠ mismatch", "err")) +
        "</div><div class=\"dcs-sig-strip\">";
      rep.signatures.forEach(function (s) {
        h += '<span class="dcs-sig-chip ' + (s.match ? "ok" : "bad") +
          '" title="' + BV.esc(s.current + " vs " + s.latch) + '">' +
          BV.esc(s.name) + (s.match ? " ✓" : " ✗") + "</span>";
      });
      cell.innerHTML = h + "</div>";
      dash.appendChild(cell);
    }
    [["robot-setup", "robot"], ["cip-safety", "cip safety"], ["mastering-parameter", "mastering"]]
      .forEach(function (pair) {
        var sec = rep.sections.find(function (s) { return s.id === pair[0]; });
        if (!sec) return;
        var cell = BV.el("div", { class: "dcs-dash-cell clickable", title: "open " + sec.title });
        cell.innerHTML = '<div class="dcs-dash-h">' + BV.esc(pair[1]) + " " + menuBadge(sec.summary) +
          "</div><div class=\"dcs-dash-body\">" + dashSummary(sec) + "</div>";
        cell.addEventListener("click", function () {
          location.hash = "#dcs/" + encodeURIComponent(curFile) + "/" + sec.id;
        });
        dash.appendChild(cell);
      });
    return dash;
  }

  function renderLanding(view, toolbar, files, curFile) {
    toolbar.appendChild(fileSeg(files, curFile, function (f) { location.hash = "#dcs/" + encodeURIComponent(f); }));
    /* vs has no two-up view on the menu itself - it lights the toggle so the
       section pages you drill into open side-by-side */
    var vb = vsButton(function () { render(view, toolbar, [curFile]); });
    if (vb) toolbar.appendChild(vb);

    BV.api.call("get_dcs", curFile).then(function (rep) {
      view.appendChild(dcsDashboard(rep, curFile));

      view.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);margin:.2rem 0 .5rem">sections</h3>');
      var menu = BV.el("div", { class: "dcs-menu" });
      rep.sections.forEach(function (sec) {
        var row = BV.el("div", { class: "dcs-menu-row" });
        row.innerHTML = '<span class="dcs-menu-title">' + BV.esc(sec.title) + "</span>" +
          '<span class="dcs-menu-tags">' + menuBadge(sec.summary) +
          '<span class="dcs-caret">›</span></span>';
        row.addEventListener("click", function () {
          location.hash = "#dcs/" + encodeURIComponent(curFile) + "/" + sec.id;
        });
        menu.appendChild(row);
      });
      view.appendChild(menu);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  function renderSectionPage(view, toolbar, files, curFile, sectionId) {
    var showEmpty = false;
    var crumb = BV.el("div", { class: "crumb" });
    crumb.innerHTML = '<span class="back" id="dcs-back">← back</span>' +
      '<span class="back" id="dcs-home">dcs</span><span class="title" id="dcs-sectitle"></span>';
    crumb.querySelector("#dcs-back").addEventListener("click", function () { history.back(); });
    crumb.querySelector("#dcs-home").addEventListener("click", function () {
      location.hash = "#dcs/" + encodeURIComponent(curFile);
    });
    view.appendChild(crumb);

    var emptyBtn = BV.el("button", { class: "btn", title: "unconfigured / ---[ 0] rows" }, "show empty");
    /* reports are PAGES, not filters: switching reports lands on that report's
       own dashboard (its sections differ), never an absent section */
    toolbar.appendChild(fileSeg(files, curFile, function (f) {
      location.hash = "#dcs/" + encodeURIComponent(f);
    }));
    toolbar.appendChild(emptyBtn);
    var vb = vsButton(function () { draw(); });
    if (vb) toolbar.appendChild(vb);

    var host = BV.el("div");
    view.appendChild(host);

    emptyBtn.addEventListener("click", function () {
      showEmpty = !showEmpty;
      emptyBtn.textContent = showEmpty ? "hide empty" : "show empty";
      draw();
    });

    function column(rep, robotName) {
      var sec = rep.sections.find(function (s) { return s.id === sectionId; });
      var col = BV.el("div", { style: "min-width:0" });
      if (robotName) {
        col.insertAdjacentHTML("beforeend",
          '<div class="mt-label" style="font-size:.92rem">' + BV.esc(robotName) + "</div>");
      }
      if (!sec) {
        col.insertAdjacentHTML("beforeend",
          '<div class="dim" style="font-size:.8rem">this report has no “' + BV.esc(sectionId) + '” section</div>');
        return col;
      }
      crumb.querySelector("#dcs-sectitle").textContent = sec.title;
      col.appendChild(renderSection(sec, showEmpty));
      return col;
    }

    function draw() {
      host.innerHTML = "";
      if (vsOn && BV.state.compare) {
        Promise.all([
          BV.api.call("get_dcs", curFile, "a"),
          BV.api.call("get_dcs", curFile, "b").catch(function (e) { return { error: e.message }; }),
        ]).then(function (res) {
          var grid = BV.el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;align-items:start" });
          grid.appendChild(column(res[0], BV.state.manifest.robot_name || BV.state.manifest.name));
          if (res[1].error) {
            grid.insertAdjacentHTML("beforeend",
              '<div><div class="mt-label" style="font-size:.92rem">' +
              BV.esc(BV.state.compare.robot_name || BV.state.compare.name) + "</div>" +
              '<div class="dim" style="font-size:.8rem">' + BV.esc(res[1].error) + "</div></div>");
          } else {
            grid.appendChild(column(res[1], BV.state.compare.robot_name || BV.state.compare.name));
          }
          host.appendChild(grid);
        });
        return;
      }
      BV.api.call("get_dcs", curFile).then(function (rep) {
        host.appendChild(column(rep, null));
      }).catch(function (e) {
        host.innerHTML = '<div class="empty-state"><div class="hint">' + BV.esc(e.message) + "</div></div>";
      });
    }
    draw();
  }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    BV.api.call("get_dcs_files").then(function (files) {
      var curFile = (params && params[0] && files.find(function (f) {
        return f.file.toUpperCase() === decodeURIComponent(params[0]).toUpperCase();
      }) ? decodeURIComponent(params[0]) : files[0].file);
      var sectionId = params && params[1];
      if (sectionId) renderSectionPage(view, toolbar, files, curFile, sectionId);
      else renderLanding(view, toolbar, files, curFile);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">no dcs reports</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "dcs", label: "dcs", render: render });
})();
