"""MH valve signal-table resolution (parsers/mhvalves).

A valve's *_SN field is a 1-based index into one of four stored signal tables
(VALVE_TAB / PARTP_TAB / CLAMP_TAB / VMADE_TAB); the parser must resolve it to the
real DI/DO held in that table entry, and treat an empty slot (number 0) as "not
wired" - the controller-default VACMADE_SN[1]=1 lands on an empty VMADE_TAB[1] and
must NOT become a phantom vacuum (the old GRIP_VSENSOR heuristic's bug). This
synthetic fixture mirrors the BinPicker pendant: valve 1 BinToteClamp (parts +
valve outputs) and valve 6 Flip Mag (one clamp), plus a default-only slot.
"""
from backupviewer.parsers import mhvalves


def _arr(prefix, field, values):
    out = [f"       Field: {prefix}.{field}  ARRAY[{len(values)}] OF INTEGER\n"]
    for j, v in enumerate(values, 1):
        out.append(f"        [{j}] = {v}\n")
    return "".join(out)


def _gripper(num, name, va_sn, parts, clamps, copen, cclose, ck_o, ck_c):
    p = f"MH_GRIPPERS[1,{num}]"
    return (
        f"       Field: {p}.GRIP_ID Access: RW: INTEGER = 1\n"
        f"       Field: {p}.GRIP_NAME Access: RW: STRING[16] = '{name}'\n"
        f"       Field: {p}.VALVETOA_SN Access: RW: INTEGER = {va_sn}\n"
        f"       Field: {p}.VALVETOB_SN Access: RW: INTEGER = {va_sn}\n"
        f"       Field: {p}.GRIP_PARTPRS Access: RW: INTEGER = {sum(1 for x in parts if x)}\n"
        f"       Field: {p}.PARTPRS_CHK Access: RW: BOOLEAN = FALSE\n"
        + _arr(p, "PART_PRES_SN", parts)
        + f"       Field: {p}.GRIP_CLAMPS Access: RW: INTEGER = {clamps}\n"
        f"       Field: {p}.CHK_OPENED Access: RW: BOOLEAN = {ck_o}\n"
        f"       Field: {p}.CHK_CLOSED Access: RW: BOOLEAN = {ck_c}\n"
        f"       Field: {p}.CLAMP_DELAY Access: RW: INTEGER = 750\n"
        + _arr(p, "CLAMPOPEN_SN", copen)
        + _arr(p, "CLAMPCLOSESN", cclose)
        + f"       Field: {p}.TGL_GRP Access: RW: BOOLEAN = FALSE\n"
        f"       Field: {p}.TGL_REL Access: RW: BOOLEAN = FALSE\n"
        f"       Field: {p}.GRIP_VSENSOR Access: RW: INTEGER = 1\n"
        + _arr(p, "VACMADE_SN", [1, 0])
    )


def _toggle(num):
    p = f"MH_GRIPPERS2[1,{num}]"
    return (
        f"       Field: {p}.CNCL_RCVRGRP Access: RW: BOOLEAN = FALSE\n"
        f"       Field: {p}.CNCL_RCVRREL Access: RW: BOOLEAN = FALSE\n"
        f"       Field: {p}.OVRSTRKDELAY Access: RW: INTEGER = 0\n"
    )


def _sigab(i, an, ai, bn, bi):
    return (
        f"     Field: VALVE_TAB[{i}].SIGTOA_N Access: RW: STRING[16] = '{an}'\n"
        f"     Field: VALVE_TAB[{i}].SIGTOA_T Access: RW: INTEGER = 2\n"
        f"     Field: VALVE_TAB[{i}].SIGTOA_I Access: RW: INTEGER = {ai}\n"
        f"     Field: VALVE_TAB[{i}].SIGTOB_N Access: RW: STRING[16] = '{bn}'\n"
        f"     Field: VALVE_TAB[{i}].SIGTOB_T Access: RW: INTEGER = 2\n"
        f"     Field: VALVE_TAB[{i}].SIGTOB_I Access: RW: INTEGER = {bi}\n"
    )


def _signal(i, n, idx):
    return (
        f"     Field: PARTP_TAB[{i}].SIGNAL_N Access: RW: STRING[16] = '{n}'\n"
        f"     Field: PARTP_TAB[{i}].SIGNAL_T Access: RW: INTEGER = 1\n"
        f"     Field: PARTP_TAB[{i}].SIGNAL_I Access: RW: INTEGER = {idx}\n"
    )


def _clamp(i, on, oi, cn, ci):
    return (
        f"     Field: CLAMP_TAB[{i}].SIGOPEN_N Access: RW: STRING[16] = '{on}'\n"
        f"     Field: CLAMP_TAB[{i}].SIGOPEN_T Access: RW: INTEGER = 1\n"
        f"     Field: CLAMP_TAB[{i}].SIGOPEN_I Access: RW: INTEGER = {oi}\n"
        f"     Field: CLAMP_TAB[{i}].SIGCLOSE_N Access: RW: STRING[16] = '{cn}'\n"
        f"     Field: CLAMP_TAB[{i}].SIGCLOSE_T Access: RW: INTEGER = 1\n"
        f"     Field: CLAMP_TAB[{i}].SIGCLOSE_I Access: RW: INTEGER = {ci}\n"
    )


