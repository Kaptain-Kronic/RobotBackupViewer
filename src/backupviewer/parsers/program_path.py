"""Where a program's taught points actually are, in world millimetres.

Pure: parsed dicts in, one drawable payload out. The inputs are what other
parsers already produce - `ls_program.parse_ls_program`, `ls_motion.
parse_motions`, `frames.build_frames_model`, `registers.parse_posreg` - plus
an optional kinematic chain from `modeldb.match`.

A taught cartesian point is a pose **in its user frame**, measured to the
**tool**, so neither number means anything on its own:

    T_tcp    = frame(uframe) * frame(point)
    T_flange = T_tcp * inv_rigid(frame(utool))

That second line is the inverse of the relation `kinematics.measure_flange`
already proves against a controller's own position report
(`tcp = mul(fp, frame(utool))`, kinematics.py) - it is reused rather than
re-derived, because getting its sense backwards puts every point off by the
tool length in a rotated direction and still looks completely plausible.

A joint-rep point needs no frame composition at all: forward kinematics puts
the faceplate where the controller would, and the tool rides on the end. That
path is exact - it is the same pendant-proven chain the posed arm runs on.

Everything that CANNOT be resolved is reported, never dropped: `why` names the
reason and `note` is the sentence to show. A backup that cannot place half a
program should say which half and why, not quietly draw the other half.
"""
from __future__ import annotations

import math
import re

from .kinematics import (chain_frames, frame, inv_rigid, lerp_pose, mul,
                         pose_error, solve_ik, wpr_of)
from .ls_motion import ASSUMED_JOINT_DEG_S, step_duration_ms
from .ls_program import mn_stream

# a body line whose statement ASSIGNS a position register: PR[7]=... . Such a
# register's value at run time is written by the running program, so whatever
# POSREG.VA captured is a snapshot of one moment, not the taught destination.
_PR_WRITE = re.compile(r"^PR\[(\d+)\s*(?:[,:][^\]]*)?\]\s*=", re.M)

NOTES = {
    "no-pos-entry": "P[{id}] has no /POS entry — nothing in this program records that position",
    "anonymous": "this motion carries no position id (P[...])",
    "indirect": "{raw} is indirect — a listing cannot say which position that is",
    "uninit-pr": "PR[{id}] is listed uninitialized in POSREG.VA",
    "no-pr": "PR[{id}] is not in this backup's POSREG.VA",
    "pr-written-at-runtime": ("PR[{id}] is written by a program in this backup — its value "
                              "at run time is not something the backup records"),
    "masked": "******** masked — the value was not exported",
    "wrong-group": "group {group} has no entry for this position",
    "no-frames-file": "no SYSFRAME.VA in this backup — user and tool frames are unknown",
    "uframe-missing": "uframe {n} is not in this backup's SYSFRAME.VA",
    "utool-missing": "utool {n} is not in this backup's SYSFRAME.VA",
    "uframe-uninit": "uframe {n} is listed uninitialized in SYSFRAME.VA",
    "utool-uninit": "utool {n} is listed uninitialized in SYSFRAME.VA",
    "uframe-masked": "uframe {n} has masked values — they were not exported",
    "utool-masked": "utool {n} has masked values — they were not exported",
    "uframe-unnamed": ("uf: {raw_uf} names no frame this backup can resolve "
                       "(“F” means whatever was current, and no active user frame "
                       "is recorded)"),
    "utool-unnamed": ("ut: {raw_ut} names no tool this backup can resolve "
                      "(“F” means whatever was current, and no active tool frame "
                      "is recorded)"),
    "no-kinematics": "a joint-recorded point needs the robot's kinematics to place",
    "no-rep": "this /POS entry records neither cartesian nor joint values",
}

ASSUMPTIONS = {
    "uframe-current": ("uf: F means “use the frame that is current” — resolved to {n}, "
                       "the one active when this backup was written"),
    "utool-current": ("ut: F means “use the tool that is current” — resolved to {n}, "
                      "the one active when this backup was written"),
    "circular-via": ("circular moves are drawn as a 3-point arc, reading the first point as "
                     "the via — not pendant-verified"),
    "arc-chord": "consecutive arc (A) points are drawn as straight chords, not as the blended arc",
    "offset": ("{n} move(s) carry a runtime offset (Offset / Tool_Offset) — drawn at the "
               "taught point, which is not where the robot went"),
    "incremental": ("{n} move(s) are incremental (INC) — the destination depends on where the "
                    "robot already was, so the taught numbers are a delta, not a place"),
}


