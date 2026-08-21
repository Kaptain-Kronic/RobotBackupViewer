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
The JS twin of chain_frames lives in web/js/components/fk.js - the fk probe
(tests/ui_fk_probe.py) holds them equal to 1e-6 across builtin chains.
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


# -- the inverse solve -----------------------------------------------------
#
# Damped least squares over the SAME chain above, so the forward pass under
# every step of it is the pendant-proven one. Deliberately numerical rather
# than the closed-form OPW solution the vendor's own SDK dispatches to: OPW
# needs an ortho-parallel basis AND a spherical wrist, which only 121 of the
# 228 shipped chains have (167 are 6-joint, 152 of those have a spherical
# wrist - the 15 misses are exactly the CRX family - and 36 more fail only
# because a side-slung or undersling mount rotates the base). A fallback
# solver would be needed either way; this is that solver, doing the whole
# job. What OPW buys and this does not is the enumeration of all 8 branches,
# which is only useful once the taught CONFIG string has been pendant-paired
# and can say WHICH branch to take. See ROADMAP, 3D View follow-ups.
#
# The Jacobian is analytic, not finite-difference, and that is the whole
# performance story: one chain_frames call per iteration instead of seven.
# It falls out of the chain's own algebra. chain_frames builds
# frames[i] = acc_{i-1} * T_i * Rz(theta_i), and Rz about the local z changes
# neither the translation nor the third column - so for joint i the world
# screw axis is simply
#     omega_i = the third column of the POSED frame
#     p_i     = its translation
#     v_i     = omega_i x (p_tcp - p_i)
# and the neg/parallel coupling is linear in q, so
#     dTCP/dq_k = sum over i of (dtheta_i/dq_k) * screw_i
# with dtheta_i/dq_k = s_i for i == k, plus s_i again when joint i is a
# parallel link mastered by k. That coupling rule is _thetas' rule and is
# read off the same chain dict - never restated - because a second copy of
# it is what would mis-pose the forearm on 181 of the 228 types.

# 1 degree of orientation error weighs the same as 1 mm of position error.
L_ORI = 180.0 / math.pi

# tried in order, only when the one before it failed. The first entry is
# always the caller's own seed (the previous step's answer, or CURPOS).
# an attempt that has not improved its best cost in this many iterations has
# arrived wherever it is going - almost always the edge of the envelope.
STALL_ITERS = 8

IK_SEEDS = (
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, -90.0, 0.0],
    [0.0, 30.0, -30.0, 0.0, -60.0, 0.0],
    [45.0, -20.0, 20.0, 30.0, -45.0, 60.0],
    [-45.0, 20.0, -20.0, -30.0, 45.0, -60.0],
)


def _coupling(kin: dict) -> list:
    """(theta_index, driven_by_q_index, sign) - _thetas' rule, as triples."""
    out = []
    for i, j in enumerate(kin["joints"]):
        sign = -1.0 if j.get("neg") else 1.0
        out.append((i, i, sign))
        par = j.get("parallel")
        if par:
            out.append((i, par - 1, sign))
    return out


def joint_screws(frames: list, p_tcp) -> list:
    """(omega, v) per joint at p_tcp, in world. See the note above."""
    out = []
    for m in frames:
        w = [m[0][2], m[1][2], m[2][2]]
        d = [p_tcp[k] - m[k][3] for k in range(3)]
        v = [w[1] * d[2] - w[2] * d[1],
             w[2] * d[0] - w[0] * d[2],
             w[0] * d[1] - w[1] * d[0]]
        out.append((w, v))
    return out


def jacobian(kin: dict, frames: list, p_tcp) -> list:
    """6 x n. Rows 0-2 mm per radian, rows 3-5 orientation scaled by L_ORI so
    one column is comparable with another and the damping means one thing."""
    n = len(kin["joints"])
    screws = joint_screws(frames, p_tcp)
    j = [[0.0] * n for _ in range(6)]
    for i, k, sign in _coupling(kin):
        w, v = screws[i]
        for r in range(3):
            j[r][k] += sign * v[r]
            j[r + 3][k] += sign * w[r] * L_ORI
    return j


