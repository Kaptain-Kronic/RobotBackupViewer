/* settings_ui.js - the ⚙ dialog (every setting in the app) + BV.uiPrefs.apply.

   ONE button, TWO tabs:
     display     - theme (one row) · interface · background
     preferences - 3d view · library · updates

   It used to be three pages behind two buttons (⚙ for behavior, 🎨 with its own
   themes/customize tabs), which put related settings three clicks apart. The app
   has 14 controls total; two categories hold them with room to spare, and a new
   setting is a new row.

   Two groupings moved with the consolidation:
     - opacity + frost are INTERFACE, not background. They skin the panels
       whether or not an effect is running; they only sat next to the effect
       sliders because that is where the sliders happened to be built.
     - the theme catalog is a picker row (theme_ui.js), not a permanent
       accordion.

   v0.98 note that still holds: content and chrome scale SEPARATELY. "text size"
   drives the data area (root font-size -> every rem in the tables); "toolbar
   size" drives header/tabs/footer via --chrome-fs. The old whole-page zoom is
   retired - it inflated the chrome together with the text until the data had no
   room, and it needed the 100vh/--app-zoom and menu-transform workarounds. */
(function () {
  "use strict";

  var CHROME_BASE_PX = 15;
  /* shop-floor friendly defaults - old eyes and young eyes both read this */
  var DEFAULT_FONT = 15;
  var DEFAULT_CHROME = 1.0;
  /* text size runs to 32px: the accessibility knob for plant-floor eyes -
     content reflows (labels wrap, dialogs grow) instead of shattering */
  var FONT_MIN = 12, FONT_MAX = 32;
  var CHROME_MIN = 85, CHROME_MAX = 160;
  /* switchable UI chrome font. "mono" keeps the classic look; "rog" uses the
     bundled Orbitron display face (web/fonts); sans/serif are zero-byte system
     stacks (the app ships offline — no webfonts). Applied only to UI chrome,
     never to data or code (those are pinned to --font-mono in css), so
     columns stay aligned. */
  var FONT_OPTIONS = [
    { id: "mono", label: "mono", css: "var(--font-mono)" },
    { id: "rog", label: "ROG", css: '"Orbitron", var(--font-mono)' },
    { id: "sans", label: "sans", css: 'system-ui, "Segoe UI", sans-serif' },
    { id: "serif", label: "serif", css: 'Georgia, "Times New Roman", serif' },
  ];
  var DEFAULT_FONT_FAMILY = "mono";
  var FX_CREDIT = "odysseus effects inspired by pewdiepie's odysseus — AGPL-3.0, combined per GPLv3 §13";

  /* one-time carry-over from the retired zoom: someone who ran at 125% mostly
     wanted comfortable chrome too - start their chrome scale there (capped;
     content size is the text-size knob's job now) */
  function chromeScale(s) {
    if (s.chrome_scale) return s.chrome_scale;
    if (s.ui_scale && s.ui_scale !== 1) return Math.min(s.ui_scale, 1.25);
    return DEFAULT_CHROME;
  }

  /* frosted surfaces, TWO knobs: opacity (how see-through panels go, the
     --panel/--glass rgba fills) and frost (how much backdrop BLUR the glass
     surfaces get, via --frost -> --glass-blur). The fills are computed in JS
     and set inline on <html>: the CSS-only route (color-mix over a calc of
     a var) is NOT reliably re-resolved by Chromium on existing elements
     when the var changes - the slider looked dead. Runs on every
     uiPrefs.apply AND on theme switches (colors move under the fills). */
  var _lastOp = 0, _lastBlur = 0;
  function applyGlass(op, blur) {
    _lastOp = op;
    _lastBlur = blur;
    var root = document.documentElement;
    root.style.setProperty("--frost", String(blur));   /* drives --glass-blur */
    /* Content surfaces opt INTO the backdrop blur via this class (see the
       html.frosted rules in components.css). A backdrop-filter costs a
       compositing layer per element even when it resolves to blur(0), so at
       frost 0 a plant PC pays nothing for a feature it is not using. */
    root.classList.toggle("frosted", blur > 0);
    /* Frost is frosted GLASS, and a blur behind an opaque surface is invisible —
       so the frost slider on its own did nothing at all, no matter how far you
       pushed it, unless you happened to raise opacity too. It now carries a
       FLOOR of translucency (100% frost ≈ 50% opacity) so it can show its own
       effect; a higher opacity setting still wins. */
    var eff = Math.max(op, blur * 0.5);
    if (!eff) {
      /* exactly the solid original: fall back to the stylesheet defaults */
      root.style.removeProperty("--panel");
      root.style.removeProperty("--overlay");
      root.style.removeProperty("--glass");
      return;
    }
    /* one shared hex reader: this used to carry its own 3-or-6-digit regex and
       a hardcoded "323437" on a miss, so an 8-digit theme color silently
       frosted the panels in the DEFAULT bg instead of the theme's. */
    function rgba(varName, alpha) {
      var c = BV.varRgb(varName);
      if (!c) return "";   /* not a hex we can read - leave the stylesheet's own value */
      return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + alpha.toFixed(3) + ")";
    }
    /* TWO curves, because the two kinds of surface sit over completely
       different backdrops and one curve cannot serve both:

         --panel   content over the PAGE (cards, tables, rows). Its backdrop is
                   --bg plus a sparse effect, so it can go far see-through
                   before anything becomes hard to read.
         --overlay floating UI over CONTENT (dialogs, menus, the theme picker).
                   Its backdrop is text, so the same alpha reads as a window you
                   can see the settings through. It stops at ~25%.

       eff, not op: the frost floor above has to reach the fills, or raising
       frost alone leaves every surface fully opaque and invisible-blurred —
       which is exactly what it did while these two lines still read `op`. */
    var panel = rgba("--bg2", 1 - eff * 0.60);
    var overlay = rgba("--bg2", 1 - eff * 0.30);
    var glass = rgba("--bg", 1 - eff * 0.55);
    if (panel) root.style.setProperty("--panel", panel);
    else root.style.removeProperty("--panel");
    if (overlay) root.style.setProperty("--overlay", overlay);
    else root.style.removeProperty("--overlay");
    if (glass) root.style.setProperty("--glass", glass);
    else root.style.removeProperty("--glass");
  }
  /* theme switches (including the editor's live preview) move --bg/--bg2 */
  BV.state.on("theme", function () { applyGlass(_lastOp, _lastBlur); });

  /* ---- row builders ---- */
  function section(into, title) {
    into.appendChild(BV.el("div", { class: "set-head" }, BV.esc(title)));
  }
  function row(into, label) {
    var r = BV.el("div", { class: "set-row" });
    r.appendChild(BV.el("span", { class: "name" }, BV.esc(label)));
    into.appendChild(r);
    return r;
  }
  function segRow(into, label, values, current, fmt, onPick) {
    var r = row(into, label);
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
    r.appendChild(seg);
  }

  /* A slider over RAW values; fmt renders the readout.

     opts.onRelease: commit on let-go instead of on every tick. The two SCALE
     sliders need it — they are the only controls whose own geometry is a
     function of the value they set. The dialog is sized in rem and centred, so
     applying live grew the track ~170px and walked it ~120px left mid-drag
     (measured), and a range input re-reads its box on every pointermove: the
     value fought the pointer. The readout still tracks the drag, so you see
     where you are; the app rescales when you let go. */
  function sliderRow(into, label, min, max, step, value, fmt, onCommit, opts) {
    opts = opts || {};
    var r = row(into, label);
    var holder = BV.el("div", { class: "range-wrap" });
    var val = BV.el("span", { class: "range-val" }, fmt(value));
    var input = BV.el("input", {
      type: "range", min: String(min), max: String(max), step: String(step),
      value: String(value),
    });
    input.addEventListener("input", function (e) {
      var v = Number(e.target.value);
      val.textContent = fmt(v);
      if (!opts.onRelease) onCommit(v);
    });
    if (opts.onRelease) {
      /* change fires on let-go, and once per press for keyboard arrows */
      input.addEventListener("change", function (e) { onCommit(Number(e.target.value)); });
    }
    holder.appendChild(input);
    holder.appendChild(val);
    r.appendChild(holder);
  }
  function pct(v) { return v + "%"; }
  function deg(v) { return v + "°"; }

  BV.uiPrefs = {
    FONT_OPTIONS: FONT_OPTIONS,
    DEFAULT_FONT: DEFAULT_FONT,
    DEFAULT_FONT_FAMILY: DEFAULT_FONT_FAMILY,
    chromeScale: chromeScale,

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
      /* the chrome bars: frosted glass by default, opaque --bg when toggled off */
      document.documentElement.classList.toggle("chrome-solid", settings.chrome_frost === false);
      /* frosted surfaces: 0/0 = the solid original. A pre-split `frost`
         value seeds opacity once, so an existing look carries over. */
      var op = settings.glass_op != null ? Number(settings.glass_op) : (Number(settings.frost) || 0);
      applyGlass(Math.min(0.85, Math.max(0, op || 0)),
                 Math.min(1, Math.max(0, Number(settings.frost) || 0)));
      if (BV.bgfx) BV.bgfx.apply(settings);   /* idempotent - only reacts to changes */
      BV.state.emit("uiprefs", settings);
    },

    /* tab = "display" (default) | "preferences" */
    modal: function (tab) {
      var s = BV.state.settings || {};
      BV.state.settings = s;
      var themeRow = null;

      /* sliders fire per tick, so every write is debounced per key */
      var writers = {};
      function persist(key, v) {
        s[key] = v;
        if (!writers[key]) writers[key] = BV.debounce(function () {
          BV.api.call("set_setting", key, s[key]).catch(function () {});
        }, 400);
        writers[key]();
      }
      function applyPrefs() { BV.uiPrefs.apply(s); }

      var body = BV.el("div");
      var seg = BV.el("div", { class: "seg set-tabs" });
      var slot = BV.el("div");
      body.appendChild(seg);
      body.appendChild(slot);

      /* ---- display ---- */
      function buildDisplay(into) {
        section(into, "theme");
        themeRow = BV.themeUI.row(into, {
          closeHost: function () { modal.close(true); },
        });

        section(into, "interface");
        segRow(into, "font", FONT_OPTIONS.map(function (o) { return o.id; }),
          s.font_family || DEFAULT_FONT_FAMILY,
          function (id) {
            var o = FONT_OPTIONS.find(function (x) { return x.id === id; });
            return o ? o.label : id;
          },
          function (id) { persist("font_family", id); applyPrefs(); });
        segRow(into, "borders", [true, false], s.edges !== false,
          function (v) { return v ? "on" : "off"; },
          function (v) { persist("edges", v); applyPrefs(); });
        /* the top/bottom bars: frosted glass by default, opaque on request */
        segRow(into, "frosted chrome", [true, false], s.chrome_frost !== false,
          function (v) { return v ? "on" : "off"; },
          function (v) { persist("chrome_frost", v); applyPrefs(); });
        sliderRow(into, "text size", FONT_MIN, FONT_MAX, 1, s.font_size || DEFAULT_FONT,
          function (v) { return v + "px"; },
          function (v) { persist("font_size", v); applyPrefs(); },
          { onRelease: true });
        /* "toolbar size" in the UI (plainer than "chrome scale"); the persisted
           key stays chrome_scale - no migration */
        sliderRow(into, "toolbar size", CHROME_MIN, CHROME_MAX, 5,
          Math.round(chromeScale(s) * 100), pct,
          function (v) { persist("chrome_scale", v / 100); applyPrefs(); },
          { onRelease: true });
        /* two glass knobs: opacity = how see-through panels go (0 = the solid
           original), frost = how much the glass surfaces BLUR */
        var op0 = s.glass_op != null ? Number(s.glass_op) : (Number(s.frost) || 0);
        sliderRow(into, "panel opacity", 0, 85, 5, Math.round((op0 || 0) * 100), pct,
          function (v) { persist("glass_op", v / 100); applyPrefs(); });
        sliderRow(into, "frost", 0, 100, 5, Math.round((Number(s.frost) || 0) * 100), pct,
          function (v) { persist("frost", v / 100); applyPrefs(); });

        section(into, "background");
        var fxRow = row(into, "effect");
        var fxBtn = BV.el("button", { class: "btn fx-pick" }, BV.esc(BV.bgfx.activeName()) + " ▾");
        fxRow.appendChild(fxBtn);
        fxBtn.addEventListener("click", function () {
          BV.menu(fxBtn, BV.bgfx.EFFECTS.map(function (t) {
            return {
              label: t.name + (t.id === BV.bgfx.activeId ? "  ✓" : ""),
              onClick: function () {
                BV.bgfx.set(t.id, true);
                fxBtn.textContent = t.name + " ▾";
              },
            };
          }));
        });
        sliderRow(into, "intensity", 10, 100, 5, Math.round(BV.bgfx.intensity * 100), pct,
          function (v) { BV.bgfx.tune({ intensity: v / 100 }, true); });
        sliderRow(into, "size", 50, 400, 10, Math.round(BV.bgfx.size * 100), pct,
          function (v) { BV.bgfx.tune({ size: v / 100 }, true); });
        sliderRow(into, "speed", 10, 300, 10, Math.round(BV.bgfx.speed * 100), pct,
          function (v) { BV.bgfx.tune({ speed: v / 100 }, true); });
        /* density and variance fix counts and per-element properties when the
           effect is seeded, so they cannot be applied mid-flight — they ask
           for a re-seed, and sliderRow already commits on release rather than
           per drag pixel, which is the whole reason that is not a slideshow */
        sliderRow(into, "density", 25, BV.bgfx.denseMax(), 5,
          Math.round(BV.bgfx.density * 100), pct,
          function (v) { BV.bgfx.tune({ density: v / 100, reseed: true }, true); });
        sliderRow(into, "variance", 0, 200, 5, Math.round(BV.bgfx.spread * 100), pct,
          function (v) { BV.bgfx.tune({ spread: v / 100, reseed: true }, true); });
        sliderRow(into, "hue drift", 0, 180, 5, Math.round(BV.bgfx.hue), deg,
          function (v) { BV.bgfx.tune({ hue: v }, true); });

        /* the active effect's OWN dials. Reynolds' three weights mean nothing
           to a reaction-diffusion grid and its feed rate means nothing to a
           flock, so these belong to the effect that owns them rather than to
           a global panel that would have to pretend otherwise. */
        var ownHost = BV.el("div", { class: "fx-own" });
        into.appendChild(ownHost);
        buildFxParams(ownHost);
        fxBtn.addEventListener("click", function () {
          setTimeout(function () { buildFxParams(ownHost); }, 0);
        });
        into.appendChild(BV.el("div", { class: "acc-credit" }, BV.esc(FX_CREDIT)));
      }

      function buildFxParams(host) {
        host.innerHTML = "";
        var spec = BV.bgfx.paramSpec();
        if (!spec.length) return;
        section(host, BV.bgfx.activeName() + " settings");
        var cur = BV.bgfx.params();
        spec.forEach(function (p) {
          var unit = p.unit === "%" ? pct : function (v) { return v + (p.unit || ""); };
          sliderRow(host, p.name, p.lo, p.hi, p.step,
            cur[p.k] != null ? cur[p.k] : p.def, unit,
            function (v) { BV.bgfx.setParam(p.k, v, true); });
        });
        var rst = BV.el("button", { class: "btn" }, "reset");
        rst.addEventListener("click", function () {
          spec.forEach(function (p) { BV.bgfx.setParam(p.k, p.def, true); });
          buildFxParams(host);
        });
        row(host, "").appendChild(rst);
      }

      /* ---- preferences ---- */
      function buildPrefs(into) {
        section(into, "3d view");
        segRow(into, "invert rotate x", [false, true], s.v3_invert_x === true,
          function (v) { return v ? "inverted" : "normal"; },
          function (v) {
            s.v3_invert_x = v;
            BV.api.call("set_setting", "v3_invert_x", v).catch(function () {});
          });
        segRow(into, "invert rotate y", [false, true], s.v3_invert_y === true,
          function (v) { return v ? "inverted" : "normal"; },
          function (v) {
            s.v3_invert_y = v;
            BV.api.call("set_setting", "v3_invert_y", v).catch(function () {});
          });

        section(into, "library");
        /* the single root that is both the FTP backup destination and the tree
           the app scans to build the library. Changing it rescans. */
        var pathRow = row(into, "library folder");
        var pathWrap = BV.el("div", { class: "set-path" });
        var pathVal = BV.el("span", { class: "set-path-val dim" }, "…");
        var changeBtn = BV.el("button", { class: "btn" }, "change…");
        pathWrap.appendChild(pathVal);
        pathWrap.appendChild(changeBtn);
        pathRow.appendChild(pathWrap);

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

        /* ---- the CV-X simulator's flat folder ----
           The simulator takes ONE base path and lists the workspace folders
           directly inside it - it will not walk the library's plant/line/dated
           tree. So this is an explicit export: pick cameras, and their latest
           workspaces land side by side in here. The path is a copy button
           because its whole job is to be pasted into the simulator. */
        section(into, "cv-x simulator");
        var simRow = row(into, "simulator folder");
        var simWrap = BV.el("div", { class: "set-path" });
        var simVal = BV.el("span", { class: "set-path-val dim" }, "…");
        var simCopy = BV.el("button", { class: "btn", title: "copy this path for the simulator" }, "copy");
        var simChange = BV.el("button", { class: "btn" }, "change…");
        var simLoad = BV.el("button", { class: "btn" }, "load cameras…");
        simWrap.appendChild(simVal);
        simWrap.appendChild(simCopy);
        simWrap.appendChild(simChange);
        simWrap.appendChild(simLoad);
        simRow.appendChild(simWrap);

        var simPath = "";
        function showSim(p) {
          simPath = p || "";
          simVal.textContent = simPath || "(default)";
          simVal.title = simPath;
        }
        BV.api.call("get_sim_root").then(function (r) { showSim(r && r.path); }).catch(function () {});

        simCopy.addEventListener("click", function () {
          if (simPath) BV.copyText(simPath, "path copied — paste it as the simulator's base path");
        });
        simChange.addEventListener("click", function () {
          BV.api.call("pick_sim_root").then(function (p) {
            if (!p) return null;
            return BV.api.call("set_sim_root", p).then(function (r) {
              showSim(r && r.path);
              BV.toast("simulator folder set");
            });
          }).catch(function (e) { BV.toast(e.message); });
        });
        /* the picker takes over the one modal root, so hand it a way back here */
        simLoad.addEventListener("click", function () {
          BV.simExport(function () { BV.uiPrefs.modal("preferences"); });
        });

        section(into, "updates");
        /* the boot-time github ping runs only in the packaged exe (source runs
           stay offline); this switch turns even that off. The about box's
           manual check works regardless. */
        segRow(into, "check on startup", [true, false], s.update_check !== false,
          function (v) { return v ? "auto" : "off"; },
          function (v) {
            s.update_check = v;
            BV.api.call("set_setting", "update_check", v).catch(function () {});
          });
      }

      var TABS = [
        { id: "display", build: buildDisplay },
        { id: "preferences", build: buildPrefs },
      ];
      var activeTab = tab === "preferences" ? "preferences" : "display";

      function switchTab(next) {
        activeTab = next;
        /* the theme row dies with the tab that owned it */
        if (themeRow) { themeRow.closePanel(); themeRow = null; }
        seg.querySelectorAll("button").forEach(function (b) {
          b.classList.toggle("active", b.dataset.tab === next);
        });
        slot.innerHTML = "";
        TABS.find(function (t) { return t.id === next; }).build(slot);
      }
      TABS.forEach(function (t) {
        var b = BV.el("button", { class: t.id === activeTab ? "active" : "" }, t.id);
        b.dataset.tab = t.id;
        b.addEventListener("click", function () { switchTab(t.id); });
        seg.appendChild(b);
      });

      var modal = BV.modal("settings", body, {
        /* the background controls live in here, and you cannot tune an effect
           you cannot see: no scrim, no backdrop blur, for this dialog only */
        seeThrough: true,
        /* the picker panel lives on <html> so it can't be clipped, which means
           it outlives this dialog unless we take it down with us */
        onClose: function () { if (themeRow) themeRow.closePanel(); },
      });
      modal.el.classList.add("settings-win");

      switchTab(activeTab);
    },
  };
})();
