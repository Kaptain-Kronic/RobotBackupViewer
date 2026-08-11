"""DCS zone geometry for the 3D view: DCSPOS.VA + DCSVRFY.DG.

DCSPOS.VA (a sysvar dump of the DCS SHADOW tables) is the authoritative
geometry source: full vertex arrays, Z extents and the DCS user frames
(which can be rotated - a zone's numbers only make sense through its
frame). DCSVRFY.DG rides along for what the .VA does not carry: the
pendant's verify STATUS per zone, the method text ("Diagonal(OUT)") and
the TCP position captured when the report was written. Backups without
DCSPOS.VA fall back to the verify report's Point 1/Point 2 boxes
(diagonal-only - lossy but drawable, flagged "approx").

Ground truth (every mapping below was paired with pendant text from a
real controller's verify report - same robot, same values):
  $MODE      0 <-> "Working zone(Diagnal)" (keep-in) · 1 <-> "Diagonal(OUT)"
             and "Restricted zone(Diagnal)" (keep-out, both vocabularies) ·
             3 <-> "Restricted zone(Lines)" (polygon keep-out). 2 has never
             been seen - method reads "?" + vertex-count heuristic.
  $STOP_TYP  0 <-> "Power-Off stop" / "Stop Category 0" · 1 <-> "Stop
             Category 1" (= controlled stop) · 2 <-> "Not stop". Newer DCS
             prints the "Stop Category" wording; we display the older text.
  $MODEL_NUM the zone's three target-model slots. The legend is printed by
             the pendant itself: (0:Disable,-1:Robot,-2:Tool), positive n =
             user model n ($DCSS_MODEL[n]).
  $DCSS_MODEL user models (EOAT etc.): per element $SHAPE 1 <-> "Point",
             2 <-> "Line_seg" (radius $SIZE[1] <-> "Size (mm)", ends
             $DATA[1..3]/[4..6] <-> Pos1/Pos2), $LINK_NO 99 <-> FacePlate,
             $LINK_TYPE 1 <-> "NORMAL". $SIZE[2] is nonzero on a few real
             elements but no pendant report ever prints it - passed through
             raw, meaning unverified. Elements carry link/faceplate frames;
             the 3D view poses them once kinematics match (else data-only).
$NUM_VTX stays at its factory 8 on Diagonal zones, so it is only
trusted in Lines mode. $DCSS_TUIRO/$DCSS_TUIZN are not handled
(unknown semantics; none seen configured with geometry).
"""
from __future__ import annotations

import re

from .common import coerce_scalar

# tables kept from DCSPOS.VA; everything else in the dump is skipped
_WANTED = {"DCSS_CPC", "DCSS_CSC", "DCSS_JPC", "DCSS_JSC", "DCSS_UFRM", "DCSS_MODEL"}

_TABLE = re.compile(r"^\[\*SYSTEM\*\]\$(\w+)\b")
# optional [j].$SUB tail = the one-deeper $DCSS_MODEL[i].$ELEM[j].$FIELD shape
_FIELD = re.compile(r"^\s+Field:\s+\$(\w+)\[([\d,]+)\]\.\$(\w+)(?:\[(\d+)\]\.\$(\w+))?\b(.*)$")
_ELEM = re.compile(r"^\s+\[(\d+)\]\s*=\s*(.*?)\s*$")
# verify-report method text, both pendant vocabularies
_METHOD = re.compile(r"^(\w+)\s*\(\s*(IN|OUT)\s*\)", re.I)
_METHOD2 = re.compile(r"^(Working|Restricted)\s+zone\s*\(\s*(\w+)\s*\)", re.I)
_METHOD2_SHAPES = {"diagnal": "diagonal", "diagonal": "diagonal", "lines": "lines"}

