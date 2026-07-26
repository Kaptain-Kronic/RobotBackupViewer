/* tabs/edit.js - the multi-robot EDIT WORKSPACE (#edit).

   A shell screen (like home): it owns the whole window and renders with zero
   backups open, because a working set spans robots while the tabbar and
   BV.tabState are per-backup. It replaces the old in-place edit toggle that
   lived in the programs tab - one editing surface, no drift.

   Layout: [rail] | panes | [navigator], both side panels collapsible, every
   divider draggable. The split is DERIVED, never toggled: a second pane exists
   exactly while it holds tabs, so dropping a program on the right edge opens it
   and closing the last tab folds it away. Programs are read and exported
   through the path-addressed ws_* endpoints (BV.workspace), so the workspace
   never opens a session.

   Editor instances are CACHED per program and their DOM re-attached on every
   re-render, so undo history, caret and scroll survive closing a tab, switching
   panes and a whole route away. They are dropped only when the program leaves
   the workspace.

   All state here is MODULE-LEVEL - route() rebuilds the DOM on every hash
   change, so nothing may live in the DOM alone. */
(function () {
  "use strict";

  /* the pane LAYOUT is a tree: a leaf holds tabs, a split holds two kids in a
     row (side by side) or a column (stacked) with draggable sizes. Up to
     MAX_PANES leaves; dropping on a pane's EDGE splits it in that direction,
     the center lands in its tabs. An emptied leaf folds away and a single-kid
     split collapses - still derived, never toggled. */
  var MAX_PANES = 4;
  var treeSeq = 0;
  function mkLeaf(tabs) {
    return { id: "L" + (++treeSeq), leaf: true, tabs: tabs || [], active: 0 };
  }

  var st = {
    tree: mkLeaf([]),
    activeLeaf: null,        /* leaf id; fixed up by normalizeTree */
    railOpen: true,
    navOpen: true,
    railTab: "set",          /* set | find */
    setFolds: {},            /* working-set robot folds */
    frFolds: {},             /* find-result folds */
    details: {},             /* leaf id -> attrs/positions panel open */
    detH: {},                /* leaf id -> details panel px height */
    navSeg: "calls",         /* calls | labels */
    /* the rail's selection: a set of ENTRY IDS (stable across renames, unlike
       a row index or a name), plus the row a shift-range measures from. Ids,
       not nodes: renderWorkingSet() re-runs on every keystroke in an editor. */
    sel: {}, selAnchor: null,
  };
  st.activeLeaf = st.tree.id;
  var closedTabs = [];       /* ctrl+shift+t: [{entryId, leafId}], newest last */
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

  function keyOf(t) { return t.id; }   /* stable: a rename must not orphan a tab */

  /* ---- tree walkers ---- */
  function leaves(node, out) {
    node = node || st.tree;
    out = out || [];
    if (node.leaf) out.push(node);
    else node.kids.forEach(function (k) { leaves(k, out); });
    return out;
  }
  function leafById(id, node) {
    node = node || st.tree;
    if (node.id === id) return node.leaf ? node : null;
    if (node.leaf) return null;
    for (var i = 0; i < node.kids.length; i++) {
      var f = leafById(id, node.kids[i]);
      if (f) return f;
    }
    return null;
  }
  function parentOf(id, node) {
    node = node || st.tree;
    if (node.leaf) return null;
    for (var i = 0; i < node.kids.length; i++) {
      if (node.kids[i].id === id) return node;
      var f = parentOf(id, node.kids[i]);
      if (f) return f;
    }
    return null;
  }
  function activeLeafObj() {
    return leafById(st.activeLeaf) || leaves()[0];
  }

  /* ---- the working set's selection ---- */
  function selIds() { return Object.keys(st.sel); }
  function selCount() { return selIds().length; }
  function selEntries() {
    return selIds().map(function (id) { return BV.workspace.byId(id); }).filter(Boolean);
  }
  function setSel(ids) {
    st.sel = {};
    (ids || []).forEach(function (id) { st.sel[id] = true; });
  }
  /* an id can outlive its entry (removed here, renamed away by find/replace, a
     workspace cleared elsewhere, a set restored from settings). Prune on every
     paint so a stale id can never inflate the count or resurrect a row. */
  function pruneSel() {
    var live = {};
    BV.workspace.entries().forEach(function (e) { live[e.id] = true; });
    selIds().forEach(function (id) { if (!live[id]) delete st.sel[id]; });
    if (st.selAnchor && !live[st.selAnchor]) st.selAnchor = null;
  }
  /* the rail top to bottom, a folded group contributing nothing. A shift-range
     runs over THIS list, so one shift-click can never sweep up forty programs
     you cannot see. Folding does not clear the selection (find/replace keeps
     its ticks across a fold too) - the rail head says "N of M selected", so
     nothing is ever armed and invisible at the same time. */
  function visibleEntries() {
    var out = [];
    BV.workspace.byRobot().forEach(function (g) {
      if (st.setFolds[g.root]) return;
      g.programs.forEach(function (e) { out.push(e); });
    });
    return out;
  }

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
    /* (ws_split is dead - the split is derived from pane 1's tabs now. The
       stale settings key is left alone rather than bought with a migration.) */
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
    /* the inline pane diff: diff… arms pick-two, the stats + prev/next ride
       here while it is on. Always offered - picking opens what it needs. */
    el.diffStats = BV.el("span", { class: "ws-diffstats",
      style: "display:flex;gap:.3rem;align-items:center" });
    bar.appendChild(el.diffStats);
    el.armHint = BV.el("span", { class: "ws-armhint" });
    bar.appendChild(el.armHint);
    el.diffPrev = BV.el("button", { class: "btn", title: "previous difference" }, "↑");
    el.diffNext = BV.el("button", { class: "btn", title: "next difference" }, "↓");
    el.diffPrev.style.display = el.diffNext.style.display = "none";
    el.diffPrev.addEventListener("click", function () { jumpDiff(-1); });
    el.diffNext.addEventListener("click", function () { jumpDiff(1); });
    bar.appendChild(el.diffPrev);
    bar.appendChild(el.diffNext);
    el.diffBtn = BV.el("button", { class: "btn",
      title: "mark the differences between two programs inside their editors, " +
             "live while you edit - click it, then pick the two" }, "diff…");
    el.diffBtn.addEventListener("click", function () {
      if (pd.on) { clearPaneDiff(); renderPanes(); return; }
      pd.armed = !pd.armed;
      pd.picked = [];
      renderPanes();
    });
    bar.appendChild(el.diffBtn);
    el.reviewBtn = BV.el("button", { class: "btn",
      title: "original vs edited for every program - body, attributes, points and renames" },
      "review…");
    el.reviewBtn.addEventListener("click", function () { showReview(); });
    bar.appendChild(el.reviewBtn);
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
    /* pending, not dirty: a rename is a real change with no buffer edit, and
       gating export on buffer dirt alone made a rename-only set unexportable */
    var n = BV.workspace.count(), d = BV.workspace.pendingCount();
    var robots = BV.workspace.byRobot().length;
    el.counts.textContent = n
      ? robots + (robots === 1 ? " robot · " : " robots · ") + n + " programs" +
        (d ? " · " + d + " edited" : "")
      : "nothing in the workspace yet";
    el.exportBtn.textContent = d ? "export " + d + " file" + (d === 1 ? "" : "s") + "…" : "export…";
    el.exportBtn.disabled = !d;
    /* review stays available while anything is IN the workspace: a review of a
       clean program answers "no changes" plainly instead of being unreachable */
    if (el.reviewBtn) el.reviewBtn.disabled = !n;
  }
  /* counts are safe any time; the RAIL must never be rebuilt while find owns
     it, or clicking a result would destroy the list being worked through */
  function afterChange() {
    updateCounts();
    if (st.railTab === "set" && st.railOpen) renderWorkingSet();
  }
  /* dirty dots without touching the rest of the DOM */
  function refreshDirtyMarks() {
    if (!el.paneEls) return;
    leaves().forEach(function (l) {
      var pEl = el.paneEls[l.id];
      if (!pEl) return;
      var tabs = pEl.querySelectorAll(".ws-tab");
      l.tabs.forEach(function (t, ti) {
        var tab = tabs[ti];
        if (!tab) return;
        var dirty = BV.workspace.dirty(t);
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

  function headText() {
    var n = BV.workspace.count(), s = selCount();
    return s ? s + " of " + n + " selected" : n + " programs";
  }

  function renderWorkingSet() {
    pruneSel();
    el.railHead.innerHTML = "";
    el.headCount = BV.el("span", null, headText());
    el.railHead.appendChild(el.headCount);
    if (BV.workspace.count()) {
      var clr = BV.el("button", { class: "btn",
        style: "margin-left:auto;padding:.1rem .4rem;font-size:.7rem",
        title: "remove everything from the workspace" }, "clear");
      clr.addEventListener("click", function () {
        if (BV.workspace.anyDirty() &&
            !window.confirm("There are unsaved edits. Discard them?")) return;
        BV.workspace.entries().forEach(function (e) { dropEditor(e); });
        BV.workspace.clear();
        setSel([]);
        st.selAnchor = null;
        st.tree = mkLeaf([]);
        st.activeLeaf = st.tree.id;
        st.details = {};
        st.detH = {};
        closedTabs = [];
        renderPanes();
        afterChange();
      });
      el.railHead.appendChild(clr);
    }

    el.railBody.innerHTML = "";
    el.progRows = [];
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
        return BV.workspace.dirty(e);
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
        var dirty = BV.workspace.dirty(e);
        var row = BV.el("div", { class: "ws-prog", draggable: "true", title: e.file });
        if (pd.armed) row.classList.add("pickable");
        if (st.sel[keyOf(e)]) row.classList.add("sel");
        if (st.selAnchor === keyOf(e)) row.classList.add("anchor");
        row.innerHTML = '<span class="dot' + (dirty ? " dirty" : "") + '"></span>' +
          '<span class="nm">' + BV.esc(BV.workspace.displayName(e)) + "</span>";
        var more = BV.el("span", { class: "rm",
          title: "actions (or right-click the row)" }, "⋯");
        more.addEventListener("click", function (ev) {
          ev.stopPropagation();
          rowMenu(more, e, dirty);
        });
        row.appendChild(more);
        /* one menu for the ⋯ button AND right-click on the row (home.js does
           the same for library rows). Anchored to a POINT, not to the ⋯ node:
           the rail can be rebuilt under an open menu, and BV.menu's dismissal
           holds on to an element anchor. */
        row.addEventListener("contextmenu", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          /* right-clicking OUTSIDE the selection means "I mean this one": it
             re-selects that row first, so a menu can never act on rows you had
             forgotten were lit. */
          if (!st.sel[keyOf(e)]) {
            setSel([keyOf(e)]);
            st.selAnchor = keyOf(e);
            paintSelection();
          }
          if (selCount() > 1) multiMenu({ x: ev.clientX, y: ev.clientY });
          else rowMenu({ x: ev.clientX, y: ev.clientY }, e, dirty);
        });
        row.addEventListener("click", function (ev) {
          if (pd.armed) { pickForDiff(e); return; }
          clickRow(e, ev);
        });
        row.addEventListener("dblclick", function () {
          if (pd.armed) return;
          openTab(e);
        });
        row.addEventListener("dragstart", function (ev) {
          drag = { kind: "prog", entry: e };
          try { ev.dataTransfer.setData("text/plain", e.name); } catch (x2) {}
        });
        row.addEventListener("dragend", function () { drag = null; hideDrop(); });
        el.railBody.appendChild(row);
        /* node <-> id pairs live HERE, never in a data attribute: an entry id
           contains BV.KEYSEP, which is a NUL. */
        el.progRows.push({ el: row, id: keyOf(e) });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * rename / duplicate / create
   *
   * A rename is export-time only: the source stays untouched (as everything
   * here does) and the export writes <newname>.LS with its /PROG header
   * repointed to match. A duplicate is a SECOND entry on the same source with
   * its own name and its own buffer - and a duplicate with an empty body is
   * the honest "new program", because the header it inherits is one this
   * controller already accepted.
   * ------------------------------------------------------------------ */
  function askName(title, initial, hint, onOk) {
    var body = BV.el("div");
    if (hint) body.appendChild(BV.el("div", { class: "dim", style: "font-size:.8rem;margin-bottom:.5rem" }, hint));
    var inp = BV.el("input", { type: "text", spellcheck: "false",
      style: "width:100%;box-sizing:border-box;font-family:var(--font-mono);" +
             "background:var(--bg);color:var(--text);border:1px solid var(--edge);" +
             "border-radius:4px;padding:.3rem .5rem" });
    inp.value = initial || "";
    body.appendChild(inp);
    var err = BV.el("div", { style: "color:var(--error);font-size:.76rem;min-height:1.1em;margin-top:.3rem" });
    body.appendChild(err);
    var acts = BV.el("div", { style: "display:flex;gap:.5rem;justify-content:flex-end;margin-top:.6rem" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var ok = BV.el("button", { class: "btn primary" }, "ok");
    acts.appendChild(cancel);
    acts.appendChild(ok);
    body.appendChild(acts);
    var m = BV.modal(title, body, {
      onKey: function (e, close) {
        if (e.key === "Enter") { submit(); return true; }
        return false;
      },
    });
    function submit() {
      var v = inp.value.trim();
      var why = onOk(v);
      if (why) { err.textContent = why; return; }
      m.close(true);
    }
    cancel.addEventListener("click", function () { m.close(true); });
    ok.addEventListener("click", submit);
    setTimeout(function () { try { inp.focus(); inp.select(); } catch (e) {} }, 0);
  }

  function baseName(e) {
    return (e.saveAs || e.name).replace(/\.[Ll][Ss]$/, "");
  }

  /* plain = only this - ctrl = toggle - shift = range from the anchor -
     ctrl+shift = add the range.

     This repaints CLASSES rather than calling renderWorkingSet(): replacing
     the row node between the two halves of a double-click eats the dblclick
     that opens the program, which is exactly what the old single-selection
     click did. */
  function clickRow(e, ev) {
    var id = keyOf(e);
    if (ev.shiftKey && st.selAnchor) {
      var list = visibleEntries().map(keyOf);
      var a = list.indexOf(st.selAnchor), b = list.indexOf(id);
      if (a >= 0 && b >= 0) {
        if (!(ev.ctrlKey || ev.metaKey)) st.sel = {};
        for (var i = Math.min(a, b); i <= Math.max(a, b); i++) st.sel[list[i]] = true;
        /* the anchor STAYS: shift again re-measures from the same origin, the
           way every file list behaves */
        paintSelection();
        return;
      }
      /* the anchor sits inside a folded group - fall through to a plain click */
    }
    if (ev.ctrlKey || ev.metaKey) {
      if (st.sel[id]) delete st.sel[id]; else st.sel[id] = true;
    } else {
      setSel([id]);
    }
    st.selAnchor = id;
    paintSelection();
  }

  function paintSelection() {
    (el.progRows || []).forEach(function (r) {
      r.el.classList.toggle("sel", !!st.sel[r.id]);
      r.el.classList.toggle("anchor", st.selAnchor === r.id);
    });
    if (el.headCount) el.headCount.textContent = headText();
  }

  /* THE way a program leaves the workspace: the ⋯ item, the multi-select menu
     and the Delete key all land here, so the unsaved-work guard and the
     editor/tab teardown can never drift apart. */
  function removeEntries(list) {
    list = (list || []).filter(Boolean);
    if (!list.length) return false;
    var dirty = list.filter(function (e) { return BV.workspace.dirty(e); });
    if (dirty.length && !window.confirm(dirty.length === 1
        ? "Discard unsaved edits to " + BV.workspace.displayName(dirty[0]) + "?"
        : "Discard unsaved edits to " + dirty.length + " programs?")) return false;
    list.forEach(function (e) {
      dropEditor(e);
      closeEverywhere(e);
      BV.workspace.remove(e.id);
      delete st.sel[keyOf(e)];
    });
    st.selAnchor = null;
    renderPanes();
    afterChange();
    return true;
  }

  /* the menu for a SET. Deliberately one item: rename and duplicate have no
     sane bulk form, and everything else here acts on one program. */
  function multiMenu(at) {
    var list = selEntries(), n = list.length;
    BV.menu(at, [
      { label: "remove " + n + " programs from workspace (del)", danger: true,
        onClick: function () { removeEntries(list); } },
    ]);
  }

  function rowMenu(anchor, e, dirty) {
    var items = [];
    var held = leafHolding(e);
    var act = activeLeafObj();
    /* "open beside what I'm looking at": splits the active pane to the right,
       or focuses the pane that already shows it. Omitted (BV.menu has no
       disabled state) when it cannot mean anything: a split needs work on
       BOTH sides, so the active pane must keep at least one tab after the
       move - and a new pane needs room under the cap. */
    var elsewhere = held && held.id !== act.id;
    var actKeeps = act.tabs.length - (held && held.id === act.id ? 1 : 0);
    if (!pd.on && (elsewhere || (actKeeps > 0 && leaves().length < MAX_PANES))) {
      items.push({ label: "open in split view",
        onClick: function () {
          if (elsewhere) {
            held.active = held.tabs.findIndex(function (t) { return keyOf(t) === keyOf(e); });
            st.activeLeaf = held.id;
            renderPanes();
            return;
          }
          if (held) {
            held.tabs = held.tabs.filter(function (t) { return keyOf(t) !== keyOf(e); });
          }
          if (!splitLeaf(activeLeafObj(), "row", 1, e)) placeTab(e);
          normalizeTree();
          renderPanes();
          afterChange();
        } });
    }
    items.push(
      { label: "review changes",
        onClick: function () { showReview([e]); } },
      { label: e.saveAs ? "rename (now " + e.saveAs + ")" : "rename…",
        onClick: function () {
          askName("rename program", baseName(e),
            "exports as <name>.LS with its /PROG header repointed. The backup " +
            "is never touched, and CALLs to it elsewhere are a separate " +
            "find/replace.",
            function (v) {
              if (!v) return "a name is required";
              if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(v.replace(/\.[Ll][Ss]$/, ""))) {
                return "letters, digits and underscore only, not starting with a digit";
              }
              if (!BV.workspace.rename(e.id, v)) return "that name is already used for this robot";
              afterChange();
              renderPanes();
              return "";
            });
        } },
      { label: "duplicate…",
        onClick: function () {
          askName("duplicate program", baseName(e) + "_COPY",
            "a second program from the same source, with its own edits.",
            function (v) {
              if (!v) return "a name is required";
              var made = BV.workspace.duplicate(e.id, v, false);
              if (!made) return "invalid name, or already used for this robot";
              afterChange();
              openTab(made);
              return "";
            });
        } },
      { label: "new empty program from this…",
        onClick: function () {
          askName("new program", "NEWPROG",
            "clones this program's header (one the controller already accepted) " +
            "with an empty body.",
            function (v) {
              if (!v) return "a name is required";
              var made = BV.workspace.duplicate(e.id, v, true);
              if (!made) return "invalid name, or already used for this robot";
              afterChange();
              openTab(made);
              return "";
            });
        } },
      { label: "remove from workspace", danger: true,
        onClick: function () { removeEntries([e]); } });
    BV.menu(anchor, items);
  }

  /* ------------------------------------------------------------------ *
   * panes + tabs
   * ------------------------------------------------------------------ */

  /* a program lives in exactly ONE pane: asking for it anywhere else focuses
     the pane that already has it instead of opening a second copy */
  function leafHolding(entry) {
    return leaves().filter(function (l) {
      return l.tabs.some(function (t) { return keyOf(t) === keyOf(entry); });
    })[0] || null;
  }

  /* split `l` in `dir`; the new leaf (holding `entry`) sits before (side=0)
     or after (side=1) it. null when the pane cap is hit. */
  function splitLeaf(l, dir, side, entry) {
    if (leaves().length >= MAX_PANES) {
      BV.toast(MAX_PANES + " panes is the cap");
      return null;
    }
    var nl = mkLeaf([entry]);
    var split = { id: "S" + (++treeSeq), dir: dir, sizes: [0.5, 0.5],
                  kids: side ? [l, nl] : [nl, l] };
    if (st.tree.id === l.id) st.tree = split;
    else {
      var p = parentOf(l.id);
      p.kids[p.kids.indexOf(l)] = split;
    }
    st.activeLeaf = nl.id;
    return nl;
  }

  /* every path that can change the tree funnels through here: an emptied leaf
     folds away (the survivor slides over by construction), a single-kid split
     collapses to that kid, actives are clamped, per-leaf state for dead
     leaves is dropped, and the active leaf is always a live one. */
  function normalizeTree() {
    (function prune(n) {
      if (n.leaf) return;
      n.kids.forEach(prune);
      n.kids = n.kids.filter(function (k) { return k.leaf ? k.tabs.length : k.kids.length; });
      n.kids = n.kids.map(function (k) {
        return (!k.leaf && k.kids.length === 1) ? k.kids[0] : k;
      });
    })(st.tree);
    if (!st.tree.leaf) {
      st.tree.kids = st.tree.kids.filter(function (k) {
        return k.leaf ? k.tabs.length : k.kids.length;
      });
      if (st.tree.kids.length === 1) st.tree = st.tree.kids[0];
      else if (!st.tree.kids.length) st.tree = mkLeaf([]);
    }
    var live = {};
    leaves().forEach(function (l) {
      live[l.id] = true;
      if (l.active >= l.tabs.length || l.active < 0) {
        l.active = l.tabs.length ? l.tabs.length - 1 : 0;
      }
    });
    Object.keys(st.details).forEach(function (id) { if (!live[id]) delete st.details[id]; });
    Object.keys(st.detH).forEach(function (id) { if (!live[id]) delete st.detH[id]; });
    if (!live[st.activeLeaf]) st.activeLeaf = leaves()[0].id;
    /* closing one of the diff's two tabs ends the diff AND restores the
       parked layout in one motion (the pair it pointed at is gone) */
    if (pd.on && !pairOpen()) clearPaneDiff();
  }

  /* put an entry in a leaf WITHOUT painting; returns the leaf it actually
     landed in (a program lives in exactly one pane, so an already-open one
     wins its existing home). openTab is the single-entry wrapper that paints. */
  function placeTab(entry, leaf) {
    var held = leafHolding(entry);
    leaf = held || leaf || activeLeafObj();
    var at = -1;
    leaf.tabs.forEach(function (t, i) { if (keyOf(t) === keyOf(entry)) at = i; });
    if (at < 0) {
      leaf.tabs.push(entry);
      at = leaf.tabs.length - 1;
    }
    leaf.active = at;
    return leaf;
  }

  function openTab(entry, leaf) {
    /* diff mode locks the layout; a program already on screen may still be
       focused (that changes nothing about the tree) */
    if (pd.on && !leafHolding(entry)) {
      BV.toast("close the diff first");
      return;
    }
    st.activeLeaf = placeTab(entry, leaf).id;
    normalizeTree();
    renderPanes();
    afterChange();
  }
  /* closing a tab keeps the cached editor (undo history included); the TAB
     itself goes on the reopen stack for ctrl+shift+t */
  function closeTab(leaf, idx) {
    var t = leaf.tabs[idx];
    if (t) {
      closedTabs.push({ entryId: keyOf(t), leafId: leaf.id });
      if (closedTabs.length > 40) closedTabs.shift();
    }
    leaf.tabs.splice(idx, 1);
    normalizeTree();
    renderPanes();
  }
  function reopenClosedTab() {
    while (closedTabs.length) {
      var c = closedTabs.pop();
      var e = BV.workspace.byId(c.entryId);
      if (!e) continue;                     /* left the workspace since */
      if (leafHolding(e)) continue;         /* already back on screen */
      openTab(e, leafById(c.leafId) || activeLeafObj());
      return;
    }
    BV.toast("no closed tab to reopen");
  }
  function closeEverywhere(entry) {
    leaves().forEach(function (l) {
      l.tabs = l.tabs.filter(function (t) { return keyOf(t) !== keyOf(entry); });
    });
    /* an entry removed mid-diff must leave the PARKED tree too, or the
       restore would resurrect a tab for a program that no longer exists */
    if (parked) {
      leaves(parked).forEach(function (l) {
        l.tabs = l.tabs.filter(function (t) { return keyOf(t) !== keyOf(entry); });
      });
    }
    normalizeTree();
  }
  /* only leaving the workspace discards the editor (and its undo) */
  function dropEditor(entry) {
    var k = keyOf(entry);
    var ed = editors[k];
    if (ed && ed.el && ed.el.parentNode) ed.el.parentNode.removeChild(ed.el);
    delete editors[k];
  }
  function cycleTab(dir) {
    var leaf = activeLeafObj();
    if (!leaf || leaf.tabs.length < 2) return;
    leaf.active = (leaf.active + dir + leaf.tabs.length) % leaf.tabs.length;
    renderPanes();
  }
  function hideDrop() {
    document.querySelectorAll(".ws-pane > .dropzone.show").forEach(function (d) {
      d.classList.remove("show", "split");
    });
  }

  var EDGE_ZONE = 0.22;  /* the outer band of a pane that means "split here" */

  /* where would a drop land? Read by dragover (to paint it) and by drop (to
     act on it), so the picture and the result can never drift apart. The
     nearest edge wins inside the outer band - left/right split side by side,
     top/bottom stack - and everything else lands in this pane's tabs. */
  function zoneFor(paneEl, cx, cy) {
    var r = paneEl.getBoundingClientRect();
    var x = r.width ? (cx - r.left) / r.width : 0.5;
    var y = r.height ? (cy - r.top) / r.height : 0.5;
    var edge = [
      { z: "left", d: x }, { z: "right", d: 1 - x },
      { z: "top", d: y }, { z: "bottom", d: 1 - y },
    ].sort(function (a, b) { return a.d - b.d; })[0];
    return edge.d < EDGE_ZONE ? edge.z : "center";
  }
  function paintZone(dz, zone) {
    hideDrop();              /* ONE lit zone ever - stale ones die here */
    dz.style.left = zone === "right" ? "50%" : "0";
    dz.style.top = zone === "bottom" ? "50%" : "0";
    dz.style.width = (zone === "left" || zone === "right") ? "50%" : "100%";
    dz.style.height = (zone === "top" || zone === "bottom") ? "50%" : "100%";
    dz.classList.toggle("split", zone !== "center");
    dz.classList.add("show");
  }

  function dropOn(leaf, zone, d) {
    if (pd.on) { BV.toast("close the diff first"); return; }
    var entry = d.entry;
    var held = leafHolding(entry);
    if (zone === "center") {
      /* a drop is an explicit PLACEMENT, so it moves a program that is open
         elsewhere. (Double-click is the "just show me this" gesture, and that
         one still focuses wherever it already lives.) */
      if (held && held.id !== leaf.id) {
        held.tabs = held.tabs.filter(function (t) { return keyOf(t) !== keyOf(entry); });
        normalizeTree();
      }
      openTab(entry, leafById(leaf.id) || activeLeafObj());
      return;
    }
    if (held && held.id === leaf.id && leaf.tabs.length === 1) {
      BV.toast("that's the only program here — nothing to split against");
      return;
    }
    /* remove-then-count: moving a pane's last tab onto another pane's edge
       folds the source away, so the split must not be refused for a pane
       that is about to stop existing */
    if (held) {
      held.tabs = held.tabs.filter(function (t) { return keyOf(t) !== keyOf(entry); });
      normalizeTree();
    }
    var target = leafById(leaf.id) || activeLeafObj();
    var made = splitLeaf(target, (zone === "left" || zone === "right") ? "row" : "col",
                         (zone === "right" || zone === "bottom") ? 1 : 0, entry);
    if (!made) {
      /* cap hit: the entry still needs a home - back into the target's tabs */
      placeTab(entry, target);
      st.activeLeaf = target.id;
    }
    normalizeTree();
    renderPanes();
    afterChange();
  }

  function activeTab() {
    var leaf = activeLeafObj();
    return leaf.tabs[leaf.active] || null;
  }

  function renderPanes() {
    if (!el.work) return;
    /* detach cached editors before wiping, so their DOM (and undo) survives */
    Object.keys(editors).forEach(function (k) {
      var ed = editors[k];
      if (ed && ed.el && ed.el.parentNode) ed.el.parentNode.removeChild(ed.el);
    });
    el.work.innerHTML = "";
    el.paneEls = {};                       /* leaf id -> pane element */
    var wrap = BV.el("div", { class: "ws-panes" });
    el.work.appendChild(wrap);
    wrap.appendChild(renderNode(st.tree));
    syncDiffUi();
    renderNav();
  }

  /* the layout tree, recursively: a split is a flex row/column of two kids
     with a draggable seam between them; a leaf is a pane */
  function renderNode(node) {
    if (node.leaf) return buildPane(node);
    var box = BV.el("div", { class: "ws-split " + node.dir });
    node.kids.forEach(function (k, i) {
      if (i) box.appendChild(splitResizer(box, node, i));
      var kid = BV.el("div", { class: "ws-kid" });
      kid.style.flex = node.sizes[i] + " 1 0";
      kid.appendChild(renderNode(k));
      box.appendChild(kid);
    });
    return box;
  }

  function splitResizer(box, node, i) {
    var rz = BV.el("div", { class: "ws-splitrz", title: "drag to resize" });
    rz.addEventListener("mousedown", function (e) {
      e.preventDefault();
      rz.classList.add("dragging");
      var horiz = node.dir === "row";
      var r = box.getBoundingClientRect();
      function mv(ev) {
        var f = horiz ? (ev.clientX - r.left) / r.width : (ev.clientY - r.top) / r.height;
        f = Math.max(0.12, Math.min(0.88, f));
        node.sizes[i - 1] = f;
        node.sizes[i] = 1 - f;
        var kids = box.querySelectorAll(":scope > .ws-kid");
        kids[i - 1].style.flex = f + " 1 0";
        kids[i].style.flex = (1 - f) + " 1 0";
      }
      function up() {
        document.removeEventListener("mousemove", mv);
        document.removeEventListener("mouseup", up);
        rz.classList.remove("dragging");
      }
      document.addEventListener("mousemove", mv);
      document.addEventListener("mouseup", up);
    });
    return rz;
  }

  /* ------------------------------------------------------------------ *
   * the inline pane diff: pick two programs, their EDITORS carry the marks
   *
   * diff… arms picking; the next two clicks (open tabs or working-set rows)
   * choose the pair. Diff mode is a VIEW, not a rearrangement: unless the
   * two already are the only two panes, the layout is PARKED and a clean
   * side-by-side pair presented; closing the diff - or one of its two tabs -
   * restores the parked tree exactly. While it is on the layout is LOCKED:
   * opening anything else says "close the diff first" rather than quietly
   * reshaping the tree under the comparison.
   *
   * The marks are honest: ws_diff_texts runs in normalize mode, so two
   * robots' programs differing only in ref comments / the pendant's
   * IO-status display read as display-only, never as changed lines. Where
   * one side has lines the other lacks, the other side shows REAL blank
   * rows (lsEditor gaps) - both editors are then the same height row for
   * row, and the scroll lock is a plain mirror.
   * ------------------------------------------------------------------ */
  var pd = { armed: false, picked: [], on: false, a: null, b: null,
             rows: null, idx: [], cur: -1, lastD: null };
  var parked = null;          /* the tree as it was before diff mode took over */

  function pairOpen() {
    if (!pd.a || !pd.b) return false;
    var la = leafHolding(pd.a), lb = leafHolding(pd.b);
    return !!(la && lb && la.id !== lb.id);
  }
  function diffEditors() {
    var ea = pd.a && editors[keyOf(pd.a)], eb = pd.b && editors[keyOf(pd.b)];
    return ea && eb && ea.el.parentNode && eb.el.parentNode ? [ea, eb] : null;
  }
  function sideOfLeaf(leaf) {
    if (!pd.on) return null;
    var t = leaf.tabs[leaf.active];
    if (t && pd.a && keyOf(t) === keyOf(pd.a)) return "a";
    if (t && pd.b && keyOf(t) === keyOf(pd.b)) return "b";
    return null;
  }

  function pickForDiff(entry) {
    if (pd.picked.some(function (e) { return keyOf(e) === keyOf(entry); })) return;
    pd.picked.push(entry);
    if (pd.picked.length < 2) {
      renderPanes();
      if (st.railTab === "set" && st.railOpen) renderWorkingSet();
      return;
    }
    var a = pd.picked[0], b = pd.picked[1];
    pd.armed = false;
    pd.picked = [];
    pd.on = true;
    pd.a = a;
    pd.b = b;
    var ls = leaves();
    function shows(l, e) {
      var t = l.tabs[l.active];
      return !!(t && keyOf(t) === keyOf(e));
    }
    var already = ls.length === 2 &&
      ((shows(ls[0], a) && shows(ls[1], b)) || (shows(ls[0], b) && shows(ls[1], a)));
    if (!already) {
      parked = st.tree;
      st.tree = { id: "S" + (++treeSeq), dir: "row", sizes: [0.5, 0.5],
                  kids: [mkLeaf([a]), mkLeaf([b])] };
      st.activeLeaf = st.tree.kids[0].id;
    }
    renderPanes();
    if (st.railTab === "set" && st.railOpen) renderWorkingSet();
    runPaneDiff();
  }

  function clearPaneDiff() {
    var eds = diffEditors();
    pd.on = false;
    pd.armed = false;
    pd.picked = [];
    unlockScroll();
    if (eds) {
      eds.forEach(function (ed) {
        ed.el.querySelectorAll(".wsd-bar").forEach(function (bx) { bx.remove(); });
        ed.setGaps([]);
      });
    }
    pd.a = pd.b = pd.rows = pd.lastD = null;
    pd.idx = [];
    pd.cur = -1;
    if (parked) {
      st.tree = parked;
      parked = null;
      st.activeLeaf = leaves()[0].id;
      normalizeTree();       /* entries removed mid-diff leave holes to fold */
    }
  }

  var diffSeq = 0, diffTimer = null;
  /* debounced: onChange fires per keystroke, and every buffer mutation path
     (typing, find/replace, attrs) funnels through a workspace emit anyway */
  function schedulePaneDiff() {
    if (!pd.on) return;
    clearTimeout(diffTimer);
    diffTimer = setTimeout(function () { runPaneDiff(); }, 400);
  }
  function runPaneDiff(tries) {
    if (!pd.on) return;
    if (!diffEditors()) {          /* first-open editors mount async */
      if ((tries || 0) < 40) {
        setTimeout(function () { runPaneDiff((tries || 0) + 1); }, 100);
      }
      return;
    }
    var seq = ++diffSeq;
    var a = pd.a, b = pd.b;
    Promise.all([BV.workspace.buffer(a), BV.workspace.buffer(b)])
      .then(function (bufs) {
        return BV.api.call("ws_diff_texts", bufs[0].text, bufs[1].text, true);
      })
      .then(function (d) {
        if (seq !== diffSeq || !pd.on || pd.a !== a || pd.b !== b) return;  /* stale */
        var eds = diffEditors();
        if (!eds) return;
        pd.rows = d.rows;
        pd.lastD = d;
        pd.idx = [];
        d.rows.forEach(function (r, i) {
          if (r.kind !== "same" && r.kind !== "equiv") pd.idx.push(i);
        });
        eds[0].setGaps(gapsFor(d.rows, "a"));
        eds[1].setGaps(gapsFor(d.rows, "b"));
        decorateDiff(eds, d.rows);
        lockScroll(eds);
        syncDiffUi();
      })
      .catch(function (e) {
        if (seq !== diffSeq || !pd.on) return;
        if (el.diffStats) {
          el.diffStats.innerHTML = '<span class="dim" style="font-size:.74rem">' +
            "diff unavailable: " + BV.esc(e.message || e) + "</span>";
        }
      });
  }

  /* rows -> [{after, n}] for one side: a run of rows where this side is null
     becomes one gap after the last line this side DID have */
  function gapsFor(rows, mine) {
    var out = [], last = 0, run = 0;
    rows.forEach(function (r) {
      var n = r[mine + "_n"];
      if (n === null) { run++; return; }
      if (run) { out.push({ after: last, n: run }); run = 0; }
      last = n;
    });
    if (run) out.push({ after: last, n: run });
    return out;
  }

  function decorateDiff(eds, rows) {
    [["a", eds[0]], ["b", eds[1]]].forEach(function (cfg) {
      var mine = cfg[0], ed = cfg[1];
      var scroller = ed.el.querySelector(".lsed-scroll");
      var lh = parseFloat(getComputedStyle(ed.code).lineHeight) || 16;
      ed.el.querySelectorAll(".wsd-bar").forEach(function (bx) { bx.remove(); });
      rows.forEach(function (r) {
        var n = r[mine + "_n"];
        if (n === null || r.kind === "same") return;  /* nulls are real gaps */
        var bar = BV.el("div", { class: "wsd-bar " +
          (r.kind === "change" ? "change" : r.kind === "equiv" ? "equiv" : "only") });
        bar.style.top = ed.lineTop(n) + "px";         /* gap-aware, never n*lh */
        bar.style.height = lh + "px";
        scroller.appendChild(bar);
      });
    });
  }

  /* locked scrolling: the gaps made both sides the same height row for row,
     so the lock is a plain mirror - no alignment map needed */
  var lockHandlers = null;
  function lockScroll(eds) {
    unlockScroll();
    var sA = eds[0].el.querySelector(".lsed-scroll");
    var sB = eds[1].el.querySelector(".lsed-scroll");
    var syncing = false;
    function mirror(src, dst) {
      return function () {
        if (syncing) return;
        syncing = true;
        dst.scrollTop = src.scrollTop;
        syncing = false;
      };
    }
    var hA = mirror(sA, sB), hB = mirror(sB, sA);
    sA.addEventListener("scroll", hA);
    sB.addEventListener("scroll", hB);
    lockHandlers = [[sA, hA], [sB, hB]];
  }
  function unlockScroll() {
    (lockHandlers || []).forEach(function (h) { h[0].removeEventListener("scroll", h[1]); });
    lockHandlers = null;
  }

  function jumpDiff(dir) {
    if (!pd.on || !pd.rows) return;
    if (!pd.idx.length) { BV.toast("no differences"); return; }
    pd.cur = (pd.cur + dir + pd.idx.length) % pd.idx.length;
    var r = pd.rows[pd.idx[pd.cur]];
    var side = r.a_n !== null ? "a" : "b";
    var ed = editors[keyOf(pd[side])];
    if (!ed || !ed.el.parentNode) return;
    var scroller = ed.el.querySelector(".lsed-scroll");
    var lh = parseFloat(getComputedStyle(ed.code).lineHeight) || 16;
    var top = ed.lineTop(r[side + "_n"]);
    scroller.scrollTop = Math.max(0, top - scroller.clientHeight / 2);
    /* the mirror lock carries the other side to the same row */
    var bar = BV.el("div", { class: "flashbar" });
    bar.style.top = top + "px";
    bar.style.height = lh + "px";
    scroller.appendChild(bar);
    setTimeout(function () { if (bar.parentNode) bar.parentNode.removeChild(bar); }, 1600);
  }

  /* the toolbar follows the mode; stats repaint from the last result so a
     route away and back does not blank them */
  function syncDiffUi() {
    if (!el.diffBtn) return;
    el.diffBtn.textContent = pd.on ? "diff ✕" : (pd.armed ? "picking…" : "diff…");
    el.diffBtn.classList.toggle("on", pd.on || pd.armed);
    el.diffPrev.style.display = el.diffNext.style.display = pd.on ? "" : "none";
    el.armHint.textContent = pd.armed
      ? (pd.picked.length
          ? "picked " + BV.workspace.displayName(pd.picked[0]) + " — pick the second"
          : "pick two open tabs or working-set programs (esc cancels)")
      : "";
    if (pd.on && pd.lastD) paintDiffStats(pd.lastD);
    else el.diffStats.innerHTML = "";
  }

  function paintDiffStats(d) {
    var s = d.stats;
    var html = '<span class="pill acc">~' + s.change + "</span>" +
      '<span class="pill err">a only ' + s.a_only + "</span>" +
      '<span class="pill on">b only ' + s.b_only + "</span>" +
      (s.equiv ? '<span class="pill ghost" title="same instruction, different ref ' +
                 'comments or IO-status display — not a change">' + s.equiv +
                 " display-only</span>" : "") +
      '<span class="dim" style="font-size:.74rem">' + s.same + " identical</span>";
    if (d.io_status && d.io_status.a !== d.io_status.b) {
      html += '<span class="pill ghost" title="the two sides were saved under ' +
        'different IO-status display (a pendant toggle) — those rows are ' +
        'display-only, not changes">io display differs</span>';
    }
    el.diffStats.innerHTML = html;
  }

  function buildPane(leaf) {
    var many = leaves().length > 1;
    var p = BV.el("div", { class: "ws-pane" + (st.activeLeaf === leaf.id && many ? " active" : "") });
    p.dataset.leaf = leaf.id;
    el.paneEls[leaf.id] = p;
    p.addEventListener("mousedown", function () {
      if (st.activeLeaf === leaf.id) return;
      st.activeLeaf = leaf.id;
      /* classes ONLY - a re-render here destroys the node under the pointer
         between mousedown and click, eating the click (the rail lesson) */
      var multi = leaves().length > 1;
      Object.keys(el.paneEls).forEach(function (id) {
        el.paneEls[id].classList.toggle("active", multi && id === leaf.id);
      });
      renderNav();
    });

    var stripBox = BV.el("div", { class: "ws-strip" });
    /* which side of the diff this pane is - matches the stats pills */
    var side = sideOfLeaf(leaf);
    if (side) stripBox.appendChild(BV.el("span", { class: "ws-side" }, side));
    var strip = BV.el("div", { class: "ws-tabs" });
    stripBox.appendChild(strip);
    /* a vertical wheel pans the strip: with a mouse you are otherwise stuck */
    strip.addEventListener("wheel", function (ev) {
      if (!ev.deltaY) return;
      strip.scrollLeft += ev.deltaY;
      ev.preventDefault();
    }, { passive: false });
    leaf.tabs.forEach(function (t, ti) {
      var dirty = BV.workspace.dirty(t);
      var tab = BV.el("div", { class: "ws-tab" + (leaf.active === ti ? " active" : ""),
                               draggable: "true", title: t.root + "\n" + t.file });
      tab.innerHTML = '<span class="rb">' + BV.esc(labelFor(t.root)) + "</span>" +
        "<span>" + BV.esc(BV.workspace.displayName(t)) + "</span>" + (dirty ? '<span class="dot"></span>' : "");
      if (pd.armed) {
        tab.classList.add("pickable");
        if (pd.picked.some(function (p) { return keyOf(p) === keyOf(t); })) {
          tab.classList.add("picked");
        }
      }
      var x = BV.el("span", { class: "x" }, "✕");
      x.addEventListener("click", function (ev) { ev.stopPropagation(); closeTab(leaf, ti); });
      tab.appendChild(x);
      tab.addEventListener("click", function () {
        if (pd.armed) { pickForDiff(t); return; }
        leaf.active = ti; st.activeLeaf = leaf.id; renderPanes();
      });
      tab.addEventListener("dragstart", function (ev) {
        drag = { kind: "tab", entry: t, fromLeaf: leaf.id };
        try { ev.dataTransfer.setData("text/plain", t.name); } catch (x2) {}
      });
      tab.addEventListener("dragend", function () { drag = null; hideDrop(); });
      strip.appendChild(tab);
    });
    if (!leaf.tabs.length) {
      strip.appendChild(BV.el("span", { class: "dim", style: "font-size:.72rem;padding:.25rem" },
        "drop or double-click a program"));
    } else {
      /* pinned OUTSIDE the scrolling tab box, so it never drifts off-screen
         behind a long tab strip */
      var dt = BV.el("button", { class: "btn ws-detbtn",
        title: "attributes + point data (rarely edited)" },
        st.details[leaf.id] ? "hide details" : "details");
      dt.addEventListener("click", function () {
        st.details[leaf.id] = !st.details[leaf.id];
        renderPanes();
      });
      stripBox.appendChild(dt);
    }
    p.appendChild(stripBox);

    var host = BV.el("div", { class: "ws-pane-host" });
    p.appendChild(host);

    /* drop zones live on the PANE: edges split it, the center lands in its
       tabs. One overlay per pane, but paintZone puts out every other one. */
    var dz = BV.el("div", { class: "dropzone" });
    p.appendChild(dz);
    p.addEventListener("dragover", function (e) {
      if (!drag || pd.on) return;   /* locked layout: no zones to offer */
      e.preventDefault();
      e.stopPropagation();
      paintZone(dz, zoneFor(p, e.clientX, e.clientY));
    });
    p.addEventListener("dragleave", function (e) { if (e.target === p) hideDrop(); });
    p.addEventListener("drop", function (e) {
      if (!drag) return;
      e.preventDefault();
      e.stopPropagation();
      var d = drag;
      drag = null;
      hideDrop();
      var zone = zoneFor(p, e.clientX, e.clientY);
      if (d.kind === "tab" && d.fromLeaf === leaf.id && zone === "center") return; /* no reorder yet */
      dropOn(leaf, zone, d);
    });

    var t = leaf.tabs[leaf.active];
    if (!t) {
      host.innerHTML = '<div class="empty-state" style="height:100%">' +
        '<div class="hint">' + (BV.workspace.count()
          ? "double-click a program in the working set"
          : "add programs to the workspace first") + "</div></div>";
      return p;
    }
    mountEditor(t, host, p, leaf);
    return p;
  }

  /* cached instance re-attached; only a first open builds one */
  function mountEditor(t, host, paneEl, leaf) {
    var k = keyOf(t);
    var cached = editors[k];
    if (cached) {
      host.appendChild(cached.el);
      if (st.details[leaf.id]) {
        var buf = BV.workspace.peek(t);
        if (buf) paneEl.appendChild(detailsPanel(t, buf, leaf));
      }
      renderNav();
      return;
    }
    host.innerHTML = '<div class="dim" style="padding:1rem">loading…</div>';
    BV.workspace.buffer(t).then(function (buf) {
      if (!document.body.contains(host)) return;      /* routed away mid-load */
      host.innerHTML = "";
      editors[k] = BV.lsEditor(host, {
        text: buf.text,
        onChange: function (txt) {
          BV.workspace.setBody(t, txt);
          updateCounts();
          refreshDirtyMarks();
          if (st.railTab === "set" && st.railOpen) renderWorkingSet();
          if (activeTab() && keyOf(activeTab()) === k) renderNav();
        },
      });
      if (st.details[leaf.id]) paneEl.appendChild(detailsPanel(t, buf, leaf));
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
    var buf = BV.workspace.peek(t);
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
        BV.workspace.setAttr(t, name, inp.value);
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
            BV.workspace.setAttr(t, "protect", id);
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
      var edited = BV.workspace.getPos(t, id, gp, name);
      inp.value = edited !== null ? edited
        : (masked ? "" : (current === null || current === undefined ? "" : current));
      if (masked) inp.placeholder = "********";
      inp.addEventListener("change", function () {
        var v = inp.value.trim();
        if (v === "" || (!masked && String(current) === v)) {
          inp.classList.remove("err");
          BV.workspace.setPos(t, { id: id, gp: gp, field: name, value: null });
          if (!masked && v === "") inp.value = current === null ? "" : current;
        } else if (check(v)) {
          inp.classList.remove("err");
          BV.workspace.setPos(t, { id: id, gp: gp, field: name, value: v });
        } else {
          inp.classList.add("err");
          BV.workspace.setPos(t, { id: id, gp: gp, field: name, value: null });
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
      var ce = BV.workspace.getPos(t, pos.id, 1, "comment");
      cmt.value = ce !== null ? ce : (pos.comment || "");
      cmt.addEventListener("change", function () {
        BV.workspace.setPos(t, { id: pos.id, gp: 1, field: "comment",
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
        var cfe = BV.workspace.getPos(t, pos.id, g.gp, "config");
        cin.value = cfe !== null ? cfe : (g.config || "");
        cin.addEventListener("change", function () {
          if (cin.value.indexOf("'") >= 0) { cin.classList.add("err"); return; }
          cin.classList.remove("err");
          BV.workspace.setPos(t, { id: pos.id, gp: g.gp, field: "config",
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
        var b = BV.workspace.peek(e);
        return !b || b.base === null;
      });
      if (!missing.length) return Promise.resolve();
      results.innerHTML = '<div class="dim" style="padding:1rem;font-size:.78rem">reading ' +
        missing.length + " program" + (missing.length === 1 ? "" : "s") + "…</div>";
      return Promise.all(missing.map(function (e) {
        return BV.workspace.buffer(e).catch(function () { return null; });
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
          var b = BV.workspace.peek(e);
          if (!b || b.base === null) return;
          var hits = findHits(b.text, fr.find);
          /* a NAME match is replaceable too: applying it renames the program on
             export (renaming is a real job - style kits are exactly this) */
          var named = nameMatches(baseName(e), fr.find);
          if (hits.length || named) per.push({ e: e, hits: hits, named: named });
        });
        var n = per.reduce(function (a, x) { return a + x.hits.length; }, 0);
        var names = per.filter(function (x) { return x.named; }).length;
        if (!n && !names) return;
        total += n;

        var rbKeys = [];
        per.forEach(function (rec) {
          if (rec.named) rbKeys.push(keyOf(rec.e) + "|name");
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
          if (rec.named) pgKeys.unshift(pgKey + "|name");
          var pgFolded = !!st.frFolds[pgKey];
          var ph = BV.el("div", { class: "fp-pg" });
          if (pgKeys.length > 1) {
            ph.appendChild(picks.group(BV.el("input", { type: "checkbox" }),
              function () { return pgKeys; }, "pg:" + pgKey));
          } else if (rec.named && !rec.hits.length) {
            /* a name-only match is the single actionable thing here, so it gets
               the one checkbox - ticking it renames the program on export */
            var ncb = picks.bind(BV.el("input", { type: "checkbox" }), pgKey + "|name");
            ncb.addEventListener("click", function (ev) { ev.stopPropagation(); });
            ph.appendChild(ncb);
          }
          ph.appendChild(BV.el("span", { class: "caret" }, pgFolded ? "▸" : "▾"));
          ph.appendChild(BV.el("span", { class: "nm" },
            BV.esc(BV.workspace.displayName(rec.e))));
          if (rec.named) {
            ph.appendChild(BV.el("span", { class: "pill acc",
              title: "the program NAME matches - replacing renames it on export" }, "name"));
          }
          ph.appendChild(BV.el("span", { class: "dim", style: "font-size:.7rem;margin-left:auto" },
            String(rec.hits.length)));
          ph.addEventListener("click", function (ev) {
            if (ev.target.tagName === "INPUT") return;
            if (!rec.hits.length) { openTab(rec.e); return; }
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
      var changed = 0, renamed = 0, refused = [], touched = [];
      BV.workspace.entries().forEach(function (e) {
        /* a ticked NAME match renames the program (export-time; the backup is
           never touched). Done first so the rail/tab label updates once. */
        if (chosen[keyOf(e) + "|name"]) {
          var want = baseName(e).replace(buildRx(fr.find), fr.repl);
          if (want && want !== baseName(e)) {
            if (BV.workspace.rename(e.id, want)) { renamed++; changed++; }
            else refused.push(BV.workspace.displayName(e) + " → " + want);
          }
        }
        var b = BV.workspace.peek(e);
        if (!b || b.base === null) return;
        var lines = b.text.split("\n"), hit = false;
        findHits(b.text, fr.find).forEach(function (h) {
          if (!chosen[keyOf(e) + "|" + h.line]) return;
          lines[h.line] = applyHit(h.text, h.spans, fr.repl);
          hit = true; changed++;
        });
        if (hit) {
          BV.workspace.setBody(e, lines.join("\n"));
          touched.push(e);
        }
      });
      if (refused.length) {
        /* say which renames could not happen rather than silently skip them */
        BV.toast("could not rename (name already used): " + refused.join(", "), 5000);
      }
      /* an open editor holds its own DOM copy of the text - push the new text
         into it (and reset its undo baseline) rather than leave it stale */
      touched.forEach(function (e) {
        var ed = editors[keyOf(e)];
        var b = BV.workspace.peek(e);
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

  /* flash (and center) one line of an OPEN program's editor without stealing
     focus or moving the caret - the diff strip's click is inspection, not
     editing. scroll=false leaves the viewport alone (revealLine already
     centered via focusLine). */
  function flashInPane(entry, lineIdx, scroll) {
    var ed = editors[keyOf(entry)];
    if (!ed || !ed.el.parentNode) return;
    var scroller = ed.el.querySelector(".lsed-scroll");
    if (!scroller) return;
    var lh = parseFloat(getComputedStyle(ed.code).lineHeight) || 16;
    var top = ed.lineTop(lineIdx + 1);   /* gap-aware: diff gaps are real pixels */
    if (scroll !== false) {
      scroller.scrollTop = Math.max(0, top - scroller.clientHeight / 2);
    }
    var bar = BV.el("div", { class: "flashbar" });
    bar.style.top = top + "px";
    bar.style.height = lh + "px";
    scroller.appendChild(bar);
    setTimeout(function () { if (bar.parentNode) bar.parentNode.removeChild(bar); }, 1600);
  }

  /* open a program in a pane and flash one line. The editor for a program
     opened for the FIRST time mounts asynchronously (its text has to be read),
     so wait for the instance instead of assuming one frame is enough. */
  function revealLine(entry, lineIdx) {
    openTab(entry);          /* placeTab focuses an existing home on its own */
    var tries = 0;
    (function waitForEditor() {
      var ed = editors[keyOf(entry)];
      if (!ed || !ed.el.parentNode) {
        if (tries++ > 40) return;              /* ~2s, then give up quietly */
        setTimeout(waitForEditor, 50);
        return;
      }
      ed.focusLine(lineIdx + 1);
      flashInPane(entry, lineIdx, false);
    })();
  }

  /* ------------------------------------------------------------------ *
   * review-your-edits: original vs edited, before anything leaves the tool
   *
   * The honesty screen for the whole editing lane: EVERY kind of change an
   * entry can carry is shown - body lines (the shared aligned diff, raw mode:
   * same source, same save-time state, so byte differences are real),
   * attribute edits, position/point edits, and a rename. A clean program says
   * "no changes" plainly instead of rendering an empty table. Originals are
   * the PRISTINE buffer base - exports re-apply to pristine, so what this
   * screen shows is exactly what the export writes.
   * ------------------------------------------------------------------ */
  function stemOf(name) { return String(name || "").replace(/\.[Ll][Ss]$/, ""); }

  function attrChangeRows(buf) {
    var out = [];
    if (!buf || !buf.baseAttrs) return out;
    ["comment", "owner", "protect"].forEach(function (k) {
      if ((buf.attrs[k] || "") !== (buf.baseAttrs[k] || "")) {
        out.push({ what: k, a: buf.baseAttrs[k] || "—", b: buf.attrs[k] || "—" });
      }
    });
    return out;
  }

  /* the pristine value a position edit replaces; masked fields show their
     ******** honestly - a point placed logically but never initialized */
  function posOriginal(buf, ed) {
    var p = (buf.positions || []).filter(function (x) { return x.id === ed.id; })[0];
    if (!p) return "—";
    if (ed.field === "comment") return p.comment || "—";
    var g = (p.groups || []).filter(function (x) { return x.gp === ed.gp; })[0];
    if (!g) return "—";
    if (ed.field === "config") return g.config || "—";
    if (ed.field === "uf" || ed.field === "ut") {
      return g[ed.field] === undefined ? "—" : String(g[ed.field]);
    }
    var v = /^j\d$/.test(ed.field)
      ? (g.joints || [])[+ed.field.slice(1) - 1]
      : g[ed.field];
    if (v === null || v === undefined) return g.masked ? "********" : "—";
    return String(v);
  }

  function posChangeRows(buf) {
    var keys = Object.keys((buf && buf.posEdits) || {});
    keys.sort();
    return keys.map(function (k) {
      var ed = buf.posEdits[k];
      return { what: "P[" + ed.id + "] gp" + ed.gp + " " + ed.field,
               a: posOriginal(buf, ed), b: String(ed.value) };
    });
  }

  var CHANGE_COLS = [
    { key: "what", label: "", dim: true },
    { key: "a", label: "original" },
    { key: "b", label: "edited", accent: true },
  ];

  /* one program's card; .changed says whether anything is in it */
  function reviewCard(e, bodyDiff) {
    var card = BV.el("div", { class: "card rv-card" });
    card.appendChild(BV.el("h3", null,
      BV.esc(labelFor(e.root)) + " · " + BV.esc(BV.workspace.displayName(e))));
    var buf = BV.workspace.peek(e);
    var renamed = !!e.saveAs && e.saveAs.toUpperCase() !== stemOf(e.name).toUpperCase();

    var rows = [];
    if (renamed) rows.push({ what: "program name", a: stemOf(e.name), b: e.saveAs });
    if (buf) {
      rows = rows.concat(attrChangeRows(buf), posChangeRows(buf));
    }
    if (renamed) {
      card.appendChild(BV.el("div", { class: "dim", style: "font-size:.76rem;margin:0 0 .3rem" },
        "exports as " + BV.esc(e.saveAs) + ".LS with its /PROG header repointed — " +
        "the source file is never touched"));
    }
    if (rows.length) card.appendChild(BV.table(CHANGE_COLS, rows));

    var bodyChanged = bodyDiff &&
      (bodyDiff.stats.change + bodyDiff.stats.a_only + bodyDiff.stats.b_only) > 0;
    if (bodyChanged) {
      var bar = BV.el("div", { class: "rv-diffbar" });
      var pd = BV.pdiffView(card, { heads: ["original (backup)", "edited"], data: bodyDiff });
      bar.appendChild(pd.stats);
      bar.appendChild(BV.el("span", { style: "flex:1" }));
      var prev = BV.el("button", { class: "btn", title: "previous difference" }, "↑");
      var next = BV.el("button", { class: "btn", title: "next difference" }, "↓");
      prev.addEventListener("click", function () { pd.jump(-1); });
      next.addEventListener("click", function () { pd.jump(1); });
      bar.appendChild(prev);
      bar.appendChild(next);
      card.insertBefore(bar, pd.el);
    }

    var changed = rows.length > 0 || bodyChanged;
    if (buf && buf.base === null) {
      /* a restored draft whose pristine source could not be re-read: saying
         "no changes" here would be a lie - say what is actually known */
      card.appendChild(BV.el("div", { style: "color:var(--warn);font-size:.8rem" },
        "the original could not be read — body and attribute changes cannot be shown"));
      changed = true;
    } else if (!changed) {
      card.appendChild(BV.el("div", { class: "dim", style: "font-size:.8rem" },
        "no changes — matches the backup"));
    }
    return { el: card, changed: changed };
  }

  /* list defaults to the whole working set (the export…-sibling button);
     the rail's ⋯ menu passes one entry */
  function showReview(list) {
    list = (list || BV.workspace.entries()).slice();
    if (!list.length) { BV.toast("the workspace is empty"); return; }
    var body = BV.el("div", { class: "rv-body" });
    body.innerHTML = '<div class="dim" style="padding:1rem">reading originals…</div>';
    var m = BV.modal("review edits — original vs edited", body);
    m.el.classList.add("rv-modal");

    /* a draft restored from settings has base=null and is unsaved by
       definition - seed pristine first so "original" is real, never a guess */
    Promise.all(list.map(function (e) {
      var b = BV.workspace.peek(e);
      return b && b.base === null
        ? BV.workspace.buffer(e).catch(function () { return null; })
        : Promise.resolve(null);
    })).then(function () {
      return Promise.all(list.map(function (e) {
        var b = BV.workspace.peek(e);
        if (b && b.base !== null && b.text !== b.base) {
          return BV.api.call("ws_diff_texts", b.base, b.text, false)
            .catch(function () { return null; });
        }
        return Promise.resolve(null);
      }));
    }).then(function (diffs) {
      if (!document.body.contains(body)) return;    /* closed mid-load */
      body.innerHTML = "";
      var clean = [];
      list.forEach(function (e, i) {
        var card = reviewCard(e, diffs[i]);
        if (card.changed || list.length === 1) body.appendChild(card.el);
        else clean.push(e);
      });
      var changedN = list.length - clean.length;
      body.insertBefore(BV.el("div", { class: "dim rv-sum" },
        list.length === 1
          ? BV.esc(BV.workspace.displayName(list[0]))
          : changedN + " of " + list.length + " programs changed"),
        body.firstChild);
      if (clean.length && list.length > 1) {
        /* honesty: the unchanged ones are listed, not silently dropped */
        body.appendChild(BV.el("div", { class: "dim", style: "font-size:.76rem;padding:.2rem .4rem" },
          "unchanged: " + BV.esc(clean.map(function (e) {
            return BV.workspace.displayName(e);
          }).join(", "))));
      }
    });
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
    if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === "w" || e.key === "W")) {
      /* close the active tab - and swallow the browser accelerator, which
         would close the whole window */
      e.preventDefault();
      e.stopPropagation();
      var leafW = activeLeafObj();
      if (leafW.tabs.length) closeTab(leafW, leafW.active);
      return;
    }
    if (e.ctrlKey && e.shiftKey && (e.key === "t" || e.key === "T")) {
      e.preventDefault();
      e.stopPropagation();
      reopenClosedTab();
      return;
    }
    if (e.key === "Delete" && !e.ctrlKey && !e.altKey && !e.metaKey) {
      /* Delete belongs to whatever is being typed in first - an editor, a find
         box, a rename field. It only reaches the rail when nothing is, the
         working set is the rail tab on show, and rows are actually lit (the
         head says how many, so this can never fire blind). */
      var a = document.activeElement;
      if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) return;
      if (st.railTab !== "set" || !st.railOpen || !selCount()) return;
      e.preventDefault();
      e.stopPropagation();
      removeEntries(selEntries());
      return;
    }
    if (e.key === "Escape" && pd.armed) {
      pd.armed = false;
      pd.picked = [];
      renderPanes();
      if (st.railTab === "set" && st.railOpen) renderWorkingSet();
      return;
    }
    if (e.key === "Escape" && st.railTab === "find") {
      st.railTab = "set";
      renderRail();
    }
  }, true);

  BV.state.on("workspace", function () {
    if (el.counts) updateCounts();
    schedulePaneDiff();          /* every buffer mutation funnels through here */
  });

  BV.openWorkspace = function () {
    if (location.hash === "#edit") BV.route();
    else location.hash = "#edit";
  };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "edit", label: "edit", render: render,
                 hidden: true, always: true, shell: true });
})();
