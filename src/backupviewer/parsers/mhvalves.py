"""GM material-handling gripper / valve configuration (MHGRIPDT.VA).

GM-specific KAREL data, saved differently from the standard tabs but fully
readable. The record [MHGRIPDT]MH_GRIPPERS ARRAY[4,16] OF GRIP_DATA holds one
struct per gripper slot (tool, num); each struct carries the I/O signal numbers
that drive and sense the gripper:

  VALVETOA_SN / VALVETOB_SN        valve solenoid outputs (DO) - "trigger"
  PART_PRES_SN[6]                  part-presence sensors (DI) - "see"
  CLAMPOPEN_SN[8] / CLAMPCLOSESN[8] clamp open / closed sensors (DI)
  GRIP_VSENSOR + VACMADE_SN[29]    vacuum-made sensor (DI)

The DO/DI split follows the field semantics (outputs trigger the valve, inputs
sense state) - which is exactly the "DO/DI to trigger / see it" a tech scans for.
The api layer resolves each (type, index) to its IO comment; the full untouched
struct is still shown as a nested tree on the tab.
"""
from __future__ import annotations

from .va import VaFile, parse_struct_fields

# scalar signal fields: (role label, field, io type)
_SCALAR_SIGS = [
    ("valve A", "VALVETOA_SN", "DO"),
    ("valve B", "VALVETOB_SN", "DO"),
]
# array signal fields: (role label, field, io type)
_ARRAY_SIGS = [
    ("part presence", "PART_PRES_SN", "DI"),
    ("clamp open", "CLAMPOPEN_SN", "DI"),
    ("clamp closed", "CLAMPCLOSESN", "DI"),
    ("vacuum made", "VACMADE_SN", "DI"),
]


def build_mhvalves(dt_text: str) -> dict:
    """{"grippers": [{tool, num, id, name, vacuum, signals:[{role, type, index}]}]}.

    Empty slots (no name, no id, no wired signals) are dropped. Signal entries
    are only the non-zero, configured ones - in the order a tech reads them.
    """
    rec = VaFile(dt_text).get("MH_GRIPPERS")
    if rec is None:
        return {"grippers": []}

    grippers: list[dict] = []
    for idx, f in sorted(parse_struct_fields(rec).items()):
        tool, num = idx if len(idx) == 2 else (1, idx[0])
        name = (f.get("GRIP_NAME") or "").strip()
        gid = f.get("GRIP_ID")

        vacuum = bool(f.get("GRIP_VSENSOR"))
        signals: list[dict] = []
        for label, fld, typ in _SCALAR_SIGS:
            v = f.get(fld)
            if isinstance(v, int) and v > 0:
                signals.append({"role": label, "type": typ, "index": v})
        for label, fld, typ in _ARRAY_SIGS:
            # VACMADE_SN[1]=1 is a controller-wide default that appears on every
            # slot - only meaningful when this gripper actually uses vacuum
            if fld == "VACMADE_SN" and not vacuum:
                continue
            for v in f.get(fld) or []:
                if isinstance(v, int) and v > 0:
                    signals.append({"role": label, "type": typ, "index": v})

        # an unconfigured slot has a default name ("VALVE 7") but no wired I/O;
        # a real gripper has at least one signal
        if not signals:
            continue
        grippers.append({
            "tool": tool, "num": num, "id": gid,
            "name": name or f"gripper {tool}.{num}",
            "vacuum": vacuum,
            "signals": signals,
        })
    return {"grippers": grippers}
