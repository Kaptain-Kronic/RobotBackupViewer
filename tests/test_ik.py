"""The inverse solve, against the pendant-proven forward chain.

Every assertion here is grounded on chain_frames: the forward pass is the
thing that was brute-forced against a real controller's own position report,
so "the solver is right" means "running its answer forward reproduces the
pose". Nothing in this file trusts the solver to check itself.

Fully synthetic and identifier-clean - the chains are the shipped table.
"""
import math
import random

import pytest

from backupviewer.kinematics_builtin import BUILTIN
from backupviewer.parsers import kinematics as K


def shapes() -> dict:
    """One representative per DISTINCT chain shape (joint count + the neg and
    parallel-link tuples). Deduped rather than a frozen list, so the sweep
    keeps covering the whole table as the table grows."""
    out = {}
    for key, e in BUILTIN.items():
        kin = e["kin"]
        sig = (len(kin["joints"]),
               tuple(j.get("parallel") for j in kin["joints"]),
               tuple(bool(j.get("neg")) for j in kin["joints"]))
        out.setdefault(sig, key)
    return out


SHAPES = sorted(shapes().values())
ARM = BUILTIN["ARCMATE120ID"]["kin"]          # a validated 6-axis family


def fk(kin, q, dz=0.0):
    return K.chain_frames(kin, q, dz)["faceplate"]


def rotated(m, w, p, r):
    """m turned by a FANUC w/p/r about its own origin."""
    out = K.mul(m, K.frame([0, 0, 0], [w, p, r]))
    for i in range(3):
        out[i][3] = m[i][3]
    return out


# ---- the coupling rule ----------------------------------------------------

@pytest.mark.parametrize("key", SHAPES)
def test_coupling_is_the_same_rule_thetas_uses(key):
    """The Jacobian's neg/parallel handling is _thetas' handling, read off the
    same chain dict. A second copy of that rule drifting is what would
    mis-pose the forearm on 181 of the 228 shipped types."""
    kin = BUILTIN[key]["kin"]
    n = len(kin["joints"])
    rnd = random.Random(11)
    q = [rnd.uniform(-70, 70) for _ in range(n)]
    want = K._thetas(kin, q)
    got = [0.0] * n
    for i, k, sign in K._coupling(kin):
        got[i] += sign * q[k]
    assert got == pytest.approx(want, abs=1e-12)


# ---- the Jacobian ---------------------------------------------------------

def fd_column(kin, q, k, dz, h=1e-6):
    """One finite-difference column, in the same mm-equivalent units."""
    a, b = list(q), list(q)
    a[k] -= math.degrees(h) / 2
    b[k] += math.degrees(h) / 2
    fa, fb = fk(kin, a, dz), fk(kin, b, dz)
    dp = [(fb[i][3] - fa[i][3]) / h for i in range(3)]
    e, _, _ = K.pose_error(fb, fa)
    return dp + [x / h for x in e[3:]]


@pytest.mark.parametrize("key", SHAPES)
def test_analytic_jacobian_matches_finite_difference(key):
    """The performance claim rests on this: one forward pass per iteration
    instead of seven. It is only allowed to be fast if it is also right."""
    kin = BUILTIN[key]["kin"]
    n = len(kin["joints"])
    rnd = random.Random(3)
    for _ in range(4):
        q = [rnd.uniform(-60, 60) for _ in range(n)]
        fr = K.chain_frames(kin, q, 23.0)
        fp = fr["faceplate"]
        j = K.jacobian(kin, fr["joints"], [fp[i][3] for i in range(3)])
        for k in range(n):
            got = [j[r][k] for r in range(6)]
            want = fd_column(kin, q, k, 23.0)
            scale = max(1.0, max(abs(x) for x in want))
            assert max(abs(g - w) for g, w in zip(got, want)) / scale < 1e-5


# ---- rotation error -------------------------------------------------------

@pytest.mark.parametrize("deg", [0.0, 0.5, 30.0, -95.0, 120.0, 179.5, 180.0])
def test_rotation_error_measures_the_whole_turn(deg):
    """Turn a pose by a known angle about one axis; the reported orientation
    error must BE that angle. A cold seed's first iteration routinely sits
    past 90 degrees, where the skew part alone shrinks back toward zero and
    would send the step the wrong way - hence atan2 plus a near-pi fallback,
    and hence this check reaching all the way to 180."""
    base = fk(ARM, [0.0] * 6)
    turned = rotated(base, 0.0, 0.0, deg)
    _, pos_mm, ori_deg = K.pose_error(turned, base)
    assert pos_mm == pytest.approx(0.0, abs=1e-9)
    assert ori_deg == pytest.approx(abs(deg), abs=1e-6)


# ---- the round trip -------------------------------------------------------