_STOP_TYPES = {0: "Power-Off stop", 1: "Controlled stop", 2: "Not stop"}
_MODES = {  # $MODE -> (method, side, display text); see module docstring
    0: ("diagonal", "in", "Diagonal(IN)"),
    1: ("diagonal", "out", "Diagonal(OUT)"),
    3: ("lines", "out", "Restricted zone(Lines)"),
}
_SHAPES = {1: "Point", 2: "Line_seg"}
_LINK_TYPES = {1: "NORMAL"}


def _value(tok: str):
    """Shared .VA scalar rules - do NOT re-implement these here.

    The private version this replaced returned the literal text for
    'Uninitialized' and 'FALSE', both of which are TRUTHY, so an
    uninitialized OR explicitly-disabled safety zone read back as
    enabled (bool("FALSE") is True) and an uninitialized $UFRM_NUM
    crashed int()."""
    return coerce_scalar(tok)


def _parse_tables(text: str) -> dict:
    """DCSPOS.VA -> {table: {idx_str: {FIELD: value | {elem_i: value}}}}.

    $DCSS_MODEL nests one level deeper (Field: $DCSS_MODEL[1].$ELEM[2].$USE);
    those land under rec["ELEM"][2]["USE"], and their $SIZE/$DATA arrays
    collect element lines the same way top-level array fields do."""
    tables: dict[str, dict] = {}
    cur_table: str | None = None
    cur_arr: dict | None = None  # element target for ARRAY fields
    for raw in text.splitlines():
        m = _TABLE.match(raw)
        if m:
            cur_table = m.group(1) if m.group(1) in _WANTED else None
            cur_arr = None
            continue
        if cur_table is None:
            continue
        m = _FIELD.match(raw)
        if m:
            var, idx, field, sub_i, sub_f, rest = m.groups()
            if var != cur_table:
                continue
            rec = tables.setdefault(var, {}).setdefault(idx, {})
            if sub_i is not None:  # nested struct element field
                rec = rec.setdefault(field, {}).setdefault(int(sub_i), {})
                field = sub_f
            if " = " in rest:
                rec[field] = _value(rest.split(" = ", 1)[1])
                cur_arr = None
            elif "=" in rest:  # tight "STRING[25] ='x'" never seen, but be lenient
                rec[field] = _value(rest.split("=", 1)[1])
                cur_arr = None
            elif "OF DCSS_" in rest:  # struct-array header - children carry the data
                cur_arr = None
            else:  # "ARRAY[8] OF REAL" header - elements follow
                cur_arr = rec[field] = {}
            continue
        if cur_arr is not None:
            m = _ELEM.match(raw)
            if m:
                cur_arr[int(m.group(1))] = _value(m.group(2))
    return tables


def _arr(rec: dict, field: str, n: int) -> list:
    """Vertex/extent arrays as floats. An uninitialized element coerces to
    None; treat it exactly like an absent one (0.0) so geometry stays
    numeric rather than throwing deep in the projection math."""
    a = rec.get(field) or {}
    out = []
    for i in range(1, n + 1):
        v = a.get(i)
        out.append(v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0)
    return out


def _frames(tables: dict) -> dict:
    """$DCSS_UFRM[group,slot] -> {(group, frame_number): frame dict}.

    Zones reference the user-chosen frame NUMBER ($UFRM_NUM, e.g. 21),
    not the slot, so the lookup is keyed on that."""
    out = {}
    for idx, rec in (tables.get("DCSS_UFRM") or {}).items():
        parts = idx.split(",")
        grp = int(parts[0]) if len(parts) == 2 else 1
        num = rec.get("UFRM_NUM") or 0
        if not num:
            continue
        out[(grp, int(num))] = {
            "x": rec.get("X", 0.0), "y": rec.get("Y", 0.0), "z": rec.get("Z", 0.0),
            "w": rec.get("W", 0.0), "p": rec.get("P", 0.0), "r": rec.get("R", 0.0),
            "num": int(num), "comment": rec.get("COMMENT", "") or "",
        }
    return out


# -- DCSVRFY.DG cross-reference ---------------------------------------------


