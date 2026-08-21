"""Resolving a program's taught points to world millimetres.

Fully synthetic and identifier-clean - no sample backup needed. The chain
used for the joint-rep cases is a shipped builtin, so the forward kinematics
under these assertions are the pendant-proven ones.
"""
import math

import pytest

from backupviewer.kinematics_builtin import BUILTIN
from backupviewer.parsers import ls_motion, ls_program, program_path
from backupviewer.parsers.kinematics import chain_frames, frame, mul, wpr_of

KIN = BUILTIN["ARCMATE120ID"]["kin"]        # validated family, 6 joints


# ---- synthetic listings ---------------------------------------------------

def cart(pid, x, y, z, w=0.0, p=0.0, r=0.0, uf="0", ut="0", gp=1,
         config="N U T, 0, 0, 0", masked=False):
    v = "********" if masked else None
    def f(n, unit):
        return ("%s  %s" % (v, unit)) if masked else ("%.3f  %s" % (n, unit))
    return ("P[%d]{\n   GP%d:\n\tUF : %s, UT : %s,\t\tCONFIG : '%s',\n"
            "\tX =  %s,\tY =  %s,\tZ =  %s,\n"
            "\tW =  %s,\tP =  %s,\tR =  %s\n};\n"
            % (pid, gp, uf, ut, config,
               f(x, "mm"), f(y, "mm"), f(z, "mm"),
               f(w, "deg"), f(p, "deg"), f(r, "deg")))


def joint(pid, js, uf="0", ut="0", gp=1):
    rows = ",\t".join("J%d=  %.3f deg" % (i + 1, v) for i, v in enumerate(js))
    return ("P[%d]{\n   GP%d:\n\tUF : %s, UT : %s,\n\t%s\n};\n"
            % (pid, gp, uf, ut, rows))


def listing(mn, pos=""):
    return "/PROG  MOVER\n/ATTR\n/MN\n" + mn + "/POS\n" + pos + "/END\n"


def frames_model(uframes=(), utools=(), active_uf=None, active_ut=None):
    def table(entries):
        return {"1": [{"index": i, "kind": "cartesian", "comment": "",
                       "x": e[0], "y": e[1], "z": e[2],
                       "w": e[3], "p": e[4], "r": e[5]} if e else
                      {"index": i, "comment": "", "uninit": True}
                      for i, e in entries]}
    return {
        "frames": table(uframes), "tools": table(utools), "jogs": {},
        "active_frame": {"1": active_uf} if active_uf is not None else {},
        "active_tool": {"1": active_ut} if active_ut is not None else {},
    }


def build(mn, pos="", **kw):
    text = listing(mn, pos)
    return program_path.build_path(ls_program.parse_ls_program(text),
                                   ls_motion.parse_motions(text), **kw)


ONE_MOVE = "   1:L P[1] 500mm/sec FINE ;\n"


# ---- frame composition ----------------------------------------------------

def test_uframe_composition_against_a_hand_derived_expectation():
    """A point at x=100 in a frame shifted 2000 in X and turned 90 deg about
    Z lands at world (2000, 100, 0), facing 90 deg."""
    out = build(ONE_MOVE, cart(1, 100, 0, 0, uf="1"),
                frames_model=frames_model(uframes=[(1, (2000, 0, 0, 0, 0, 90))]))
    st = out["steps"][0]
    assert st["ok"] is True
    assert st["world"][:3] == pytest.approx([2000.0, 100.0, 0.0], abs=1e-9)
    assert st["world"][5] == pytest.approx(90.0, abs=1e-9)


def test_uframe_zero_is_world_and_needs_no_frames_file():
    """UF:0 IS the world frame - a backup with no SYSFRAME.VA still places it,
    and it is not flagged as a missing frame."""
    out = build(ONE_MOVE, cart(1, 10, 20, 30, uf="0", ut="0"), frames_model=None)
    st = out["steps"][0]
    assert (st["ok"], st["why"]) == (True, None)
    assert st["world"][:3] == pytest.approx([10.0, 20.0, 30.0])


