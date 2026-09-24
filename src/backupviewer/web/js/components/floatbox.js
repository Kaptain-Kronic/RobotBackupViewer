/* components/floatbox.js - BV.FloatBox: draggable, snappable, resizable boxes
   floating over a screen, and BV.floatLayer, the one host they live in.

   The layer is mounted on <html>, never inside the chrome. html.frosted gives
   #chrome-top a backdrop-filter, which makes it a stacking context - anything
   mounted in there paints UNDER the view (components.css has the receipt, and
   it is why BV.menu and BV.dropPanel mount on <html> too). It spans the
   content region only, between the topbar and the statusbar (BV.chromeInset),
   so a float can never cover navigation or hide the statusbar.

   z 70: above #toast and .card.expanded (60), below .cvx-remote (80) so a full
   remote covers the floats, and below #modal-root (90) so dialogs still land
   on top. The layer itself is pointer-events:none and each box re-enables
   them, so the screen underneath stays clickable in the gaps between boxes.

   GEOMETRY IS FRACTIONS of the layer (x/y/w/h in 0..1) plus an optional snap
   zone, never stored px. That is what survives a window resize and the
   text-size knob without a reflow pass: a snapped box re-derives its rect from
   the zone, a free one scales with its fractions and is clamped back inside.
   A drag writes px inline while it is happening and converts back on release -
   the same exception the pane resizers already take.

   Gestures are MOUSE events with a document-level move/up pair, not pointer
   events with capture: setPointerCapture throws under the hidden-window probe
   and there is no requestAnimationFrame there either, so the whole interaction
   has to be driveable by synthetic events alone. */
