"""The /MN motion grammar: mn_stream() and parse_motions().

Fully synthetic and identifier-clean - no sample backup needed.
"""
import pytest

from backupviewer.parsers import ls_motion
from backupviewer.parsers.ls_motion import parse_motions, step_duration_ms
from backupviewer.parsers.ls_program import mn_stream, parse_ls_program


def prog(*body: str) -> str:
    """A minimal listing around the given /MN lines."""
    return "/PROG  MOVER\n/ATTR\n/MN\n" + "\n".join(body) + "\n/POS\n/END\n"


def one(*body: str) -> dict:
    steps = parse_motions(prog(*body))
    assert len(steps) == 1, [s["text"] for s in steps]
    return steps[0]


# ---- mn_stream: the raw instruction stream --------------------------------

def test_mn_stream_keeps_circular_continuations():
    """The body parser drops the unnumbered row; mn_stream must not - it
    carries the second P[..] of a circular move."""
    rows = mn_stream(prog("   7:C P[1] ;", "    :  P[4] 300mm/sec FINE ;"))
    assert [(r["n"], r["cont"]) for r in rows] == [(7, False), (7, True)]
    assert rows[1]["text"] == "P[4] 300mm/sec FINE"
    # and the body parser still deliberately drops it
    body = parse_ls_program(prog("   7:C P[1] ;", "    :  P[4] 300mm/sec FINE ;"))["body"]
    assert [b["n"] for b in body] == [7]


def test_mn_stream_remark_state_carries_to_continuations():
    rows = mn_stream(prog("   3://C P[1] ;", "    :  P[2] 300mm/sec FINE ;"))
    assert [r["active"] for r in rows] == [False, False]


def test_mn_stream_marks_comments_inactive():
    rows = mn_stream(prog("   1:  !a note ;", "   2:J P[1] 100% FINE ;"))
    assert [r["active"] for r in rows] == [False, True]


def test_mn_stream_stops_at_pos():
    """A P[1]{ block in /POS must never be read as an instruction."""
    rows = mn_stream("/PROG X\n/MN\n   1:J P[1] 100% FINE ;\n/POS\nP[1]{\n"
                     "   GP1:\n\tUF : 0, UT : 1,\n};\n/END\n")
    assert len(rows) == 1


def test_mn_stream_matches_healthscan_tuples():
    """The promotion's pin: healthscan's mn_lines is now expressed over
    mn_stream, so the two must agree on shape for the checks that read it."""
    text = prog("   1:J P[1] 100% FINE ;", "   2://L P[2] 100mm/sec FINE ;",
                "   3:C P[3] ;", "    :  P[4] 300mm/sec FINE ;")
    assert [(r["n"], r["text"], r["active"]) for r in mn_stream(text)] == [
        (1, "J P[1] 100% FINE", True),
        (2, "//L P[2] 100mm/sec FINE", False),
        (3, "C P[3]", True),
        (3, "P[4] 300mm/sec FINE", True),
    ]


# ---- the destination ------------------------------------------------------

def test_motion_types_and_plain_targets():
    steps = parse_motions(prog(
        "   1:J P[1] 100% FINE ;",
        "   2:L P[2] 500mm/sec CNT50 ;",
        "   3:A P[9] 500mm/sec CNT10 ;",
    ))
    assert [s["motion"] for s in steps] == ["J", "L", "A"]
    assert [s["motion_name"] for s in steps] == ["joint", "linear", "arc"]
    assert [s["target"]["id"] for s in steps] == [1, 2, 9]
    assert [s["i"] for s in steps] == [0, 1, 2]
    assert [s["line"] for s in steps] == [1, 2, 3]


def test_position_comment_may_contain_spaces():
    s = one("   1:L P[2:WELD START] 500mm/sec FINE ;")
    assert s["target"] == {"kind": "P", "id": 2, "comment": "WELD START",
                           "raw": "P[2:WELD START]"}


@pytest.mark.parametrize("line, kind, pid", [
    ("   1:J PR[1:Home] 100% FINE ;", "PR", 1),
    ("   1:J PR[4] 100% FINE ;", "PR", 4),
    ("   1:J P[...] 100% CNT100 ;", "anon", None),
    ("   1:L P[R[4]] 200mm/sec FINE ;", "indirect", None),
    ("   1:L PR[R[2]] 200mm/sec FINE ;", "indirect", None),
    # PR[7,3] addresses ONE ELEMENT of a register - a number, not a position
    ("   1:L PR[7,3] 100mm/sec FINE ;", "indirect", None),
])
def test_target_kinds(line, kind, pid):
    s = one(line)
    assert (s["target"]["kind"], s["target"]["id"]) == (kind, pid)
    assert s["target"]["raw"] in line          # the raw reference always survives