def test_utool_zero_is_the_faceplate():
    out = build(ONE_MOVE, joint(1, [0, 0, 0, 0, 0, 0], ut="0"),
                frames_model=None, kin=KIN)
    fp = chain_frames(KIN, [0] * 6)["faceplate"]
    assert out["steps"][0]["world"][:3] == pytest.approx(
        [fp[0][3], fp[1][3], fp[2][3]], abs=1e-9)


def test_uf_f_resolves_to_the_frame_active_at_backup_time():
    fm = frames_model(uframes=[(3, (1000, 0, 0, 0, 0, 0))], active_uf=3)
    out = build(ONE_MOVE, cart(1, 5, 0, 0, uf="F"), frames_model=fm)
    st = out["steps"][0]
    assert st["ok"] is True
    assert st["world"][0] == pytest.approx(1005.0)
    assert st["uf"] == "F"                       # the raw string is preserved
    ass = {a["id"]: a for a in out["assumptions"]}
    assert ass["uframe-current"]["n"] == 3
    assert "current" in ass["uframe-current"]["text"]


def test_uf_and_ut_stay_strings():
    """"F" is a legal value; parseInt("F") is NaN, which would silently become
    frame 0 and place the point somewhere plausible and wrong."""
    fm = frames_model(uframes=[(1, (0, 0, 0, 0, 0, 0))], utools=[(2, (0, 0, 0, 0, 0, 0))])
    st = build(ONE_MOVE, cart(1, 0, 0, 0, uf="1", ut="2"), frames_model=fm)["steps"][0]
    assert (st["uf"], st["ut"]) == ("1", "2")
    assert isinstance(st["uf"], str) and isinstance(st["ut"], str)


def test_utool_inversion_round_trips_against_the_proven_composition():
    """flange_target is the inverse of measure_flange's own
    tcp = faceplate * frame(utool). A sign or order flip there puts every
    point off by the tool length in a rotated direction - and still converges.
    """
    q = [12.0, -20.0, 15.0, 30.0, -45.0, 60.0]
    tool = [80.0, -15.0, 210.0, 10.0, -20.0, 35.0]
    fp = chain_frames(KIN, q)["faceplate"]
    tcp = mul(fp, frame(tool[:3], tool[3:]))          # measure_flange's relation
    world = [tcp[0][3], tcp[1][3], tcp[2][3]] + wpr_of(tcp)
    back = program_path.flange_target(world, tool)
    for i in range(3):
        for j in range(4):
            assert back[i][j] == pytest.approx(fp[i][j], abs=1e-9)


# ---- joint-recorded points ------------------------------------------------

def test_joint_point_places_exactly_through_forward_kinematics():
    q = [10.0, -15.0, 8.0, 0.0, -30.0, 45.0]
    tool = [0.0, 0.0, 150.0, 0.0, 0.0, 0.0]
    fm = frames_model(utools=[(1, tuple(tool))])
    st = build(ONE_MOVE, joint(1, q, ut="1"), frames_model=fm, kin=KIN)["steps"][0]
    fp = chain_frames(KIN, q)["faceplate"]
    want = mul(fp, frame(tool[:3], tool[3:]))
    assert st["rep"] == "joint"
    assert st["joints"] == pytest.approx(q)
    assert st["world"][:3] == pytest.approx([want[0][3], want[1][3], want[2][3]], abs=1e-9)


def test_joint_point_honours_the_flange_correction():
    q = [0.0] * 6
    a = build(ONE_MOVE, joint(1, q), frames_model=None, kin=KIN)["steps"][0]
    b = build(ONE_MOVE, joint(1, q), frames_model=None, kin=KIN,
              flange_dz=23.0)["steps"][0]
    assert math.dist(a["world"][:3], b["world"][:3]) == pytest.approx(23.0, abs=1e-9)


