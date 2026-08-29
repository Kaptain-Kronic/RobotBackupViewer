/* components/viewcube.js - BV.viewCube: the orientation cube + snap views.

   The little cube riding the top-right corner of a 3D viewport: it rotates
   with the camera - a compass, so you can SEE when you're under the floor -
   and clicking any of its 26 targets (6 faces, 12 edges, 8 corners) snaps
   the camera to look from that direction. Directions are named per the
   FANUC world frame (+X front, +Y left, +Z top).

   Extracted from tabs/view3d.js so the camera mesh screen (cvx3d.js) shows
   the same instrument - one cube for every 3D surface, robot or camera.
   Pixel-space furniture by design: a fixed 108px svg anchored to the
   top-right of `host` (any positioned element), so it never scales with
   zoom, content, or the text-size setting - same as the rest of the
   overlay layer. Styling lives on the shared .v3-cube-* classes
   (components.css), so the borders-off exemption there covers every
   caller at once. Purely rotational: projected with the camera basis
   only, never with perspective or world offsets.

   BV.viewCube(host, opts) -> {update, destroy, el}
     opts.basisOf()       -> the current {right, up, toViewer} camera basis
                             (i.e. BV.proj3d.orbitProjector(az, el).basis)
     opts.onSnap(az, el)     a snap target was clicked; az/el are the
                             turntable angles that look FROM that direction
   update() re-projects the cube from basisOf() - call it after every
   camera change (each redraw). Clicks are delegated from the cube's own
   svg, so callers never wire [data-az] handling themselves. */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var SIZE = 108, HALF = 54, CS = 21;  /* svg box px, its center, cube half-edge px */

  var dot3 = function (a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; };

  /* cube geometry: 6 labeled faces + 12 edge and 8 corner snap targets.
     Each target's az/el is the turntable angle that LOOKS from there.
     Top/bottom faces keep the canonical plan azimuths. */
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

  BV.viewCube = function (host, opts) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "viewcube");
    svg.setAttribute("viewBox", "0 0 " + SIZE + " " + SIZE);
    host.appendChild(svg);

    svg.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target.closest("[data-az]") : null;
      if (!t) return;
      opts.onSnap(parseFloat(t.getAttribute("data-az")),
                  parseFloat(t.getAttribute("data-el")));
    });

    function update() {
      var bs = opts.basisOf();
      function cpj(p) {
        return [dot3(bs.right, p), -dot3(bs.up, p), dot3(bs.toViewer, p)];
      }
      function cpx(p) {
        var q = cpj(p);
        return [HALF + q[0] * CS, HALF + q[1] * CS];
      }
      var out = [];
      CUBE_FACES.forEach(function (f) {
        if (cpj(f.n)[2] < 0.03) return; /* backface */
        var pts = f.corners.map(cpx).map(function (p) { return p[0] + "," + p[1]; });
        out.push('<polygon class="v3-cube-face" points="' + pts.join(" ") +
          '" data-az="' + f.az + '" data-el="' + f.el + '"><title>' + f.label + ' view</title></polygon>');
        var lc = cpx(f.n);
        out.push('<text class="v3-cube-lab" x="' + lc[0] + '" y="' + (lc[1] + 3) + '">' + f.label + "</text>");
      });
      CUBE_HITS.forEach(function (h) {
        if (cpj(h.d)[2] < 0.1) return;
        var p = cpx(h.at);
        out.push('<circle class="v3-cube-hit" cx="' + p[0] + '" cy="' + p[1] + '" r="' + h.r +
          '" data-az="' + h.az + '" data-el="' + h.el + '"/>');
      });
      svg.innerHTML = out.join("");
    }

    update();
    return {
      update: update,
      destroy: function () { if (svg.parentNode) svg.parentNode.removeChild(svg); },
      el: svg,
    };
  };
})();
