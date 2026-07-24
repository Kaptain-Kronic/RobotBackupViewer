"""dcszones: DCSPOS.VA geometry + DCSVRFY.DG merge for the 3D view.

Fixtures mirror the real controller output shapes (fabricated values,
no plant identifiers): the sysvar-dump Field/element lines of DCSPOS.VA
and the pendant-text verify report parsed by parsers.dcs.
"""
from backupviewer.parsers import dcs, dcszones


def _cpc_zone(n, comment, enable, mode, grp, ufrm, num_vtx, xs, ys, z1, z2,
              stop=0, models=(-1, 0, 0)):
    lines = [
        f"     Field: $DCSS_CPC[{n}].$COMMENT Access: RW: STRING[25] = '{comment}'",
        f"     Field: $DCSS_CPC[{n}].$ENABLE Access: RW: INTEGER = {enable}",
        f"     Field: $DCSS_CPC[{n}].$MODE Access: RW: INTEGER = {mode}",
        f"     Field: $DCSS_CPC[{n}].$GRP_NUM Access: RW: INTEGER = {grp}",
        f"     Field: $DCSS_CPC[{n}].$MODEL_NUM  ARRAY[3] OF INTEGER",
    ]
    lines += [f"      [{i + 1}] = {v}" for i, v in enumerate(models)]
    lines += [
        f"     Field: $DCSS_CPC[{n}].$UFRM_NUM Access: RW: INTEGER = {ufrm}",
        f"     Field: $DCSS_CPC[{n}].$NUM_VTX Access: RW: INTEGER = {num_vtx}",
        f"     Field: $DCSS_CPC[{n}].$X  ARRAY[8] OF REAL",
    ]
    lines += [f"      [{i + 1}] = {v:e}" for i, v in enumerate(xs + [0.0] * (8 - len(xs)))]
    lines += [f"     Field: $DCSS_CPC[{n}].$Y  ARRAY[8] OF REAL"]
    lines += [f"      [{i + 1}] = {v:e}" for i, v in enumerate(ys + [0.0] * (8 - len(ys)))]
    lines += [
        f"     Field: $DCSS_CPC[{n}].$Z1 Access: RW: REAL = {z1:e}",
        f"     Field: $DCSS_CPC[{n}].$Z2 Access: RW: REAL = {z2:e}",
        f"     Field: $DCSS_CPC[{n}].$STOP_TYP Access: RW: INTEGER = {stop}",
        f"     Field: $DCSS_CPC[{n}].$USE_PREDICT Access: RW: INTEGER = 1",
    ]
    return "\n".join(lines)


def _elem(m, j, use, shape, size, size2, p1, p2, link=99):
    d = list(p1) + list(p2)
    lines = [
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$USE Access: RW: INTEGER = {use}",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$LINK_NO Access: RW: INTEGER = {link}",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$LINK_TYPE Access: RW: INTEGER = 1",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$UTOOL_NUM Access: RW: INTEGER = 0",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$SHAPE Access: RW: INTEGER = {shape}",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$SIZE  ARRAY[2] OF REAL",
        f"          [1] = {size:e}",
        f"          [2] = {size2:e}",
        f"         Field: $DCSS_MODEL[{m}].$ELEM[{j}].$DATA  ARRAY[6] OF REAL",
    ]
    lines += [f"          [{i + 1}] = {v:e}" for i, v in enumerate(d)]
    return "\n".join(lines)