def pr_writers(texts) -> set:
    """PR indices a program in this backup assigns to. texts: {NAME: source}.

    Only lines the robot would actually run count: a remarked (`//`) or
    commented (`!`) assignment never executes, so the register it names is
    still exactly what POSREG.VA recorded. mn_stream already decides that.
    """
    out: set = set()
    for src in texts.values():
        for row in mn_stream(src):
            if not row["active"]:
                continue
            m = _PR_WRITE.match(row["text"])
            if m:
                out.add(int(m.group(1)))
    return out


def _xyzwpr(d) -> list | None:
    """[x,y,z,w,p,r] from a cartesian entry, or None if any axis is masked."""
    vals = [d.get(k) for k in ("x", "y", "z", "w", "p", "r")]
    return None if any(v is None for v in vals) else [float(v) for v in vals]


def _frame_entry(model, kind, group, num):
    """-> (xyzwpr | None, why | None). num 0 is the world/faceplate identity -
    it needs no table, so a backup with no SYSFRAME.VA still places those."""
    if num == 0:
        return [0.0] * 6, None
    if model is None:
        return None, "no-frames-file"
    table = model.get("frames" if kind == "uframe" else "tools", {})
    for e in table.get(str(group), []):
        if e.get("index") != num:
            continue
        if e.get("uninit"):
            return None, kind + "-uninit"
        vals = _xyzwpr(e)
        return (vals, None) if vals else (None, kind + "-masked")
    return None, kind + "-missing"


def _resolve_num(raw, model, key, group):
    """A /POS uf|ut STRING -> (number, used_current). "F" = whatever was
    current; the backup's own active number is the only honest reading, and it
    is an assumption because "current" is a run-time thing."""
    raw = (raw or "0").strip()
    if raw.upper() == "F":
        active = (model or {}).get(key, {}).get(str(group))
        return (int(active) if active is not None else None), True
    try:
        return int(raw), False
    except ValueError:
        return None, False


def _dist(a, b):
    return math.dist(a[:3], b[:3]) if a and b else None


def build_path(prog, motions, frames_model=None, posreg=None, pr_written=(),
               group: int = 1, kin=None, flange_dz: float = 0.0,
               start_world=None) -> dict:
    """Resolve every motion's destination to a world TCP pose.

    -> {"group", "steps": [...], "counts", "assumptions", "bounds", "frames"}

    Each step keeps everything the panel needs to show its evidence - the
    verbatim instruction text, the raw CONFIG string, the taught numbers, the
    uf/ut STRINGS as written - and, when it could be placed, "world": the TCP
    pose [x,y,z,w,p,r] in FANUC world millimetres.
    """
    pos_by_id = {p["id"]: p for p in prog.get("positions", [])}
    pr_by_id = {e["index"]: e for e in (posreg or [])
                if e.get("group", 1) == group}
    assume: dict = {}
    steps = []
    prev = list(start_world) if start_world else None

    for m in motions:
        st = {
            "i": m["i"], "line": m["line"], "text": m["text"],
            "motion": m["motion"], "motion_name": m["motion_name"],
            "target": m["target"], "via": m["via"],
            "speed": m["speed"], "term": m["term"],
            "options": m["options"], "offset": m["offset"],
            "tool_offset": m["tool_offset"], "incremental": m["incremental"],
            "rep": None, "uf": None, "ut": None, "config": "", "tool": None,
            "xyzwpr": None, "joints": None, "world": None,
            "dist_mm": None, "dur_ms": None, "dur_kind": "unknown",
            "ok": False, "why": None, "note": None,
        }
        _place(st, m["target"], pos_by_id, pr_by_id, pr_written, frames_model,
               group, kin, flange_dz, assume)
        if st["ok"]:
            st["dist_mm"] = _dist(prev, st["world"])
            st["dur_ms"], st["dur_kind"] = step_duration_ms(m, dist_mm=st["dist_mm"])
            prev = st["world"]
        if st["why"]:
            st["note"] = NOTES[st["why"]].format(
                id=(m["target"].get("id")), raw=m["target"].get("raw", ""),
                group=group, n=st.get("_num"),
                raw_uf=st.get("uf"), raw_ut=st.get("ut"))
        st.pop("_num", None)
        if m["offset"] or m["tool_offset"]:
            assume["offset"] = assume.get("offset", 0) + 1
        if m["incremental"]:
            assume["incremental"] = assume.get("incremental", 0) + 1
        if m["motion"] == "C":
            assume.setdefault("circular-via", 0)
            assume["circular-via"] += 1
        if m["motion"] == "A":
            assume.setdefault("arc-chord", 0)
            assume["arc-chord"] += 1
        steps.append(st)

    placed = [s for s in steps if s["ok"]]
    bounds = None
    if placed:
        pts = [s["world"] for s in placed]
        bounds = {
            "min": [min(p[k] for p in pts) for k in range(3)],
            "max": [max(p[k] for p in pts) for k in range(3)],
        }
    return {
        "group": group,
        "steps": steps,
        "counts": {"steps": len(steps), "placed": len(placed),
                   "refused": len(steps) - len(placed),
                   "joint": sum(1 for s in placed if s["rep"] == "joint"),
                   "cartesian": sum(1 for s in placed if s["rep"] == "cartesian")},
        # n is a COUNT for the per-move assumptions (offset, incremental,
        # circular-via, arc-chord) and the resolved frame NUMBER for the two
        # "current" ones - each message reads its own n
        "assumptions": [{"id": k, "n": v, "text": ASSUMPTIONS[k].format(n=v)}
                        for k, v in sorted(assume.items())],
        "bounds": bounds,
        "frames": {
            "source": "SYSFRAME.VA" if frames_model is not None else None,
            "active_uframe": (frames_model or {}).get("active_frame", {}).get(str(group)),
            "active_utool": (frames_model or {}).get("active_tool", {}).get(str(group)),
        },
    }


