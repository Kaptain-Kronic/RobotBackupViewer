/* components/meshview.js - BV.meshView: a canvas-2D triangle-mesh viewer.

   Draws scanned-surface triangle meshes with a hand-rolled painter's
   algorithm on a plain 2D canvas. No WebGL - ROADMAP's parking lot bans it
   (rescue-mode plant PCs render in software; canvas 2D still works on that
   path) - and no libraries. All camera math is BV.proj3d.orbitProjector
   (script order: proj3d.js loads before this file): project(p) returns
   [sx, sy, depth] with depth ASCENDING toward the viewer - painter's order
   is "sort ascending, draw in order" - and sy grows DOWN. Orthographic by
   default (distances stay measurable, which is what earns the mm ruler);
   state.persp adds the same mild center-pivoted perspective view3d uses,
   and the ruler hides there - mm-per-px varies with depth under
   perspective, so showing it would be a lie. Redraws are synchronous - no
   requestAnimationFrame (the probe's headless WebView2 does not have it);
   the one timer is a plain debounce for the wheel's settle frame.

   The painter is CPU-bound and its cost tracks FILLED PIXELS more than
   triangles (api.py's measured numbers by CVX_MODEL_MAX_TRIS), so speed
   comes from classic software-renderer moves, each exact or honest:
     - face normals + centroids precomputed once per mesh, never per frame;
     - backface culling ONLY on meshes proven closed and consistently
       wound (every directed edge pairs with its exact reverse): there the
       back faces are always overdrawn, so skipping them cannot change a
       pixel. Open scan surfaces (a range image has no back) fail the
       proof and keep today's two-sided draw - culling them would punch
       holes the data does not have. Decimated soups fail it too (the
       every-k-th sample tears the pairing), which is the safe direction;
     - depth-run batching: consecutive same-shade, same-winding triangles
       share one path and one fill. Windings must not mix inside a
       nonzero-rule path - opposite-wound overlaps would cancel to holes
       per-triangle fills never had;
     - big meshes draw mid-gesture at reduced backing resolution with the
       AA-crack hairline skipped, and the settle frame repaints at 1:1 -
       the resting image is always full quality.

   BV.meshView(container, opts) -> handle
     opts.state        shared, persistable camera state (hand it a
                       BV.tabState slice and the view restores; hand the
                       SAME object to two views and they follow each
                       other). Reads/writes:
                         .az / .el  turntable degrees; defaults -24.8 /
                                    36.8, view3d's deliberately off-grid
                                    house view (axis-aligned geometry seen
                                    exactly edge-on degenerates to a
                                    sliver - do not "tidy" these)
                         .zoom      {x,y,w,h} pan/zoom box in projected
                                    mm, or null = auto-fit
                         .persp     true = perspective projection; the
                                    caller supplies the toggle
     opts.interactive  default true; false renders static (no gestures)
     opts.onDraw       called after every completed repaint - the hook the
                       orientation cube (BV.viewCube) updates from
   handle:
     setMesh(mesh|null)  mesh = { vertices: flat [x,y,z,...] world mm,
                         triangles: flat vertex-index triples,
                         bounds: {min:[3], max:[3]},
                         shown_tris, tri_count, decimated }.
                         The last three ride along for the CALLER to
                         display - this component draws geometry only.
     resize()            re-measure the container, re-size the backing
                         store by devicePixelRatio, redraw
     redraw()            synchronous repaint
     destroy()
     el                  the canvas element
     _stats              last-repaint counters for the probes: {closed,
                         drawn, culled, coarse, ms} - diagnostics, not UI

   BV.meshView.enlarge(mesh, opts) -> {close, view}: in-window lightbox on
   the photos.js openFullscreen pattern - position:fixed inset:0 overlay
   that closes ONLY via the visible close button, Esc, or a clean backdrop
   click (never click-anywhere). It instantiates a fresh meshView sharing
   opts.state, so orientation persists between inline and enlarged views.
   The fullscreen button toggles the HOST window via BV.fullscreen - the
   web Fullscreen API is a silent no-op in WebView2 - and teardown always
   calls BV.fullscreen.exit(). The lightbox carries the same orientation
   cube (BV.viewCube) the inline viewers do, and honors state.persp.
   opts: { state, interactive, onClose }. */
