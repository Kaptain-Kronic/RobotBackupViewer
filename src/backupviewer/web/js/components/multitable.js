/* multitable.js - two VTables working as one, to halve scrolling on wide screens.
   Duck-types the VTable surface keys.js relies on (container, total, setFilter,
   moveSelection, openSelected, destroy) plus switchPane for ←/→.

   mode "split": ONE logical list shown as two contiguous halves side by side
                 (rows 1..N/2 | N/2+1..N). Falls back to a single pane when narrow.
   mode "pair":  TWO independent lists (e.g. DI | DO), each with its own data
                 and label. Stacks vertically when narrow.
*/
(function () {
  "use strict";

  var MIN_PANE_W = 470;

  function MultiTable(host, opts) {
    this.host = host;
    this.container = host; /* for liveVT() document.contains checks */
    this.opts = opts;
    this.mode = opts.mode || "split";
    this.filter = "";
    this.active = 0;       /* pane index that owns the selection */
    this.linked = true;    /* side-by-side panes scroll together until unlocked */
    this.tables = [];
    this.host.classList.add("multitable");
    this._onResize = BV.debounce(this._layout.bind(this), 120);
    window.addEventListener("resize", this._onResize);
    this._layout();
  }

  MultiTable.prototype._wide = function () {
    return this.host.clientWidth >= MIN_PANE_W * 2;
  };

  MultiTable.prototype._layout = function () {
    var wide = this._wide();
    if (this._wasWide === wide && this.tables.length) return;
    this._wasWide = wide;
    this._build(wide);
  };

  MultiTable.prototype._build = function (wide) {
    var self = this;
    this.tables.forEach(function (t) { t.vt.destroy(); });
    this.tables = [];
    this.host.innerHTML = "";

    var panes;
    if (this.mode === "pair") {
      panes = this.opts.panes;
      this.host.classList.toggle("mt-stack", !wide);
    } else {
      panes = wide ? [{}, {}] : [{}];
      this.host.classList.remove("mt-stack");
    }
    this.host.classList.toggle("mt-haslabels", panes.some(function (p) { return p.label; }));
    this.host.style.gridTemplateColumns = (this.mode === "pair" && !wide)
      ? "1fr" : "repeat(" + panes.length + ", 1fr)";

    panes.forEach(function (pane, i) {
      var cell = BV.el("div", { class: "mt-pane" });
      if (pane.label) {
        cell.appendChild(BV.el("div", { class: "mt-label" }, BV.esc(pane.label)));
      }
      var tEl = BV.el("div", { class: "mt-table" });
      cell.appendChild(tEl);
      self.host.appendChild(cell);
      var vt = new BV.VTable(tEl, {
        columns: pane.columns || self.opts.columns,
        data: [],
        rowHeight: self.opts.rowHeight,
        rowClass: pane.rowClass || self.opts.rowClass,
        onOpen: function (row) {
          self.active = i;
          if (self.opts.onOpen) self.opts.onOpen(row);
        },
      });
      self.tables.push({ vt: vt, pane: pane });
    });
    /* two panes always get the lock + linked scroll, including when the window
       shrinks and they stack - the lock must never silently vanish */
    if (this.tables.length === 2) this._setupLinkedScroll();
    this._refill();
  };

  /* panes scroll together by default; the tiny lock between them toggles it */
  MultiTable.prototype._setupLinkedScroll = function () {
    var self = this;
    var a = this.tables[0].vt.container;
    var b = this.tables[1].vt.container;
    var syncing = false;

    function follow(src, dst) {
      src.addEventListener("scroll", function () {
        if (!self.linked || syncing) return;
        syncing = true;
        dst.scrollTop = src.scrollTop;
        syncing = false;
      });
    }
    follow(a, b);
    follow(b, a);

    var lock = BV.el("button", { class: "mt-lock", title: "panes scroll together — click to scroll independently" }, "🔒");
    lock.addEventListener("click", function () {
      self.linked = !self.linked;
      lock.textContent = self.linked ? "🔒" : "🔓";
      lock.title = self.linked
        ? "panes scroll together — click to scroll independently"
        : "panes scroll independently — click to link them";
      if (self.linked) b.scrollTop = a.scrollTop;
    });
    this.host.appendChild(lock);
  };

  MultiTable.prototype._matches = function (row, columns) {
    if (!this.filter) return true;
    var q = this.filter.toLowerCase();
    return columns.some(function (c) {
      var v = row[c.key];
      return v !== null && v !== undefined && String(v).toLowerCase().indexOf(q) >= 0;
    });
  };

  MultiTable.prototype._refill = function () {
    var self = this;
    this.totalShown = 0;
    this.totalAll = 0;
    if (this.mode === "pair") {
      this.tables.forEach(function (t) {
        var cols = t.pane.columns || self.opts.columns;
        var rows = (t.pane.data || []).filter(function (r) { return self._matches(r, cols); });
        t.vt.setData(rows);
        self.totalShown += rows.length;
        self.totalAll += (t.pane.data || []).length;
      });
    } else {
      var cols = this.opts.columns;
      var rows = (this.opts.data || []).filter(function (r) { return self._matches(r, cols); });
      this.totalAll = (this.opts.data || []).length;
      this.totalShown = rows.length;
      if (this.tables.length === 2) {
        var half = Math.ceil(rows.length / 2);
        this.tables[0].vt.setData(rows.slice(0, half));
        this.tables[1].vt.setData(rows.slice(half));
      } else if (this.tables.length === 1) {
        this.tables[0].vt.setData(rows);
      }
    }
    this.total = this.totalShown;
    if (this.opts.onCount) this.opts.onCount(this.totalShown, this.totalAll);
  };

  /* ---- duck-typed surface ---- */

  MultiTable.prototype.setFilter = function (q) {
    this.filter = q || "";
    this._refill();
  };

  MultiTable.prototype.setData = function (dataOrPanes) {
    if (this.mode === "pair") this.opts.panes = dataOrPanes;
    else this.opts.data = dataOrPanes;
    this._refill();
  };

  MultiTable.prototype.moveSelection = function (d) {
    var t = this.tables[this.active];
    if (!t) return;
    if (t.vt.total === 0 && this.tables.length > 1) {
      this.active = (this.active + 1) % this.tables.length;
      t = this.tables[this.active];
    }
    t.vt.moveSelection(d);
  };

  MultiTable.prototype.switchPane = function (d) {
    if (this.tables.length < 2) return;
    var from = this.tables[this.active];
    this.active = (this.active + d + this.tables.length) % this.tables.length;
    var to = this.tables[this.active];
    var idx = Math.max(0, Math.min(from.vt.selected, to.vt.total - 1));
    from.vt.selected = -1;
    from.vt._render();
    if (to.vt.total) to.vt.select(idx);
  };

  MultiTable.prototype.openSelected = function () {
    var t = this.tables[this.active];
    if (t) t.vt.openSelected();
  };

  /* select a row by predicate across panes (used by jump-to-signal);
     centers the row so it never lands a pixel inside the bottom edge */
  MultiTable.prototype.selectWhere = function (pred) {
    for (var i = 0; i < this.tables.length; i++) {
      var vt = this.tables[i].vt;
      var idx = vt.view.findIndex(pred);
      if (idx >= 0) {
        this.active = i;
        vt.select(idx, true);
        return true;
      }
    }
    return false;
  };

  MultiTable.prototype.destroy = function () {
    window.removeEventListener("resize", this._onResize);
    this.tables.forEach(function (t) { t.vt.destroy(); });
    this.host.classList.remove("multitable", "mt-stack");
    this.host.innerHTML = "";
  };

  BV.MultiTable = MultiTable;
})();