_POS = "\n".join([
    "[*SYSTEM*]$DCSS_CPC  Storage: SHADOW  Access: RW  : ARRAY[32] OF DCSS_CPC_T",
    # diagonal keep-out fence in a rotated frame (mirrors the live zone shape)
    _cpc_zone(1, "FenceKO", 1, 1, 1, 21, 8,
              [-3839.63, -139.63], [2522.03, 2222.03], 3247.08, -1752.92,
              models=(-1, 1, 0)),
    # untouched slot - factory defaults
    _cpc_zone(2, "", 0, 0, 1, 0, 8, [], [], 0.0, 0.0),
    # $MODE=2 has never been pendant-paired -> heuristic shape stays honest
    _cpc_zone(3, "Conveyor", 1, 2, 2, 0, 4,
              [0.0, 1000.0, 1000.0, 0.0], [0.0, 0.0, 500.0, 500.0],
              100.0, 900.0, stop=1),
    # polygon keep-out, the confirmed $MODE=3 <-> Restricted zone(Lines)
    _cpc_zone(4, "LoadZone", 1, 3, 1, 0, 5,
              [-1700.0, -2900.0, -2900.0, -2500.0, -1600.0],
              [2600.0, 2600.0, 500.0, 500.0, 1500.0],
              -2600.0, -800.0, stop=2, models=(0, 1, 0)),
    "[*SYSTEM*]$DCSS_MODEL  Storage: SHADOW  Access: RW  : ARRAY[16] OF DCSS_MODEL_T",
    "     Field: $DCSS_MODEL[1].$COMMENT Access: RW: STRING[25] = 'EOAT'",
    "     Field: $DCSS_MODEL[1].$ELEM  ARRAY[10] OF DCSS_ELEM_T",
    _elem(1, 1, 1, 2, 350.0, 0.0, (-4.0, -606.0, 140.0), (-4.0, 602.0, 140.0)),
    _elem(1, 2, 0, 1, 0.0, 0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    # nonzero second size: passed through raw (no pendant report prints it)
    _elem(1, 3, 1, 2, 125.0, 80.0, (-353.0, -264.0, -92.0), (-70.0, 19.0, -92.0),
          link=3),
    "     Field: $DCSS_MODEL[2].$COMMENT Access: RW: STRING[25] = ''",
    "     Field: $DCSS_MODEL[2].$ELEM  ARRAY[10] OF DCSS_ELEM_T",
    _elem(2, 1, 0, 1, 0.0, 0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    "[*SYSTEM*]$DCSS_CSC  Storage: SHADOW  Access: RW  : ARRAY[16] OF DCSS_CSC_T",
    "     Field: $DCSS_CSC[1].$COMMENT Access: RW: STRING[25] = ''",
    "     Field: $DCSS_CSC[1].$ENABLE Access: RW: INTEGER = 1",
    "     Field: $DCSS_CSC[1].$MODE Access: RW: INTEGER = 0",
    "     Field: $DCSS_CSC[1].$GRP_NUM Access: RW: INTEGER = 1",
    "     Field: $DCSS_CSC[1].$TCP Access: RW: INTEGER = 0",
    "     Field: $DCSS_CSC[1].$UFRM_NUM Access: RW: INTEGER = 0",
    "     Field: $DCSS_CSC[1].$SPD_LIM Access: RW: REAL = 2.500000e+02",
    "     Field: $DCSS_CSC[1].$STOP_TYP Access: RW: INTEGER = 0",
    "     Field: $DCSS_CSC[1].$STOP_TOL Access: RW: REAL = 0.000000e+00",
    # a table the parser must skip untouched
    "[*SYSTEM*]$DCSS_TCP  Storage: SHADOW  Access: RW  : ARRAY[8,10] OF DCSS_TCP_T",
    "     Field: $DCSS_TCP[1,1].$X Access: RW: REAL = 9.990000e+02",
    "[*SYSTEM*]$DCSS_JPC  Storage: SHADOW  Access: RW  : ARRAY[40] OF DCSS_JPC_T",
    "     Field: $DCSS_JPC[1].$COMMENT Access: RW: STRING[25] = 'J2 window'",
    "     Field: $DCSS_JPC[1].$ENABLE Access: RW: INTEGER = 1",
    "     Field: $DCSS_JPC[1].$MODE Access: RW: INTEGER = 0",
    "     Field: $DCSS_JPC[1].$GRP_NUM Access: RW: INTEGER = 1",
    "     Field: $DCSS_JPC[1].$AXS_NUM Access: RW: INTEGER = 2",
    "     Field: $DCSS_JPC[1].$UPR_LIM Access: RW: REAL = 9.500000e+01",
    "     Field: $DCSS_JPC[1].$LWR_LIM Access: RW: REAL = -3.000000e+01",
    "     Field: $DCSS_JPC[1].$STOP_TYP Access: RW: INTEGER = 1",
    "[*SYSTEM*]$DCSS_JSC  Storage: SHADOW  Access: RW  : ARRAY[40] OF DCSS_JSC_T",
    "     Field: $DCSS_JSC[1].$COMMENT Access: RW: STRING[25] = ''",
    "     Field: $DCSS_JSC[1].$ENABLE Access: RW: INTEGER = 0",
    "     Field: $DCSS_JSC[1].$GRP_NUM Access: RW: INTEGER = 1",
    "     Field: $DCSS_JSC[1].$AXS_NUM Access: RW: INTEGER = 1",
    "     Field: $DCSS_JSC[1].$SPD_LIM Access: RW: REAL = 0.000000e+00",
    "[*SYSTEM*]$DCSS_UFRM  Storage: SHADOW  Access: RW  : ARRAY[8,9] OF DCSS_UFRM_T",
    "       Field: $DCSS_UFRM[1,1].$COMMENT Access: RW: STRING[25] = 'CPC SafeZones'",
    "       Field: $DCSS_UFRM[1,1].$UFRM_NUM Access: RW: INTEGER = 21",
    "       Field: $DCSS_UFRM[1,1].$X Access: RW: REAL = 1.000000e+02",
    "       Field: $DCSS_UFRM[1,1].$Y Access: RW: REAL = 0.000000e+00",
    "       Field: $DCSS_UFRM[1,1].$Z Access: RW: REAL = 0.000000e+00",
    "       Field: $DCSS_UFRM[1,1].$W Access: RW: REAL = 0.000000e+00",
    "       Field: $DCSS_UFRM[1,1].$P Access: RW: REAL = 0.000000e+00",
    "       Field: $DCSS_UFRM[1,1].$R Access: RW: REAL = -4.500000e+01",
    "       Field: $DCSS_UFRM[1,2].$COMMENT Access: RW: STRING[25] = ''",
    "       Field: $DCSS_UFRM[1,2].$UFRM_NUM Access: RW: INTEGER = 0",
    "       Field: $DCSS_UFRM[1,2].$X Access: RW: REAL = 0.000000e+00",
])

_VRFY = """F Number: F999999
VERSION : SpotTool+
$VERSION: V8.33258     10/28/2024
DATE:     12-JUN-26 13:29
DCS Version: V3.5.21

--- Cartesian Position Check -----------
  Process time factor (Max.1000):  450
 No.        G M Status     Comment
  1 ENABLE  1 DO SAFE [FenceKO         ]
  2 DISABLE 1 DI ---- [                ]

    No.  1              Status:SAFE
  1 Comment:   [FenceKO                ]
  2 Enable/Disable:            ENABLE
  3 Method(Safe side): Diagonal(OUT)
  4 Group:                            1
  5 Target model 1:                  -1
  6 Target model 2:                   1
  7 Target model 3:                   0
      (0:Disable,-1:Robot,-2:Tool)
  8 User frame:                      21
    Position(mm):
        Current      Point 1    Point 2
  9 X     881.1    -3839.6     -139.6
 10 Y     731.5     2522.0     2222.0
 11 Z    1303.3     3247.1    -1752.9
 12 Stop type:         Power-Off stop
 14 Use Stop Position Prediction: Yes

--- User model -------------------------
 No.    Elements   Status   Comment
  1 2 OK [robot model]

No.  1 [robot model]
Element:  1  Status: OK
  1 Link No:                          1

--- Signature number (Dec) -------------
  1 DCS:   123  123
"""


def _rep():
    return dcs.parse_dcs_report(_VRFY)


def test_va_geometry_frames_and_heuristics():
    z = dcszones.build_zones(_POS, None)
    assert z["source"] == "va"
    assert z["groups"] == [1, 2]

    fence, empty, conv, load = z["cpc"]
    assert fence["label"] == "FenceKO" and fence["enabled"]
    # $MODE=1 is the one observed ground-truth mapping
    assert (fence["method"], fence["side"], fence["method_source"]) == ("diagonal", "out", "va")
    # diagonal -> a 4-corner box from the two diagonal points, Z normalized lo/hi
    assert fence["poly"] == [[-3839.63, 2522.03], [-139.63, 2522.03],
                             [-139.63, 2222.03], [-3839.63, 2222.03]]
    assert (fence["z1"], fence["z2"]) == (-1752.92, 3247.08)
    # rotated DCS user frame resolved by (group, frame NUMBER)
    assert fence["frame"]["num"] == 21 and fence["frame"]["r"] == -45.0
    assert fence["frame"]["x"] == 100.0 and fence["frame"]["comment"] == "CPC SafeZones"
    assert not fence["frame_missing"]
    # va-only zones still carry a pendant-shaped detail block
    assert any("pos_table" in d for d in fence["detail"])

    assert not empty["enabled"] and empty["label"] == "zone 2"

    # unknown $MODE + vertices beyond the first two -> polygon heuristic
    assert conv["method"] == "?" and conv["method_source"] == "heuristic"
    assert conv["poly"] == [[0.0, 0.0], [1000.0, 0.0], [1000.0, 500.0], [0.0, 500.0]]
    assert conv["group"] == 2 and conv["stop"] == "Controlled stop"

    # $MODE=3 <-> Restricted zone(Lines): NUM_VTX-many vertices, keep-out
    assert (load["method"], load["side"], load["method_source"]) == ("lines", "out", "va")
    assert load["method_text"] == "Restricted zone(Lines)"
    assert load["vtx_used"] == 5 and len(load["poly"]) == 5
    assert load["poly"][4] == [-1600.0, 1500.0]
    assert (load["z1"], load["z2"]) == (-2600.0, -800.0)
    assert load["stop"] == "Not stop"

    # zone -> target-model slots, resolved per the pendant's printed legend
    assert [(t["kind"], t["raw"]) for t in fence["target_models"]] == [
        ("robot", -1), ("user", 1), ("none", 0)]
    assert fence["target_models"][1]["label"] == "User model 1 'EOAT'"
    tm_rows = {d["key"]: d["value"] for d in fence["detail"] if "key" in d}
    assert tm_rows["Target model 1"] == "Robot model"
    assert tm_rows["Target model 2"] == "User model 1 'EOAT'"

    jpc = z["jpc"][0]
    assert (jpc["axis"], jpc["low"], jpc["high"], jpc["side"]) == (2, -30.0, 95.0, "in")
    assert jpc["label"] == "J2 window" and jpc["stop"] == "Controlled stop"
    csc = z["csc"][0]
    assert csc["enabled"] and csc["limit"] == 250.0
    assert not z["jsc"][0]["enabled"]
    assert z["tcp"] is None


def test_user_models_from_va():
    z = dcszones.build_zones(_POS, None)
    m1, m2 = z["models"]
    assert m1["label"] == "EOAT" and m1["active"] and m1["elem_count"] == 2
    e1, e2, e3 = m1["elements"]

    assert e1["enabled"] and e1["shape"] == "Line_seg" and e1["size"] == 350.0
    assert e1["link_no"] == 99 and e1["link_type_text"] == "NORMAL"
    assert e1["p1"] == [-4.0, -606.0, 140.0] and e1["p2"] == [-4.0, 602.0, 140.0]
    # synthetic detail mirrors the pendant's element block
    kv = {d["key"]: d["value"] for d in e1["detail"] if "key" in d}
    assert kv["Shape"] == "Line_seg" and kv["Size (mm)"] == "350.0"
    assert kv["Link No.(99:FacePlate)"] == "99" and kv["Enable/Disable"] == "ENABLE"
    assert {"axes": [["X", "-4.000"], ["Y", "-606.000"], ["Z", "140.000"]]} in e1["detail"]

    # a Point is a single position - no Pos2 block; disabled slot still listed
    assert not e2["enabled"] and e2["shape"] == "Point"
    assert not any(d.get("raw") == "Pos2" for d in e2["detail"])

    # the unexplained second size rides along raw, never interpreted
    assert e3["size2"] == 80.0 and e3["link_no"] == 3
    assert {"key": "Size[2] (raw)", "value": "80.0"} in e3["detail"]

    assert not m2["active"] and m2["label"] == "model 2" and m2["elem_count"] == 0


def test_method_text_both_vocabularies():
    assert dcszones._parse_method_text("Diagonal(OUT)") == ("diagonal", "out")
    assert dcszones._parse_method_text("Lines(IN)") == ("lines", "in")
    assert dcszones._parse_method_text("Working zone(Diagnal)") == ("diagonal", "in")
    assert dcszones._parse_method_text("Restricted zone(Diagnal)") == ("diagonal", "out")
    assert dcszones._parse_method_text("Restricted zone(Lines)") == ("lines", "out")
    assert dcszones._parse_method_text("gibberish") is None


def test_target_name_normalization():
    entry = {"detail": [
        {"key": "Target model 1", "value": "Robot model"},
        {"key": "Target model 2", "value": "User model 2"},
        {"key": "Target model 3", "value": "DISABLE"},
    ]}
    t = dcszones._targets_from_detail(entry, {2: "gripper"})
    assert [(x["kind"], x["raw"]) for x in t] == [("robot", -1), ("user", 2), ("none", 0)]
    assert t[1]["label"] == "User model 2 'gripper'"


def test_vrfy_merge_wins_and_tcp():
    z = dcszones.build_zones(_POS, _rep())
    assert z["source"] == "va+dg" and z["date"] == "12-JUN-26 13:29"
    fence = z["cpc"][0]
    assert fence["method_source"] == "vrfy" and fence["method_text"] == "Diagonal(OUT)"
    assert fence["status"] == "SAFE" and fence["stop"] == "Power-Off stop"
    # the verify detail block (with the pendant's position table) rides along
    assert any("pos_table" in d for d in fence["detail"])
    # target slots come from the VA regardless of which detail is shown
    assert [t["raw"] for t in fence["target_models"]] == [-1, 1, 0]
    # TCP at report time, frame-local + that zone's frame for the transform
    assert z["tcp"]["xyz"] == [881.1, 731.5, 1303.3]
    assert z["tcp"]["frame"]["num"] == 21 and not z["tcp"]["approx"]
    # VA models win; the report's status + per-element pendant detail merge in
    m1 = z["models"][0]
    assert m1["label"] == "EOAT" and m1["status"] == "OK"
    assert m1["elements"][0]["status"] == "OK"
    assert m1["elements"][0]["detail"] == [{"key": "Link No", "value": "1"}]


_VRFY_NEW = """F Number: F999999
DATE:     12-JUN-26 13:29
DCS Version: V3.5.21

--- Cartesian Position Check -----------
 No.        G M Status     Comment
  1 ENABLE  1 DO SAFE [FenceKO         ]

    No.  1              Status:SAFE
  1 Comment:   [FenceKO                ]
  2 Enable/Disable:            ENABLE
  3 Method:    Working zone(Diagnal)
  4 Group:                            1
  5 Target model 1:         Robot model
  6 Target model 2:        User model 1
  7 Target model 3:             DISABLE
  8 Base frame:   User frame:     0
 12 Stop type:         Stop Category 0
"""


def test_newer_pendant_vocabulary():
    z = dcszones.build_zones(_POS, dcs.parse_dcs_report(_VRFY_NEW))
    fence = z["cpc"][0]
    assert (fence["method"], fence["side"], fence["method_source"]) == \
        ("diagonal", "in", "vrfy")
    assert fence["method_text"] == "Working zone(Diagnal)"
    assert fence["stop"] == "Stop Category 0"


def test_dg_fallback_is_flagged_approx():
    z = dcszones.build_zones(None, _rep())
    assert z["source"] == "dg"
    assert len(z["cpc"]) == 1  # zone 2 has no detail table to rebuild from
    fence = z["cpc"][0]
    assert fence["approx"] and fence["enabled"]
    assert fence["poly"] == [[-3839.6, 2522.0], [-139.6, 2522.0],
                             [-139.6, 2222.0], [-3839.6, 2222.0]]
    assert (fence["z1"], fence["z2"]) == (-1752.9, 3247.1)
    # frame rotation isn't in the report: flagged missing, drawn frame-less
    assert fence["ufrm_num"] == 21 and fence["frame"] is None and fence["frame_missing"]
    assert z["tcp"]["approx"]
    # target slots recovered from the report's own detail rows, names from
    # the report's user-model section
    assert [(t["kind"], t["raw"]) for t in fence["target_models"]] == [
        ("robot", -1), ("user", 1), ("none", 0)]
    assert fence["target_models"][1]["label"] == "User model 1 'robot model'"


def test_report_target_note_annotation():
    """dcs.parse_dcs_report attaches the referenced user model's comment to
    old-vocabulary numeric "Target model N" rows as a note - the pendant's
    verbatim value never changes."""
    rep = _rep()
    sec = next(s for s in rep["sections"] if s["kind"] == "list_detail")
    rows = {d["key"]: d for d in sec["entries"][0]["detail"] if "key" in d}
    assert rows["Target model 2"]["value"] == "1"
    assert rows["Target model 2"]["note"] == "robot model"
    assert "note" not in rows["Target model 1"]  # -1 = Robot, legend covers it
    assert "note" not in rows["Target model 3"]  # 0 = Disable


def test_empty_sources_stay_total():
    z = dcszones.build_zones(None, None)
    assert z["cpc"] == [] and z["jpc"] == [] and z["models"] == []
    assert z["groups"] == [1] and z["tcp"] is None


def _one_cpc(**kw):
    """A single-zone DCSPOS.VA, defaults sane, one field overridable."""
    args = dict(n=1, comment="Zone", enable=1, mode=1, grp=1, ufrm=0, num_vtx=8,
                xs=[-100.0, 100.0], ys=[-100.0, 100.0], z1=500.0, z2=0.0)
    args.update(kw)
    return "\n".join([
        "[*SYSTEM*]$DCSS_CPC  Storage: SHADOW  Access: RW  : ARRAY[32] OF DCSS_CPC_T",
        _cpc_zone(**args),
    ])


def test_disabled_zone_is_not_reported_enabled():
    """$ENABLE = FALSE / Uninitialized must read as DISABLED.

    Regression: dcszones used to carry its own scalar coercer that returned
    the literal text, so bool("FALSE") and bool("Uninitialized") were both
    True - a disabled or never-taught safety zone drew as an ENABLED fence.
    Wrong data about a safety zone is the worst thing this app can do."""
    for raw in ("FALSE", "Uninitialized", "0"):
        z = dcszones.build_zones(_one_cpc(enable=raw), None)
        assert z["cpc"][0]["enabled"] is False, f"$ENABLE = {raw} read as enabled"
    assert dcszones.build_zones(_one_cpc(enable=1), None)["cpc"][0]["enabled"] is True


def test_uninitialized_user_frame_does_not_crash():
    """An untaught $UFRM_NUM used to reach int('Uninitialized') and take the
    whole 3D tab down; it must degrade to "no frame" instead."""
    z = dcszones.build_zones(_one_cpc(ufrm="Uninitialized"), None)
    assert z["cpc"][0]["ufrm_num"] == 0
    assert z["cpc"][0]["frame"] is None
    assert z["cpc"][0]["frame_missing"] is False   # 0 = none referenced, not a dangling ref


def test_uninitialized_vertex_stays_numeric():
    """An uninitialized vertex coerces to None; geometry must see 0.0 like an
    absent one, not None leaking into the projection math."""
    va = _one_cpc().replace("      [2] = 1.000000e+02", "      [2] = Uninitialized", 1)
    poly = dcszones.build_zones(va, None)["cpc"][0]["poly"]
    assert all(isinstance(c, (int, float)) for pt in poly for c in pt)
