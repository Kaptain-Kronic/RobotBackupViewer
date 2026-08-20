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
      /* the default iso view, turntable angles, unbounded. Deliberately OFF
         the 45° grid: plant fences love 0/45/90° orientations, and a wall
         parallel to the eye azimuth degenerates to a sliver (seen on a real
         -45° fence). Do not "tidy" these to -25/35. */
      s.az = -24.8;
      s.el = 36.8;
      s.showDisabled = false;
      s.hidden = {};     /* zone n -> true when unchecked */
      s.group = 0;       /* 0 = all groups */
      s.box = null;      /* single pan/zoom override (null = auto-fit) */
      s.persp = false;   /* orthographic by default */
      /* program playback: prog is a .LS file name, step the selected move.
         s.prog and s.pose are MUTUALLY EXCLUSIVE - both drive the arm, so
         loading a program clears a hand-edited pose and editing a joint
         drops the program. Without that the pose grid and the step list
         fight over the skeleton and the "manual" pill lies about which
         one you are looking at. */
      s.prog = null;
      s.step = 0;
      s.showPath = true;
      s.showPoints = true;
      s.t = 0;           /* playhead, ms into the run */
      s.speed = 1;
      /* deliberately NOT stored: whether it was playing. Coming back to a tab
         and finding the robot already moving is a jump scare, not a feature -
         the scrub position restores, the motion does not resume itself. */
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

  /* one status map, owned by the dcs tab (loaded first in index.html). This
     used to be a private copy that disagreed with it - the same zone read
     green here and red there. */
  function statusPill(stat) {
    return BV.dcsStatusPill(stat);
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

  /* precedence: the loaded program's selected step, then a hand-edited
     pose, then the backup's own CURPOS snapshot, then home. The first two
     never coexist - loading a program clears s.pose and editing a joint
     clears s.prog (see st()) - so this is an order, not a fight. */
  function poseQ(s, robot, pose) {
    var row = pose && pose.steps ? pose.steps[s.step] : null;
    if (row && row.q) return row.q;
    if (s.pose) return s.pose;
    if (robot && robot.q) return robot.q;
    return [];
  }

  /* frames when the arm is honestly posable: kinematics matched AND the
     backup's own position report did not contradict them. calib=null
     (no CURPOS to check against) still poses, flagged unverified. */
  function robotFrames(s, robot, pose) {
    if (!poseGate(robot)) return null;
    return BV.fk.chain(robot.kin, poseQ(s, robot, pose), robot.flange_dz || 0);
  }

  /* the one contradiction gate. Python has the same rule in _posable_chain,
     so a backup whose kinematics disagree with its own position report gets
     no arm here AND no fk-placed point there. Every path that ends in
     BV.fk.chain goes through this - a per-frame redraw that skipped it would
     cheerfully draw a whole program's worth of arms the still frame refuses. */
  function poseGate(robot) {
    if (!robot || !robot.kin) return false;
    return !(robot.calib && !robot.calib.ok);
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

  /* the posed arm as scene-layer strings: joint-to-joint capsule limbs (a
     schematic body, sized from the arm's reach and tapering to the wrist -
     deliberately NOT the DCS robot model or a mesh, just enough girth to
     read as a robot) + the DCS user-model elements at their true frames.
     Spheres project as circles, capsules as round-cap strokes, all in world
     mm so they scale with the scene. Its own function because playback
     rebuilds THIS layer every frame and nothing else. */
  function armLayer(proj, skel, elems) {
    var out = [];
    if (!skel) return out;
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
    return out;
  }

  /* the arm's polyline in world mm: the floor anchor, every joint origin,
     the faceplate. Shared by the full draw and the per-frame redraw so the
     body can never differ between a still frame and a moving one. */
  function skeletonOf(robot, frames) {
    var zr = robot.kin.zero || [0, 0, 0];
    var out = [[-zr[0], -zr[1], -zr[2]]];
    frames.joints.forEach(function (m) { out.push([m[0][3], m[1][3], m[2][3]]); });
    var fp = frames.faceplate;
    out.push([fp[0][3], fp[1][3], fp[2][3]]);
    return out;
  }

  /* the placed steps of the loaded program, in program order */
  function pathPoints(path) {
    if (!path) return [];
    return path.steps.filter(function (st) { return st.ok; });
  }

  function draw(svg, data, s, colors, robot, path, pose) {
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
    var frames = robotFrames(s, robot, pose);
    var skel = null, elems = [];
    if (frames) {
      skel = skeletonOf(robot, frames);
      wpts = wpts.concat(skel);
      elems = posedElements(data, frames);
      elems.forEach(function (e) {
        wpts.push(e.p1);
        if (e.p2) wpts.push(e.p2);
      });
    }
    /* the loaded program's whole extent joins the bounds ONCE, so the fit
       sphere already covers every point the arm will visit. Playback moves
       the arm inside a view that was sized for the entire run - otherwise
       the auto-fit pumps on every frame, the same way fitting the projected
       bounding box made it breathe while orbiting. */
    var placed = pathPoints(path);
    placed.forEach(function (st) { wpts.push(st.world); });
    /* and every pose the arm will take while playing, so the fit is sized for
       the whole run before the first frame. Eight AABB corners, computed once
       when the program loaded - growing the bounds per frame is the same trap
       the projected-bounding-box fit was, and the view pumps. */
    if (frames && pose && pose._reach) {
      pose._reach.forEach(function (p) { wpts.push(p); });
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

    /* ---- scene layer (viewBox space): geometry only, never text ----
       Five ordered groups rather than one blob, in exactly the paint order
       the single blob had (translucent zones still wash over the arm). The
       arm sits in its own group because playback rewrites that one alone. */
    var out = [];        /* base: grid + axes */
    var wires = [];

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

    /* wireframe per zone (keep-in = dashed envelope) */
    scr.forEach(function (sz) {
      var dash = sz.z.side === "in" ? ' stroke-dasharray="6 4"' : "";
      var d = "";
      sz.edges.forEach(function (e) {
        d += "M" + sz.spts[e[0]][0] + " " + sz.spts[e[0]][1] +
          "L" + sz.spts[e[1]][0] + " " + sz.spts[e[1]][1];
      });
      wires.push('<path class="v3-edge" d="' + d + '" stroke="' + colors[sz.z.n] + '"' + dash + "/>");
    });

    /* the taught path: one polyline through the placed points, in world mm.
       Joint-recorded and cartesian points join the same line - it is where
       the tool centre goes, whichever way the point was written down. */
    var pathOut = [];
    if (s.showPath && placed.length > 1) {
      var pd = "";
      placed.forEach(function (st, i) {
        var q = proj.project(st.world);
        pd += (i ? "L" : "M") + q[0] + " " + q[1];
      });
      pathOut.push('<path class="v3-path" d="' + pd + '"/>');
    }

    svg.innerHTML =
      '<g class="v3-l-base">' + out.join("") + "</g>" +
      '<g class="v3-l-path">' + pathOut.join("") + "</g>" +
      '<g class="v3-l-arm">' + armLayer(proj, skel, elems).join("") + "</g>" +
      '<g class="v3-l-zone">' + faces.map(function (f) { return f.html; }).join("") + "</g>" +
      '<g class="v3-l-wire">' + wires.join("") + "</g>";
    /* handles so a per-frame redraw can rewrite one layer and leave the
       projector, the viewBox and the painter sort exactly as they are */
    svg._L = {
      base: svg.children[0], path: svg.children[1], arm: svg.children[2],
      zone: svg.children[3], wire: svg.children[4],
    };

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
    svg._toPx = toPx;   /* the per-frame redraw re-uses this exact mapping */
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

    /* taught points: constant-size markers in pixel space like the tcp
       crosshair, so they stay clickable at any zoom. The selected step wears
       its own class and carries its P id. */
    if (s.showPoints) {
      placed.forEach(function (st) {
        var q = toPx(proj.project(st.world));
        var sel = st.i === s.step ? " sel" : "";
        ov.push('<circle class="v3-pt' + sel + '" cx="' + q[0] + '" cy="' + q[1] +
          '" r="' + (sel ? 5.5 : 3.5) + '" data-step="' + st.i + '"><title>' +
          BV.esc(st.target.raw + "  " + st.text) + "</title></circle>");
        if (sel) {
          ov.push('<text class="v3-pt-lab" x="' + (q[0] + 9) + '" y="' + (q[1] - 7) +
            '" font-size="11">' + BV.esc(st.target.raw) + "</text>");
        }
      });
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
    if (path) {
      var refused = path.counts.refused;
      if (refused) {
        notes.push("⚠ " + refused + " of " + path.counts.steps +
          " steps not placed — see “program” on the right");
      }
      if (!path.robot.posable && path.counts.joint === 0 &&
          path.steps.some(function (st) { return st.why === "no-kinematics"; })) {
        notes.push("no kinematics for this type — cartesian points shown, " +
          "joint-recorded ones cannot be placed");
      }
    }
    if (pose && pose.counts.solved) {
      notes.push("arm posture solved from cartesian points — the taught CONFIG " +
        "is not decoded, so a pose may take a different branch than the robot did");
    }
    if (pose && pose.counts.posed) {
      notes.push("path preview — not a cycle-time simulation " +
        "(no acceleration, no deceleration, no CNT blending)");
    }
    if (pose && pose.counts.refused && path && path.counts.placed) {
      var unposed = pose.counts.refused - (path.counts.refused || 0);
      if (unposed > 0) {
        notes.push("⚠ " + unposed + " placed point" + (unposed === 1 ? "" : "s") +
          " could not be reached by the arm — see “program” on the right");
      }
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

    /* everything that moves while playing lives in one trailing group, so a
       frame rewrites that and leaves the labels, ruler, notes and cube - all
       camera-dependent only - exactly where they are */
    ov.push('<g class="v3-ovl-live"></g>');
    ovl.innerHTML = ov.join("");
    svg._live = ovl.querySelector(".v3-ovl-live");
  }

  /* ---- the per-frame redraw ----
     Rewrites the arm group and the live overlay and NOTHING else: the
     projector, the viewBox, the painter's sort and every label stay as the
     last full draw left them. */
  function drawArm(svg, data, s, robot, q, tool) {
    var proj = svg._proj;
    if (!svg._L || !proj) return;
    var frames = poseGate(robot) && q && q.length
      ? BV.fk.chain(robot.kin, q, robot.flange_dz || 0) : null;
    var skel = null, elems = [];
    if (frames) {
      skel = skeletonOf(robot, frames);
      elems = posedElements(data, frames);
    }
    svg._L.arm.innerHTML = armLayer(proj, skel, elems).join("");
    if (!svg._live || !svg._toPx) return;
    var ov = [];
    if (frames) {
      /* the tool centre, not the faceplate - the drawn path is the tcp's */
      var tcp = BV.fk.apply(frames.faceplate, (tool || [0, 0, 0]).slice(0, 3));
      var q2 = svg._toPx(proj.project(tcp));
      ov.push('<circle class="v3-tcp-live" cx="' + q2[0] + '" cy="' + q2[1] + '" r="5"/>');
    }
    svg._live.innerHTML = ov.join("");
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

  /* ---- playback ----
     One uniform rule: lerp in joint space between consecutive knots. A JOINT
     move satisfies that exactly - a FANUC joint move IS a joint-space lerp,
     all axes starting and stopping together - and a linear or circular move
     satisfies it to the density of the knots the solver walked along the
     drawn line. So there is no per-motion-type branch in the render loop. */

  /* a move whose speed the backup cannot price still has to take SOME time on
     screen; this is that time, and the viewport says which moves used it */
  var UNTIMED_MS = 700;
  var MIN_SEG_MS = 40;

  function timeline(pose) {
    if (!pose || !pose.steps) return null;
    var segs = [], t = 0, prev = null, untimed = 0;
    pose.steps.forEach(function (r) {
      if (!r.solved || !r.knots.length) return;
      var ms = r.dur_ms;
      if (ms === null || ms === undefined) { ms = UNTIMED_MS; untimed++; }
      ms = Math.max(ms, MIN_SEG_MS);
      var from = prev || r.knots[0];
      var per = ms / r.knots.length;
      r.knots.forEach(function (k) {
        segs.push({ t0: t, t1: t + per, a: from, b: k, step: r.i });
        t += per;
        from = k;
      });
      prev = r.knots[r.knots.length - 1];
    });
    return segs.length ? { segs: segs, total: t, untimed: untimed } : null;
  }

  /* the segment a playhead sits in. A time exactly ON a boundary belongs to
     the segment that ENDS there, not the one that starts: seeking to a move
     means "the robot has arrived at that move", and the two segments agree on
     the joints at that instant anyway, so nothing jumps. */
  function segAt(tl, t) {
    var segs = tl.segs;
    if (t <= segs[0].t0) return segs[0];
    var lo = 0, hi = segs.length - 1;
    while (lo < hi) {
      var m = (lo + hi) >> 1;
      if (segs[m].t1 < t) lo = m + 1; else hi = m;
    }
    return segs[lo];
  }

  /* the clock time at which a step ARRIVES. A linear move is several
     segments; you want the last one, which is the taught point itself. */
  function endOfStep(tl, i) {
    for (var k = tl.segs.length - 1; k >= 0; k--) {
      if (tl.segs[k].step === i) return tl.segs[k].t1;
    }
    return null;
  }

  function qAt(tl, t) {
    var sg = segAt(tl, t);
    var u = sg.t1 > sg.t0 ? Math.max(0, Math.min(1, (t - sg.t0) / (sg.t1 - sg.t0))) : 1;
    return sg.a.map(function (v, i) { return v + (sg.b[i] - v) * u; });
  }

  function clock(ms, total) {
    return (ms / 1000).toFixed(1) + " / " + (total / 1000).toFixed(1) + " s";
  }

  /* the eight corners of the box every pose in the run fits inside. Computed
     once per loaded program: one fk per step, not per frame. */
  function reachCorners(robot, pose) {
    if (!poseGate(robot) || !pose || !pose.steps) return null;
    var lo = null, hi = null;
    pose.steps.forEach(function (r) {
      if (!r.solved || !r.q) return;
      skeletonOf(robot, BV.fk.chain(robot.kin, r.q, robot.flange_dz || 0))
        .forEach(function (p) {
          if (!lo) { lo = p.slice(); hi = p.slice(); return; }
          for (var k = 0; k < 3; k++) {
            if (p[k] < lo[k]) lo[k] = p[k];
            if (p[k] > hi[k]) hi[k] = p[k];
          }
        });
    });
    if (!lo) return null;
    var out = [];
    [0, 1].forEach(function (a) {
      [0, 1].forEach(function (b) {
        [0, 1].forEach(function (c) {
          out.push([a ? hi[0] : lo[0], b ? hi[1] : lo[1], c ? hi[2] : lo[2]]);
        });
      });
    });
    return out;
  }

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

  /* one step row: the line number, the verbatim instruction, and its
     honesty pills. Flat, not a collapsible - a program runs to hundreds of
     moves, and the evidence for the SELECTED one shows once, below the list. */
  function stepRow(st, s, onPick, pr) {
    /* two different questions, two different answers: "not placed" means the
       backup does not say where the point is; "not reached" means it does and
       the arm cannot get there. Never collapse them into one pill. */
    var tags = "";
    if (!st.ok) {
      tags += BV.pill("not placed", "err");
    } else if (!pr) {
      tags += BV.pill("taught", "ghost");
    } else if (!pr.solved) {
      tags += BV.pill("not reached", "err");
    } else {
      tags += pr.source === "joint" ? BV.pill("exact", "ok-soft")
        : BV.pill("solved", "ghost");
    }
    if (st.offset || st.tool_offset) tags += BV.pill("offset", "warn");
    if (st.incremental) tags += BV.pill("inc", "warn");
    if (st.via) tags += BV.pill("via", "ghost");
    var row = BV.el("div", {
      /* dim = the backup does not say where the point is. unreached = it
         does, and the arm cannot get there - the point still draws, because
         it is still evidence about the program. Two states, two classes. */
      class: "v3-step" + (st.i === s.step ? " sel" : "") +
        (st.ok ? "" : " dim") +
        (st.ok && pr && !pr.solved ? " unreached" : ""),
      title: st.note || (pr && pr.note) || st.text,
    });
    row.innerHTML = '<span class="v3-step-n">' + st.line + "</span>" +
      '<span class="v3-step-t">' + BV.esc(st.text) + "</span>" +
      '<span class="v3-step-tags">' + tags + "</span>";
    row.addEventListener("click", function () { onPick(st.i); });
    return row;
  }

  function programSection(side, s, path, pick, clear, pose) {
    var c = path.counts;
    side.appendChild(catHead("program", c.placed, c.steps, [
      miniBtn("clear", "stop showing this program", clear),
    ]));
    var head = BV.el("div", { class: "v3-prog-head" });
    head.innerHTML = '<span class="v3-prog-name">' + BV.esc(path.name) + "</span>" +
      (path.comment ? '<span class="v3-prog-cmt">' + BV.esc(path.comment) + "</span>" : "");
    side.appendChild(head);

    /* every assumption this drawing rests on, stated once, before the steps */
    var notes = (path.assumptions || []).map(function (a) { return a.text; });
    if (pose && pose.counts.solved) {
      notes.push(pose.counts.exact + " of " + (pose.counts.exact + pose.counts.solved) +
        " poses come straight from taught joints; the rest are solved from the " +
        "cartesian point, and the taught CONFIG is not decoded — so a solved " +
        "posture may differ from the one the robot used");
    }
    if (c.refused) {
      notes.push(c.refused + " of " + c.steps + " moves could not be placed — " +
        "each one says why in the list below");
    }
    if (notes.length) {
      var nb = BV.el("div", { class: "v3-prog-notes" });
      nb.innerHTML = notes.map(function (t) { return "<div>" + BV.esc(t) + "</div>"; }).join("");
      side.appendChild(nb);
    }

    var poseRows = (pose && pose.steps) || [];
    var list = BV.el("div", { class: "v3-steps" });
    path.steps.forEach(function (st) {
      list.appendChild(stepRow(st, s, pick, poseRows[st.i]));
    });
    side.appendChild(list);

    /* the selected move's evidence, in the pendant-style block the rest of
       this panel uses */
    var st = path.steps[s.step];
    if (!st) return;
    var kv = [
      { key: "Line", value: String(st.line) },
      { key: "Instruction", value: st.text },
      { key: "Target", value: st.target.raw +
        (st.target.comment ? "  " + st.target.comment : "") },
    ];
    if (st.via) kv.push({ key: "Via", value: st.via.raw });
    if (st.uf !== null) kv.push({ key: "UF / UT", value: st.uf + " / " + st.ut });
    if (st.config) kv.push({ key: "CONFIG", value: st.config });
    function nums(a, dp) {
      return a.map(function (v) { return v.toFixed(dp); }).join("  ");
    }
    if (st.xyzwpr) kv.push({ key: "Taught (in uframe)", value: nums(st.xyzwpr, 1) });
    if (st.joints) kv.push({ key: "Taught joints", value: nums(st.joints, 2) });
    if (st.world) kv.push({ key: "World tcp", value: nums(st.world, 1) });
    if (st.speed) kv.push({ key: "Speed", value: st.speed.raw });
    if (st.term) kv.push({ key: "Termination", value: st.term.raw });
    if (st.options.length) kv.push({ key: "Options", value: st.options.join("  ") });
    if (st.dist_mm !== null) kv.push({ key: "Distance", value: st.dist_mm.toFixed(1) + " mm" });
    if (st.dur_ms !== null) {
      kv.push({ key: "Duration",
                value: (st.dur_ms / 1000).toFixed(2) + " s (" + st.dur_kind + ")" });
    }
    if (st.note) kv.push({ key: "Not placed", value: st.note });
    var pr = poseRows[s.step];
    if (pr && pr.q) {
      kv.push({ key: "Arm joints", value: nums(pr.q, 2) });
      kv.push({ key: "Posed by", value: pr.source === "joint"
        ? "the taught joints — exact, no solver"
        : "the inverse solver — posture not from CONFIG" });
    }
    if (pr && pr.residual) {
      kv.push({ key: "Solve residual", value:
        pr.residual.pos_mm.toFixed(3) + " mm · " + pr.residual.ori_deg.toFixed(4) +
        "° · " + pr.residual.iters + " iterations" });
    }
    if (pr && pr.note) kv.push({ key: "Not reached", value: pr.note });
    var det = BV.el("div", { class: "v3-prog-detail" });
    det.appendChild(BV.dcsDetail(kv));
    side.appendChild(det);
  }

  function buildSide(side, data, s, colors, redraw, robot, reload, path, pick,
                     clear, pose, dropProgram) {
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

      var frames = robotFrames(s, robot, pose);
      if (frames) {
        var srcPill = function () {
          if (pose && pose.steps && pose.steps[s.step] && pose.steps[s.step].q) {
            return BV.pill("program", "acc");
          }
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
                /* a hand-edited pose and a program cannot both drive the arm;
                   typing here means you want this one (see st()) */
                s.pose = inputs.map(function (x) { return parseFloat(x.value) || 0; });
                if (dropProgram) dropProgram();
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
              buildSide(side, data, s, colors, redraw, robot, reload, path, pick, clear,
                pose, dropProgram);
              redraw();
            });
            body.appendChild(rst);
          },
        });
        side.appendChild(poseRow);
      }
    }

    /* the loaded program: its moves and the evidence for the selected one.
       Under the robot rows because it is about the arm; above the zones
       because it is what you came here to watch. */
    if (path) programSection(side, s, path, pick, clear, pose);

    /* cartesian zones - the drawable category, checkbox + swatch */
    var zs = listed(data.cpc);
    if (data.cpc.length) {
      var en = data.cpc.filter(function (z) { return z.enabled; }).length;
      side.appendChild(catHead("cartesian position", en, data.cpc.length, [
        miniBtn("all", "show every listed zone", function () {
          zs.forEach(function (z) { delete s.hidden[z.n]; });
          buildSide(side, data, s, colors, redraw, robot, reload, path, pick, clear,
                pose, dropProgram); redraw();
        }),
        miniBtn("none", "hide every listed zone", function () {
          zs.forEach(function (z) { s.hidden[z.n] = true; });
          buildSide(side, data, s, colors, redraw, robot, reload, path, pick, clear,
                pose, dropProgram); redraw();
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
       verify report merged in). Drawn in the viewport while the arm is posed
       (posedElements puts link/faceplate shapes on the FK frames); with no
       matched kinematics they stay data-only here. Element rows carry the
       geometry (shape · radius · link) so the panel tells the whole story. */
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
    /* cameras carry scanner CAD, not DCS - their 3d view lives in cvx3d.js */
    if ((BV.state.manifest.backup_type || "").indexOf("camera") >= 0 && BV.cvx3d) {
      return BV.cvx3d.render(view, toolbar, params);
    }
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

      var path = null;      /* the loaded program's resolved path, or null */
      var pose = null;      /* and the joint angles that walk it */

      function redraw() {
        draw(svg, data, s, colors, robot, path, pose);
        /* a full draw rebuilds the overlay, which empties the live group and
           poses the arm at the SELECTED step. Re-applying the playhead over
           the fresh frame is what keeps orbiting, pausing and scrubbing from
           snapping the arm somewhere the clock does not agree with. */
        if (tl) showAt(s.t);
      }
      function rebuildSide() {
        buildSide(side, data, s, colors, redraw, robot, reload, path, pick, clear,
                pose, dropProgram);
      }
      function pick(i) {
        /* selecting a move IS seeking to it: the playhead and the highlight
           are one state, so a full redraw cannot put them at odds */
        s.step = i;
        pauseQuietly();
        if (tl) {
          var t = endOfStep(tl, i);
          if (t !== null) s.t = t;
        }
        rebuildSide();
        redraw();
      }
      function clear() {
        /* back out through the router, the way esc does elsewhere - the hash
           IS the state, and a same-hash set fires no hashchange (router.js's
           own idiom, which is why the explicit re-route is here) */
        s.prog = null;
        pose = null;
        pauseQuietly();
        tl = null;
        if (location.hash === "#view3d") BV.route();
        else location.hash = "#view3d";
      }
      function dropProgram() {
        /* the pose grid took the arm. The path stays drawn - it is still
           evidence about the program - but the program stops driving the
           skeleton, so the pose pill can honestly say "manual". */
        pauseQuietly();
        pose = null;
        tl = null;
        syncPlayer();
      }
      function loadProgram(file) {
        return BV.api.call("get_program_path", file).then(function (r) {
          s.prog = file;
          s.step = 0;
          s.pose = null;      /* the program owns the arm now - see st() */
          path = r;
          s.box = null;       /* refit: a path usually reaches past the zones */
          progBtn.textContent = r.name + " ▾";
          syncTools();
          rebuildSide();
          redraw();
          if (!r.robot.posable) return null;
          /* the solve is the expensive half and the path is useful without
             it, so it lands second and the view repaints when it arrives */
          return BV.api.call("get_program_pose", file).then(function (pr) {
            if (s.prog !== file) return;    /* a later pick won the race */
            pose = pr;
            pose._reach = reachCorners(robot, pose);
            tl = timeline(pose);
            s.t = 0;
            rebuildSide();
            redraw();
            syncPlayer();
          }).catch(function () { /* the path still stands on its own */ });
        }).catch(function (e) {
          BV.toast("could not read " + file + " — " + e.message);
        });
      }
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

      /* toolbar: program · path · points · fit · perspective · show-disabled
         · group filter. The picker is a dropPanel rather than a menu because
         a controller carries hundreds of programs and you need to type at
         them - "a filter + a list" is what dropPanel exists for. */
      var progBtn = BV.el("button", {
        class: "btn", title: "draw a program's taught path among the zones",
      }, "program ▾");
      progBtn.addEventListener("click", function () {
        var wrap = BV.el("div", { class: "v3-pick" });
        var filter = BV.el("input", { type: "search", placeholder: "filter programs…" });
        var list = BV.el("div", { class: "v3-pick-list" });
        wrap.appendChild(filter);
        wrap.appendChild(list);
        var handle = BV.dropPanel(progBtn, wrap);
        if (!handle) return;
        BV.api.call("get_programs").then(function (rows) {
          var progs = rows.filter(function (r) { return !r.binary; });
          function paint() {
            var q = filter.value.trim().toUpperCase();
            list.innerHTML = "";
            progs.filter(function (r) {
              return !q || (r.name + " " + (r.comment || "")).toUpperCase().indexOf(q) >= 0;
            }).forEach(function (r) {
              var b = BV.el("button", { class: "v3-pick-item", title: r.comment || "" });
              b.innerHTML = BV.esc(r.name) +
                (r.comment ? '<span class="dim"> ' + BV.esc(r.comment) + "</span>" : "");
              b.addEventListener("click", function () {
                handle.close();
                location.hash = "#view3d/" + encodeURIComponent(r.file);
              });
              list.appendChild(b);
            });
            if (!list.children.length) {
              list.innerHTML = '<div class="dim" style="padding:.4rem">no match</div>';
            }
          }
          filter.addEventListener("input", paint);
          paint();
          filter.focus();
        });
      });
      toolbar.appendChild(progBtn);
      /* these two only mean anything while a program is loaded, so they are
         absent until then rather than sitting there greyed */
      function toggleBtn(key, label, title) {
        var b = BV.el("button", {
          class: "btn" + (s[key] ? " primary" : ""), title: title,
        }, label);
        b.addEventListener("click", function () {
          s[key] = !s[key];
          b.classList.toggle("primary", s[key]);
          redraw();
        });
        toolbar.appendChild(b);
        return b;
      }
      var pathBtn = toggleBtn("showPath", "path",
        "draw the line between the program's taught points");
      var ptsBtn = toggleBtn("showPoints", "points", "mark each taught point");
      function syncTools() { pathBtn.hidden = ptsBtn.hidden = !path; }
      syncTools();
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

      /* ---- the player ----
         A real DOM bar inside .v3-vp, not markup in the overlay: the overlay
         is pointer-events:none by design, and a scrubber you cannot grab is
         not a scrubber. */
      var tl = null;                 /* the flattened timeline, or null */
      var raf = null;                /* the pending frame handle */
      var last = 0;                  /* wall clock of the previous frame */
      var HAS_RAF = typeof window.requestAnimationFrame === "function";

      var bar = BV.el("div", { class: "v3-player", hidden: true });
      var playBtn = BV.el("button", { class: "btn v3-pb", title: "play / pause" }, "▶");
      var stopBtn = BV.el("button", { class: "btn v3-pb", title: "back to the start" }, "■");
      var scrub = BV.el("input", { type: "range", class: "v3-scrub",
                                   min: "0", max: "1000", value: "0",
                                   title: "scrub through the program" });
      var clockEl = BV.el("span", { class: "v3-clock" }, "0.0 / 0.0 s");
      bar.appendChild(playBtn);
      bar.appendChild(stopBtn);
      bar.appendChild(scrub);
      bar.appendChild(clockEl);
      var speedSeg = BV.segmented(
        [{ id: "0.25", label: "¼×" }, { id: "1", label: "1×" }, { id: "4", label: "4×" }],
        { value: String(s.speed), onChange: function (id) { s.speed = parseFloat(id); } });
      bar.appendChild(speedSeg.el);
      vp.appendChild(bar);

      function schedule(fn) {
        /* the probe's hidden WebView2 has no requestAnimationFrame at all, and
           throttles timers to about a second. Both paths drive the playhead
           off Date.now(), never off a frame COUNT - bgfx.js paid for that one
           - so a throttled tick advances by real time instead of stalling. */
        return HAS_RAF ? window.requestAnimationFrame(fn) : window.setTimeout(fn, 33);
      }
      function unschedule(h) {
        if (h === null) return;
        if (HAS_RAF) window.cancelAnimationFrame(h); else window.clearTimeout(h);
      }

      function syncPlayer() {
        bar.hidden = !tl;
        if (!tl) return;
        scrub.value = String(Math.round(1000 * (s.t / (tl.total || 1))));
        clockEl.textContent = clock(Math.min(s.t, tl.total), tl.total);
        playBtn.textContent = raf === null ? "▶" : "❚❚";
      }

      function markStep(i) {
        /* move the highlight without rebuilding three hundred rows a frame -
           the selected step's evidence block refreshes when playback stops */
        if (i === s.step) return;
        s.step = i;
        var rows = side.querySelectorAll(".v3-step");
        for (var k = 0; k < rows.length; k++) rows[k].classList.toggle("sel", k === i);
      }

      function showAt(t) {
        if (!tl) return;
        var sg = segAt(tl, t);
        var st = path && path.steps[sg.step];
        drawArm(svg, data, s, robot, qAt(tl, t), st && st.tool);
        markStep(sg.step);
        syncPlayer();
      }

      function pause() {
        if (raf === null) return;
        unschedule(raf);
        raf = null;
        rebuildSide();      /* the evidence block catches up to where we are */
        redraw();
        syncPlayer();
      }

      function tick() {
        /* the router empties this slot on the next route, leaving us holding a
           detached svg. Every loop in this app self-terminates the same way
           (cvx3d.js, overview.js) rather than registering a teardown. */
        if (!document.contains(svg)) { raf = null; return; }
        var now = Date.now();
        /* clamp: one throttled tick in a hidden window must not silently
           finish the whole run */
        var dt = Math.min(now - last, 1000);
        last = now;
        s.t += dt * s.speed;
        if (s.t >= tl.total) {
          s.t = tl.total;
          showAt(s.t);
          pause();
          return;
        }
        showAt(s.t);
        raf = schedule(tick);
      }

      function play() {
        if (!tl || raf !== null) return;
        if (s.t >= tl.total) s.t = 0;
        last = Date.now();
        raf = schedule(tick);
        syncPlayer();
      }

      playBtn.addEventListener("click", function () {
        if (raf === null) play(); else pause();
      });
      stopBtn.addEventListener("click", function () {
        pause();
        s.t = 0;
        showAt(0);
        rebuildSide();
        redraw();
      });
      scrub.addEventListener("input", function () {
        if (!tl) return;
        pauseQuietly();
        s.t = tl.total * (parseFloat(scrub.value) / 1000);
        showAt(s.t);
      });
      scrub.addEventListener("change", function () { rebuildSide(); redraw(); });
      function pauseQuietly() {
        if (raf === null) return;
        unschedule(raf);
        raf = null;
        syncPlayer();
      }

      /* space and the arrows, scoped to the viewport's own focus. Not the
         global key map: keys.js's typing() guard does not exclude a focused
         button or checkbox, and this tab is full of both - a global space
         would fight the toolbar and scroll the page besides. */
      svg.setAttribute("tabindex", "0");
      svg.addEventListener("keydown", function (e) {
        if (!tl) return;
        if (e.key === " ") {
          e.preventDefault();
          if (raf === null) play(); else pause();
        } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          pauseQuietly();
          var i = Math.max(0, Math.min(path.steps.length - 1,
            s.step + (e.key === "ArrowRight" ? 1 : -1)));
          if (endOfStep(tl, i) !== null) pick(i);
        }
      });

      /* clicking a point in the viewport selects its step */
      ovl.addEventListener("click", function (e) {
        var t = e.target && e.target.closest ? e.target.closest("[data-step]") : null;
        if (t) pick(parseInt(t.getAttribute("data-step"), 10));
      });

      rebuildSide();
      redraw();
      syncPlayer();
      wireViewport(svg, s, redraw);

      /* #view3d/<FILE.LS> deep-links a program; with no fragment the tab
         restores whatever it was showing when you left it */
      var want = params && params[0] ? decodeURIComponent(params[0]) : s.prog;
      if (want) loadProgram(want);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">no DCS zone data</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "view3d", label: "3d view", render: render });
})();
