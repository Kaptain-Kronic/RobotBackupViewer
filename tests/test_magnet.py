"""Magnet end-effector detection (parsers/magnet).

Detection is programs-only: a robot is magnet-equipped when it carries a MAG*.PC
KAREL program. The R[800-899] "Mag ..." registers are surfaced as its config but
do not, by themselves, flag a magnet.
"""
from backupviewer.parsers import magnet


def test_detects_mag_programs_and_in_range_registers():
    numreg = [
        {"index": 800, "value": 1, "comment": "Mag Part Number"},
        {"index": 802, "value": 50, "comment": "Mag Power(0-100)"},
        {"index": 500, "value": 0, "comment": "Mag something"},   # out of 800-899 -> ignored
        {"index": 850, "value": 0, "comment": "spot count"},       # in range, no 'mag' -> ignored
    ]
    progs = ["BinToteClamp", "MAGFUNCTIONS", "MAGSETREGS", "WELDPROG"]
    m = magnet.build_magnet(numreg, progs)
    assert m["is_magnet"] is True
    assert m["programs"] == ["MAGFUNCTIONS", "MAGSETREGS"]
    assert m["counts"]["programs"] == 2
    assert [r["index"] for r in m["registers"]] == [800, 802]
    assert m["counts"]["registers"] == 2


def test_not_magnet_without_mag_programs():
    # mag registers present, but no MAG*.PC program -> not flagged (programs-only)
    m = magnet.build_magnet(
        [{"index": 800, "value": 1, "comment": "Mag Power"}],
        ["WELD", "PALLETIZE"],
    )
    assert m["is_magnet"] is False
    assert m["programs"] == []
    assert m["counts"]["registers"] == 1   # still surfaced for display


def test_empty_inputs():
    m = magnet.build_magnet(None, None)
    assert m["is_magnet"] is False
    assert m["programs"] == [] and m["registers"] == []
    assert m["groups"] == {"general": [], "magnets": []}
    assert m["mag_select"] is None


def test_groups_split_general_and_per_magnet():
    numreg = [
        {"index": 800, "value": 1, "comment": "Mag Part Number"},
        {"index": 807, "value": 2, "comment": "Mag Select"},      # active magnet selector
        {"index": 804, "value": 100, "comment": "MagPPI Delay"},  # "MagP.." is not a magnet number
        {"index": 815, "value": 5, "comment": "Mag1 Tolerance"},
        {"index": 819, "value": 675, "comment": "Mag 1 North PPI"},
        {"index": 816, "value": 0, "comment": "Mag2 Tolerance"},
        {"index": 835, "value": 1, "comment": "Mag N Var"},       # North/South, NOT a magnet number
        {"index": 836, "value": 1, "comment": "Mag S Var"},
    ]
    g = magnet.build_magnet(numreg, ["MAGFUNCTIONS"])["groups"]
    gen = [r["index"] for r in g["general"]]
    assert gen == [800, 807, 804, 835, 836]          # all the non-numbered ones, incl. N/S Var
    mags = {m["n"]: [r["index"] for r in m["registers"]] for m in g["magnets"]}
    assert mags == {1: [815, 819], 2: [816]}


def test_mag_select_value():
    m = magnet.build_magnet(
        [{"index": 807, "value": 3, "comment": "Mag Select"}], ["MAGSETREGS"]
    )
    assert m["mag_select"] == 3
