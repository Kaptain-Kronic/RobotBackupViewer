"""Extended axes and multi-group positions - the shapes a rail robot writes.

Fully synthetic and identifier-clean. The fixtures mirror real dumps byte for
byte in the parts that matter: POSREG.VA prints an extended axis as its own
"EXT1:   700.000 mm" line after the six joints OR after W/P/R; a .LS listing
prints it as "E1=   900.000  mm" in BOTH representations (never J7). A
two-group robot writes one [g, index] entry per group in POSREG.VA and one
GPn: block per group under a single P[n] in a listing.
"""
from backupviewer import compare
from backupviewer.parsers.ls_program import parse_ls_program
from backupviewer.parsers.registers import parse_posreg

POSREG_RAIL = (
    "[*POSREG*]$POSREG  Storage: CMOS  Access: RW  : ARRAY[2,8] OF Position Reg\n"
    "    [1,1] =   'Home1'   Group: 1\n"
    "  J1 =     0.000 deg   J2 =   -60.000 deg   J3 =     0.000 deg \n"
    "  J4 =     0.000 deg   J5 =    90.000 deg   J6 =      .000 deg \n"
    "  EXT1:     0.000 mm  \n"
    "\n"
    "    [1,2] =   'Home2' Uninitialized\n"
    "    [1,6] =   'Maintenance' \n"
    "  Group: 1   Config: F U T, 0, 0, 0\n"
    "  X:  2066.881   Y: -1269.113   Z: -1122.291\n"
    "  W:  -179.786   P:     -.649   R:   -58.819\n"
    "  EXT1:    40.492 mm  \n"
    "    [1,7] =   'Turn' \n"
    "  Group: 1   Config: N U T, 0, 0, 0\n"
    "  X:  1600.000   Y: -1000.000   Z:  -850.000\n"
    "  W:  -180.000   P:     0.000   R:  -180.000\n"
    "    [1,8] =   '' Uninitialized\n"
    "    [2,1] =   'Home1' Uninitialized\n"
    "    [2,7] =   'Turn'   Group: 2\n"
    "  J1 =    45.000 deg \n"
    "    [2,8] =   '' Uninitialized\n"
)

LS_RAIL = (
    "/PROG  RAIL\n/ATTR\n/MN\n   1:J P[1] 100% FINE ;\n   2:J P[2] 100% FINE ;\n"
    "   3:J P[3] 100% FINE ;\n/POS\n"
    'P[1:"To Infd"]{\n   GP1:\n'
    "\tUF : 1, UT : 1,\t\tCONFIG : 'F U T, 1, 0, 0',\n"
    "\tX = -1632.357  mm,\tY =   358.358  mm,\tZ =    90.419  mm,\n"
    "\tW =  -178.180 deg,\tP =     -.063 deg,\tR =   131.632 deg,\n"
    "\tE1=   900.000  mm\n};\n"
    "P[2]{\n   GP1:\n\tUF : 0, UT : 1,\t\n"
    "\tJ1=     0.000 deg,\tJ2=   -60.000 deg,\tJ3=     0.000 deg,\n"
    "\tJ4=     0.000 deg,\tJ5=    90.000 deg,\tJ6=      .000 deg,\n"
    "\tE1=  ********  mm\n"
    "   GP2:\n\tUF : 0, UT : 1,\t\n\tJ1=    45.000 deg\n};\n"
    "P[3]{\n   GP1:\n\tUF : 0, UT : 1,\t\n"
    "\tJ1=    10.000 deg,\tJ2=   -30.000 deg,\tJ3=     5.000 deg,\n"
    "\tJ4=     0.000 deg,\tJ5=   -60.000 deg,\tJ6=     0.000 deg\n};\n"
    "/END\n"
)


# ---- POSREG.VA -------------------------------------------------------------

