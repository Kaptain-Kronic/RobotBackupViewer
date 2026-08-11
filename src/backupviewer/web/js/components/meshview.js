/* components/meshview.js - BV.meshView: a canvas-2D triangle-mesh viewer.

   Draws scanned-surface triangle meshes with a hand-rolled painter's
   algorithm on a plain 2D canvas. No WebGL - ROADMAP's parking lot bans it
   (rescue-mode plant PCs render in software; canvas 2D still works on that
   path) - and no libraries. All camera math is BV.proj3d.orbitProjector
   (script order: proj3d.js loads before this file): project(p) returns
   [sx, sy, depth] with depth ASCENDING toward the viewer - painter's order
   is "sort ascending, draw in order" - and sy grows DOWN. Orthographic
   only: distances stay measurable, which is what earns the mm ruler.
   Redraws are synchronous - no requestAnimationFrame (the probe's headless
   WebView2 does not have it).

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
     opts.interactive  default true; false renders static (no gestures)
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

   BV.meshView.enlarge(mesh, opts) -> {close, view}: in-window lightbox on
   the photos.js openFullscreen pattern - position:fixed inset:0 overlay
   that closes ONLY via the visible close button, Esc, or a clean backdrop
   click (never click-anywhere). It instantiates a fresh meshView sharing
   opts.state, so orientation persists between inline and enlarged views.
   The fullscreen button toggles the HOST window via BV.fullscreen - the
   web Fullscreen API is a silent no-op in WebView2 - and teardown always
   calls BV.fullscreen.exit(). opts: { state, interactive, onClose }. */
(function () {
  "use strict";

  var SHADES = 24;   /* shade buckets: fewer fillStyle switches, no visible banding */

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
    var P = [0, 0, 0];                                   /* reused projector input */
    var lastProj = null, lastFit = null;                 /* gestures reuse the draw's camera */

    function cmpDepth(a, b) { return tDepth[a] - tDepth[b]; }

    /* rotation-invariant fit: the bounds' bounding SPHERE projects to the
       same circle at every angle, so auto-fit never "breathes" while
       orbiting (view3d paid for this; same 1.12 pad). Square box, so the
       fit is also aspect-proof under the uniform meet-scale below. */
    function fitBox(proj) {
      var fr = radius * 1.12;
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
      var rect = canvas.getBoundingClientRect();
      var cw = rect.width, ch = rect.height;
      var dpr = window.devicePixelRatio || 1;
      /* backing store tracks the CSS box + dpr; a mismatch (fresh layout,
         moved to another monitor) self-heals here. Setting width clears. */
      var bw = Math.max(1, Math.round(cw * dpr)), bh = Math.max(1, Math.round(ch * dpr));
      if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);   /* draw in CSS px from here on */
      ctx.clearRect(0, 0, cw, ch);              /* background stays transparent - the container's --bg2 shows */
      if (!mesh || !cw || !ch || !mesh.triangles.length) { lastProj = null; lastFit = null; return; }

      var proj = BV.proj3d.orbitProjector(s.az, s.el);
      var fit = fitBox(proj);
      var box = s.zoom || fit;
      /* ONE uniform meet-scale for both axes; the slack axis letterboxes.
         (Independent per-axis scales made view3d's pan lag - same rule.) */
      var sc = Math.min(cw / box.w, ch / box.h) || 1;
      var ox = (cw - box.w * sc) / 2 - box.x * sc;
      var oy = (ch - box.h * sc) / 2 - box.y * sc;
      lastProj = proj;
      lastFit = fit;

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

      /* scan meshes are OPEN surfaces (a range image has no back), so no
         backface cull: faces are two-sided and the shade is |n . toViewer|
         - a headlight at the eye, no light state. Culling would punch
         holes the data does not have. */
      var tv = proj.basis.toViewer;
      for (i = 0; i < nt; i++) {
        var a = T[3 * i], b = T[3 * i + 1], c = T[3 * i + 2];
        tDepth[i] = vsd[a] + vsd[b] + vsd[c];   /* centroid depth x3 - same order */
        var ax = V[3 * a], ay = V[3 * a + 1], az = V[3 * a + 2];
        var ux = V[3 * b] - ax, uy = V[3 * b + 1] - ay, uz = V[3 * b + 2] - az;
        var wx = V[3 * c] - ax, wy = V[3 * c + 1] - ay, wz = V[3 * c + 2] - az;
        var nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
        var nl = Math.sqrt(nx * nx + ny * ny + nz * nz);
        var sh = nl ? Math.abs(nx * tv[0] + ny * tv[1] + nz * tv[2]) / nl : 0;
        var k = (sh * SHADES) | 0;
        tShade[i] = k >= SHADES ? SHADES - 1 : k;
        tOrder[i] = i;
      }
      tOrder.sort(cmpDepth);   /* ascending depth = far first = painter */

      var pal = shadePalette();
      ctx.lineWidth = 1;
      ctx.lineJoin = "round";
      var cur = -1;
      for (i = 0; i < nt; i++) {
        var t = tOrder[i];
        var ia = T[3 * t], ib = T[3 * t + 1], ic = T[3 * t + 2];
        var x1 = vsx[ia], y1 = vsy[ia];
        var x2 = vsx[ib], y2 = vsy[ib];
        var x3 = vsx[ic], y3 = vsy[ic];
        /* zoomed in, most triangles leave the canvas - skip them whole */
        if ((x1 < 0 && x2 < 0 && x3 < 0) || (x1 > cw && x2 > cw && x3 > cw) ||
            (y1 < 0 && y2 < 0 && y3 < 0) || (y1 > ch && y2 > ch && y3 > ch)) continue;
        if (tShade[t] !== cur) {
          cur = tShade[t];
          ctx.fillStyle = pal[cur];
          ctx.strokeStyle = pal[cur];
        }
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.lineTo(x3, y3);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();   /* same-color hairline: fill-only leaves antialiasing
                           cracks between neighbouring triangles */
      }

      drawRuler(cw, ch, sc);
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
        tOrder = new Int32Array(nt);
        var mn = mesh.bounds.min, mx = mesh.bounds.max;
        center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
        radius = Math.hypot(mx[0] - center[0], mx[1] - center[1], mx[2] - center[2]) || 1;
      } else {
        vsx = vsy = vsd = tDepth = tShade = tOrder = null;
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

      canvas.addEventListener("wheel", function (e) {
        if (!lastFit) return;
        e.preventDefault();
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
          var np = BV.proj3d.orbitProjector(s.az, s.el);
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
        });
      });
      canvas.addEventListener("dblclick", function () { s.zoom = null; redraw(); });
    }

    function destroy() {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      mesh = null;
      vsx = vsy = vsd = tDepth = tShade = tOrder = null;
      lastProj = lastFit = null;
    }

    redraw();   /* size the backing store even before a mesh arrives */

    return {
      setMesh: setMesh,
      resize: function () { redraw(); },   /* redraw self-syncs the backing store */
      redraw: redraw,
      destroy: destroy,
      el: canvas,
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
    var view = BV.meshView(holder, {
      state: opts.state || {},
      interactive: opts.interactive !== false,
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
