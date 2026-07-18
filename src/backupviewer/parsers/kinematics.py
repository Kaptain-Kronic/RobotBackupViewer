"""FANUC forward kinematics over a parsed .def chain (pure math).

Chain (proven against controllers' own CURPOS reports - 0.13 mm / 0.005
deg on the Roboguide testbed, <0.15 mm on plain plant robots, orientation
<=0.03 deg on every family tried):

    T_j   = Trans(p_j) * Rz(r) * Ry(p) * Rx(w)          (home placement)
    M_j   = T_j * Rz(theta_j) * T_j^-1                  (joint motion)
    link k in CAD   = M_1 * ... * M_k * T_k
    faceplate in CAD = M_1 * ... * M_n * T_fp * Trans(0,0,flange_dz)
    world = CAD - ZeroOffset (translation only)

    theta_j = +-q_j (pendant degrees; NegDirection flips), and a
    ParallelLink joint adds its master: theta_3 = s*(q3 + q2).

flange_dz is the measured per-robot flange correction: dress variants
("-IF") carry an adapter plate the plain library def does not include
(+23.0 mm on R-2000iC/210F-IF and R-1000iA/100F-IF, +10.06 mm on
M-900iB/280L-IF; 0 on plain robots). measure_flange() recovers it from a
backup's own CURPOS + taught tool: the full residual is reported and the
caller only trusts it when it is a pure flange-Z shift with tiny
orientation error - anything else means the kinematics do not match the
robot and the pose must not be drawn.

Matrices are row-major 4x4 nested lists; angles degrees; mm throughout.
The JS twin of chain_frames lives in web/js/components/fk.js - the probe
holds them equal.
"""
from __future__ import annotations

import math

Mat = list  # 4x4 nested list


def identity() -> Mat:
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def mul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def inv_rigid(t: Mat) -> Mat:
    """Inverse of a rotation+translation matrix (transpose trick)."""
    r = [[t[j][i] for j in range(3)] for i in range(3)]
    p = [t[i][3] for i in range(3)]
    ip = [-sum(r[i][j] * p[j] for j in range(3)) for i in range(3)]
    return [r[0] + [ip[0]], r[1] + [ip[1]], r[2] + [ip[2]], [0.0, 0, 0, 1.0]]


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return [[1.0, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1.0]]


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s, 0], [0, 1.0, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1.0]]


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def frame(p, wpr) -> Mat:
    """FANUC placement: Trans(p) * Rz(r) * Ry(p) * Rx(w)."""
    d = math.radians
    t = [[1.0, 0, 0, p[0]], [0, 1.0, 0, p[1]], [0, 0, 1.0, p[2]], [0, 0, 0, 1.0]]
    return mul(mul(mul(t, _rz(d(wpr[2]))), _ry(d(wpr[1]))), _rx(d(wpr[0])))


def wpr_of(t: Mat) -> list:
    """FANUC W,P,R (deg) back out of a rotation matrix."""
    p = math.asin(max(-1.0, min(1.0, -t[2][0])))
    if abs(math.cos(p)) < 1e-9:
        r = 0.0
        w = math.atan2(t[0][1], t[1][1])
    else:
        r = math.atan2(t[1][0], t[0][0])
        w = math.atan2(t[2][1], t[2][2])
    return [math.degrees(w), math.degrees(p), math.degrees(r)]


def _thetas(kin: dict, q_deg) -> list:
    out = []
    for i, j in enumerate(kin["joints"]):
        q = q_deg[i] if i < len(q_deg) else 0.0
        par = j.get("parallel")
        if par:
            q = q + (q_deg[par - 1] if par - 1 < len(q_deg) else 0.0)
        out.append(-q if j.get("neg") else q)
    return out


def chain_frames(kin: dict, q_deg, flange_dz: float = 0.0) -> dict:
    """Pose every joint frame + the faceplate, in FANUC WORLD coordinates.

    -> {"joints": [Mat per joint], "faceplate": Mat}
    """
    acc = identity()
    thetas = _thetas(kin, q_deg)
    frames = []
    for j, th in zip(kin["joints"], thetas):
        t = frame(j["p"], j["wpr"])
        acc = mul(acc, mul(mul(t, _rz(math.radians(th))), inv_rigid(t)))
        frames.append(mul(acc, t))
    fp = mul(acc, frame(kin["faceplate"]["p"], kin["faceplate"]["wpr"]))
    if flange_dz:
        fp = mul(fp, [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, flange_dz],
                      [0, 0, 0, 1.0]])
    zero = kin.get("zero") or [0.0] * 6

    def to_world(m: Mat) -> Mat:
        w = [row[:] for row in m]
        for k in range(3):
            w[k][3] -= zero[k]
        return w

    return {"joints": [to_world(m) for m in frames], "faceplate": to_world(fp)}


def measure_flange(kin: dict, q_deg, utool_xyzwpr, world_xyzwpr) -> dict:
    """Recover the flange correction from a backup's own position report.

    Given the pendant joints, the active taught tool and the controller's
    world TCP (all from the same backup), returns the residual expressed
    in the flange frame plus the orientation error:
      {"dz", "dxy", "ori_err", "ok"}
    ok = the residual is a pure flange-Z shift (|xy| < 1.5 mm) with tiny
    orientation error (< 0.1 deg) - the only case the caller may trust.
    """
    posed = chain_frames(kin, q_deg)
    fp = posed["faceplate"]
    tcp = mul(fp, frame(utool_xyzwpr[:3], utool_xyzwpr[3:]))
    dw = [world_xyzwpr[i] - tcp[i][3] for i in range(3)]
    rt = [[fp[j][i] for j in range(3)] for i in range(3)]
    d_fl = [sum(rt[i][j] * dw[j] for j in range(3)) for i in range(3)]
    got = wpr_of(tcp)
    ori = max(abs((a - b + 180.0) % 360.0 - 180.0)
              for a, b in zip(got, world_xyzwpr[3:]))
    dxy = math.hypot(d_fl[0], d_fl[1])
    return {
        "dz": d_fl[2], "dxy": dxy, "ori_err": ori,
        "ok": dxy < 1.5 and ori < 0.1,
    }