def _place(st, target, pos_by_id, pr_by_id, pr_written, frames_model,
           group, kin, flange_dz, assume) -> None:
    """Fill one step's taught values and world pose, or its refusal reason."""
    kind = target["kind"]
    if kind == "anon":
        st["why"] = "anonymous"
        return
    if kind == "indirect":
        st["why"] = "indirect"
        return

    if kind == "PR":
        entry = pr_by_id.get(target["id"])
        if entry is None:
            st["why"] = "no-pr"
            return
        if entry.get("kind") == "uninit":
            st["why"] = "uninit-pr"
            return
        if target["id"] in pr_written:
            st["why"] = "pr-written-at-runtime"
            return
        g = entry
    else:
        pos = pos_by_id.get(target["id"])
        if pos is None:
            st["why"] = "no-pos-entry"
            return
        g = next((x for x in pos["groups"] if x.get("gp") == group), None)
        if g is None:
            st["why"] = "wrong-group"
            return

    st["config"] = g.get("config", "") or ""
    st["uf"] = str(g.get("uf", "0"))
    st["ut"] = str(g.get("ut", "0"))
    rep = g.get("kind")
    if rep not in ("cartesian", "joint"):
        st["why"] = "no-rep"
        return
    st["rep"] = rep

    # a nonzero uf/ut is the only thing that needs SYSFRAME.VA; UF:0 (world)
    # and UT:0 (the faceplate) resolve to identity with no table at all
    ut_num, ut_cur = _resolve_num(st["ut"], frames_model, "active_tool", group)
    if ut_num is None:
        st["why"] = "utool-unnamed"
        return
    tool, why = _frame_entry(frames_model, "utool", group, ut_num)
    if why:
        st["why"] = why
        st["_num"] = ut_num
        return
    st["tool"] = tool
    if ut_cur:
        assume["utool-current"] = ut_num

    if rep == "joint":
        joints = [j for j in (g.get("joints") or [])]
        if not joints or any(j is None for j in joints):
            st["why"] = "masked" if joints else "no-rep"
            return
        st["joints"] = joints
        if kin is None:
            st["why"] = "no-kinematics"
            return
        fp = chain_frames(kin, joints, flange_dz)["faceplate"]
        tcp = mul(fp, frame(tool[:3], tool[3:]))
        st["world"] = [tcp[0][3], tcp[1][3], tcp[2][3]] + wpr_of(tcp)
        st["ok"] = True
        return

    vals = _xyzwpr(g)
    if vals is None:
        st["why"] = "masked"
        return
    st["xyzwpr"] = vals
    uf_num, uf_cur = _resolve_num(st["uf"], frames_model, "active_frame", group)
    if uf_num is None:
        st["why"] = "uframe-unnamed"
        return
    uframe, why = _frame_entry(frames_model, "uframe", group, uf_num)
    if why:
        st["why"] = why
        st["_num"] = uf_num
        return
    if uf_cur:
        assume["uframe-current"] = uf_num
    tcp = mul(frame(uframe[:3], uframe[3:]), frame(vals[:3], vals[3:]))
    st["world"] = [tcp[0][3], tcp[1][3], tcp[2][3]] + wpr_of(tcp)
    st["ok"] = True


