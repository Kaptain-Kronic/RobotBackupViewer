/* tabs/pdiff.js - program vs program, line by line (#pdiff/<fileA>/<fileB>).
   One scroller, both programs aligned side by side: same lines dimmed,
   changes highlighted, A-only / B-only rows padded on the missing side.
   Reached from a changed-program row in the compare report or from the
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

      var st = d.stats;
      var diffCount = st.change + st.a_only + st.b_only;

      /* toolbar: stats + diff navigation */
      var stats = BV.el("span", { class: "dim", style: "font-size:.78rem" },
        '<span class="pill acc">~' + st.change + "</span> " +
        '<span class="pill err">a only ' + st.a_only + "</span> " +
        '<span class="pill on">b only ' + st.b_only + "</span> " +
        '<span style="margin-left:.4em">' + st.same + " identical lines</span>");
      toolbar.appendChild(stats);
      var prevBtn = BV.el("button", { class: "btn", title: "previous difference" }, "↑ prev");
      var nextBtn = BV.el("button", { class: "btn", title: "next difference" }, "↓ next");
      toolbar.appendChild(prevBtn);
      toolbar.appendChild(nextBtn);

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

      var wrap = BV.el("div", { class: "viewer pd-viewer", style: "height:calc(100% - 2.4rem)" });
      view.appendChild(wrap);

      /* column headers */
      var head = BV.el("div", { class: "pd-row pd-head" },
        '<span class="ln"></span><span class="pd-text">' + BV.esc(d.a.robot) + " · " + BV.esc(d.a.name) + "</span>" +
        '<span class="ln"></span><span class="pd-text">' + BV.esc(d.b.robot) + " · " + BV.esc(d.b.name) + "</span>");
      wrap.appendChild(head);

      var body = BV.el("div");
      var html = "";
      d.rows.forEach(function (r, i) {
        html += '<div class="pd-row pd-' + r.kind + '" data-i="' + i + '">' +
          '<span class="ln">' + (r.a_n !== null ? r.a_n : "") + "</span>" +
          '<span class="pd-text">' + (r.a_text !== null
            ? (r.a_text.trim() ? BV.highlightTP(r.a_text) : "&nbsp;")
            : '<span class="pd-void">— non-existent —</span>') + "</span>" +
          '<span class="ln">' + (r.b_n !== null ? r.b_n : "") + "</span>" +
          '<span class="pd-text">' + (r.b_text !== null
            ? (r.b_text.trim() ? BV.highlightTP(r.b_text) : "&nbsp;")
            : '<span class="pd-void">— non-existent —</span>') + "</span></div>";
      });
      body.innerHTML = html;
      wrap.appendChild(body);

      /* center a row in the viewer, BELOW the sticky program/robot header (a
         top-aligned target would hide under it) and flash THAT row. Reading
         clientHeight / getBoundingClientRect forces a synchronous reflow, so the
         math is correct without deferring (and the hidden probe window, which
         never runs rAF callbacks, still scrolls + flashes). */
      function scrollToRow(el) {
        var headH = head.offsetHeight;
        var wr = wrap.getBoundingClientRect();
        var er = el.getBoundingClientRect();
        var avail = wrap.clientHeight - headH;
        wrap.scrollTop += (er.top - wr.top - headH) - Math.max(0, (avail - el.offsetHeight) / 2);
        el.classList.remove("flash");
        void el.offsetWidth; /* restart the animation */
        el.classList.add("flash");
      }

      /* jump between differences */
      var diffIdx = [];
      d.rows.forEach(function (r, i) { if (r.kind !== "same") diffIdx.push(i); });
      var cur = -1;
      function jump(dir) {
        if (!diffIdx.length) { BV.toast("programs are identical"); return; }
        cur = (cur + dir + diffIdx.length) % diffIdx.length;
        var el = body.querySelector('[data-i="' + diffIdx[cur] + '"]');
        if (el) scrollToRow(el);
      }
      nextBtn.addEventListener("click", function () { jump(1); });
      prevBtn.addEventListener("click", function () { jump(-1); });

      /* #pdiff/A/B/La26 - land on a specific line (from the report's inline
         mini-diff). The side letter (a/b) disambiguates which program's line 26
         this is; a bare L26 (older deep link) falls back to either side. */
      var am = params[2] ? /^L([ab])?(\d+)$/.exec(params[2]) : null;
      var anchorSide = am && am[1] ? am[1] : null;
      var anchor = am ? parseInt(am[2], 10) : null;
      if (anchor !== null) {
        var target = -1;
        d.rows.some(function (r, i) {
          var hit = anchorSide ? r[anchorSide + "_n"] === anchor
                               : (r.a_n === anchor || r.b_n === anchor);
          if (hit) { target = i; return true; }
          return false;
        });
        if (target >= 0) {
          cur = diffIdx.indexOf(target); /* keep prev/next in sync if it's a diff row */
          var tel = body.querySelector('[data-i="' + target + '"]');
          if (tel) scrollToRow(tel);
        } else if (diffCount) jump(1);
      } else if (diffCount) jump(1); /* land on the first difference */

      body.addEventListener("click", function (e) {
        var row = e.target.closest(".pd-row[data-i]");
        if (!row) return;
        row.classList.remove("flash");
        void row.offsetWidth;
        row.classList.add("flash");
      });
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">diff unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "pdiff", label: "program diff", hidden: true, always: true, render: render });
})();
