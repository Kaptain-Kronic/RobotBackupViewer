/* settings_ui.js - font size + ui scale, persisted via the settings api.
   "too little information ... in too small of font" -> both knobs live here. */
(function () {
  "use strict";

  var FONT_SIZES = [12, 13, 14, 15, 16, 18];
  var SCALES = [0.85, 1.0, 1.1, 1.25, 1.4];
  /* shop-floor friendly defaults - old eyes and young eyes both read this */
  var DEFAULT_FONT = 15;
  var DEFAULT_SCALE = 1.1;

  BV.uiPrefs = {
    apply: function (settings) {
      var fs = settings.font_size || DEFAULT_FONT;
      var sc = settings.ui_scale || DEFAULT_SCALE;
      document.documentElement.style.fontSize = fs + "px";
      document.body.style.fontSize = fs + "px";
      document.body.style.zoom = sc;
      /* #app divides its 100vh by this so the layout still fits the window
         exactly under zoom (vh units don't compensate for zoom) */
      document.documentElement.style.setProperty("--app-zoom", sc);
      BV.state.emit("uiprefs", settings);
    },

    modal: function () {
      var s = BV.state.settings || {};
      var body = BV.el("div");

      function segRow(label, values, current, fmt, onPick) {
        var rowEl = BV.el("div", { class: "set-row" });
        rowEl.appendChild(BV.el("span", { class: "name" }, BV.esc(label)));
        var seg = BV.el("div", { class: "seg" });
        values.forEach(function (v) {
          var b = BV.el("button", { class: v === current ? "active" : "" }, fmt(v));
          b.addEventListener("click", function () {
            seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
            b.classList.add("active");
            onPick(v);
          });
          seg.appendChild(b);
        });
        rowEl.appendChild(seg);
        body.appendChild(rowEl);
      }

      segRow("font size", FONT_SIZES, s.font_size || DEFAULT_FONT,
        function (v) { return v + "px"; },
        function (v) {
          s.font_size = v;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "font_size", v).catch(function () {});
        });

      segRow("ui scale", SCALES, s.ui_scale || DEFAULT_SCALE,
        function (v) { return Math.round(v * 100) + "%"; },
        function (v) {
          s.ui_scale = v;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "ui_scale", v).catch(function () {});
        });

      BV.state.settings = s;
      BV.modal("display", body);
    },
  };
})();
