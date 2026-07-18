/* settings_ui.js - the ⚙ settings panel + BV.uiPrefs.apply.
   v0.98: content and chrome scale SEPARATELY. "text size" drives the data area
   (root font-size -> every rem in the tables); "chrome scale" drives the
   header/tabs/footer via --chrome-fs. The old whole-page zoom is retired - it
   inflated the chrome together with the text until the data had no room, and
   it needed the 100vh/--app-zoom and menu-transform workarounds. */
(function () {
  "use strict";

  /* caps raised for the shop floor (v1.0): 18px text / 125% chrome wasn't
     enough on high-DPI laptops viewed at arm's length. The seg row wraps. */
  var FONT_SIZES = [12, 13, 14, 15, 16, 18, 20, 22, 24];
  var CHROME_SCALES = [0.85, 1.0, 1.1, 1.25, 1.4, 1.6];
  var CHROME_BASE_PX = 15;
  /* shop-floor friendly defaults - old eyes and young eyes both read this */
  var DEFAULT_FONT = 15;
  var DEFAULT_CHROME = 1.0;
  /* switchable UI font. "mono" keeps the classic look; "rog" uses the bundled
     Orbitron display face (web/fonts) - applied only to UI chrome, never to data
     or code (those are pinned to --font-mono in css), so columns stay aligned. */
  var FONT_OPTIONS = [
    { id: "mono", label: "mono", css: "var(--font-mono)" },
    { id: "rog", label: "ROG", css: '"Orbitron", var(--font-mono)' },
  ];
  var DEFAULT_FONT_FAMILY = "mono";

  /* one-time carry-over from the retired zoom: someone who ran at 125% mostly
     wanted comfortable chrome too - start their chrome scale there (capped;
     content size is the text-size knob's job now) */
  function chromeScale(s) {
    if (s.chrome_scale) return s.chrome_scale;
    if (s.ui_scale && s.ui_scale !== 1) return Math.min(s.ui_scale, 1.25);
    return DEFAULT_CHROME;
  }

  BV.uiPrefs = {
    apply: function (settings) {
      var fs = settings.font_size || DEFAULT_FONT;
      var ff = settings.font_family || DEFAULT_FONT_FAMILY;
      var fopt = FONT_OPTIONS.find(function (o) { return o.id === ff; }) || FONT_OPTIONS[0];
      document.documentElement.style.setProperty("--font", fopt.css);
      document.documentElement.style.fontSize = fs + "px";       /* content (rem base) */
      document.body.style.fontSize = fs + "px";
      document.documentElement.style.setProperty(
        "--chrome-fs", (CHROME_BASE_PX * chromeScale(settings)) + "px");
      /* the whole-page zoom is retired: clear any leftover inline zoom state */
      document.body.style.zoom = "";
      document.documentElement.style.removeProperty("--app-zoom");
      /* accent panel borders on by default; "off" flattens the UI (see base.css .no-edges) */
      document.documentElement.classList.toggle("no-edges", settings.edges === false);
      BV.state.emit("uiprefs", settings);
    },

    modal: function () {
      var s = BV.state.settings || {};
      var body = BV.el("div");

      function section(title) {
        body.appendChild(BV.el("div", { class: "set-head" }, BV.esc(title)));
      }

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

      /* ---- appearance ---- */
      section("appearance");
      var themeRow = BV.el("div", { class: "set-row" });
      themeRow.appendChild(BV.el("span", { class: "name" }, "theme"));
      var themeWrap = BV.el("div", { class: "set-path" });
      var active = (BV.theme.themes || []).find(function (t) { return t.id === BV.theme.activeId; });
      themeWrap.appendChild(BV.el("span", { class: "set-path-val dim" },
        BV.esc((active && active.name) || BV.theme.activeId || "—")));
      var themeBtn = BV.el("button", { class: "btn" }, "choose…");
      themeWrap.appendChild(themeBtn);
      themeRow.appendChild(themeWrap);
      body.appendChild(themeRow);

      segRow("font", FONT_OPTIONS.map(function (o) { return o.id; }),
        s.font_family || DEFAULT_FONT_FAMILY,
        function (id) {
          var o = FONT_OPTIONS.find(function (x) { return x.id === id; });
          return o ? o.label : id;
        },
        function (id) {
          s.font_family = id;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "font_family", id).catch(function () {});
        });

      segRow("borders", [true, false], s.edges !== false,
        function (v) { return v ? "on" : "off"; },
        function (v) {
          s.edges = v;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "edges", v).catch(function () {});
        });

      /* ---- text & scale ---- */
      section("text & scale");
      segRow("text size", FONT_SIZES, s.font_size || DEFAULT_FONT,
        function (v) { return v + "px"; },
        function (v) {
          s.font_size = v;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "font_size", v).catch(function () {});
        });

      segRow("chrome scale", CHROME_SCALES, chromeScale(s),
        function (v) { return Math.round(v * 100) + "%"; },
        function (v) {
          s.chrome_scale = v;
          BV.uiPrefs.apply(s);
          BV.api.call("set_setting", "chrome_scale", v).catch(function () {});
        });

      /* ---- 3d view ---- */
      section("3d view");
      segRow("invert rotate x", [false, true], s.v3_invert_x === true,
        function (v) { return v ? "inverted" : "normal"; },
        function (v) {
          s.v3_invert_x = v;
          BV.api.call("set_setting", "v3_invert_x", v).catch(function () {});
        });
      segRow("invert rotate y", [false, true], s.v3_invert_y === true,
        function (v) { return v ? "inverted" : "normal"; },
        function (v) {
          s.v3_invert_y = v;
          BV.api.call("set_setting", "v3_invert_y", v).catch(function () {});
        });

      /* ---- library ---- */
      section("library");
      /* the single root that is both the FTP backup destination and the tree
         the app scans to build the library. Changing it rescans. */
      var pathRow = BV.el("div", { class: "set-row" });
      pathRow.appendChild(BV.el("span", { class: "name" }, "library folder"));
      var pathWrap = BV.el("div", { class: "set-path" });
      var pathVal = BV.el("span", { class: "set-path-val dim" }, "…");
      var changeBtn = BV.el("button", { class: "btn" }, "change…");
      pathWrap.appendChild(pathVal);
      pathWrap.appendChild(changeBtn);
      pathRow.appendChild(pathWrap);
      body.appendChild(pathRow);

      function showPath(p) { pathVal.textContent = p || "(default)"; pathVal.title = p || ""; }
      BV.api.call("get_library_root").then(function (r) { showPath(r && r.path); }).catch(function () {});
      changeBtn.addEventListener("click", function () {
        BV.api.call("pick_library_root").then(function (p) {
          if (!p) return null;
          return BV.api.call("set_library_root", p).then(function (r) {
            showPath(r && r.path);
            BV.toast("library folder set — rescanning…");
            return BV.api.call("lib_rescan").then(function () { BV.toast("library updated"); });
          });
        }).catch(function (e) { BV.toast(e.message); });
      });

      BV.state.settings = s;
      var m = BV.modal("settings", body);
      themeBtn.addEventListener("click", function () {
        m.close();               /* the picker owns #modal-root next */
        BV.theme.picker();
      });
    },
  };
})();