def _rot_error(target: Mat, actual: Mat):
    """Rotation vector taking actual's orientation to target's.

    -> (axis*angle in radians, angle in radians). The atan2 form with a
    near-pi fallback, not (E - E^T)/2 alone: a cold seed's first iteration
    routinely sits past 90 degrees, where the skew part alone shrinks toward
    zero again and the step turns the wrong way.
    """
    e = [[sum(target[i][k] * actual[j][k] for k in range(3)) for j in range(3)]
         for i in range(3)]
    vx = [e[2][1] - e[1][2], e[0][2] - e[2][0], e[1][0] - e[0][1]]
    sin_a = 0.5 * math.sqrt(sum(x * x for x in vx))
    cos_a = max(-1.0, min(1.0, (e[0][0] + e[1][1] + e[2][2] - 1.0) / 2.0))
    ang = math.atan2(sin_a, cos_a)
    if sin_a > 1e-9:
        f = ang / (2.0 * sin_a)
        return [f * x for x in vx], ang
    if cos_a > 0.0:
        return [0.5 * x for x in vx], ang          # tiny angle: the skew part is it
    # near pi: (E + I)/2 = a a^T, so the biggest diagonal names the best column
    m = [[e[i][j] + (1.0 if i == j else 0.0) for j in range(3)] for i in range(3)]
    k = max(range(3), key=lambda i: m[i][i])
    ax = [m[i][k] for i in range(3)]
    nrm = math.sqrt(sum(x * x for x in ax))
    if nrm < 1e-12:
        return [0.0, 0.0, 0.0], ang
    return [math.pi * x / nrm for x in ax], ang


def _rodrigues(v) -> Mat:
    """Rotation vector (radians) -> matrix. The inverse of _rot_error."""
    a = math.sqrt(sum(x * x for x in v))
    m = identity()
    if a < 1e-12:
        return m
    k = [x / a for x in v]
    c, s = math.cos(a), math.sin(a)
    for i in range(3):
        for j in range(3):
            m[i][j] = (c if i == j else 0.0) + (1 - c) * k[i] * k[j]
    m[0][1] -= s * k[2]; m[0][2] += s * k[1]
    m[1][0] += s * k[2]; m[1][2] -= s * k[0]
    m[2][0] -= s * k[1]; m[2][1] += s * k[0]
    return m


def lerp_pose(a: Mat, b: Mat, t: float) -> Mat:
    """The pose t of the way from a to b: straight line in position, shortest
    turn in orientation. This is what a LINEAR move does between two taught
    points, so the substeps solved along it land on the path the viewport
    draws instead of bowing away from it."""
    w, _ = _rot_error(b, a)
    out = mul(_rodrigues([t * x for x in w]), a)
    for i in range(3):
        out[i][3] = a[i][3] + t * (b[i][3] - a[i][3])
    return out


def pose_error(target: Mat, actual: Mat):
    """-> (6-vector in mm-equivalent, position mm, orientation degrees)."""
    dp = [target[i][3] - actual[i][3] for i in range(3)]
    dr, ang = _rot_error(target, actual)
    return (dp + [L_ORI * x for x in dr],
            math.sqrt(sum(x * x for x in dp)), math.degrees(ang))


