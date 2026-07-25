/* tabs/edit.js - the multi-robot EDIT WORKSPACE (#edit).

   A shell screen (like home): it owns the whole window and renders with zero
   backups open, because a working set spans robots while the tabbar and
   BV.tabState are per-backup. It replaces the old in-place edit toggle that
   lived in the programs tab - one editing surface, no drift.

   Layout: a resizable rail (working set | find/replace) beside two editor
   panes, each with its own tab strip; the panes are separated by a second
   resizer. Programs are read and exported through the path-addressed ws_*
   endpoints (see BV.workspace), so the workspace never opens a session.

   All state here is MODULE-LEVEL - route() rebuilds the DOM on every hash
   change, so nothing may live in the DOM alone. */
(function () {
  "use strict";

  var st = {
    panes: [{ tabs: [], active: 0 }, { tabs: [], active: 0 }],
    activePane: 0,
    railTab: "set",          /* set | find */
    setFolds: {},            /* working-set robot folds (root -> true = folded) */
    frFolds: {},             /* find-result folds */
    details: [false, false], /* per-pane attrs/positions panel */
    editors: [null, null],
    selRow: null,
  };
  var fr = { find: "", repl: "", ignoreComment: true, includeRemarks: false, scope: {} };

  /* live DOM handles for the current render (nulled by the next route) */
  var el = {};
  var drag = null;

  function keyOf(e) { return e.root + BV.KEYSEP + e.file; }

  /* ------------------------------------------------------------------ *
   * render
   * ------------------------------------------------------------------ */
  function render(view, toolbar, params) {
    BV.workspace.load();
    view.classList.add("no-pad");
    el = {};

    /* toolbar: counts + export */
    var bar = BV.el("div", { style: "display:flex;gap:.6rem;align-items:center;flex-wrap:wrap" });
    el.counts = BV.el("span", { class: "dim", style: "font-size:.78rem" });
    bar.appendChild(el.counts);
    bar.appendChild(BV.el("span", { style: "flex:1" }));
    el.hint = BV.el("span", { class: "dim", style: "font-size:.72rem" },
      "dbl-click or drag to open · ctrl+tab cycles tabs · ctrl+f finds the selection");
    bar.appendChild(el.hint);
    el.exportBtn = BV.el("button", { class: "btn primary" }, "export…");
    el.exportBtn.addEventListener("click", showExport);
    bar.appendChild(el.exportBtn);
    toolbar.appendChild(bar);

    var shell = BV.el("div", { class: "ws" });
    view.appendChild(shell);

    /* rail */
    var rail = BV.el("div", { class: "ws-rail" });
    var savedW = BV.state.settings && BV.state.settings.ws_rail_w;
    if (savedW) rail.style.width = savedW + "px";
    el.rail = rail;
    el.railTabs = BV.el("div", { class: "ws-railtabs" });
    el.railHead = BV.el("div", { class: "ws-railhead" });
    el.railBody = BV.el("div", { class: "ws-railbody" });
    rail.appendChild(el.railTabs);
    rail.appendChild(el.railHead);
    rail.appendChild(el.railBody);
    shell.appendChild(rail);
    shell.appendChild(makeResizer(function (dx, startW) {
      var w = Math.max(176, Math.min(736, startW + dx));
      rail.style.width = w + "px";
      return w;
    }, function (w) {
      BV.api.call("set_setting", "ws_rail_w", w).catch(function () {});
      if (BV.state.settings) BV.state.settings.ws_rail_w = w;
    }, function () { return el.rail.getBoundingClientRect().width; }));

    el.work = BV.el("div", { class: "ws-work" });
    shell.appendChild(el.work);

    renderRail();
    renderPanes();
    updateCounts();
    return true;
  }

  /* a generic drag handle: onMove(dx, startValue) -> current value, onDone(value) */
  function makeResizer(onMove, onDone, startValue) {
    var rz = BV.el("div", { class: "ws-resizer", title: "drag to resize" });
    rz.addEventListener("mousedown", function (e) {
      e.preventDefault();
      var x0 = e.clientX, start = startValue(), cur = start;
      rz.classList.add("dragging");
      function mv(ev) { cur = onMove(ev.clientX - x0, start); }
      function up() {
        document.removeEventListener("mousemove", mv);
        document.removeEventListener("mouseup", up);
        rz.classList.remove("dragging");
        if (onDone) onDone(cur);
      }
      document.addEventListener("mousemove", mv);
      document.addEventListener("mouseup", up);
    });
    return rz;
  }

  function updateCounts() {
    if (!el.counts) return;
    var n = BV.workspace.count(), d = BV.workspace.dirtyCount();
    var robots = BV.workspace.byRobot().length;
    el.counts.textContent = n
      ? robots + (robots === 1 ? " robot · " : " robots · ") + n + " programs" +
        (d ? " · " + d + " edited" : "")
      : "nothing in the workspace yet";
    el.exportBtn.textContent = d ? "export " + d + " file" + (d === 1 ? "" : "s") + "…" : "export…";
    el.exportBtn.disabled = !d;
  }
  /* counts are safe any time; the RAIL must never be rebuilt while find owns
     it (clicking a result would destroy the list being worked through) */
  function afterChange() {
    updateCounts();
    if (st.railTab === "set") renderWorkingSet();
  }

  /* ------------------------------------------------------------------ *
   * rail
   * ------------------------------------------------------------------ */
  function renderRail(prefill) {
    el.railTabs.innerHTML = "";
    [["set", "working set"], ["find", "find/replace"]].forEach(function (t) {
      var b = BV.el("div", { class: "ws-railtab" + (st.railTab === t[0] ? " active" : "") }, t[1]);
      b.addEventListener("click", function () {
        if (st.railTab === t[0]) return;
        st.railTab = t[0];
        renderRail();
      });
      el.railTabs.appendChild(b);
    });
    if (st.railTab === "find") renderFind(prefill);
    else renderWorkingSet();
  }

  function renderWorkingSet() {
    el.railHead.innerHTML = "";
    el.railHead.appendChild(BV.el("span", null, BV.workspace.count() + " programs"));
    var acts = BV.el("span", { style: "margin-left:auto;display:flex;gap:.3rem" });
    if (BV.workspace.count()) {
      var clr = BV.el("button", { class: "btn", style: "padding:.1rem .4rem;font-size:.7rem",
        title: "remove everything from the workspace (edits are lost)" }, "clear");
      clr.addEventListener("click", function () {
        if (BV.workspace.anyDirty() && !confirmDiscard()) return;
        BV.workspace.clear();
        st.panes.forEach(function (p) { p.tabs = []; p.active = 0; });
        renderPanes();
        afterChange();
      });
      acts.appendChild(clr);
    }
    el.railHead.appendChild(acts);

    el.railBody.innerHTML = "";
    var groups = BV.workspace.byRobot();
    if (!groups.length) {
      el.railBody.appendChild(BV.el("div", { class: "ws-empty" },
        '<div class="big">the workspace is empty</div>' +
        '<div class="hint">add programs from the programs tab, a compare, ' +
        "or right-click a robot in the library</div>"));
      return;
    }
    groups.forEach(function (g) {
      var folded = !!st.setFolds[g.root];
      var dirtyN = g.programs.filter(function (e) {
        return BV.workspace.dirty(e.root, e.file);
      }).length;
      var h = BV.el("div", { class: "ws-robot-h", title: g.root });
      /* the caret folds this robot - a rail with 8 robots is unusable otherwise */
      var caret = BV.el("span", { class: "caret" }, folded ? "▸" : "▾");
      h.appendChild(caret);
      h.appendChild(BV.el("span", { class: "nm" }, BV.esc(g.label)));
      var right = BV.el("span", { style: "margin-left:auto;display:flex;gap:.3rem;align-items:center" });
      if (dirtyN) right.innerHTML = BV.pill(dirtyN + " edited", "warn");
      right.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem" },
        String(g.programs.length)));
      h.appendChild(right);
      h.addEventListener("click", function () {
        st.setFolds[g.root] = !folded;
        renderWorkingSet();
      });
      el.railBody.appendChild(h);
      if (folded) return;

      g.programs.forEach(function (e) {
        var dirty = BV.workspace.dirty(e.root, e.file);
        var row = BV.el("div", { class: "ws-prog", draggable: "true", title: e.file });
        if (st.selRow === keyOf(e)) row.classList.add("sel");
        row.innerHTML = '<span class="dot' + (dirty ? " dirty" : "") + '"></span>' +
          '<span class="nm">' + BV.esc(e.name) + "</span>";
        var x = BV.el("span", { class: "rm", title: "remove from the workspace" }, "✕");
        x.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (dirty && !confirmDiscard()) return;
          closeEverywhere(e);
          BV.workspace.remove(e.root, e.file);
          renderPanes();
          afterChange();
        });
        row.appendChild(x);
        row.addEventListener("click", function () {
          st.selRow = keyOf(e);
          renderWorkingSet();
        });
        row.addEventListener("dblclick", function () { openTab(e, st.activePane); });
        row.addEventListener("dragstart", function (ev) {
          drag = { kind: "prog", entry: e };
          try { ev.dataTransfer.setData("text/plain", e.name); } catch (x2) {}
        });
        row.addEventListener("dragend", function () { drag = null; hideDrop(); });
        el.railBody.appendChild(row);
      });
    });
  }

  function confirmDiscard() {
    /* one-shot confirm: the modal-free version of BV.dirtyGuard's press-again */
    return window.confirm("There are unsaved edits. Discard them?");
  }

  /* ------------------------------------------------------------------ *
   * panes + tabs
   * ------------------------------------------------------------------ */
  function openTab(entry, paneIdx) {
    var pane = st.panes[paneIdx] || st.panes[0];
    var at = -1;
    pane.tabs.forEach(function (t, i) {
      if (t.root === entry.root && t.file === entry.file) at = i;
    });
    if (at < 0) { pane.tabs.push({ root: entry.root, file: entry.file, name: entry.name }); at = pane.tabs.length - 1; }
    pane.active = at;
    st.activePane = st.panes.indexOf(pane);
    renderPanes();
    afterChange();
  }
  function closeTab(paneIdx, idx) {
    var pane = st.panes[paneIdx];
    pane.tabs.splice(idx, 1);
    if (pane.active >= pane.tabs.length) pane.active = pane.tabs.length - 1;
    renderPanes();
  }
  function closeEverywhere(entry) {
    st.panes.forEach(function (pane) {
      pane.tabs = pane.tabs.filter(function (t) {
        return !(t.root === entry.root && t.file === entry.file);
      });
      if (pane.active >= pane.tabs.length) pane.active = pane.tabs.length - 1;
    });
  }
  function cycleTab(dir) {
    var pane = st.panes[st.activePane];
    if (!pane || pane.tabs.length < 2) return;
    pane.active = (pane.active + dir + pane.tabs.length) % pane.tabs.length;
    renderPanes();
  }
  function hideDrop() { if (el.dropzone) el.dropzone.classList.remove("show"); }

  function renderPanes() {
    if (!el.work) return;
    el.work.innerHTML = "";
    var wrap = BV.el("div", { class: "ws-panes" });
    el.work.appendChild(wrap);
    el.dropzone = BV.el("div", { class: "dropzone" });
    wrap.appendChild(el.dropzone);

    wrap.addEventListener("dragover", function (e) {
      if (!drag) return;
      e.preventDefault();
      var r = wrap.getBoundingClientRect();
      el.dropzone.style.left = ((e.clientX - r.left) > r.width / 2) ? "50%" : "0";
      el.dropzone.classList.add("show");
    });
    wrap.addEventListener("dragleave", function (e) { if (e.target === wrap) hideDrop(); });
    wrap.addEventListener("drop", function (e) {
      if (!drag) return;
      e.preventDefault();
      var r = wrap.getBoundingClientRect();
      var target = (e.clientX - r.left) > r.width / 2 ? 1 : 0;
      var d = drag; drag = null; hideDrop();
      if (d.kind === "tab") {
        if (d.fromPane === target) return;
        st.panes[d.fromPane].tabs.splice(d.idx, 1);
        if (st.panes[d.fromPane].active >= st.panes[d.fromPane].tabs.length) {
          st.panes[d.fromPane].active = st.panes[d.fromPane].tabs.length - 1;
        }
      }
      openTab(d.entry, target);
    });

    st.panes.forEach(function (pane, i) {
      if (i === 1) {
        /* the resizer BETWEEN the two program windows */
        wrap.appendChild(makeResizer(function (dx, startPx) {
          var total = wrap.getBoundingClientRect().width;
          var px = Math.max(160, Math.min(total - 160, startPx + dx));
          el.paneEls[0].style.flex = "0 0 " + px + "px";
          el.paneEls[1].style.flex = "1 1 auto";
          return px;
        }, null, function () { return el.paneEls[0].getBoundingClientRect().width; }));
      }
      var p = BV.el("div", { class: "ws-pane" + (st.activePane === i ? " active" : "") });
      (el.paneEls = el.paneEls || [])[i] = p;
      p.addEventListener("mousedown", function () {
        if (st.activePane !== i) {
          st.activePane = i;
          if (el.paneEls[0]) el.paneEls[0].classList.toggle("active", i === 0);
          if (el.paneEls[1]) el.paneEls[1].classList.toggle("active", i === 1);
        }
      });

      var strip = BV.el("div", { class: "ws-tabs" });
      /* a vertical wheel pans the strip: with a mouse you are otherwise stuck */
      strip.addEventListener("wheel", function (ev) {
        if (!ev.deltaY) return;
        strip.scrollLeft += ev.deltaY;
        ev.preventDefault();
      }, { passive: false });
      pane.tabs.forEach(function (t, ti) {
        var dirty = BV.workspace.dirty(t.root, t.file);
        var tab = BV.el("div", { class: "ws-tab" + (pane.active === ti ? " active" : ""),
                                 draggable: "true", title: t.root + "\n" + t.file });
        tab.innerHTML = '<span class="rb">' + BV.esc(labelFor(t.root)) + "</span>" +
          "<span>" + BV.esc(t.name) + "</span>" +
          (dirty ? '<span class="dot"></span>' : "");
        var x = BV.el("span", { class: "x" }, "✕");
        x.addEventListener("click", function (ev) { ev.stopPropagation(); closeTab(i, ti); });
        tab.appendChild(x);
        tab.addEventListener("click", function () {
          pane.active = ti; st.activePane = i; renderPanes();
        });
        tab.addEventListener("dragstart", function (ev) {
          drag = { kind: "tab", entry: t, fromPane: i, idx: ti };
          try { ev.dataTransfer.setData("text/plain", t.name); } catch (x2) {}
        });
        tab.addEventListener("dragend", function () { drag = null; hideDrop(); });
        strip.appendChild(tab);
      });
      if (!pane.tabs.length) {
        strip.appendChild(BV.el("span", { class: "dim", style: "font-size:.72rem;padding:.25rem" },
          "drop or double-click a program"));
      } else {
        var dt = BV.el("button", { class: "btn ws-detbtn",
          title: "attributes + point data (rarely edited)" }, st.details[i] ? "hide details" : "details");
        dt.addEventListener("click", function () {
          st.details[i] = !st.details[i];
          renderPanes();
        });
        strip.appendChild(dt);
      }
      p.appendChild(strip);

      var host = BV.el("div", { class: "ws-pane-host" });
      p.appendChild(host);
      wrap.appendChild(p);

      var t = pane.tabs[pane.active];
      if (!t) {
        host.innerHTML = '<div class="empty-state" style="height:100%">' +
          '<div class="hint">drop a program here</div></div>';
        st.editors[i] = null;
        return;
      }
      host.innerHTML = '<div class="dim" style="padding:1rem">loading…</div>';
      BV.workspace.buffer(t.root, t.file).then(function (buf) {
        if (!document.body.contains(host)) return;   /* routed away mid-load */
        host.innerHTML = "";
        st.editors[i] = BV.lsEditor(host, {
          text: buf.text,
          onChange: function (txt) {
            BV.workspace.setBody(t.root, t.file, txt);
            updateCounts();
            markTabDirty(i, pane.active);
            if (st.railTab === "set") renderWorkingSet();
          },
        });
        if (st.details[i]) p.appendChild(detailsPanel(t, buf, i));
      }).catch(function (e) {
        host.innerHTML = '<div class="empty-state"><div class="hint">' +
          BV.esc(e.message || e) + "</div></div>";
      });
    });
  }

  function markTabDirty(paneIdx, tabIdx) {
    var pEl = el.paneEls && el.paneEls[paneIdx];
    if (!pEl) return;
    var tab = pEl.querySelectorAll(".ws-tab")[tabIdx];
    if (tab && !tab.querySelector(".dot")) {
      tab.insertBefore(BV.el("span", { class: "dot" }), tab.querySelector(".x"));
    }
  }

  function labelFor(root) {
    var g = BV.workspace.byRobot().filter(function (x) { return x.root === root; })[0];
    return g ? g.label : root.split(/[\\/]/).pop();
  }

  /* ------------------------------------------------------------------ *
   * details: editable attributes + point data
   * ------------------------------------------------------------------ */
  function detailsPanel(t, buf, paneIdx) {
    var box = BV.el("div", { class: "ws-details scrollbody" });

    var ac = BV.el("div", { class: "card" });
    ac.appendChild(BV.el("h3", null, "attributes"));
    function textRow(label, name) {
      var row = BV.el("div", { class: "edattr-row" });
      row.appendChild(BV.el("label", null, label));
      var inp = BV.el("input", { type: "text", style: "flex:1" });
      inp.value = buf.attrs[name] || "";
      inp.addEventListener("input", function () {
        BV.workspace.setAttr(t.root, t.file, name, inp.value);
        updateCounts();
        markTabDirty(paneIdx, st.panes[paneIdx].active);
      });
      row.appendChild(inp);
      return row;
    }
    ac.appendChild(textRow("comment", "comment"));
    ac.appendChild(textRow("owner", "owner"));
    var prot = (buf.baseAttrs || {}).protect;
    var prow = BV.el("div", { class: "edattr-row" });
    prow.appendChild(BV.el("label", null, "protect"));
    if (prot === "READ_WRITE" || prot === "READ") {
      var seg = BV.segmented(
        [{ id: "READ_WRITE", label: "read_write" }, { id: "READ", label: "read" }],
        { value: buf.attrs.protect, onChange: function (id) {
            BV.workspace.setAttr(t.root, t.file, "protect", id);
            updateCounts();
          } });
      prow.appendChild(seg.el);
    } else {
      /* an unknown value: show it honestly rather than offer switches we
         cannot prove the controller accepts */
      prow.appendChild(BV.el("span", { class: "dim" }, BV.esc(prot || "—")));
    }
    ac.appendChild(prow);
    box.appendChild(ac);

    var positions = buf.positions || [];
    if (positions.length) {
      var pc = BV.el("div", { class: "card edpos" });
      pc.innerHTML = '<h3>positions <span class="count">' + positions.length + "</span></h3>";
      var isNum = function (v) { return /^-?\d+(\.\d+)?$/.test(v); };
      var isUfUt = function (v) { return /^(\d+|F)$/i.test(v); };
      function field(id, gp, name, current, masked, check) {
        var cell = BV.el("div", { class: "cell" });
        cell.appendChild(BV.el("span", null, BV.esc(name)));
        var inp = BV.el("input", { type: "text" });
        var edited = BV.workspace.getPos(t.root, t.file, id, gp, name);
        inp.value = edited !== null ? edited
          : (masked ? "" : (current === null || current === undefined ? "" : current));
        if (masked) inp.placeholder = "********";
        inp.addEventListener("change", function () {
          var v = inp.value.trim();
          if (v === "" || (!masked && String(current) === v)) {
            inp.classList.remove("err");
            BV.workspace.setPos(t.root, t.file, { id: id, gp: gp, field: name, value: null });
            if (!masked && v === "") inp.value = current === null ? "" : current;
          } else if (check(v)) {
            inp.classList.remove("err");
            BV.workspace.setPos(t.root, t.file, { id: id, gp: gp, field: name, value: v });
          } else {
            inp.classList.add("err");
            BV.workspace.setPos(t.root, t.file, { id: id, gp: gp, field: name, value: null });
          }
          updateCounts();
        });
        cell.appendChild(inp);
        return cell;
      }
      positions.forEach(function (pos) {
        var head = BV.el("div", { class: "edpos-p" });
        head.appendChild(BV.el("span", { class: "pid" }, "P[" + pos.id + "]"));
        var cmt = BV.el("input", { type: "text", placeholder: "comment" });
        var ce = BV.workspace.getPos(t.root, t.file, pos.id, 1, "comment");
        cmt.value = ce !== null ? ce : (pos.comment || "");
        cmt.addEventListener("change", function () {
          BV.workspace.setPos(t.root, t.file, { id: pos.id, gp: 1, field: "comment",
            value: cmt.value === (pos.comment || "") ? null : cmt.value });
          updateCounts();
        });
        head.appendChild(cmt);
        pc.appendChild(head);
        (pos.groups || []).forEach(function (g) {
          if ((pos.groups || []).length > 1) {
            pc.appendChild(BV.el("div", { class: "dim", style: "margin-left:.4rem;font-size:.72rem" },
              "gp" + g.gp));
          }
          var grid = BV.el("div", { class: "edpos-grid" });
          if (g.kind === "joint") {
            (g.joints || []).forEach(function (jv, ji) {
              grid.appendChild(field(pos.id, g.gp, "j" + (ji + 1), jv,
                g.masked && jv === null, isNum));
            });
          } else {
            ["x", "y", "z", "w", "p", "r"].forEach(function (ax) {
              grid.appendChild(field(pos.id, g.gp, ax, g[ax],
                g.masked && (g[ax] === null || g[ax] === undefined), isNum));
            });
          }
          grid.appendChild(field(pos.id, g.gp, "uf", g.uf, false, isUfUt));
          grid.appendChild(field(pos.id, g.gp, "ut", g.ut, false, isUfUt));
          var cfg = BV.el("div", { class: "cell", style: "grid-column:1/-1" });
          cfg.appendChild(BV.el("span", null, "cfg"));
          var cin = BV.el("input", { type: "text" });
          var cfe = BV.workspace.getPos(t.root, t.file, pos.id, g.gp, "config");
          cin.value = cfe !== null ? cfe : (g.config || "");
          cin.addEventListener("change", function () {
            if (cin.value.indexOf("'") >= 0) { cin.classList.add("err"); return; }
            cin.classList.remove("err");
            BV.workspace.setPos(t.root, t.file, { id: pos.id, gp: g.gp, field: "config",
              value: cin.value === (g.config || "") ? null : cin.value });
            updateCounts();
          });
          cfg.appendChild(cin);
          grid.appendChild(cfg);
          pc.appendChild(grid);
        });
      });
      box.appendChild(pc);
    }
    return box;
  }

  /* ------------------------------------------------------------------ *
   * find / replace
   * ------------------------------------------------------------------ */
  var REF = /^([A-Z]+)\[(\d+)\]$/;

  function findHits(text, needle) {
    var hits = [];
    if (!needle) return hits;
    var rx, m = REF.exec(needle.trim());
    /* an IO/register reference matches with or WITHOUT its comment - and the
       pendant's IO-status view adds a THIRD field (DO[495:OFF:Name]), so the
       optional tail is "everything up to the closing bracket" */
    if (m && fr.ignoreComment) rx = new RegExp("\\b" + m[1] + "\\[" + m[2] + "(?::[^\\]]*)?\\]", "g");
    else rx = new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
    text.split("\n").forEach(function (line, i) {
      if (!fr.includeRemarks && line.trim().charAt(0) === "!") return;
      rx.lastIndex = 0;
      var mm, spans = [];
      while ((mm = rx.exec(line)) !== null) {
        spans.push([mm.index, mm.index + mm[0].length, mm[0]]);
        if (mm.index === rx.lastIndex) rx.lastIndex++;
      }
      if (spans.length) hits.push({ line: i, text: line, spans: spans });
    });
    return hits;
  }
  function applyHit(line, spans, repl) {
    var out = "", last = 0;
    spans.forEach(function (s) { out += line.slice(last, s[0]) + repl; last = s[1]; });
    return out + line.slice(last);
  }
  function snipHtml(line, spans) {
    var lead = line.length - line.replace(/^\s+/, "").length;
    var t = line.trim(), out = "", last = 0;
    spans.forEach(function (s) {
      out += BV.esc(t.slice(last, s[0] - lead)) + "<mark>" + BV.esc(s[2]) + "</mark>";
      last = s[1] - lead;
    });
    return out + BV.esc(t.slice(last));
  }

  function renderFind(prefill) {
    el.railHead.innerHTML = "";
    el.railHead.appendChild(BV.el("span", null, "scoped to the working set"));
    el.railBody.innerHTML = "";

    var groups = BV.workspace.byRobot();
    if (!groups.length) {
      el.railBody.appendChild(BV.el("div", { class: "ws-empty" },
        '<div class="hint">add programs first — find/replace works over the working set</div>'));
      return;
    }
    groups.forEach(function (g) { if (!(g.root in fr.scope)) fr.scope[g.root] = true; });

    var panel = BV.el("div", { class: "fp" });
    el.railBody.appendChild(panel);

    var inputs = BV.el("div", { class: "fp-inputs" });
    var fIn = BV.el("input", { type: "text", placeholder: "find" });
    var rIn = BV.el("input", { type: "text", placeholder: "replace with" });
    fIn.value = prefill !== undefined ? prefill : fr.find;
    rIn.value = fr.repl;
    inputs.appendChild(fIn);
    inputs.appendChild(rIn);

    var optRef = BV.el("label", { class: "fp-opt" });
    var cbRef = BV.el("input", { type: "checkbox" });
    cbRef.checked = fr.ignoreComment;
    cbRef.addEventListener("change", function () { fr.ignoreComment = cbRef.checked; paint(); });
    var refTxt = BV.el("span");
    optRef.appendChild(cbRef);
    optRef.appendChild(refTxt);
    inputs.appendChild(optRef);

    var optRem = BV.el("label", { class: "fp-opt" });
    var cbRem = BV.el("input", { type: "checkbox" });
    cbRem.checked = fr.includeRemarks;
    cbRem.addEventListener("change", function () { fr.includeRemarks = cbRem.checked; paint(); });
    optRem.appendChild(cbRem);
    optRem.appendChild(BV.el("span", null, "include ! remarked lines"));
    inputs.appendChild(optRem);

    var scope = BV.el("div", { class: "fp-scope" });
    groups.forEach(function (g) {
      var l = BV.el("label", { title: g.root });
      var cb = BV.el("input", { type: "checkbox" });
      cb.checked = fr.scope[g.root];
      cb.addEventListener("change", function () { fr.scope[g.root] = cb.checked; paint(); });
      l.appendChild(cb);
      l.appendChild(BV.el("span", null, BV.esc(g.label)));
      scope.appendChild(l);
    });
    inputs.appendChild(scope);
    panel.appendChild(inputs);

    var results = BV.el("div", { class: "fp-results" });
    panel.appendChild(results);
    var foot = BV.el("div", { class: "fp-foot" });
    panel.appendChild(foot);

    var picks = BV.checklist({ onChange: function () { updateFoot(); } });
    var lastTotals = {};

    /* searching needs every program's text: buffers are lazy, so pull the ones
       that have not been opened yet (once - they stay cached afterwards) */
    function ensureBuffers() {
      var missing = BV.workspace.entries().filter(function (e) {
        var b = BV.workspace.peek(e.root, e.file);
        return !b || b.base === null;
      });
      if (!missing.length) return Promise.resolve();
      results.innerHTML = '<div class="dim" style="padding:1rem;font-size:.78rem">reading ' +
        missing.length + " program" + (missing.length === 1 ? "" : "s") + "…</div>";
      return Promise.all(missing.map(function (e) {
        return BV.workspace.buffer(e.root, e.file).catch(function () { return null; });
      }));
    }

    function paint() {
      fr.find = fIn.value; fr.repl = rIn.value;
      var isRef = REF.test((fr.find || "").trim());
      optRef.style.display = isRef ? "" : "none";
      if (isRef) {
        refTxt.innerHTML = "match <code>" + BV.esc(fr.find.trim()) +
          "</code> with or without its comment";
      }
      results.innerHTML = "";
      picks.clear();
      var total = 0, scoped = 0;
      BV.workspace.byRobot().forEach(function (g) {
        var on = fr.scope[g.root];
        var per = [];
        g.programs.forEach(function (e) {
          var b = BV.workspace.peek(e.root, e.file);
          if (!b || b.base === null) return;
          var hits = findHits(b.text, fr.find);
          if (hits.length) per.push({ e: e, hits: hits });
        });
        var n = per.reduce(function (a, x) { return a + x.hits.length; }, 0);
        total += n;
        if (!n) return;

        var rbKeys = [];
        per.forEach(function (rec) {
          rec.hits.forEach(function (h) { rbKeys.push(keyOf(rec.e) + "|" + h.line); });
        });
        var rbFolded = !!st.frFolds[g.root];
        var rh = BV.el("div", { class: "fp-rb" + (on ? "" : " excluded") });
        rh.appendChild(picks.group(BV.el("input", { type: "checkbox" }),
          function () { return rbKeys; }, "rb:" + g.root));
        var caret = BV.el("span", { class: "caret" }, rbFolded ? "▸" : "▾");
        caret.addEventListener("click", function (ev) {
          ev.stopPropagation();
          st.frFolds[g.root] = !rbFolded;
          paint();
        });
        rh.appendChild(caret);
        rh.appendChild(BV.el("span", null, BV.esc(g.label)));
        rh.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem;margin-left:auto" },
          n + (on ? "" : " excluded")));
        results.appendChild(rh);
        if (!on) return;
        scoped += n;
        if (rbFolded) { rbKeys.forEach(function (k) { picks.set(k, true); }); return; }

        per.forEach(function (rec) {
          var pgKey = keyOf(rec.e);
          var pgKeys = rec.hits.map(function (h) { return pgKey + "|" + h.line; });
          var pgFolded = !!st.frFolds[pgKey];
          var ph = BV.el("div", { class: "fp-pg" });
          ph.appendChild(picks.group(BV.el("input", { type: "checkbox" }),
            function () { return pgKeys; }, "pg:" + pgKey));
          var pc2 = BV.el("span", { class: "caret" }, pgFolded ? "▸" : "▾");
          pc2.addEventListener("click", function (ev) {
            ev.stopPropagation();
            st.frFolds[pgKey] = !pgFolded;
            paint();
          });
          ph.appendChild(pc2);
          ph.appendChild(BV.el("span", null, BV.esc(rec.e.name)));
          ph.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem;margin-left:auto" },
            String(rec.hits.length)));
          results.appendChild(ph);
          if (pgFolded) { pgKeys.forEach(function (k) { picks.set(k, true); }); return; }
          rec.hits.forEach(function (h) {
            var k = pgKey + "|" + h.line;
            picks.set(k, true);
            var row = BV.el("div", { class: "fp-ln" });
            var cb = picks.bind(BV.el("input", { type: "checkbox" }), k);
            cb.addEventListener("click", function (ev) { ev.stopPropagation(); });
            row.appendChild(cb);
            row.appendChild(BV.el("span", { class: "n" }, String(h.line + 1)));
            var snip = BV.el("span", { class: "snip" });
            snip.innerHTML = snipHtml(h.text, h.spans);
            row.appendChild(snip);
            row.addEventListener("click", function () { revealHit(rec.e, h.line); });
            results.appendChild(row);
          });
        });
      });
      picks.sync();
      if (!total) {
        results.innerHTML = '<div class="dim" style="padding:1rem;font-size:.78rem">no matches</div>';
      }
      lastTotals = { total: total, scoped: scoped };
      updateFoot();
    }

    function updateFoot() {
      foot.innerHTML = "";
      var sel = picks.size();
      var info = BV.el("span", { class: "dim", style: "font-size:.72rem;flex:1" });
      info.textContent = sel + " selected" +
        (lastTotals.total !== undefined ? " / " + lastTotals.total + " found" : "");
      foot.appendChild(info);
      var b = BV.el("button", { class: "btn primary",
        style: "padding:.15rem .5rem;font-size:.75rem" }, "replace " + sel);
      b.disabled = !sel;
      b.addEventListener("click", doReplace);
      foot.appendChild(b);
    }

    function doReplace() {
      var chosen = {};
      picks.selected().forEach(function (k) { chosen[k] = true; });
      var changed = 0, touchedOpen = false;
      BV.workspace.byRobot().forEach(function (g) {
        if (!fr.scope[g.root]) return;
        g.programs.forEach(function (e) {
          var b = BV.workspace.peek(e.root, e.file);
          if (!b || b.base === null) return;
          var lines = b.text.split("\n"), touched = false;
          findHits(b.text, fr.find).forEach(function (h) {
            if (!chosen[keyOf(e) + "|" + h.line]) return;
            lines[h.line] = applyHit(h.text, h.spans, fr.repl);
            touched = true; changed++;
          });
          if (touched) {
            BV.workspace.setBody(e.root, e.file, lines.join("\n"));
            touchedOpen = touchedOpen || st.panes.some(function (pane) {
              return pane.tabs.some(function (t) { return t.root === e.root && t.file === e.file; });
            });
          }
        });
      });
      BV.toast("replaced " + changed + " occurrence" + (changed === 1 ? "" : "s"));
      updateCounts();
      if (touchedOpen) renderPanes();   /* open editors must show the new text */
      paint();
    }

    fIn.addEventListener("input", paint);
    rIn.addEventListener("input", paint);
    ensureBuffers().then(function () {
      if (!document.body.contains(panel)) return;
      paint();
      try { fIn.focus(); fIn.select(); } catch (e) {}
    });
  }

  /* open a program in a pane and flash the line a find result points at */
  function revealHit(entry, lineIdx) {
    var paneIdx = st.activePane;
    st.panes.forEach(function (pane, i) {
      if (pane.tabs.some(function (t) { return t.root === entry.root && t.file === entry.file; })) {
        paneIdx = i;
      }
    });
    openTab(entry, paneIdx);
    setTimeout(function () {
      var ed = st.editors[paneIdx];
      if (!ed) return;
      ed.focusLine(lineIdx + 1);
      var scroller = ed.el.querySelector(".lsed-scroll");
      if (!scroller) return;
      var cs = getComputedStyle(ed.code);
      var lh = parseFloat(cs.lineHeight) || 16;
      var bar = BV.el("div", { class: "flashbar" });
      bar.style.top = (parseFloat(cs.paddingTop) + lineIdx * lh) + "px";
      bar.style.height = lh + "px";
      scroller.appendChild(bar);
      setTimeout(function () { if (bar.parentNode) bar.parentNode.removeChild(bar); }, 1600);
    }, 40);
  }

  /* ------------------------------------------------------------------ *
   * export
   * ------------------------------------------------------------------ */
  function showExport() {
    var edits = BV.workspace.edits();
    if (!edits.length) { BV.toast("no edits to export"); return; }
    var body = BV.el("div");
    var byRoot = {};
    edits.forEach(function (e) { (byRoot[e.root] = byRoot[e.root] || []).push(e); });
    var html = '<div class="dim" style="font-size:.8rem;margin-bottom:.4rem">' +
      "one folder per robot — program names repeat across robots</div>";
    Object.keys(byRoot).forEach(function (root) {
      html += '<div style="margin:.5rem 0 .2rem;font-family:var(--font-mono);color:var(--accent)">' +
        BV.esc(labelFor(root)) + "/</div>";
      byRoot[root].forEach(function (e) {
        html += '<div style="padding-left:1.2rem;font-family:var(--font-mono);font-size:.82rem">' +
          BV.esc(e.file.split("/").pop()) + "</div>";
      });
    });
    body.innerHTML = html;
    var acts = BV.el("div", { style: "display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var go = BV.el("button", { class: "btn primary" }, "choose folder…");
    acts.appendChild(cancel);
    acts.appendChild(go);
    body.appendChild(acts);
    var m = BV.modal("export edited programs", body);
    cancel.addEventListener("click", function () { m.close(true); });
    go.addEventListener("click", function () {
      m.close(true);
      BV.api.call("pick_export_folder").then(function (dest) {
        if (!dest) return;
        return BV.api.call("ws_export", edits, dest).then(function (res) {
          BV.workspace.markSaved();
          afterChange();
          showExported(res);
        });
      }).catch(function (e) { BV.toast("export failed: " + (e.message || e), 4500); });
    });
  }

  function showExported(res) {
    var body = BV.el("div");
    body.innerHTML = "<p>exported <b>" + res.count + "</b> file" +
      (res.count === 1 ? "" : "s") + " to</p>" +
      '<p style="font-family:var(--font-mono);font-size:.8rem;word-break:break-all">' +
      BV.esc(res.dest) + "</p>" +
      '<ul class="dim" style="margin:.3rem 0 0 1.1rem;font-size:.82rem">' +
      (res.files || []).map(function (f) { return "<li>" + BV.esc(f) + "</li>"; }).join("") +
      "</ul>";
    var acts = BV.el("div", { style: "display:flex;gap:.6rem;justify-content:flex-end;margin-top:1rem" });
    var reveal = BV.el("button", { class: "btn" }, "reveal in explorer");
    var done = BV.el("button", { class: "btn primary" }, "done");
    acts.appendChild(reveal);
    acts.appendChild(done);
    body.appendChild(acts);
    var m = BV.modal("exported", body);
    reveal.addEventListener("click", function () {
      BV.api.call("reveal_export_folder", res.dest).catch(function () {});
    });
    done.addEventListener("click", function () { m.close(true); });
  }

  /* ------------------------------------------------------------------ *
   * keys (workspace-local; the global map lives in keys.js)
   * ------------------------------------------------------------------ */
  document.addEventListener("keydown", function (e) {
    if (location.hash.split("/")[0] !== "#edit") return;
    if (BV.modalOpen()) return;
    if (e.ctrlKey && e.key === "Tab") {
      /* inside the workspace ctrl+tab means "next program tab"; stop it here so
         keys.js's app-wide backup cycling does not also fire */
      e.preventDefault();
      e.stopPropagation();
      cycleTab(e.shiftKey ? -1 : 1);
      return;
    }
    if (e.ctrlKey && (e.key === "f" || e.key === "F")) {
      e.preventDefault();
      var sel = "";
      try { sel = String(window.getSelection()).trim(); } catch (x) {}
      st.railTab = "find";
      renderRail(sel || undefined);
      return;
    }
    if (e.key === "Escape" && st.railTab === "find") {
      st.railTab = "set";
      renderRail();
    }
  }, true);

  /* the rail's dirty markers must follow edits made anywhere */
  BV.state.on("workspace", function () { if (el.counts) updateCounts(); });

  BV.openWorkspace = function () {
    if (location.hash === "#edit") BV.route();
    else location.hash = "#edit";
  };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "edit", label: "edit", render: render,
                 hidden: true, always: true, shell: true });
})();