def test_joint_point_without_kinematics_says_so():
    st = build(ONE_MOVE, joint(1, [0] * 6), frames_model=None, kin=None)["steps"][0]
    assert (st["ok"], st["why"], st["rep"]) == (False, "no-kinematics", "joint")
    assert st["joints"] == [0.0] * 6              # the taught numbers still show
    assert "kinematics" in st["note"]


# ---- the refusal ladder ---------------------------------------------------

def test_missing_pos_entry():
    st = build("   1:L P[9] 500mm/sec FINE ;\n", cart(1, 0, 0, 0))["steps"][0]
    assert (st["ok"], st["why"]) == (False, "no-pos-entry")
    assert "P[9]" in st["note"]


def test_anonymous_and_indirect_targets():
    out = build("   1:J P[...] 100% FINE ;\n   2:L P[R[4]] 100mm/sec FINE ;\n")
    assert [s["why"] for s in out["steps"]] == ["anonymous", "indirect"]
    assert "P[R[4]]" in out["steps"][1]["note"]


def test_masked_values_refuse_and_never_become_zero():
    st = build(ONE_MOVE, cart(1, 0, 0, 0, masked=True), frames_model=None)["steps"][0]
    assert (st["ok"], st["why"]) == (False, "masked")
    assert st["world"] is None and st["xyzwpr"] is None


def test_missing_uframe_is_named():
    fm = frames_model(uframes=[(1, (0, 0, 0, 0, 0, 0))])
    st = build(ONE_MOVE, cart(1, 0, 0, 0, uf="5"), frames_model=fm)["steps"][0]
    assert (st["ok"], st["why"]) == (False, "uframe-missing")
    assert "uframe 5" in st["note"]


def test_uf_f_with_nothing_active_is_a_different_finding():
    """A frame we could not even NAME is not a frame we looked up and missed -
    "uframe None is not in this backup" would help nobody."""
    fm = frames_model(uframes=[(1, (0, 0, 0, 0, 0, 0))])   # no active_frame
    st = build(ONE_MOVE, cart(1, 0, 0, 0, uf="F"), frames_model=fm)["steps"][0]
    assert (st["ok"], st["why"]) == (False, "uframe-unnamed")
    assert "None" not in st["note"] and "uf: F" in st["note"]


def test_uninitialized_uframe_is_named():
    fm = frames_model(uframes=[(2, None)])
    st = build(ONE_MOVE, cart(1, 0, 0, 0, uf="2"), frames_model=fm)["steps"][0]
    assert (st["ok"], st["why"]) == (False, "uframe-uninit")
    assert "uninitialized" in st["note"]


def test_no_sysframe_file_only_blocks_the_points_that_need_it():
    out = build("   1:L P[1] 500mm/sec FINE ;\n   2:L P[2] 500mm/sec FINE ;\n",
                cart(1, 1, 2, 3, uf="0", ut="0") + cart(2, 1, 2, 3, uf="1", ut="0"),
                frames_model=None)
    a, b = out["steps"]
    assert (a["ok"], a["why"]) == (True, None)
    assert (b["ok"], b["why"]) == (False, "no-frames-file")
    assert "SYSFRAME.VA" in b["note"]


@pytest.mark.parametrize("posreg, written, why", [
    ([], set(), "no-pr"),
    ([{"group": 1, "index": 7, "kind": "uninit", "comment": ""}], set(), "uninit-pr"),
    ([{"group": 1, "index": 7, "kind": "cartesian", "comment": "",
       "x": 1.0, "y": 2.0, "z": 3.0, "w": 0.0, "p": 0.0, "r": 0.0}],
     {7}, "pr-written-at-runtime"),
])
def test_position_register_ladder(posreg, written, why):
    st = build("   1:J PR[7] 100% FINE ;\n", posreg=posreg,
               pr_written=written, frames_model=None)["steps"][0]
    assert (st["ok"], st["why"]) == (False, why)


