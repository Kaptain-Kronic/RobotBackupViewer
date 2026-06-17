/* tabs/macros.js - macro table (name, program, assignment); vs mode shows
   both robots side by side with optional difference highlighting. */
(function () {
  "use strict";

  var vt = null;

  var COLUMNS = [
    { key: "index", label: "#", width: 70, num: true, dim: true },
    { key: "name", label: "macro", width: 240, accent: true },
    { key: "prog_name", label: "program", width: 160 },
    { key: "assign_type", label: "assign", width: 110, render: function (r) {
        if (!r.assign_type) return '<span class="dim">—</span>';
        return BV.pill(r.assign_type + (r.assign_id ? "[" + r.assign_id + "]" : ""), "ghost");
      } },
    { key: "system", label: "", grow: true, render: function (r) {
        return r.system ? '<span class="dim">system macro</span>' : "";
      } },
  ];

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    view.classList.add("no-pad");

    BV.api.call("get_macros").then(function (macros) {
      var vs = false;
      var macrosB = null;
      var hlState = null;

      var sb = BV.searchBox({
        placeholder: "filter macros…",
        onChange: function (q) { if (vt) vt.setFilter(q); },
        onCommit: function () { if (vt) vt.moveSelection(1); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      /* vs controls live at the END of the toolbar on every tab */
      if (BV.state.compare) {
        var vsBtn = BV.el("button", {
          class: "btn",
          title: "show both robots' macro tables side by side",
        }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
        var hlWrap = BV.el("div", { style: "display:none;gap:.75rem;align-items:center" });
        vsBtn.addEventListener("click", function () {
          vs = !vs;
          vsBtn.classList.toggle("primary", vs);
          hlWrap.style.display = vs ? "flex" : "none";
          if (vs && !macrosB) {
            BV.api.call("get_macros", "b").then(function (mb) {
              macrosB = mb;
              build();
            }).catch(function (e) {
              vs = false;
              vsBtn.classList.remove("primary");
              hlWrap.style.display = "none";
              BV.toast(e.message);
            });
          } else build();
        });
        toolbar.appendChild(vsBtn);
        hlState = BV.vsDiff.controls(hlWrap, function () { build(); });
        toolbar.appendChild(hlWrap);
      }

      var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
      view.appendChild(host);

      function build() {
        if (vt) vt.destroy();
        if (vs && macrosB) {
          var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
          var nameB = BV.state.compare.robot_name || BV.state.compare.name;
          var keyFn = function (r) { return r.name; };
          vt = new BV.MultiTable(host, {
            mode: "pair",
            panes: [
              { label: nameA, columns: COLUMNS, data: macros,
                rowClass: BV.vsDiff.marker(hlState, macrosB, keyFn, BV.vsDiff.macro) },
              { label: nameB, columns: COLUMNS, data: macrosB,
                rowClass: BV.vsDiff.marker(hlState, macros, keyFn, BV.vsDiff.macro) },
            ],
            onCount: function (n) { sb.setCount(n); },
            onOpen: function (r) {
              if (r.prog_name) location.hash = "#programs/" + encodeURIComponent(r.prog_name + ".LS");
            },
          });
        } else {
          vt = new BV.VTable(host, {
            columns: COLUMNS,
            data: macros,
            onCount: function (n) { sb.setCount(n, macros.length); },
            onOpen: function (r) {
              if (r.prog_name) location.hash = "#programs/" + encodeURIComponent(r.prog_name + ".LS");
            },
          });
        }
        BV.currentVTable = vt;
        vt.setFilter(sb.value());
      }
      build();
    }).catch(function (e) {
      view.classList.remove("no-pad");
      view.innerHTML = '<div class="empty-state"><div class="big">macros unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* macros are no longer a top-level tab - they're reached via a button in the
     programs tab (#programs/macros). Expose the render for programs.js to call. */
  BV.macros = { render: render };
})();
