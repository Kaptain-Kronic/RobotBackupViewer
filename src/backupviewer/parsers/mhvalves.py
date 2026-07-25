"""Material-handling gripper / valve configuration (MHGRIPDT.VA).

Reconstructs the pendant's MH valve menus. MHGRIPDT.VA carries, in one file:

  MH_TOOL      ARRAY[4]    OF TOOL_DATA      the tool list (name + valve count)
  MH_GRIPPERS  ARRAY[4,16] OF GRIP_DATA      per-valve config (counts, checks, *_SN)
  MH_GRIPPERS2 ARRAY[4,16] OF GRP_TGL_DATA   per-valve retry / cancel-recover / over-stroke
  VALVE_TAB    ARRAY[16]   OF SIGAB_TAB      "Valve Outputs"       signal table (pendant F3)
  PARTP_TAB    ARRAY[16]   OF SIGNAL_TAB     "Part Present Inputs" signal table
  CLAMP_TAB    ARRAY[34]   OF SIGOPEN_TAB    "Clamp Inputs"        signal table
  VMADE_TAB    ARRAY[24]   OF SIGNAL_TAB     "Vacuum Inputs"       signal table

A valve's *_SN field is NOT a DI/DO index - it is a 1-based index into the matching
signal table. The table entry holds the signal's name, its type (_T: 1=DI, 2=DO) and
the real DI/DO number (_I). A signal is only real when its resolved number is > 0;
index 0 / an empty table slot is a controller default (e.g. VACMADE_SN[1]=1 sits on
every valve even when no vacuum is wired - and VMADE_TAB[1] is then empty). Resolving
those defaults as live signals is exactly what made the old view report phantom
"vacuums" (it also keyed "vacuum" off GRIP_VSENSOR, a 1-on-every-valve default).

  VALVETOA_SN / VALVETOB_SN        -> VALVE_TAB  (ToA / ToB outputs, DO)
  PART_PRES_SN[]                   -> PARTP_TAB  (part-presence inputs, DI)
  CLAMPOPEN_SN[] / CLAMPCLOSESN[]  -> CLAMP_TAB  (open / closed inputs; one index = one head)
  VACMADE_SN[]                     -> VMADE_TAB  (vacuum-made inputs, DI)

Verified against the BinPicker sample: valve 1 (BinToteClamp) VALVETOA/B_SN=1 ->
DO[801]/DO[802], PART_PRES_SN=[1,2] -> DI[813]/DI[814]; valve 6 (Flip Mag)
VALVETOA/B_SN=6 -> DO[977]/DO[978], CLAMPOPEN/CLOSE_SN=7 -> DI[977]/DI[978].
"""
from __future__ import annotations

from .va import VaFile, parse_struct_fields

_KIND = {1: "DI", 2: "DO"}


def _rows(rec) -> dict[int, dict]:
    """A 1-dimensional struct array -> {index:int -> {field: value}}."""
    if rec is None:
        return {}
    return {idx[0]: f for idx, f in parse_struct_fields(rec).items() if len(idx) == 1}


def _sig(name, kind_t, number, default_kind):
    """One resolved signal, or None when the slot is empty (number falsy / <= 0)."""
    if not isinstance(number, int) or number <= 0:
        return None
    return {
        "name": (name or "").strip(),
        "kind": _KIND.get(kind_t, default_kind),
        "number": number,
    }


