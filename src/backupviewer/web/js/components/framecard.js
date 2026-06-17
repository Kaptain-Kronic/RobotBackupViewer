/* components/framecard.js - the vertical tool/uframe card (a title, optional
   status pills, an optional identity subtitle, the x/y/z/w/p/r list, and an
   optional config line). One card, used by the frames tab and the dcs frames
   section so they look identical; each caller supplies the data it has (the
   frames tab passes a comment + an "active" pill, dcs passes a signal + an
   "unused"/status pill - the styling is the same either way).

   The caller pre-formats axis values (precision differs per source) and applies
   the .vsdiff class itself after a side-by-side compare.

   opts: { title, pills:[[text, variant], ...], subtitle, axes:[[label, value], ...],
           config, uninitialized, class } */
(function () {
  "use strict";

  BV.frameCard = function (opts) {
    opts = opts || {};
    var c = BV.el("div", { class: "card frame-card" + (opts.class ? " " + opts.class : "") });

    var pills = (opts.pills || []).filter(Boolean).map(function (p) {
      return BV.pill(p[0], p[1]);
    }).join(" ");
    var h = "<h3>" + BV.esc(opts.title) + (pills ? " " + pills : "") + "</h3>";

    if (opts.subtitle) {
      h += '<div class="frame-sub">' + BV.esc(opts.subtitle) + "</div>";
    }
    if (opts.uninitialized) {
      c.innerHTML = h + '<div class="dim" style="font-size:.8rem">uninitialized</div>';
      return c;
    }

    h += '<dl class="kv">';
    (opts.axes || []).forEach(function (a) {
      h += "<dt>" + BV.esc(a[0]) + "</dt><dd>" + BV.esc(a[1] == null ? "" : a[1]) + "</dd>";
    });
    h += "</dl>";
    if (opts.config) h += '<div class="cfg">config : ' + BV.esc(opts.config) + "</div>";

    c.innerHTML = h;
    return c;
  };
})();
