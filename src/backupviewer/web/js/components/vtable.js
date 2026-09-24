/* vtable.js - the one table component. Windowed rendering handles 30k+ rows.
   Modes:
     sync : data = array; client-side sort + filter.
     async: provider(offset, limit, query) -> Promise<{total, filtered, rows}>;
            server-side filter+page (used by alarms).

   var vt = new BV.VTable(container, {
     columns: [{key, label, width, grow, num, dim, accent, sortable, render(row),
                headRender() -> node}],
     data: [...]  OR  provider: fn,
     rowHeight: 27,
     onOpen: fn(row),
     onContext: fn(row, ev),          // right-click a row (selects it first)
     rowClass: fn(row) -> extra class string,
     // expandable rows (sync mode): a click or enter on a row opens its detail
     // under the cells instead of calling onOpen. Heights are measured from
     // the DOM, so the window math stays exact. rowKey names a row across
     // refills (filter, sort, both panes of a MultiTable); expanded is the
     // caller-owned {key: true} map so the open set can live in BV.tabState.
     rowKey: fn(row) -> key, detail: fn(row) -> node|html,
     expanded: {}, onToggle: fn(row, open)
   });
   vt.setFilter(text); vt.refresh(); vt.destroy(); vt.toggle(row, force?);
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
    /* a resize-drag ends with a mouseup that the browser also reports as a click
       on the header cell; this timestamp lets the sort handler ignore that click
       (mirrors dragReorder's clickGuardMs/isRecentDrag) */
    this._lastResizeEnd = 0;

    this.rowKey = opts.rowKey || null;
    this.detail = opts.detail || null;
    this.expanded = opts.expanded || {};
    this._detailH = {};      /* key -> measured detail height (px) */
    this._tops = null;       /* row offsets, only while something is expanded */

    /* opts.stateKey: persist scroll position + sort across navigating in/out of
       the tab (in-session, via BV.tabState). Restored once on first layout. */
    this.stateKey = opts.stateKey || null;
    if (this.stateKey) {
      var saved = BV.tabState(this.stateKey);
      if (saved.sortKey) { this.sortKey = saved.sortKey; this.sortDir = saved.sortDir || 1; }
    }

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
      /* col.headRender() -> a NODE prepended inside the header cell (a
         select-all box, a units switch). Deliberately OUTSIDE .vt-label, for
         the same reason the resize grip is: _updateArrows rewrites only the
         label span, so a sort toggle leaves this alone. */
      if (col.headRender) {
        var extra = col.headRender();
        if (extra) cell.insertBefore(extra, cell.firstChild);
      }
      if (self._colWidth(col)) { cell.style.width = self._colWidth(col); }
      if (col.sortable !== false && !self.async) {
        cell.addEventListener("click", function () {
          /* a header click that is really the tail of a column resize-drag must
             not toggle the sort (the resize mouseup lands on the cell) */
          if (Date.now() - self._lastResizeEnd < 250) return;
          self._sortBy(col.key);
        });
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
    /* persist scroll synchronously (a number), independent of the render debounce,
       so it survives the router's scrollTop reset on the next route */
    if (this.stateKey) {
      this.container.addEventListener("scroll", function () {
        BV.tabState(self.stateKey).scrollTop = self.container.scrollTop;
      });
    }
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
    if (this.stateKey) {
      var s = BV.tabState(this.stateKey);
      s.sortKey = this.sortKey; s.sortDir = this.sortDir;
    }
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
    var moved = false;
    function move(ev) {
      moved = true;
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
      /* only guard the sort click when an actual drag happened */
      if (moved) self._lastResizeEnd = Date.now();
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

  /* ---------- expandable rows ---------- */

  VTable.prototype._key = function (row) {
    return this.rowKey ? this.rowKey(row) : undefined;
  };

  VTable.prototype._isExpanded = function (row) {
    if (!this.detail || !row) return false;
    var k = this._key(row);
    return k !== undefined && k !== null && !!this.expanded[k];
  };

  /* row offsets: null (every row is rowHeight tall - the plain arithmetic
     path every table has always used) until a row is expanded, then a prefix
     sum over the view. Async tables never expand: their rows arrive by page
     and the sum needs them all. */
  VTable.prototype._reindex = function () {
    this._tops = null;
    if (!this.detail || this.async) return;
    var rh = this.rowHeight, tops = null;
    for (var i = 0; i < this.total; i++) {
      var row = this.view[i];
      if (!this._isExpanded(row)) {
        if (tops) tops[i + 1] = tops[i] + rh;
        continue;
      }
      if (!tops) {
        tops = new Array(this.total + 1);
        for (var j = 0; j <= i; j++) tops[j] = j * rh;
      }
      var dh = this._detailH[this._key(row)];
      /* unmeasured: guess three rows; the first render corrects it */
      tops[i + 1] = tops[i] + rh + (dh === undefined ? rh * 3 : dh);
    }
    this._tops = tops;
  };

  VTable.prototype._rowTop = function (i) { return this._tops ? this._tops[i] : i * this.rowHeight; };
  VTable.prototype._rowH = function (i) { return this._tops ? this._tops[i + 1] - this._tops[i] : this.rowHeight; };
  VTable.prototype._totalH = function () { return this._tops ? this._tops[this.total] : this.total * this.rowHeight; };

  /* the row at spacer offset y (a binary search once rows differ in height) */
  VTable.prototype._indexAt = function (y) {
    if (!this._tops) return Math.floor(y / this.rowHeight);
    var lo = 0, hi = this.total - 1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (this._tops[mid] <= y) lo = mid; else hi = mid - 1;
    }
    return lo;
  };

  /* keep row i on screen: its top never above the viewport, and as much of
     its (possibly expanded) height showing as fits - the top wins when the
     whole thing can't */
  VTable.prototype._scrollRowIntoView = function (i) {
    var top = this._rowTop(i), hgt = this._rowH(i);
    var avail = this.container.clientHeight - this.head.offsetHeight;
    if (top < this.container.scrollTop) {
      this.container.scrollTop = top;
    } else if (top + hgt > this.container.scrollTop + avail) {
      this.container.scrollTop = Math.min(top, top + hgt - avail);
    }
  };

  /* expanded rows are as tall as their content: read that back after a
     render and, when a guess or a stale height was in play, relayout once
     with the truth (the guard stops the render->measure->render loop) */
  VTable.prototype._measureDetails = function () {
    var self = this, changed = false;
    Array.prototype.forEach.call(this.spacer.querySelectorAll(".vt-detail"), function (d) {
      var h = d.offsetHeight;
      if (h && self._detailH[d._bvKey] !== h) { self._detailH[d._bvKey] = h; changed = true; }
    });
    if (changed && !this._measuring) {
      this._measuring = true;
      try {
        this._reindex();
        this.spacer.style.height = this._totalH() + "px";
        this._render();
      } finally {
        this._measuring = false;
      }
    }
  };

  /* open/close a row's detail; force = true/false sets, undefined toggles */
  VTable.prototype.toggle = function (row, force) {
    if (!this.detail) return;
    var k = this._key(row);
    if (k === undefined || k === null) return;
    var open = force === undefined ? !this.expanded[k] : !!force;
    if (open) this.expanded[k] = true; else delete this.expanded[k];
    this._reindex();
    this.spacer.style.height = this._totalH() + "px";
    this._render();
    if (open) {
      var idx = this.view.indexOf(row);
      if (idx >= 0) this._scrollRowIntoView(idx);
    }
    if (this.opts.onToggle) this.opts.onToggle(row, open);
  };

  /* ---------- rendering ---------- */

  VTable.prototype._layout = function () {
    this._reindex();
    this.spacer.style.height = this._totalH() + "px";
    this.spacer.innerHTML = "";
    this._renderedRows = [];
    this._render();
    /* restore the saved scroll once the container is actually laid out + scrollable
       (sync - the hidden probe window runs no rAF). The first _layout can fire
       before the host has a resolved height, where scrollTop would clamp to 0, so
       keep retrying on later layouts until it sticks. */
    if (this.stateKey && !this._scrollRestored) {
      var saved = BV.tabState(this.stateKey);
      if (!saved.scrollTop) {
        this._scrollRestored = true;
      } else if (this.container.clientHeight > 0 &&
                 this.spacer.offsetHeight > this.container.clientHeight) {
        this.container.scrollTop = saved.scrollTop;
        this._scrollRestored = true;
        this._render();
      }
    }
    if (this.opts.onCount) this.opts.onCount(this.total);
  };

  VTable.prototype._render = function () {
    var h = this.container.clientHeight - this.head.offsetHeight;
    var top = this.container.scrollTop;
    var first = Math.max(0, this._indexAt(top) - OVERSCAN);
    var last = Math.min(this.total - 1, this._indexAt(top + h) + OVERSCAN);

    var frag = document.createDocumentFragment();
    var self = this;
    this.spacer.innerHTML = "";

    for (var i = first; i <= last; i++) {
      var row = this._rowAt(i);
      var open = this._isExpanded(row);
      var el = BV.el("div", { class: "vt-row" + (this.opts.onOpen || this.detail ? " clickable" : "") +
        (i === this.selected ? " selected" : "") + (open ? " expanded" : "") });
      el.style.top = this._rowTop(i) + "px";
      el.style.height = this._rowH(i) + "px";
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
          if (open) {
            /* the cell line stays one row tall; the detail wraps under it */
            cell.style.height = self.rowHeight + "px";
            cell.style.lineHeight = self.rowHeight + "px";
          }
          el.appendChild(cell);
        });
        if (open) {
          var det = BV.el("div", { class: "vt-detail" });
          var content = this.detail(row);
          if (typeof content === "string") det.innerHTML = content;
          else if (content) det.appendChild(content);
          det._bvKey = this._key(row);
          el.appendChild(det);
        }
        (function (idx, r) {
          el.addEventListener("click", function (ev) {
            /* clicks inside an open detail (its pager, its buttons, selecting
               its text) belong to the detail, never to the row */
            if (ev.target.closest && ev.target.closest(".vt-detail")) return;
            self.select(idx);
            if (self.detail) self.toggle(r);
            else if (self.opts.onOpen) self.opts.onOpen(r);
          });
          /* right-click = row actions. select() first, so the menu and the
             highlight can never point at different rows; preventDefault lives
             here rather than in the caller, so a table WITHOUT onContext keeps
             the platform menu. */
          if (self.opts.onContext) {
            el.addEventListener("contextmenu", function (ev) {
              ev.preventDefault();
              self.select(idx);
              self.opts.onContext(r, ev);
            });
          }
        })(i, row);
      } else {
        el.innerHTML = '<div class="vt-cell dim">…</div>';
      }
      frag.appendChild(el);
    }
    this.spacer.appendChild(frag);
    if (this.detail) this._measureDetails();
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

  /* repaint the visible rows without touching data, filter or selection.
     Rows are rebuilt every frame, so a column whose render() reflects state
     the caller owns (a checked set, a star) needs this after that state
     changes - otherwise the change only shows once you happen to scroll. */
  VTable.prototype.repaint = function () { this._render(); };

  VTable.prototype.select = function (i, center) {
    if (i < 0 || i >= this.total) return;
    this.selected = i;
    if (center) {
      /* jump flows (search results, config jump) land the row mid-window,
         not one pixel inside the bottom edge */
      var avail = this.container.clientHeight - this.head.offsetHeight;
      this.container.scrollTop = Math.max(0, this._rowTop(i) - avail / 2 + this._rowH(i) / 2);
    } else {
      this._scrollRowIntoView(i);
    }
    this._render();
  };

  VTable.prototype.moveSelection = function (delta) {
    var n = this.selected < 0 ? (delta > 0 ? 0 : this.total - 1) : this.selected + delta;
    this.select(Math.max(0, Math.min(this.total - 1, n)));
  };

  VTable.prototype.openSelected = function () {
    if (this.selected < 0) return;
    var row = this._rowAt(this.selected);
    if (!row) return;
    if (this.detail) this.toggle(row);
    else if (this.opts.onOpen) this.opts.onOpen(row);
  };

  VTable.prototype.destroy = function () {
    this.container.removeEventListener("scroll", this._onScroll);
    this.container.classList.remove("vtable");
    this.container.innerHTML = "";
  };

  BV.VTable = VTable;
})();
