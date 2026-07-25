/* tabs/programs.js - program list (style stars, system/binary filters),
   style-table view, and a detail view with highlighted source, call panel
   and an expandable call tree. Source IO/register tokens click to search. */
(function () {
  "use strict";

  var vt = null;
  /* list view state, kept at module scope so it survives navigating into a
     program and back (router re-runs render on an emptied #view). Default view
     is names A-Z with system hidden / binary shown. */
  var listState = { showSystem: false, showBinary: true, sortKey: "name", sortDir: 1, scrollTop: 0 };

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    if (params && params[0] === "styles") renderStyleTable(view, toolbar);
    else if (params && params[0] === "macros") renderMacros(view, toolbar);
    else if (params && params[0] && decodeURIComponent(params[0]).toUpperCase().endsWith(".PC")) {
      renderKarelDetail(view, toolbar, params[0]);
    } else if (params && params[0]) renderDetail(view, toolbar, params[0], params[1]);
    else renderList(view, toolbar);
  }

  /* macros now live under the programs tab (a toolbar button -> #programs/macros)
     instead of their own top-level tab; macros.js exposes BV.macros.render */
  function renderMacros(view, toolbar) {
    var crumb = BV.el("div", { class: "crumb", style: "margin:0 1.25rem .2rem" });
    crumb.innerHTML = '<span class="back">← programs</span><span class="title">macros</span>' +
      '<span class="dim">macro number → program assignments</span>';
    crumb.querySelector(".back").addEventListener("click", function () { location.hash = "#programs"; });
    view.appendChild(crumb);
    var host = BV.el("div", { class: "view-slot", style: "height:calc(100% - 2.4rem)" });
    view.appendChild(host);
    if (BV.macros && BV.macros.render) BV.macros.render(host, toolbar);
    else host.innerHTML = '<div class="empty-state"><div class="hint">macros unavailable</div></div>';
  }

  /* ------------------------------ list ------------------------------ */

  function renderList(view, toolbar) {
    view.classList.add("no-pad");
    Promise.all([BV.api.call("get_programs"), BV.api.call("get_styles").catch(function () { return []; })])
      .then(function (res) {
        var progs = res[0], styleTable = res[1];
        var showSystem = listState.showSystem;
        var showBinary = listState.showBinary;
        /* the type filter is per-backup (tabState, wiped on backup switch) so
           robot A's "TP only" never silently filters robot B; the rest of
           listState is deliberately sticky across backups */
        var pst = BV.tabState("programs");
        var typeFilter = pst.typeFilter || null;
        /* inverted rank so clicking the ★ header once (ascending) floats
           starred style programs to the top */
        progs.forEach(function (p) {
          p.star_rank = p.styles && p.styles.length ? 0 : 1;
        });
        var typeSet = {};
        progs.forEach(function (p) { if (p.prog_type) typeSet[p.prog_type] = 1; });
        var progTypes = Object.keys(typeSet).sort();
        if (typeFilter && progTypes.indexOf(typeFilter) < 0) typeFilter = pst.typeFilter = null;

        var sb = BV.searchBox({
          placeholder: "filter programs…",
          onChange: function (q) { if (vt) vt.setFilter(q); },
          onCommit: function () { if (vt) vt.moveSelection(1); },
        });
        toolbar.appendChild(sb.el);
        BV.currentSearch = sb;

        if (styleTable.length) {
          var stBtn = BV.el("button", { class: "btn", title: "programs PLC can call by style code" },
            "style table (" + styleTable.length + ")");
          stBtn.addEventListener("click", function () { location.hash = "#programs/styles"; });
          toolbar.appendChild(stBtn);
        }

        /* macros moved here from their own tab - sits next to style table */
        if (BV.state.manifest && BV.state.manifest.tabs && BV.state.manifest.tabs.macros) {
          var mBtn = BV.el("button", { class: "btn", title: "macro number → program assignments" }, "macros");
          mBtn.addEventListener("click", function () { location.hash = "#programs/macros"; });
          toolbar.appendChild(mBtn);
        }

        /* add to the edit workspace. VTable has no multi-select (one integer
           `selected`, reset by every sort/filter), so the honest bulk gesture is
           "everything the current filter shows" - narrow the list, then add it. */
        var wsSrc = BV.workspace.currentSource();
        if (wsSrc) {
          toolbar.appendChild(BV.workspace.button({
            label: "+ workspace",
            title: "add the programs this filter shows to the edit workspace",
            entries: function () {
              var rows = (vt && vt.view) ? vt.view : [];
              return rows.filter(function (r) { return !r.binary && r.rel; })
                .map(function (r) {
                  return { root: wsSrc.root, label: wsSrc.label,
                           file: r.rel, name: r.file };
                });
            },
          }));
        }

        var sysBtn = BV.el("button", { class: "btn", title: "-BCKED*- markers and BACKGRND-owned programs" },
          showSystem ? "hide system" : "show system");
        sysBtn.addEventListener("click", function () {
          showSystem = !showSystem;
          listState.showSystem = showSystem;
          sysBtn.textContent = showSystem ? "hide system" : "show system";
          refill();
        });
        toolbar.appendChild(sysBtn);

        var hasBinary = progs.some(function (p) { return p.binary; });
        if (hasBinary) {
          var binBtn = BV.el("button", { class: "btn" }, showBinary ? "hide binary" : "show binary");
          binBtn.addEventListener("click", function () {
            showBinary = !showBinary;
            listState.showBinary = showBinary;
            binBtn.textContent = showBinary ? "hide binary" : "show binary";
            refill();
          });
          toolbar.appendChild(binBtn);
        }

        /* file-type filter (TP / MACRO / PC / KAREL …) - only when the backup has
           more than one type to choose between */
        if (progTypes.length > 1) {
          var typeSeg = BV.segmented(
            [{ id: "all", label: "all" }].concat(progTypes.map(function (t) {
              return { id: t, label: t.toLowerCase() };
            })),
            { value: typeFilter || "all", onChange: function (id) {
                typeFilter = pst.typeFilter = (id === "all" ? null : id);
                refill();
              } }
          );
          toolbar.appendChild(typeSeg.el);
        }

        /* compare mode: both robots' program lists + pick-one-each diffing */
        var vs = false;
        var progsB = null;
        var diffBtn = null;
        var hlState = null;
        if (BV.state.compare) {
          var vsBtn = BV.el("button", {
            class: "btn",
            title: "show both robots' program lists side by side",
          }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
          vsBtn.addEventListener("click", function () {
            vs = !vs;
            vsBtn.classList.toggle("primary", vs);
            diffBtn.style.display = vs ? "" : "none";
            if (vs && !progsB) {
              BV.api.call("get_programs", null, "b").then(function (pb) {
                progsB = pb;
                progsB.forEach(function (p) { p.star_rank = p.styles && p.styles.length ? 0 : 1; });
                build();
              }).catch(function (e) {
                vs = false;
                vsBtn.classList.remove("primary");
                diffBtn.style.display = "none";
                hlWrap.style.display = "none";   /* var-hoisted; assigned before any click */
                BV.toast(e.message);
              });
            } else build();
          });
          toolbar.appendChild(vsBtn);
          diffBtn = BV.el("button", {
            class: "btn",
            style: "display:none",
            title: "select one program on each side, then diff them line by line",
          }, "diff selected ⇄");
          diffBtn.addEventListener("click", function () {
            if (!vt || !vt.tables || vt.tables.length < 2) return;
            var ra = vt.tables[0].vt.view[vt.tables[0].vt.selected];
            var rb = vt.tables[1].vt.view[vt.tables[1].vt.selected];
            if (!ra || !rb) { BV.toast("select a program on each side first"); return; }
            if (ra.binary || rb.binary) { BV.toast("binary programs have no listing to diff"); return; }
            location.hash = "#pdiff/" + encodeURIComponent(ra.file) + "/" + encodeURIComponent(rb.file);
          });
          toolbar.appendChild(diffBtn);
          var hlWrap = BV.el("div", { style: "display:none;gap:.75rem;align-items:center" });
          hlState = BV.vsDiff.controls(hlWrap, function () { build(); });
          toolbar.appendChild(hlWrap);
          vsBtn.addEventListener("click", function () {
            hlWrap.style.display = vs ? "flex" : "none";
          });
        }

        var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
        view.appendChild(host);

        function filterRows(list) {
          return (list || []).filter(function (p) {
            if (!showSystem && p.system) return false;
            if (!showBinary && p.binary) return false;
            if (typeFilter && p.prog_type !== typeFilter) return false;
            return true;
          });
        }
        function rows() { return filterRows(progs); }

        var COLUMNS = [
          { key: "star_rank", label: "★", width: 46, render: function (r) {
              if (!r.styles || !r.styles.length) return "";
              return '<span class="accent" title="style ' + r.styles.join(", ") + '">★</span>';
            } },
          { key: "name", label: "program", width: 150, accent: true },
          { key: "comment", label: "comment", grow: true, render: function (r) {
              return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
          { key: "prog_type", label: "type", width: 118, dim: true },
          { key: "line_count", label: "lines", width: 70, num: true },
          { key: "prog_size", label: "size", width: 90, num: true, render: function (r) {
              return r.prog_size === null || r.prog_size === undefined ? "" : BV.fmt.bytes(r.prog_size); } },
          { key: "modified", label: "modified", width: 165, dim: true, render: function (r) {
              return BV.esc(BV.fmt.date(r.modified)); } },
        ];

        function build() {
          if (vt) vt.destroy();
          if (vs && progsB) {
            var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
            var nameB = BV.state.compare.robot_name || BV.state.compare.name;
            var keyFn = function (r) { return r.name.toUpperCase(); };
            function paneClass(otherRows) {
              var mark = BV.vsDiff.marker(hlState, otherRows, keyFn, BV.vsDiff.prog);
              return function (r) {
                return ((r.binary ? "dim-row " : "") + mark(r)).trim();
              };
            }
            vt = new BV.MultiTable(host, {
              mode: "pair",
              panes: [
                { label: nameA, columns: COLUMNS, data: filterRows(progs),
                  rowClass: paneClass(filterRows(progsB)) },
                { label: nameB, columns: COLUMNS, data: filterRows(progsB),
                  rowClass: paneClass(filterRows(progs)) },
              ],
              onCount: function (n) { sb.setCount(n); },
              /* clicking selects (for diffing); double-purpose nav would fight it */
              onOpen: function () {},
            });
          } else {
            vt = new BV.VTable(host, {
              columns: COLUMNS,
              data: rows(),
              /* default names A-Z; unified across TP/binary/KAREL (the client
                 sort interleaves them, so toggled rows slot in, not at the
                 bottom). The active sort persists across navigation. */
              sortKey: listState.sortKey,
              sortDir: listState.sortDir,
              onSort: function (k, d) { listState.sortKey = k; listState.sortDir = d; },
              rowClass: function (r) { return r.binary ? "dim-row" : ""; },
              onCount: function (n) { sb.setCount(n, progs.length); },
              onOpen: function (r) {
                if (r.binary) { BV.toast(r.file + " is binary — no listing to show"); return; }
                location.hash = "#programs/" + encodeURIComponent(r.file);
              },
            });
          }
          BV.currentVTable = vt;
          vt.setFilter(sb.value());
        }
        build();

        /* keep the scroll position across navigating into a program and back */
        host.addEventListener("scroll", BV.debounce(function () {
          if (!(vs && progsB)) listState.scrollTop = host.scrollTop;
        }, 80));
        if (!(vs && progsB)) requestAnimationFrame(function () { host.scrollTop = listState.scrollTop; });

        function refill() {
          if (vs && progsB) { build(); return; }
          vt.setData(rows());
          vt.setFilter(sb.value());
        }
      }).catch(function (e) {
        view.classList.remove("no-pad");
        view.innerHTML = '<div class="empty-state"><div class="big">programs unavailable</div>' +
          '<div class="hint">' + BV.esc(e.message) + "</div></div>";
      });
  }

  /* --------------------------- style table --------------------------- */

  function renderStyleTable(view, toolbar) {
    view.classList.add("no-pad");
    BV.api.call("get_styles").then(function (styles) {
      var crumbWrap = BV.el("div", { style: "margin:0 1.25rem" });
      var crumb = BV.el("div", { class: "crumb" });
      crumb.innerHTML = '<span class="back">← programs</span><span class="title">style table</span>' +
        '<span class="dim">programs PLC can call by style code</span>';
      crumb.querySelector(".back").addEventListener("click", function () { location.hash = "#programs"; });
      crumbWrap.appendChild(crumb);
      view.appendChild(crumbWrap);

      var host = BV.el("div", { style: "height:calc(100% - 2.6rem);margin:0 1.25rem 1rem" });
      view.appendChild(host);
      var svt = new BV.VTable(host, {
        columns: [
          { key: "style", label: "style", width: 90, num: true, accent: true },
          { key: "program", label: "program", width: 180 },
          { key: "comment", label: "comment", grow: true, render: function (r) {
              return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
          { key: "enabled", label: "enabled", width: 100, render: function (r) {
              return r.enabled ? '<span class="pill on">yes</span>' : '<span class="pill err">no</span>'; } },
        ],
        data: styles,
        onOpen: function (r) { location.hash = "#programs/" + encodeURIComponent(r.program + ".LS"); },
      });
      BV.currentVTable = svt;
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* ----------------------------- detail ----------------------------- */

  function sigFromToken(text) {
    var m = /^([A-Z]+)\[\s*(\d+)/.exec(text);
    return m ? m[1] + "[" + m[2] + "]" : null;
  }

  /* wrap the first text-node occurrence of `word` under el in a clickable
     .tp-call span (walks text nodes so highlighter spans/attrs are untouched) */
  function wrapToken(el, word, target) {
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      var i = node.nodeValue.indexOf(word);
      if (i < 0) continue;
      var before = node.nodeValue.slice(0, i);
      var after = node.nodeValue.slice(i + word.length);
      var span = BV.el("span", { class: "tp-call" }, BV.esc(word));
      span.dataset.hop = target;
      var parent = node.parentNode;
      if (after) parent.insertBefore(document.createTextNode(after), node.nextSibling);
      parent.insertBefore(span, node.nextSibling);
      node.nodeValue = before;
      return true;
    }
    return false;
  }

  function renderDetail(view, toolbar, file, anchor) {
    view.classList.remove("no-pad");
    BV.api.call("get_program", decodeURIComponent(file)).then(function (p) {
      var crumb = BV.el("div", { class: "crumb" });
      crumb.innerHTML = '<span class="back" id="crumb-hist" title="previous view (backspace)">← back</span>' +
        '<span class="back" id="crumb-list">programs</span>' +
        '<span class="title">' + BV.esc(p.name) + "</span>" +
        (p.prog_type ? '<span class="pill ghost">' + BV.esc(p.prog_type.toLowerCase()) + "</span>" : "") +
        (p.attrs.comment ? '<span class="dim">' + BV.esc(p.attrs.comment) + "</span>" : "");
      crumb.querySelector("#crumb-hist").addEventListener("click", function () {
        history.back();
      });
      crumb.querySelector("#crumb-list").addEventListener("click", function () {
        location.hash = "#programs";
      });
      view.appendChild(crumb);

      var split = BV.el("div", { class: "split", style: "height:calc(100% - 2.4rem)" });
      view.appendChild(split);

      /* left: source with clickable io/register tokens + CALL/macro hops */
      var viewer = BV.el("div", { class: "viewer" });
      var hops = p.hops || {};
      var labels = p.labels || [];
      var pre = null;
      /* label id -> definition line, for click-to-definition on LBL tokens */
      var labelDef = {};
      labels.forEach(function (L) {
        if (L.line !== null && L.line !== undefined) labelDef[L.id] = L.line;
      });

      /* center + flash a source line - the one jump helper every navigator
         (search anchor, labels xref, LBL tokens) shares. Manual scroll math,
         not scrollIntoView: reading rects forces a sync reflow so the hidden
         probe window (no rAF) scrolls + flashes too (same trick as pdiff). */
      function revealLine(n) {
        if (!pre) return;
        var el = pre.querySelector("#srcline-" + n);
        if (!el) return;
        var vr = viewer.getBoundingClientRect();
        var er = el.getBoundingClientRect();
        viewer.scrollTop += (er.top - vr.top) - Math.max(0, (viewer.clientHeight - el.offsetHeight) / 2);
        el.classList.remove("flash");
        void el.offsetWidth; /* restart the animation */
        el.classList.add("flash");
      }

      if (p.body.length) {
        pre = BV.el("pre");
        var html = "";
        p.body.forEach(function (line) {
          html += '<div class="code-line" id="srcline-' + line.n + '"><span class="ln">' + line.n + '</span>' +
            '<span class="lc">' + (line.text.trim() ? BV.highlightTP(line.text) : "") + "</span></div>";
        });
        pre.innerHTML = html;
        /* make CALL/RUN program names and bare macro-name lines clickable */
        p.body.forEach(function (line) {
          var lh = hops[String(line.n)];
          if (!lh) return;
          var lc = pre.querySelector("#srcline-" + line.n + " .lc");
          if (!lc) return;
          lh.forEach(function (h) {
            if (!h.exists) return;
            if (h.name === line.text.trim()) {
              /* whole line is the macro name → make the line clickable */
              lc.classList.add("tp-call");
              lc.dataset.hop = h.target;
            } else {
              wrapToken(lc, h.name, h.target);
            }
          });
        });
        pre.addEventListener("click", function (ev) {
          /* releasing a drag-selection over a token is not a navigation
             click - let text be copied without yanking the view away */
          var sel = window.getSelection ? window.getSelection() : null;
          if (sel && !sel.isCollapsed) return;
          var hop = ev.target.closest(".tp-call");
          if (hop && hop.dataset.hop) {
            location.hash = "#programs/" + encodeURIComponent(hop.dataset.hop + ".LS");
            return;
          }
          var lbl = ev.target.closest(".tp-label");
          if (lbl) {
            var lm = /LBL\[\s*(\d+)/.exec(lbl.textContent);
            if (lm && labelDef[lm[1]] !== undefined) revealLine(labelDef[lm[1]]);
            return;
          }
          var tok = ev.target.closest(".tp-io, .tp-reg");
          if (!tok) return;
          var sig = sigFromToken(tok.textContent);
          if (sig) location.hash = "#search/" + encodeURIComponent(sig);
        });
        viewer.appendChild(pre);
      } else {
        viewer.innerHTML = '<div class="empty-state" style="height:200px"><div class="hint">empty program body</div></div>';
      }
      split.appendChild(viewer);

      /* right: attrs (collapsible), navigator (call tree / calls / labels), positions */
      var side = BV.el("div", { style: "display:flex;flex-direction:column;gap:.9rem" });
      split.appendChild(side);
      var pst = BV.tabState("programs");

      var a = p.attrs;
      var ac = BV.el("div", { class: "card" });
      var kv = [
        ["created", BV.fmt.date(a.create)], ["modified", BV.fmt.date(a.modified)],
        ["lines", a.line_count], ["size", a.prog_size !== undefined ? BV.fmt.bytes(a.prog_size) : ""],
        ["protect", a.protect], ["file", p.name + ".LS"],
      ];
      if (p.appl.length) kv.push(["appl", p.appl.join(", ")]);
      var ah = BV.el("h3", null, "attributes");
      var ab = BV.el("div");
      ab.innerHTML = BV.kv.html(kv);
      ac.appendChild(ah);
      ac.appendChild(ab);
      /* collapsible so a read-once block stops eating side-panel height;
         fold state remembered per backup like everything else */
      BV.collapsible(ac, ah, ab, {
        open: pst.attrsOpen !== false,
        onToggle: function (open) { pst.attrsOpen = open; },
      });
      side.appendChild(ac);

      /* navigator: call tree / calls+called-by / labels are three lenses on
         the same question (where does control go?), so they share one card
         behind a segmented switch instead of stacking boxes */
      var hasCalls = (p.calls && p.calls.length) || (p.called_by && p.called_by.length);
      if (hasCalls || labels.length) {
        var nc = BV.el("div", { class: "card prognav" });
        var navHead = BV.el("div", { class: "prognav-head" });
        var navBody = BV.el("div", { class: "prognav-body scrollbody" });
        var expandBtn = BV.el("button", {
          class: "btn icon-btn", title: "expand to half-screen",
        }, "⤢");
        expandBtn.addEventListener("click", function () {
          var big = nc.classList.toggle("expanded");
          expandBtn.title = big ? "shrink" : "expand to half-screen";
        });

        var panes = {};
        function showSeg(id) {
          pst.navSeg = id;
          navBody.innerHTML = "";
          navBody.appendChild(panes[id] || (panes[id] = buildSeg(id)));
        }
        function buildSeg(id) {
          if (id === "tree") return buildTreePane();
          if (id === "labels") return buildLabelsPane();
          return buildCallsPane();
        }

        var initial = pst.navSeg;
        if (["tree", "calls", "labels"].indexOf(initial) < 0) {
          initial = (p.calls && p.calls.length) ? "tree" : (hasCalls ? "calls" : "labels");
        }
        var seg = BV.segmented([
          { id: "tree", label: "call tree" },
          { id: "calls", label: "calls", count: (p.calls || []).length },
          { id: "labels", label: "labels", count: labels.length },
        ], { value: initial, onChange: showSeg });
        navHead.appendChild(seg.el);
        navHead.appendChild(expandBtn);
        nc.appendChild(navHead);
        nc.appendChild(navBody);
        side.appendChild(nc);
        showSeg(initial);
      }

      function buildTreePane() {
        var holder = BV.el("div", { class: "calltree" });
        if (!(p.calls && p.calls.length)) {
          holder.innerHTML = '<div class="dim" style="font-size:.8rem">calls nothing — no tree to walk</div>';
          return holder;
        }
        holder.innerHTML = '<div class="dim" style="font-size:.8rem">loading…</div>';
        BV.api.call("get_call_tree", p.name).then(function (tree) {
          holder.innerHTML = "";
          holder.appendChild(renderTree(tree, true));
        }).catch(function () {
          holder.innerHTML = '<div class="dim">call tree unavailable</div>';
        });
        return holder;
      }

      function buildCallsPane() {
        var box = BV.el("div");
        var ch = "<h3>calls</h3>";
        if (p.calls.length) {
          ch += '<div style="display:flex;flex-direction:column;gap:.15rem;margin-bottom:.6rem">';
          p.calls.forEach(function (e) {
            ch += '<div style="display:flex;gap:.5rem;align-items:center;font-size:.85rem">' +
              '<span class="pill ghost" style="min-width:3.2em;text-align:center">' + BV.esc(e.kind) + "</span>" +
              (e.exists
                ? '<a href="#programs/' + encodeURIComponent(e.target + ".LS") + '">' + BV.esc(e.target) + "</a>"
                : '<span class="dim" title="not in this backup">' + BV.esc(e.target) + "</span>") +
              (e.count > 1 ? '<span class="dim">×' + e.count + "</span>" : "") +
              '<span class="dim" style="margin-left:auto">line ' + e.first_line + "</span></div>";
          });
          ch += "</div>";
        } else {
          ch += '<div class="dim" style="font-size:.8rem;margin-bottom:.6rem">calls nothing</div>';
        }
        ch += "<h3>called by</h3>";
        if (p.called_by.length) {
          ch += '<div style="display:flex;flex-wrap:wrap;gap:.3rem">';
          p.called_by.forEach(function (e) {
            ch += '<a class="pill ghost" href="#programs/' + encodeURIComponent(e.caller + ".LS") + '">' +
              BV.esc(e.caller) + "</a>";
          });
          ch += "</div>";
        } else {
          ch += '<div class="dim" style="font-size:.8rem">nothing calls this (entry point?)</div>';
        }
        box.innerHTML = ch;
        return box;
      }

      /* labels xref: every LBL definition with the lines that JMP to it -
         the same caller/callee idea as the calls pane, scoped to one program */
      function buildLabelsPane() {
        var box = BV.el("div", { class: "lblxref" });
        if (!labels.length) {
          box.innerHTML = '<div class="dim" style="font-size:.8rem">no labels in this program</div>';
          return box;
        }
        var textByLine = {};
        p.body.forEach(function (l) { textByLine[l.n] = l.text; });
        labels.forEach(function (L) {
          var broken = L.line === null || L.line === undefined;
          var def = BV.el("div", {
            class: "lblx-def" + (broken ? " lblx-broken" : ""),
            title: broken ? "JMP target that is never defined — broken jump" : "go to line " + L.line,
          });
          def.innerHTML = '<span class="lblx-id">LBL[' + L.id + "]</span>" +
            (L.name ? '<span class="lblx-name">' + BV.esc(L.name) + "</span>" : "") +
            (broken ? '<span class="lblx-miss">no such label</span>'
                    : '<span class="lblx-line">line ' + L.line + "</span>");
          if (!broken) def.addEventListener("click", function () { revealLine(L.line); });
          box.appendChild(def);
          if (L.jumps.length) {
            L.jumps.forEach(function (jn) {
              var j = BV.el("div", { class: "lblx-jmp", title: "go to line " + jn });
              j.innerHTML = '<span class="lblx-jline">' + jn + "</span>" +
                '<span class="lblx-jtext">' + BV.highlightTP((textByLine[jn] || "").trim()) + "</span>";
              j.addEventListener("click", function () { revealLine(jn); });
              box.appendChild(j);
            });
          } else {
            box.appendChild(BV.el("div", { class: "lblx-none dim" }, "no jumps to this label"));
          }
        });
        return box;
      }

      if (p.positions.length) {
        var pc = BV.el("div", { class: "card" });
        pc.innerHTML = '<h3>positions <span class="count">' + p.positions.length + "</span></h3>";
        /* one row per group; the P[] cell shows only on a position's first group */
        var posRows = [];
        p.positions.forEach(function (pos) {
          pos.groups.forEach(function (g, gi) { posRows.push({ pos: pos, g: g, gi: gi }); });
        });
        function posVal(g) {
          if (g.masked) return '<span class="dim">masked ********</span>';
          if (g.kind === "joint") {
            return '<span class="dim">J</span> ' + g.joints.map(function (j) {
              return j === null ? "—" : BV.fmt.num(j, 2);
            }).join(", ");
          }
          if (g.kind === "cartesian") {
            return ["x", "y", "z", "w", "p", "r"].map(function (ax) {
              return '<span class="dim">' + ax + "</span>" + BV.fmt.num(g[ax], 1);
            }).join(" ");
          }
          return '<span class="dim">—</span>';
        }
        pc.appendChild(BV.table([
          { key: "p", label: "p", num: true, render: function (r) {
              return r.gi !== 0 ? "" : "P[" + r.pos.id + "]" +
                (r.pos.comment ? ' <span class="dim">' + BV.esc(r.pos.comment) + "</span>" : "");
            } },
          { key: "gp", label: "gp", num: true, dim: true, render: function (r) { return r.g.gp; } },
          { key: "uft", label: "uf/ut", dim: true, render: function (r) {
              return BV.esc((r.g.uf || "") + "/" + (r.g.ut || "")); } },
          { key: "value", label: "value", render: function (r) { return posVal(r.g); } },
        ], posRows, { maxHeight: "380px" }));
        side.appendChild(pc);
      }

      /* editing lives in the multi-robot workspace (#edit), not here - one
         editing surface, no drift. This adds the program and jumps there. */
      var src = BV.workspace.currentSource();
      if (src) {
        toolbar.appendChild(BV.workspace.button({
          label: "edit", jump: true,
          title: "open this program in the edit workspace",
          entries: function () {
            return [{ root: src.root, label: src.label,
                      file: p.rel || (p.name + ".LS"), name: p.name + ".LS" }];
          },
        }));
      }

      /* line anchor from search results: #programs/FILE.LS/L26 */
      if (anchor && /^L\d+$/.test(anchor)) revealLine(anchor.slice(1));
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">program unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* KAREL (.PC) program: show its variables (from the .VA twin) as a
     collapsible tree instead of TP source */
  function renderKarelDetail(view, toolbar, file) {
    view.classList.remove("no-pad");
    var stem = decodeURIComponent(file).replace(/\.PC$/i, "");
    BV.api.call("get_program_variables", stem).then(function (p) {
      var crumb = BV.el("div", { class: "crumb" });
      crumb.innerHTML = '<span class="back" id="crumb-hist" title="previous view (backspace)">← back</span>' +
        '<span class="back" id="crumb-list">programs</span>' +
        '<span class="title">' + BV.esc(p.name) + "</span>" +
        '<span class="pill ghost">pc · karel variables</span>';
      crumb.querySelector("#crumb-hist").addEventListener("click", function () { history.back(); });
      crumb.querySelector("#crumb-list").addEventListener("click", function () { location.hash = "#programs"; });
      view.appendChild(crumb);

      var sb = BV.searchBox({
        placeholder: "filter variables…",
        onChange: function (q) { applyFilter(q); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      var list = BV.el("div", { class: "sysvar-list", style: "height:calc(100% - 2.4rem);overflow:auto" });
      view.appendChild(list);
      var nodes = (p.records || []).map(function (rec) {
        var el = BV.sysvars.treeNode(rec, "sysvar-node");  /* top-level: match the system-vars look */
        return { el: el, name: (rec.name || "").toLowerCase() };
      });
      nodes.forEach(function (n) { list.appendChild(n.el); });
      if (!nodes.length) list.innerHTML = '<div class="dim" style="padding:.5rem">no variables</div>';
      /* a single top-level record (e.g. DCD_TEXT -> DCD_USER) auto-expands so
         the [1..n] list shows immediately - the slow-to-navigate complaint */
      else if (nodes.length === 1) BV.setOpen(nodes[0].el, true);

      function applyFilter(q) {
        q = (q || "").toLowerCase();
        var shown = 0;
        nodes.forEach(function (n) {
          var hit = !q || n.name.indexOf(q) >= 0;
          n.el.style.display = hit ? "" : "none";
          if (hit) shown++;
        });
        sb.setCount(shown, nodes.length);
      }
      sb.setCount(nodes.length, nodes.length);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">variables unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* indented expandable call tree (right-click a caret = expand/collapse all) */
  function renderTree(node, isRoot) {
    var el = BV.el("div", { class: "ct-node" + (isRoot ? " ct-root" : "") });
    var row = BV.el("div", { class: "ct-row" });
    var kids = node.children || [];
    var toggle = BV.el("span", { class: "ct-toggle bv-caret" }, kids.length ? "▾" : "·");
    row.appendChild(toggle);
    var name = BV.el("span", {
      class: "ct-name" + (node.exists === false && !isRoot ? " missing" : ""),
      title: node.exists === false ? "not in this backup" : "open " + node.name,
    }, BV.esc(node.name));
    if (node.exists !== false || isRoot) {
      name.addEventListener("click", function () {
        location.hash = "#programs/" + encodeURIComponent(node.name + ".LS");
      });
    }
    row.appendChild(name);
    if (node.kind) row.appendChild(BV.el("span", { class: "ct-kind" }, BV.esc(node.kind) + (node.count > 1 ? " ×" + node.count : "")));
    if (node.cycle) row.appendChild(BV.el("span", { class: "ct-cycle" }, "↻ recursion"));
    if (node.truncated) row.appendChild(BV.el("span", { class: "ct-kind" }, "…"));
    el.appendChild(row);

    if (kids.length) {
      var box = BV.el("div", { class: "bv-collapse-body" });
      kids.forEach(function (k) { box.appendChild(renderTree(k, false)); });
      el.appendChild(box);
      el.classList.add("bv-collapsible", "open");
      toggle.addEventListener("click", function () {
        BV.setOpen(el, !el.classList.contains("open"));
      });
      toggle.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        BV.subtreeToggle(el);
      });
    }
    return el;
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "programs", label: "programs", render: render });
})();
