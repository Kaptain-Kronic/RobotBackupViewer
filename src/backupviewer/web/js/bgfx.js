/* bgfx.js - animated background effects behind the app chrome.
   A fixed canvas (plus a CSS-driven layer for the gradient looks) sits at
   z-index -1: above the root background, below all in-flow content, so
   panels and tables naturally mask it. Every effect tints itself from the
   LIVE theme vars each frame - switching themes recolors the motion
   instantly, same as every other surface. Off by default; the choice
   persists via set_setting like the rest of the prefs.

   The "odysseus" section is inspired by the background patterns in
   PewDiePie's Odysseus workspace (github.com/pewdiepie-archdaemon/odysseus,
   AGPL-3.0) - re-expressed here for accent-var theming and the probe
   environment. GPLv3 §13 permits combining AGPLv3-covered work with this
   app; the theme window's background section credits the source like the
   theme packs do.

   Probe environment note: the hidden-window probe has no
   requestAnimationFrame - effects must build without animating and never
   throw. Honor prefers-reduced-motion the same way: one static frame. */
(function () {
  "use strict";

  /* ---- live theme color taps ----
     Parsed lazily and re-parsed only when the computed string changes, so
     per-frame reads stay cheap and a theme switch recolors the next frame. */
  var _varCache = {};
  function rgbOf(varName, fallback) {
    var raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    var hit = _varCache[varName];
    if (hit && hit.raw === raw) return hit.rgb;
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(raw);
    var h = m ? m[1] : fallback;
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var rgb = [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    _varCache[varName] = { raw: raw, rgb: rgb };
    return rgb;
  }
  /* the intensity slider scales every effect alpha through here; the size
     slider scales geometry at each effect's natural dimension via SZ() */
  function I() { return BV.bgfx.intensity; }
  function SZ() { return BV.bgfx.size; }
  function accent(a) {
    var c = rgbOf("--accent", "e2b714");
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (a * I()) + ")";
  }
  /* hot cores (embers, star heads): the accent pushed toward white */
  function accentHot(a) {
    var c = rgbOf("--accent", "e2b714");
    return "rgba(" + Math.min(255, c[0] + 90) + "," + Math.min(255, c[1] + 90) + ","
      + Math.min(255, c[2] + 90) + "," + (a * I()) + ")";
  }
  /* translucent slice of the page bg - the fade wash trail effects paint with */
  function bgWash(a) {
    var c = rgbOf("--bg", "323437");
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }

  /* ---- canvas plumbing ---- */
  var _canvas = null, _ctx = null, _css = null;
  var W = 0, H = 0, _step = null, _raf = 0;
  var HAS_RAF = typeof window.requestAnimationFrame === "function";
  var REDUCED = !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function ensureCss() {
    if (_css) return;
    _css = document.createElement("div");
    _css.id = "bgfx-css";
    _css.setAttribute("aria-hidden", "true");
    document.body.insertBefore(_css, document.body.firstChild);
    syncTuning();
  }
  /* the CSS-driven looks read the sliders through a var (canvas effects read
     I()/SZ() live per frame instead) */
  function syncTuning() {
    if (_css) _css.style.setProperty("--bgfx-i", String(BV.bgfx.intensity));
  }
  function ensureCanvas() {
    if (_canvas) return;
    _canvas = document.createElement("canvas");
    _canvas.id = "bgfx-canvas";
    _canvas.setAttribute("aria-hidden", "true");
    document.body.insertBefore(_canvas, document.body.firstChild);
    _ctx = _canvas.getContext("2d");
    sizeCanvas();
  }
  function sizeCanvas() {
    /* draw in CSS pixels; back the store at devicePixelRatio (capped - the
       effects are soft glows, 2x is plenty and 3x laptops shouldn't pay) */
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth; H = window.innerHeight;
    _canvas.width = Math.max(1, Math.round(W * dpr));
    _canvas.height = Math.max(1, Math.round(H * dpr));
    _ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function dropLayers() {
    if (_canvas) { _canvas.remove(); _canvas = null; _ctx = null; }
    if (_css) { _css.remove(); _css = null; }
  }

  function tick() { _raf = requestAnimationFrame(tick); if (_step) _step(); }
  function start() {
    if (!_step) return;
    if (!HAS_RAF || REDUCED) { _step(); return; }  /* one honest static frame */
    cancelAnimationFrame(_raf);
    tick();
  }
  function stop() { if (HAS_RAF) cancelAnimationFrame(_raf); }

  /* offscreen windows must not burn the shop laptop's GPU */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
    else start();
  });
  window.addEventListener("resize", function () {
    if (!_canvas) return;
    sizeCanvas();
    var t = byId(BV.bgfx.activeId);
    if (t && t.make) _step = t.make();   /* re-seed for the new geometry */
  });

  /* ---- the effects ----
     Each entry: id, name (picker label, lowercase), cat, and for canvas
     effects a make() returning the per-frame draw. cls names a CSS class on
     the #bgfx-css layer (the two gradient looks, synapse's grid floor). */

  function fxParticles() {
    var N = 64, LINK = 130, pts = [], i, j;
    for (i = 0; i < N; i++) pts.push({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.45, vy: (Math.random() - 0.5) * 0.45,
    });
    return function () {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var p = pts[i];
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0) p.x += W; if (p.x > W) p.x -= W;
        if (p.y < 0) p.y += H; if (p.y > H) p.y -= H;
      }
      _ctx.fillStyle = accent(0.7);
      for (i = 0; i < N; i++) {
        _ctx.beginPath(); _ctx.arc(pts[i].x, pts[i].y, 1.6 * SZ(), 0, 7); _ctx.fill();
      }
      for (i = 0; i < N; i++) for (j = i + 1; j < N; j++) {
        var dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d2 = dx * dx + dy * dy;
        if (d2 < LINK * LINK) {
          _ctx.strokeStyle = accent((1 - Math.sqrt(d2) / LINK) * 0.22);
          _ctx.beginPath(); _ctx.moveTo(pts[i].x, pts[i].y);
          _ctx.lineTo(pts[j].x, pts[j].y); _ctx.stroke();
        }
      }
    };
  }

  function fxStarfield() {
    var N = 180, stars = [], i;
    function seed(s) { s.x = (Math.random() - 0.5) * W; s.y = (Math.random() - 0.5) * H; s.z = Math.random() * W; }
    for (i = 0; i < N; i++) { var s = {}; seed(s); stars.push(s); }
    return function () {
      _ctx.fillStyle = bgWash(0.5);          /* short motion trails */
      _ctx.fillRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var st = stars[i];
        st.z -= 2.2;
        if (st.z < 1) { seed(st); st.z = W; }
        var k = 128 / st.z, x = st.x * k + W / 2, y = st.y * k + H / 2;
        if (x < 0 || x >= W || y < 0 || y >= H) { seed(st); st.z = W; continue; }
        var a = 1 - st.z / W;
        _ctx.fillStyle = accentHot(a * 0.8);
        _ctx.beginPath(); _ctx.arc(x, y, a * 2.2 * SZ(), 0, 7); _ctx.fill();
      }
    };
  }

  function fxWaves() {
    var layers = [
      { amp: 26, freq: 0.006, speed: 0.018, off: 0.62, a: 0.10 },
      { amp: 34, freq: 0.004, speed: 0.012, off: 0.70, a: 0.07 },
      { amp: 46, freq: 0.003, speed: 0.008, off: 0.78, a: 0.05 },
    ];
    var t = 0;
    return function () {
      _ctx.clearRect(0, 0, W, H); t++;
      for (var li = 0; li < layers.length; li++) {
        var L = layers[li], A = L.amp * SZ();
        _ctx.beginPath(); _ctx.moveTo(0, H);
        for (var x = 0; x <= W; x += 6) {
          var y = H * L.off + Math.sin(x * L.freq + t * L.speed) * A
            + Math.sin(x * L.freq * 1.7 + t * L.speed * 1.4) * A * 0.5;
          _ctx.lineTo(x, y);
        }
        _ctx.lineTo(W, H); _ctx.closePath();
        _ctx.fillStyle = accent(L.a); _ctx.fill();
      }
    };
  }

  /* pulses race along the 24px grid the CSS layer draws under them */
  function fxSynapse() {
    var GRID = 24, MAX = 18, TRAIL = 12, pulses = [];
    return function () {
      _ctx.clearRect(0, 0, W, H);
      if (pulses.length < MAX && Math.random() < 0.12 * (0.35 + 0.65 * I())) {
        var v = 2 + Math.random() * 18, horiz = Math.random() < 0.5;
        pulses.push(horiz
          ? { x: -TRAIL, y: Math.round(Math.random() * H / GRID) * GRID, dx: v, dy: 0 }
          : { x: Math.round(Math.random() * W / GRID) * GRID, y: -TRAIL, dx: 0, dy: v });
      }
      for (var i = pulses.length - 1; i >= 0; i--) {
        var p = pulses[i];
        p.x += p.dx; p.y += p.dy;
        if (p.x > W + TRAIL || p.y > H + TRAIL) { pulses.splice(i, 1); continue; }
        var tx = p.x - (p.dx ? TRAIL : 0), ty = p.y - (p.dy ? TRAIL : 0);
        var g = _ctx.createLinearGradient(tx, ty, p.x, p.y);
        g.addColorStop(0, "rgba(0,0,0,0)");
        g.addColorStop(1, accent(0.4));
        _ctx.strokeStyle = g; _ctx.lineWidth = 1;
        _ctx.beginPath(); _ctx.moveTo(tx, ty); _ctx.lineTo(p.x, p.y); _ctx.stroke();
        _ctx.fillStyle = accentHot(0.55);
        _ctx.beginPath(); _ctx.arc(p.x, p.y, 1.2 * SZ(), 0, 7); _ctx.fill();
      }
    };
  }

  function fxRain() {
    var MAX = 70, drops = [];
    return function () {
      _ctx.clearRect(0, 0, W, H);
      if (drops.length < MAX && Math.random() < 0.35 * (0.35 + 0.65 * I())) drops.push({
        x: Math.random() * W, y: -60,
        len: 18 + Math.random() * 36, v: 3 + Math.random() * 5,
        a: 0.10 + Math.random() * 0.22,
      });
      for (var i = drops.length - 1; i >= 0; i--) {
        var d = drops[i], len = d.len * SZ();
        d.y += d.v;
        if (d.y - len > H) { drops.splice(i, 1); continue; }
        var g = _ctx.createLinearGradient(d.x, d.y - len, d.x, d.y);
        g.addColorStop(0, "rgba(0,0,0,0)");
        g.addColorStop(1, accent(d.a));
        _ctx.strokeStyle = g; _ctx.lineWidth = 1.25;
        _ctx.beginPath(); _ctx.moveTo(d.x, d.y - len); _ctx.lineTo(d.x, d.y); _ctx.stroke();
      }
    };
  }

  function fxConstellations() {
    var N = 48, LINK = 115, stars = [], t = 0, i, j;
    for (i = 0; i < N; i++) stars.push({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.14, vy: (Math.random() - 0.5) * 0.14,
      r: 0.8 + Math.random() * 0.8, ph: Math.random() * 6.28,
    });
    return function () {
      t += 0.01;
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var s = stars[i];
        s.x += s.vx; s.y += s.vy;
        if (s.x < 0) s.x = W; if (s.x > W) s.x = 0;
        if (s.y < 0) s.y = H; if (s.y > H) s.y = 0;
      }
      _ctx.lineWidth = 0.5;
      for (i = 0; i < N; i++) for (j = i + 1; j < N; j++) {
        var dx = stars[i].x - stars[j].x, dy = stars[i].y - stars[j].y, d2 = dx * dx + dy * dy;
        if (d2 < LINK * LINK) {
          _ctx.strokeStyle = accent((1 - Math.sqrt(d2) / LINK) * 0.13);
          _ctx.beginPath(); _ctx.moveTo(stars[i].x, stars[i].y);
          _ctx.lineTo(stars[j].x, stars[j].y); _ctx.stroke();
        }
      }
      for (i = 0; i < N; i++) {
        _ctx.fillStyle = accent(0.12 + (0.5 + 0.5 * Math.sin(t * 2 + stars[i].ph)) * 0.22);
        _ctx.beginPath(); _ctx.arc(stars[i].x, stars[i].y, stars[i].r * SZ(), 0, 7); _ctx.fill();
      }
    };
  }

  /* motes riding a smooth pseudo-noise direction field, trails via bg wash.
     The field is layered sines (not real perlin) - continuous, cheap, and
     good enough that streams visibly bend around each other. */
  function fxFlow() {
    var N = 150, motes = [], t = 0, i;
    for (i = 0; i < N; i++) motes.push({ x: Math.random() * W, y: Math.random() * H, life: Math.random() });
    function angle(x, y) {
      return (Math.sin(x * 0.004 + t * 0.0011)
        + Math.cos(y * 0.0035 - t * 0.0007)
        + Math.sin((x + y) * 0.0016)) * 2.1;
    }
    return function () {
      _ctx.fillStyle = bgWash(0.04);        /* the wash IS the trail fade */
      _ctx.fillRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var m = motes[i], a = angle(m.x, m.y);
        var v = 0.9 + 0.8 * (0.5 + 0.5 * Math.sin(m.x * 0.003 - m.y * 0.002));
        m.x += Math.cos(a) * v; m.y += Math.sin(a) * v; m.life -= 0.0012;
        if (m.life <= 0 || m.x < 0 || m.x > W || m.y < 0 || m.y > H) {
          m.x = Math.random() * W; m.y = Math.random() * H; m.life = 1;
        }
        _ctx.fillStyle = accent(m.life * 0.14);
        _ctx.beginPath(); _ctx.arc(m.x, m.y, SZ(), 0, 7); _ctx.fill();
      }
      t++;
    };
  }

  function fxPetals() {
    var N = 26, petals = [], i;
    function seed() {
      return {
        x: Math.random() * W, y: -12 - Math.random() * 40,
        size: 3 + Math.random() * 5, rot: Math.random() * 6.28,
        vr: (Math.random() - 0.5) * 0.03, vy: 0.3 + Math.random() * 0.55,
        sway: Math.random() * 6.28, swayV: 0.008 + Math.random() * 0.012,
        swayAmp: 0.3 + Math.random() * 0.8,
      };
    }
    for (i = 0; i < N; i++) { var p = seed(); p.y = Math.random() * H; petals.push(p); }
    return function () {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var pt = petals[i];
        pt.y += pt.vy; pt.rot += pt.vr; pt.sway += pt.swayV;
        pt.x += Math.sin(pt.sway) * pt.swayAmp;
        if (pt.y > H + 16) {
          var np = seed();
          for (var k in np) pt[k] = np[k];
        }
        var ps = pt.size * SZ();
        _ctx.save();
        _ctx.translate(pt.x, pt.y); _ctx.rotate(pt.rot);
        _ctx.fillStyle = accent(0.18);
        _ctx.beginPath();
        _ctx.ellipse(-ps * 0.18, 0, ps * 0.62, ps * 0.3, 0.35, 0, 7);
        _ctx.fill();
        _ctx.fillStyle = accent(0.12);
        _ctx.beginPath();
        _ctx.ellipse(ps * 0.18, 0, ps * 0.62, ps * 0.3, -0.35, 0, 7);
        _ctx.fill();
        _ctx.restore();
      }
    };
  }

  function fxSparkles() {
    var N = 30, sparks = [], i;
    function seed() {
      return {
        x: Math.random() * W, y: Math.random() * H, size: 2 + Math.random() * 4.5,
        ph: Math.random() * 6.28, v: 0.015 + Math.random() * 0.03,
        vigor: 0.5 + Math.random() * 0.5,
      };
    }
    for (i = 0; i < N; i++) sparks.push(seed());
    /* a 4-point star: straight concave edges via the diamond's pinch points */
    function star(x, y, r, a) {
      var pinch = r * 0.22;
      _ctx.save();
      _ctx.translate(x, y);
      _ctx.fillStyle = accentHot(a);
      _ctx.beginPath();
      _ctx.moveTo(0, -r); _ctx.lineTo(pinch, -pinch); _ctx.lineTo(r, 0);
      _ctx.lineTo(pinch, pinch); _ctx.lineTo(0, r); _ctx.lineTo(-pinch, pinch);
      _ctx.lineTo(-r, 0); _ctx.lineTo(-pinch, -pinch);
      _ctx.closePath(); _ctx.fill();
      _ctx.restore();
    }
    return function () {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var s = sparks[i];
        s.ph += s.v;
        var tw = Math.sin(s.ph);
        if (tw > 0.03) star(s.x, s.y, s.size * SZ() * (0.5 + tw * 0.5), tw * 0.3 * s.vigor);
        if (s.ph > 18.8) { var ns = seed(); for (var k in ns) s[k] = ns[k]; }
      }
    };
  }

  function fxEmbers() {
    var MAX = 55, embers = [], i;
    function seed(low) {
      return {
        x: Math.random() * W, y: low ? H + Math.random() * 30 : Math.random() * H,
        vx: (Math.random() - 0.5) * 0.3, vy: -0.3 - Math.random() * 0.7,
        r: 0.4 + Math.random() * 0.7, age: 0, span: 220 + Math.random() * 200,
        sway: Math.random() * 6.28,
      };
    }
    for (i = 0; i < MAX; i++) { var e0 = seed(false); e0.age = Math.random() * e0.span; embers.push(e0); }
    return function () {
      /* fade what's there instead of clearing - that's the glow trail */
      _ctx.globalCompositeOperation = "destination-out";
      _ctx.fillStyle = "rgba(0,0,0,0.16)";
      _ctx.fillRect(0, 0, W, H);
      _ctx.globalCompositeOperation = "lighter";
      for (i = embers.length - 1; i >= 0; i--) {
        var e = embers[i];
        e.sway += 0.03;
        e.x += e.vx + Math.sin(e.sway) * 0.5;
        e.y += e.vy;
        e.age++;
        if (e.age > e.span || e.y < -20) {
          var ne = seed(true);
          for (var k in ne) e[k] = ne[k];
          continue;
        }
        /* ramp in over 40 frames, out over the last 60 */
        var a = Math.min(e.age / 40, 1) * Math.min((e.span - e.age) / 60, 1);
        var er = e.r * SZ();
        var g = _ctx.createRadialGradient(e.x, e.y, 0, e.x, e.y, er * 4);
        g.addColorStop(0, accent(a * 0.45));
        g.addColorStop(1, "rgba(0,0,0,0)");
        _ctx.fillStyle = g;
        _ctx.beginPath(); _ctx.arc(e.x, e.y, er * 4, 0, 7); _ctx.fill();
        _ctx.fillStyle = accentHot(a * 0.6);
        _ctx.beginPath(); _ctx.arc(e.x, e.y, er, 0, 7); _ctx.fill();
      }
      _ctx.globalCompositeOperation = "source-over";
    };
  }

  var HOUSE = "backupviewer";
  var ODY = "odysseus";
  var EFFECTS = [
    { id: "none", name: "off", cat: null },
    { id: "gradient", name: "gradient drift", cat: HOUSE, cls: "bgfx-gradient" },
    { id: "aurora", name: "aurora", cat: HOUSE, cls: "bgfx-aurora" },
    { id: "particles", name: "particles", cat: HOUSE, make: fxParticles },
    { id: "starfield", name: "starfield", cat: HOUSE, make: fxStarfield },
    { id: "waves", name: "waves", cat: HOUSE, make: fxWaves },
    { id: "synapse", name: "synapse", cat: ODY, make: fxSynapse, cls: "bgfx-synapse" },
    { id: "rain", name: "rain", cat: ODY, make: fxRain },
    { id: "constellations", name: "constellations", cat: ODY, make: fxConstellations },
    { id: "flow", name: "flow field", cat: ODY, make: fxFlow },
    { id: "petals", name: "petals", cat: ODY, make: fxPetals },
    { id: "sparkles", name: "sparkles", cat: ODY, make: fxSparkles },
    { id: "embers", name: "embers", cat: ODY, make: fxEmbers },
  ];
  var CREDITS = {};
  CREDITS[HOUSE] = "hand-rolled for backupviewer";
  CREDITS[ODY] = "inspired by odysseus (pewdiepie) — AGPL-3.0, combined per GPLv3 §13";

  function byId(id) { return EFFECTS.find(function (e) { return e.id === id; }); }

  var persistTune = null;   /* built lazily so BV.debounce is ready */

  BV.bgfx = {
    EFFECTS: EFFECTS,
    activeId: "none",
    intensity: 1,   /* 0.1..1 - global alpha (+ spawn rate on the spawny effects) */
    size: 1,        /* 0.5..2 - geometry at each effect's natural dimension */

    activeName: function () {
      var t = byId(BV.bgfx.activeId);
      return (t && t.name) || "off";
    },

    apply: function (settings) {
      settings = settings || {};
      BV.bgfx.tune({
        intensity: settings.bgfx_intensity != null ? settings.bgfx_intensity : 1,
        size: settings.bgfx_size != null ? settings.bgfx_size : 1,
      }, false);
      BV.bgfx.set(settings.bgfx || "none", false);
    },

    /* the theme window's sliders land here; canvas effects pick the new
       values up on their next frame, the CSS looks through --bgfx-i */
    tune: function (opts, persist) {
      if (opts.intensity != null) BV.bgfx.intensity = Math.min(1, Math.max(0.1, opts.intensity));
      if (opts.size != null) BV.bgfx.size = Math.min(2, Math.max(0.5, opts.size));
      syncTuning();
      if (!HAS_RAF || REDUCED) { if (_step) _step(); }   /* static frame keeps up too */
      if (persist) {
        if (!persistTune) persistTune = BV.debounce(function () {
          BV.api.call("set_setting", "bgfx_intensity", BV.bgfx.intensity).catch(function () {});
          BV.api.call("set_setting", "bgfx_size", BV.bgfx.size).catch(function () {});
        }, 400);
        persistTune();
      }
    },

    set: function (id, persist) {
      var t = byId(id) || EFFECTS[0];
      if (t.id !== BV.bgfx.activeId) {
        stop();
        _step = null;
        if (t.id === "none") {
          dropLayers();
        } else {
          ensureCss();
          _css.className = t.cls || "";
          if (t.make) {
            ensureCanvas();
            /* a previous effect may leave composite/alpha state behind */
            _ctx.globalCompositeOperation = "source-over";
            _ctx.globalAlpha = 1;
            _ctx.clearRect(0, 0, W, H);
            _step = t.make();
            start();
          } else if (_canvas) {
            _canvas.remove(); _canvas = null; _ctx = null;
          }
        }
        BV.bgfx.activeId = t.id;
      }
      if (persist) BV.api.call("set_setting", "bgfx", t.id).catch(function () {});
    },
  };
})();