def flange_target(world_xyzwpr, utool_xyzwpr) -> list:
    """The FACEPLATE pose that puts the tool's tcp at world_xyzwpr.

    T_flange = T_tcp * inv_rigid(T_tool) - the inverse of the composition
    kinematics.measure_flange proves against a controller's own report. The
    inverse solver targets this, never the tcp.
    """
    tcp = frame(world_xyzwpr[:3], world_xyzwpr[3:])
    return mul(tcp, inv_rigid(frame(utool_xyzwpr[:3], utool_xyzwpr[3:])))

# -- posing the arm along the path -----------------------------------------

POSE_NOTES = {
    "no-target": "this move has no placed position to pose at",
    "no-chain": "no kinematics for this robot type — the arm cannot be posed",
    "no-converge": ("the solver did not converge on this point in {iters} "
                    "iterations — not posed"),
    "out-of-tolerance": ("the solver landed {pos:.1f} mm / {ori:.2f}° off the taught "
                         "point — not posed"),
}

# substeps along a LINEAR or CIRCULAR move, so the arm follows the drawn line
# instead of bowing off it. A joint move needs none: joint-space interpolation
# IS what a J move does, so its two ends are the whole truth.
SUBSTEP_MM = 25.0
SUBSTEP_DEG = 5.0
MAX_SUBSTEPS = 40
MAX_KNOTS = 4000        # whole-program budget; over it, density scales down


def _substeps(dist_mm, ori_deg, scale=1.0):
    n = math.ceil((dist_mm or 0.0) / SUBSTEP_MM) + math.ceil((ori_deg or 0.0) / SUBSTEP_DEG)
    return max(1, min(MAX_SUBSTEPS, int(math.ceil(n * scale))))


def _pose_mat(world):
    return frame(world[:3], world[3:])