@pytest.mark.parametrize("tail", [
    "Offset,PR[7]",
    "Tool_Offset,PR[3]",
    "TIME BEFORE 0.5sec,DO[1]=ON",
    "TIME AFTER 0.2sec,CALL FOO",
    "Skip,LBL[7]",
    "DB 5.0mm",
    "ACC 50",
    "ACC80",
    "PSPD 100",
    "INC",
    "CD",
    "CR",
    "Offset,PR[7] Tool_Offset,PR[3]",
])
def test_destination_is_the_reference_after_the_motion_letter(tail):
    """The load-bearing rule. Options carry their OWN P[..]/PR[..]/LBL[..]
    references - reading one of those as the destination would send the arm
    somewhere the robot never went."""
    s = one("   1:L P[2] 100mm/sec FINE " + tail + " ;")
    assert s["target"] == {"kind": "P", "id": 2, "comment": "", "raw": "P[2]"}
    assert s["term"]["kind"] == "fine"
    assert s["options_raw"] == tail


def test_multiword_options_are_not_shredded():
    s = one("   1:L P[2] 100mm/sec FINE TIME AFTER 0.2sec,CALL FOO ;")
    assert s["options"] == ["TIME AFTER 0.2sec,CALL FOO"]
    s = one("   1:L P[2] 100mm/sec CNT10 DB 5.0mm ;")
    assert s["options"] == ["DB 5.0mm"]
    s = one("   1:L P[2] 100mm/sec FINE Offset,PR[7] Tool_Offset,PR[3] ;")
    assert s["options"] == ["Offset,PR[7]", "Tool_Offset,PR[3]"]


def test_offset_and_tool_offset_are_independent_flags():
    s = one("   1:L P[2] 100mm/sec FINE Offset,PR[7] ;")
    assert (s["offset"], s["tool_offset"], s["incremental"]) == (True, False, False)
    s = one("   1:L P[2] 100mm/sec FINE Tool_Offset,PR[3] ;")
    assert (s["offset"], s["tool_offset"], s["incremental"]) == (False, True, False)
    s = one("   1:L P[2] 100mm/sec FINE Tool_Offset,PR[3] INC ;")
    assert (s["offset"], s["tool_offset"], s["incremental"]) == (False, True, True)


# ---- circular -------------------------------------------------------------

def test_circular_collapses_to_one_step_with_a_via():
    s = one("   7:C P[3] ;", "    :  P[4] 300mm/sec CNT10 ;")
    assert s["motion"] == "C"
    assert s["via"]["id"] == 3          # the numbered line names the VIA point
    assert s["target"]["id"] == 4       # the continuation names the END point
    assert s["speed"]["value"] == 300.0
    assert s["term"] == {"raw": "CNT10", "kind": "cnt", "value": 10}
    assert s["line"] == 7
    assert "P[4]" in s["text"] and "P[3]" in s["text"]


def test_remarked_circular_yields_no_step():
    assert parse_motions(prog("   3://C P[1] ;", "    :  P[2] 300mm/sec FINE ;")) == []


def test_a_stray_continuation_is_ignored():
    """A continuation with no circular move above it binds to nothing."""
    steps = parse_motions(prog("   1:J P[1] 100% FINE ;", "    :  P[2] 300mm/sec FINE ;"))
    assert len(steps) == 1 and steps[0]["via"] is None and steps[0]["target"]["id"] == 1


# ---- inactive lines -------------------------------------------------------

def test_remarked_and_commented_lines_are_not_motions():
    steps = parse_motions(prog(
        "   1://J P[3] 50% FINE ;",
        "   2:  !J P[4] 50% FINE ;",
        "   3:J P[5] 50% FINE ;",
    ))
    assert [s["target"]["id"] for s in steps] == [5]
    assert steps[0]["i"] == 0           # indices renumber over the kept steps


def test_non_motion_lines_are_skipped():
    steps = parse_motions(prog(
        "   1:  DO[71:Process1TaskOk]=OFF ;",
        "   2:  CALL SUBPROG ;",
        "   3:J P[1] 100% FINE ;",
        "   4:  LBL[5] ;",
    ))
    assert [s["line"] for s in steps] == [3]


# ---- speed and termination ------------------------------------------------