_MH = (
    "[MHGRIPDT]MH_TOOL  Storage: CMOS  Access: RW  : ARRAY[4] OF TOOL_DATA\n"
    "     Field: MH_TOOL[1].TOOL_NAME Access: RW: STRING[16] = 'TOOL 1'\n"
    "     Field: MH_TOOL[1].TOOL_VALVES Access: RW: INTEGER = 2\n"
    "[MHGRIPDT]MH_GRIPPERS  Storage: CMOS  Access: RW  : ARRAY[4,16] OF GRIP_DATA\n"
    + _gripper(1, "BinToteClamp", 1, [1, 2, 0, 0, 0, 0], 0, [0] * 8, [0] * 8, "TRUE", "TRUE")
    + _gripper(6, "Flip Mag", 6, [0] * 6, 1, [7] + [0] * 7, [7] + [0] * 7, "FALSE", "FALSE")
    + _gripper(2, "VALVE 2", 0, [0] * 6, 0, [0] * 8, [0] * 8, "FALSE", "FALSE")
    + "[MHGRIPDT]MH_GRIPPERS2  Storage: CMOS  Access: RW  : ARRAY[4,16] OF GRP_TGL_DATA\n"
    + _toggle(1) + _toggle(6)
    + "[MHGRIPDT]VALVE_TAB  Storage: SHADOW  Access: RW  : ARRAY[16] OF SIGAB_TAB\n"
    + _sigab(1, "do801Valve01ToA", 801, "do802Valve01ToB", 802)
    + _sigab(6, "do977T1Valve1ToA", 977, "do978T1Valve1ToB", 978)
    + "[MHGRIPDT]PARTP_TAB  Storage: SHADOW  Access: RW  : ARRAY[16] OF SIGNAL_TAB\n"
    + _signal(1, "di813PartPres01", 813) + _signal(2, "di814PartPres02", 814)
    + "[MHGRIPDT]CLAMP_TAB  Storage: SHADOW  Access: RW  : ARRAY[34] OF SIGOPEN_TAB\n"
    + _clamp(7, "di977T1PH01PX1", 977, "di978T1PH01PX2", 978)
    + "[MHGRIPDT]VMADE_TAB  Storage: SHADOW  Access: RW  : ARRAY[24] OF SIGNAL_TAB\n"
    "     Field: VMADE_TAB[1].SIGNAL_N Access: RW: STRING[16] = ''\n"
    "     Field: VMADE_TAB[1].SIGNAL_T Access: RW: INTEGER = 1\n"
    "     Field: VMADE_TAB[1].SIGNAL_I Access: RW: INTEGER = 0\n"
)


def _valves(model):
    return {v["num"]: v for t in model["tools"] for v in t["valves"]}


def test_tool_and_valve_list():
    m = mhvalves.build_mhvalves(_MH)
    assert len(m["tools"]) == 1
    t = m["tools"][0]
    assert t["tool"] == 1 and t["name"] == "TOOL 1" and t["valve_count"] == 2
    # valve 1 + valve 6 are configured; the default-only "VALVE 2" slot is dropped
    assert sorted(v["num"] for v in t["valves"]) == [1, 6]


def test_valve1_resolves_parts_and_outputs():
    v = _valves(mhvalves.build_mhvalves(_MH))[1]
    assert v["name"] == "BinToteClamp" and v["type"] == "clamp"
    assert v["setup"]["parts_present"] == 2 and v["setup"]["clamps"] == 0
    assert v["setup"]["check_opened"] is True and v["setup"]["check_closed"] is True
    assert v["setup"]["operation_timeout_ms"] == 750
    ins = [(s["role"], s["kind"], s["number"]) for s in v["inputs"]]
    outs = [(s["role"], s["kind"], s["number"]) for s in v["outputs"]]
    assert ins == [("part present", "DI", 813), ("part present", "DI", 814)]
    assert outs == [("valve A", "DO", 801), ("valve B", "DO", 802)]


def test_valve6_resolves_clamp_pair():
    v = _valves(mhvalves.build_mhvalves(_MH))[6]
    assert v["name"] == "Flip Mag" and v["type"] == "clamp"
    assert v["setup"]["clamps"] == 1
    ins = [(s["role"], s["kind"], s["number"]) for s in v["inputs"]]
    outs = [(s["role"], s["number"]) for s in v["outputs"]]
    # CLAMPOPEN_SN=CLAMPCLOSESN=7 -> CLAMP_TAB[7] open DI[977] / closed DI[978]
    assert ins == [("clamp open", "DI", 977), ("clamp closed", "DI", 978)]
    assert outs == [("valve A", 977), ("valve B", 978)]


def test_no_phantom_vacuum_from_defaults():
    m = mhvalves.build_mhvalves(_MH)
    # VACMADE_SN[1]=1 sits on both valves but VMADE_TAB[1] is empty (I=0)
    for v in _valves(m).values():
        assert v["type"] != "vacuum"
        assert all(s["role"] != "vacuum made" for s in v["inputs"])
    assert m["tables"]["vacuum"] == []          # nothing wired in the vacuum table
    assert {e["index"] for e in m["tables"]["valve"]} == {1, 6}
    assert m["tables"]["clamp"][0]["open"]["number"] == 977
