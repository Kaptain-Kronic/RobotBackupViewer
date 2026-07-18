/* tabs/view3d.js - "3D View": DCS zone geometry drawn to scale.

   Viewport (left) = hand-rolled SVG projection (BV.proj3d) - no WebGL, no
   libraries, so it renders fine even on the software-rendering rescue
   path. The camera is a free unbounded turntable: left-drag rotates
   (invertible per-axis in settings), middle/shift-drag pans, wheel
   zooms, and the viewport CUBE (top-right, rotates with the view) snaps
   to any of 26 directions - faces, edges, corners. Side panel (right) =
   every DCS check as a row: cartesian zones get a show/hide checkbox +
   a color swatch matching the viewport; joint/speed checks carry data
   only (nothing honest to draw without a robot model); each row expands
   to its pendant-style detail (BV.dcsDetail).
   The robot arm poses from imported Roboguide .def kinematics (BV.fk,
   local registry via "import…") at the backup's own CURPOS snapshot -
   editable per joint - drawn as an honest stick-figure skeleton with the
   DCS user-model spheres/capsules at their true frames; the pose is
   cross-checked against the backup's own TCP report and refuses to draw
   on a mismatch. Meshes are a later tier - they'd slot in as one more
   draw layer between grid and zones. */
(function () {
  "use strict";

  var NICE = [100, 250, 500, 1000, 2000, 5000, 10000, 20000];

  function st() {
    var s = BV.tabState("view3d");
    if (!s.init2) {      /* v2 state: the camera is ALWAYS the free orbit */
      s.init2 = true;
      s.az = -24.8;      /* turntable angles, unbounded */
      s.el = 36.8;
      s.showDisabled = false;
      s.hidden = {};     /* zone n -> true when unchecked */
      s.group = 0;       /* 0 = all groups */
      s.box = null;      /* single pan/zoom override (null = auto-fit) */
      s.persp = false;   /* orthographic by default */
    }
    /* older sessions could park el past a pole - normalize back in range */
    s.el = Math.max(-90, Math.min(90, s.el));
    return s;
  }

  /* ---- zone colors: rotate the theme accent's hue (golden angle) so
     every theme keeps its own character and 32 zones stay tellable ---- */

  function accentHsl() {
    var v = getComputedStyle(document.body).getPropertyValue("--accent").trim();
    var m = /^#?([0-9a-f]{6})$/i.exec(v);
    if (!m) return [200, 70, 55]; /* non-hex theme value: neutral fallback hue */
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

  function zoneColor(base, n) {
    var h = (base[0] + (n - 1) * 137.508) % 360;
    var s = Math.min(90, Math.max(45, base[1]));
    var l = Math.min(65, Math.max(45, base[2]));
    return "hsl(" + h.toFixed(1) + "," + s.toFixed(0) + "%," + l.toFixed(0) + "%)";
  }

  function statusPill(stat) {
    if (!stat || /^-+$/.test(stat)) return "";
    var v = (stat === "OK" || stat === "SAFE") ? "ok-soft"
      : (stat === "CHGD" || stat === "PEND" || stat === "WARN") ? "warn" : "err";
    return BV.pill(stat.toLowerCase(), v);
  }

  function niceStep(raw) {
    for (var i = 0; i < NICE.length; i++) if (NICE[i] >= raw) return NICE[i];
    return NICE[NICE.length - 1];
  }

  /* ---- viewport ---- */

  function visibleZones(data, s) {
    return data.cpc.filter(function (z) {
      if (!s.showDisabled && !z.enabled) return false;
      if (s.group && z.group !== s.group) return false;
      return !s.hidden[z.n];
    });
  }

  /* ---- robot pose (imported .def kinematics + backup CURPOS) ---- */

  function poseQ(s, robot) {
    if (s.pose) return s.pose;
    if (robot && robot.q) return robot.q;
    return [];
  }

  /* frames when the arm is honestly posable: kinematics matched AND the
     backup's own position report did not contradict them. calib=null
     (no CURPOS to check against) still poses, flagged unverified. */
  function robotFrames(s, robot) {
    if (!robot || !robot.kin) return null;
    if (robot.calib && !robot.calib.ok) return null;
    return BV.fk.chain(robot.kin, poseQ(s, robot), robot.flange_dz || 0);
  }

  /* user-model elements drawable at this pose: enabled, structured (VA),
     plain tool frame, on the faceplate or a numbered link */
  function posedElements(data, frames) {
    var out = [];
    data.models.forEach(function (m) {
      if (!m.active) return;
      (m.elements || []).forEach(function (el) {
        if (!el.enabled || !el.shape_raw || el.utool_num) return;
        var f = el.link_no === 99 ? frames.faceplate
          : (el.link_no >= 1 && el.link_no <= frames.joints.length)
            ? frames.joints[el.link_no - 1] : null;
        if (!f) return;
        out.push({
          el: el, model: m,
          p1: BV.fk.apply(f, el.p1),
          p2: el.shape_raw === 2 ? BV.fk.apply(f, el.p2) : null,
          approx: el.link_no !== 99, /* link-frame convention unverified */
        });
      });
    });
    return out;
  }

  function draw(svg, data, s, colors, robot) {
    var zones = visibleZones(data, s);

    /* world geometry per zone + world bounds (grid and fit both use them) */
    var wmin = [-1000, -1000, 0], wmax = [1000, 1000, 0];
    var wpts = [[0, 0, 0]];
    var geo = [];   /* {z, W, faces, edges} */
    zones.forEach(function (z) {
      var toW = BV.proj3d.frameTransform(z.frame);
      var pr = BV.proj3d.prism(z.poly, z.z1, z.z2);
      var W = pr.pts.map(toW);
      wpts = wpts.concat(W);
      geo.push({ z: z, W: W, faces: pr.faces, edges: pr.edges });
    });
    var tcpW = data.tcp ? BV.proj3d.frameTransform(data.tcp.frame)(data.tcp.xyz) : null;
    if (tcpW) wpts.push(tcpW);

    /* the posed arm + its user-model elements join the world bounds */
    var frames = robotFrames(s, robot);
    var skel = null, elems = [];
    if (frames) {
      var zr = (robot.kin.zero || [0, 0, 0]);
      skel = [[-zr[0], -zr[1], -zr[2]]];  /* base floor point */
      frames.joints.forEach(function (m) { skel.push([m[0][3], m[1][3], m[2][3]]); });
      var fpm = frames.faceplate;
      skel.push([fpm[0][3], fpm[1][3], fpm[2][3]]);
      wpts = wpts.concat(skel);
      elems = posedElements(data, frames);
      elems.forEach(function (e) {
        wpts.push(e.p1);
        if (e.p2) wpts.push(e.p2);
      });
    }
    wpts.forEach(function (p) {
      for (var k = 0; k < 3; k++) {
        if (p[k] < wmin[k]) wmin[k] = p[k];
        if (p[k] > wmax[k]) wmax[k] = p[k];
      }
    });

    /* bounding sphere BEFORE the projector - perspective pivots on its
       center, and rotation math pins depths there */
    var C = [(wmin[0] + wmax[0]) / 2, (wmin[1] + wmax[1]) / 2, (wmin[2] + wmax[2]) / 2];
    var R = 0;
    wpts.forEach(function (p) {
      var d = Math.hypot(p[0] - C[0], p[1] - C[1], p[2] - C[2]);
      if (d > R) R = d;
    });
    R = R || 800;

    var persp = s.persp ? { center: C, dist: 3.5 * R } : null;
    var proj = BV.proj3d.orbitProjector(s.az, s.el, persp);
    svg._proj = proj;
    svg._center = C;
    svg._persp = persp;
    var scr = geo.map(function (g) {
      return { z: g.z, spts: g.W.map(proj.project), faces: g.faces, edges: g.edges };
    });

    /* rotation-invariant fit: the content's world bounding SPHERE projects
       to the same circle at EVERY angle, so auto-fit cannot "breathe"
       while orbiting. (Fitting the projected bounding box did exactly
       that - its extent changes with the angle, even spinning in place
       over the top.) Bonus: mm-per-px now matches across all views.
       Perspective magnifies the near side by up to D/(D-R) = 1.4 - the
       pad covers it. */
    var fitR = R * 1.12 * (persp ? 1.4 : 1);
    var c2 = proj.project(C);
    var fit = { x: c2[0] - fitR, y: c2[1] - fitR, w: 2 * fitR, h: 2 * fitR };
    svg._fitBox = fit;
    var box = s.box || fit;
    svg.setAttribute("viewBox", box.x + " " + box.y + " " + box.w + " " + box.h);

    /* ---- scene layer (viewBox space): geometry only, never text ---- */
    var out = [];

    /* floor grid (world Z=0): side views collapse it to the floor line */
    var step = niceStep(Math.max(wmax[0] - wmin[0], wmax[1] - wmin[1]) / 10);
    var gx0 = Math.floor(wmin[0] / step) - 1, gx1 = Math.ceil(wmax[0] / step) + 1;
    var gy0 = Math.floor(wmin[1] / step) - 1, gy1 = Math.ceil(wmax[1] / step) + 1;
    var i, a, b;
    for (i = gx0; i <= gx1; i++) {
      a = proj.project([i * step, gy0 * step, 0]); b = proj.project([i * step, gy1 * step, 0]);
      out.push('<line class="v3-grid" x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '"/>');
    }
    for (i = gy0; i <= gy1; i++) {
      a = proj.project([gx0 * step, i * step, 0]); b = proj.project([gx1 * step, i * step, 0]);
      out.push('<line class="v3-grid" x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '"/>');
    }

    /* world axes at the robot base (one grid-step long - true to scale) */
    var o2 = proj.project([0, 0, 0]);
    var tips = {};
    ["x", "y", "z"].forEach(function (ax, k) {
      var e = [0, 0, 0];
      e[k] = step;
      tips[ax] = proj.project(e);
      out.push('<line class="v3-ax v3-ax-' + ax + '" x1="' + o2[0] + '" y1="' + o2[1] +
        '" x2="' + tips[ax][0] + '" y2="' + tips[ax][1] + '"/>');
    });

    /* the posed arm: joint-to-joint capsule limbs (a schematic body, sized
       from the arm's reach and tapering to the wrist - deliberately NOT
       the DCS robot model or a mesh, just enough girth to read as a
       robot) + the DCS user-model elements at their true frames. Spheres
       project as circles, capsules as round-cap strokes, all in world mm
       so they scale with the scene. */
    if (skel) {
      var sp = skel.map(function (p) { return proj.project(p); });
      var reach = 0;
      skel.forEach(function (p) { reach = Math.max(reach, Math.hypot(p[0], p[1], p[2])); });
      var girth = Math.max(30, Math.min(110, reach * 0.045));
      var segN = sp.length - 1;
      for (var si = 0; si < segN; si++) {
        var taper = 1.25 - 0.75 * (si / (segN - 1)); /* pedestal thick, wrist slim */
        out.push('<line class="v3-body" x1="' + sp[si][0] + '" y1="' + sp[si][1] +
          '" x2="' + sp[si + 1][0] + '" y2="' + sp[si + 1][1] +
          '" stroke-width="' + (2 * girth * taper) + '"/>');
      }
      var sd = "";
      sp.forEach(function (p, i) { sd += (i ? "L" : "M") + p[0] + " " + p[1]; });
      out.push('<path class="v3-skel" d="' + sd + '"/>');
      sp.forEach(function (p, i) {
        if (i === 0) return; /* floor anchor gets no joint dot */
        out.push('<circle class="v3-skel-j" cx="' + p[0] + '" cy="' + p[1] + '" r="' +
          (girth * 0.42) + '"/>');
      });
      elems.forEach(function (e) {
        var a = proj.project(e.p1);
        var dash = e.approx ? ' stroke-dasharray="10 7"' : "";
        if (!e.p2) {
          out.push('<circle class="v3-elem" cx="' + a[0] + '" cy="' + a[1] +
            '" r="' + e.el.size + '"' + dash + "/>");
          return;
        }
        var b = proj.project(e.p2);
        out.push('<line class="v3-elem-cap" x1="' + a[0] + '" y1="' + a[1] +
          '" x2="' + b[0] + '" y2="' + b[1] +
          '" stroke-width="' + (2 * e.el.size) + '"' + dash + "/>");
        out.push('<line class="v3-elem-axis" x1="' + a[0] + '" y1="' + a[1] +
          '" x2="' + b[0] + '" y2="' + b[1] + '"/>');
      });
    }

    /* zone faces, painter-sorted across ALL zones so overlaps stack right */
    var faces = [];
    scr.forEach(function (sz) {
      var color = colors[sz.z.n];
      var op = sz.z.side === "out" ? 0.26 : sz.z.side === "in" ? 0.10 : 0.16;
      sz.faces.forEach(function (f) {
        var d = 0, pts = [];
        f.forEach(function (idx) { d += sz.spts[idx][2]; pts.push(sz.spts[idx][0] + "," + sz.spts[idx][1]); });
        faces.push({ d: d / f.length, html: '<polygon class="v3-face" points="' + pts.join(" ") +
          '" fill="' + color + '" fill-opacity="' + op + '"/>' });
      });
    });
    faces.sort(function (p, q) { return p.d - q.d; });
    faces.forEach(function (f) { out.push(f.html); });

    /* wireframe per zone (keep-in = dashed envelope) */
    scr.forEach(function (sz) {
      var dash = sz.z.side === "in" ? ' stroke-dasharray="6 4"' : "";
      var d = "";
      sz.edges.forEach(function (e) {
        d += "M" + sz.spts[e[0]][0] + " " + sz.spts[e[0]][1] +
          "L" + sz.spts[e[1]][0] + " " + sz.spts[e[1]][1];
      });
      out.push('<path class="v3-edge" d="' + d + '" stroke="' + colors[sz.z.n] + '"' + dash + "/>");
    });

    svg.innerHTML = out.join("");

    /* ---- overlay layer (PIXEL space): every label and furniture piece
       at constant screen size. Zoom/orbit move the geometry, never the
       text. World-anchored bits (zone names, axis letters, tcp, base
       dot) re-project to px each draw; ruler/notes/hint pin to the
       viewport corners. Same uniform meet-scale as the gesture math. */
    var ovl = svg._ovl;
    if (!ovl) return;
    var rect = svg.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) { ovl.innerHTML = ""; return; }
    var sc = Math.min(rect.width / box.w, rect.height / box.h) || 1;
    var ox = (rect.width - box.w * sc) / 2, oy = (rect.height - box.h * sc) / 2;
    function toPx(p2) { return [(p2[0] - box.x) * sc + ox, (p2[1] - box.y) * sc + oy]; }
    ovl.setAttribute("viewBox", "0 0 " + rect.width + " " + rect.height);
    var ov = [];

    var op2 = toPx(o2);
    ov.push('<circle class="v3-base" cx="' + op2[0] + '" cy="' + op2[1] + '" r="3.5"/>');
    ["x", "y", "z"].forEach(function (ax) {
      var t = toPx(tips[ax]);
      ov.push('<text class="v3-ax-lab v3-ax-' + ax + '" x="' + (op2[0] + (t[0] - op2[0]) * 1.14) +
        '" y="' + (op2[1] + (t[1] - op2[1]) * 1.14 + 4) + '" font-size="11" text-anchor="middle">' +
        ax.toUpperCase() + "</text>");
    });

    scr.forEach(function (sz) {
      var cx = 0, cy = 0;
      sz.spts.forEach(function (p) { cx += p[0]; cy += p[1]; });
      var p = toPx([cx / sz.spts.length, cy / sz.spts.length]);
      ov.push('<text class="v3-zlab" x="' + p[0] + '" y="' + p[1] + '" fill="' + colors[sz.z.n] +
        '" font-size="12">' + BV.esc(sz.z.label) + "</text>");
    });

    /* TCP position captured when the verify report was written */
    if (tcpW) {
      var tp = toPx(proj.project(tcpW));
      ov.push('<g class="v3-tcp"><circle cx="' + tp[0] + '" cy="' + tp[1] + '" r="6"/>' +
        '<line x1="' + (tp[0] - 11) + '" y1="' + tp[1] + '" x2="' + (tp[0] + 11) + '" y2="' + tp[1] + '"/>' +
        '<line x1="' + tp[0] + '" y1="' + (tp[1] - 11) + '" x2="' + tp[0] + '" y2="' + (tp[1] + 11) + '"/>' +
        '<text x="' + (tp[0] + 13) + '" y="' + (tp[1] - 7) + '" font-size="11">tcp</text></g>');
    }

    /* scale ruler: largest nice mm length that stays ~1/4 viewport wide */
    var RULER = [10, 25, 50].concat(NICE);
    var mm = RULER[0];
    for (i = 0; i < RULER.length; i++) if (RULER[i] * sc <= rect.width * 0.28) mm = RULER[i];
    var bpx = mm * sc, bx = 16, by = rect.height - 16;
    ov.push('<g class="v3-scale"><line x1="' + bx + '" y1="' + by + '" x2="' + (bx + bpx) + '" y2="' + by + '"/>' +
      '<line x1="' + bx + '" y1="' + (by - 5) + '" x2="' + bx + '" y2="' + (by + 5) + '"/>' +
      '<line x1="' + (bx + bpx) + '" y1="' + (by - 5) + '" x2="' + (bx + bpx) + '" y2="' + (by + 5) + '"/>' +
      '<text x="' + (bx + bpx / 2) + '" y="' + (by - 6) + '" font-size="11">' + mm + " mm</text></g>");

    var notes = [];
    if (zones.some(function (z) { return z.approx || z.frame_missing; })) {
      notes.push("⚠ frame rotation unknown — geometry approximate");
    }
    if (robot && robot.kin && robot.calib && !robot.calib.ok) {
      notes.push("⚠ kinematics mismatch vs backup position report (" +
        robot.calib.dxy.toFixed(1) + " mm / " + robot.calib.ori_err.toFixed(2) +
        "° residual) — robot not posed");
    } else if (frames && !robot.calib) {
      notes.push("robot pose unverified — no position report in this backup");
    }
    if (elems.some(function (e) { return e.approx; })) {
      notes.push("⚠ link-attached elements: link-frame convention unverified");
    }
    if (!zones.length) {
      notes.push(data.cpc.length ? "no zones shown — check some on the right" +
        (s.showDisabled ? "" : " or “show disabled”") : "no cartesian zones in this backup");
    }
    notes.forEach(function (t, k) {
      ov.push('<text class="v3-note" x="16" y="' + (22 + k * 17) + '" font-size="11.5">' + BV.esc(t) + "</text>");
    });
    ov.push('<text class="v3-hint" x="' + (rect.width - 10) + '" y="' + (rect.height - 8) +
      '" text-anchor="end" font-size="10.5">drag rotate · mid-drag pan · wheel zoom · dblclick fit</text>');

    /* ---- viewport cube (top-right): rotates with the view, doubles as a
       compass (you can SEE when you're under the floor). Click a face,
       edge or corner to snap the camera to that direction - 26 targets,
       named per the FANUC world frame. Purely rotational: projected with
       the current basis, never with perspective or world offsets. ---- */
    var bs = proj.basis;
    function cpj(p) {
      return [dot3(bs.right, p), -dot3(bs.up, p), dot3(bs.toViewer, p)];
    }
    var CS = 21, ccx = rect.width - 54, ccy = 54;
    function cpx(p) {
      var q = cpj(p);
      return [ccx + q[0] * CS, ccy + q[1] * CS];
    }
    var cube = [];
    CUBE_FACES.forEach(function (f) {
      if (cpj(f.n)[2] < 0.03) return; /* backface */
      var pts = f.corners.map(cpx).map(function (p) { return p[0] + "," + p[1]; });
      cube.push('<polygon class="v3-cube-face" points="' + pts.join(" ") +
        '" data-az="' + f.az + '" data-el="' + f.el + '"><title>' + f.label + ' view</title></polygon>');
      var lc = cpx(f.n);
      cube.push('<text class="v3-cube-lab" x="' + lc[0] + '" y="' + (lc[1] + 3) + '">' + f.label + "</text>");
    });
    CUBE_HITS.forEach(function (h) {
      if (cpj(h.d)[2] < 0.1) return;
      var p = cpx(h.at);
      cube.push('<circle class="v3-cube-hit" cx="' + p[0] + '" cy="' + p[1] + '" r="' + h.r +
        '" data-az="' + h.az + '" data-el="' + h.el + '"/>');
    });
    ov.push('<g class="v3-cube">' + cube.join("") + "</g>");

    ovl.innerHTML = ov.join("");
  }

  /* cube geometry: 6 labeled faces + 12 edge and 8 corner snap targets.
     Directions live in the FANUC world frame (+X front, +Y left, +Z top);
     each target's az/el is the turntable angle that LOOKS from there.
     Top/bottom faces keep the canonical plan azimuths. */
  var dot3 = function (a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; };
  var CUBE_FACES = (function () {
    var defs = [
      { n: [1, 0, 0], label: "front", az: 0, el: 0 },
      { n: [-1, 0, 0], label: "back", az: 180, el: 0 },
      { n: [0, 1, 0], label: "left", az: 90, el: 0 },
      { n: [0, -1, 0], label: "right", az: -90, el: 0 },
      { n: [0, 0, 1], label: "top", az: 180, el: 90 },
      { n: [0, 0, -1], label: "btm", az: 0, el: -90 },
    ];
    defs.forEach(function (f) {
      var k = f.n[0] ? 0 : f.n[1] ? 1 : 2;
      var a = (k + 1) % 3, b = (k + 2) % 3;
      f.corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(function (uv) {
        var p = [0, 0, 0];
        p[k] = f.n[k];
        p[a] = uv[0];
        p[b] = uv[1];
        return p;
      });
    });
    return defs;
  })();
  var CUBE_HITS = (function () {
    var out = [], i, j;
    var R2D = 180 / Math.PI;
    function target(v, r) {
      var l = Math.sqrt(dot3(v, v));
      var d = [v[0] / l, v[1] / l, v[2] / l];
      out.push({
        at: v, d: d, r: r,
        az: Math.round(Math.atan2(d[1], d[0]) * R2D * 10) / 10,
        el: Math.round(Math.asin(d[2]) * R2D * 10) / 10,
      });
    }
    for (i = 0; i < 6; i++) {
      for (j = i + 1; j < 6; j++) {
        var n1 = CUBE_FACES[i].n, n2 = CUBE_FACES[j].n;
        if (dot3(n1, n2) !== 0) continue; /* opposite faces share no edge */
        target([n1[0] + n2[0], n1[1] + n2[1], n1[2] + n2[2]], 4.5);
      }
    }
    [-1, 1].forEach(function (x) {
      [-1, 1].forEach(function (y) {
        [-1, 1].forEach(function (z) { target([x, y, z], 4); });
      });
    });
    return out;
  })();

  /* ---- orbit / pan / zoom (stored in tab state) ---- */

  function wireViewport(svg, s, redraw) {
    function curBox() { return s.box || svg._fitBox; }
    /* preserveAspectRatio="meet" scales the viewBox UNIFORMLY and letterboxes
       the slack axis, so px -> viewBox conversion must use that ONE scale for
       both axes. (Using width/height independently made pan lag to a fraction
       of the mouse on whichever axis was letterboxed.) */
    function metrics() {
      var rect = svg.getBoundingClientRect();
      var box = curBox();
      var sc = Math.min(rect.width / box.w, rect.height / box.h) || 1;
      return {
        box: box, sc: sc,
        ox: rect.left + (rect.width - box.w * sc) / 2,
        oy: rect.top + (rect.height - box.h * sc) / 2,
      };
    }

    svg.addEventListener("wheel", function (e) {
      e.preventDefault();
      var m = metrics();
      var fit = svg._fitBox;
      var k = Math.exp(e.deltaY * 0.0015);
      var nw = Math.min(Math.max(m.box.w * k, fit.w / 50), fit.w * 20);
      k = nw / m.box.w;
      var cx = m.box.x + (e.clientX - m.ox) / m.sc;
      var cy = m.box.y + (e.clientY - m.oy) / m.sc;
      s.box = {
        x: cx - (cx - m.box.x) * k, y: cy - (cy - m.box.y) * k,
        w: m.box.w * k, h: m.box.h * k,
      };
      redraw();
    }, { passive: false });

    var drag = null;
    svg.addEventListener("pointerdown", function (e) {
      var pan = e.button === 1 || (e.button === 0 && e.shiftKey);
      if (!pan && e.button !== 0) return;
      e.preventDefault(); /* keep middle-click from starting autoscroll */
      /* rotate about whatever sits at the viewport CENTER, not the world
         origin: when the view was panned, unproject the center point (at
         the scene center's depth, where ortho and perspective agree) and
         keep it pinned there while the angles change */
      var pv = null;
      if (!pan && s.box && svg._proj) {
        var bb = curBox();
        pv = svg._proj.unproject(bb.x + bb.w / 2, bb.y + bb.h / 2,
                                 svg._proj.depthOf(svg._center));
      }
      drag = {
        mode: pan ? "pan" : "rotate", live: false,
        x: e.clientX, y: e.clientY,
        box: curBox(), sc: metrics().sc, pivot: pv,
        az: s.az, el: s.el,
      };
      /* synthetic pointer events (the probe) have no capturable pointerId */
      try { svg.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
      svg.classList.add("dragging");
    });
    svg.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (drag.mode === "pan") {
        s.box = {
          x: drag.box.x - dx / drag.sc, y: drag.box.y - dy / drag.sc,
          w: drag.box.w, h: drag.box.h,
        };
        redraw();
        return;
      }
      /* rotate: unbounded turntable - spin as far as you like, incl. over
         the top. Vertical feel inverts app-wide via the settings toggles
         (drag-down raises the camera by default - Cody's pick). */
      if (!drag.live && Math.abs(dx) + Math.abs(dy) < 3) return;
      drag.live = true;
      var prefs = BV.state.settings || {};
      s.az = drag.az - dx * 0.35 * (prefs.v3_invert_x ? -1 : 1);
      /* el stops EXACTLY at the poles (top/bottom stay exact) - letting it
         run past 90 flipped the world's screen-vertical "seamlessly", and
         right at the pole the cube is face-on from either side, so nothing
         warned you. Over-the-top orbiting read as a portal, not a feature. */
      s.el = Math.max(-90, Math.min(90,
        drag.el + dy * 0.35 * (prefs.v3_invert_y ? -1 : 1)));
      if (drag.pivot) {
        var np = BV.proj3d.orbitProjector(s.az, s.el, svg._persp);
        var p2 = np.project(drag.pivot);
        s.box = { x: p2[0] - drag.box.w / 2, y: p2[1] - drag.box.h / 2,
                  w: drag.box.w, h: drag.box.h };
      }
      redraw();
    });
    ["pointerup", "pointercancel"].forEach(function (ev) {
      svg.addEventListener(ev, function () { drag = null; svg.classList.remove("dragging"); });
    });
    svg.addEventListener("dblclick", function () { s.box = null; redraw(); });
  }

  /* ---- side panel ---- */

  function panelRow(opts) {
    var node = BV.el("div", { class: "v3-row" + (opts.dim ? " dim" : "") });
    var head = BV.el("div", { class: "v3-row-head" });
    if (opts.check) head.appendChild(opts.check);
    if (opts.swatch) head.insertAdjacentHTML("beforeend",
      '<span class="v3-swatch" style="background:' + opts.swatch + '"></span>');
    head.insertAdjacentHTML("beforeend",
      '<span class="v3-row-label">' + BV.esc(opts.label) + "</span>" +
      '<span class="v3-row-tags">' + (opts.tags || "") + "</span>");
    var body = BV.el("div", { class: "v3-row-body" });
    if (opts.fillBody) opts.fillBody(body);
    node.appendChild(head);
    node.appendChild(body);
    BV.collapsible(node, head, body, { open: !!opts.open });
    return node;
  }

  function zoneCheck(z, s, redraw) {
    var c = BV.el("input", { type: "checkbox", class: "v3-check", title: "show in viewport" });
    c.checked = !s.hidden[z.n];
    c.addEventListener("click", function (e) { e.stopPropagation(); });
    c.addEventListener("change", function () {
      if (c.checked) delete s.hidden[z.n]; else s.hidden[z.n] = true;
      redraw();
    });
    return c;
  }

  function catHead(label, enabledCount, total, actions) {
    var el = BV.el("div", { class: "v3-cat" });
    el.innerHTML = '<span class="v3-cat-label">' + BV.esc(label) + "</span>" +
      (total === null ? "" :
        '<span class="v3-cat-count">' + enabledCount + "/" + total + "</span>");
    (actions || []).forEach(function (a) { el.appendChild(a); });
    return el;
  }

  function miniBtn(text, title, onClick) {
    var b = BV.el("button", { class: "btn v3-mini", title: title }, text);
    b.addEventListener("click", onClick);
    return b;
  }

  function buildSide(side, data, s, colors, redraw, robot, reload) {
    side.innerHTML = "";
    var listed = function (arr) {
      return arr.filter(function (e) {
        if (s.group && e.group && e.group !== s.group) return false;
        return s.showDisabled || e.enabled || e.active;
      });
    };

    /* the robot: imported .def kinematics + this backup's own pose */
    if (robot) {
      var doImport = function (path) {
        BV.api.call("import_kinematics", path || "").then(function (res) {
          if (!res) return; /* dialog cancelled */
          BV.toast("imported " + res.imported + " robot types" +
            (res.skipped ? " (" + res.skipped + " non-robot files skipped)" : ""), 3200);
          reload();
        });
      };
      side.appendChild(catHead("robot", null, null, [
        miniBtn("import…", "re-import kinematics from a Roboguide “Robot Library” folder", function () { doImport(""); }),
      ]));
      var rtags = "";
      if (robot.matched) {
        rtags += BV.pill(robot.type_name, "acc");
        if (robot.calib && robot.calib.ok) {
          rtags += BV.pill(robot.flange_dz ? "flange +" + robot.flange_dz + " mm"
            : "verified", "ok-soft");
        } else if (robot.calib) {
          rtags += BV.pill("mismatch", "err");
        } else {
          rtags += BV.pill("unverified", "ghost");
        }
      } else {
        rtags += BV.pill("no kinematics for this type", "ghost");
      }
      side.appendChild(panelRow({
        label: robot.backup_type || "unknown type",
        tags: rtags,
        open: !robot.matched, /* type not covered: lead with the how */
        fillBody: function (body) {
          if (!robot.matched) {
            /* plain-language guidance, one-click when we can */
            var c = robot.counts || { builtin: 0, imported: 0 };
            var hint = "“" + (robot.backup_type || "?") + "” isn’t covered yet (" +
              c.builtin + " built-in types" +
              (c.imported ? " + " + c.imported + " imported" : "") +
              ") — importing from a Roboguide install adds every type its " +
              "library has, in one go.";
            body.insertAdjacentHTML("beforeend",
              '<div class="v3-import-hint">' + BV.esc(hint) + "</div>");
            if (robot.suggested_library) {
              var one = BV.el("button", { class: "btn primary v3-import-btn",
                title: robot.suggested_library }, "import from this PC’s Roboguide");
              one.addEventListener("click", function () { doImport(robot.suggested_library); });
              body.appendChild(one);
            } else {
              body.insertAdjacentHTML("beforeend",
                '<div class="v3-import-hint dim">no Roboguide found on this PC — ' +
                "pick the folder yourself (usually " +
                "C:\\ProgramData\\FANUC\\ROBOGUIDECore\\Robot Library, possibly " +
                "copied from another machine)</div>");
            }
            var pick = BV.el("button", { class: "btn v3-import-btn" }, "pick folder…");
            pick.addEventListener("click", function () { doImport(""); });
            body.appendChild(pick);
            return;
          }
          var cts = robot.counts || { builtin: 0, imported: 0 };
          var src = robot.source_kind === "builtin"
            ? "built-in" + (robot.validated
              ? ", validated on " + robot.validated.robots + " robot" +
                (robot.validated.robots === 1 ? "" : "s") +
                " (≤" + robot.validated.max_xy_mm + " mm)"
              : ", not yet validated against a controller")
            : "imported " + robot.imported_date;
          var kv = [
            { key: "Backup reports", value: robot.backup_type || "—" },
            { key: "Matched kinematics", value: robot.type_name + " (" + src + ")" },
            { key: "Registry", value: cts.builtin + " built-in + " + cts.imported + " imported types" },
          ];
          if (robot.pose_date) kv.push({ key: "Pose snapshot", value: robot.pose_date });
          if (robot.calib) {
            kv.push({ key: "Check vs backup TCP", value:
              robot.calib.dxy.toFixed(2) + " mm xy · " +
              robot.calib.ori_err.toFixed(3) + "° · flange z " +
              robot.calib.dz.toFixed(2) + " mm" + (robot.calib.ok ? "" : " — MISMATCH") });
          }
          body.appendChild(BV.dcsDetail(kv));
        },
      }));

      var frames = robotFrames(s, robot);
      if (frames) {
        var srcPill = function () {
          return s.pose ? BV.pill("manual", "warn")
            : BV.pill(robot.q ? "backup" : "home", "ghost");
        };
        var poseRow = panelRow({
          label: "pose",
          tags: srcPill(),
          fillBody: function (body) {
            var grid = BV.el("div", { class: "v3-pose-grid" });
            var q = poseQ(s, robot);
            var inputs = [];
            robot.kin.joints.forEach(function (j, i) {
              var cell = BV.el("label", { class: "v3-pose-cell" });
              cell.insertAdjacentHTML("beforeend", "<span>J" + j.n + "</span>");
              var inp = BV.el("input", { type: "number", step: "1", value: String(+(q[i] || 0).toFixed(2)) });
              inp.addEventListener("change", function () {
                s.pose = inputs.map(function (x) { return parseFloat(x.value) || 0; });
                poseRow.querySelector(".v3-row-tags").innerHTML = BV.pill("manual", "warn");
                redraw();
              });
              inputs.push(inp);
              cell.appendChild(inp);
              grid.appendChild(cell);
            });
            body.appendChild(grid);
            var rst = BV.el("button", { class: "btn v3-mini", title: "back to the backup’s own pose" }, "reset pose");
            rst.addEventListener("click", function () {
              s.pose = null;
              buildSide(side, data, s, colors, redraw, robot, reload);
              redraw();
            });
            body.appendChild(rst);
          },
        });
        side.appendChild(poseRow);
      }
    }

    /* cartesian zones - the drawable category, checkbox + swatch */
    var zs = listed(data.cpc);
    if (data.cpc.length) {
      var en = data.cpc.filter(function (z) { return z.enabled; }).length;
      side.appendChild(catHead("cartesian position", en, data.cpc.length, [
        miniBtn("all", "show every listed zone", function () {
          zs.forEach(function (z) { delete s.hidden[z.n]; });
          buildSide(side, data, s, colors, redraw, robot, reload); redraw();
        }),
        miniBtn("none", "hide every listed zone", function () {
          zs.forEach(function (z) { s.hidden[z.n] = true; });
          buildSide(side, data, s, colors, redraw, robot, reload); redraw();
        }),
      ]));
      zs.forEach(function (z) {
        var tags = "";
        if (z.side === "out") tags += BV.pill("keep-out", "acc");
        else if (z.side === "in") tags += BV.pill("keep-in", "ghost");
        else tags += BV.pill(z.method_text || "?", "ghost");
        if (z.approx || z.frame_missing) tags += BV.pill("approx", "warn");
        if (!z.enabled) tags += BV.pill("disabled", "ghost");
        tags += statusPill(z.status);
        side.appendChild(panelRow({
          check: zoneCheck(z, s, redraw),
          swatch: colors[z.n],
          label: z.label,
          tags: tags,
          dim: !z.enabled,
          fillBody: function (body) { body.appendChild(BV.dcsDetail(z.detail || [])); },
        }));
      });
    }

    /* data-only categories: nothing honest to draw without a robot model */
    [["joint position", data.jpc, function (e) {
      return BV.pill("J" + e.axis, "ghost") + BV.pill(e.side === "out" ? "keep-out" : "keep-in", "ghost");
    }],
    ["cartesian speed", data.csc, function (e) {
      return BV.pill(e.limit + " mm/s", "ghost");
    }],
    ["joint speed", data.jsc, function (e) {
      return BV.pill("J" + e.axis, "ghost");
    }]].forEach(function (cat) {
      var entries = listed(cat[1]);
      if (!entries.length) return;
      var en = cat[1].filter(function (e) { return e.enabled; }).length;
      side.appendChild(catHead(cat[0], en, cat[1].length));
      entries.forEach(function (e) {
        side.appendChild(panelRow({
          label: e.label,
          tags: cat[2](e) + (!e.enabled ? BV.pill("disabled", "ghost") : ""),
          dim: !e.enabled,
          fillBody: function (body) { body.appendChild(BV.dcsDetail(e.detail || [])); },
        }));
      });
    });

    /* DCS user models (EOAT etc. collision shapes, from $DCSS_MODEL with the
       verify report merged in). Still data-only in the viewport: every real
       element is link/faceplate-attached, so placing one needs kinematics
       we don't have. Element rows carry the geometry (shape · radius ·
       link) so the panel tells the whole story. */
    var ms = data.models.filter(function (m) { return s.showDisabled || m.active; });
    if (ms.length) {
      var mn = data.models.filter(function (m) { return m.active; }).length;
      side.appendChild(catHead("user models", mn, data.models.length));
      ms.forEach(function (m) {
        side.appendChild(panelRow({
          label: m.label,
          tags: (m.elem_count ? BV.pill(m.elem_count + " elem", "ghost") : "") + statusPill(m.status),
          dim: !m.active,
          fillBody: function (body) {
            if (m.detail && m.detail.length) body.appendChild(BV.dcsDetail(m.detail));
            var els = (m.elements || []).filter(function (el) {
              return s.showDisabled || el.enabled !== false;
            });
            els.forEach(function (el) {
              var sub = "element " + el.num;
              if (el.shape) {
                sub += " · " + el.shape.toLowerCase() +
                  (el.size ? " r" + el.size : "") +
                  " · " + (el.link_no === 99 ? "faceplate" : "link " + el.link_no);
              }
              body.insertAdjacentHTML("beforeend",
                '<div class="dcs-sub' + (el.enabled === false ? " dim" : "") + '">' +
                BV.esc(sub) + "</div>");
              body.appendChild(BV.dcsDetail(el.detail || []));
            });
            if (!(m.detail && m.detail.length) && !els.length) {
              body.innerHTML = '<div class="dim" style="padding:.3rem .2rem">no elements</div>';
            }
          },
        }));
      });
    }

    if (!side.children.length) {
      side.innerHTML = '<div class="dim" style="padding:.6rem .4rem">no DCS checks configured' +
        (s.showDisabled ? "" : " — “show disabled” lists the empty slots") + "</div>";
    }
    BV.persistScroll("view3d-side", side);
  }

  /* ---- tab ---- */

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    Promise.all([
      BV.api.call("get_dcs_zones"),
      BV.api.call("get_robot_pose").catch(function () { return null; }),
    ]).then(function (rs) {
      var data = rs[0], robot = rs[1];
      var s = st();
      view.classList.add("v3-host");

      var base = accentHsl();
      var colors = {};
      data.cpc.forEach(function (z) { colors[z.n] = zoneColor(base, z.n); });

      var vp = BV.el("div", { class: "v3-vp" });
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "v3-svg");
      svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
      vp.appendChild(svg);
      /* pixel-space overlay for labels/ruler - sits on top, ignores mouse */
      var ovl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      ovl.setAttribute("class", "v3-ovl");
      svg._ovl = ovl;
      vp.appendChild(ovl);
      var side = BV.el("aside", { class: "v3-side" });
      view.appendChild(vp);
      view.appendChild(side);

      function redraw() { draw(svg, data, s, colors, robot); }
      function rebuildSide() { buildSide(side, data, s, colors, redraw, robot, reload); }
      function reload() {
        BV.api.call("get_robot_pose").catch(function () { return null; })
          .then(function (r) {
            robot = r;
            rebuildSide();
            redraw();
          });
      }

      /* snap views live on the viewport cube (top-right) - click a face,
         edge or corner. The cube markup is rebuilt every draw, so the
         click handler is delegated from the overlay root. */
      ovl.addEventListener("click", function (e) {
        var t = e.target && e.target.closest ? e.target.closest("[data-az]") : null;
        if (!t) return;
        s.az = parseFloat(t.getAttribute("data-az"));
        s.el = parseFloat(t.getAttribute("data-el"));
        s.box = null; /* snapping also refits */
        redraw();
      });

      /* toolbar: fit · perspective · show-disabled · group filter */
      var fitBtn = BV.el("button", { class: "btn", title: "reset pan/zoom (double-click does too)" }, "fit");
      fitBtn.addEventListener("click", function () { s.box = null; redraw(); });
      toolbar.appendChild(fitBtn);
      var perspBtn = BV.el("button", {
        class: "btn" + (s.persp ? " primary" : ""),
        title: "perspective projection — off = orthographic (parallel, true to scale)",
      }, "perspective");
      perspBtn.addEventListener("click", function () {
        s.persp = !s.persp;
        perspBtn.classList.toggle("primary", s.persp);
        redraw();
      });
      toolbar.appendChild(perspBtn);
      var disBtn = BV.el("button", {
        class: "btn" + (s.showDisabled ? " primary" : ""),
        title: "list/draw the unconfigured + disabled checks too",
      }, "show disabled");
      disBtn.addEventListener("click", function () {
        s.showDisabled = !s.showDisabled;
        disBtn.classList.toggle("primary", s.showDisabled);
        rebuildSide();
        redraw();
      });
      toolbar.appendChild(disBtn);
      if (data.groups.length > 1) {
        toolbar.appendChild(BV.segmented(
          [{ id: "0", label: "all groups" }].concat(data.groups.map(function (g) {
            return { id: String(g), label: "grp " + g };
          })),
          { value: String(s.group), onChange: function (id) {
            s.group = parseInt(id, 10) || 0;
            rebuildSide();
            redraw();
          } }
        ).el);
      }

      rebuildSide();
      redraw();
      wireViewport(svg, s, redraw);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">no DCS zone data</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "view3d", label: "3d view", render: render });
})();
