/* components/dragreorder.js - generic drag-to-reorder for items inside one or
   more drop zones. Lifted out of the overview dashboard so the same behaviour
   can be dropped onto any list of cards/rows later: drag an item by its handle,
   see a before/after marker, drop to reposition (within or across zones), get
   a callback to persist the new order.

   Items keep their identity on the node itself (e.g. dataset.cardId); this
   module only moves DOM around. It also tracks the last drop so a click that
   immediately follows a sloppy drag can be ignored (isRecentDrag()).

   opts: { zones:[el], itemSelector, handleSelector, classes, onDrop, clickGuardMs }
           zones          - the drop containers
           itemSelector   - selects a draggable item (".card[data-card-id]")
           handleSelector  - child selector for the grab handle, relative to an
                             item; wire(item) sets draggable on it
           classes        - { dragging, dropBefore, dropAfter, dropEnd } overrides
           onDrop(item, fromZone, toZone) - called after a successful move
   returns: { wire(item), isRecentDrag(), destroy() } */
(function () {
  "use strict";

  BV.dragReorder = function (opts) {
    opts = opts || {};
    var zones = opts.zones || [];
    var itemSel = opts.itemSelector || ".card";
    var handleSel = opts.handleSelector || null;
    var cls = opts.classes || {};
    var DRAGGING = cls.dragging || "dragging";
    var BEFORE = cls.dropBefore || "drop-before";
    var AFTER = cls.dropAfter || "drop-after";
    var END = cls.dropEnd || "drop-end";
    var guardMs = opts.clickGuardMs || 250;

    var dragged = null;
    var lastDragEnd = 0;
    var bound = [];

    function on(el, ev, fn) { el.addEventListener(ev, fn); bound.push([el, ev, fn]); }

    function clearMarks() {
      zones.forEach(function (z) {
        z.classList.remove(END);
        z.querySelectorAll("." + BEFORE + ", ." + AFTER).forEach(function (e) {
          e.classList.remove(BEFORE, AFTER);
        });
      });
    }

    zones.forEach(function (zone) {
      on(zone, "dragstart", function (e) {
        var item = e.target.closest(itemSel);
        if (!item || !zone.contains(item)) return;
        dragged = item;
        item.classList.add(DRAGGING);
        try {
          e.dataTransfer.setData("text/plain", item.dataset.cardId || "");
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setDragImage(item, 20, 16);
        } catch (err) { /* synthetic probe events carry no dataTransfer */ }
      });

      on(zone, "dragend", function () {
        if (dragged) dragged.classList.remove(DRAGGING);
        clearMarks();
        dragged = null;
        lastDragEnd = Date.now();
      });

      on(zone, "dragover", function (e) {
        if (!dragged) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
        clearMarks();
        var t = e.target.closest(itemSel);
        if (t && t !== dragged && zone.contains(t)) {
          var r = t.getBoundingClientRect();
          t.classList.add(e.clientY < r.top + r.height / 2 ? BEFORE : AFTER);
        } else if (!t) {
          zone.classList.add(END);
        }
      });

      on(zone, "drop", function (e) {
        if (!dragged) return;
        e.preventDefault();
        var from = dragged.parentElement;
        var t = e.target.closest(itemSel);
        if (t && t !== dragged && zone.contains(t)) {
          var r = t.getBoundingClientRect();
          var before = e.clientY < r.top + r.height / 2;
          t.parentElement.insertBefore(dragged, before ? t : t.nextSibling);
        } else if (!t) {
          zone.appendChild(dragged);
        }
        clearMarks();
        if (opts.onDrop) opts.onDrop(dragged, from, zone);
      });
    });

    return {
      /* make an item draggable by marking its handle draggable */
      wire: function (item) {
        var handle = handleSel ? item.querySelector(handleSel) : item;
        if (handle) handle.draggable = true;
      },
      isRecentDrag: function () { return Date.now() - lastDragEnd < guardMs; },
      destroy: function () {
        bound.forEach(function (b) { b[0].removeEventListener(b[1], b[2]); });
        bound = [];
      },
    };
  };
})();
