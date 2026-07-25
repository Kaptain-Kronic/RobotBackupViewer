/* tabs/edit.js - the multi-robot EDIT WORKSPACE (#edit).

   A shell screen (like home): it owns the whole window and renders with zero
   backups open, because a working set spans robots while the tabbar and
   BV.tabState are per-backup. It replaces the old in-place edit toggle that
   lived in the programs tab - one editing surface, no drift.

   Layout: [rail] | panes | [navigator], both side panels collapsible, every
   divider draggable. The second pane is OPT-IN (a split toggle). Programs are
   read and exported through the path-addressed ws_* endpoints (BV.workspace),
   so the workspace never opens a session.

   Editor instances are CACHED per program and their DOM re-attached on every
   re-render, so undo history, caret and scroll survive closing a tab, switching
   panes and a whole route away. They are dropped only when the program leaves
   the workspace.

   All state here is MODULE-LEVEL - route() rebuilds the DOM on every hash
   change, so nothing may live in the DOM alone. */
(function () {
  "use strict";

  var st = {
    panes: [{ tabs: [], active: 0 }, { tabs: [], active: 0 }],
    split: false,            /* the second pane is optional */
    activePane: 0,
    railOpen: true,
    navOpen: true,
    railTab: "set",          /* set | find */
    setFolds: {},            /* working-set robot folds */
    frFolds: {},             /* find-result folds */
    details: [false, false], /* per-pane attrs/positions panel */
    navSeg: "calls",         /* calls | labels */
    selRow: null,
  };
  var fr = {
    find: "", repl: "",
    matchCase: false, wholeWord: false,
    ignoreComment: true,     /* R[21] also matches R[21:ANY] and R[21:OFF:ANY] */
    includeRemarks: false,   /* skip ! lines unless asked */
  };

  var editors = {};          /* key -> the cached BV.lsEditor instance */
  var namesByRoot = {};      /* root -> {STEM: true} for honest CALL resolution */
  var el = {};
  var drag = null;

  function keyOf(t) { return t.root + BV.KEYSEP + t.file; }
  function paneOf(idx) { return st.panes[idx] || st.panes[0]; }
  function visiblePanes() { return st.split ? 2 : 1; }

  /* ------------------------------------------------------------------ *
   * render
   * ------------------------------------------------------------------ */
  var prefsRead = false;
  function readPrefs() {
    if (prefsRead) return;
    prefsRead = true;
    var s = BV.state.settings || {};
    if (s.ws_panels) {
      st.railOpen = s.ws_panels.rail !== false;
      st.navOpen = s.ws_panels.nav !== false;
    }
    if (typeof s.ws_split === "boolean") st.split = s.ws_split;
  }

  function render(view, toolbar, params) {
    BV.workspace.load();
    readPrefs();
    view.classList.add("no-pad");
    el = {};

    var bar = BV.el("div", { style: "display:flex;gap:.6rem;align-items:center;flex-wrap:wrap" });
    el.counts = BV.el("span", { class: "dim", style: "font-size:.78rem" });
    bar.appendChild(el.counts);
    bar.appendChild(BV.el("span", { style: "flex:1" }));
    var splitBtn = BV.el("button", { class: "btn" + (st.split ? " primary" : ""),
      title: "show a second program window side by side" }, "split");
    splitBtn.addEventListener("click", function () {
      st.split = !st.split;
      if (!st.split) {
        /* folding the split away must not hide open work - move it left */
        st.panes[1].tabs.forEach(function (t) {
          if (!st.panes[0].tabs.some(function (x) { return keyOf(x) === keyOf(t); })) {
            st.panes[0].tabs.push(t);
          }
        });
        st.panes[1].tabs = [];
        st.panes[1].active = 0;
        st.activePane = 0;
      }
      BV.api.call("set_setting", "ws_split", st.split).catch(function () {});
      splitBtn.classList.toggle("primary", st.split);
      renderPanes();
    });
    bar.appendChild(splitBtn);
    el.exportBtn = BV.el("button", { class: "btn primary" }, "export…");
    el.exportBtn.addEventListener("click", showExport);
    bar.appendChild(el.exportBtn);
    toolbar.appendChild(bar);

    var shell = BV.el("div", { class: "ws" });
    view.appendChild(shell);
    el.shell = shell;
    renderShell();
    updateCounts();
    return true;
  }

  /* the three columns, honouring the two collapse toggles */
  function renderShell() {
    var shell = el.shell;
    shell.innerHTML = "";

    if (st.railOpen) {
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
      }, function () { return rail.getBoundingClientRect().width; }));
      renderRail();
    } else {
      shell.appendChild(stub("rail", "working set / find", function () {
        st.railOpen = true; persistPanels(); renderShell();
      }));
    }

    el.work = BV.el("div", { class: "ws-work" });
    shell.appendChild(el.work);

    if (st.navOpen) {
      shell.appendChild(makeResizer(function (dx, startW) {
        var w = Math.max(160, Math.min(640, startW - dx));
        el.nav.style.width = w + "px";
        return w;
      }, function (w) {
        BV.api.call("set_setting", "ws_nav_w", w).catch(function () {});
        if (BV.state.settings) BV.state.settings.ws_nav_w = w;
      }, function () { return el.nav.getBoundingClientRect().width; }));
      el.nav = BV.el("div", { class: "ws-nav" });
      var navW = BV.state.settings && BV.state.settings.ws_nav_w;
      if (navW) el.nav.style.width = navW + "px";
      shell.appendChild(el.nav);
    } else {
      shell.appendChild(stub("nav", "calls / labels", function () {
        st.navOpen = true; persistPanels(); renderShell();
      }));
    }

    renderPanes();
  }

  function stub(side, title, onOpen) {
    var s = BV.el("div", { class: "ws-stub", title: "show " + title },
      side === "rail" ? "»" : "«");
    s.addEventListener("click", onOpen);
    return s;
  }
  function persistPanels() {
    BV.api.call("set_setting", "ws_panels",
      { rail: st.railOpen, nav: st.navOpen }).catch(function () {});
  }

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
     it, or clicking a result would destroy the list being worked through */
  function afterChange() {
    updateCounts();
    if (st.railTab === "set" && st.railOpen) renderWorkingSet();
  }
  /* dirty dots without touching the rest of the DOM */
  function refreshDirtyMarks() {
    (el.paneEls || []).forEach(function (pEl, i) {
      if (!pEl) return;
      var tabs = pEl.querySelectorAll(".ws-tab");
      paneOf(i).tabs.forEach(function (t, ti) {
        var tab = tabs[ti];
        if (!tab) return;
        var dirty = BV.workspace.dirty(t.root, t.file);
        var dot = tab.querySelector(".dot");
        if (dirty && !dot) tab.insertBefore(BV.el("span", { class: "dot" }), tab.querySelector(".x"));
        else if (!dirty && dot) dot.parentNode.removeChild(dot);
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * rail: working set
   * ------------------------------------------------------------------ */
  function renderRail(prefill) {
    if (!st.railOpen) return;
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
    var hide = BV.el("div", { class: "ws-railtab ws-hidetab", title: "hide this panel" }, "«");
    hide.addEventListener("click", function () {
      st.railOpen = false; persistPanels(); renderShell();
    });
    el.railTabs.appendChild(hide);
    if (st.railTab === "find") renderFind(prefill);
    else renderWorkingSet();
  }

  function renderWorkingSet() {
    el.railHead.innerHTML = "";
    el.railHead.appendChild(BV.el("span", null, BV.workspace.count() + " programs"));
    if (BV.workspace.count()) {
      var clr = BV.el("button", { class: "btn",
        style: "margin-left:auto;padding:.1rem .4rem;font-size:.7rem",
        title: "remove everything from the workspace" }, "clear");
      clr.addEventListener("click", function () {
        if (BV.workspace.anyDirty() &&
            !window.confirm("There are unsaved edits. Discard them?")) return;
        BV.workspace.entries().forEach(function (e) { dropEditor(e); });
        BV.workspace.clear();
        st.panes.forEach(function (p) { p.tabs = []; p.active = 0; });
        renderPanes();
        afterChange();
      });
      el.railHead.appendChild(clr);
    }

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
      h.appendChild(BV.el("span", { class: "caret" }, folded ? "▸" : "▾"));
      h.appendChild(BV.el("span", { class: "nm" }, BV.esc(g.label)));
      var right = BV.el("span",
        { style: "margin-left:auto;display:flex;gap:.3rem;align-items:center" });
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
          if (dirty && !window.confirm("Discard unsaved edits to " + e.name + "?")) return;
          dropEditor(e);
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

  /* ------------------------------------------------------------------ *
   * panes + tabs
   * ------------------------------------------------------------------ */

  /* a program lives in exactly ONE pane: asking for it anywhere else focuses
     the pane that already has it instead of opening a second copy */
  function paneHolding(entry) {
    for (var i = 0; i < visiblePanes(); i++) {
      if (paneOf(i).tabs.some(function (t) { return keyOf(t) === keyOf(entry); })) return i;
    }
    return -1;
  }

  function openTab(entry, paneIdx) {
    if (paneIdx >= visiblePanes()) paneIdx = 0;
    var held = paneHolding(entry);
    if (held >= 0) paneIdx = held;          /* never duplicate across panes */
    var pane = paneOf(paneIdx);
    var at = -1;
    pane.tabs.forEach(function (t, i) { if (keyOf(t) === keyOf(entry)) at = i; });
    if (at < 0) {
      pane.tabs.push({ root: entry.root, file: entry.file, name: entry.name });
      at = pane.tabs.length - 1;
    }
    pane.active = at;
    st.activePane = paneIdx;
    renderPanes();
    afterChange();
  }
  /* closing a tab keeps the cached editor, so its undo history is still there
     when the program is reopened */
  function closeTab(paneIdx, idx) {
    var pane = paneOf(paneIdx);
    pane.tabs.splice(idx, 1);
    if (pane.active >= pane.tabs.length) pane.active = pane.tabs.length - 1;
    renderPanes();
  }
  function closeEverywhere(entry) {
    st.panes.forEach(function (pane) {
      pane.tabs = pane.tabs.filter(function (t) { return keyOf(t) !== keyOf(entry); });
      if (pane.active >= pane.tabs.length) pane.active = pane.tabs.length - 1;
    });
  }
  /* only leaving the workspace discards the editor (and its undo) */
  function dropEditor(entry) {
    var k = keyOf(entry);
    var ed = editors[k];
    if (ed && ed.el && ed.el.parentNode) ed.el.parentNode.removeChild(ed.el);
    delete editors[k];
  }
  function cycleTab(dir) {
    var pane = paneOf(st.activePane);
    if (!pane || pane.tabs.length < 2) return;
    pane.active = (pane.active + dir + pane.tabs.length) % pane.tabs.length;
    renderPanes();
  }
  function hideDrop() { if (el.dropzone) el.dropzone.classList.remove("show"); }

  function activeTab() {
    var pane = paneOf(st.activePane);
    return pane.tabs[pane.active] || null;
  }

  function renderPanes() {
    if (!el.work) return;
    /* detach cached editors before wiping, so their DOM (and undo) survives */
    Object.keys(editors).forEach(function (k) {
      var ed = editors[k];
      if (ed && ed.el && ed.el.parentNode) ed.el.parentNode.removeChild(ed.el);
    });
    el.work.innerHTML = "";
    el.paneEls = [];
    var wrap = BV.el("div", { class: "ws-panes" });
    el.work.appendChild(wrap);
    el.dropzone = BV.el("div", { class: "dropzone" });
    wrap.appendChild(el.dropzone);

    wrap.addEventListener("dragover", function (e) {
      if (!drag) return;
      e.preventDefault();
      if (!st.split) { el.dropzone.style.left = "0"; el.dropzone.style.width = "100%"; }
      else {
        el.dropzone.style.width = "50%";
        var r = wrap.getBoundingClientRect();
        el.dropzone.style.left = ((e.clientX - r.left) > r.width / 2) ? "50%" : "0";
      }
      el.dropzone.classList.add("show");
    });
    wrap.addEventListener("dragleave", function (e) { if (e.target === wrap) hideDrop(); });
    wrap.addEventListener("drop", function (e) {
      if (!drag) return;
      e.preventDefault();
      var target = 0;
      if (st.split) {
        var r = wrap.getBoundingClientRect();
        target = (e.clientX - r.left) > r.width / 2 ? 1 : 0;
      }
      var d = drag; drag = null; hideDrop();
      if (d.kind === "tab") {
        if (d.fromPane === target) return;
        /* a MOVE between panes - the dedupe in openTab would otherwise just
           bounce it back to the pane it came from */
        var from = paneOf(d.fromPane);
        from.tabs.splice(d.idx, 1);
        if (from.active >= from.tabs.length) from.active = from.tabs.length - 1;
      }
      openTab(d.entry, target);
    });

    for (var i = 0; i < visiblePanes(); i++) {
      if (i === 1) {
        wrap.appendChild(makeResizer(function (dx, startPx) {
          var total = wrap.getBoundingClientRect().width;
          var px = Math.max(160, Math.min(total - 160, startPx + dx));
          el.paneEls[0].style.flex = "0 0 " + px + "px";
          el.paneEls[1].style.flex = "1 1 auto";
          return px;
        }, null, function () { return el.paneEls[0].getBoundingClientRect().width; }));
      }
      buildPane(wrap, i);
    }
    renderNav();
  }

  function buildPane(wrap, i) {
    var pane = paneOf(i);
    var p = BV.el("div", { class: "ws-pane" + (st.activePane === i && st.split ? " active" : "") });
    el.paneEls[i] = p;
    p.addEventListener("mousedown", function () {
      if (st.activePane !== i) {
        st.activePane = i;
        (el.paneEls || []).forEach(function (pe, idx) {
          if (pe) pe.classList.toggle("active", idx === i && st.split);
        });
        renderNav();
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
        "<span>" + BV.esc(t.name) + "</span>" + (dirty ? '<span class="dot"></span>' : "");
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
        title: "attributes + point data (rarely edited)" },
        st.details[i] ? "hide details" : "details");
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
        '<div class="hint">' + (BV.workspace.count()
          ? "double-click a program in the working set"
          : "add programs to the workspace first") + "</div></div>";
      return;
    }
    mountEditor(t, host, p, i);
  }

  /* cached instance re-attached; only a first open builds one */
  function mountEditor(t, host, paneEl, paneIdx) {
    var k = keyOf(t);
    var cached = editors[k];
    if (cached) {
      host.appendChild(cached.el);
      if (st.details[paneIdx]) {
        var buf = BV.workspace.peek(t.root, t.file);
        if (buf) paneEl.appendChild(detailsPanel(t, buf, paneIdx));
      }
      renderNav();
      return;
    }
    host.innerHTML = '<div class="dim" style="padding:1rem">loading…</div>';
    BV.workspace.buffer(t.root, t.file).then(function (buf) {
      if (!document.body.contains(host)) return;      /* routed away mid-load */
      host.innerHTML = "";
      editors[k] = BV.lsEditor(host, {
        text: buf.text,
        onChange: function (txt) {
          BV.workspace.setBody(t.root, t.file, txt);
          updateCounts();
          refreshDirtyMarks();
          if (st.railTab === "set" && st.railOpen) renderWorkingSet();
          if (activeTab() && keyOf(activeTab()) === k) renderNav();
        },
      });
      if (st.details[paneIdx]) paneEl.appendChild(detailsPanel(t, buf, paneIdx));
      loadNames(t.root);
      renderNav();
    }).catch(function (e) {
      host.innerHTML = '<div class="empty-state"><div class="hint">' +
        BV.esc(e.message || e) + "</div></div>";
    });
  }

  function labelFor(root) {
    var g = BV.workspace.byRobot().filter(function (x) { return x.root === root; })[0];
    return g ? g.label : root.split(/[\\/]/).pop();
  }

  /* ------------------------------------------------------------------ *
   * navigator: LBL/JMP + CALL for the ACTIVE program
   *
   * Computed client-side from the buffer text, not from get_program: the
   * workspace is path-addressed and its backups may have no session at all.
   * The upside is that it stays live while you type. CALL targets resolve
   * against that backup's own program list (cached per root) so "not in this
   * backup" is an honest statement rather than a guess.
   * ------------------------------------------------------------------ */
  var LBL_DEF = /^LBL\[\s*(\d+)\s*(?::([^\]]*))?\]$/;
  var JMP_REF = /\bJMP\s+LBL\[\s*(\d+)/g;
  var CALL_REF = /\b(CALL|RUN)\s+([A-Z][A-Z0-9_]*)/g;

  function tpNav(text, known) {
    var lines = text.split("\n");
    var defs = [], byId = {}, broken = {}, calls = {};
    lines.forEach(function (raw, i) {
      var m = LBL_DEF.exec(raw.trim());
      if (m) {
        var e = { id: +m[1], name: (m[2] || "").trim(), line: i + 1, jumps: [] };
        defs.push(e);
        if (byId[e.id] === undefined) byId[e.id] = e;
      }
    });
    lines.forEach(function (raw, i) {
      if (raw.trim().charAt(0) === "!") return;   /* remarked lines jump nowhere */
      var mm;
      JMP_REF.lastIndex = 0;
      while ((mm = JMP_REF.exec(raw)) !== null) {
        var id = +mm[1];
        var e = byId[id];
        if (!e) e = broken[id] = broken[id] || { id: id, name: "", line: null, jumps: [] };
        e.jumps.push(i + 1);
      }
      CALL_REF.lastIndex = 0;
      while ((mm = CALL_REF.exec(raw)) !== null) {
        var nm = mm[2];
        var c = calls[nm] || (calls[nm] = { name: nm, kind: mm[1].toLowerCase(),
                                            lines: [], exists: null });
        c.lines.push(i + 1);
        if (known) c.exists = !!known[nm.toUpperCase()];
      }
    });
    Object.keys(broken).forEach(function (k) { defs.push(broken[k]); });
    return {
      labels: defs,
      calls: Object.keys(calls).sort().map(function (k) { return calls[k]; }),
    };
  }

  function loadNames(root) {
    if (namesByRoot[root]) return Promise.resolve(namesByRoot[root]);
    namesByRoot[root] = {};      /* claim it so we ask once */
    return BV.api.call("ws_list_programs", root).then(function (list) {
      var set = {};
      (list || []).forEach(function (p) {
        set[String(p.name).replace(/\.[Ll][Ss]$/, "").toUpperCase()] = true;
      });
      namesByRoot[root] = set;
      renderNav();
      return set;
    }).catch(function () { return {}; });
  }

  function renderNav() {
    if (!st.navOpen || !el.nav) return;
    el.nav.innerHTML = "";
    var head = BV.el("div", { class: "ws-navhead" });
    var t = activeTab();
    var seg = BV.segmented([{ id: "calls", label: "calls" }, { id: "labels", label: "labels" }],
      { value: st.navSeg, onChange: function (id) { st.navSeg = id; renderNav(); } });
    head.appendChild(seg.el);
    var hide = BV.el("span", { class: "ws-navhide", title: "hide this panel" }, "»");
    hide.addEventListener("click", function () {
      st.navOpen = false; persistPanels(); renderShell();
    });
    head.appendChild(hide);
    el.nav.appendChild(head);

    var body = BV.el("div", { class: "ws-navbody" });
    el.nav.appendChild(body);
    if (!t) {
      body.innerHTML = '<div class="dim" style="padding:.7rem;font-size:.78rem">' +
        "no program open</div>";
      return;
    }
    var buf = BV.workspace.peek(t.root, t.file);
    if (!buf) {
      body.innerHTML = '<div class="dim" style="padding:.7rem;font-size:.78rem">loading…</div>';
      return;
    }
    var nav = tpNav(buf.text, namesByRoot[t.root]);
    var known = namesByRoot[t.root];

    if (st.navSeg === "calls") {
      if (!nav.calls.length) {
        body.innerHTML = '<div class="dim" style="padding:.7rem;font-size:.78rem">' +
          "calls nothing</div>";
        return;
      }
      nav.calls.forEach(function (c) {
        var row = BV.el("div", { class: "ws-navrow", title: "line " + c.lines.join(", ") });
        var miss = known && c.exists === false;
        row.innerHTML = '<span class="pill ghost">' + BV.esc(c.kind) + "</span>" +
          '<span class="nm' + (miss ? " miss" : "") + '">' + BV.esc(c.name) + "</span>" +
          (c.lines.length > 1 ? '<span class="dim">×' + c.lines.length + "</span>" : "") +
          '<span class="dim ln">' + c.lines[0] + "</span>";
        if (miss) row.title = c.name + " is not a program in this backup";
        row.addEventListener("click", function () { revealLine(t, c.lines[0] - 1); });
        body.appendChild(row);
      });
      return;
    }

    if (!nav.labels.length) {
      body.innerHTML = '<div class="dim" style="padding:.7rem;font-size:.78rem">' +
        "no labels</div>";
      return;
    }
    nav.labels.forEach(function (L) {
      var brokenLbl = L.line === null;
      var row = BV.el("div", { class: "ws-navrow" + (brokenLbl ? " miss" : "") });
      row.innerHTML = '<span class="nm">LBL[' + L.id + "]</span>" +
        (L.name ? '<span class="dim">' + BV.esc(L.name) + "</span>" : "") +
        (brokenLbl ? '<span class="dim">never defined</span>'
                   : '<span class="dim ln">' + L.line + "</span>");
      row.title = brokenLbl
        ? "jumped to from line " + L.jumps.join(", ") + " but never defined"
        : L.jumps.length ? "jumped to from line " + L.jumps.join(", ") : "no jumps to this label";
      if (!brokenLbl) row.addEventListener("click", function () { revealLine(t, L.line - 1); });
      else if (L.jumps.length) {
        row.addEventListener("click", function () { revealLine(t, L.jumps[0] - 1); });
      }
      body.appendChild(row);
    });
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
        refreshDirtyMarks();
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
      prow.appendChild(BV.segmented(
        [{ id: "READ_WRITE", label: "read_write" }, { id: "READ", label: "read" }],
        { value: buf.attrs.protect, onChange: function (id) {
            BV.workspace.setAttr(t.root, t.file, "protect", id);
            updateCounts();
            refreshDirtyMarks();
          } }).el);
    } else {
      /* an unknown value: show it honestly rather than offer switches we
         cannot prove the controller accepts */
      prow.appendChild(BV.el("span", { class: "dim" }, BV.esc(prot || "—")));
    }
    ac.appendChild(prow);
    box.appendChild(ac);

    var positions = buf.positions || [];
    if (positions.length) box.appendChild(positionsCard(t, positions));
    return box;
  }

  function positionsCard(t, positions) {
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
        refreshDirtyMarks();
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
          pc.appendChild(BV.el("div", { class: "dim",
            style: "margin-left:.4rem;font-size:.72rem" }, "gp" + g.gp));
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
    return pc;
  }

  /* ------------------------------------------------------------------ *
   * find / replace
   * ------------------------------------------------------------------ */
  var REF = /^([A-Z]+)\[(\d+)\]$/i;

  function buildRx(needle) {
    var m = REF.exec((needle || "").trim());
    var src;
    if (m && fr.ignoreComment) {
      /* an IO/register reference matches with or WITHOUT its comment - and the
         pendant's IO-status view adds a THIRD field (DO[495:OFF:Name]), so the
         optional tail is everything up to the closing bracket */
      src = "\\b" + m[1] + "\\[" + m[2] + "(?::[^\\]]*)?\\]";
    } else {
      src = (needle || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      if (fr.wholeWord) src = "\\b" + src + "\\b";
    }
    return new RegExp(src, fr.matchCase ? "g" : "gi");
  }

  function findHits(text, needle) {
    var hits = [];
    if (!needle) return hits;
    var rx = buildRx(needle);
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
  function nameMatches(name, needle) {
    if (!needle) return false;
    var rx = buildRx(needle);
    rx.lastIndex = 0;
    return rx.test(name);
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

    if (!BV.workspace.count()) {
      el.railBody.appendChild(BV.el("div", { class: "ws-empty" },
        '<div class="hint">add programs first — find/replace works over the working set</div>'));
      return;
    }

    var panel = BV.el("div", { class: "fp" });
    el.railBody.appendChild(panel);

    var inputs = BV.el("div", { class: "fp-inputs" });
    var fIn = BV.el("input", { type: "text", placeholder: "find" });
    var rIn = BV.el("input", { type: "text", placeholder: "replace with" });
    if (prefill !== undefined) fr.find = prefill;
    fIn.value = fr.find;
    rIn.value = fr.repl;
    inputs.appendChild(fIn);
    inputs.appendChild(rIn);

    var opts = BV.el("div", { class: "fp-opts" });
    function opt(label, flag, title) {
      var l = BV.el("label", { class: "fp-opt", title: title || "" });
      var cb = BV.el("input", { type: "checkbox" });
      cb.checked = fr[flag];
      cb.addEventListener("change", function () { fr[flag] = cb.checked; paint(true); });
      l.appendChild(cb);
      l.appendChild(BV.el("span", null, label));
      opts.appendChild(l);
      return l;
    }
    opt("match case", "matchCase");
    opt("whole word", "wholeWord");
    opt("include ! remarks", "includeRemarks", "remarked-out lines are skipped by default");
    var refOpt = opt("with or without comment", "ignoreComment",
      "R[21] also matches R[21:SERVO GUN WORK] and the pendant's status form");
    inputs.appendChild(opts);
    panel.appendChild(inputs);

    var results = BV.el("div", { class: "fp-results" });
    panel.appendChild(results);
    var foot = BV.el("div", { class: "fp-foot" });
    panel.appendChild(foot);

    var picks = BV.checklist({ onChange: function () { updateFoot(); } });
    var lastSig = null, lastTotal = 0;

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

    /* reset===true re-seeds "everything selected"; a plain repaint (folding a
       group) must PRESERVE the selection - BV.checklist keeps its state across
       re-binds, so we simply do not clear it */
    function paint(force) {
      fr.find = fIn.value;
      fr.repl = rIn.value;
      var sig = JSON.stringify([fr.find, fr.matchCase, fr.wholeWord,
                                fr.ignoreComment, fr.includeRemarks]);
      var reset = !!force || sig !== lastSig;
      lastSig = sig;
      if (reset) picks.clear();

      refOpt.style.display = REF.test((fr.find || "").trim()) ? "" : "none";
      results.innerHTML = "";
      var fresh = [], total = 0;

      BV.workspace.byRobot().forEach(function (g) {
        var per = [];
        g.programs.forEach(function (e) {
          var b = BV.workspace.peek(e.root, e.file);
          if (!b || b.base === null) return;
          var hits = findHits(b.text, fr.find);
          /* a program NAME match is navigational, never replaceable - it gets
             no checkbox and does not count toward the replace set */
          var named = nameMatches(e.name, fr.find);
          if (hits.length || named) per.push({ e: e, hits: hits, named: named });
        });
        var n = per.reduce(function (a, x) { return a + x.hits.length; }, 0);
        var names = per.filter(function (x) { return x.named; }).length;
        if (!n && !names) return;
        total += n;

        var rbKeys = [];
        per.forEach(function (rec) {
          rec.hits.forEach(function (h) { rbKeys.push(keyOf(rec.e) + "|" + h.line); });
        });
        rbKeys.forEach(function (k) { fresh.push(k); });

        var folded = !!st.frFolds[g.root];
        var rh = BV.el("div", { class: "fp-rb" });
        /* ONE box per level, and only where it does something: a group with a
           single hit would otherwise stack three checkboxes for one result */
        if (rbKeys.length > 1) {
          rh.appendChild(picks.group(BV.el("input", { type: "checkbox" }),
            function () { return rbKeys; }, "rb:" + g.root));
        }
        var caret = BV.el("span", { class: "caret" }, folded ? "▸" : "▾");
        rh.appendChild(caret);
        rh.appendChild(BV.el("span", { class: "nm" }, BV.esc(g.label)));
        rh.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem;margin-left:auto" },
          n + (names ? " +" + names + " name" : "")));
        /* the caret must fold WITHOUT touching the selection */
        rh.addEventListener("click", function (ev) {
          if (ev.target.tagName === "INPUT") return;
          st.frFolds[g.root] = !folded;
          paint(false);
        });
        results.appendChild(rh);
        if (folded) return;

        per.forEach(function (rec) {
          var pgKey = keyOf(rec.e);
          var pgKeys = rec.hits.map(function (h) { return pgKey + "|" + h.line; });
          var pgFolded = !!st.frFolds[pgKey];
          var ph = BV.el("div", { class: "fp-pg" });
          if (pgKeys.length > 1) {
            ph.appendChild(picks.group(BV.el("input", { type: "checkbox" }),
              function () { return pgKeys; }, "pg:" + pgKey));
          }
          ph.appendChild(BV.el("span", { class: "caret" }, pgFolded ? "▸" : "▾"));
          ph.appendChild(BV.el("span", { class: "nm" }, BV.esc(rec.e.name)));
          if (rec.named) {
            ph.appendChild(BV.el("span", { class: "pill acc", title: "the program NAME matches" },
              "name"));
          }
          ph.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem;margin-left:auto" },
            String(rec.hits.length)));
          ph.addEventListener("click", function (ev) {
            if (ev.target.tagName === "INPUT") return;
            if (!rec.hits.length) { openTab(rec.e, st.activePane); return; }
            st.frFolds[pgKey] = !pgFolded;
            paint(false);
          });
          results.appendChild(ph);
          if (pgFolded) return;

          rec.hits.forEach(function (h) {
            var k = pgKey + "|" + h.line;
            var row = BV.el("div", { class: "fp-ln" });
            var cb = picks.bind(BV.el("input", { type: "checkbox" }), k);
            cb.addEventListener("click", function (ev) { ev.stopPropagation(); });
            row.appendChild(cb);
            row.appendChild(BV.el("span", { class: "n" }, String(h.line + 1)));
            var snip = BV.el("span", { class: "snip" });
            snip.innerHTML = snipHtml(h.text, h.spans);
            row.appendChild(snip);
            row.addEventListener("click", function () { revealLine(rec.e, h.line); });
            results.appendChild(row);
          });
        });
      });

      if (reset) fresh.forEach(function (k) { picks.set(k, true); });
      picks.sync();
      if (!results.childNodes.length) {
        results.innerHTML = '<div class="dim" style="padding:1rem;font-size:.78rem">no matches</div>';
      }
      lastTotal = total;
      updateFoot();
    }

    function updateFoot() {
      foot.innerHTML = "";
      var sel = picks.size();
      var info = BV.el("span", { class: "dim", style: "font-size:.72rem;flex:1" });
      info.textContent = sel + " selected / " + lastTotal + " found";
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
      var changed = 0, touched = [];
      BV.workspace.entries().forEach(function (e) {
        var b = BV.workspace.peek(e.root, e.file);
        if (!b || b.base === null) return;
        var lines = b.text.split("\n"), hit = false;
        findHits(b.text, fr.find).forEach(function (h) {
          if (!chosen[keyOf(e) + "|" + h.line]) return;
          lines[h.line] = applyHit(h.text, h.spans, fr.repl);
          hit = true; changed++;
        });
        if (hit) {
          BV.workspace.setBody(e.root, e.file, lines.join("\n"));
          touched.push(e);
        }
      });
      /* an open editor holds its own DOM copy of the text - push the new text
         into it (and reset its undo baseline) rather than leave it stale */
      touched.forEach(function (e) {
        var ed = editors[keyOf(e)];
        var b = BV.workspace.peek(e.root, e.file);
        if (ed && b) ed.setText(b.text);
      });
      BV.toast("replaced " + changed + " occurrence" + (changed === 1 ? "" : "s"));
      updateCounts();
      refreshDirtyMarks();
      renderNav();
      paint(true);
    }

    fIn.addEventListener("input", function () { paint(false); });
    rIn.addEventListener("input", function () { fr.repl = rIn.value; updateFoot(); });
    ensureBuffers().then(function () {
      if (!document.body.contains(panel)) return;
      paint(true);
      try { fIn.focus(); fIn.select(); } catch (e) {}
    });
  }

  /* open a program in a pane and flash one line. The editor for a program
     opened for the FIRST time mounts asynchronously (its text has to be read),
     so wait for the instance instead of assuming one frame is enough. */
  function revealLine(entry, lineIdx) {
    var held = paneHolding(entry);
    openTab(entry, held >= 0 ? held : st.activePane);
    var tries = 0;
    (function waitForEditor() {
      var ed = editors[keyOf(entry)];
      if (!ed || !ed.el.parentNode) {
        if (tries++ > 40) return;              /* ~2s, then give up quietly */
        setTimeout(waitForEditor, 50);
        return;
      }
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
    })();
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
    var acts = BV.el("div",
      { style: "display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem" });
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
          refreshDirtyMarks();
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
    var acts = BV.el("div",
      { style: "display:flex;gap:.6rem;justify-content:flex-end;margin-top:1rem" });
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
      if (!st.railOpen) { st.railOpen = true; persistPanels(); renderShell(); }
      st.railTab = "find";
      renderRail(sel || undefined);
      return;
    }
    if (e.key === "Escape" && st.railTab === "find") {
      st.railTab = "set";
      renderRail();
    }
  }, true);

  BV.state.on("workspace", function () { if (el.counts) updateCounts(); });

  BV.openWorkspace = function () {
    if (location.hash === "#edit") BV.route();
    else location.hash = "#edit";
  };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "edit", label: "edit", render: render,
                 hidden: true, always: true, shell: true });
})();
