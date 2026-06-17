/* components/segmented.js - the .seg sub-tab control (the little pill-row that
   switches io categories, register kinds, dcs reports, frame groups, ...).

   Built once so every tab's sub-tabs look and behave the same. It supports two
   driving styles:

     - uncontrolled (default): clicking a button makes it the active one and
       fires onChange. Good for in-place switches (frame groups, alarm files).
     - controlled (opts.controlled, or any multi-select): clicking only fires
       onChange; the owner decides what becomes active and calls setActive().
       Good for hash-routed sub-tabs (registers, dcs) and for io's in/out pair
       whose "at least one stays on / vs forces a single side" rule lives in io.

   options: [{ id, label, count?, title? }]
   opts:    { value, onChange(id, btn), multi, controlled, idPrefix }
            value     - id (or array of ids when multi) to start active
            idPrefix  - element id given to each button (idPrefix + option.id),
                        e.g. "iocat-" so io keeps its #iocat-group selector
   returns: { el, buttons, setActive(idOrIds), get() } */
(function () {
  "use strict";

  BV.segmented = function (options, opts) {
    opts = opts || {};
    var prefix = opts.idPrefix || "";
    var controlled = !!opts.multi || !!opts.controlled;
    var seg = BV.el("div", { class: "seg" });
    var buttons = {};

    var api = {
      el: seg,
      buttons: buttons,
      setActive: function (val) {
        var ids = (Array.isArray(val) ? val : [val]).map(String);
        Object.keys(buttons).forEach(function (id) {
          buttons[id].classList.toggle("active", ids.indexOf(id) >= 0);
        });
      },
      get: function () {
        return Object.keys(buttons).filter(function (id) {
          return buttons[id].classList.contains("active");
        });
      },
    };

    (options || []).forEach(function (o) {
      var html = BV.esc(o.label);
      if (o.count !== undefined && o.count !== null && o.count !== "") {
        html += '<span class="cnt">' + BV.esc(o.count) + "</span>";
      }
      var attrs = {};
      if (prefix) attrs.id = prefix + o.id;
      if (o.title) attrs.title = o.title;
      var b = BV.el("button", attrs, html);
      b.addEventListener("click", function () {
        if (!controlled) api.setActive(o.id);
        if (opts.onChange) opts.onChange(o.id, b);
      });
      buttons[o.id] = b;
      seg.appendChild(b);
    });

    if (opts.value !== undefined) api.setActive(opts.value);
    return api;
  };
})();