def _vrfy_sections(report: dict | None, needle: str) -> list[dict]:
    if not report:
        return []
    return [s for s in report.get("sections", []) if needle in s.get("title", "").lower()]


def _vrfy_cpc(report: dict | None) -> dict[int, dict]:
    """verify-report Cartesian Position Check entries by zone number."""
    out: dict[int, dict] = {}
    for sec in _vrfy_sections(report, "cartesian position check"):
        for e in sec.get("entries", []):
            out[e["index"]] = e
    return out


def _detail_kv(entry: dict, key_start: str) -> str:
    for d in entry.get("detail", []):
        if d.get("key", "").lower().startswith(key_start):
            return str(d.get("value", ""))
    return ""


def _detail_pos(entry: dict) -> dict | None:
    for d in entry.get("detail", []):
        if "pos_table" in d:
            return d["pos_table"]
    return None


def _pos_col(pt: dict, col: int) -> dict:
    """pos_table column -> {axis: float} (masked/odd values skipped)."""
    out = {}
    for row in pt.get("rows", []):
        vals = row.get("values", [])
        if col < len(vals):
            try:
                out[row["axis"]] = float(vals[col])
            except ValueError:
                pass
    return out


# -- builders ----------------------------------------------------------------


def _box_poly(p1: tuple, p2: tuple) -> list:
    (x1, y1), (x2, y2) = p1, p2
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _parse_method_text(txt: str):
    """Pendant method text -> (method, side) or None. Two vocabularies:
    "Diagonal(OUT)" / "Lines(IN)" style, and the newer "Working
    zone(Diagnal)" (keep-in) / "Restricted zone(Lines)" (keep-out) -
    FANUC's own Diagnal spelling included."""
    m = _METHOD.match(txt)
    if m:
        return m.group(1).lower(), m.group(2).lower()
    m = _METHOD2.match(txt)
    if m:
        side = "in" if m.group(1).lower() == "working" else "out"
        return _METHOD2_SHAPES.get(m.group(2).lower(), "?"), side
    return None


def _method(rec: dict, ventry: dict | None):
    """(method, side, text, source). Verify text wins; $MODE maps only the
    three values pendant-paired so far (see module docstring)."""
    txt = _detail_kv(ventry, "method") if ventry else ""
    parsed = _parse_method_text(txt)
    if parsed:
        return parsed[0], parsed[1], txt, "vrfy"
    mode = rec.get("MODE", 0)
    if mode in _MODES:
        method, side, text = _MODES[mode]
        return method, side, text, "va"
    return "?", "?", f"mode {mode}", "heuristic"


def _cpc_shape(rec: dict, method: str):
    """-> (poly, vtx_used). Diagonal = 2 corner points regardless of
    $NUM_VTX (the pendant leaves it at 8); Lines = the first $NUM_VTX
    vertices. Unknown method: trust the vertex data itself."""
    xs, ys = _arr(rec, "X", 8), _arr(rec, "Y", 8)
    n_vtx = int(rec.get("NUM_VTX") or 2)
    if method == "lines":
        n = max(3, min(n_vtx, 8))
        return [[xs[i], ys[i]] for i in range(n)], n
    if method == "diagonal":
        return _box_poly((xs[0], ys[0]), (xs[1], ys[1])), 2
    # unknown mode: any vertex beyond the first two set -> treat as polygon
    if any(xs[i] or ys[i] for i in range(2, 8)):
        n = max(3, min(n_vtx, 8))
        return [[xs[i], ys[i]] for i in range(n)], n
    return _box_poly((xs[0], ys[0]), (xs[1], ys[1])), 2


def _stop_text(rec: dict, ventry: dict | None) -> str:
    txt = _detail_kv(ventry, "stop type") if ventry else ""
    if txt:
        return txt
    v = rec.get("STOP_TYP")
    if v is None:
        return ""
    return _STOP_TYPES.get(v, f"stop type {v}")


