/* tabs/registers.js - R / PR / SR sub-tabs, list split into side-by-side
   columns on wide screens to halve scrolling. Click -> backup-wide search. */
(function () {
  "use strict";

  var mt = null;

  function posSummary(r) {
    if (r.kind === "joint") {
      return '<span class="dim">J:</span> ' + r.joints.map(function (j) {
        return BV.fmt.num(j, 2);
      }).join(", ");
    }
    if (r.kind === "cartesian") {
      return ["x", "y", "z", "w", "p", "r"].map(function (ax) {
        return '<span class="dim">' + ax + "</span> " + BV.fmt.num(r[ax], 1);
      }).join("  ");
    }
    return '<span class="dim">uninitialized</span>';
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
    var cur = params && params[0] && kinds.some(function (k) { return k.id === params[0]; })
      ? params[0] : "num";
    var jumpIndex = params && params[1] === "jump" ? parseInt(params[2] || "0", 10) : null;

    var seg = BV.segmented(kinds, {
      value: cur,
      controlled: true,   /* hash-routed: navigation re-renders with the new kind active */
      onChange: function (id) { location.hash = "#registers/" + id; },
    });
    toolbar.appendChild(seg.el);

    var hideEmpty = true;
    var ht = BV.el("button", { class: "btn" }, "show empty");
    ht.addEventListener("click", function () {
      hideEmpty = !hideEmpty;
      ht.textContent = hideEmpty ? "show empty" : "hide empty";
      load();
    });
    toolbar.appendChild(ht);

    var vs = false;
    var regsB = null;
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
        regsB = null; /* refetch per kind */
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

    function load() {
      BV.api.call("get_registers", cur).then(function (regs) {
        var rows = regs;
        if (hideEmpty && jumpIndex === null) {
          rows = regs.filter(function (r) {
            if (r.comment) return true;
            if (cur === "num") return r.value !== 0 && r.value !== null;
            if (cur === "str") return !!r.value;
            return r.kind !== "uninit";
          });
        }
        var columns;
        if (cur === "pos") {
          columns = [
            { key: "group", label: "grp", width: 55, num: true, dim: true },
            { key: "index", label: "#", width: 95, num: true, accent: true, render: function (r) {
                return regName(r); } },
            { key: "comment", label: "name", width: 180, render: function (r) {
                return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
            { key: "_pos", label: "value", grow: true, sortable: false, render: posSummary },
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
          mt = new BV.MultiTable(host, panesOrData);
          BV.currentVTable = mt;
          mt.setFilter(sb.value());
          if (jumpIndex !== null) {
            mt.selectWhere(function (r) { return r.index === jumpIndex; });
            jumpIndex = null;
          }
        }

        if (vs) {
          BV.api.call("get_registers", cur, "b").then(function (rb) {
            var rowsB = rb;
            if (hideEmpty) {
              rowsB = rb.filter(function (r) {
                if (r.comment) return true;
                if (cur === "num") return r.value !== 0 && r.value !== null;
                if (cur === "str") return !!r.value;
                return r.kind !== "uninit";
              });
            }
            var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
            var nameB = BV.state.compare.robot_name || BV.state.compare.name;
            var keyFn = cur === "pos"
              ? function (r) { return r.group + ":" + r.index; }
              : function (r) { return r.index; };
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