@pytest.mark.parametrize("tok, value, unit", [
    ("100%", 100.0, "%"),
    ("500mm/sec", 500.0, "mm/sec"),
    ("50cm/min", 50.0, "cm/min"),
    ("10inch/min", 10.0, "inch/min"),
    ("30deg/sec", 30.0, "deg/sec"),
    ("3sec", 3.0, "sec"),
    ("500msec", 500.0, "msec"),
    ("2.5sec", 2.5, "sec"),
])
def test_speed_units(tok, value, unit):
    s = one("   1:L P[2] " + tok + " FINE ;")
    assert s["speed"]["value"] == value and s["speed"]["unit"] == unit


def test_register_and_max_speed_carry_no_value():
    s = one("   1:L P[2] R[12]mm/sec FINE ;")
    assert s["speed"]["value"] is None and s["speed"]["register"] == "R[12]"
    s = one("   1:J P[2] max_speed FINE ;")
    assert s["speed"]["value"] is None and s["speed"]["unit"] == "max_speed"


@pytest.mark.parametrize("tok, kind, value", [
    ("FINE", "fine", None),
    ("CNT100", "cnt", 100),
    ("CNT0", "cnt", 0),
    ("CNT R[282]", "cnt", None),        # a register - unknowable from a listing
])
def test_termination(tok, kind, value):
    s = one("   1:L P[2] 100mm/sec " + tok + " ;")
    assert s["term"]["kind"] == kind and s["term"]["value"] == value
    assert s["term"]["raw"] == tok


# ---- duration -------------------------------------------------------------

def test_linear_duration_is_derived_from_the_feedrate():
    s = one("   1:L P[2] 500mm/sec FINE ;")
    assert step_duration_ms(s, dist_mm=1000.0) == (2000.0, "derived")


def test_linear_units_convert():
    s = one("   1:L P[2] 60cm/min FINE ;")           # 60 cm/min = 10 mm/sec
    ms, how = step_duration_ms(s, dist_mm=100.0)
    assert how == "derived" and ms == pytest.approx(10000.0)


def test_time_specified_move_states_its_own_answer():
    s = one("   1:L P[2] 3sec FINE ;")
    assert step_duration_ms(s, dist_mm=1000.0) == (3000.0, "derived")
    s = one("   1:L P[2] 250msec FINE ;")
    assert step_duration_ms(s, dist_mm=1000.0) == (250.0, "derived")


def test_percent_move_is_assumed_never_derived():
    """No FANUC backup records per-model maximum joint rates, so a percentage
    move's duration is an assumption and must say so."""
    s = one("   1:J P[2] 100% FINE ;")
    ms, how = step_duration_ms(s, travel_deg=ls_motion.ASSUMED_JOINT_DEG_S)
    assert how == "assumed" and ms == pytest.approx(1000.0)
    half, how = step_duration_ms(s, travel_deg=ls_motion.ASSUMED_JOINT_DEG_S / 2)
    assert how == "assumed" and half == pytest.approx(500.0)
    # and half speed takes twice as long
    slow = one("   1:J P[2] 50% FINE ;")
    ms2, _ = step_duration_ms(slow, travel_deg=ls_motion.ASSUMED_JOINT_DEG_S)
    assert ms2 == pytest.approx(2000.0)


def test_register_speed_yields_no_duration():
    s = one("   1:L P[2] R[12]mm/sec FINE ;")
    assert step_duration_ms(s, dist_mm=1000.0) == (None, "unknown")


def test_missing_measurement_yields_no_duration():
    """A feedrate with no distance to apply it to is not a duration."""
    s = one("   1:L P[2] 500mm/sec FINE ;")
    assert step_duration_ms(s) == (None, "unknown")


# ---- masked values --------------------------------------------------------

def test_masked_position_values_stay_none():
    """A masked axis must never arrive as 0.0 - that is a real coordinate."""
    text = ("/PROG  MOVER\n/MN\n   1:J P[1] 100% FINE ;\n/POS\nP[1]{\n   GP1:\n"
            "\tUF : 0, UT : 1,\t\tCONFIG : 'N U T, 0, 0, 0',\n"
            "\tX =  ********  mm,\tY =  ********  mm,\tZ =  ********  mm,\n"
            "\tW =  ********  deg,\tP =  ********  deg,\tR =  ********  deg\n};\n/END\n")
    grp = parse_ls_program(text)["positions"][0]["groups"][0]
    assert grp["masked"] is True
    assert [grp[k] for k in "xyzwpr"] == [None] * 6
    assert parse_motions(text)[0]["target"]["id"] == 1