def _classify_target(raw: int, model_names: dict[int, str]):
    """(kind, label) per the pendant's own legend: (0:Disable,-1:Robot,
    -2:Tool), positive n = user model n. Labels use the newer pendant's
    resolved wording, with the model's comment appended when we have it."""
    if raw == -1:
        return "robot", "Robot model"
    if raw == -2:
        return "tool", "Tool model"
    if raw == 0:
        return "none", "DISABLE"
    if raw > 0:
        name = model_names.get(raw, "")
        return "user", f"User model {raw}" + (f" '{name}'" if name else "")
    return "?", f"? ({raw})"


def _target_models(rec: dict, model_names: dict[int, str]) -> list:
    """$MODEL_NUM[1..3] -> [{slot, raw, kind, label}] (empty when the
    backup predates the field)."""
    a = rec.get("MODEL_NUM")
    if not isinstance(a, dict):
        return []
    out = []
    for slot in sorted(a):
        try:
            raw = int(a[slot])
        except (TypeError, ValueError):
            out.append({"slot": slot, "raw": None, "kind": "?", "label": str(a[slot])})
            continue
        kind, label = _classify_target(raw, model_names)
        out.append({"slot": slot, "raw": raw, "kind": kind, "label": label})
    return out


_TM_KEY = re.compile(r"^target model\s*(\d)", re.I)
_TM_USER = re.compile(r"^user model\s*(\d+)", re.I)


def _targets_from_detail(entry: dict, model_names: dict[int, str]) -> list:
    """Verify-report detail rows -> the same structure. The old pendant
    prints the raw number (legend underneath), the newer one the resolved
    name (Robot model / User model 1 / DISABLE)."""
    out = []
    for d in entry.get("detail", []):
        m = _TM_KEY.match(str(d.get("key", "")))
        if not m:
            continue
        slot, val = int(m.group(1)), str(d.get("value", "")).strip()
        try:
            raw = int(val)
        except ValueError:
            lo = val.lower()
            um = _TM_USER.match(lo)
            raw = (int(um.group(1)) if um else
                   -1 if lo.startswith("robot") else
                   -2 if lo.startswith("tool") else
                   0 if lo.startswith("disable") else None)
            if raw is None:
                out.append({"slot": slot, "raw": None, "kind": "?", "label": val})
                continue
        kind, label = _classify_target(raw, model_names)
        out.append({"slot": slot, "raw": raw, "kind": kind, "label": label})
    return out


def _synth_detail(rec: dict, poly: list, z1: float, z2: float, extra: list) -> list:
    """va-only zones still get a pendant-shaped detail block for the panel."""
    items = list(extra)
    pt = {
        "headers": [f"Point {i + 1}" for i in range(len(poly))],
        "rows": [
            {"axis": "X", "values": [f"{p[0]:.1f}" for p in poly]},
            {"axis": "Y", "values": [f"{p[1]:.1f}" for p in poly]},
            {"axis": "Z", "values": [f"{z1:.1f}", f"{z2:.1f}"] + [""] * (len(poly) - 2)},
        ],
    }
    items.append({"pos_table": pt})
    return items


