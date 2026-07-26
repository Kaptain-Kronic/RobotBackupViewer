/* tabs/pdiff.js - program vs program, line by line (#pdiff/<fileA>/<fileB>).
   The rendering (aligned rows, prev/next, flash) is BV.pdiffView - shared with
   the edit workspace's review and pane-vs-pane diffs. This tab owns only the
   route: the crumb, the toolbar, the workspace buttons and the deep-link
   anchor. Reached from a changed-program row in the compare report or from the
   programs tab in vs mode. */
(function () {
  "use strict";

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    if (!params || !params[0] || !params[1]) {
      view.innerHTML = '<div class="empty-state"><div class="big">program diff</div>' +
        '<div class="hint">pick a changed program in the compare report,<br>' +
        "or select one program on each side of the programs tab (vs mode)</div></div>";
      return;
    }
    var fileA = decodeURIComponent(params[0]);
    var fileB = decodeURIComponent(params[1]);

    BV.api.call("diff_program", fileA, fileB).then(function (d) {
      var crumb = BV.el("div", { class: "crumb" });
      crumb.innerHTML = '<span class="back">← back</span>' +
        '<span class="title">' + BV.esc(d.a.name) +
        (d.a.name !== d.b.name ? " ⇄ " + BV.esc(d.b.name) : "") + "</span>" +
        '<span class="dim">' + BV.esc(d.a.robot) + " vs " + BV.esc(d.b.robot) + "</span>";
      crumb.querySelector(".back").addEventListener("click", function () { history.back(); });
      view.appendChild(crumb);

      var pd = BV.pdiffView(view, {
        heads: [d.a.robot + " · " + d.a.name, d.b.robot + " · " + d.b.name],
        data: d,
      });
      pd.el.style.height = "calc(100% - 2.4rem)";

      /* toolbar: stats + diff navigation */
      toolbar.appendChild(pd.stats);
      var prevBtn = BV.el("button", { class: "btn", title: "previous difference" }, "↑ prev");
      var nextBtn = BV.el("button", { class: "btn", title: "next difference" }, "↓ next");
      toolbar.appendChild(prevBtn);
      toolbar.appendChild(nextBtn);
      nextBtn.addEventListener("click", function () { pd.jump(1); });
      prevBtn.addEventListener("click", function () { pd.jump(-1); });

      /* each side goes to the edit workspace on its own: the two sides are
         different robots, so they are two separate entries. diff_program
         returns no path - the roots come from state, like compare.js does. */
      /* root_key (resolved), not path (the sid's literal spelling) - see
         BV.workspace.currentSource */
      var mA = BV.state.manifest || {}, mB = BV.state.compare || {};
      var rootA = mA.root_key || mA.path || "";
      var rootB = mB.root_key || mB.path || "";
      if (rootA) {
        toolbar.appendChild(BV.workspace.button({
          label: "+ left", title: "add " + d.a.robot + " · " + d.a.name + " to the edit workspace",
          entries: function () {
            return [{ root: rootA, label: d.a.robot, file: d.a.rel || fileA, name: fileA }];
          },
        }));
      }
      if (rootB) {
        toolbar.appendChild(BV.workspace.button({
          label: "+ right", title: "add " + d.b.robot + " · " + d.b.name + " to the edit workspace",
          entries: function () {
            return [{ root: rootB, label: d.b.robot, file: d.b.rel || fileB, name: fileB }];
          },
        }));
      }

      /* #pdiff/A/B/La26 - land on a specific line (from the report's inline
         mini-diff). The side letter (a/b) disambiguates which program's line 26
         this is; a bare L26 (older deep link) falls back to either side. */
      var am = params[2] ? /^L([ab])?(\d+)$/.exec(params[2]) : null;
      if (am) {
        if (!pd.jumpToLine(am[1] || null, parseInt(am[2], 10)) && pd.diffCount()) pd.jump(1);
      } else if (pd.diffCount()) pd.jump(1); /* land on the first difference */
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">diff unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "pdiff", label: "program diff", hidden: true, always: true, render: render });
})();
