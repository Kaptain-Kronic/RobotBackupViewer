/* components/table.js - BV.table: a small, STATIC HTML table.

   Use this for bounded, small row counts that render all at once (frame
   positions, the overview tasks/snapshot tables, dcs consistency tables, the
   alarm snapshot). It reuses the .tbl CSS, escapes consistently, and is the
   one place table markup lives instead of being re-typed per tab.

   NOT a replacement for BV.VTable. The rule of thumb:
     - bounded, small, shown all at once   -> BV.table   (this)
     - backup-sized list the user scrolls
       and j/k-navigates                   -> BV.VTable
   BV.table never sets BV.currentVTable and has no windowing, sorting, or
   keyboard surface - keeping it out of the keyboard-nav path on purpose.

   columns: [{ key, label, num?, dim?, accent?, render(row, i) -> html }]
            label may be "" for an unlabelled column; cell content is
            render(row) when given, else the escaped row[key].
   opts:    { maxWidth, maxHeight, style }  applied to the .tbl-wrap. */
(function () {
  "use strict";

  function cellClass(c) {
    var cls = "";
    if (c.num) cls += " num";
    if (c.dim) cls += " dim";
    if (c.accent) cls += " accent";
    return cls ? ' class="' + cls.slice(1) + '"' : "";
  }

  BV.table = function (columns, rows, opts) {
    opts = opts || {};
    columns = columns || [];
    var html = '<table class="tbl"><thead><tr>';
    columns.forEach(function (c) { html += "<th>" + BV.esc(c.label) + "</th>"; });
    html += "</tr></thead><tbody>";
    (rows || []).forEach(function (row, i) {
      html += "<tr>";
      columns.forEach(function (c) {
        var content = c.render ? c.render(row, i) : BV.esc(row[c.key]);
        html += "<td" + cellClass(c) + ">" + content + "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody></table>";

    var wrap = BV.el("div", { class: "tbl-wrap" });
    if (opts.maxWidth) wrap.style.maxWidth = opts.maxWidth;
    if (opts.maxHeight) wrap.style.maxHeight = opts.maxHeight;
    if (opts.style) wrap.style.cssText += opts.style;
    wrap.innerHTML = html;
    return wrap;
  };
})();
