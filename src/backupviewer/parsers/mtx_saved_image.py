"""Parser for a Matrox Design Assistant SavedImages `.txt` sidecar.

Each runtime inspection photo the camera saves comes as a triple - `<name>.jpg`
(preview), `<name>.png` (full), `<name>.txt` (this file). The `.txt` is a flat
sectioned report: a bare section-title line (no colon) followed by `Key: Value`
lines, blank-line separated. An example (identifiers synthetic):

    Camera
    Camera Name: CELL-01RB172-R01CAM02
    Host Name: gtx000000
    MAC Address: 00:20:FC:00:00:01
    IP Address: 192.0.2.161
    Camera Type: Matrox GTX2000
    Software Version: 9.1.54
    Project Name: SAMPLEPROJ_9_1_V2_0 5

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


# -- photo-triple grouping --------------------------------------------------------
# A DA camera saves each inspection as a jpg (preview) + png (full) + txt
# (metadata) triple sharing one stem. Pure path-shaping: rel-paths in, grouped
# records out - the api layer owns reading sidecars and stat'ing files.

_RESULT_RE = re.compile(r"-(Pass|Fail)-", re.IGNORECASE)
_TS_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.(\d+)")

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".txt")


def photo_sort_key(name: str, mtime: int) -> tuple:
    """Newest-first ordering: the filename's own timestamp when present (the
    camera stamps one), the filesystem mtime as tie-break/fallback."""
    m = _TS_RE.search(name)
    return ("".join(m.groups()) if m else "", mtime)


def group_photo_files(rels) -> dict:
    """{stem: {"jpg"/"png"/"txt": rel}} for SavedImages rel-paths. jpg/jpeg/bmp
    class as the grid preview, png as the full frame, txt as the sidecar."""
    groups: dict[str, dict] = {}
    for rel in rels:
        stem, dot, ext = rel.rpartition(".")
        ext = ("." + ext.lower()) if dot else ""
        if ext not in PHOTO_EXTS:
            continue
        g = groups.setdefault(stem, {})
        if ext == ".txt":
            g["txt"] = rel
        elif ext == ".png":
            g["png"] = rel
        else:  # jpg/jpeg/bmp
            g["jpg"] = rel
    return groups


def photo_record(group: dict, info: dict, mtime: int) -> dict | None:
    """One grid/hero record from a grouped triple + its parsed sidecar (info may
    be {} when the txt was missing or unreadable). None for a stray sidecar with
    no image. "_sort" carries the newest-first key; the caller sorts and pops it."""
    rel_any = group.get("jpg") or group.get("png")
    if not rel_any:
        return None
    parts = rel_any.split("/")
    name = parts[-1]
    date = parts[-2] if len(parts) >= 2 else ""
    rname = _RESULT_RE.search(name)
    return {
        "name": name,
        "date": date,
        "thumb": group.get("jpg") or group.get("png"),   # small preview for the grid
        "full": group.get("png") or group.get("jpg"),    # full image for the hero
        "txt": group.get("txt", ""),
        "result": info.get("result") or (rname.group(1).title() if rname else ""),
        "timestamp": info.get("timestamp", ""),
        "camera": info.get("camera", {}),
        "recipe": info.get("recipe", {}),
        "tools": info.get("tools", []),
        "sections": info.get("sections", []),
        "_sort": photo_sort_key(name, mtime),
    }