def _build_cpc(tables: dict, frames: dict, vmap: dict[int, dict],
               model_names: dict[int, str]) -> list:
    out = []
    for idx, rec in sorted((tables.get("DCSS_CPC") or {}).items(), key=lambda kv: int(kv[0])):
        n = int(idx)
        ventry = vmap.get(n)
        method, side, mtext, msource = _method(rec, ventry)
        poly, vtx_used = _cpc_shape(rec, method)
        za, zb = rec.get("Z1", 0.0) or 0.0, rec.get("Z2", 0.0) or 0.0
        grp = int(rec.get("GRP_NUM") or 1)
        ufrm_num = int(rec.get("UFRM_NUM") or 0)
        frame = frames.get((grp, ufrm_num)) if ufrm_num else None
        label = rec.get("COMMENT", "") or (ventry or {}).get("comment", "") or f"zone {n}"
        targets = _target_models(rec, model_names) or (
            _targets_from_detail(ventry, model_names) if ventry else [])
        detail = (ventry or {}).get("detail") or _synth_detail(
            rec, poly, min(za, zb), max(za, zb),
            [{"key": "Method(Safe side)", "value": mtext},
             {"key": "Group", "value": str(grp)}]
            + [{"key": f"Target model {t['slot']}", "value": t["label"]} for t in targets]
            + [{"key": "User frame", "value": str(ufrm_num)},
               {"key": "Stop type", "value": _stop_text(rec, None)}])
        out.append({
            "n": n, "label": label,
            "enabled": bool(rec.get("ENABLE")) or bool((ventry or {}).get("active")),
            "group": grp,
            "method": method, "side": side,
            "method_text": mtext, "method_source": msource,
            "ufrm_num": ufrm_num, "frame": frame,
            "frame_missing": bool(ufrm_num and frame is None),
            "poly": poly, "vtx_used": vtx_used,
            "z1": min(za, zb), "z2": max(za, zb),
            "stop": _stop_text(rec, ventry),
            "status": (ventry or {}).get("detail_status") or (ventry or {}).get("row_status", ""),
            "approx": False,
            "target_models": targets,
            "detail": detail,
        })
    return out


def _cpc_from_vrfy(vmap: dict[int, dict], model_names: dict[int, str]) -> list:
    """No DCSPOS.VA: rebuild diagonal boxes from the verify report's
    Point 1/Point 2 table. The DCS user frame's rotation is not in the
    report, so geometry is drawn frame-less and flagged approx."""
    out = []
    for n, e in sorted(vmap.items()):
        pt = _detail_pos(e)
        if not pt:
            continue
        p1, p2 = _pos_col(pt, 1), _pos_col(pt, 2)
        if "X" not in p1 or "X" not in p2:
            continue
        txt = _detail_kv(e, "method")
        parsed = _parse_method_text(txt)
        try:
            ufrm_num = int(_detail_kv(e, "user frame") or 0)
        except ValueError:
            ufrm_num = 0
        out.append({
            "n": n, "label": e.get("comment") or f"zone {n}",
            "enabled": bool(e.get("active")),
            "group": int(e.get("group") or 1),
            "method": parsed[0] if parsed else "diagonal",
            "side": parsed[1] if parsed else "?",
            "method_text": txt or "", "method_source": "vrfy",
            "ufrm_num": ufrm_num, "frame": None,
            "frame_missing": bool(ufrm_num),
            "poly": _box_poly((p1["X"], p1.get("Y", 0.0)), (p2["X"], p2.get("Y", 0.0))),
            "vtx_used": 2,
            "z1": min(p1.get("Z", 0.0), p2.get("Z", 0.0)),
            "z2": max(p1.get("Z", 0.0), p2.get("Z", 0.0)),
            "stop": _detail_kv(e, "stop type"),
            "status": e.get("detail_status") or e.get("row_status", ""),
            "approx": True,
            "target_models": _targets_from_detail(e, model_names),
            "detail": e.get("detail", []),
        })
    return out


def _kv_items(pairs) -> list:
    return [{"key": k, "value": str(v)} for k, v in pairs if v not in (None, "")]


# -- user models ($DCSS_MODEL) ----------------------------------------------


def _axes_row(xyz: list) -> dict:
    return {"axes": [[ax, f"{v:.3f}"] for ax, v in zip("XYZ", xyz)]}


def _elem_detail(e: dict) -> list:
    """A pendant-shaped element block (mirrors the verify report's wording)
    for backups whose report is missing or predates the element."""
    items = [
        {"key": "Enable/Disable", "value": "ENABLE" if e["enabled"] else "DISABLE"},
        {"key": "Link No.(99:FacePlate)", "value": str(e["link_no"])},
        {"key": "Link type", "value": e["link_type_text"]},
        {"key": "Tool frame", "value": str(e["utool_num"])},
        {"key": "Shape", "value": e["shape"]},
        {"key": "Size (mm)", "value": f"{e['size']:.1f}"},
    ]
    if e["size2"]:
        # real elements carry a nonzero second size, but no pendant report
        # ever prints it - shown raw, never interpreted
        items.append({"key": "Size[2] (raw)", "value": f"{e['size2']:.1f}"})
    items.append({"raw": "Pos1"})
    items.append(_axes_row(e["p1"]))
    if e["shape_raw"] != 1:  # a Point is a single position
        items.append({"raw": "Pos2"})
        items.append(_axes_row(e["p2"]))
    return items


