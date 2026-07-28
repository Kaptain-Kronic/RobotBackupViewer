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
    /* one shared hex reader (theme.js) so an 8-digit theme color resolves the
       same here as in the editor. The literal is a last resort, not a default:
       the private regex here rejected 8-digit hex and painted the app's
       stock yellow over the user's theme. */
    var rgb = BV.hexRgb(raw) || BV.hexRgb(fallback);
    _varCache[varName] = { raw: raw, rgb: rgb };
    return rgb;
  }
  /* the intensity slider scales every effect alpha through here; the size
     slider scales geometry at each effect's natural dimension via SZ() */
  function I() { return BV.bgfx.intensity; }
  function SZ() { return BV.bgfx.size; }

  /* CNT — the population knob. Every effect's "how many" literal goes through
     here, so density is one slider instead of a dozen constants. */
  function CNT(n) { return Math.max(1, Math.round(n * BV.bgfx.density)); }
  /* the same knob raw, for effects whose "how much" is not a count of things
     — life has no particles, its density is how thick the soup is */
  function DEN() { return BV.bgfx.density; }

  /* rr — a plain draw from an authored range. Deliberately NOT widened by the
     variance knob: routing lifespans, spawn rates and oscillator frequencies
     through one global spread is what made "variance" feel vague, and several
     of them produce nonsense at the extremes. Ranges are exactly what the
     author wrote; variance acts only through the declared channels below. */
  function rr(lo, hi) { return lo + Math.random() * (hi - lo); }

  /* ---- variance, scoped per effect ----
     One global spread multiplier applied to everything is too blunt an
     instrument. Turned up on boids it shrinks birds to invisibility and
     splits their cruise speeds so far the flock stops flowing — fast birds
     tear through slow ones and the influence reads as noise. On life it has
     nothing meaningful to touch at all, because every cell must step on the
     same clock or it is not Conway's Life any more.

     So variance is a set of CHANNELS, and each effect declares which of them
     apply to it and how far each may swing. A channel an effect does not
     declare is immune — vary() hands back the value untouched. The knob then
     means one honest thing everywhere: "how much do individual elements
     differ from each other, in the ways THIS effect can afford to differ".

     limit = the maximum fractional swing, reached at variance 200%. */
  var _varies = {};        /* the active effect's declaration */

  function vary(channel, v) {
    var lim = _varies[channel];
    if (!lim) return v;
    return v * (1 + (Math.random() * 2 - 1) * lim * BV.bgfx.spread * 0.5);
  }

  /* per-element hue offset in degrees, on the same knob. This is what lets
     variance mean "these things are different colours" without also meaning
     "these things are different sizes" — the channels are independent, so an
     effect can afford wild colour and almost no speed spread. */
  function varyHue() {
    var lim = _varies.hue;
    if (!lim) return 0;
    return (Math.random() * 2 - 1) * lim * BV.bgfx.spread * 0.5;
  }

  /* ---- per-effect parameters ----
     The six global knobs are the ones EVERY effect can answer honestly:
     intensity, size, speed, density, variance, hue drift. Past those, an
     algorithm has dials that mean nothing anywhere else — Gray-Scott's feed
     and kill rates, the three flocking weights, the crystal's decay. Those
     belong to the effect that owns them, not to a global panel that would
     have to pretend they apply to the other eight.

     So an effect declares its own `params: [...]`, reads them with P("key"),
     and the lab builds a second slider row for whatever it declared. An
     effect that declares nothing behaves exactly as before — the whole
     mechanism costs a non-participating effect one property lookup at switch
     time.

     Values live in a per-effect store, so tuning boids, wandering off to
     look at life, and coming back finds the flock exactly as you left it.
     P() returns the slider's number in DISPLAY units; the effect does its own
     conversion, which keeps the declared range readable as the thing the user
     actually sees. */
  var _params = {};                /* the active effect's live values */
  var _pstore = {};                /* keyed by effect id, survives switching */

  function P(k) {
    var v = _params[k];
    return v === undefined ? 0 : v;
  }

  function paramsFor(t) {
    if (!t.params || !t.params.length) return {};
    var store = _pstore[t.id] || (_pstore[t.id] = {});
    for (var i = 0; i < t.params.length; i++) {
      var p = t.params[i];
      if (store[p.k] === undefined) store[p.k] = p.def;
    }
    return store;
  }

  /* ---- live theme color taps (bgfx.js, plus the hue-drift hook) ---- */
  var _varCache = {};
  function rgbOf(varName, fallback) {
    var raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    var hit = _varCache[varName];
    if (hit && hit.raw === raw) return hit.rgb;
    var rgb = BV.hexRgb(raw) || BV.hexRgb(fallback);
    _varCache[varName] = { raw: raw, rgb: rgb };
    return rgb;
  }

  /* hue drift: the whole psychedelic dimension for one function, because
     every effect's color already funnels through accent()/accentHot().
     The drift OSCILLATES about the theme's accent rather than cycling the
     full wheel, so a theme keeps its character at small values and only
     goes full trip when you ask for it (180 = the entire wheel, both ways). */
  function drift(c) {
    if (!BV.bgfx.hue) return c;
    return rotHue(c, Math.sin(CLOCK * 0.0016) * BV.bgfx.hue);
  }
  function rotHue(c, deg) {
    var r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    var l = (mx + mn) / 2, h = 0, s = 0;
    if (d) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
    }
    h = (h + deg / 360 + 1) % 1;
    if (!s) { var v = Math.round(l * 255); return [v, v, v]; }
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    return [hue2c(p, q, h + 1 / 3), hue2c(p, q, h), hue2c(p, q, h - 1 / 3)];
  }
  function hue2c(p, q, t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    var v = t < 1 / 6 ? p + (q - p) * 6 * t
      : t < 1 / 2 ? q
      : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6
      : p;
    return Math.round(v * 255);
  }

  /* both taps take an optional per-element hue offset in degrees, so an
     effect can colour its elements individually without touching the global
     drift — the two compose: drift moves the whole palette, the offset
     spreads elements across it */
  function accent(a, off) {
    var c = drift(rgbOf("--accent", "e2b714"));
    if (off) c = rotHue(c, off);
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (a * I()) + ")";
  }
  function accentHot(a, off) {
    var c = drift(rgbOf("--accent", "e2b714"));
    if (off) c = rotHue(c, off);
    return "rgba(" + Math.min(255, c[0] + 90) + "," + Math.min(255, c[1] + 90) + ","
      + Math.min(255, c[2] + 90) + "," + (a * I()) + ")";
  }
  function bgWash(a) {
    var c = rgbOf("--bg", "323437");
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }
  /* Hue drift is the whole colour dimension for one function, because every
     effect's colour already funnels through accent()/accentHot(). It
     OSCILLATES about the theme's accent rather than cycling the wheel, so a
     theme keeps its character at small values and only goes the whole way
     when asked. `off` is a per-ELEMENT offset in degrees — that is how the
     variance knob can colour individual particles without also resizing
     them. */
  function drift(c) {
    if (!BV.bgfx.hue) return c;
    return rotHue(c, Math.sin(CLOCK * 0.0016) * BV.bgfx.hue);
  }
  function rotHue(c, deg) {
    if (!deg) return c;
    var r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    var l = (mx + mn) / 2, h = 0, sa = 0;
    if (d) {
      sa = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
    }
    h = (h + deg / 360 + 1) % 1;
    if (!sa) { var v = Math.round(l * 255); return [v, v, v]; }
    var q = l < 0.5 ? l * (1 + sa) : l + sa - l * sa, p = 2 * l - q;
    return [hue2c(p, q, h + 1 / 3), hue2c(p, q, h), hue2c(p, q, h - 1 / 3)];
  }
  function hue2c(p, q, t) {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    return Math.round((t < 1 / 6 ? p + (q - p) * 6 * t
      : t < 1 / 2 ? q
      : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6
      : p) * 255);
  }

  function accent(a, off) {
    var c = rotHue(drift(rgbOf("--accent", "e2b714")), off || 0);
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (a * I()) + ")";
  }
  /* hot cores (embers, star heads): the accent pushed toward white */
  function accentHot(a, off) {
    var c = rotHue(drift(rgbOf("--accent", "e2b714")), off || 0);
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
  var CLOCK = 0;                 /* effect time, not wall time (fractional) */
  var _last = 0, _fdt = 0;
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
  /* Backing resolution for the ACTIVE effect, in device px per CSS px.
     "dpr" is the default (sharp, capped at 2x). A soft field effect that is
     nothing but wide alpha washes is fill-rate bound rather than detail
     bound, so it may declare a number instead and let the canvas upscale —
     same picture, a quarter of the pixels to blend. */
  var _res = "dpr";
  function sizeCanvas() {
    /* draw in CSS pixels; back the store at devicePixelRatio (capped - the
       effects are soft glows, 2x is plenty and 3x laptops shouldn't pay) */
    var dpr = _res === "dpr" ? Math.min(window.devicePixelRatio || 1, 2) : _res;
    W = window.innerWidth; H = window.innerHeight;
    _canvas.width = Math.max(1, Math.round(W * dpr));
    _canvas.height = Math.max(1, Math.round(H * dpr));
    _ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function dropLayers() {
    if (_canvas) { _canvas.remove(); _canvas = null; _ctx = null; }
    if (_css) { _css.remove(); _css = null; }
  }

  /* dt used to be the frame COUNT — every effect therefore ran at whatever
     rate the panel refreshed at, so a 144Hz display drove everything at 2.4x
     the authored pace and a dropped frame read as a change of velocity.
     It is real elapsed time now, normalised to 60fps so every constant tuned
     by eye keeps its meaning there and is corrected everywhere else. The EMA
     absorbs a single hiccup rather than compensating exactly for it (which
     would trade frame-time noise for position noise), and the clamps stop a
     tab-switch stall from teleporting the whole field. */
  function tick(now) {
    _raf = requestAnimationFrame(tick);
    if (!_step) return;
    var ms = _last ? now - _last : 16.667;
    _last = now;
    _fdt = _fdt ? _fdt * 0.8 + ms * 0.2 : ms;
    var dt = BV.bgfx.speed * Math.max(0.25, Math.min(2.5, _fdt / 16.667));
    CLOCK += dt;
    _step(dt);
  }
  function start() {
    if (!_step) return;
    if (!HAS_RAF || REDUCED) { _step(1); return; }  /* one honest static frame */
    cancelAnimationFrame(_raf);
    _last = 0;
    _raf = requestAnimationFrame(tick);
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
    var N = CNT(64), LINK = 130, pts = [], i, j;
    for (i = 0; i < N; i++) pts.push({
      x: Math.random() * W, y: Math.random() * H,
      vx: rr(-0.225, 0.225), vy: rr(-0.225, 0.225),
    });
    return function (dt) {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var p = pts[i];
        p.x += p.vx * dt; p.y += p.vy * dt;
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
    var N = CNT(180), stars = [], i;
    function seed(s) { s.x = (Math.random() - 0.5) * W; s.y = (Math.random() - 0.5) * H; s.z = Math.random() * W; }
    for (i = 0; i < N; i++) { var s = {}; seed(s); stars.push(s); }
    return function (dt) {
      _ctx.fillStyle = bgWash(Math.min(1, 0.5 * dt));   /* short motion trails */
      _ctx.fillRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var st = stars[i];
        st.z -= 2.2 * dt;
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
    return function (dt) {
      _ctx.clearRect(0, 0, W, H); t += dt;
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
    var GRID = 24, MAX = CNT(18), TRAIL = 12, pulses = [];
    return function (dt) {
      _ctx.clearRect(0, 0, W, H);
      if (pulses.length < MAX && Math.random() < 0.12 * dt * (0.35 + 0.65 * I())) {
        var v = 2 + Math.random() * 18, horiz = Math.random() < 0.5;
        pulses.push(horiz
          ? { x: -TRAIL, y: Math.round(Math.random() * H / GRID) * GRID, dx: v, dy: 0 }
          : { x: Math.round(Math.random() * W / GRID) * GRID, y: -TRAIL, dx: 0, dy: v });
      }
      for (var i = pulses.length - 1; i >= 0; i--) {
        var p = pulses[i];
        p.x += p.dx * dt; p.y += p.dy * dt;
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
    var MAX = CNT(70), drops = [];
    return function (dt) {
      _ctx.clearRect(0, 0, W, H);
      if (drops.length < MAX && Math.random() < 0.35 * dt * (0.35 + 0.65 * I())) drops.push({
        x: Math.random() * W, y: -60,
        len: 18 + Math.random() * 36, v: 3 + Math.random() * 5,
        a: 0.10 + Math.random() * 0.22,
      });
      for (var i = drops.length - 1; i >= 0; i--) {
        var d = drops[i], len = d.len * SZ();
        d.y += d.v * dt;
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
    var N = CNT(48), LINK = 115, stars = [], t = 0, i, j;
    for (i = 0; i < N; i++) stars.push({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.14, vy: (Math.random() - 0.5) * 0.14,
      r: 0.8 + Math.random() * 0.8, ph: Math.random() * 6.28,
    });
    return function (dt) {
      t += 0.01 * dt;
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var s = stars[i];
        s.x += s.vx * dt; s.y += s.vy * dt;
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
    var N = CNT(150), motes = [], t = 0, i;
    for (i = 0; i < N; i++) motes.push({ x: Math.random() * W, y: Math.random() * H, life: Math.random() });
    function angle(x, y) {
      return (Math.sin(x * 0.004 + t * 0.0011)
        + Math.cos(y * 0.0035 - t * 0.0007)
        + Math.sin((x + y) * 0.0016)) * 2.1;
    }
    return function (dt) {
      _ctx.fillStyle = bgWash(Math.min(1, 0.04 * dt));  /* the wash IS the trail fade */
      _ctx.fillRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var m = motes[i], a = angle(m.x, m.y);
        var v = 0.9 + 0.8 * (0.5 + 0.5 * Math.sin(m.x * 0.003 - m.y * 0.002));
        m.x += Math.cos(a) * v * dt; m.y += Math.sin(a) * v * dt;
        m.life -= 0.0012 * dt;
        if (m.life <= 0 || m.x < 0 || m.x > W || m.y < 0 || m.y > H) {
          m.x = Math.random() * W; m.y = Math.random() * H; m.life = 1;
        }
        _ctx.fillStyle = accent(m.life * 0.14);
        _ctx.beginPath(); _ctx.arc(m.x, m.y, SZ(), 0, 7); _ctx.fill();
      }
      t += dt;
    };
  }

  function fxPetals() {
    var N = CNT(26), petals = [], i;
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
    return function (dt) {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var pt = petals[i];
        pt.y += pt.vy * dt; pt.rot += pt.vr * dt; pt.sway += pt.swayV * dt;
        pt.x += Math.sin(pt.sway) * pt.swayAmp * dt;
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
    var N = CNT(30), sparks = [], i;
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
    return function (dt) {
      _ctx.clearRect(0, 0, W, H);
      for (i = 0; i < N; i++) {
        var s = sparks[i];
        s.ph += s.v * dt;
        var tw = Math.sin(s.ph);
        if (tw > 0.03) star(s.x, s.y, s.size * SZ() * (0.5 + tw * 0.5), tw * 0.3 * s.vigor);
        if (s.ph > 18.8) { var ns = seed(); for (var k in ns) s[k] = ns[k]; }
      }
    };
  }

  function fxEmbers() {
    var MAX = CNT(55), embers = [], i;
    function seed(low) {
      return {
        x: Math.random() * W, y: low ? H + Math.random() * 30 : Math.random() * H,
        vx: (Math.random() - 0.5) * 0.3, vy: -0.3 - Math.random() * 0.7,
        r: 0.4 + Math.random() * 0.7, age: 0, span: 220 + Math.random() * 200,
        sway: Math.random() * 6.28,
      };
    }
    for (i = 0; i < MAX; i++) { var e0 = seed(false); e0.age = Math.random() * e0.span; embers.push(e0); }
    return function (dt) {
      /* fade what's there instead of clearing - that's the glow trail */
      _ctx.globalCompositeOperation = "destination-out";
      _ctx.fillStyle = "rgba(0,0,0," + Math.min(1, 0.16 * dt) + ")";
      _ctx.fillRect(0, 0, W, H);
      _ctx.globalCompositeOperation = "lighter";
      for (i = embers.length - 1; i >= 0; i--) {
        var e = embers[i];
        e.sway += 0.03 * dt;
        e.x += (e.vx + Math.sin(e.sway) * 0.5) * dt;
        e.y += e.vy * dt;
        e.age += dt;
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

  /* ---- ferrofluid ------------------------------------------------------
     A metaball field on a coarse grid, contoured with marching squares and
     filled at four nested thresholds. Blobs do not overlap — they MERGE,
     because the field sums: two masses grow a neck, swallow each other,
     and tear apart again. That, at a very slow drift, is the ferrofluid.

     Hand-rolled on purpose. The gooey look is normally a GPU blur plus an
     alpha threshold; that is a full-screen blur every frame, which is
     exactly the thing a plant PC on software rendering cannot afford. The
     field costs cells x kernels of flat arithmetic instead, and the fill is
     one path.

     Each blob carries small satellites orbiting just inside its own surface.
     They bulge the surface as they pass and stretch into spikes as they
     drift out — the Rosensweig look — for the price of two more kernels. */
  function fxFerro() {
    var CELL = 10;                /* field pitch in CSS px — finer than the
                                     alpha-washed version needed, because a
                                     near-solid fill shows contour faceting */
    var cols = 0, rows = 0, fld = null;
    var blobs = [], kern = [], t = 0;
    var cx = [0, 0, 0, 0], cy = [0, 0, 0, 0], cv = [0, 0, 0, 0];

    function grid() {
      cols = Math.ceil(W / CELL) + 2;
      rows = Math.ceil(H / CELL) + 2;
      fld = new Float32Array(cols * rows);
    }

    var N = CNT(6), i, k;
    /* Placement is a JITTERED GRID, not independent random draws. Independent
       draws clump — that is genuinely what random looks like — and clumped
       metaballs merge into one mass, which is the "it groups together too
       much" complaint. One blob per cell, jittered inside its cell, spreads
       them without needing a repulsion pass. Cell size also sets the blob
       radius and how far it may wander, so raising density makes blobs
       smaller and tighter-roaming instead of collapsing them into a wall.

       Note this stays out of rr()/vary(): variance governs an element's
       PROPERTIES, never where it sits. Routing position through the knob
       would make "no variance" mean "every blob stacked in the middle of the
       screen" — a bug, not a look. */
    var gcols = Math.max(1, Math.round(Math.sqrt(N * (W / Math.max(1, H)))));
    var grows = Math.max(1, Math.ceil(N / gcols));
    var cells = [];
    for (i = 0; i < gcols * grows; i++) cells.push(i);
    for (i = cells.length - 1; i > 0; i--) {     /* shuffle: a short count
                                                    must not always fill the
                                                    top-left cells */
      var j0 = (Math.random() * (i + 1)) | 0, tmp = cells[i];
      cells[i] = cells[j0]; cells[j0] = tmp;
    }
    var span = Math.min(W / gcols, H / grows);   /* the cell's short side */

    for (i = 0; i < N; i++) {
      var cell = cells[i], ccx = cell % gcols, ccy = (cell / gcols) | 0;
      var b = {
        hx: (ccx + 0.2 + Math.random() * 0.6) / gcols,
        hy: (ccy + 0.2 + Math.random() * 0.6) / grows,
        /* wander within roughly its own cell, so neighbours touch and part
           instead of piling up in the middle */
        ax: rr(0.20, 0.42) / gcols, ay: rr(0.20, 0.42) / grows,
        fx: rr(0.00055, 0.00135), fy: rr(0.00045, 0.00115),
        px: Math.random() * 6.283, py: Math.random() * 6.283,
        R: span * rr(0.34, 0.52),   /* SUPPORT radius — the visible surface
                                       lands near 0.45 of it at the body
                                       threshold, and it tracks the cell so
                                       density and size stay independent */
        bAmp: rr(0.08, 0.26), bF: rr(0.0008, 0.0019), bP: Math.random() * 6.283,
        /* this blob's own deviation from the global size and speed knobs */
        /* no hue channel here: the blobs MERGE into one field, so there is
           no per-element surface left to colour separately */
        szf: vary("size", 1), spf: vary("speed", 1),
        sats: [],
      };
      for (k = 0; k < P("sats"); k++) b.sats.push({
        orb: rr(0.35, 0.62),          /* orbit, in parent support radii */
        rad: rr(0.35, 0.55),
        f: rr(0.0009, 0.0026) * (Math.random() < 0.5 ? -1 : 1),
        p: Math.random() * 6.283,
        wob: rr(0.10, 0.30), wf: rr(0.0011, 0.0031),
      });
      blobs.push(b);
    }
    grid();

    /* one sub-polygon per straddled cell, all into a single path: adjacent
       cells share edges, so a single nonzero fill unions them with no seams.
       Cells that are fully inside are not emitted one at a time — consecutive
       ones are coalesced into a single wide rect per row, which is what keeps
       the path a few hundred vertices instead of a few thousand. */
    function pt(started, x, y) {
      if (started) _ctx.lineTo(x, y); else _ctx.moveTo(x, y);
      return true;
    }
    function run(gx0, gx1, y0, y1) {
      var x0 = gx0 * CELL, x1 = gx1 * CELL;
      _ctx.moveTo(x0, y0); _ctx.lineTo(x1, y0);
      _ctx.lineTo(x1, y1); _ctx.lineTo(x0, y1);
      _ctx.closePath();
    }
    /* one inside corner, clipped by the two edges leaving it: prev-crossing →
       corner → next-crossing, same rotational order as the main walk so the
       winding matches and the nonzero union still works */
    function tri(c, T) {
      var p = (c + 3) & 3, n = (c + 1) & 3;
      var s1 = (T - cv[c]) / (cv[p] - cv[c]);
      var s2 = (T - cv[c]) / (cv[n] - cv[c]);
      _ctx.moveTo(cx[c] + (cx[p] - cx[c]) * s1, cy[c] + (cy[p] - cy[c]) * s1);
      _ctx.lineTo(cx[c], cy[c]);
      _ctx.lineTo(cx[c] + (cx[n] - cx[c]) * s2, cy[c] + (cy[n] - cy[c]) * s2);
      _ctx.closePath();
    }
    function layer(T, style) {
      var gx, gy;
      _ctx.beginPath();
      for (gy = 0; gy < rows - 1; gy++) {
        var y0 = gy * CELL, y1 = y0 + CELL, row = gy * cols;
        var runX = -1;
        for (gx = 0; gx < cols - 1; gx++) {
          var i0 = row + gx;
          var va = fld[i0], vb = fld[i0 + 1], vc = fld[i0 + cols + 1], vd = fld[i0 + cols];
          var ia = va >= T, ib = vb >= T, ic = vc >= T, idd = vd >= T;
          if (ia && ib && ic && idd) { if (runX < 0) runX = gx; continue; }
          if (runX >= 0) { run(runX, gx, y0, y1); runX = -1; }
          if (!ia && !ib && !ic && !idd) continue;
          var x0 = gx * CELL, x1 = x0 + CELL;
          cx[0] = x0; cy[0] = y0; cv[0] = va;
          cx[1] = x1; cy[1] = y0; cv[1] = vb;
          cx[2] = x1; cy[2] = y1; cv[2] = vc;
          cx[3] = x0; cy[3] = y1; cv[3] = vd;

          /* SADDLE: two opposite corners inside, two outside. The naive
             corner-walk joins them straight through the middle and emits a
             bowtie — those are the hard diagonal edges and the stray little
             triangle that never moves (a cell parked on the threshold keeps
             re-emitting the same wrong shape). Marching squares cannot tell
             from the corners alone whether the two lobes are joined, so
             disambiguate on the cell centre: joined if the centre is inside,
             two separate corner triangles if it is not. */
          if ((ia && ic && !ib && !idd) || (ib && idd && !ia && !ic)) {
            if ((va + vb + vc + vd) * 0.25 < T) {
              if (ia) tri(0, T);
              if (ib) tri(1, T);
              if (ic) tri(2, T);
              if (idd) tri(3, T);
              continue;
            }
          }
          var started = false;
          for (var e = 0; e < 4; e++) {
            var e2 = (e + 1) & 3;
            var in1 = cv[e] >= T, in2 = cv[e2] >= T;
            if (in1) started = pt(started, cx[e], cy[e]);
            if (in1 !== in2) {
              var s = (T - cv[e]) / (cv[e2] - cv[e]);
              started = pt(started,
                cx[e] + (cx[e2] - cx[e]) * s,
                cy[e] + (cy[e2] - cy[e]) * s);
            }
          }
          if (started) _ctx.closePath();
        }
        if (runX >= 0) run(runX, cols - 1, y0, y1);
      }
      _ctx.fillStyle = style;
      _ctx.fill();
    }

    return function (dt) {
      t += dt;
      /* the grain slider resizes the field, so compare CELL itself: a new
         pitch that happens to round to the same column count would otherwise
         index a stale array */
      var cg = P("grain");
      if (!fld || cg !== CELL || cols !== Math.ceil(W / cg) + 2) { CELL = cg; grid(); }
      var rm = P("roam") * 0.01, reach = P("reach") * 0.01;

      kern.length = 0;
      for (var bi = 0; bi < blobs.length; bi++) {
        var b = blobs[bi];
        var tb = t * b.spf;                 /* this blob's own clock */
        var bx = (b.hx + Math.sin(tb * b.fx + b.px) * b.ax * rm) * W;
        var by = (b.hy + Math.cos(tb * b.fy + b.py) * b.ay * rm) * H;
        var br = b.R * SZ() * b.szf * (1 + Math.sin(tb * b.bF + b.bP) * b.bAmp);
        kern.push({ x: bx, y: by, R2: br * br });
        for (var si = 0; si < b.sats.length; si++) {
          var s = b.sats[si];
          var ang = tb * s.f + s.p;
          var orb = br * s.orb * reach * (1 + Math.sin(tb * s.wf) * s.wob);
          var sr = br * s.rad;
          kern.push({ x: bx + Math.cos(ang) * orb, y: by + Math.sin(ang) * orb, R2: sr * sr });
        }
      }

      var idx = 0, kn = kern.length;
      for (var gy = 0; gy < rows; gy++) {
        var yy = gy * CELL;
        for (var gx = 0; gx < cols; gx++) {
          var xx = gx * CELL, f = 0;
          /* compact-support kernel, not 1/d². An inverse-square blob reaches
             the whole canvas, so every blob quietly raises the field
             everywhere and six of them sum into one wall that fills the
             screen. (1 - d²/R²)³ is zero beyond R: a blob only ever affects
             its own neighbourhood, so masses stay separate until they
             genuinely touch — and the cells outside R cost one compare. */
          for (var kk = 0; kk < kn; kk++) {
            var K = kern[kk], dx = xx - K.x, dy = yy - K.y;
            var d2 = dx * dx + dy * dy;
            if (d2 >= K.R2) continue;
            var q = 1 - d2 / K.R2;
            f += q * q * q;
          }
          fld[idx++] = f;
        }
      }
      _ctx.clearRect(0, 0, W, H);
      /* ONE fill, ONE flat colour. Nested thresholds in different tones read
         as a cheap gradient, not as a liquid — a mass has to be one solid
         thing and let its SILHOUETTE do the work. Alpha rides I(), so
         intensity takes it from a whisper to fully opaque. */
      layer(P("surface") * 0.01, accent(1));
    };
  }

  /* ---- boids -----------------------------------------------------------
     Reynolds flocking: separation, alignment, cohesion, and nothing else.
     The murmurations, the splits, the way a flock folds back through itself
     are all emergent — no path is authored anywhere.

     The model was never the hard part. Four rounds of this looked wrong, and
     every one of them was wrong for a reason worth writing down, because
     three of the four fixes were plausible and made it worse.

     1. A HARD NEIGHBOUR CUTOFF flickers, so neighbours were weighted with a
        rim fade. Correct — and then completely undone by (2).
     2. THE STEERING NORMALISED ITS OWN EVIDENCE AWAY. The old helper did
        `desired = (offset/|offset|) * cruise`, which divides out the very
        magnitude the fade had just computed. With one neighbour the weight
        cancels EXACTLY, so a bird at 99.99% of the radius — weight 1e-4 —
        commanded cohesion at full authority, and `wsum > 0` became a hard
        on/off switch stepping acceleration from 0 to 2.1F between frames.
        This was the dominant defect and it hid behind a correct-looking fade.
     3. CAPPING THE TURN RATE at constant speed IS a circle, by definition,
        for any bird whose demand exceeds the cap — which in a flock is most
        of them. The limiter did not smooth the motion, it replaced it.
     4. BANK DRAG charged on achieved heading change, i.e. on a_perp
        independent of speed, while turn rate a/v RISES as speed falls. Above
        roughly a 50-degree demand that has no fixed point: the bird pins on
        the speed floor and pirouettes at a 5px radius.

     5. THE ARENA WAS A TORUS, which quietly guaranteed the worst outcome of
        all. Alignment is a consensus dynamic; on a periodic domain nothing
        ever breaks it, so the whole population converges on one heading and
        cohesion winds the aligned mass into a mill — one screen-filling
        spiral, forever. That is the ordered phase of the model, not a
        tuning failure, and no choice of weights escapes it. Bounded edges
        break the symmetry continuously: every wall turns a group, splits it
        against its neighbours, and hands the flock a heading nothing in the
        system chose. (This is where beneater/boids gets its variety — a
        generous margin and a steady nudge inward, no wrapping anywhere.)

     What is here now: influence is GRADED by evidence (a quorum factor, an
     arrival ramp, and the raw mean velocity rather than its direction), the
     accumulated desire is turned into ONE lateral command so the turn radius
     is exactly v²/F and never jumps, speed is a bounded first-order lag with
     an explicit fixed point, and the arena has walls. The neighbourhood is
     sized to hold about six birds instead of 1.5 — with 1.5, "flocking" was
     a churn of transient pairs, and the bearing to a single close neighbour
     rotates faster than a bird can physically turn. */
  function fxBoids() {
    var N = CNT(70), fl = [], i;
    for (i = 0; i < N; i++) {
      var a = Math.random() * 6.283, sp = rr(1.9, 2.4) * vary("speed", 1);
      fl.push({
        x: Math.random() * W, y: Math.random() * H,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
        cruise: sp,                        /* deviation from the speed knob */
        /* force IS this bird's turn radius: R = v²/force. Narrow on purpose
           — the old rr(1.0,1.8) x rr(0.016,0.030) spread meant a 6.1x range
           of turn radius inside one flock, so members could not physically
           hold a shared arc. This pair gives 2.3x. */
        force: rr(0.011, 0.016) * (P("agility") * 0.01) * vary("agility", 1),
        pl: 0,                             /* lagged lateral command */
        wph: Math.random() * 6.283,
        wrate: rr(0.0008, 0.0018),         /* own wander rate, not a shared one */
        q: 0, wsum: 0, nsp: 0, fore: 0,    /* scratch for the speed law */
        sz: vary("size", 1), hue: varyHue(),
        hot: Math.random() < 0.06,
      });
    }
    var ax = new Float32Array(N), ay = new Float32Array(N);
    /* uniform bins one neighbourhood wide: each bird reads its own bin and
       the eight around it, which is O(N) instead of O(N²). Linked lists in
       typed arrays, so binning allocates nothing per frame. */
    var head = null, link = new Int32Array(N), bc = 0, br = 0;

    /* no wrap ghosts any more: the arena is bounded, so nothing teleports
       across the screen and there is no straddling flock to stitch together */
    function wing(b, L, Wd) {
      var sp = Math.sqrt(b.vx * b.vx + b.vy * b.vy) || 1;
      var ux = b.vx / sp, uy = b.vy / sp;
      var px = -uy, py = ux;
      /* stretch with airspeed, width held. Damped to about ±10%: now that
         speed changes on a ~1s constant this still reads as pace, where the
         old ±20% on a twitching speed just read as a wobbling shape. */
      L *= 0.86 + 0.26 * (sp / b.cruise);
      var s = b.sz;
      var bx = b.x - ux * L * 0.6 * s, by = b.y - uy * L * 0.6 * s;
      _ctx.moveTo(b.x + ux * L * s, b.y + uy * L * s);
      _ctx.lineTo(bx + px * Wd * s, by + py * Wd * s);
      _ctx.lineTo(bx - px * Wd * s, by - py * Wd * s);
      _ctx.closePath();
    }

    return function (dt) {
      /* sized to hold ~6 birds, not 1.5. The 1/sqrt(density) factor holds the
         neighbour count — and the pair-test cost — constant across the whole
         25%..600% density slider, which is the one property topological
         (k-nearest) neighbours would have been worth having for. */
      var den = Math.sqrt(Math.max(0.05, DEN()));
      var NEI = P("vision") * SZ() / den;
      var SEP = NEI * P("space") * 0.01;    /* personal space as a FRACTION of
                                               vision, so the two cannot cross
                                               over and invert the rules */
      var NEI2 = NEI * NEI, SEP2 = SEP * SEP;
      var i, j;
      /* the turning band, as a fraction of the short side. Sized from the
         turn radius rather than picked: a bird at cruise with boosted
         authority needs about v²/(3.4*force) ~ 100px to reverse, so 20%
         leaves room to spare and it is always already coming about before it
         reaches the glass. Below ~8% it cannot finish the turn and will
         scrape along the edge — hence the floor on the slider. */
      var MG = P("margin") * 0.01 * Math.min(W, H);
      /* the three weights, read once per frame rather than per bird */
      var COH = P("cohesion") * 0.01;
      var ALI = P("align") * 0.01;
      var SEPW = P("separate") * 0.01;

      bc = Math.max(1, Math.floor(W / NEI));
      br = Math.max(1, Math.floor(H / NEI));
      var nb = bc * br, cw = W / bc, chh = H / br;
      if (!head || head.length !== nb) head = new Int32Array(nb);
      head.fill(-1);
      for (i = 0; i < N; i++) {
        var qx = (fl[i].x / cw) | 0; if (qx < 0) qx = 0; else if (qx >= bc) qx = bc - 1;
        var qy = (fl[i].y / chh) | 0; if (qy < 0) qy = 0; else if (qy >= br) qy = br - 1;
        var qi = qy * bc + qx;
        link[i] = head[qi]; head[qi] = i;
      }

      for (i = 0; i < N; i++) {
        var b = fl[i];
        var cx = 0, cy = 0, avx = 0, avy = 0, spx = 0, spy = 0, wsum = 0, nsp = 0;
        var bx2 = (b.x / cw) | 0; if (bx2 < 0) bx2 = 0; else if (bx2 >= bc) bx2 = bc - 1;
        var by2 = (b.y / chh) | 0; if (by2 < 0) by2 = 0; else if (by2 >= br) by2 = br - 1;
        /* the arena is bounded now, so the bin window simply clamps — no
           modulo, no wrapped deltas, and no fewer-than-three-bins special
           case, all of which existed only to serve the torus */
        var gx0 = bx2 > 0 ? bx2 - 1 : 0, gx1 = bx2 < bc - 1 ? bx2 + 1 : bc - 1;
        var gy0 = by2 > 0 ? by2 - 1 : 0, gy1 = by2 < br - 1 ? by2 + 1 : br - 1;
        for (var gy = gy0; gy <= gy1; gy++) {
          for (var gx = gx0; gx <= gx1; gx++) {
            for (j = head[gy * bc + gx]; j >= 0; j = link[j]) {
              if (j === i) continue;
              var o = fl[j];
              var dx = o.x - b.x, dy = o.y - b.y;
              var d2 = dx * dx + dy * dy;
              if (d2 > NEI2) continue;
              var wt = 1 - d2 / NEI2;
              wsum += wt;
              cx += dx * wt; cy += dy * wt;
              avx += o.vx * wt; avy += o.vy * wt;
              nsp += Math.sqrt(o.vx * o.vx + o.vy * o.vy) * wt;
              if (d2 < SEP2) {
                var d = Math.sqrt(d2) || 0.001;
                var s2 = 1 - d / SEP;
                spx -= (dx / d) * s2; spy -= (dy / d) * s2;
              }
            }
          }
        }

        var sp0 = Math.sqrt(b.vx * b.vx + b.vy * b.vy) || 1;
        var ux = b.vx / sp0, uy = b.vy / sp0;

        /* QUORUM. The evidence now scales the command instead of merely
           choosing its direction: 0.04 at wsum 0.05, 0.45 at 1.0, 0.71 at
           2.9. One distant bird can no longer swing a heading, and the step
           at the old wsum gate is gone. */
        var q = wsum / (wsum + 1.2);
        var fx = 0, fy = 0, ox = 0, oy = 0;
        if (wsum > 1e-4) {
          ox = cx / wsum; oy = cy / wsum;
          var om = Math.sqrt(ox * ox + oy * oy);
          if (om > 1e-3) {
            /* arrival ramp: without it a bird sitting at its own
               neighbourhood centre acts at full strength on a few px of
               residual whose DIRECTION is numerical noise, re-aimed every
               frame — the jitter is worst exactly where the flock is best */
            var g = b.cruise * Math.min(1, om / (NEI * 0.45)) / om;
            fx += (ox * g - b.vx) * COH * q;
            fy += (oy * g - b.vy) * COH * q;
          }
          /* alignment on the RAW weighted mean velocity, not its direction.
             Self-grading — when neighbours disagree the mean is short and the
             command is weak — and matching it gives speed matching for free,
             which is what lets a change of pace travel as a wave. */
          fx += (avx / wsum - b.vx) * ALI * q;
          fy += (avy / wsum - b.vy) * ALI * q;
        }
        var sm = Math.sqrt(spx * spx + spy * spy);
        if (sm > 1e-3) {
          /* the 1 - d/SEP ramp survives into the magnitude now */
          var gs = b.cruise / sm, rr2 = Math.min(1, sm);
          fx += (spx * gs - b.vx) * SEPW * rr2;
          fy += (spy * gs - b.vy) * SEPW * rr2;
        }

        /* wander in the BODY frame at a per-bird rate. Shared world-frame
           wander was a synchronised differential force between neighbours —
           a shear cohesion had to fight continuously. As a slow lateral curl
           it is an arc of radius v/omega, 1200-2700px: larger than the
           screen, so a lone bird "follows one direction" instead of orbiting. */
        b.wph += b.wrate * dt;
        var wv = Math.sin(b.wph) * 0.18 * b.cruise;
        fx += -uy * wv; fy += ux * wv;

        /* EDGES ARE WALLS, not a seam. This is the single change that stops
           the whole screen agreeing on one heading.

           On a torus, alignment is a consensus with nothing to break it: the
           population converges on one global direction and cohesion winds the
           aligned mass into a mill — one enormous spiral, every time, which
           is exactly what was on screen. It is not a tuning failure, it is
           the ordered phase of the model, and no weight fixes it because the
           symmetry is never broken.

           A bounded arena breaks it continuously. Every wall turns a group,
           splits it against its neighbours' heading, and hands the flock a
           new direction that nothing else in the system chose. Ben Eater's
           demo gets its variety from exactly this. */
        var eu = 0, edx = 0, edy = 0;
        if (b.x < MG) { var t1 = 1 - b.x / MG; edx += t1; if (t1 > eu) eu = t1; }
        else if (b.x > W - MG) { var t2 = 1 - (W - b.x) / MG; edx -= t2; if (t2 > eu) eu = t2; }
        if (b.y < MG) { var t3 = 1 - b.y / MG; edy += t3; if (t3 > eu) eu = t3; }
        else if (b.y > H - MG) { var t4 = 1 - (H - b.y) / MG; edy -= t4; if (t4 > eu) eu = t4; }
        if (edx || edy) {
          var em = Math.sqrt(edx * edx + edy * edy);
          var ge = b.cruise / em;
          fx += (edx * ge - b.vx) * 2.2 * eu;
          fy += (edy * ge - b.vy) * 2.2 * eu;
        }

        /* ONE command, on the lateral axis only. Four separately-clipped
           forces used to be summed to as much as 4.1F with an 81% jump the
           instant separation tripped, so the turn radius was redrawn
           discontinuously every frame — curvature that jumps cannot draw an
           arc. Now a_lateral = force * cmd exactly, so R = v²/(force*cmd) is
           smooth and graded: gentle demands give wide arcs, hard demands
           saturate at v²/force. Dropping the tangential component is
           deliberate; the speed law below owns magnitude, and the two are no
           longer fighting over it. */
        var dvx = b.vx + fx, dvy = b.vy + fy;
        var dm = Math.sqrt(dvx * dvx + dvy * dvy) || 1;
        var sn = (ux * dvy - uy * dvx) / dm;
        var cs = (ux * dvx + uy * dvy) / dm;
        var cmd = cs > 0 ? sn / 0.55 : (sn >= 0 ? 1 : -1);
        if (cmd > 1) cmd = 1; else if (cmd < -1) cmd = -1;
        /* a bird may bank harder to avoid a wall than it ever would to follow
           the flock. Without the boost the edge rule proposes a turn the
           lateral clip then refuses to execute, and the bird slides along the
           glass instead of coming about — which is the ugly version of this. */
        var lim = b.force * (1 + 2.4 * eu);
        /* first-order lag on the turn: tau ~8 frames against a heading
           constant of ~90, so it cannot ring. It low-passes whatever
           switching noise survives, and turns the alignment response from
           overdamped — where a turn dies within a neighbour or two — to
           underdamped, which is what lets a turn started at one edge arrive
           at the far side as a visible wave. */
        var p = lim * cmd;
        b.pl += (p - b.pl) * 0.12 * dt;
        ax[i] = -uy * b.pl; ay[i] = ux * b.pl;

        b.q = q; b.wsum = wsum; b.nsp = nsp;
        b.fore = wsum > 1e-4 ? Math.max(-1, Math.min(1, (ox * ux + oy * uy) / NEI)) : 0;
      }

      /* SPEED. A bounded first-order lag with an explicit fixed point, which
         is what the old multiplicative bank-drag charge lacked. Load is the
         lateral command as a fraction of authority — speed-independent, so
         slowing down cannot feed back into turning harder. Sag is exactly 30%
         at full lock, so the worst-case radius is ~113px rather than the 5px
         pirouette, and the clamps are no longer where the dynamics live. */
      for (i = 0; i < N; i++) {
        var bb = fl[i];
        bb.vx += ax[i] * dt; bb.vy += ay[i] * dt;
        var sp2 = Math.sqrt(bb.vx * bb.vx + bb.vy * bb.vy);
        if (sp2 > 1e-6) {
          /* capped at 1: the edge boost can push the command past normal
             authority, and a >100% load would sag the speed past its floor */
          var load = Math.min(1, Math.abs(bb.pl) / bb.force);
          /* fore: birds behind the flock centre push up, birds ahead ease
             off, which is the fore-aft organisation the lateral-only command
             would otherwise have cost us */
          var tgt = bb.cruise * (1 - 0.30 * load) * (1 + 0.18 * bb.fore * bb.q);
          if (bb.wsum > 1e-4) {
            tgt = tgt * (1 - 0.35 * bb.q) + (bb.nsp / bb.wsum) * 0.35 * bb.q;
          }
          var s = sp2 + (tgt - sp2) * 0.018 * dt;
          var lo = bb.cruise * 0.55, hi = bb.cruise * 1.35;
          if (s < lo) s = lo; else if (s > hi) s = hi;
          var kk = s / sp2;
          bb.vx *= kk; bb.vy *= kk;
        }
        bb.x += bb.vx * dt; bb.y += bb.vy * dt;
        /* a backstop, not the mechanism: the edge rule turns birds long
           before here. This only catches a bird pushed out by separation in
           a crowded corner, and it kills the outward component rather than
           bouncing, so nothing ever reflects off the glass. */
        if (bb.x < 0) { bb.x = 0; if (bb.vx < 0) bb.vx = -bb.vx * 0.5; }
        else if (bb.x > W) { bb.x = W; if (bb.vx > 0) bb.vx = -bb.vx * 0.5; }
        if (bb.y < 0) { bb.y = 0; if (bb.vy < 0) bb.vy = -bb.vy * 0.5; }
        else if (bb.y > H) { bb.y = H; if (bb.vy > 0) bb.vy = -bb.vy * 0.5; }
      }

      _ctx.clearRect(0, 0, W, H);
      var L = 11 * SZ(), Wd = 4.2 * SZ();
      /* Birds are grouped into hue bins so colour variance costs six fills
         rather than N state changes. Each bin fills at the MEAN hue of its
         members, not the bin centre — filling at the centre meant that at
         variance 0, where every bird has hue 0, the whole flock landed in bin
         3 and painted at accent+30 degrees. The effect never rendered the
         theme's actual accent in any of the 28 themes. */
      var BINS = 6, bin = [], hs = [], hn = [], bi;
      for (bi = 0; bi < BINS; bi++) { bin[bi] = null; hs[bi] = 0; hn[bi] = 0; }
      for (i = 0; i < N; i++) {
        var qb = fl[i];
        if (qb.hot) continue;
        bi = ((qb.hue + 180) / 360 * BINS) | 0;
        if (bi < 0) bi = 0; else if (bi > BINS - 1) bi = BINS - 1;
        if (!bin[bi]) bin[bi] = [];
        bin[bi].push(qb);
        hs[bi] += qb.hue; hn[bi]++;
      }
      for (bi = 0; bi < BINS; bi++) {
        if (!bin[bi]) continue;
        _ctx.fillStyle = accent(0.5, hs[bi] / hn[bi]);
        _ctx.beginPath();
        for (i = 0; i < bin[bi].length; i++) wing(bin[bi][i], L, Wd);
        _ctx.fill();
      }
      /* the few catching the light — at 0.75 they were a 7.4:1 contrast
         against a 2.8:1 flock, so the eye tracked four individuals instead
         of the mass */
      _ctx.fillStyle = accentHot(0.55);
      _ctx.beginPath();
      for (i = 0; i < N; i++) if (fl[i].hot) wing(fl[i], L, Wd);
      _ctx.fill();
    };
  }

  /* ---- life ------------------------------------------------------------
     Conway, on a torus. The only effect here with STATE rather than
     particles: nothing is positioned, everything is consequence.

     Three things had to be solved to make it a background rather than a
     demo, and the third is the interesting one:

     1. IT STROBES. A cell is alive or dead, so drawing state directly hard-
        cuts every cell between two values every generation. Each cell now
        carries a brightness that EASES toward its target, asymmetrically —
        quick to rise so a birth registers, slow to fall so the board
        breathes. That asymmetry is the whole difference from a seizure.

     2. IT SILTS UP. Soup burns down into still-lifes and blinkers that never
        change again. Brightness also decays with a cell's AGE, so anything
        that has merely sat there for ~16 generations sinks to nothing and
        what remains lit is precisely the part still doing something.

     3. FEEDING IT WAS OBVIOUS. A settled board has to be disturbed or the
        screen dies, but soup appearing out of nowhere in the middle of the
        view reads exactly like what it is — someone dropping a rock in the
        pond. So THE GRID IS BIGGER THAN THE VIEWPORT. There is a margin of
        cells off every edge, and a disturbance is a small colony of gliders
        seeded out there, aimed inward. It sails in under its own rules, at
        c/4, and arrives looking like something that was always out there and
        happened to travel here. Nothing is ever placed where you can see it
        appear.

     Colour, when the variance knob asks for it, is INHERITED: a newborn
     takes the average hue of the three neighbours that produced it, plus a
     little mutation. Lineages drift apart over hundreds of generations, so
     a glider carries its ancestry across the board and stains whatever it
     crashes into. */
  function fxLife() {
    var CELL = 0, cols = 0, rows = 0, mx = 0, my = 0;   /* mx/my = margin */
    var cur = null, nxt = null, age = null, lum = null, hue = null;
    var acc = 0, sinceFeed = 0, hueLim = 0;
    var STEP = 11;                    /* frames per generation at speed 1 */
    /* Conway's own behaviour is not negotiable and should not be: a big
       random colony really does burn down fast, and two colonies colliding
       really do usually annihilate rather than merge. That is Life. What IS
       ours to choose is how long the survivors stay legible — sinking them
       after 16 generations made the board read as "everything dies", when
       most of what died was only the DISPLAY giving up on it. */
    var AGEF = 30;                    /* generations for a survivor to sink */
    var FLOOR = 0.09;                 /* what a still-life settles at */
    var MARGIN = 12;                  /* cells of off-screen staging area */

    function soup(x0, y0, w, h, p) {
      for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
        if (Math.random() >= p) continue;
        var i = ((y0 + y + rows) % rows) * cols + ((x0 + x + cols) % cols);
        cur[i] = 1; age[i] = 0; hue[i] = (Math.random() * 256) | 0;
      }
    }

    /* the five-cell glider, mirrored into whichever diagonal it should
       travel along. It moves one cell every four generations, forever. */
    function glider(ox, oy, dx, dy, h) {
      var pat = [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]];
      for (var k = 0; k < 5; k++) {
        var px = dx < 0 ? 2 - pat[k][0] : pat[k][0];
        var py = dy < 0 ? 2 - pat[k][1] : pat[k][1];
        var i = ((oy + py + rows) % rows) * cols + ((ox + px + cols) % cols);
        cur[i] = 1; age[i] = 0; hue[i] = h;
      }
    }

    /* a colony arriving from off-screen. Seeded in the margin, aimed in. */
    function flotilla() {
      var side = (Math.random() * 4) | 0;
      var dx, dy, ox, oy;
      if (side === 0) { dx = 1; ox = 1; oy = (Math.random() * rows) | 0; }
      else if (side === 1) { dx = -1; ox = cols - 6; oy = (Math.random() * rows) | 0; }
      else { dx = Math.random() < 0.5 ? 1 : -1; ox = (Math.random() * cols) | 0; }
      if (side === 2) { dy = 1; oy = 1; }
      else if (side === 3) { dy = -1; oy = rows - 6; }
      else dy = Math.random() < 0.5 ? 1 : -1;
      /* one lineage per colony, so an arriving fleet reads as related */
      var h = (Math.random() * 256) | 0;
      var k = 1 + ((Math.random() * 3) | 0);
      for (var g = 0; g < k; g++) {
        glider(ox + ((Math.random() * 8) | 0), oy + ((Math.random() * 8) | 0),
          dx, dy, (h + ((Math.random() * 12) | 0) - 6) & 255);
      }
    }

    function build() {
      CELL = Math.max(4, Math.round(11 * SZ()));
      mx = MARGIN; my = MARGIN;
      cols = Math.max(8, Math.ceil(W / CELL)) + mx * 2;
      rows = Math.max(8, Math.ceil(H / CELL)) + my * 2;
      var n = cols * rows;
      cur = new Uint8Array(n); nxt = new Uint8Array(n);
      age = new Uint8Array(n); lum = new Float32Array(n);
      hue = new Uint8Array(n);
      soup(0, 0, cols, rows, Math.min(0.42, 0.20 * DEN()));
      acc = 0; sinceFeed = 0;
    }

    function step() {
      var x, y;
      for (y = 0; y < rows; y++) {
        var yu = ((y - 1 + rows) % rows) * cols;
        var yd = ((y + 1) % rows) * cols;
        var yc = y * cols;
        for (x = 0; x < cols; x++) {
          var xl = (x - 1 + cols) % cols, xr = (x + 1) % cols;
          var a1 = cur[yu + xl], a2 = cur[yu + x], a3 = cur[yu + xr];
          var a4 = cur[yc + xl], a5 = cur[yc + xr];
          var a6 = cur[yd + xl], a7 = cur[yd + x], a8 = cur[yd + xr];
          var n = a1 + a2 + a3 + a4 + a5 + a6 + a7 + a8;
          var i = yc + x, was = cur[i];
          var v = was ? (n === 2 || n === 3 ? 1 : 0) : (n === 3 ? 1 : 0);
          nxt[i] = v;
          if (v !== was) {
            age[i] = 0;
            if (v && hueLim) {
              /* inheritance: the mean hue of the three parents, nudged.
                 Averaged on the raw byte, so a lineage drifts smoothly
                 instead of snapping between families. */
              var hs = a1 * hue[yu + xl] + a2 * hue[yu + x] + a3 * hue[yu + xr]
                + a4 * hue[yc + xl] + a5 * hue[yc + xr]
                + a6 * hue[yd + xl] + a7 * hue[yd + x] + a8 * hue[yd + xr];
              hue[i] = ((hs / n) + (Math.random() * 7 | 0) - 3) & 255;
            }
          } else if (v && age[i] < 250) age[i]++;
        }
      }
      var sw = cur; cur = nxt; nxt = sw;

      /* The honest stagnation test is not "did the board change" — a field
         of blinkers changes every generation and is still nothing to look
         at. With brightness decaying by age, the question that matters is
         how much is still VISIBLE. */
      var total = cols * rows, vis = 0, i;
      for (i = 0; i < total; i++) if (cur[i] && age[i] < AGEF) vis++;
      sinceFeed++;
      /* a steady trickle as well as a rescue. Feeding only when the board has
         already gone quiet makes every arrival coincide with an empty screen,
         which is precisely what makes it legible as feeding. Colonies that
         keep turning up regardless are just traffic. */
      if ((vis < total * 0.030 && sinceFeed > 8) || sinceFeed > P("arrivals")) {
        flotilla(); sinceFeed = 0;
      }
    }

    var paths = [], HB = 1, LV = 5;

    return function (dt) {
      if (!cur || CELL !== Math.max(4, Math.round(11 * SZ()))
        || cols !== Math.max(8, Math.ceil(W / CELL)) + MARGIN * 2) build();
      hueLim = (_varies.hue || 0) * BV.bgfx.spread * 0.5;
      AGEF = P("memory");
      FLOOR = P("afterglow") * 0.01;

      acc += dt;
      var guard = 0;
      while (acc >= STEP && guard < 3) { acc -= STEP; step(); guard++; }
      if (acc > STEP) acc = STEP;

      var total = cols * rows, i;
      for (i = 0; i < total; i++) {
        var target = 0;
        if (cur[i]) {
          var a = age[i];
          target = a >= AGEF ? FLOOR : FLOOR + (1 - FLOOR) * (1 - a / AGEF);
        }
        var l = lum[i];
        var k = (target > l ? 0.20 : 0.035) * dt;
        if (k > 1) k = 1;
        lum[i] = l + (target - l) * k;
      }

      /* bins are hue x brightness; with no hue variance HB collapses to 1
         and this is exactly the five-fill version it was before */
      HB = hueLim > 0.5 ? 6 : 1;
      var bins = HB * LV, b;
      for (b = 0; b < bins; b++) paths[b] = new Path2D();
      var g = CELL > 6 ? 1 : 0, s = CELL - g;
      var ox = -mx * CELL, oy = -my * CELL;
      for (i = 0; i < total; i++) {
        var v = lum[i];
        if (v < 0.02) continue;
        var px = (i % cols) * CELL + ox;
        if (px < -CELL || px > W) continue;          /* margin stays unseen */
        var py = ((i / cols) | 0) * CELL + oy;
        if (py < -CELL || py > H) continue;
        var lb = (v * LV) | 0; if (lb > LV - 1) lb = LV - 1;
        var hb = HB === 1 ? 0 : ((hue[i] / 256 * HB) | 0) % HB;
        paths[hb * LV + lb].rect(px, py, s, s);
      }
      _ctx.clearRect(0, 0, W, H);
      for (b = 0; b < bins; b++) {
        var hOff = HB === 1 ? 0 : (((b / LV) | 0) + 0.5) / HB * 2 * hueLim - hueLim;
        _ctx.fillStyle = accent(0.06 + 0.40 * (((b % LV) + 0.5) / LV), hOff);
        _ctx.fill(paths[b]);
      }
    };
  }

  /* ---- reaction --------------------------------------------------------
     Gray-Scott reaction-diffusion. Two notional chemicals on a grid: one is
     fed in everywhere, the other consumes it (U + 2V -> 3V) and is drained
     away. Both diffuse, at different rates. That is the entire model — two
     lines of arithmetic per cell:

         u' = Du·∇²u − u·v² + F·(1 − u)
         v' = Dv·∇²v + u·v² − (F + k)·v

     Nothing in there mentions spots, or stripes, or splitting. They are all
     consequences of one substance spreading faster than the other, which is
     the reason this is worth having over something drawn: a spiral is a
     picture OF mathematics, and this is mathematics actually running. Cells
     divide, fronts collide and annihilate, mazes knit themselves closed.

     F and k are drifted very slowly through the interesting corner of the
     parameter space, so the medium keeps changing its mind about what kind
     of thing it wants to be — mitosis for a while, then coral, then worms.

     Rendered by writing the grid straight into an ImageData at grid
     resolution and letting drawImage do the upscale: one smooth blit
     instead of thousands of rects, and the interpolation is free softening.

     The timestep is what makes it honour SPEED honestly. The scheme is
     stable up to h = 1, so a slow speed passes a genuinely smaller h rather
     than skipping frames — real slow motion, not stutter. */
  function fxReaction() {
    var CELL = 0, cols = 0, rows = 0, n = 0;
    var u = null, v = null, u2 = null, v2 = null;
    var off = null, octx = null, img = null;
    var Du = 0.16, Dv = 0.08, t = 0;
    var fcol = null, fld = null, kcol = null, kld = null;   /* parameter field */

    function blob(cx, cy, r) {
      for (var y = -r; y <= r; y++) for (var x = -r; x <= r; x++) {
        if (x * x + y * y > r * r) continue;
        var xx = cx + x, yy = cy + y;
        if (xx < 0 || xx >= cols || yy < 0 || yy >= rows) continue;
        var i = yy * cols + xx;
        u[i] = 0.5; v[i] = 0.25 + Math.random() * 0.2;
      }
    }

    /* The grid pitch. drawImage upscales this grid, so the pitch IS the blur
       radius — the one number that decides how sharp the result can be. It
       must be read identically here and by the rebuild guard below, which is
       why it is a function and not two copies of an expression. */
    function pitch() { return Math.max(4, Math.round(6 * SZ())); }
    function build() {
      CELL = pitch();
      cols = Math.max(16, Math.ceil(W / CELL));
      rows = Math.max(16, Math.ceil(H / CELL));
      n = cols * rows;
      u = new Float32Array(n); v = new Float32Array(n);
      u2 = new Float32Array(n); v2 = new Float32Array(n);
      for (var i = 0; i < n; i++) { u[i] = 1; v[i] = 0; }
      var seeds = CNT(5);
      for (i = 0; i < seeds; i++) {
        blob((Math.random() * cols) | 0, (Math.random() * rows) | 0,
          Math.round(rr(3, 7)));
      }
      fcol = new Float32Array(cols); kcol = new Float32Array(cols);
      fld = new Float32Array(rows); kld = new Float32Array(rows);
      off = document.createElement("canvas");
      off.width = cols; off.height = rows;
      octx = off.getContext("2d");
      img = octx.createImageData(cols, rows);
      t = 0;
    }

    /* one explicit Euler step. The laplacian wraps, so patterns run off one
       edge and back in the other instead of dying against a wall.

       F and k are not constants across the grid. They vary with POSITION,
       through a field that drifts, and that is this effect's answer to the
       problem every other one here solved with an injection: nothing has to
       be dropped in to keep it moving, because the rules themselves are
       slowly changing underneath the pattern. A region that has settled into
       stable spots finds itself, a minute later, somewhere that wants worms,
       and has to rearrange. The decay is in the medium, not in the cells.

       The field is separable — one array along x, one along y, summed — so
       it costs two lookups per cell instead of four trig calls. */
    function react(h, F0, k0, fA, kA) {
      var x, y, i;
      for (y = 0; y < rows; y++) {
        var yu = ((y - 1 + rows) % rows) * cols;
        var yd = ((y + 1) % rows) * cols;
        var yc = y * cols;
        var fyv = fld[y], kyv = kld[y];
        for (x = 0; x < cols; x++) {
          var xl = (x - 1 + cols) % cols, xr = (x + 1) % cols;
          var F = F0 + fA * (fcol[x] + fyv);
          var k = k0 + kA * (kcol[x] + kyv);
          i = yc + x;
          var uu = u[i], vv = v[i];
          var lu = u[yc + xl] + u[yc + xr] + u[yu + x] + u[yd + x] - 4 * uu;
          var lv = v[yc + xl] + v[yc + xr] + v[yu + x] + v[yd + x] - 4 * vv;
          var r = uu * vv * vv;
          var nu = uu + (Du * lu - r + F * (1 - uu)) * h;
          var nv = vv + (Dv * lv + r - (F + k) * vv) * h;
          u2[i] = nu < 0 ? 0 : nu > 1 ? 1 : nu;
          v2[i] = nv < 0 ? 0 : nv > 1 ? 1 : nv;
        }
      }
      var sw = u; u = u2; u2 = sw;
      sw = v; v = v2; v2 = sw;
    }

    return function (dt) {
      /* ONE expression for the pitch, shared with build(). These were two
         copies that disagreed once the grain was made finer, so the guard
         never matched, build() ran every single frame, and the field was
         re-seeded before it could develop — which on screen is a strobe. */
      if (!u || CELL !== pitch()
        || cols !== Math.max(16, Math.ceil(W / CELL))) build();
      t += dt;

      /* a slow tour of the interesting corner: coral, mitosis, worms, all
         without ever being told what any of those are */
      /* the sliders set the CENTRE of the tour; the drift still wanders
         around it, so setting a spot regime does not freeze the effect — it
         moves where it wanders. wander 0 pins it exactly. */
      var wa = P("wander") / 100;
      var F = P("feed") + 0.0055 * wa * Math.sin(t * 0.00021);
      var k = P("kill") + 0.0026 * wa * Math.sin(t * 0.00013 + 1.7);

      /* ...and the same tour taken SPATIALLY, drifting. Rebuilt each frame,
         which is cols + rows trig calls rather than one per cell. */
      var i2;
      for (i2 = 0; i2 < cols; i2++) {
        fcol[i2] = Math.sin(i2 * 0.031 + t * 0.00085);
        kcol[i2] = Math.sin(i2 * 0.019 - t * 0.00062 + 2.1);
      }
      for (i2 = 0; i2 < rows; i2++) {
        fld[i2] = Math.cos(i2 * 0.026 - t * 0.00071);
        kld[i2] = Math.cos(i2 * 0.023 + t * 0.00054 + 0.7);
      }

      var pa = P("patch") / 100, gain = P("ink");
      var sh = P("sharp") * 0.01;
      var rem = dt, guard = 0;
      while (rem > 0.001 && guard < 3) {
        var h = rem > 1 ? 1 : rem;      /* stable up to 1; below it, real
                                           slow motion rather than skipping */
        react(h, F, k, 0.0022 * pa, 0.0016 * pa);
        rem -= h; guard++;
      }

      var c = drift(rgbOf("--accent", "e2b714"));
      var d = img.data, i, p = 0, alive = 0;
      for (i = 0; i < n; i++) {
        /* a smoothstep instead of a linear ramp with a clamp. The linear
           version spends most of its range in the soft middle, which is what
           reads as fuzz once drawImage has interpolated it; the S-curve puts
           the transition in a narrow band and leaves the interior flat. */
        var a = v[i] * gain;
        if (a > 1) a = 1;
        if (sh > 0) {
          var e0 = 0.5 - 0.5 * sh, e1 = 0.5 + 0.5 * sh;
          a = a <= e0 ? 0 : a >= e1 ? 1 : (a - e0) / (e1 - e0);
          a = a * a * (3 - 2 * a);
        }
        if (a > 0.25) alive++;
        d[p] = c[0]; d[p + 1] = c[1]; d[p + 2] = c[2];
        d[p + 3] = (a * 235 * I()) | 0;
        p += 4;
      }
      octx.putImageData(img, 0, 0);
      _ctx.clearRect(0, 0, W, H);
      _ctx.imageSmoothingEnabled = true;
      _ctx.drawImage(off, 0, 0, cols, rows, 0, 0, W, H);

      /* the medium can burn itself out at some parameter values; drop a new
         blob in rather than leave a blank screen */
      if (alive < n * 0.004) blob((Math.random() * cols) | 0,
        (Math.random() * rows) | 0, Math.round(rr(3, 7)));
    };
  }

  /* ---- suspension ------------------------------------------------------
     Particles suspended in a moving fluid, seen close up: motes streaking
     through a current while heavy grains barely shift.

     The idea worth keeping is that SIZE AND SPEED ARE NOT THE SAME AXIS.
     Nothing here is told how fast to go. Every particle is pushed by the
     same flow field and simply lags it according to its mass, and mass is
     size times a DENSITY drawn independently — so a big light floater gets
     shoved along nearly as fast as a speck, while a small dense grain of
     the same diameter as its neighbour sits there and refuses to move.
     Getting overtaken by something four times your size is the whole
     pleasure of watching it, and it falls straight out of m = r·ρ.

     The fluid is layered sines: direction from one sum, SPEED from another,
     so the field has genuine fast currents and dead eddies rather than a
     uniform drift. A particle crossing from one into the other visibly
     accelerates, and heavy ones take long enough to do it that they trail
     behind their lighter neighbours the whole way. */
  function fxSuspension() {
    var N = CNT(190), ps = [], t = 0, i;

    for (i = 0; i < N; i++) {
      /* cubed so the population is mostly fine grit with a few real lumps */
      var r = (0.7 + Math.pow(Math.random(), P("grain")) * 5.5) * vary("size", 1);
      var dens = rr(0.12, 1.5) * vary("mass", 1);
      var m = Math.max(0.05, r * dens * 1.6);
      ps.push({
        x: Math.random() * W, y: Math.random() * H, vx: 0, vy: 0,
        r: r,
        /* responsiveness: how quickly it matches the fluid. This is the only
           thing that differs between particles, and it is pure consequence */
        m: m,   /* stored, not baked into resp — see the grip slider */
        hue: varyHue(),
      });
    }

    /* Velocity comes from a STREAM FUNCTION: vx = dpsi/dy, vy = -dpsi/dx.
       Any field built this way is divergence-free, and that matters here for
       a very practical reason. A field of plain summed sines has sources and
       sinks in it, so particles drain into the sinks and within a minute the
       screen is empty except for one clot in the corner. Incompressible flow
       cannot do that — whatever enters a region leaves it — so the
       suspension keeps circulating indefinitely.

       Speed still varies from place to place, because the gradient does:
       tight bends in psi are fast water, flat regions are dead eddies. That
       came for free rather than being a second hand-authored sum. */
    /* A UNIFORM DRIFT, slowly rotating, added to the stream function.
       Without it the field's stagnation points — the zeros of the sines — sit
       at fixed places, and anything that wanders into one simply stops and
       stays stopped. Superimposing a drift means |v| has no permanent zero
       anywhere, and the stalling points themselves move continuously, so
       nothing can accumulate in a corner. psi += U*y - V*x is uniform flow;
       rotating (U,V) sweeps the whole field. */
    var DRIFT = 0.55;
    var A = 620, B = 430;
    var ka = 0.0034, kb = 0.0029, kc = 0.0021, kd = 0.0018;
    var VMAX = 3.4;
    var _v = [0, 0];
    var ed = 1, ch = 1;
    function vel(x, y) {
      var Ae = A / ed, Be = B / ed;
      var kae = ka * ed, kbe = kb * ed, kce = kc * ed, kde = kd * ed;
      var p1 = x * kae + t * 0.0009 * ch, p2 = y * kbe - t * 0.0007 * ch;
      var p3 = x * kce - t * 0.0005 * ch, p4 = y * kde + t * 0.0004 * ch;
      var s1 = Math.sin(p1), c1 = Math.cos(p1);
      var s2 = Math.sin(p2), c2 = Math.cos(p2);
      var s3 = Math.sin(p3), c3 = Math.cos(p3);
      var s4 = Math.sin(p4), c4 = Math.cos(p4);
      /* psi = A*sin(x)*cos(y) + B*sin(x)*sin(y) — a proper cellular second
         term. The old one was B*sin(k(x+y)), whose contribution to vx and vy
         is the SAME quantity with opposite signs, so wherever it dominated
         the flow collapsed onto exact 45° lines. That was the one-pixel
         filament: not a fluid feature, a degenerate stream function. */
      var da = t * 0.00023;
      var vx = -Ae * kbe * s1 * s2 + Be * kde * s3 * c4 + Math.cos(da) * DRIFT;
      var vy = -(Ae * kae * c1 * c2 + Be * kce * c3 * s4) + Math.sin(da) * DRIFT;
      /* and a ceiling, because the gradient can stack in a small region and
         nothing downstream bounds it */
      var m = Math.sqrt(vx * vx + vy * vy);
      if (m > VMAX) { var k = VMAX / m; vx *= k; vy *= k; }
      _v[0] = vx; _v[1] = vy;
    }

    var LV = 4, paths = [], HB = 1;

    return function (dt) {
      t += dt;
      /* eddy compensates: wavenumbers scale by ed while amplitudes scale by
         1/ed, and since every velocity term is an amplitude x wavenumber
         product, the peak flow SPEED is invariant. The slider changes the
         scale of the flow and nothing else. */
      ed = P("eddy") / 100; ch = P("churn") / 100;
      var gp = P("grip") / 100, sk = P("streak") / 100;
      var hueLim = (_varies.hue || 0) * BV.bgfx.spread * 0.5;
      HB = hueLim > 0.5 ? 4 : 1;
      var bins = HB * LV, b;
      for (b = 0; b < bins; b++) paths[b] = new Path2D();
      var streak = new Path2D(), any = false;

      for (i = 0; i < N; i++) {
        var p = ps[i];
        vel(p.x, p.y);
        var resp = Math.min(0.42, 0.075 * gp / p.m);
        var k = resp * dt; if (k > 1) k = 1;
        p.vx += (_v[0] - p.vx) * k;
        p.vy += (_v[1] - p.vy) * k;
        /* Brownian jitter, scaled by responsiveness — which is to say by
           1/mass, which is what Brownian motion actually is. It earns its
           place twice: it is the correct physics for a suspension, and it
           stops light particles from collapsing onto a single shared
           streamline and rendering as one bright hairline. The filaments
           still form, they just no longer become one pixel wide. */
        var jit = resp * 1.1 * dt;
        p.vx += (Math.random() - 0.5) * jit;
        p.vy += (Math.random() - 0.5) * jit;
        p.x += p.vx * dt; p.y += p.vy * dt;
        var pr = p.r * SZ();
        if (p.x < -pr) p.x += W + pr * 2; else if (p.x > W + pr) p.x -= W + pr * 2;
        if (p.y < -pr) p.y += H + pr * 2; else if (p.y > H + pr) p.y -= H + pr * 2;

        /* small things are crisp and bright, big ones soft and dim, so the
           lumps read as depth rather than as blobs sitting on top */
        var l = 1 - Math.min(0.82, pr / 13);
        var lb = (l * LV) | 0; if (lb > LV - 1) lb = LV - 1; if (lb < 0) lb = 0;
        var hb = HB === 1 ? 0 : ((p.hue + 180) / 360 * HB | 0) % HB;
        var pp = paths[hb * LV + lb];
        pp.moveTo(p.x + pr, p.y);
        pp.arc(p.x, p.y, pr, 0, 7);

        /* a streak only where the fluid has genuinely picked something up —
           it is the light ones that show it, which is the point */
        var v2 = p.vx * p.vx + p.vy * p.vy;
        /* ramp in over a band instead of switching on at a threshold. A
           hard cut means a particle riding the boundary has its whole tail
           appear and vanish frame to frame — the flashing. Length now grows
           from zero across 2.2..5.0, and a zero-length line draws nothing, so
           the tail retracts smoothly instead of being switched off. */
        if (sk > 0 && v2 > 2.2 && pr < 4) {
          var ramp = (v2 - 2.2) / 2.8;
          if (ramp > 1) ramp = 1;
          var sv = Math.sqrt(v2);
          var sl = Math.min(9, sv * 2.4) * sk * ramp * ramp;
          if (sl > 0.35) {
            streak.moveTo(p.x, p.y);
            streak.lineTo(p.x - p.vx * sl / sv, p.y - p.vy * sl / sv);
            any = true;
          }
        }
      }

      _ctx.clearRect(0, 0, W, H);
      for (b = 0; b < bins; b++) {
        var hOff = HB === 1 ? 0 : (((b / LV) | 0) + 0.5) / HB * 2 * hueLim - hueLim;
        _ctx.fillStyle = accent(0.07 + 0.34 * (((b % LV) + 0.5) / LV), hOff);
        _ctx.fill(paths[b]);
      }
      if (any) {
        _ctx.strokeStyle = accent(0.16);
        _ctx.lineWidth = Math.max(0.6, 0.8 * SZ());
        _ctx.stroke(streak);
      }
    };
  }

  /* ---- tendrils --------------------------------------------------------
     Growing, branching, decaying filaments. Each arm curves through a
     smooth pseudo-noise field, tapers as it ages, and occasionally forks;
     the trail is a bg wash, so what it drew is still fading long after the
     head has moved on. Reads organic and unwell — the dark/creepy lane. */
  function fxTendrils() {
    var MAX = CNT(24), arms = [], t = 0, washDebt = 0;

    function spawn() {
      var edge = (Math.random() * 4) | 0, x, y, a;
      if (edge === 0) { x = Math.random() * W; y = -8; a = Math.PI / 2; }
      else if (edge === 1) { x = W + 8; y = Math.random() * H; a = Math.PI; }
      else if (edge === 2) { x = Math.random() * W; y = H + 8; a = -Math.PI / 2; }
      else { x = -8; y = Math.random() * H; a = 0; }
      return {
        x: x, y: y, a: a + rr(-0.55, 0.55),
        v: rr(0.35, 0.95), w: rr(1.4, 3.2),
        curl: rr(0.004, 0.016) * (Math.random() < 0.5 ? -1 : 1),
        life: 0, span: rr(320, 900), gen: 0,
      };
    }
    for (var i = 0; i < MAX; i++) { var a0 = spawn(); a0.life = Math.random() * a0.span; arms.push(a0); }

    return function (dt) {
      t += dt;
      /* the wash IS the decay, so its alpha scales with dt — otherwise a
         slow speed would fade the trails at the same per-frame rate and the
         tendrils would be stubs instead of long filaments */
      var cu = P("curl") * 0.01, wa = P("wander") * 0.01;
      var br = P("branch") * 0.01, re = P("reach") * 0.01;
      var cap = MAX * (1 + 2 * br);
      /* THE 8-BIT FLOOR. The wash is an alpha composite, so the trail
         decays as (1-a)^frames and the dt in `a` cancels analytically — the
         fade SHOULD already be speed-independent. It is not, because alpha is
         quantised to 1/255: at slow speeds 0.018*dt lands below half a level,
         rounds to zero, and the trail never fades at all, while at high speed
         it clears the floor every frame and fades at once. That is the entire
         bug, and it is why the fade looked like it was tied to the speed knob.
         Bank the fractional alpha and only paint once it clears the floor. */
      washDebt += 0.018 * P("fade") * 0.01 * dt;
      if (washDebt >= 0.006) {
        _ctx.fillStyle = bgWash(washDebt);
        _ctx.fillRect(0, 0, W, H);
        washDebt = 0;
      }
      for (var i2 = arms.length - 1; i2 >= 0; i2--) {
        var m = arms[i2];
        var px = m.x, py = m.y;
        /* layered sines, not real noise: continuous, cheap, and enough that
           neighbouring arms visibly sweep the same way then diverge */
        m.a += (m.curl * cu
          + Math.sin(m.x * 0.0021 + m.y * 0.0017 + t * 0.0016) * 0.013 * wa) * dt;
        m.x += Math.cos(m.a) * m.v * dt;
        m.y += Math.sin(m.a) * m.v * dt;
        m.life += dt;
        var span = m.span * re;
        var k = 1 - m.life / span;
        if (k <= 0 || m.x < -40 || m.x > W + 40 || m.y < -40 || m.y > H + 40) {
          var ns = spawn();
          for (var key in ns) m[key] = ns[key];
          continue;
        }
        _ctx.strokeStyle = accent(0.14 * k);
        _ctx.lineWidth = Math.max(0.4, m.w * SZ() * k);
        _ctx.lineCap = "round";
        _ctx.beginPath(); _ctx.moveTo(px, py); _ctx.lineTo(m.x, m.y); _ctx.stroke();
        if (m.gen < 2 && arms.length < cap && m.life > span * 0.2
          && Math.random() < 0.004 * br * dt) {
          arms.push({
            x: m.x, y: m.y, a: m.a + rr(-0.95, 0.95) * (Math.random() < 0.5 ? 1 : -1),
            v: m.v * 0.9, w: m.w * 0.55,
            curl: -m.curl, life: 0, span: m.span * 0.6, gen: m.gen + 1,
          });
        }
      }
    };
  }

  var HOUSE = "backupviewer";
  var HOUSE2 = "simulations";
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
    { id: "ferrofluid", name: "ferrofluid", cat: HOUSE2, make: fxFerro, res: 1,
      varies: { size: 0.45, speed: 0.30 },
      /* the iso-level is the big one: it decides whether these are droplets or one welded continent */
      params: [
        { k: "surface", name: "surface", lo: 10, hi: 90, def: 45, step: 1, unit: "%", live: true },
        { k: "roam", name: "roam", lo: 0, hi: 200, def: 100, step: 5, unit: "%", live: true },
        { k: "reach", name: "spike reach", lo: 0, hi: 160, def: 100, step: 5, unit: "%", live: true },
        { k: "sats", name: "satellites", lo: 0, hi: 4, def: 2, step: 1, unit: "", live: false },
        { k: "grain", name: "grain", lo: 7, hi: 26, def: 10, step: 1, unit: "px", live: true },
      ],
    },
    { id: "boids", name: "boids", cat: HOUSE2, make: fxBoids, denseMax: 1600,
      varies: { size: 0.45, speed: 0.14, agility: 0.40, hue: 40 },
      /* Reynolds' three weights, plus the two radii they act over and the
         two things that decide whether an arc is possible at all. Defaults
         reproduce the hard-coded behaviour exactly, so adding the rack
         changed nothing until a slider moves. */
      params: [
        { k: "cohesion", name: "cohesion", lo: 0, hi: 250, def: 80, step: 5, unit: "%", live: true },
        { k: "align", name: "alignment", lo: 0, hi: 250, def: 130, step: 5, unit: "%", live: true },
        { k: "separate", name: "separation", lo: 0, hi: 400, def: 160, step: 5, unit: "%", live: true },
        { k: "vision", name: "vision", lo: 40, hi: 400, def: 150, step: 10, unit: "px", live: true },
        { k: "space", name: "personal space", lo: 5, hi: 60, def: 23, step: 1, unit: "%", live: true },
        { k: "agility", name: "agility", lo: 30, hi: 300, def: 100, step: 5, unit: "%", live: false },
        { k: "margin", name: "edge margin", lo: 8, hi: 45, def: 20, step: 1, unit: "%", live: true },
      ],
    },
    { id: "life", name: "life", cat: HOUSE2, make: fxLife,
      varies: { hue: 90 },
      /* how much of the past stays visible, and how often new colonies arrive */
      params: [
        { k: "memory", name: "memory", lo: 2, hi: 120, def: 30, step: 1, unit: "gen", live: true },
        { k: "afterglow", name: "afterglow", lo: 0, hi: 60, def: 9, step: 1, unit: "%", live: true },
        { k: "arrivals", name: "arrivals", lo: 6, hi: 240, def: 85, step: 1, unit: "gen", live: true },
      ],
    },
    { id: "reaction", name: "reaction", cat: HOUSE2, make: fxReaction,
      varies: { hue: 60 },
      /* feed and kill ARE the model: they select spots, stripes, mitosis, coral or worms */
      params: [
        { k: "feed", name: "feed", lo: 0.018, hi: 0.062, def: 0.0345, step: 0.0005, unit: "", live: true },
        { k: "kill", name: "kill", lo: 0.048, hi: 0.064, def: 0.0605, step: 0.0005, unit: "", live: true },
        { k: "wander", name: "wander", lo: 0, hi: 250, def: 100, step: 5, unit: "%", live: true },
        { k: "patch", name: "patchiness", lo: 0, hi: 300, def: 100, step: 10, unit: "%", live: true },
        { k: "ink", name: "ink", lo: 1.2, hi: 9, def: 3.2, step: 0.1, unit: "", live: true },
        { k: "sharp", name: "edge", lo: 0, hi: 95, def: 55, step: 5, unit: "%", live: true },
      ],
    },
    { id: "suspension", name: "suspension", cat: HOUSE2, make: fxSuspension, denseMax: 2000,
      varies: { size: 0.20, mass: 0.7, hue: 110 },
      /* eddy and churn are independent of the speed knob: one is the SCALE of the flow, the other how fast it rearranges */
      params: [
        { k: "eddy", name: "eddy size", lo: 40, hi: 260, def: 100, step: 10, unit: "%", live: true },
        { k: "churn", name: "churn", lo: 0, hi: 300, def: 100, step: 10, unit: "%", live: true },
        { k: "grip", name: "coupling", lo: 25, hi: 400, def: 100, step: 5, unit: "%", live: true },
        { k: "streak", name: "streaks", lo: 0, hi: 350, def: 100, step: 10, unit: "%", live: true },
        { k: "grain", name: "grain", lo: 1, hi: 6, def: 3, step: 0.25, unit: "", live: false },
      ],
    },
    { id: "tendrils", name: "tendrils", cat: HOUSE2, make: fxTendrils,
      varies: { size: 0.45, speed: 0.40, hue: 80 },
      /* fade is the memory of the trail; branch decides whether this is a vine or a nervous system */
      params: [
        { k: "fade", name: "fade", lo: 60, hi: 600, def: 100, step: 10, unit: "%", live: true },
        { k: "curl", name: "curl", lo: 0, hi: 400, def: 100, step: 10, unit: "%", live: true },
        { k: "wander", name: "wander", lo: 0, hi: 300, def: 100, step: 10, unit: "%", live: true },
        { k: "branch", name: "branching", lo: 0, hi: 300, def: 100, step: 10, unit: "%", live: true },
        { k: "reach", name: "reach", lo: 20, hi: 400, def: 100, step: 10, unit: "%", live: true },
      ],
    },
  ];
  var CREDITS = {};
  CREDITS[HOUSE] = "hand-rolled for backupviewer";
  CREDITS[HOUSE2] = "hand-rolled — flocking, cellular automata, reaction-diffusion and fluid, all emergent from their own rules";
  CREDITS[ODY] = "inspired by odysseus (pewdiepie) — AGPL-3.0, combined per GPLv3 §13";

  function byId(id) { return EFFECTS.find(function (e) { return e.id === id; }); }

  var persistTune = null;   /* built lazily so BV.debounce is ready */
  var _pdirty = {}, pushP = null;

  /* per-effect params persist one key each, `bgfx_p_<effect>_<key>`, so an
     effect's dials cannot collide with another's and a new effect needs no
     migration. Coalesced through one debounce: dragging a slider must not
     put a write on the wire per pixel. */
  function pushParam(id, k, v) {
    _pdirty[id + "|" + k] = v;
    if (!pushP) pushP = BV.debounce(function () {
      Object.keys(_pdirty).forEach(function (key) {
        var bits = key.split("|");
        BV.api.call("set_setting", "bgfx_p_" + bits[0] + "_" + bits[1],
          _pdirty[key]).catch(function () {});
      });
      _pdirty = {};
    }, 400);
    pushP();
  }

  BV.bgfx = {
    EFFECTS: EFFECTS,
    activeId: "none",
    intensity: 1,   /* 0.1..1 - global alpha (+ spawn rate on the spawny effects) */
    size: 1,        /* 0.5..4 - geometry at each effect's natural dimension */
    speed: 1,       /* 0.1..3   - time dilation, a real dt */
    density: 1,     /* 0.25..20 - how many of a thing (ceiling is per-effect) */
    spread: 1,      /* 0..2     - variance, through declared channels only */
    hue: 0,         /* 0..180   - degrees of colour drift */

    /* the active effect's own dials, and the whole per-effect store so a
       user's tuning survives switching away and back */
    params: function () { return _params; },
    paramSpec: function (id) {
      var t = byId(id || BV.bgfx.activeId);
      return (t && t.params) || [];
    },
    denseMax: function () {
      var t = byId(BV.bgfx.activeId);
      return (t && t.denseMax) || 600;
    },
    setParam: function (k, v, persist) {
      var t = byId(BV.bgfx.activeId);
      if (!t || !t.params) return;
      _params[k] = v;
      var spec = null, i;
      for (i = 0; i < t.params.length; i++) if (t.params[i].k === k) spec = t.params[i];
      /* a param that fixes counts or per-element properties at creation has
         to re-seed; one that is read per frame must NOT, or dragging it
         restarts the effect on every pixel */
      if (spec && !spec.live && t.make) { _step = t.make(); }
      if (persist) {
        if (BV.state.settings) BV.state.settings["bgfx_p_" + t.id + "_" + k] = v;
        pushParam(t.id, k, v);
      }
    },

    activeName: function () {
      var t = byId(BV.bgfx.activeId);
      return (t && t.name) || "off";
    },

    apply: function (settings) {
      settings = settings || {};
      function g(k, d) { return settings[k] != null ? settings[k] : d; }
      BV.bgfx.tune({
        intensity: g("bgfx_intensity", 1),
        size: g("bgfx_size", 1),
        speed: g("bgfx_speed", 1),
        density: g("bgfx_density", 1),
        spread: g("bgfx_spread", 1),
        hue: g("bgfx_hue", 0),
      }, false);
      /* seed the per-effect stores from settings BEFORE the effect is built,
         or the first make() runs on defaults and the saved dials only take
         hold on the next switch */
      EFFECTS.forEach(function (t) {
        if (!t.params) return;
        var store = _pstore[t.id] || (_pstore[t.id] = {});
        t.params.forEach(function (p) {
          var v = settings["bgfx_p_" + t.id + "_" + p.k];
          store[p.k] = v != null ? Number(v) : p.def;
        });
      });
      BV.bgfx.set(settings.bgfx || "none", false);
    },

    /* the theme window's sliders land here; canvas effects pick the new
       values up on their next frame, the CSS looks through --bgfx-i */
    tune: function (opts, persist) {
      function cl(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
      if (opts.intensity != null) BV.bgfx.intensity = cl(opts.intensity, 0.1, 1);
      if (opts.size != null) BV.bgfx.size = cl(opts.size, 0.5, 4);
      if (opts.speed != null) BV.bgfx.speed = cl(opts.speed, 0.1, 3);
      if (opts.density != null) BV.bgfx.density = cl(opts.density, 0.25, 20);
      if (opts.spread != null) BV.bgfx.spread = cl(opts.spread, 0, 2);
      if (opts.hue != null) BV.bgfx.hue = cl(opts.hue, 0, 180);
      syncTuning();
      /* density and variance fix counts and per-element properties at
         creation, so they cannot be picked up mid-flight — re-seed. The
         caller debounces the drag; this is the release. */
      if (opts.reseed) {
        var t = byId(BV.bgfx.activeId);
        if (t && t.make && _canvas) _step = t.make();
      }
      if (!HAS_RAF || REDUCED) { if (_step) _step(1); }  /* static frame keeps up too */
      if (persist) {
        /* mirror into the live settings object FIRST: uiPrefs.apply re-runs
           bgfx.apply(settings) on every pref change, and a stale mirror
           would snap the effect/tuning back to boot-time values */
        var m = {
          bgfx_intensity: BV.bgfx.intensity, bgfx_size: BV.bgfx.size,
          bgfx_speed: BV.bgfx.speed, bgfx_density: BV.bgfx.density,
          bgfx_spread: BV.bgfx.spread, bgfx_hue: BV.bgfx.hue,
        };
        if (BV.state.settings) {
          Object.keys(m).forEach(function (k) { BV.state.settings[k] = m[k]; });
        }
        if (!persistTune) persistTune = BV.debounce(function () {
          Object.keys(m).forEach(function (k) {
            BV.api.call("set_setting", k, m[k]).catch(function () {});
          });
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
            _res = t.res || "dpr";
            _varies = t.varies || {};
            _params = paramsFor(t);
            ensureCanvas();
            sizeCanvas();      /* the effect may want a different backing scale */
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
      if (persist) {
        if (BV.state.settings) BV.state.settings.bgfx = t.id;   /* keep the mirror honest */
        BV.api.call("set_setting", "bgfx", t.id).catch(function () {});
      }
    },
  };
})();