def test_a_usable_position_register_places():
    pr = [{"group": 1, "index": 7, "kind": "cartesian", "comment": "",
           "uf": "0", "ut": "0", "x": 1.0, "y": 2.0, "z": 3.0,
           "w": 0.0, "p": 0.0, "r": 0.0}]
    st = build("   1:J PR[7] 100% FINE ;\n", posreg=pr, frames_model=None)["steps"][0]
    assert st["ok"] is True and st["world"][:3] == pytest.approx([1.0, 2.0, 3.0])


def test_group_two_numbers_are_never_used_for_the_group_one_arm():
    """A position taught only for a second motion group says so - it must not
    quietly supply coordinates for the robot."""
    out = build(ONE_MOVE, cart(1, 999, 999, 999, gp=2), frames_model=None)
    st = out["steps"][0]
    assert (st["ok"], st["why"]) == (False, "wrong-group")
    assert st["world"] is None and st["xyzwpr"] is None
    assert out["bounds"] is None


def test_pr_writers_counts_only_assignments_the_robot_would_run():
    texts = {
        "A": "/PROG A\n/MN\n   1:PR[7]=LPOS ;\n   2:PR[8,3]=1 ;\n/POS\n/END\n",
        "B": "/PROG B\n/MN\n   1://PR[9]=LPOS ;\n   2:  !PR[10]=LPOS ;\n/POS\n/END\n",
        "C": "/PROG C\n/MN\n   1:J PR[11] 100% FINE ;\n/POS\n/END\n",
    }
    got = program_path.pr_writers(texts)
    assert 7 in got and 8 in got            # live assignments
    assert 9 not in got and 10 not in got   # remarked / commented: never run
    assert 11 not in got                    # a destination is a read, not a write


# ---- counts, bounds, durations -------------------------------------------

def test_counts_bounds_and_distance():
    out = build("   1:L P[1] 500mm/sec FINE ;\n"
                "   2:L P[2] 500mm/sec FINE ;\n"
                "   3:L P[3] 500mm/sec FINE ;\n",
                cart(1, 0, 0, 0) + cart(2, 1000, 0, 0), frames_model=None)
    assert out["counts"] == {"steps": 3, "placed": 2, "refused": 1,
                             "joint": 0, "cartesian": 2}
    assert out["bounds"] == {"min": [0.0, 0.0, 0.0], "max": [1000.0, 0.0, 0.0]}
    a, b = out["steps"][0], out["steps"][1]
    assert a["dist_mm"] is None                  # nothing known before the first move
    assert b["dist_mm"] == pytest.approx(1000.0)
    assert (b["dur_ms"], b["dur_kind"]) == (2000.0, "derived")


def test_start_world_gives_the_first_move_a_distance():
    out = build(ONE_MOVE, cart(1, 1000, 0, 0), frames_model=None,
                start_world=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    st = out["steps"][0]
    assert st["dist_mm"] == pytest.approx(1000.0)
    assert (st["dur_ms"], st["dur_kind"]) == (2000.0, "derived")


def test_assumptions_are_counted_and_worded():
    out = build("   1:L P[1] 500mm/sec FINE Offset,PR[7] ;\n"
                "   2:L P[1] 500mm/sec FINE INC ;\n",
                cart(1, 0, 0, 0), frames_model=None)
    ass = {a["id"]: a for a in out["assumptions"]}
    assert ass["offset"]["n"] == 1 and ass["incremental"]["n"] == 1
    assert "taught point" in ass["offset"]["text"]


def test_verbatim_evidence_rides_every_step():
    st = build("   1:L P[1] 500mm/sec CNT50 Offset,PR[7] ;\n",
               cart(1, 0, 0, 0, config="F D B, 1, 0, -1"), frames_model=None)["steps"][0]
    assert st["text"] == "L P[1] 500mm/sec CNT50 Offset,PR[7]"
    assert st["config"] == "F D B, 1, 0, -1"      # never decoded, always shown
    assert st["term"]["raw"] == "CNT50"
    assert st["offset"] is True