def _build_models(tables: dict, vrfy_models: list) -> list:
    """$DCSS_MODEL -> structured user models. Verify-report entries (status,
    pendant detail per enabled element) merge in by number when present;
    the VA numbers always ride along for the 3D view and tests. Every slot
    is listed - the UI hides the empty ones behind "show disabled"."""
    vmap = {v.get("index"): v for v in (vrfy_models or [])}
    out = []
    for idx, rec in sorted((tables.get("DCSS_MODEL") or {}).items(), key=lambda kv: int(kv[0])):
        n = int(idx)
        ventry = vmap.get(n) or {}
        velems = {v.get("num"): v for v in ventry.get("elements") or []}
        elems = []
        for j, el in sorted((rec.get("ELEM") or {}).items()):
            size = _arr(el, "SIZE", 2)
            data = _arr(el, "DATA", 6)
            shape_raw = el.get("SHAPE", 0)
            lt_raw = el.get("LINK_TYPE", 0)
            e = {
                "num": j,
                "enabled": bool(el.get("USE")),
                "link_no": int(el.get("LINK_NO") or 0),
                "link_type": lt_raw,
                "link_type_text": _LINK_TYPES.get(lt_raw, f"? ({lt_raw})"),
                "utool_num": int(el.get("UTOOL_NUM") or 0),
                "shape_raw": shape_raw,
                "shape": _SHAPES.get(shape_raw, f"? ({shape_raw})"),
                "size": size[0], "size2": size[1],
                "p1": data[0:3], "p2": data[3:6],
            }
            ve = velems.get(j) or {}
            e["status"] = ve.get("status", "")
            e["detail"] = ve.get("detail") or _elem_detail(e)
            elems.append(e)
        comment = rec.get("COMMENT", "") or ""
        enabled_count = sum(1 for e in elems if e["enabled"])
        out.append({
            "n": n, "index": n,
            "comment": comment,
            "label": comment or ventry.get("comment") or f"model {n}",
            "active": enabled_count > 0,
            "elem_count": enabled_count,
            "status": ventry.get("status", ""),
            "detail": ventry.get("detail") or [],
            "elements": elems,
        })
    return out


def _build_jpc(tables: dict) -> list:
    out = []
    for idx, rec in sorted((tables.get("DCSS_JPC") or {}).items(), key=lambda kv: int(kv[0])):
        n = int(idx)
        low, high = rec.get("LWR_LIM", 0.0) or 0.0, rec.get("UPR_LIM", 0.0) or 0.0
        mode = rec.get("MODE", 0)
        side = {0: "in", 1: "out"}.get(mode, "?")
        item = {
            "n": n, "label": rec.get("COMMENT", "") or f"joint check {n}",
            "enabled": bool(rec.get("ENABLE")),
            "group": int(rec.get("GRP_NUM") or 1),
            "axis": int(rec.get("AXS_NUM") or 1),
            "low": low, "high": high, "side": side,
            "stop": _stop_text(rec, None),
        }
        item["detail"] = _kv_items([
            ("Axis", f"J{item['axis']}"), ("Group", item["group"]),
            ("Lower limit", f"{low:.3f}"), ("Upper limit", f"{high:.3f}"),
            ("Safe side", side), ("Stop type", item["stop"]),
        ])
        out.append(item)
    return out