(function () {
  "use strict";

  /* fraction rects, so they are resolution- and text-size-independent */
  var ZONES = {
    n:  { x: 0,   y: 0,   w: 1,   h: 1   },   /* the top edge fills the layer */
    w:  { x: 0,   y: 0,   w: 0.5, h: 1   },
    e:  { x: 0.5, y: 0,   w: 0.5, h: 1   },
    s:  { x: 0,   y: 0.5, w: 1,   h: 0.5 },
    nw: { x: 0,   y: 0,   w: 0.5, h: 0.5 },
    ne: { x: 0.5, y: 0,   w: 0.5, h: 0.5 },
    sw: { x: 0,   y: 0.5, w: 0.5, h: 0.5 },
    se: { x: 0.5, y: 0.5, w: 0.5, h: 0.5 },
  };
  var DIRS = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

  var _layer = null, _ghost = null, _boxes = [], _wired = false;
  /* Stacking is a z-index, NEVER DOM order. Raising by re-appending the node
     looks equivalent and is not: raise() runs on every mousedown, and
     re-inserting the element that received the press aborts the click the
     browser was about to fire - so the bar's lock and close buttons silently
     did nothing while dragging (mousedown/move/up, no click) worked fine. */
  var _zTop = 0;

  function rootFs() {
    return parseFloat(getComputedStyle(document.documentElement).fontSize) || 14;
  }
  /* the magnet band and the floor on a box, both in rem so they track the
     text-size setting rather than freezing at one display scale */
  function band() { return rootFs() * 2.2; }
  function minSize() { return { w: rootFs() * 13, h: rootFs() * 9 }; }

  function layerEl() {
    if (_layer) return _layer;
    _layer = BV.el("div", { id: "floatlayer" });
    _ghost = BV.el("div", { class: "fbox-ghost", hidden: "" });
    _layer.appendChild(_ghost);
    document.documentElement.appendChild(_layer);
    if (!_wired) {
      _wired = true;
      window.addEventListener("resize", reflow);
    }
    place();
    return _layer;
  }
  /* the layer tracks the chrome; fullscreen and a missing slab both answer 0 */
  function place() {
    if (!_layer) return;
    var ci = BV.chromeInset();
    _layer.style.top = ci.top + "px";
    _layer.style.bottom = ci.bottom + "px";
  }
  function layerRect() {
    return layerEl().getBoundingClientRect();
  }
  function reflow() {
    place();
    var lr = layerRect();
    _boxes.forEach(function (b) { b._apply(lr); });
  }

  /* which zone the POINTER is in, not the box - a box dragged by its bottom
     corner would otherwise never reach the top edge */
  function zoneAt(px, py, lr) {
    var b = band();
    var nT = py - lr.top < b, nB = lr.bottom - py < b;
    var nL = px - lr.left < b, nR = lr.right - px < b;
    if (nT && nL) return "nw";
    if (nT && nR) return "ne";
    if (nB && nL) return "sw";
    if (nB && nR) return "se";
    if (nT) return "n";
    if (nB) return "s";
    if (nL) return "w";
    if (nR) return "e";
    return "";
  }
  function paintGhost(zone, lr) {
    layerEl();
    if (!zone) { _ghost.hidden = true; return; }
    var z = ZONES[zone];
    _ghost.style.left = Math.round(z.x * lr.width) + "px";
    _ghost.style.top = Math.round(z.y * lr.height) + "px";
    _ghost.style.width = Math.round(z.w * lr.width) + "px";
    _ghost.style.height = Math.round(z.h * lr.height) + "px";
    _ghost.hidden = false;
  }

  /* opts: {key, title, onClose, onGeom, geom} */
  function FloatBox(opts) {
    opts = opts || {};
    var self = {};
    var geom = normalize(opts.geom);
    var locked = !!geom.locked;

    var el = BV.el("div", { class: "fbox" });
    var bar = BV.el("div", { class: "fbox-bar" });
    var titleEl = BV.el("button", { class: "fbox-title", type: "button" },
      BV.esc(opts.title || ""));
    var statusEl = BV.el("span", { class: "fbox-status" });
    var slot = BV.el("span", { class: "fbox-slot" });
    var lockBtn = BV.el("button", { class: "btn fbox-lock", type: "button" });
    var closeBtn = BV.el("button", { class: "btn fbox-x", type: "button",
      title: "close this box" }, "✕");
    var body = BV.el("div", { class: "fbox-body" });
    bar.appendChild(titleEl);
    bar.appendChild(statusEl);
    bar.appendChild(BV.el("span", { class: "fbox-spacer" }));
    bar.appendChild(slot);
    bar.appendChild(lockBtn);
    bar.appendChild(closeBtn);
    el.appendChild(bar);
    el.appendChild(body);
    DIRS.forEach(function (d) {
      el.appendChild(BV.el("div", { class: "fbox-rz fbox-rz-" + d, "data-dir": d }));
    });
    layerEl().appendChild(el);
    _boxes.push(self);

    function normalize(g) {
      g = g || {};
      return { x: num(g.x, 0.06), y: num(g.y, 0.08), w: num(g.w, 0.42),
               h: num(g.h, 0.46), snap: g.snap || "", locked: !!g.locked };
    }
    function num(v, d) { return (typeof v === "number" && isFinite(v)) ? v : d; }

    /* fractions -> inline px. A snapped box re-derives from its zone, so it is
       exact at any window size; a free one scales and is clamped back inside,
       which is what stops a box that was parked at the edge of a big window
       from being unreachable in a small one. */
    self._apply = function (lr) {
      lr = lr || layerRect();
      var g = geom.snap && ZONES[geom.snap] ? ZONES[geom.snap] : geom;
      var mn = minSize();
      var w = Math.max(mn.w, Math.min(g.w * lr.width, lr.width));
      var h = Math.max(mn.h, Math.min(g.h * lr.height, lr.height));
      var x = Math.max(0, Math.min(g.x * lr.width, lr.width - w));
      var y = Math.max(0, Math.min(g.y * lr.height, lr.height - h));
      el.style.left = Math.round(x) + "px";
      el.style.top = Math.round(y) + "px";
      el.style.width = Math.round(w) + "px";
      el.style.height = Math.round(h) + "px";
    };
    /* read the live px rect back into fractions - the end of every free drag
       and every resize */
    function commitFree(lr) {
      var r = el.getBoundingClientRect();
      geom.snap = "";
      geom.x = (r.left - lr.left) / lr.width;
      geom.y = (r.top - lr.top) / lr.height;
      geom.w = r.width / lr.width;
      geom.h = r.height / lr.height;
      self._apply(lr);
      changed();
    }
    function changed() { if (opts.onGeom) opts.onGeom(self.geom()); }

    /* ---- move ---- */
    bar.addEventListener("mousedown", function (e) {
      if (locked || e.button !== 0) return;
      if (e.target.closest("button")) return;   /* the bar's own controls */
      e.preventDefault();
      self.raise();
      var lr = layerRect();
      var r = el.getBoundingClientRect();
      var offX = e.clientX - r.left, offY = e.clientY - r.top;
      var zone = "";
      function mv(ev) {
        el.classList.add("fbox-moving");
        el.style.left = (ev.clientX - offX - lr.left) + "px";
        el.style.top = (ev.clientY - offY - lr.top) + "px";
        zone = zoneAt(ev.clientX, ev.clientY, lr);
        paintGhost(zone, lr);
      }
      function up() {
        document.removeEventListener("mousemove", mv);
        document.removeEventListener("mouseup", up);
        el.classList.remove("fbox-moving");
        paintGhost("", lr);
        if (zone) { geom.snap = zone; self._apply(lr); changed(); }
        else commitFree(lr);
      }
      document.addEventListener("mousemove", mv);
      document.addEventListener("mouseup", up);
    });

    /* ---- resize ---- */
    el.addEventListener("mousedown", function (e) {
      var h = e.target.closest(".fbox-rz");
      if (!h || locked || e.button !== 0) return;
      e.preventDefault();
      self.raise();
      var dir = h.dataset.dir;
      var lr = layerRect();
      var r = el.getBoundingClientRect();
      var x0 = e.clientX, y0 = e.clientY;
      var L = r.left - lr.left, T = r.top - lr.top, W = r.width, H = r.height;
      var mn = minSize();
      function mv(ev) {
        el.classList.add("fbox-sizing");
        var dx = ev.clientX - x0, dy = ev.clientY - y0;
        var nl = L, nt = T, nw = W, nh = H;
        if (dir.indexOf("e") >= 0) nw = Math.max(mn.w, W + dx);
        if (dir.indexOf("s") >= 0) nh = Math.max(mn.h, H + dy);
        if (dir.indexOf("w") >= 0) { nw = Math.max(mn.w, W - dx); nl = L + (W - nw); }
        if (dir.indexOf("n") >= 0) { nh = Math.max(mn.h, H - dy); nt = T + (H - nh); }
        el.style.left = nl + "px"; el.style.top = nt + "px";
        el.style.width = nw + "px"; el.style.height = nh + "px";
      }
      function up() {
        document.removeEventListener("mousemove", mv);
        document.removeEventListener("mouseup", up);
        el.classList.remove("fbox-sizing");
        commitFree(lr);   /* a resized box is placed by hand: it leaves its zone */
      }
      document.addEventListener("mousemove", mv);
      document.addEventListener("mouseup", up);
    });

    /* ---- lock ---- */
    function paintLock() {
      lockBtn.innerHTML = BV.icon(locked ? "lock" : "unlock");
      lockBtn.title = locked
        ? "locked in place — click to move or resize it again"
        : "lock this box where it is";
      lockBtn.classList.toggle("on", locked);
      el.classList.toggle("locked", locked);
    }
    lockBtn.addEventListener("click", function () {
      locked = !locked; geom.locked = locked; paintLock(); changed();
    });
    closeBtn.addEventListener("click", function () { self.destroy(); });
    el.addEventListener("mousedown", function () { self.raise(); }, true);

    self.el = el;
    self.bar = bar;
    self.bodyEl = body;
    self.slot = slot;
    self.key = opts.key;
    self.titleEl = titleEl;
    self.setTitle = function (t) { titleEl.innerHTML = BV.esc(t || ""); };
    self.setStatus = function (t, err) {
      statusEl.textContent = t || "";
      statusEl.classList.toggle("err", !!err);
    };
    self.geom = function () {
      return { x: geom.x, y: geom.y, w: geom.w, h: geom.h,
               snap: geom.snap, locked: locked };
    };
    self.setGeom = function (g) {
      geom = normalize(g); locked = !!geom.locked; paintLock(); self._apply();
    };
    self.locked = function () { return locked; };
    self.raise = function () {
      var i = _boxes.indexOf(self);
      if (i >= 0 && i !== _boxes.length - 1) {
        _boxes.splice(i, 1); _boxes.push(self);
      }
      el.style.zIndex = ++_zTop;    /* never appendChild: see _zTop above */
    };
    /* "this one" — the wall's placeholder tile uses it to point at its box */
    self.flash = function () {
      self.raise();
      el.classList.remove("fbox-flash");
      void el.offsetWidth;            /* restart the animation, not queue it */
      el.classList.add("fbox-flash");
    };
    /* silent = take it off the layer without meaning "the user closed it".
       Rebuilding a box (a camera swap) needs that; leaving it out would either
       fire the owner's close path or strand a dead box in the reflow list. */
    self.destroy = function (silent) {
      var i = _boxes.indexOf(self);
      if (i >= 0) _boxes.splice(i, 1);
      el.remove();
      if (silent !== true && opts.onClose) opts.onClose(self);
    };

    paintLock();
    self._apply();
    self.raise();
    return self;
  }

  BV.floatLayer = {
    el: layerEl,
    rect: layerRect,
    reflow: reflow,
    boxes: function () { return _boxes.slice(); },
    /* parked = display:none, which is also what stops BV.camFeed feeding the
       pictures inside: every img then fails checkVisibility, the leases stop
       being renewed and python's reaper hands the slots back. One mechanism. */
    show: function (on) {
      var l = layerEl();
      l.hidden = !on;
      if (on) reflow();
    },
    shown: function () { return !!_layer && !_layer.hidden; },
    ZONES: ZONES,
  };
  BV.FloatBox = FloatBox;
})();
