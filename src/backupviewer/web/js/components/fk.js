/* components/fk.js - BV.fk: FANUC forward kinematics over an imported
   .def chain. The JS twin of parsers/kinematics.py (the probe holds the
   two equal on real data); see that module's docstring for the chain
   derivation and how the convention was proven against controllers'
   own CURPOS reports.

   Matrices are row-major 4x4 nested arrays, angles in degrees, mm. */
(function () {
  "use strict";

  var D = Math.PI / 180;

  function identity() {
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  }

  function mul(a, b) {
    var out = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]];
    for (var i = 0; i < 4; i++) {
      for (var j = 0; j < 4; j++) {
        var v = 0;
        for (var k = 0; k < 4; k++) v += a[i][k] * b[k][j];
        out[i][j] = v;
      }
    }
    return out;
  }

  function invRigid(t) {
    var out = identity();
    for (var i = 0; i < 3; i++) {
      var p = 0;
      for (var j = 0; j < 3; j++) {
        out[i][j] = t[j][i];
        p -= t[j][i] * t[j][3];
      }
      out[i][3] = p;
    }
    return out;
  }

  function rx(a) {
    var c = Math.cos(a), s = Math.sin(a);
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]];
  }

  function ry(a) {
    var c = Math.cos(a), s = Math.sin(a);
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]];
  }

  function rz(a) {
    var c = Math.cos(a), s = Math.sin(a);
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  }

  /* FANUC placement: Trans(p) * Rz(r) * Ry(p) * Rx(w) */
  function frame(p, wpr) {
    var t = identity();
    t[0][3] = p[0]; t[1][3] = p[1]; t[2][3] = p[2];
    return mul(mul(mul(t, rz(wpr[2] * D)), ry(wpr[1] * D)), rx(wpr[0] * D));
  }

  /* world point through a pose matrix */
  function apply(m, p) {
    return [
      m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
      m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
      m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3],
    ];
  }

  /* pendant degrees -> per-joint theta (NegDirection + ParallelLink) */
  function thetas(kin, q) {
    return kin.joints.map(function (j, i) {
      var v = q[i] || 0;
      if (j.parallel) v += q[j.parallel - 1] || 0;
      return j.neg ? -v : v;
    });
  }

  /* -> {joints: [mat per joint], faceplate: mat} in FANUC WORLD coords */
  function chain(kin, q, flangeDz) {
    var acc = identity();
    var th = thetas(kin, q);
    var frames = [];
    kin.joints.forEach(function (j, i) {
      var t = frame(j.p, j.wpr);
      acc = mul(acc, mul(mul(t, rz(th[i] * D)), invRigid(t)));
      frames.push(mul(acc, t));
    });
    var fp = mul(acc, frame(kin.faceplate.p, kin.faceplate.wpr));
    if (flangeDz) {
      var dz = identity();
      dz[2][3] = flangeDz;
      fp = mul(fp, dz);
    }
    var zero = kin.zero || [0, 0, 0];
    function toWorld(m) {
      var w = m.map(function (row) { return row.slice(); });
      for (var k = 0; k < 3; k++) w[k][3] -= zero[k];
      return w;
    }
    return { joints: frames.map(toWorld), faceplate: toWorld(fp) };
  }

  window.BV = window.BV || {};
  BV.fk = { chain: chain, frame: frame, mul: mul, apply: apply };
})();