def _build_csc(tables: dict) -> list:
    out = []
    for idx, rec in sorted((tables.get("DCSS_CSC") or {}).items(), key=lambda kv: int(kv[0])):
        n = int(idx)
        item = {
            "n": n, "label": rec.get("COMMENT", "") or f"speed check {n}",
            "enabled": bool(rec.get("ENABLE")),
            "group": int(rec.get("GRP_NUM") or 1),
            "limit": rec.get("SPD_LIM", 0.0) or 0.0,
            "tcp_num": rec.get("TCP"), "ufrm_num": rec.get("UFRM_NUM"),
            "stop": _stop_text(rec, None),
        }
        item["detail"] = _kv_items([
            ("Speed limit (mm/s)", item["limit"]), ("Group", item["group"]),
            ("TCP", item["tcp_num"]), ("User frame", item["ufrm_num"]),
            ("Stop tolerance", rec.get("STOP_TOL")), ("Stop type", item["stop"]),
        ])
        out.append(item)
    return out


def _build_jsc(tables: dict) -> list:
    out = []
    for idx, rec in sorted((tables.get("DCSS_JSC") or {}).items(), key=lambda kv: int(kv[0])):
        n = int(idx)
        item = {
            "n": n, "label": rec.get("COMMENT", "") or f"joint speed {n}",
            "enabled": bool(rec.get("ENABLE")),
            "group": int(rec.get("GRP_NUM") or 1),
            "axis": int(rec.get("AXS_NUM") or 1),
            "limit": rec.get("SPD_LIM", 0.0) or 0.0,
            "stop": _stop_text(rec, None),
        }
        item["detail"] = _kv_items([
            ("Axis", f"J{item['axis']}"), ("Group", item["group"]),
            ("Speed limit", item["limit"]), ("Stop type", item["stop"]),
        ])
        out.append(item)
    return out


def _tcp(cpc: list, vmap: dict[int, dict]) -> dict | None:
    """TCP position when the verify report was written, from the first
    enabled zone's 'Current' column. The pendant prints it in the same
    (frame-local) columns as the points, so the zone's frame rides along
    for the world transform."""
    for z in cpc:
        if not z["enabled"]:
            continue
        e = vmap.get(z["n"])
        pt = _detail_pos(e) if e else None
        if not pt:
            continue
        cur = _pos_col(pt, 0)
        if "X" not in cur:
            continue
        return {
            "xyz": [cur.get("X", 0.0), cur.get("Y", 0.0), cur.get("Z", 0.0)],
            "frame": z["frame"],
            "approx": z["frame_missing"],
        }
    return None


def build_zones(pos_text: str | None, vrfy_report: dict | None) -> dict:
    """The 3D-view payload. Either source may be missing (not both)."""
    tables = _parse_tables(pos_text) if pos_text else {}
    vmap = _vrfy_cpc(vrfy_report)
    frames = _frames(tables)

    vrfy_models = []
    for sec in (vrfy_report.get("sections", []) if vrfy_report else []):
        if sec.get("kind") == "user_model":
            vrfy_models = sec.get("entries", [])
            break

    if tables.get("DCSS_MODEL"):
        models = _build_models(tables, vrfy_models)
    else:
        models = vrfy_models
    model_names = {m.get("n") or m.get("index"): m.get("comment", "")
                   for m in models if m.get("comment")}

    if tables.get("DCSS_CPC"):
        cpc = _build_cpc(tables, frames, vmap, model_names)
        source = "va+dg" if vrfy_report else "va"
    else:
        cpc = _cpc_from_vrfy(vmap, model_names)
        source = "dg"

    jpc, csc, jsc = _build_jpc(tables), _build_csc(tables), _build_jsc(tables)
    groups = sorted({z["group"] for coll in (cpc, jpc, csc, jsc) for z in coll} or {1})

    return {
        "source": source,
        "date": (vrfy_report or {}).get("header", {}).get("date", ""),
        "groups": groups,
        "tcp": _tcp(cpc, vmap),
        "cpc": cpc, "jpc": jpc, "csc": csc, "jsc": jsc,
        "models": models,
    }
