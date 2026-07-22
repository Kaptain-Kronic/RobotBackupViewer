/* components/checklist.js - ONE multiselect for every checkbox list.
   Every checklist in the app (library robot rows, per-line select-alls, the
   fix-names / merge previews, discover results) shares this controller, so
   selection behaves identically everywhere:
     - click toggles one row
     - shift+click selects the visible range from the last-clicked row
       (file-manager convention: the whole range takes THIS click's new state;
       rows folded inside a collapsed group are never silently included)
     - a group box (select-all) is an honest tri-state: from empty it selects
       all, but ANY existing selection makes a click CLEAR — the "minus" state
       reads as "click to reset", never "click to select the rest"
   The controller owns selection STATE; callers own the DOM. A key can be
   bound to SEVERAL checkboxes at once (the library renders a favorited robot
   in the pinned strip AND in its plant tree) — every attached copy repaints
   together, and detached copies are pruned on re-bind, so re-renders just
   re-bind and selection survives refreshes for free. */
(function () {
  "use strict";

  /* opts: {onChange()} — fired after every sync (selection repaint), so the
     caller can drive counters / button enablement from one place. */
  BV.checklist = function (opts) {
    opts = opts || {};
    var sel = {};        /* selected keys (object used as a Set) */
    var anchor = null;   /* shift+click range anchor: the last-clicked KEY */
    var items = {};      /* key -> [{cb, seq}, …] bound copies, in bind order */
    var groups = {};     /* gkey -> {cb, keys()} select-all boxes */
    var seq = 0;

    function setOne(key, on) { if (on) sel[key] = true; else delete sel[key]; }

    /* Is this row on the screen the user is ranging over? "Hidden" means a
       collapsed group, a filtered-out row, or a node detached by a re-render —
       NOT merely scrolled out of view.

       checkVisibility() answers exactly that, and it is the only affordable
       way to ask: reading offsetParent inside a `content-visibility: auto`
       subtree (the library rows) forces the browser to lay that row out, so
       asking 2400 rows cost 42 SECONDS of frozen UI on a plant-scale tree.
       checkVisibility() needs no layout, and by default does not count
       content-visibility-skipped content as invisible — which is what we
       want. offsetParent stays as the fallback for older runtimes. */
    function shown(cb) {
      return cb.checkVisibility ? cb.checkVisibility() : cb.offsetParent !== null;
    }

    /* the rows as the user SEES them: render order, skipping anything hidden.
       A key with several visible copies ranges from its FIRST bound copy's
       position, so shift+click stays coherent within the list being clicked. */
    function visibleKeys() {
      var vis = [], seen = {};
      Object.keys(items).forEach(function (k) {
        items[k].forEach(function (c) {
          if (shown(c.cb)) vis.push({ k: k, seq: c.seq });
        });
      });
      vis.sort(function (a, b) { return a.seq - b.seq; });
      return vis.filter(function (e) {
        if (seen[e.k]) return false;
        seen[e.k] = true;
        return true;
      }).map(function (e) { return e.k; });
    }

    function sync() {
      Object.keys(items).forEach(function (k) {
        items[k].forEach(function (c) { c.cb.checked = !!sel[k]; });
      });
      Object.keys(groups).forEach(function (gk) {
        var g = groups[gk];
        var keys = g.keys() || [];
        var on = keys.filter(function (k) { return sel[k]; }).length;
        g.cb.checked = keys.length > 0 && on === keys.length;
        g.cb.indeterminate = on > 0 && on < keys.length;
      });
      if (opts.onChange) opts.onChange();
    }

    var api = {
      /* wire a row checkbox to a key. Returns the checkbox for chaining. */
      bind: function (cb, key) {
        /* drop copies a re-render detached; keep every live one — the same
           key may legitimately render in two places at once */
        var copies = (items[key] || []).filter(function (c) {
          return document.contains(c.cb);
        });
        copies.push({ cb: cb, seq: seq++ });
        items[key] = copies;
        if (!cb.title) cb.title = "select (shift+click selects a range)";
        cb.checked = !!sel[key];
        cb.addEventListener("click", function (e) {
          e.stopPropagation();               /* row-click actions stay separate */
          var on = cb.checked;               /* the checkbox has already toggled */
          var keys = null, a = -1, b = -1;
          if (e.shiftKey && anchor && anchor !== key) {
            keys = visibleKeys();
            a = keys.indexOf(anchor);
            b = keys.indexOf(key);
          }
          if (keys && a >= 0 && b >= 0 && a !== b) {
            var lo = Math.min(a, b), hi = Math.max(a, b);
            for (var i = lo; i <= hi; i++) setOne(keys[i], on);
          } else {
            setOne(key, on);                 /* anchor folded away -> plain toggle */
          }
          anchor = key;
          sync();
        });
        return cb;
      },

      /* wire a select-all box over keysFn()'s CURRENT keys (call-time, so the
         group tracks whatever the caller renders). gkey dedupes across
         re-renders the same way bind does. */
      group: function (cb, keysFn, gkey) {
        groups[gkey || "all"] = { cb: cb, keys: keysFn };
        cb.addEventListener("change", function () {
          var keys = keysFn() || [];
          var any = keys.some(function (k) { return sel[k]; });
          keys.forEach(function (k) { setOne(k, !any); });   /* any -> clear; none -> all */
          sync();
        });
        return cb;
      },

      has: function (key) { return !!sel[key]; },
      set: function (key, on) { setOne(key, on); },   /* no repaint — sync() after a batch */
      clear: function () { sel = {}; anchor = null; },
      selected: function () { return Object.keys(sel); },
      size: function () { return Object.keys(sel).length; },
      sync: sync,
    };
    return api;
  };
})();