def test_posreg_extended_axis_rides_along_in_both_representations():
    regs = {(r["group"], r["index"]): r for r in parse_posreg(POSREG_RAIL)}
    home = regs[(1, 1)]
    assert home["kind"] == "joint"
    assert home["joints"] == [0.0, -60.0, 0.0, 0.0, 90.0, 0.0]   # six, never seven
    assert home["ext"] == [{"n": 1, "value": 0.0, "unit": "mm"}]
    maint = regs[(1, 6)]
    assert maint["kind"] == "cartesian"
    assert (maint["x"], maint["r"]) == (2066.881, -58.819)
    assert maint["ext"] == [{"n": 1, "value": 40.492, "unit": "mm"}]


def test_posreg_without_extended_axes_has_no_ext_key():
    """A six-axis robot's register must not grow an empty list - the UI takes
    the key's presence as 'this robot has a rail'."""
    regs = {(r["group"], r["index"]): r for r in parse_posreg(POSREG_RAIL)}
    assert "ext" not in regs[(1, 7)]
    assert regs[(1, 2)]["kind"] == "uninit" and "ext" not in regs[(1, 2)]


def test_posreg_keeps_one_entry_per_group_line():
    """The parser stays file-shaped: PR[7] appears once per group, the group-2
    entry with the positioner's single joint. Folding is the viewer's job."""
    regs = parse_posreg(POSREG_RAIL)
    seven = [r for r in regs if r["index"] == 7]
    assert [r["group"] for r in seven] == [1, 2]
    assert seven[0]["kind"] == "cartesian"
    assert seven[1]["kind"] == "joint" and seven[1]["joints"] == [45.0]
    assert seven[1]["comment"] == "Turn"
    assert len(regs) == 8      # exactly the [g,n] lines the dump prints, no more


# ---- .LS -------------------------------------------------------------------

def test_ls_extended_axis_in_cartesian_representation():
    p1 = parse_ls_program(LS_RAIL)["positions"][0]
    g = p1["groups"][0]
    assert g["kind"] == "cartesian" and g["x"] == -1632.357
    assert g["ext"] == [{"n": 1, "value": 900.0, "unit": "mm"}]
    assert "masked" not in g


def test_ls_extended_axis_in_joint_representation_and_masked():
    p2 = parse_ls_program(LS_RAIL)["positions"][1]
    g1, g2 = p2["groups"]
    assert g1["kind"] == "joint"
    assert g1["joints"] == [0.0, -60.0, 0.0, 0.0, 90.0, 0.0]   # E1 is not J7
    assert g1["ext"] == [{"n": 1, "value": None, "unit": "mm"}]  # masked: None, never 0.0
    assert g1["masked"] is True
    assert g2 == {"gp": 2, "uf": "0", "ut": "1", "kind": "joint", "joints": [45.0]}


def test_ls_six_axis_point_has_no_ext_key():
    p3 = parse_ls_program(LS_RAIL)["positions"][2]
    assert "ext" not in p3["groups"][0]


# ---- compare ---------------------------------------------------------------

def _cart(e1):
    r = {"group": 1, "index": 6, "comment": "Maint", "kind": "cartesian",
         "x": 1.0, "y": 2.0, "z": 3.0, "w": 0.0, "p": 0.0, "r": 0.0}
    if e1 is not None:
        r["ext"] = [{"n": 1, "value": e1, "unit": "mm"}]
    return r


def test_compare_sees_a_moved_rail():
    """Same six axes, the carriage 10 mm further along: that IS a changed point."""
    assert compare.diff_posreg([_cart(700.0)], [_cart(700.0004)])["rows"] == []
    d = compare.diff_posreg([_cart(700.0)], [_cart(710.0)])
    assert [r["kind"] for r in d["rows"]] == ["changed"]
    assert "e1700.000" in d["rows"][0]["a"] and "e1710.000" in d["rows"][0]["b"]


def test_compare_treats_a_gained_or_lost_rail_axis_as_a_change():
    d = compare.diff_posreg([_cart(None)], [_cart(700.0)])
    assert [r["kind"] for r in d["rows"]] == ["changed"]
    assert "e1" not in d["rows"][0]["a"] and "e1700.000" in d["rows"][0]["b"]
