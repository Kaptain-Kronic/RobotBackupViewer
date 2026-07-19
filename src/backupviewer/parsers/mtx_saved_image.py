"""Parser for a Matrox Design Assistant SavedImages `.txt` sidecar.

Each runtime inspection photo the camera saves comes as a triple - `<name>.jpg`
(preview), `<name>.png` (full), `<name>.txt` (this file). The `.txt` is a flat
sectioned report: a bare section-title line (no colon) followed by `Key: Value`
lines, blank-line separated. A real example:

    Camera
    Camera Name: FAB-01RB172-R01CAM02
    Host Name: gtx351782
    MAC Address: 00:20:FC:35:17:82
    IP Address: 192.0.2.161
    Camera Type: Matrox GTX2000
    Software Version: 9.1.54
    Project Name: GMDAX_9_1_MULTI_FANUC_G_V2_0 5

    Inspection
    Image Time Stamp: 2026/07/07 11:10:10:136
    Overall Pass or Fail: Fail

    Vision Tool Settings
    Recipe: Face A Sol 2
    Recipe ID: 402
    Exposure Time: 101
    ...

    Vision Tool Results
    Blob 1 Pass or Fail: Pass
    Edge 1 Pass or Fail: Fail

Pure function: text -> JSON-serializable dict. Values can themselves contain
colons (timestamps `11:10:10:136`, MAC `00:20:FC:...`), so keys split on the FIRST
colon only; a line with no colon starts a new section.
"""
from __future__ import annotations

import re

_TOOL_RESULT_RE = re.compile(r"^(.*?) Pass or Fail$", re.IGNORECASE)


def parse_saved_image(text: str) -> dict:
    """Parse a SavedImages `.txt` sidecar into sections + convenience fields:

        {
          "result":    "Pass" | "Fail" | "",   # Overall Pass or Fail
          "timestamp": "2026/07/07 11:10:10:136",
          "camera":    {name, type, ip, host, mac, software, project},
          "recipe":    {name, id, exposure, gain},
          "tools":     [{name: "Blob 1", result: "Pass"}, ...],
          "sections":  [{title, rows: [{key, value}, ...]}, ...],
        }
    """
    sections: list[dict] = []
    current: dict | None = None
    flat: dict[str, str] = {}          # lower-cased key -> value (last wins)
    tools: list[dict] = []

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key, value = key.strip(), value.strip()
            if current is None:
                current = {"title": "", "rows": []}
                sections.append(current)
            current["rows"].append({"key": key, "value": value})
            flat[key.lower()] = value
            m = _TOOL_RESULT_RE.match(key)
            if m and key.lower() != "overall pass or fail":
                tools.append({"name": m.group(1).strip(), "result": value})
        else:
            current = {"title": line.strip(), "rows": []}
            sections.append(current)

    def g(*keys: str) -> str:
        for k in keys:
            v = flat.get(k.lower())
            if v:
                return v
        return ""

    return {
        "result": g("overall pass or fail"),
        "timestamp": g("image time stamp"),
        "camera": {
            "name": g("camera name"),
            "type": g("camera type"),
            "ip": g("ip address"),
            "host": g("host name"),
            "mac": g("mac address"),
            "software": g("software version"),
            "project": g("project name"),
        },
        "recipe": {
            "name": g("recipe"),
            "id": g("recipe id"),
            "exposure": g("exposure time"),
            "gain": g("gain"),
        },
        "tools": tools,
        "sections": sections,
    }