def build_pose(path, kin=None, flange_dz: float = 0.0, q_seed=None) -> dict:
    """Joint angles for every placed step, and the knots between them.

    -> {"steps": [{i, solved, source, q, knots, residual, why, note}], ...}

    knots are the interpolation waypoints from the PREVIOUS pose up to and
    including this one, so playback is one uniform rule - lerp in joint space
    between consecutive knots - which a joint move satisfies exactly and a
    linear move satisfies to the knot density.

    A joint-recorded point is posed by its own taught angles: no solver, no
    branch to choose, exact. A cartesian point is solved, and the answer is
    only accepted when running it back through the forward chain reproduces
    the taught pose. What that check CANNOT catch is a solution on a different
    branch than the robot took - same tcp, mirrored elbow - so the solve warm
    starts from the previous step to keep the branch continuous, and the
    caller labels every solved pose as solved.
    """
    steps = []
    if kin is None:
        for st in path["steps"]:
            steps.append({"i": st["i"], "solved": False, "source": None, "q": None,
                          "knots": [], "residual": None, "why": "no-chain",
                          "note": POSE_NOTES["no-chain"]})
        return {"steps": steps, "counts": {"posed": 0, "refused": len(steps),
                                           "exact": 0, "solved": 0},
                "gate": None, "budget": {"knots": 0, "scaled": False},
                "posable": False}

    # one cheap pass to price the whole run, so a very long program thins its
    # substeps rather than silently truncating
    want = 0
    prev = None
    for st in path["steps"]:
        if not st["ok"]:
            continue
        if st["rep"] == "joint" or prev is None:
            want += 1
        else:
            _, pm, od = pose_error(_pose_mat(st["world"]), _pose_mat(prev))
            want += _substeps(pm, od)
        prev = st["world"]
    scale = min(1.0, MAX_KNOTS / want) if want > MAX_KNOTS else 1.0

    q = list(q_seed) if q_seed else None
    prev_q = list(q_seed) if q_seed else None
    prev_world = None
    knots_total = 0
    for st in path["steps"]:
        row = {"i": st["i"], "solved": False, "source": None, "q": None,
               "knots": [], "residual": None, "why": None, "note": None,
               "dur_ms": st["dur_ms"], "dur_kind": st["dur_kind"],
               "travel_deg": None}
        steps.append(row)
        if not st["ok"]:
            row["why"] = "no-target"
            row["note"] = POSE_NOTES["no-target"]
            continue

        if st["rep"] == "joint":
            row.update(solved=True, source="joint", q=[round(v, 4) for v in st["joints"]],
                       knots=[[round(v, 4) for v in st["joints"]]])
            q = list(st["joints"])
            _price(row, st, prev_q, q)
            prev_q = list(q)
            prev_world = st["world"]
            knots_total += 1
            continue

        target = flange_target(st["world"], st["tool"] or [0.0] * 6)
        # a linear or circular move walks the drawn line; a joint move (or the
        # first placed move, with nowhere to come from) is a single hop
        n = 1
        if prev_world is not None and st["motion"] in ("L", "C"):
            _, pm, od = pose_error(_pose_mat(st["world"]), _pose_mat(prev_world))
            n = _substeps(pm, od, scale)
        a = _pose_mat(prev_world) if prev_world is not None else None
        b = _pose_mat(st["world"])
        knots, last = [], None
        failed = None
        for k in range(1, n + 1):
            t = k / float(n)
            mid = flange_target_mat(lerp_pose(a, b, t), st["tool"] or [0.0] * 6) \
                if a is not None else target
            r = solve_ik(kin, mid, q, flange_dz=flange_dz)
            if not r["ok"]:
                failed = r
                break
            q = r["q"]
            last = r
            knots.append([round(v, 4) for v in q])
        if failed is not None or last is None:
            r = failed or {"pos_mm": float("inf"), "ori_deg": float("inf"), "iters": 0}
            row["why"] = ("no-converge" if r["iters"] >= 60 else "out-of-tolerance")
            row["note"] = POSE_NOTES[row["why"]].format(
                iters=r["iters"], pos=r["pos_mm"], ori=r["ori_deg"])
            row["residual"] = {"pos_mm": round(r["pos_mm"], 3),
                               "ori_deg": round(r["ori_deg"], 4),
                               "iters": r["iters"], "seed": r.get("seed", -1)}
            continue
        row.update(solved=True, source="ik", q=[round(v, 4) for v in last["q"]],
                   knots=knots,
                   residual={"pos_mm": round(last["pos_mm"], 3),
                             "ori_deg": round(last["ori_deg"], 4),
                             "iters": last["iters"], "seed": last["seed"]})
        _price(row, st, prev_q, last["q"])
        prev_q = list(last["q"])
        knots_total += len(knots)
        prev_world = st["world"]

    posed = [r for r in steps if r["solved"]]
    kinds = {}
    for r in posed:
        kinds[r["dur_kind"]] = kinds.get(r["dur_kind"], 0) + 1
    return {
        "steps": steps,
        "timing": kinds,
        "counts": {"posed": len(posed), "refused": len(steps) - len(posed),
                   "exact": sum(1 for r in posed if r["source"] == "joint"),
                   "solved": sum(1 for r in posed if r["source"] == "ik")},
        "gate": {"pos_mm": 0.5, "ori_deg": 0.05},
        "budget": {"knots": knots_total, "scaled": scale < 1.0},
        "posable": True,
    }


def _price(row, st, prev_q, q):
    """Finish a move's duration now that its joint travel is known.

    build_path can already derive a LINEAR move from its feedrate and the
    distance. A joint move is a percentage of an axis speed no backup file
    records, so it needs the travel in degrees and an assumption - which is
    exactly what ls_motion.step_duration_ms tags "assumed".
    """
    if prev_q is None or not q:
        row["dur_ms"], row["dur_kind"] = st["dur_ms"], st["dur_kind"]
        return
    travel = max((abs(q[i] - (prev_q[i] if i < len(prev_q) else 0.0))
                  for i in range(len(q))), default=0.0)
    row["travel_deg"] = round(travel, 3)
    ms, kind = step_duration_ms(st, dist_mm=st["dist_mm"], travel_deg=travel)
    if ms is not None:
        row["dur_ms"], row["dur_kind"] = round(ms, 1), kind


def flange_target_mat(tcp_mat, utool_xyzwpr):
    """flange_target, for a tcp already expressed as a matrix."""
    return mul(tcp_mat, inv_rigid(frame(utool_xyzwpr[:3], utool_xyzwpr[3:])))
