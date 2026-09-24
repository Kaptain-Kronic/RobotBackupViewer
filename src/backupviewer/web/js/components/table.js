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

   columns: [{ key, label, num?, dim?, accent?, width?, render(row, i) -> html }]
            label may be "" for an unlabelled column; cell content is
            render(row) when given, else the escaped row[key]; width is a
            css length honoured under opts.fixed.
   opts:    { maxWidth, maxHeight, style }  applied to the .tbl-wrap;
            { fixed: true } lays the table out at its container's width -
            columns with a width keep it, the rest share what is left, and a
            long cell ellipsises instead of widening the table;
            { rowKey(row), detail(row) -> node|html, expanded: {key: true} }
            make rows expandable: a click splices a .tbl-detail row under the
            row (and removes it again). expanded is the caller-owned open set
            so it can live in BV.tabState; rowKey defaults to the row index.
            Still no keyboard surface - that stays VTable's. */
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
    var html = '<table class="tbl' + (opts.fixed ? " tbl-fixed" : "") + '">';
    if (opts.fixed) {
      html += "<colgroup>" + columns.map(function (c) {
        return "<col" + (c.width ? ' style="width:' + BV.esc(c.width) + '"' : "") + ">";
      }).join("") + "</colgroup>";
    }
    html += "<thead><tr>";
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

    if (opts.detail) {
      var tbody = wrap.querySelector("tbody");
      var expanded = opts.expanded || {};
      var keyOf = function (i) { return opts.rowKey ? opts.rowKey(rows[i], i) : i; };
      var detailRow = function (i) {
        var tr = BV.el("tr", { class: "tbl-detail" });
        var td = BV.el("td", { colspan: String(columns.length) });
        var content = opts.detail(rows[i], i);
        if (typeof content === "string") td.innerHTML = content;
        else if (content) td.appendChild(content);
        tr.appendChild(td);
        return tr;
      };
      Array.prototype.slice.call(tbody.children).forEach(function (tr, i) {
        tr.classList.add("has-detail");
        tr._bvIdx = i;
        if (expanded[keyOf(i)]) {
          tr.classList.add("open");
          tbody.insertBefore(detailRow(i), tr.nextSibling);
        }
      });
      tbody.addEventListener("click", function (ev) {
        var tr = ev.target.closest ? ev.target.closest("tr") : null;
        /* a click inside the detail row is the detail's own business */
        if (!tr || tr._bvIdx === undefined) return;
        var i = tr._bvIdx, open = !tr.classList.contains("open");
        tr.classList.toggle("open", open);
        if (open) {
          expanded[keyOf(i)] = true;
          tbody.insertBefore(detailRow(i), tr.nextSibling);
        } else {
          delete expanded[keyOf(i)];
          var d = tr.nextSibling;
          if (d && d.classList && d.classList.contains("tbl-detail")) tbody.removeChild(d);
        }
      });
    }
    return wrap;
  };
})();
