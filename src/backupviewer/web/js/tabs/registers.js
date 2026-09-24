/* tabs/registers.js - R / PR / SR sub-tabs, list split into side-by-side
   columns on wide screens to halve scrolling. R/SR rows click through to the
   backup-wide search; a PR row expands into its position card (every axis at
   full precision, extended axes included, ‹ › across its groups) and offers
   the search from there. */
(function () {
  "use strict";

  var mt = null;

  /* POSREG.VA lists one entry per [group, index] line and the parser keeps
     that shape (files are law). The table shows one row per REGISTER with its
     groups folded in: a multi-group robot writes PR[7] for all its groups at
     once, so the groups are pages of the expanded card, never a column. */
  function foldPos(regs) {
    var byIndex = {}, rows = [];
    regs.forEach(function (r) {
      var row = byIndex[r.index];
      if (!row) {
        row = byIndex[r.index] = { index: r.index, comment: "", groups: [] };
        rows.push(row);
      }
      row.groups.push(r);
      if (!row.comment && r.comment) row.comment = r.comment;
    });
    return rows;
  }

  /* R: non-zero or named; SR: non-empty or named; PR: any group taught or
     named - the same rule on both robots of a vs page */
  function keepRow(kind, r) {
    if (r.comment) return true;
    if (kind === "num") return r.value !== 0 && r.value !== null;
    if (kind === "str") return !!r.value;
    return BV.pos.taught(r.groups);
  }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    view.classList.add("no-pad");

    var kinds = [
      { id: "num", label: "r (numeric)" },
      { id: "pos", label: "pr (position)" },
      { id: "str", label: "sr (string)" },
    ];
    /* the hash picks the kind when present (#registers/pos); otherwise return
       to the kind you were on last (per-backup, via BV.tabState) */
    var ts = BV.tabState("registers");
    var cur = params && params[0] && kinds.some(function (k) { return k.id === params[0]; })
      ? params[0]
      : (kinds.some(function (k) { return k.id === ts.kind; }) ? ts.kind : "num");
    ts.kind = cur;
    var jumpIndex = params && params[1] === "jump" ? parseInt(params[2] || "0", 10) : null;

    var seg = BV.segmented(kinds, {
      value: cur,
      controlled: true,   /* hash-routed: navigation re-renders with the new kind active */
      onChange: function (id) { location.hash = "#registers/" + id; },
    });
    toolbar.appendChild(seg.el);

    var hideEmpty = ts.hideEmpty !== false;
    var ht = BV.el("button", { class: "btn" }, hideEmpty ? "show empty" : "hide empty");
    ht.addEventListener("click", function () {
      hideEmpty = !hideEmpty;
      ts.hideEmpty = hideEmpty;
      ht.textContent = hideEmpty ? "show empty" : "hide empty";
      load();
    });
    toolbar.appendChild(ht);

    var vs = false;
    var hlState = null;

    var sb = BV.searchBox({
      placeholder: "filter registers…",
      onChange: function (q) { if (mt) mt.setFilter(q); },
      onCommit: function () { if (mt) mt.moveSelection(1); },
    });
    toolbar.appendChild(sb.el);
    BV.currentSearch = sb;

    /* compare mode: this register table for both robots, side by side
       (vs controls live at the END of the toolbar on every tab) */
    if (BV.state.compare) {
      var vsBtn = BV.el("button", {
        class: "btn",
        title: "show these registers for both robots side by side",
      }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
      var hlWrap = BV.el("div", { style: "display:none;gap:.75rem;align-items:center" });
      vsBtn.addEventListener("click", function () {
        vs = !vs;
        vsBtn.classList.toggle("primary", vs);
        hlWrap.style.display = vs ? "flex" : "none";
        load();
      });
      toolbar.appendChild(vsBtn);
      hlState = BV.vsDiff.controls(hlWrap, function () { load(); });
      toolbar.appendChild(hlWrap);
    }

    var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
    view.appendChild(host);

    function regName(r) {
      var prefix = cur === "num" ? "R" : (cur === "pos" ? "PR" : "SR");
      return prefix + "[" + r.index + "]";
    }

    /* the PR open set and each card's group page live in the tab's per-backup
       memory, so the list comes back exactly as you left it */
    var posExp = ts.posExpanded || (ts.posExpanded = {});
    var posPage = ts.posPage || (ts.posPage = {});
    function posDetail(r) {
      return BV.pos.card(r.groups, {
        page: posPage[r.index] || 0,
        onPage: function (i) { posPage[r.index] = i; },
        actions: [{
          label: "find uses",
          title: "search this backup for " + regName(r),
          onClick: function () {
            /* on a vs page the right pane (active===1) is the compare robot;
               in the plain split view both panes are this robot */
            var side = (vs && mt && mt.active === 1) ? "/b" : "";
            location.hash = "#search/" + encodeURIComponent(regName(r)) + side;
          },
        }],
      });
    }

    function load() {
      BV.api.call("get_registers", cur).then(function (regs) {
        var rows = cur === "pos" ? foldPos(regs) : regs;
        if (hideEmpty && jumpIndex === null) {
          rows = rows.filter(function (r) { return keepRow(cur, r); });
        }
        var columns;
        if (cur === "pos") {
          columns = [
            { key: "index", label: "#", width: 95, num: true, accent: true, render: function (r) {
                return regName(r); } },
            { key: "comment", label: "name", width: 180, render: function (r) {
                return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
            { key: "_pos", label: "value", grow: true, sortable: false, render: function (r) {
                return BV.pos.summary(r.groups); } },
          ];
        } else {
          columns = [
            { key: "index", label: "#", width: 95, num: true, accent: true, render: function (r) {
                return regName(r); } },
            { key: "value", label: "value", width: 150, num: cur === "num",
              render: function (r) {
                if (r.value === null || r.value === undefined || r.value === "") return '<span class="dim">—</span>';
                return BV.esc(r.value);
              } },
            { key: "comment", label: "comment", grow: true, render: function (r) {
                return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
          ];
        }
        function show(panesOrData) {
          if (mt) mt.destroy();
          panesOrData.stateKey = (vs ? "registers.vs." : "registers.") + cur;
          if (cur === "pos") {
            panesOrData.rowKey = function (r) { return r.index; };
            panesOrData.detail = posDetail;
            panesOrData.expanded = posExp;
          }
          mt = new BV.MultiTable(host, panesOrData);
          BV.currentVTable = mt;
          mt.setFilter(sb.value());
          if (jumpIndex !== null) {
            /* a jump from search lands on the register AND opens it */
            if (cur === "pos") posExp[jumpIndex] = true;
            mt.selectWhere(function (r) { return r.index === jumpIndex; });
            jumpIndex = null;
          }
        }

        if (vs) {
          BV.api.call("get_registers", cur, null, "b").then(function (rb) {
            var rowsB = cur === "pos" ? foldPos(rb) : rb;
            if (hideEmpty) {
              rowsB = rowsB.filter(function (r) { return keepRow(cur, r); });
            }
            var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
            var nameB = BV.state.compare.robot_name || BV.state.compare.name;
            var keyFn = function (r) { return r.index; };
            show({
              mode: "pair",
              panes: [
                { label: nameA, columns: columns, data: rows,
                  rowClass: BV.vsDiff.marker(hlState, rowsB, keyFn, BV.vsDiff.reg) },
                { label: nameB, columns: columns, data: rowsB,
                  rowClass: BV.vsDiff.marker(hlState, rows, keyFn, BV.vsDiff.reg) },
              ],
              onOpen: function (r) {
                /* right pane (active===1) is the compare robot */
                var side = (mt && mt.active === 1) ? "/b" : "";
                location.hash = "#search/" + encodeURIComponent(regName(r)) + side;
              },
              onCount: function (n, all) { sb.setCount(n, all); },
            });
          }).catch(function (e) {
            BV.toast(e.message);
            vs = false;
            load();
          });
          return;
        }

        show({
          mode: "split",
          columns: columns,
          data: rows,
          onOpen: function (r) {
            location.hash = "#search/" + encodeURIComponent(regName(r));
          },
          onCount: function (n, all) { sb.setCount(n, all); },
        });
      }).catch(function (e) {
        host.innerHTML = '<div class="empty-state"><div class="big">' + BV.esc(cur) +
          ' registers unavailable</div><div class="hint">' + BV.esc(e.message) + "</div></div>";
      });
    }
    load();
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "registers", label: "registers", render: render });
})();
