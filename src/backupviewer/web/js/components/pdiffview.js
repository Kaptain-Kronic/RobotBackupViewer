/* components/pdiffview.js - BV.pdiffView: the aligned side-by-side program
   diff renderer. One scroller, both programs' lines paired per visual row:
   same lines dimmed, changes highlighted, a-only / b-only rows padded with
   "non-existent" on the missing side, prev/next navigation between real
   differences.

   Extracted from tabs/pdiff.js when the edit workspace grew two more diff
   views (review-your-edits, pane-vs-pane) - one renderer, three callers, so a
   fix here heals every screen. Row shape is compare.align_program_lines():
   {kind: same|equiv|change|a_only|b_only, a_n, a_text, b_n, b_text}.

   "equiv" is the honest middle state the pane-vs-pane diff needs: the texts
   differ but only in ref comments / IO-status display (R[21] vs
   R[21:SERVO GUN WORK], DI[10:Comment] vs DI[10:OFF:Comment]) - the same
   instruction saved under different pendant display state. Equiv rows show
   BOTH raw texts (nothing is rewritten), get their own quiet styling, and are
   NOT stops on prev/next - a diff that cried wolf on every IO line would be
   worse than no diff.

   setData() preserves the scroll position, so a live caller (the pane diff
   recomputing while you type) doesn't yank the list back to the top.

   BV.pdiffView(host, opts) -> {el, stats, setHeads, setData, jump, jumpToLine,
                                diffCount}
   opts: {heads: [aLabel, bLabel], onRowClick(row, rowEl)} */
(function () {
  "use strict";

  BV.pdiffView = function (host, opts) {
    opts = opts || {};
    var wrap = BV.el("div", { class: "viewer pd-viewer" });
    host.appendChild(wrap);

    var head = BV.el("div", { class: "pd-row pd-head" });
    wrap.appendChild(head);
    var body = BV.el("div");
    wrap.appendChild(body);

    /* the stats pills live in an element the CALLER places (a toolbar, a
       panel head) - updated on every setData so live callers stay honest */
    var stats = BV.el("span", { class: "dim", style: "font-size:.78rem" });

    var rows = [];
    var diffIdx = [];      /* indices of real differences (equiv is not one) */
    var cur = -1;

    function setHeads(a, b) {
      head.innerHTML = '<span class="ln"></span><span class="pd-text">' + BV.esc(a) +
        '</span><span class="ln"></span><span class="pd-text">' + BV.esc(b) + "</span>";
    }
    if (opts.heads) setHeads(opts.heads[0], opts.heads[1]);

    function side(r, s) {
      var t = r[s + "_text"];
      return '<span class="ln">' + (r[s + "_n"] !== null ? r[s + "_n"] : "") + "</span>" +
        '<span class="pd-text">' + (t !== null
          ? (t.trim() ? BV.highlightTP(t) : "&nbsp;")
          : '<span class="pd-void">— non-existent —</span>') + "</span>";
    }

    function setData(d) {
      rows = (d && d.rows) || [];
      var html = "";
      rows.forEach(function (r, i) {
        html += '<div class="pd-row pd-' + r.kind + '" data-i="' + i + '"' +
          (r.kind === "equiv"
            ? ' title="same instruction — only ref comments / IO-status display differ"'
            : "") + ">" + side(r, "a") + side(r, "b") + "</div>";
      });
      body.innerHTML = html;
      diffIdx = [];
      rows.forEach(function (r, i) {
        if (r.kind !== "same" && r.kind !== "equiv") diffIdx.push(i);
      });
      cur = -1;

      var st = (d && d.stats) || { same: 0, equiv: 0, change: 0, a_only: 0, b_only: 0 };
      stats.innerHTML = '<span class="pill acc">~' + (st.change || 0) + "</span> " +
        '<span class="pill err">a only ' + (st.a_only || 0) + "</span> " +
        '<span class="pill on">b only ' + (st.b_only || 0) + "</span> " +
        (st.equiv
          ? '<span class="pill ghost" title="same instruction, shown differently: ' +
            'ref comments and the pendant&#39;s IO-status display are save-time ' +
            'state, not program changes">' + st.equiv + " display-only</span> "
          : "") +
        '<span style="margin-left:.4em">' + (st.same || 0) + " identical lines</span>";
    }

    /* center a row in the viewer, BELOW the sticky program/robot header (a
       top-aligned target would hide under it) and flash THAT row. Reading
       clientHeight / getBoundingClientRect forces a synchronous reflow, so the
       math is correct without deferring (and the hidden probe window, which
       never runs rAF callbacks, still scrolls + flashes). */
    function scrollToRow(rowEl) {
      var headH = head.offsetHeight;
      var wr = wrap.getBoundingClientRect();
      var er = rowEl.getBoundingClientRect();
      var avail = wrap.clientHeight - headH;
      wrap.scrollTop += (er.top - wr.top - headH) - Math.max(0, (avail - rowEl.offsetHeight) / 2);
      rowEl.classList.remove("flash");
      void rowEl.offsetWidth; /* restart the animation */
      rowEl.classList.add("flash");
    }

    function jump(dir) {
      if (!diffIdx.length) { BV.toast("no differences"); return; }
      cur = (cur + dir + diffIdx.length) % diffIdx.length;
      var rowEl = body.querySelector('[data-i="' + diffIdx[cur] + '"]');
      if (rowEl) scrollToRow(rowEl);
    }

    /* land on the row holding line `n` of side "a"/"b" (either side when
       null). Keeps prev/next in sync when the target is a diff row. */
    function jumpToLine(sideKey, n) {
      var target = -1;
      rows.some(function (r, i) {
        var hit = sideKey ? r[sideKey + "_n"] === n
                          : (r.a_n === n || r.b_n === n);
        if (hit) { target = i; return true; }
        return false;
      });
      if (target < 0) return false;
      cur = diffIdx.indexOf(target);
      var rowEl = body.querySelector('[data-i="' + target + '"]');
      if (rowEl) scrollToRow(rowEl);
      return true;
    }

    body.addEventListener("click", function (e) {
      var rowEl = e.target.closest(".pd-row[data-i]");
      if (!rowEl) return;
      rowEl.classList.remove("flash");
      void rowEl.offsetWidth;
      rowEl.classList.add("flash");
      if (opts.onRowClick) opts.onRowClick(rows[+rowEl.dataset.i], rowEl);
    });

    if (opts.data) setData(opts.data);

    return {
      el: wrap,
      stats: stats,
      setHeads: setHeads,
      setData: setData,
      jump: jump,
      jumpToLine: jumpToLine,
      diffCount: function () { return diffIdx.length; },
    };
  };
})();
