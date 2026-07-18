/* components/proj3d.js - 3D->2D projection for the 3D View tab.

   Pure math, no DOM: FANUC world coordinates (mm, Z up, right-handed)
   onto SVG screen space (y grows DOWN). The camera is a free turntable
   (azimuth + elevation, unbounded); project() returns [sx, sy, depth]
   where depth ASCENDS toward the viewer - painter's order is "sort
   ascending, draw in order". Orthographic by default (distances stay
   measurable); an optional perspective wrap foreshortens about the
   scene center. */
(function () {
  "use strict";

  var D2R = Math.PI / 180;

  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

  /* turntable camera, closed form - defined for EVERY az/el, poles
     included and PAST them. Screen-up is the elevation tangent
     d(eye)/d(el) (always unit, always perpendicular to the eye), so
     pushing elevation beyond 90° carries the camera smoothly over the
     top and the world genuinely turns upside down on screen - no gimbal
     snap at the pole, no fixed world-up "mirror". right stays horizontal
     and never degenerates: |(-sin az, cos az, 0)| = 1 everywhere. */
  function turntable(azDeg, elDeg) {
    var az = azDeg * D2R, el = elDeg * D2R;
    var ca = Math.cos(az), sa = Math.sin(az);
    var ce = Math.cos(el), se = Math.sin(el);
    return {
      right: [-sa, ca, 0],
      up: [-se * ca, -se * sa, ce],
      toViewer: [ce * ca, ce * sa, se],
    };
  }

  function fromBasis(b, persp) {
    function ortho(p) { return [dot(b.right, p), -dot(b.up, p), dot(b.toViewer, p)]; }
    var project = ortho;
    if (persp) {
      /* mild depth foreshortening about the scene center: points AT the
         center's depth are unchanged, so fit/pan/pivot math done there is
         exact in both modes. Clamped so geometry never crosses the eye. */
      var c = ortho(persp.center), D = persp.dist;
      project = function (p) {
        var o = ortho(p);
        var k = D / Math.max(D - (o[2] - c[2]), D * 0.05);
        return [c[0] + (o[0] - c[0]) * k, c[1] + (o[1] - c[1]) * k, o[2]];
      };
    }
    return {
      basis: b,
      project: project,
      /* ortho inverse at an explicit depth (exact at the persp center depth) */
      unproject: function (sx, sy, d) {
        return [
          b.right[0] * sx - b.up[0] * sy + b.toViewer[0] * d,
          b.right[1] * sx - b.up[1] * sy + b.toViewer[1] * d,
          b.right[2] * sx - b.up[2] * sy + b.toViewer[2] * d,
        ];
      },
      depthOf: function (p) { return dot(b.toViewer, p); },
    };
  }

  /* free orbit: azimuth (deg about Z from +X) + elevation (deg above the
     floor), UNBOUNDED - spin as far as you like in any direction */
  function orbitProjector(azDeg, elDeg, persp) {
    return fromBasis(turntable(azDeg, elDeg), persp);
  }

  /* the named views are just turntable angles. top/bottom sit at the
     exact poles, PLAN-oriented for the FANUC world frame (right-handed,
     X forward / Y left / Z up): X points up-screen; top shows Y to
     screen-left, bottom (seen from underneath) mirrors it right. iso is
     deliberately OFF the 45° grid: plant fences love 0/45/90°
     orientations, and a wall parallel to the eye azimuth degenerates to
     a sliver (seen on a real -45° fence). */
  var PRESET_ANGLES = {
    iso: [-24.8, 36.8],
    top: [180, 90],
    bottom: [0, -90],
    front: [0, 0],
    back: [180, 0],
    left: [90, 0],
    right: [-90, 0],
  };

  function projector(viewId, persp) {
    var a = PRESET_ANGLES[viewId] || PRESET_ANGLES.iso;
    return orbitProjector(a[0], a[1], persp);
  }

  /* FANUC xyzwpr frame: R = Rz(r) * Ry(p) * Rx(w), world = R*local + t.
     null/undefined frame -> identity (zone numbers are already world). */
  function frameTransform(f) {
    if (!f) return function (p) { return [p[0], p[1], p[2]]; };
    var w = (f.w || 0) * D2R, p = (f.p || 0) * D2R, r = (f.r || 0) * D2R;
    var cw = Math.cos(w), sw = Math.sin(w);
    var cp = Math.cos(p), sp = Math.sin(p);
    var cr = Math.cos(r), sr = Math.sin(r);
    /* rows of Rz(r)*Ry(p)*Rx(w), composed on paper once */
    var m = [
      [cr * cp, cr * sp * sw - sr * cw, cr * sp * cw + sr * sw],
      [sr * cp, sr * sp * sw + cr * cw, sr * sp * cw - cr * sw],
      [-sp, cp * sw, cp * cw],
    ];
    var t = [f.x || 0, f.y || 0, f.z || 0];
    return function (pt) {
      return [
        m[0][0] * pt[0] + m[0][1] * pt[1] + m[0][2] * pt[2] + t[0],
        m[1][0] * pt[0] + m[1][1] * pt[1] + m[1][2] * pt[2] + t[1],
        m[2][0] * pt[0] + m[2][1] * pt[1] + m[2][2] * pt[2] + t[2],
      ];
    };
  }

  /* an XY polygon extruded z1..z2 -> vertices (bottom ring then top ring),
     quad/n-gon faces and the wireframe edge list, all index-based */
  function prism(poly, z1, z2) {
    var n = poly.length;
    var pts = [];
    var i;
    for (i = 0; i < n; i++) pts.push([poly[i][0], poly[i][1], z1]);
    for (i = 0; i < n; i++) pts.push([poly[i][0], poly[i][1], z2]);
    var faces = [];
    var bottom = [], top = [];
    for (i = 0; i < n; i++) { bottom.push(i); top.push(n + i); }
    faces.push(bottom);
    faces.push(top);
    for (i = 0; i < n; i++) faces.push([i, (i + 1) % n, n + (i + 1) % n, n + i]);
    var edges = [];
    for (i = 0; i < n; i++) {
      edges.push([i, (i + 1) % n]);
      edges.push([n + i, n + (i + 1) % n]);
      edges.push([i, n + i]);
    }
    return { pts: pts, faces: faces, edges: edges };
  }

  BV.proj3d = {
    PRESET_ANGLES: PRESET_ANGLES,
    projector: projector,
    orbitProjector: orbitProjector,
    frameTransform: frameTransform,
    prism: prism,
  };
})();