@pytest.mark.parametrize("key", SHAPES)
def test_warm_start_round_trip_reproduces_the_pose(key):
    """Forward -> inverse -> forward. The gate says ok only when it does."""
    kin = BUILTIN[key]["kin"]
    n = len(kin["joints"])
    rnd = random.Random(5)
    solved = 0
    for _ in range(4):
        q = [rnd.uniform(-50, 50) for _ in range(n)]
        target = fk(kin, q)
        seed = [v + rnd.uniform(-25, 25) for v in q]
        r = K.solve_ik(kin, target, seed)
        if not r["ok"]:
            continue        # short/redundant chains may honestly refuse
        solved += 1
        _, pos_mm, ori_deg = K.pose_error(target, fk(kin, r["q"]))
        assert pos_mm < 0.5 and ori_deg < 0.05, (key, pos_mm, ori_deg)
    assert solved >= 1, f"{key} solved nothing at all from a warm seed"


def test_warm_start_recovers_the_branch_not_just_the_pose():
    """The one failure no runtime check can catch is a solution on a DIFFERENT
    branch: same tcp, mirrored elbow, residual ~0. Warm-starting is what keeps
    the branch continuous, so this asserts the joints themselves come back."""
    rnd = random.Random(9)
    for _ in range(12):
        q = [rnd.uniform(-45, 45) for _ in range(6)]
        r = K.solve_ik(ARM, fk(ARM, q), [v + rnd.uniform(-15, 15) for v in q])
        assert r["ok"]
        assert r["q"] == pytest.approx(q, abs=0.5)


def test_the_flange_correction_is_part_of_the_target():
    """Solving with a flange plate must not quietly solve for the bare
    faceplate - a silent 23 mm on every -IF robot."""
    q = [10.0, -20.0, 15.0, 5.0, -35.0, 40.0]
    target = fk(ARM, q, 23.0)
    r = K.solve_ik(ARM, target, [0.0] * 6, flange_dz=23.0)
    assert r["ok"]
    _, pos_mm, _ = K.pose_error(target, fk(ARM, r["q"], 23.0))
    assert pos_mm < 0.5
    # the same answer against the BARE faceplate is off by the plate
    _, wrong_mm, _ = K.pose_error(target, fk(ARM, r["q"], 0.0))
    assert wrong_mm == pytest.approx(23.0, abs=0.6)


# ---- refusing ------------------------------------------------------------

def test_unreachable_target_refuses_with_an_honest_residual():
    target = fk(ARM, [0.0] * 6)
    far = [row[:] for row in target]
    far[0][3] += 8.0 * 1055.0        # well past this arm's reach
    r = K.solve_ik(ARM, far, [0.0] * 6)
    assert r["ok"] is False
    # the residual reported is the residual of the q handed back
    _, pos_mm, ori_deg = K.pose_error(far, fk(ARM, r["q"]))
    assert pos_mm == pytest.approx(r["pos_mm"], rel=1e-6)
    assert ori_deg == pytest.approx(r["ori_deg"], abs=1e-6)


def test_a_short_chain_refuses_what_it_cannot_reach_rather_than_approximating():
    """A 4-joint palletizer keeps its wrist level by linkage. Asked for an
    orientation it physically has no axis for, it must refuse - not clamp to
    something plausible, and not raise."""
    key = next(k for k in SHAPES if len(BUILTIN[k]["kin"]["joints"]) == 4)
    kin = BUILTIN[key]["kin"]
    q = [10.0, -20.0, 15.0, 5.0]
    target = rotated(fk(kin, q), 0.0, 55.0, 0.0)
    r = K.solve_ik(kin, target, q)
    assert r["ok"] is False
    assert r["ori_deg"] > 1.0
    assert len(r["q"]) == 4


def test_failure_returns_the_best_iterate_seen():
    """Not the last one - a diverging last step would report a residual that
    has nothing to do with the joints it hands back."""
    target = fk(ARM, [0.0] * 6)
    far = [row[:] for row in target]
    far[2][3] += 4000.0
    r = K.solve_ik(ARM, far, [0.0] * 6, maxit=8)
    assert r["ok"] is False
    _, pos_mm, _ = K.pose_error(far, fk(ARM, r["q"]))
    assert pos_mm == pytest.approx(r["pos_mm"], rel=1e-6)


# ---- the seed ladder ------------------------------------------------------

def test_the_seed_ladder_beats_a_single_cold_seed():
    """Home is singular on several chains, so a single cold seed genuinely
    fails sometimes. Asserted as an inequality, not a frozen count."""
    rnd = random.Random(21)
    targets = []
    for key in SHAPES:
        kin = BUILTIN[key]["kin"]
        n = len(kin["joints"])
        for _ in range(6):
            q = [rnd.uniform(-60, 60) for _ in range(n)]
            targets.append((kin, fk(kin, q)))
    one = sum(1 for kin, t in targets
              if K.solve_ik(kin, t, None, seeds=[[0.0] * 9])["ok"])
    many = sum(1 for kin, t in targets if K.solve_ik(kin, t, None)["ok"])
    assert many >= one
    assert many > 0
