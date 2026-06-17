/* vtable.js - the one table component. Windowed rendering handles 30k+ rows.
   Modes:
     sync : data = array; client-side sort + filter.
     async: provider(offset, limit, query) -> Promise<{total, filtered, rows}>;
            server-side filter+page (used by alarms).

   var vt = new BV.VTable(container, {
     columns: [{key, label, width, grow, num, dim, accent, sortable, render(row)}],
     data: [...]  OR  provider: fn,
     rowHeight: 27,
     onOpen: fn(row),
     rowClass: fn(row) -> extra class string
   });
   vt.setFilter(text); vt.refresh(); vt.destroy();
*/
(function () {
  "use strict";

  var OVERSCAN = 12;
  var PAGE = 300;

  /* column widths were designed at 14px root font; rem keeps them proportional
     to the font-size setting AND identical for header and body cells (em would
     track each cell's own font - 0.85rem rows vs 0.75rem headers - leaving
     columns ~15% narrow and misaligned) */
  function emw(px) {
    return (px / 14).toFixed(3) + "rem";
  }

  function defaultRowHeight() {
    var base = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    return Math.round(base * 1.85);
  }

  function VTable(container, opts) {
    this.container = container;
    this.opts = opts;
    this.rowHeight = opts.rowHeight || defaultRowHeight();
    this.columns = opts.columns;
    /* opts.sortKey/sortDir set the initial sort (e.g. programs default to name
       A-Z); header clicks then just toggle asc<->desc, never back to unsorted */
    this.sortKey = opts.sortKey || null;
    this.sortDir = opts.sortDir || 1;
    this.filter = "";
    this.selected = -1;

    this.async = !!opts.provider;
    this.allData = opts.data || [];
    this.view = this.async ? null : this.allData.slice();
    this.total = this.async ? 0 : this.view.length;
    this.pages = {};   /* async page cache: pageIndex -> rows */
    this.pending = {};

    this._build();
    if (this.async) this._fetchMeta();
    else this._applySync();
  }

  VTable.prototype._build = function () {
    var self = this;
    this.container.classList.add("vtable");
    this.container.innerHTML = "";

    this.head = BV.el("div", { class: "vt-head" });
    this.columns.forEach(function (col, ci) {
      var cls = "vt-cell" + (self._growCls(col) ? " grow" : "") + (col.num ? " num" : "") +
        (col.sortable !== false && !self.async ? " sortable" : "");
      var cell = BV.el("div", { class: cls }, '<span class="vt-label">' + BV.esc(col.label) + "</span>");
      if (self._colWidth(col)) { cell.style.width = self._colWidth(col); }
      if (col.sortable !== false && !self.async) {
        cell.addEventListener("click", function () { self._sortBy(col.key); });
      }
      /* drag the right border to resize this column; double-click it to auto-fit.
         the grip swallows its own click/dblclick so it never triggers a sort. */
      if (col.resizable !== false) {
        var grip = BV.el("div", { class: "vt-resize", title: "drag to resize · double-click to auto-fit" });
        grip.addEventListener("mousedown", function (e) { self._startResize(e, col, cell); });
        grip.addEventListener("click", function (e) { e.stopPropagation(); });
        grip.addEventListener("dblclick", function (e) { e.preventDefault(); e.stopPropagation(); self._autofit(col, ci); });
        cell.appendChild(grip);
      }
      col._headEl = cell;
      self.head.appendChild(cell);
    });
    this.container.appendChild(this.head);
    this._updateArrows();

    this.spacer = BV.el("div", { class: "vt-spacer" });
    this.container.appendChild(this.spacer);

    this._onScroll = BV.debounce(function () { self._render(); }, 8);
    this.container.addEventListener("scroll", this._onScroll);
    this._renderedRows = [];
  };

  /* ---------- sync ---------- */

  VTable.prototype._applySync = function () {
    var self = this;
    var rows = this.allData;
    if (this.filter) {
      var q = this.filter.toLowerCase();
      var keys = this.columns.map(function (c) { return c.key; });
      rows = rows.filter(function (r) {
        return keys.some(function (k) {
          var v = r[k];
          return v !== null && v !== undefined && String(v).toLowerCase().indexOf(q) >= 0;
        });
      });
    }
    if (this.sortKey) {
      var k = this.sortKey, dir = this.sortDir;
      rows = rows.slice().sort(function (a, b) {
        var x = a[k], y = b[k];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y)) * dir;
      });
    }
    this.view = rows;
    this.total = rows.length;
    this._layout();
  };

  VTable.prototype._sortBy = function (key) {
    /* toggle asc<->desc only; the list is never left "unsorted" (default is
       names A-Z via opts.sortKey) */
    if (this.sortKey === key) this.sortDir = -this.sortDir;
    else { this.sortKey = key; this.sortDir = 1; }
    this._updateArrows();
    if (this.opts.onSort) this.opts.onSort(this.sortKey, this.sortDir);
    this._applySync();
  };

  /* update only the label span so the resize grip in each header survives */
  VTable.prototype._updateArrows = function () {
    var self = this;
    this.columns.forEach(function (c) {
      var lab = c._headEl && c._headEl.querySelector(".vt-label");
      if (!lab) return;
      lab.innerHTML = BV.esc(c.label) + (c.key === self.sortKey
        ? '<span class="arrow">' + (self.sortDir === 1 ? "▲" : "▼") + "</span>" : "");
    });
  };

  /* a column grows to fill until the user resizes it, then it holds its width */
  VTable.prototype._growCls = function (col) { return col.grow && !col._resized; };
  VTable.prototype._colWidth = function (col) {
    if (col._resized) return emw(col._userWidth);
    return col.width ? emw(col.width) : null;
  };

  VTable.prototype._startResize = function (e, col, cell) {
    e.preventDefault();
    e.stopPropagation();
    var self = this;
    var startX = e.clientX;
    var startW = cell.getBoundingClientRect().width;
    var rootFs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    document.body.style.cursor = "col-resize";
    function move(ev) {
      var w = Math.max(40, startW + (ev.clientX - startX));
      col._resized = true;
      col._userWidth = w * 14 / rootFs;   /* emw() interprets widths at a 14px root */
      cell.classList.remove("grow");
      cell.style.width = emw(col._userWidth);
      self._render();
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.style.cursor = "";
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };

  /* double-click the grip: fit the column to its widest visible content */
  VTable.prototype._autofit = function (col, ci) {
    var rootFs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    var meas = BV.el("span", { style:
      "position:absolute;visibility:hidden;white-space:nowrap;font-size:0.85rem;" +
      "font-family:" + getComputedStyle(this.container).fontFamily });
    this.container.appendChild(meas);
    var hl = col._headEl.querySelector(".vt-label");
    meas.textContent = hl ? hl.textContent : col.label;
    var max = meas.scrollWidth;
    Array.prototype.forEach.call(this.spacer.querySelectorAll(".vt-row"), function (r) {
      var c = r.children[ci];
      if (!c) return;
      meas.textContent = c.textContent;
      if (meas.scrollWidth > max) max = meas.scrollWidth;
    });
    this.container.removeChild(meas);
    if (!max) return;
    var px = max + rootFs * 1.7;   /* cell padding (2 x 0.8rem) + slack */
    col._resized = true;
    col._userWidth = px * 14 / rootFs;
    col._headEl.classList.remove("grow");
    col._headEl.style.width = emw(col._userWidth);
    this._render();
  };

  /* ---------- async ---------- */

  VTable.prototype._fetchMeta = function () {
    var self = this;
    this.pages = {};
    this.pending = {};
    this.opts.provider(0, PAGE, this.filter).then(function (res) {
      self.total = res.filtered;
      self.pages[0] = res.rows;
      if (self.opts.onMeta) self.opts.onMeta(res);
      self._layout();
    }).catch(function (e) {
      self.container.appendChild(BV.el("div", { class: "notice" }, BV.esc(e.message)));
    });
  };

  VTable.prototype._pageFor = function (rowIdx) {
    return Math.floor(rowIdx / PAGE);
  };

  VTable.prototype._ensurePage = function (pi) {
    var self = this;
    if (this.pages[pi] || this.pending[pi]) return;
    this.pending[pi] = true;
    this.opts.provider(pi * PAGE, PAGE, this.filter).then(function (res) {
      delete self.pending[pi];
      self.pages[pi] = res.rows;
      self._render();
    }).catch(function () { delete self.pending[pi]; });
  };

  VTable.prototype._rowAt = function (i) {
    if (!this.async) return this.view[i];
    var pi = this._pageFor(i);
    var page = this.pages[pi];
    if (!page) { this._ensurePage(pi); return null; }
    return page[i - pi * PAGE] || null;
  };

  /* ---------- rendering ---------- */

  VTable.prototype._layout = function () {
    this.spacer.style.height = (this.total * this.rowHeight) + "px";
    this.spacer.innerHTML = "";
    this._renderedRows = [];
    this._render();
    if (this.opts.onCount) this.opts.onCount(this.total);
  };

  VTable.prototype._render = function () {
    var h = this.container.clientHeight - this.head.offsetHeight;
    var top = this.container.scrollTop;
    var first = Math.max(0, Math.floor(top / this.rowHeight) - OVERSCAN);
    var last = Math.min(this.total - 1, Math.ceil((top + h) / this.rowHeight) + OVERSCAN);

    var frag = document.createDocumentFragment();
    var self = this;
    this.spacer.innerHTML = "";

    for (var i = first; i <= last; i++) {
      var row = this._rowAt(i);
      var el = BV.el("div", { class: "vt-row" + (this.opts.onOpen ? " clickable" : "") + (i === this.selected ? " selected" : "") });
      el.style.top = (i * this.rowHeight) + "px";
      el.style.height = this.rowHeight + "px";
      if (row) {
        if (this.opts.rowClass) {
          var extra = this.opts.rowClass(row);
          if (extra) el.className += " " + extra;
        }
        this.columns.forEach(function (col) {
          var cls = "vt-cell" + (self._growCls(col) ? " grow" : "") + (col.num ? " num" : "") +
            (col.dim ? " dim" : "") + (col.accent ? " accent" : "");
          var cell = BV.el("div", { class: cls });
          var cw = self._colWidth(col);
          if (cw) cell.style.width = cw;
          var v = col.render ? col.render(row) : BV.esc(row[col.key]);
          cell.innerHTML = (v === null || v === undefined || v === "") ? "" : v;
          el.appendChild(cell);
        });
        (function (idx, r) {
          el.addEventListener("click", function () {
            self.select(idx);
            if (self.opts.onOpen) self.opts.onOpen(r);
          });
        })(i, row);
      } else {
        el.innerHTML = '<div class="vt-cell dim">…</div>';
      }
      frag.appendChild(el);
    }
    this.spacer.appendChild(frag);
  };

  /* ---------- public ---------- */

  VTable.prototype.setFilter = function (q) {
    this.filter = q || "";
    this.selected = -1;
    if (this.async) this._fetchMeta();
    else this._applySync();
  };

  VTable.prototype.setData = function (data) {
    this.allData = data || [];
    this.selected = -1;
    this._applySync();
  };

  VTable.prototype.select = function (i, center) {
    if (i < 0 || i >= this.total) return;
    this.selected = i;
    var viewTop = i * this.rowHeight;
    var headH = this.head.offsetHeight;
    if (center) {
      /* jump flows (search results, config jump) land the row mid-window,
         not one pixel inside the bottom edge */
      var avail = this.container.clientHeight - headH;
      this.container.scrollTop = Math.max(0, viewTop - avail / 2 + this.rowHeight / 2);
    } else if (viewTop < this.container.scrollTop) {
      this.container.scrollTop = viewTop;
    } else if (viewTop + this.rowHeight > this.container.scrollTop + this.container.clientHeight - headH) {
      this.container.scrollTop = viewTop - this.container.clientHeight + this.rowHeight + headH;
    }
    this._render();
  };

  VTable.prototype.moveSelection = function (delta) {
    var n = this.selected < 0 ? (delta > 0 ? 0 : this.total - 1) : this.selected + delta;
    this.select(Math.max(0, Math.min(this.total - 1, n)));
  };

  VTable.prototype.openSelected = function () {
    if (this.selected < 0 || !this.opts.onOpen) return;
    var row = this._rowAt(this.selected);
    if (row) this.opts.onOpen(row);
  };

  VTable.prototype.destroy = function () {
    this.container.removeEventListener("scroll", this._onScroll);
    this.container.classList.remove("vtable");
    this.container.innerHTML = "";
  };

  BV.VTable = VTable;
})();