def _solve6(a: list, b: list) -> list | None:
    """Gaussian elimination with partial pivoting on a small dense system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            return None
        m[c], m[piv] = m[piv], m[c]
        inv = 1.0 / m[c][c]
        for r in range(c + 1, n):
            f = m[r][c] * inv
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))
        x[r] = acc / m[r][r]
    return x


def _attempt(kin: dict, target: Mat, q0, flange_dz, maxit, tol_pos, tol_ori,
             step_cap):
    n = len(kin["joints"])
    q = [float(q0[i]) if i < len(q0) else 0.0 for i in range(n)]
    lam = 1.0
    best = (float("inf"), list(q), 0.0, 0.0, 0)
    stalled = 0
    for it in range(1, maxit + 1):
        fr = chain_frames(kin, q, flange_dz)
        fp = fr["faceplate"]
        b, pos_mm, ori_deg = pose_error(target, fp)
        cost = math.sqrt(sum(x * x for x in b))
        # an unreachable target pins the arm against its envelope and the cost
        # simply stops moving. Without this the run-out costs the full
        # iteration budget every time, on every seed, for an answer that was
        # settled in a dozen steps
        stalled = 0 if cost < best[0] * (1.0 - 1e-6) else stalled + 1
        if cost < best[0]:
            best = (cost, list(q), pos_mm, ori_deg, it)
        if stalled >= STALL_ITERS:
            break
        if pos_mm < tol_pos and ori_deg < tol_ori:
            return {"q": q, "ok": True, "pos_mm": pos_mm, "ori_deg": ori_deg,
                    "iters": it}
        j = jacobian(kin, fr["joints"], [fp[k][3] for k in range(3)])
        # (J J^T + lam^2 I) y = b, then dq = J^T y - always a 6x6 solve, so a
        # 4-joint palletizer, a 6-axis arm and the 7-axis type share one path:
        # too few joints leaves residual the gate then refuses, too many gives
        # the minimum-norm step, which stays near the seed for free continuity
        jjt = [[sum(j[r][k] * j[c][k] for k in range(n)) + (lam * lam if r == c else 0.0)
                for c in range(6)] for r in range(6)]
        y = _solve6(jjt, b)
        if y is None:
            lam *= 4.0
            continue
        dq = [sum(j[r][k] * y[r] for r in range(6)) for k in range(n)]
        big = max((abs(x) for x in dq), default=0.0)
        cap = math.radians(step_cap)
        if big > cap:
            dq = [x * cap / big for x in dq]
        q = [q[k] + math.degrees(dq[k]) for k in range(n)]
        lam = max(0.05, lam * 0.7)
    return {"q": best[1], "ok": False, "pos_mm": best[2], "ori_deg": best[3],
            "iters": best[4]}


def solve_ik(kin: dict, target: Mat, q0=None, flange_dz: float = 0.0,
             seeds=None, tol_pos: float = 0.05, tol_ori: float = 0.005,
             gate_pos: float = 0.5, gate_ori: float = 0.05,
             maxit: int = 60, step_cap: float = 20.0) -> dict:
    """Joint angles putting the FACEPLATE at target (a 4x4 world pose).

    -> {"q", "ok", "pos_mm", "ori_deg", "iters", "seed"}

    target is the faceplate, never the tcp: compose it with
    program_path.flange_target, which inverts the taught tool the same way
    measure_flange composes it.

    ok is the HONESTY gate, not convergence: the returned q is accepted only
    when running it back through the forward chain reproduces target inside
    gate_pos / gate_ori. On failure the best iterate seen is returned, so the
    residual reported is the residual of the q handed back and a caller can
    print it. What no gate can catch is a solution on a different BRANCH than
    the robot took - same tcp, mirrored elbow, residual ~0 - which is why a
    caller warm-starts from the previous step and labels the result.
    """
    tried = [list(q0)] if q0 else []
    tried += [list(x) for x in (IK_SEEDS if seeds is None else seeds)]
    best = None
    for i, seed in enumerate(tried):
        r = _attempt(kin, target, seed, flange_dz, maxit, tol_pos, tol_ori,
                     step_cap)
        r["seed"] = i
        r["ok"] = r["pos_mm"] < gate_pos and r["ori_deg"] < gate_ori
        if r["ok"]:
            return r
        prev = best["pos_mm"] if best else None
        if best is None or r["pos_mm"] < best["pos_mm"]:
            best = r
        # The extra seeds exist to find a different BRANCH - a mirrored elbow
        # reaching the same tcp. A branch change cannot make an out-of-reach
        # point reachable, and an out-of-reach point pins every seed against
        # the same envelope. So two seeds agreeing to within 1% means the
        # answer is "no", and the rest of the ladder is only a way to spend
        # three quarters of a minute saying it on a long program.
        if prev is not None and abs(r["pos_mm"] - prev) <= 0.01 * max(prev, 1e-9):
            break
    return best or {"q": [], "ok": False, "pos_mm": float("inf"),
                    "ori_deg": float("inf"), "iters": 0, "seed": -1}
