/* tabs/io.js - signals organized like the pendant: category sub-tabs
   (digital/group/uop/...), IN and OUT side by side (toggleable), a rack/slot/
   port assignments jump-list, and click-any-signal -> backup-wide search. */
(function () {
  "use strict";

  var CATS = [
    { id: "digital", label: "digital", inT: "DI", outT: "DO" },
    { id: "group", label: "group", inT: "GI", outT: "GO" },
    { id: "analog", label: "analog", inT: "AI", outT: "AO" },
    { id: "uop", label: "uop", inT: "UI", outT: "UO" },
    { id: "sop", label: "sop", inT: "SI", outT: "SO" },
    { id: "robot", label: "robot", inT: "RI", outT: "RO" },
    { id: "weld", label: "weld", inT: "WI", outT: "WO" },
    { id: "flags", label: "flags", single: "F" },
  ];

  function statePill(row) {
    var s = row.state;
    if (s === "" || s === null || s === undefined) return "";
    if (s === "ON") return BV.pill("on", "on");
    if (s === "OFF") return BV.pill("off", "off");
    return BV.pill(s, "ghost");
  }

  function rsp(row) {
    if (row.rack === null || row.rack === undefined) return '<span class="dim">—</span>';
    return '<span class="dim">' + row.rack + " · " + row.slot + " · " + row.port +
      (row.num_bits ? " · " + row.num_bits + "b" : "") + "</span>";
  }

  function signalColumns(typed) {
    var cols = [];
    if (typed) cols.push({ key: "type", label: "type", width: 60, dim: true });
    return cols.concat([
      { key: "index", label: "#", width: 96, num: true, render: function (r) {
          return '<span class="accent">' + BV.esc(r.type) + "[" + r.index + "]</span>";
        } },
      { key: "state", label: "state", width: 70, render: statePill },
      { key: "comment", label: "comment", grow: true, render: function (r) {
          return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>';
        } },
      { key: "rack", label: "r · s · p", width: 130, render: rsp, sortable: false },
    ]);
  }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    view.classList.add("no-pad");

    BV.api.call("get_io").then(function (io) {
      var byType = {};
      io.signals.forEach(function (s) { (byType[s.type] = byType[s.type] || []).push(s); });
      var cats = CATS.filter(function (c) {
        return c.single ? byType[c.single] : (byType[c.inT] || byType[c.outT]);
      });
      if (!cats.length) {
        view.classList.remove("no-pad");
        view.innerHTML = '<div class="empty-state"><div class="big">no signals found</div></div>';
        return;
      }

      if (io.source === "summary") {
        toolbar.appendChild(BV.el("span", {
          id: "io-src-note",
          class: "dim",
          style: "font-size:.72rem;margin-left:auto",
          title: "this backup has no IOCONFIG.DG/IOSTATE.DG; signals come from the SUMMARY.DG snapshot",
        }, "from SUMMARY.DG"));
      }

      var state = { cat: cats[0], showIn: true, showOut: true, asg: false, vs: false };
      var mt = null;
      var byTypeB = null; /* comparison robot's signals, fetched on first vs toggle */
      var hlState = null; /* highlight-differences state (vs mode only) */

      /* ---- toolbar ---- */
      /* category carries over into the configuration view; ids kept as
         #iocat-<id> so deep-links/tests can target a category button */
      var catSeg = BV.segmented(
        cats.map(function (c) {
          var n = (byType[c.inT] || []).length + (byType[c.outT] || []).length +
            (byType[c.single] || []).length;
          return { id: c.id, label: c.label, count: n };
        }),
        {
          value: state.cat.id,
          controlled: true,
          idPrefix: "iocat-",
          onChange: function (id) {
            state.cat = cats.find(function (c) { return c.id === id; });
            syncToolbar(); draw();
          },
        }
      );
      toolbar.appendChild(catSeg.el);

      /* in/out is a multi-toggle: the "keep at least one on / vs forces a
         single side" rule stays here; the control just reports the click */
      var dirSeg = BV.segmented(
        [{ id: "in", label: "in" }, { id: "out", label: "out" }],
        {
          multi: true,
          onChange: function (id) {
            if (id === "in") {
              if (state.vs) { state.showIn = true; state.showOut = false; }
              else {
                state.showIn = !state.showIn;
                if (!state.showIn && !state.showOut) state.showOut = true;
              }
            } else {
              if (state.vs) { state.showOut = true; state.showIn = false; }
              else {
                state.showOut = !state.showOut;
                if (!state.showIn && !state.showOut) state.showIn = true;
              }
            }
            syncToolbar(); draw();
          },
        }
      );
      toolbar.appendChild(dirSeg.el);

      var sb = BV.searchBox({
        placeholder: "filter signals…",
        onChange: function (q) { if (mt) mt.setFilter(q); },
        onCommit: function () { if (mt) mt.moveSelection(1); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      var asgBtn = BV.el("button", { class: "btn", title: "rack / slot / port configuration (pendant: I/O config) - click a row to jump to its signals" }, "configuration");
      asgBtn.addEventListener("click", function () {
        state.asg = !state.asg;
        if (state.asg) state.vs = false;
        syncToolbar(); draw();
      });
      toolbar.appendChild(asgBtn);

      /* compare mode: same signal table for both robots, side by side */
      var vsBtn = null;
      if (BV.state.compare) {
        vsBtn = BV.el("button", {
          class: "btn",
          title: "show this signal table for both robots side by side",
        }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
        vsBtn.addEventListener("click", function () {
          state.vs = !state.vs;
          if (state.vs) {
            state.asg = false;
            if (state.showIn && state.showOut) state.showOut = false; /* one direction at a time */
          }
          if (state.vs && !byTypeB) {
            BV.api.call("get_io", "b").then(function (iob) {
              byTypeB = {};
              iob.signals.forEach(function (s) { (byTypeB[s.type] = byTypeB[s.type] || []).push(s); });
              syncToolbar(); draw();
            }).catch(function (e) {
              state.vs = false;
              BV.toast(e.message);
              syncToolbar();
            });
          } else {
            syncToolbar(); draw();
          }
        });
        toolbar.appendChild(vsBtn);
        var hlWrap = BV.el("div", { style: "display:none;gap:.75rem;align-items:center" });
        hlState = BV.vsDiff.controls(hlWrap, function () { draw(); });
        toolbar.appendChild(hlWrap);
        state._hlWrap = hlWrap;
      }

      function syncToolbar() {
        catSeg.setActive(state.cat.id);
        var single = !!state.cat.single;
        dirSeg.el.style.display = (single || state.asg) ? "none" : "";
        dirSeg.setActive([state.showIn ? "in" : null, state.showOut ? "out" : null].filter(Boolean));
        asgBtn.classList.toggle("primary", state.asg);
        if (vsBtn) vsBtn.classList.toggle("primary", state.vs);
        if (state._hlWrap) state._hlWrap.style.display = state.vs ? "flex" : "none";
      }

      var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
      view.appendChild(host);

      function openSearch(row) {
        /* in vs mode the right pane (active===1) is the compare robot - search
           THAT robot, not the primary one */
        var side = (state.vs && mt && mt.active === 1) ? "/b" : "";
        location.hash = "#search/" + encodeURIComponent(row.type + "[" + row.index + "]") + side;
      }

      function draw() {
        if (mt) { mt.destroy(); mt = null; }
        host.innerHTML = "";
        if (state.asg) { drawConfiguration(); return; }

        var c = state.cat;
        if (state.vs && byTypeB) {
          var tv = c.single || (state.showIn ? c.inT : c.outT) || c.inT;
          var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
          var nameB = BV.state.compare.robot_name || BV.state.compare.name;
          var keyFn = function (r) { return r.type + "[" + r.index + "]"; };
          mt = new BV.MultiTable(host, {
            mode: "pair",
            panes: [
              { label: nameA + " · " + tv, columns: signalColumns(false), data: byType[tv] || [],
                rowClass: BV.vsDiff.marker(hlState, byTypeB[tv] || [], keyFn, BV.vsDiff.io) },
              { label: nameB + " · " + tv, columns: signalColumns(false), data: byTypeB[tv] || [],
                rowClass: BV.vsDiff.marker(hlState, byType[tv] || [], keyFn, BV.vsDiff.io) },
            ],
            onOpen: openSearch,
            onCount: function (n, all) { sb.setCount(n, all); },
          });
          mt.setFilter(sb.value());
          BV.currentVTable = mt;
          return;
        }
        if (c.single || !(state.showIn && state.showOut)) {
          var t = c.single || (state.showIn ? c.inT : c.outT);
          mt = new BV.MultiTable(host, {
            mode: "split",
            columns: signalColumns(false),
            data: byType[t] || [],
            onOpen: openSearch,
            onCount: function (n, all) { sb.setCount(n, all); },
          });
        } else {
          mt = new BV.MultiTable(host, {
            mode: "pair",
            panes: [
              { label: (c.inT || "") + " · inputs", columns: signalColumns(false), data: byType[c.inT] || [] },
              { label: (c.outT || "") + " · outputs", columns: signalColumns(false), data: byType[c.outT] || [] },
            ],
            onOpen: openSearch,
            onCount: function (n, all) { sb.setCount(n, all); },
          });
        }
        mt.setFilter(sb.value());
        BV.currentVTable = mt;
      }

      /* pendant's I/O config screen: rack/slot/port ranges for the CURRENT
         category, inputs and outputs cleanly separated. Click a row to jump
         to that signal in the signal view. */
      function drawConfiguration() {
        var cfgCols = [
          { key: "start", label: "signals", width: 178, accent: true, render: function (r) {
              return r.start === r.end ? r.type + "[" + r.start + "]"
                : r.type + "[" + r.start + " – " + r.end + "]";
            } },
          { key: "rack", label: "rack", width: 75, num: true },
          { key: "slot", label: "slot", width: 75, num: true },
          { key: "port", label: "port", width: 75, num: true },
          { key: "num_bits", label: "bits", width: 65, num: true, dim: true },
        ];
        function rowsFor(t) {
          return io.assignments.filter(function (a) { return a.type === t; });
        }
        var c = state.cat;
        var open = function (r) { jumpTo(r.type, r.start); };
        if (c.single) {
          mt = new BV.MultiTable(host, {
            mode: "split", columns: cfgCols, data: rowsFor(c.single),
            onOpen: open,
            onCount: function (n, all) { sb.setCount(n, all); },
          });
        } else {
          mt = new BV.MultiTable(host, {
            mode: "pair",
            panes: [
              { label: (c.inT || "") + " · inputs", columns: cfgCols, data: rowsFor(c.inT) },
              { label: (c.outT || "") + " · outputs", columns: cfgCols, data: rowsFor(c.outT) },
            ],
            onOpen: open,
            onCount: function (n, all) { sb.setCount(n, all); },
          });
        }
        mt.setFilter(sb.value());
        BV.currentVTable = mt;
      }

      function jumpTo(type, index) {
        var cat = cats.find(function (c) {
          return c.single === type || c.inT === type || c.outT === type;
        });
        if (!cat) return;
        state.cat = cat;
        state.asg = false;
        if (cat.inT === type) state.showIn = true;
        if (cat.outT === type) state.showOut = true;
        sb.input.value = "";
        syncToolbar();
        draw();
        if (mt) {
          mt.selectWhere(function (r) { return r.type === type && r.index === index; });
        }
      }

      syncToolbar();
      draw();

      /* deep-link: #io/jump/DI/279 (from search results) */
      if (params && params[0] === "jump" && params[1]) {
        jumpTo(decodeURIComponent(params[1]).toUpperCase(), parseInt(params[2] || "1", 10));
      }
    }).catch(function (e) {
      view.classList.remove("no-pad");
      view.innerHTML = '<div class="empty-state"><div class="big">io unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "io", label: "io", render: render });
})();