def build_mhvalves(dt_text: str) -> dict:
    """{"tools": [{tool, name, valve_count, valves:[...]}], "tables": {...}}.

    Each valve carries a pendant-shaped `setup` block plus `inputs`/`outputs`
    lists of signals already resolved to real DI/DO (name, kind, number) - only
    the wired ones; controller-default indices that land on empty table slots
    are dropped. Default-only valve slots (no wired I/O) are skipped entirely.
    """
    vf = VaFile(dt_text)

    valve_tab = _rows(vf.get("VALVE_TAB"))
    partp_tab = _rows(vf.get("PARTP_TAB"))
    clamp_tab = _rows(vf.get("CLAMP_TAB"))
    vmade_tab = _rows(vf.get("VMADE_TAB"))

    def out_a(sn):
        e = valve_tab.get(sn)
        return _sig(e.get("SIGTOA_N"), e.get("SIGTOA_T"), e.get("SIGTOA_I"), "DO") if e else None

    def out_b(sn):
        e = valve_tab.get(sn)
        return _sig(e.get("SIGTOB_N"), e.get("SIGTOB_T"), e.get("SIGTOB_I"), "DO") if e else None

    def partp(sn):
        e = partp_tab.get(sn)
        return _sig(e.get("SIGNAL_N"), e.get("SIGNAL_T"), e.get("SIGNAL_I"), "DI") if e else None

    def vmade(sn):
        e = vmade_tab.get(sn)
        return _sig(e.get("SIGNAL_N"), e.get("SIGNAL_T"), e.get("SIGNAL_I"), "DI") if e else None

    def clamp_open(sn):
        e = clamp_tab.get(sn)
        return _sig(e.get("SIGOPEN_N"), e.get("SIGOPEN_T"), e.get("SIGOPEN_I"), "DI") if e else None

    def clamp_closed(sn):
        e = clamp_tab.get(sn)
        return _sig(e.get("SIGCLOSE_N"), e.get("SIGCLOSE_T"), e.get("SIGCLOSE_I"), "DI") if e else None

    grippers = parse_struct_fields(vf.get("MH_GRIPPERS")) if vf.get("MH_GRIPPERS") else {}
    toggles = parse_struct_fields(vf.get("MH_GRIPPERS2")) if vf.get("MH_GRIPPERS2") else {}
    tool_rows = _rows(vf.get("MH_TOOL"))

    valves_by_tool: dict[int, list] = {}
    for idx, f in sorted(grippers.items()):
        if len(idx) != 2:
            continue
        tool, num = idx
        g2 = toggles.get(idx, {})

        inputs: list[dict] = []
        for sn in (f.get("PART_PRES_SN") or []):
            s = partp(sn) if isinstance(sn, int) else None
            if s:
                inputs.append({"role": "part present", "sn": sn, **s})
        # one clamp index = one photo head -> open + closed; interleave per head
        opens = f.get("CLAMPOPEN_SN") or []
        closes = f.get("CLAMPCLOSESN") or []
        for k in range(max(len(opens), len(closes))):
            so = opens[k] if k < len(opens) else 0
            sc = closes[k] if k < len(closes) else 0
            o = clamp_open(so) if isinstance(so, int) else None
            c = clamp_closed(sc) if isinstance(sc, int) else None
            if o:
                inputs.append({"role": "clamp open", "sn": so, **o})
            if c:
                inputs.append({"role": "clamp closed", "sn": sc, **c})
        has_vacuum = False
        for sn in (f.get("VACMADE_SN") or []):
            s = vmade(sn) if isinstance(sn, int) else None
            if s:
                inputs.append({"role": "vacuum made", "sn": sn, **s})
                has_vacuum = True

        outputs: list[dict] = []
        a = out_a(f.get("VALVETOA_SN"))
        b = out_b(f.get("VALVETOB_SN"))
        if a:
            outputs.append({"role": "valve A", "sn": f.get("VALVETOA_SN"), **a})
        if b:
            outputs.append({"role": "valve B", "sn": f.get("VALVETOB_SN"), **b})

        # a configured valve has at least one wired signal; default-only slots
        # ("VALVE 7" with no I/O) are skipped, matching the pendant's list
        if not inputs and not outputs:
            continue

        name = (f.get("GRIP_NAME") or "").strip() or f"valve {tool}.{num}"
        valves_by_tool.setdefault(tool, []).append({
            "tool": tool, "num": num, "id": f.get("GRIP_ID"),
            "name": name,
            # no stored type field; default "clamp" (the pendant's term for these
            # double-solenoid ToA/ToB valves), only "vacuum" when one is wired
            "type": "vacuum" if has_vacuum else "clamp",
            "setup": {
                "clamps": f.get("GRIP_CLAMPS"),
                "parts_present": f.get("GRIP_PARTPRS"),
                "check_opened": bool(f.get("CHK_OPENED")),
                "check_closed": bool(f.get("CHK_CLOSED")),
                "operation_timeout_ms": f.get("CLAMP_DELAY"),
                "over_stroke_delay_ms": g2.get("OVRSTRKDELAY"),
                "continuous_check": bool(f.get("PARTPRS_CHK")),
                "retry_grip": bool(f.get("TGL_GRP")),
                "retry_release": bool(f.get("TGL_REL")),
                "cancel_recover_grip": bool(g2.get("CNCL_RCVRGRP")),
                "cancel_recover_release": bool(g2.get("CNCL_RCVRREL")),
            },
            "inputs": inputs,
            "outputs": outputs,
        })

    tools: list[dict] = []
    for t in sorted(set(tool_rows) | set(valves_by_tool)):
        valves = valves_by_tool.get(t, [])
        if not valves:  # only surface tools that actually have configured valves
            continue
        tr = tool_rows.get(t, {})
        count = tr.get("TOOL_VALVES")
        tools.append({
            "tool": t,
            "name": (tr.get("TOOL_NAME") or f"tool {t}").strip(),
            "valve_count": count if count is not None else len(valves),
            "valves": valves,
        })

    def tab_list(rows: dict[int, dict], fields: dict) -> list[dict]:
        """Non-empty entries of one signal table, for the inspector panel."""
        out: list[dict] = []
        for n in sorted(rows):
            e = rows[n]
            entry: dict = {"index": n}
            keep = False
            for key, (nf, tf, inf, dk) in fields.items():
                s = _sig(e.get(nf), e.get(tf), e.get(inf), dk)
                if s:
                    entry[key] = s
                    keep = True
            if keep:
                out.append(entry)
        return out

    tables = {
        "valve": tab_list(valve_tab, {
            "a": ("SIGTOA_N", "SIGTOA_T", "SIGTOA_I", "DO"),
            "b": ("SIGTOB_N", "SIGTOB_T", "SIGTOB_I", "DO"),
        }),
        "part_present": tab_list(partp_tab, {"sig": ("SIGNAL_N", "SIGNAL_T", "SIGNAL_I", "DI")}),
        "clamp": tab_list(clamp_tab, {
            "open": ("SIGOPEN_N", "SIGOPEN_T", "SIGOPEN_I", "DI"),
            "closed": ("SIGCLOSE_N", "SIGCLOSE_T", "SIGCLOSE_I", "DI"),
        }),
        "vacuum": tab_list(vmade_tab, {"sig": ("SIGNAL_N", "SIGNAL_T", "SIGNAL_I", "DI")}),
    }

    return {"tools": tools, "tables": tables}