(function () {
  "use strict";

  var SHADES = 24;   /* shade buckets: fewer fillStyle switches, no visible banding */
  /* meshes at or above this many shown triangles draw their mid-gesture
     frames coarse (0.6x backing resolution, no hairline); smaller ones are
     already cheap at full quality. Sized off api.py's measured redraw costs
     (32k tris = 81 ms full-window) - the aim is drag frames under ~15 ms. */
  var COARSE_TRIS = 12000;
  var COARSE_SCALE = 0.6;
  /* painter-order depth quantization: order holds to 1/4096 of the depth
     range - the same class of approximation centroid-depth painting already
     is - and buys an O(n) counting sort over the comparator sort that was
     about a third of a 29k-triangle frame (measured 2026-08-20) */
  var DEPTH_BUCKETS = 4096;

  function remPx() {
    return parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  }

  function cssVar(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }

  /* theme accent -> [h, s, l]; the same reading view3d.js accentHsl() does
     (a tab's private helper, so it cannot be imported from here - promote
     both into a shared primitive when a third caller appears). Non-hex
     theme value falls back to the same neutral hue view3d uses. */
  function accentHsl() {
    var m = /^#?([0-9a-f]{6})$/i.exec(cssVar("--accent"));
    if (!m) return [200, 70, 55];
    var r = parseInt(m[1].slice(0, 2), 16) / 255;
    var g = parseInt(m[1].slice(2, 4), 16) / 255;
    var b = parseInt(m[1].slice(4, 6), 16) / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    var h = 0, l = (max + min) / 2;
    var sat = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
    if (d) {
      if (max === r) h = 60 * (((g - b) / d) % 6);
      else if (max === g) h = 60 * ((b - r) / d + 2);
      else h = 60 * ((r - g) / d + 4);
    }
    return [(h + 360) % 360, sat * 100, l * 100];
  }

  /* the shade ramp, derived from the theme at draw time (never hardcoded):
     accent hue, moderated saturation, lightness from near-dark to light -
     readable on both dark and light --bg2 because the mesh shades itself */
  function shadePalette() {
    var a = accentHsl();
    var h = a[0].toFixed(1);
    var s = Math.min(50, Math.max(12, a[1] * 0.5)).toFixed(0);
    var out = [];
    for (var i = 0; i < SHADES; i++) {
      var l = 18 + 58 * (i / (SHADES - 1));
      out.push("hsl(" + h + "," + s + "%," + l.toFixed(1) + "%)");
    }
    return out;
  }

  BV.meshView = function (container, opts) {
    opts = opts || {};
    var s = opts.state || {};
    if (s.az === undefined) s.az = -24.8;
    if (s.el === undefined) s.el = 36.8;
    s.el = Math.max(-90, Math.min(90, s.el));   /* stale state past a pole */
    if (s.zoom === undefined) s.zoom = null;

    var canvas = BV.el("canvas", { class: "meshview", style:
      "display:block;width:100%;height:100%;touch-action:none" });
    if (opts.interactive !== false) canvas.style.cursor = "grab";
    container.appendChild(canvas);
    var ctx = canvas.getContext("2d");

    var mesh = null;
    var center = null, radius = 0;      /* world bounding sphere, from bounds */
    /* projection caches, allocated once per setMesh: the redraw loop
       allocates nothing per triangle (project() returns one transient
       3-array per VERTEX - short-lived nursery garbage, accepted to keep
       the projection math in proj3d and nowhere else) */
    var vsx = null, vsy = null, vsd = null;              /* per-vertex screen x/y + depth */
    var tDepth = null, tShade = null, tOrder = null;     /* per-triangle */
    var tSign = null, culled = null;                     /* winding sign + backface flag */
    var nrm = null, cen = null;                          /* unit face normals + centroids (world) */
    var closed = false, cullSign = 1;                    /* the closed-mesh proof + its outward sign */
    var P = [0, 0, 0];                                   /* reused projector input */
    var lastProj = null, lastFit = null, lastPersp = null;  /* gestures reuse the draw's camera */
    var coarse = false;                  /* a gesture is live on a big mesh */
    var stats = { closed: false, drawn: 0, culled: 0, coarse: false, ms: 0 };
    var bCount = new Int32Array(DEPTH_BUCKETS + 1);   /* counting-sort scratch */

    function bigMesh() { return !!mesh && mesh.triangles.length / 3 >= COARSE_TRIS; }

    /* one-time per mesh: face normals + centroids (camera-independent), and
       the closed-mesh proof that makes backface culling EXACT (see header).
       Orientation: at the max-x vertex an outward-wound surface's incident
       faces must lean +x on aggregate; a clean inward winding flips the
       cull sign, and an ambiguous lean (0) refuses to cull at all. */
    function prepMesh(nv, nt) {
      var V = mesh.vertices, T = mesh.triangles;
      nrm = new Float32Array(3 * nt);
      cen = new Float32Array(3 * nt);
      var i, a, b, c;
      for (i = 0; i < nt; i++) {
        a = 3 * T[3 * i]; b = 3 * T[3 * i + 1]; c = 3 * T[3 * i + 2];
        var ax = V[a], ay = V[a + 1], az = V[a + 2];
        var ux = V[b] - ax, uy = V[b + 1] - ay, uz = V[b + 2] - az;
        var wx = V[c] - ax, wy = V[c + 1] - ay, wz = V[c + 2] - az;
        var nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
        var nl = Math.sqrt(nx * nx + ny * ny + nz * nz);
        if (nl) {
          nrm[3 * i] = nx / nl;
          nrm[3 * i + 1] = ny / nl;
          nrm[3 * i + 2] = nz / nl;
        }
        cen[3 * i] = (ax + V[b] + V[c]) / 3;
        cen[3 * i + 1] = (ay + V[b + 1] + V[c + 1]) / 3;
        cen[3 * i + 2] = (az + V[b + 2] + V[c + 2]) / 3;
      }
      closed = false;
      cullSign = 1;
      /* directed-edge pairing: closed + consistently wound <=> no directed
         edge repeats AND every edge's reverse exists. Keys are a*nv+b -
         exact integers well under 2^53 at any bridge-capped mesh size. */
      var edges = new Map();
      var p, q, k;
      for (i = 0; i < nt; i++) {
        for (k = 0; k < 3; k++) {
          p = T[3 * i + k];
          q = T[3 * i + (k + 1) % 3];
          edges.set(p * nv + q, (edges.get(p * nv + q) || 0) + 1);
        }
      }
      if (edges.size === 3 * nt) {         /* no repeats - now every reverse */
        closed = true;
        for (i = 0; i < nt && closed; i++) {
          for (k = 0; k < 3; k++) {
            p = T[3 * i + k];
            q = T[3 * i + (k + 1) % 3];
            if (!edges.has(q * nv + p)) { closed = false; break; }
          }
        }
      }
      if (closed) {
        var mxi = 0;
        for (i = 1; i < nv; i++) if (V[3 * i] > V[3 * mxi]) mxi = i;
        var lean = 0;
        for (i = 0; i < nt; i++) {
          if (T[3 * i] === mxi || T[3 * i + 1] === mxi || T[3 * i + 2] === mxi) {
            lean += nrm[3 * i];
          }
        }
        if (lean < 0) cullSign = -1;
        else if (!(lean > 0)) closed = false;
      }
      stats.closed = closed;
    }

    /* rotation-invariant fit: the bounds' bounding SPHERE projects to the
       same circle at every angle, so auto-fit never "breathes" while
       orbiting (view3d paid for this; same 1.12 pad). Square box, so the
       fit is also aspect-proof under the uniform meet-scale below.
       Perspective magnifies the near side by up to D/(D-R) = 1.4 - the
       extra pad covers it, same as view3d. */
    function fitBox(proj) {
      var fr = radius * 1.12 * (s.persp ? 1.4 : 1);
      var c2 = proj.project(center);
      return { x: c2[0] - fr, y: c2[1] - fr, w: 2 * fr, h: 2 * fr };
    }

    /* one subtle mm ruler at the bottom - ortho keeps mm measurable at
       every angle, so it stays honest. Nice 1-2-5 steps (view3d's idea,
       extended below 10 mm because scans are small). */
    function drawRuler(cw, ch, sc) {
      var MANT = [1, 2, 5], step = 0.1, e, i, v;
      for (e = -1; e <= 4; e++) for (i = 0; i < 3; i++) {
        v = MANT[i] * Math.pow(10, e);
        if (v * sc <= cw * 0.28 && v > step) step = v;
      }
      var u = remPx();
      var w = step * sc, bx = u, by = ch - u * 0.9, tick = u * 0.25;
      var col = cssVar("--sub") || "#888";   /* last-ditch only: themeless page */
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(bx, by); ctx.lineTo(bx + w, by);
      ctx.moveTo(bx, by - tick); ctx.lineTo(bx, by + tick);
      ctx.moveTo(bx + w, by - tick); ctx.lineTo(bx + w, by + tick);
      ctx.stroke();
      /* 0.6875rem = the app's small-label size, scaled by the text setting */
      ctx.font = (u * 0.6875) + "px " + (getComputedStyle(canvas).fontFamily || "sans-serif");
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText((step >= 1 ? String(step) : step.toFixed(1)) + " mm", bx + w / 2, by - u * 0.2);
    }

    function redraw() {
      var t0 = window.performance ? performance.now() : 0;
      var rect = canvas.getBoundingClientRect();
      var cw = rect.width, ch = rect.height;
      /* backing store tracks the CSS box + dpr; a mismatch (fresh layout,
         moved to another monitor) self-heals here. Setting width clears.
         Coarse (mid-gesture) frames shrink the STORE, not the box - fewer
         filled pixels is exactly the measured cost driver - and the
         browser scales the frame up to the CSS size while it moves. */
      var dpr = (window.devicePixelRatio || 1) * (coarse ? COARSE_SCALE : 1);
      var bw = Math.max(1, Math.round(cw * dpr)), bh = Math.max(1, Math.round(ch * dpr));
      if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);   /* draw in CSS px from here on */
      ctx.clearRect(0, 0, cw, ch);              /* background stays transparent - the container's --bg2 shows */
      if (!mesh || !cw || !ch || !mesh.triangles.length) {
        lastProj = null; lastFit = null; lastPersp = null;
        return;
      }

      /* the same mild center-pivoted perspective view3d uses */
      var persp = s.persp ? { center: center, dist: 3.5 * radius } : null;
      var proj = BV.proj3d.orbitProjector(s.az, s.el, persp);
      var fit = fitBox(proj);
      var box = s.zoom || fit;
      /* ONE uniform meet-scale for both axes; the slack axis letterboxes.
         (Independent per-axis scales made view3d's pan lag - same rule.) */
      var sc = Math.min(cw / box.w, ch / box.h) || 1;
      var ox = (cw - box.w * sc) / 2 - box.x * sc;
      var oy = (ch - box.h * sc) / 2 - box.y * sc;
      lastProj = proj;
      lastFit = fit;
      lastPersp = persp;

      var V = mesh.vertices, T = mesh.triangles;
      var nv = (V.length / 3) | 0, nt = (T.length / 3) | 0;
      var i;
      for (i = 0; i < nv; i++) {
        P[0] = V[3 * i]; P[1] = V[3 * i + 1]; P[2] = V[3 * i + 2];
        var o = proj.project(P);
        vsx[i] = ox + o[0] * sc;
        vsy[i] = oy + o[1] * sc;
        vsd[i] = o[2];
      }

      /* shade = |n . toViewer| off the PRECOMPUTED face normal - a headlight
         at the eye, no light state, two-sided. On a proven-closed mesh the
         same dot's SIGN is the backface test (see the header - open scan
         surfaces never cull); under perspective the cull ray is per-face
         (eye - centroid), because the view-axis sign is wrong by up to
         ~16 deg near the silhouette at dist 3.5R, while the shade keeps the
         view axis - indistinguishable at that angle and sqrt-free. */
      var tv = proj.basis.toViewer;
      var cullOn = closed;
      var ex = 0, ey = 0, ez = 0;
      if (persp) {
        ex = center[0] + tv[0] * persp.dist;
        ey = center[1] + tv[1] * persp.dist;
        ez = center[2] + tv[2] * persp.dist;
      }
      var nCulled = 0;
      var dMin = Infinity, dMax = -Infinity;
      for (i = 0; i < nt; i++) {
        var a = T[3 * i], b = T[3 * i + 1], c = T[3 * i + 2];
        tDepth[i] = vsd[a] + vsd[b] + vsd[c];   /* centroid depth x3 - same order */
        var nx = nrm[3 * i], ny = nrm[3 * i + 1], nz = nrm[3 * i + 2];
        var d = nx * tv[0] + ny * tv[1] + nz * tv[2];
        if (cullOn) {
          var cd = persp
            ? nx * (ex - cen[3 * i]) + ny * (ey - cen[3 * i + 1]) + nz * (ez - cen[3 * i + 2])
            : d;
          if (cd * cullSign <= 0) { culled[i] = 1; nCulled++; continue; }
          culled[i] = 0;
        }
        if (tDepth[i] < dMin) dMin = tDepth[i];
        if (tDepth[i] > dMax) dMax = tDepth[i];
        var sh = d < 0 ? -d : d;
        var k = (sh * SHADES) | 0;
        tShade[i] = k >= SHADES ? SHADES - 1 : k;
        tSign[i] = d > 0 ? 1 : 0;
      }
      /* ascending depth = far first = painter, by counting sort on the
         quantized depth (see DEPTH_BUCKETS): two O(nt) passes over the
         NON-culled triangles only, stable by index. tOrder[0..m) is this
         frame's draw list. */
      var m = 0;
      if (dMax > dMin) {
        var dScale = (DEPTH_BUCKETS - 1) / (dMax - dMin);
        bCount.fill(0);
        for (i = 0; i < nt; i++) {
          if (cullOn && culled[i]) continue;
          bCount[(((tDepth[i] - dMin) * dScale) | 0) + 1]++;
        }
        for (i = 1; i <= DEPTH_BUCKETS; i++) bCount[i] += bCount[i - 1];
        m = bCount[DEPTH_BUCKETS];
        for (i = 0; i < nt; i++) {
          if (cullOn && culled[i]) continue;
          tOrder[bCount[((tDepth[i] - dMin) * dScale) | 0]++] = i;
        }
      } else {
        /* flat depth (a face-on plane): any order paints the same */
        for (i = 0; i < nt; i++) {
          if (cullOn && culled[i]) continue;
          tOrder[m++] = i;
        }
      }

      var pal = shadePalette();
      ctx.lineWidth = 1;
      ctx.lineJoin = "round";
      /* the hairline stroke is the antialiasing-crack fill (fill-only
         leaves seams between neighbours); coarse frames skip it - it is
         half the canvas work and invisible while the mesh moves */
      var stroking = !coarse;
      var runShade = -1, runSign = -1, pathOpen = false;
      var nDrawn = 0;
      for (i = 0; i < m; i++) {
        var t = tOrder[i];
        var ia = T[3 * t], ib = T[3 * t + 1], ic = T[3 * t + 2];
        var x1 = vsx[ia], y1 = vsy[ia];
        var x2 = vsx[ib], y2 = vsy[ib];
        var x3 = vsx[ic], y3 = vsy[ic];
        /* zoomed in, most triangles leave the canvas - skip them whole */
        if ((x1 < 0 && x2 < 0 && x3 < 0) || (x1 > cw && x2 > cw && x3 > cw) ||
            (y1 < 0 && y2 < 0 && y3 < 0) || (y1 > ch && y2 > ch && y3 > ch)) continue;
        /* runs of same-shade, same-winding neighbours in depth order share
           one path and one fill - same pixels, a fraction of the calls.
           Windings must not mix inside a nonzero-rule path: opposite-wound
           overlaps would cancel to holes per-triangle fills never had. */
        if (tShade[t] !== runShade || tSign[t] !== runSign) {
          if (pathOpen) { ctx.fill(); if (stroking) ctx.stroke(); }
          runShade = tShade[t];
          runSign = tSign[t];
          ctx.fillStyle = pal[runShade];
          if (stroking) ctx.strokeStyle = pal[runShade];
          ctx.beginPath();
          pathOpen = true;
        }
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.lineTo(x3, y3);
        ctx.closePath();
        nDrawn++;
      }
      if (pathOpen) { ctx.fill(); if (stroking) ctx.stroke(); }

      if (!s.persp) drawRuler(cw, ch, sc);   /* ortho only - see the header */

      stats.drawn = nDrawn;
      stats.culled = nCulled;
      stats.coarse = coarse;
      stats.ms = t0 ? performance.now() - t0 : 0;
      if (opts.onDraw) opts.onDraw();
    }

    function setMesh(m) {
      mesh = m || null;
      if (mesh) {
        var nv = (mesh.vertices.length / 3) | 0, nt = (mesh.triangles.length / 3) | 0;
        vsx = new Float64Array(nv);
        vsy = new Float64Array(nv);
        vsd = new Float64Array(nv);
        tDepth = new Float64Array(nt);
        tShade = new Uint8Array(nt);
        tSign = new Uint8Array(nt);
        culled = new Uint8Array(nt);
        tOrder = new Int32Array(nt);
        var mn = mesh.bounds.min, mx = mesh.bounds.max;
        center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
        radius = Math.hypot(mx[0] - center[0], mx[1] - center[1], mx[2] - center[2]) || 1;
        prepMesh(nv, nt);
      } else {
        vsx = vsy = vsd = tDepth = tShade = tOrder = null;
        tSign = culled = nrm = cen = null;
        closed = false;
        stats.closed = false;
        center = null;
        radius = 0;
      }
      redraw();
    }

    /* ---- gestures (canvas client space - the same feel as view3d) ---- */

    if (opts.interactive !== false) {
      /* client px <-> projected mm through the one meet-scale (see redraw) */
      var metrics = function () {
        var rect = canvas.getBoundingClientRect();
        var box = s.zoom || lastFit;
        var sc = Math.min(rect.width / box.w, rect.height / box.h) || 1;
        return {
          box: box, sc: sc,
          ox: rect.left + (rect.width - box.w * sc) / 2,
          oy: rect.top + (rect.height - box.h * sc) / 2,
        };
      };

      /* a wheel burst has no "up" event to hang the settle frame on - a
         plain debounce restores full resolution when the burst ends (the
         one timer in this file; rAF stays banned for the probe's sake) */
      var settle = BV.debounce(function () {
        if (!coarse || drag) return;   /* a live drag settles on ITS pointerup */
        coarse = false;
        redraw();
      }, 180);

      canvas.addEventListener("wheel", function (e) {
        if (!lastFit) return;
        e.preventDefault();
        if (bigMesh()) { coarse = true; settle(); }
        var m = metrics();
        /* zoom factor exp(-deltaY*0.0015) => the view BOX scales by the
           inverse; clamped so the mesh spans 1/50th..20x the viewport */
        var k = Math.exp(e.deltaY * 0.0015);
        var nw = Math.min(Math.max(m.box.w * k, lastFit.w / 20), lastFit.w * 50);
        k = nw / m.box.w;
        var cx = m.box.x + (e.clientX - m.ox) / m.sc;   /* zoom about the cursor */
        var cy = m.box.y + (e.clientY - m.oy) / m.sc;
        s.zoom = {
          x: cx - (cx - m.box.x) * k, y: cy - (cy - m.box.y) * k,
          w: m.box.w * k, h: m.box.h * k,
        };
        redraw();
      }, { passive: false });

      var drag = null;
      canvas.addEventListener("pointerdown", function (e) {
        if (!lastFit) return;
        var pan = e.button === 1 || (e.button === 0 && e.shiftKey);
        if (!pan && e.button !== 0) return;
        e.preventDefault();   /* keep middle-click from starting autoscroll */
        /* rotate about whatever sits at the viewport CENTER, not the world
           origin: when panned, unproject the center point (at the scene
           center's depth) and keep it pinned while the angles change */
        var pv = null;
        if (!pan && s.zoom && lastProj) {
          var bb = s.zoom;
          pv = lastProj.unproject(bb.x + bb.w / 2, bb.y + bb.h / 2,
                                  lastProj.depthOf(center));
        }
        drag = {
          mode: pan ? "pan" : "rotate", live: false,
          x: e.clientX, y: e.clientY,
          box: s.zoom || lastFit, sc: metrics().sc, pivot: pv,
          az: s.az, el: s.el,
        };
        if (bigMesh()) coarse = true;   /* gesture frames render coarse; pointerup settles */
        /* synthetic pointer events (the probe) have no capturable pointerId */
        try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
        canvas.style.cursor = "grabbing";
      });
      canvas.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (drag.mode === "pan") {
          s.zoom = {
            x: drag.box.x - dx / drag.sc, y: drag.box.y - dy / drag.sc,
            w: drag.box.w, h: drag.box.h,
          };
          redraw();
          return;
        }
        if (!drag.live && Math.abs(dx) + Math.abs(dy) < 3) return;
        drag.live = true;
        /* 0.35 deg/px, the app-wide invert toggles honored like view3d */
        var prefs = (BV.state && BV.state.settings) || {};
        s.az = drag.az - dx * 0.35 * (prefs.v3_invert_x ? -1 : 1);
        /* el stops EXACTLY at the poles - past 90 the world flips its
           screen-vertical seamlessly with nothing to warn you (view3d
           paid for this; the poles must stay exactly reachable) */
        s.el = Math.max(-90, Math.min(90,
          drag.el + dy * 0.35 * (prefs.v3_invert_y ? -1 : 1)));
        if (drag.pivot) {
          var np = BV.proj3d.orbitProjector(s.az, s.el, lastPersp);
          var p2 = np.project(drag.pivot);
          s.zoom = { x: p2[0] - drag.box.w / 2, y: p2[1] - drag.box.h / 2,
                     w: drag.box.w, h: drag.box.h };
        }
        redraw();
      });
      ["pointerup", "pointercancel"].forEach(function (ev) {
        canvas.addEventListener(ev, function () {
          drag = null;
          canvas.style.cursor = "grab";
          if (coarse) { coarse = false; redraw(); }   /* the full-quality settle frame */
        });
      });
      canvas.addEventListener("dblclick", function () { s.zoom = null; redraw(); });
    }

    function destroy() {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      mesh = null;
      vsx = vsy = vsd = tDepth = tShade = tOrder = null;
      tSign = culled = nrm = cen = null;
      lastProj = lastFit = lastPersp = null;
    }

    redraw();   /* size the backing store even before a mesh arrives */

    return {
      setMesh: setMesh,
      resize: function () { redraw(); },   /* redraw self-syncs the backing store */
      redraw: redraw,
      destroy: destroy,
      el: canvas,
      _stats: stats,
    };
  };

  /* in-window lightbox, the photos.js openFullscreen pattern: closes ONLY
     via the ✕, Esc, or a clean backdrop click - click-anywhere-closes eats
     pans, and with no visible close control people reach for the app's own
     titlebar ✕ instead. */
  BV.meshView.enlarge = function (mesh, opts) {
    opts = opts || {};
    var overlay = BV.el("div", { class: "meshview-fsov", style:
      "position:fixed;inset:0;z-index:9999;overflow:hidden;" +
      "background:color-mix(in srgb, var(--bg) 93%, transparent)" });
    /* the viewer sits inset from the edges so a real backdrop ring exists -
       the clean-backdrop-click close needs somewhere clean to click */
    var holder = BV.el("div", { style: "position:absolute;inset:2.75rem 2.5rem 2rem" });
    overlay.appendChild(holder);
    var fsBtn = BV.el("button", { class: "btn", title: "fullscreen", style:
      "position:absolute;top:0.6rem;right:3.9rem;z-index:1;" +
      "font-size:1.25rem;line-height:1;padding:0.3rem 0.75rem" }, "⛶");
    overlay.appendChild(fsBtn);
    var closeBtn = BV.el("button", { class: "btn", title: "close (esc)", style:
      "position:absolute;top:0.6rem;right:0.8rem;z-index:1;" +
      "font-size:1.25rem;line-height:1;padding:0.3rem 0.75rem" }, "✕");
    overlay.appendChild(closeBtn);
    document.body.appendChild(overlay);

    /* fresh view, SAME state object - orientation persists in and out of
       the lightbox (and back to whichever inline view shares the state) */
    var st = opts.state || {};
    var view = BV.meshView(holder, {
      state: st,
      interactive: opts.interactive !== false,
      onDraw: function () { if (cube) cube.update(); },
    });
    /* the same orientation cube the inline viewers ride (created after the
       view so the state carries its seeded az/el; onDraw guards the gap) */
    var cube = BV.viewCube(holder, {
      basisOf: function () { return BV.proj3d.orbitProjector(st.az, st.el).basis; },
      onSnap: function (az, el) {
        st.az = az;
        st.el = el;
        st.zoom = null;
        view.redraw();
      },
    });
    view.setMesh(mesh);

    function onResize() { view.resize(); }
    window.addEventListener("resize", onResize);

    function close() {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      BV.fullscreen.exit();   /* ALWAYS - the host window must never stay stuck */
      view.destroy();
      overlay.remove();
      if (opts.onClose) opts.onClose();
    }
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    }
    document.addEventListener("keydown", onKey);
    closeBtn.addEventListener("click", close);
    fsBtn.addEventListener("click", function () {
      /* HOST-window fullscreen (util.js owns the one path that works in
         WebView2); the window resize it causes lands in onResize */
      BV.fullscreen.toggle();
    });
    /* clean backdrop click = the press started on the backdrop itself AND
       the click resolves to it - a drag that starts on the canvas and
       strays off it must not close (photos.js paid for this) */
    var downOnBackdrop = false;
    overlay.addEventListener("pointerdown", function (e) {
      downOnBackdrop = e.target === overlay;
    });
    overlay.addEventListener("click", function (e) {
      if (downOnBackdrop && e.target === overlay) close();
    });

    return { close: close, view: view };
  };
})();
