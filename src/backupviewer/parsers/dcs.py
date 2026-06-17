"""DCS (Dual Check Safety) reports: DCSVRFY.DG, DCSCHGD*.DG, DCSDIFF.DG.

FANUC compiles the DCS parameters themselves (SYSDCS/DCSIOC .SV), but the
controller also exports the pendant's verify screen as plain text. Each report
is a header block followed by '--- Title ---' sections.

Sections are classified into a small set of KINDS so the UI can render each
the way the pendant does:

  list_detail   position checks (Cartesian/Joint) - a numbered list where each
                enabled entry has a detail block (group, frames, model, stop)
  grouped_list  stop position prediction - grouped by motion group, then one
                entry per Cartesian / Joint J1..Jn, each with stop parameters
  logic         safe I/O connect - one safety logic equation per line
                (SSO[2] = !SSI[11] AND CSI[3])
  table         safe I/O consistency check - No / Signal1 / Signal2 / Time
  table_grouped mastering parameter - axis / position / master count per group
  raw           everything else (robot setup, CIP safety, safe I/O device,
                tool/user frame, user model) - shown as-is, status-coded

Signatures are pulled out of the 'Signature number' section into a single
structured block (current vs latch) - the section itself is dropped so the
report never shows them twice.
"""
from __future__ import annotations

import re

_SECTION = re.compile(r"^--- (.+?) -+\s*$")
_STATUS_TOKENS = ("OK", "CHGD", "NG", "PEND", "WARN", "UNAV", "INIT")
_STATUS_RE = re.compile(r"^(.*?)\s+(" + "|".join(_STATUS_TOKENS) + r")\s*$")
_HEAD_KV = re.compile(r"^(.+?)\s*:\s*(.*)$")

_SIG = re.compile(r"^\s*\d\s+(.+?):\s+(-?\d+)\s+(-?\d+)\s*$")
_SIG_TIME = re.compile(r"^\s*Time:\s*(.+?)\s{2,}(.+?)\s*$")

_LD_ROW = re.compile(r"^\s*(\d+)\s+(ENABLE|DISABLE)\s+(\d+)\s+(\S+)\s+(\S+)\s+\[(.*?)\]\s*$")
_LD_DETAIL_HEAD = re.compile(r"^\s*No\.\s+(\d+)\s+Status:\s*(\S+)")
_DETAIL_KV = re.compile(r"^\s*\d+\s+(.+?):\s+(.*?)\s*$")
_BRACKET = re.compile(r"^\[(.*)\]$")
# Cartesian Position Check detail has a sub-table: a "Position(mm):" line, a
# column header (Current/Point 1/Point 2), then "N axis v1 v2 v3" rows. Captured
# structurally so the UI can colour the axis labels + headers but not the values.
_POS_START = re.compile(r"^Position\b", re.I)
_POS_AXIS = re.compile(r"^(\d+)\s+([XYZWPR])\s+(.+)$")
# a multi-axis point line, e.g. "7  X:  -45.000 Y:  531.000 Z:  294.000" (user
# model Pos1/Pos2). FANUC crams all three axes on one line with a leading menu
# number; capture each axis/value pair so the UI can colour + align them.
_AXES_RE = re.compile(r"([XYZWPR]):\s*(-?[\d.]+)")

# raw-section "N label: value" rows. The leading N is a pendant menu item
# number (CIP safety shows 1,2,2,4 - those are indices, NOT data); drop it and
# keep a clean key/value. Labels stay verbatim (FANUC's own text, incl. its
# duplicate "input size" line).
_RAW_KV = re.compile(r"^(?:\d+\s+)?(\S.*?):\s*(.*)$")

_GROUP_LINE = re.compile(r"^Group:\s*(\d+)")
_PREDICT_ENTRY = re.compile(r"^(Cartesian|Joint J\d+)\b")
_LEAD_NUM = re.compile(r"^\d+\s+")
_PREDICT_SUB = ("Power-Off stop", "Controlled stop")

_CONS_ROW = re.compile(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\S.*?)\s{2,}(\d+)\s*(\w+)?\s*$")
_MGROUP = re.compile(r"^-+\s*Group\s*(\d+)\s*-+")
_MASTER_AXIS = re.compile(r"^J\d+$")
_EMPTY_SIG = re.compile(r"^---\[\s*0\]")

_STAT = r"OK|CHGD|NG|PEND|WARN|UNAV|INIT"
_TOOL_FRAME_START = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S.*?)\s+(" + _STAT + r")\s*$")
_USER_FRAME_START = re.compile(r"^\s*(\d+)\s+X:\s*(-?[\d.]+)\s+W:\s*(-?[\d.]+)\s*(" + _STAT + r")?\s*$")
_FRAME_AXIS = re.compile(r"([XYZWPR]):\s*(-?[\d.]+)")
_MODEL_HEAD = re.compile(r"^No\.\s+(\d+)\s+\[(.*?)\]\s*$")
_MODEL_ROW = re.compile(r"^(\d+)\s+(\d+)\s+(" + _STAT + r")\s+\[(.*?)\]\s*$")
_MODEL_ELEM = re.compile(r"^Element:\s*(\d+)\s+Status:\s*(\S+)")


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _split_status(line: str):
    """(text_without_trailing_status, status|None)."""
    m = _STATUS_RE.match(line.rstrip())
    if m:
        return m.group(1).rstrip(), m.group(2)
    return line.rstrip(), None


def _summary(lines: list[str]) -> dict:
    counts = {"ok": 0, "chgd": 0, "ng": 0}
    for raw in lines:
        _, st = _split_status(raw)
        if st == "OK":
            counts["ok"] += 1
        elif st == "CHGD":
            counts["chgd"] += 1
        elif st == "NG":
            counts["ng"] += 1
    return counts


# -- section body parsers --------------------------------------------------


def _parse_list_detail(lines: list[str]) -> dict:
    preamble: list[dict] = []
    order: list[int] = []
    entries: dict[int, dict] = {}
    details: dict[int, dict] = {}
    cur_detail: list | None = None
    cur_pos: dict | None = None
    pos_state: str | None = None  # None | "header" | "rows"

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            pos_state = None
            continue

        m = _LD_DETAIL_HEAD.match(line)
        if m:
            idx = int(m.group(1))
            cur_detail = []
            pos_state = None
            details[idx] = {"status": m.group(2), "items": cur_detail}
            continue

        if cur_detail is not None:
            if pos_state is not None:
                am = _POS_AXIS.match(s)
                if pos_state == "header" and not am:
                    cur_pos["headers"] = re.split(r"\s{2,}", s)
                    pos_state = "rows"
                    continue
                if am:
                    pos_state = "rows"
                    cur_pos["rows"].append({"axis": am.group(2),  # just X/Y/Z, drop the menu number
                                            "values": am.group(3).split()})
                    continue
                pos_state = None  # table ended - fall through to normal handling
            if _POS_START.match(s):
                cur_pos = {"headers": [], "rows": []}
                cur_detail.append({"pos_table": cur_pos})
                pos_state = "header"
                continue
            m = _DETAIL_KV.match(line)
            if m:
                val = m.group(2).strip()
                bm = _BRACKET.match(val)
                if bm:
                    val = bm.group(1).strip()
                cur_detail.append({"key": m.group(1).strip(), "value": val})
            else:
                cur_detail.append({"raw": s})
            continue

        m = _LD_ROW.match(line)
        if m:
            idx = int(m.group(1))
            order.append(idx)
            entries[idx] = {
                "index": idx,
                "enable": m.group(2),
                "group": int(m.group(3)),
                "io": m.group(4),
                "row_status": m.group(5),
                "comment": m.group(6).strip(),
            }
            continue

        if s.startswith("No."):
            continue  # list column header

        text, st = _split_status(line)
        preamble.append({"text": text.strip(), "status": st})

    out_entries = []
    for idx in order:
        e = entries[idx]
        d = details.get(idx)
        e["label"] = e["comment"] or f"No. {idx}"
        e["active"] = e["enable"] == "ENABLE"
        e["detail"] = d["items"] if d else []
        e["detail_status"] = d["status"] if d else ""
        out_entries.append(e)
    return {"preamble": preamble, "entries": out_entries}


def _parse_grouped_list(lines: list[str]) -> dict:
    groups: list[dict] = []
    cur_group: dict | None = None
    cur_entry: dict | None = None

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        m = _GROUP_LINE.match(s)
        if m:
            cur_group = {"group": int(m.group(1)), "entries": []}
            groups.append(cur_group)
            cur_entry = None
            continue
        m = _PREDICT_ENTRY.match(s)
        if m and cur_group is not None:
            cur_entry = {"label": m.group(1), "detail": []}
            cur_group["entries"].append(cur_entry)
            continue
        if cur_entry is not None:
            text, st = _split_status(line)
            t = _LEAD_NUM.sub("", text.strip())
            cur_entry["detail"].append({"text": t, "status": st, "sub": t in _PREDICT_SUB})
    return {"groups": groups}


def _parse_logic(lines: list[str]) -> dict:
    rows = []
    for raw in lines:
        s = raw.rstrip()
        if not s.strip():
            continue
        if "Output" in s and "Status" in s:
            continue  # column header
        text, status = _split_status(s)
        text = text.strip()
        rows.append({"text": text, "status": status, "empty": bool(_EMPTY_SIG.match(text))})
    return {"rows": rows}


def _parse_consistency(lines: list[str]) -> dict:
    rows = []
    for raw in lines:
        s = raw.rstrip()
        if not s.strip() or s.strip().startswith("No."):
            continue
        m = _CONS_ROW.match(s)
        if not m:
            continue
        sig1, sig2 = m.group(2).strip(), m.group(3).strip()
        rows.append({
            "index": int(m.group(1)),
            "sig1": sig1,
            "sig2": sig2,
            "time": int(m.group(4)),
            "status": m.group(5) or "",
            "empty": sig1.startswith("---[") and sig2.startswith("---["),
        })
    return {"rows": rows}


def _parse_mastering(lines: list[str]) -> dict:
    groups: list[dict] = []
    cur: dict | None = None
    for raw in lines:
        s = raw.rstrip()
        if not s.strip():
            continue
        m = _MGROUP.match(s.strip())
        if m:
            cur = {"group": int(m.group(1)), "rows": []}
            groups.append(cur)
            continue
        if s.strip().startswith("Axis"):
            continue
        text, status = _split_status(s)
        parts = text.split()
        if cur is not None and parts and _MASTER_AXIS.match(parts[0]):
            cur["rows"].append({
                "axis": parts[0],
                "position": " ".join(parts[1:-1]),
                "count": parts[-1],
                "status": status,
            })
    return {"groups": groups}


def _parse_raw(lines: list[str]) -> dict:
    rows = []
    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        sub = re.match(r"^-+\s*(.+?)\s*-+$", s)
        if sub and not _STATUS_RE.match(line):
            rows.append({"text": sub.group(1).strip(), "kind": "subhead"})
            continue
        text, status = _split_status(line)
        text = text.strip()
        kv = _RAW_KV.match(text)
        if kv:
            rows.append({"key": kv.group(1).strip(), "value": kv.group(2).strip(),
                         "status": status, "kind": "kv"})
            continue
        rows.append({
            "text": text,
            "status": status,
            "kind": "row",
            "empty": bool(_EMPTY_SIG.match(text)),
        })
    return {"rows": rows}


def _parse_frames(lines: list[str], is_tool: bool) -> dict:
    """Tool frame / User frame sections -> per-group XYZWPR frames, so the UI
    can render them as pendant-style frame cards (like the Frames tab)."""
    groups: list[dict] = []
    cur_group: dict | None = None
    cur_frame: dict | None = None

    def ensure_group():
        nonlocal cur_group
        if cur_group is None:
            cur_group = {"group": 1, "frames": []}
            groups.append(cur_group)

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        m = _GROUP_LINE.match(s)
        if m:
            cur_group = {"group": int(m.group(1)), "frames": []}
            groups.append(cur_group)
            cur_frame = None
            continue
        ensure_group()
        if is_tool:
            fm = _TOOL_FRAME_START.match(line)
            if fm:
                cur_frame = {"index": int(fm.group(1)), "frame_no": int(fm.group(2)),
                             "signal": fm.group(3).strip(), "status": fm.group(4),
                             "x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0, "p": 0.0, "r": 0.0}
                cur_group["frames"].append(cur_frame)
                continue
        else:
            fm = _USER_FRAME_START.match(line)
            if fm:
                cur_frame = {"index": int(fm.group(1)), "status": fm.group(4) or "",
                             "x": float(fm.group(2)), "w": float(fm.group(3)),
                             "y": 0.0, "z": 0.0, "p": 0.0, "r": 0.0}
                cur_group["frames"].append(cur_frame)
                continue
        if cur_frame is not None:
            axes = _FRAME_AXIS.findall(line)
            if axes:
                for ax, val in axes:
                    cur_frame[ax.lower()] = float(val)
    for g in groups:
        for f in g["frames"]:
            f["used"] = (f["index"] != 0
                         or any(f.get(ax) for ax in ("x", "y", "z", "w", "p", "r"))
                         or (is_tool and not (f.get("signal", "").startswith("---["))))
    return {"groups": groups, "is_tool": is_tool}


def _parse_user_model(lines: list[str]) -> dict:
    """User model -> a list of models, each with its elements as nested
    collapsible sub-entries (element header -> that element's fields). The
    redundant 'Elements' summary table is dropped; the per-element detail
    blocks carry the same Link/Shape/Size info."""
    entries: dict[int, dict] = {}
    order: list[int] = []
    cur_model: dict | None = None
    cur_elem: dict | None = None
    in_list = True

    def ensure(idx: int, comment: str) -> dict:
        e = entries.get(idx)
        if e is None:
            e = entries[idx] = {"index": idx, "comment": comment, "elements": [], "detail": []}
            order.append(idx)
        return e

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        hm = _MODEL_HEAD.match(s)
        if hm:
            cur_model = ensure(int(hm.group(1)), hm.group(2).strip())
            cur_elem = None
            in_list = False
            continue
        if in_list:
            lm = _MODEL_ROW.match(s)
            if lm:
                e = ensure(int(lm.group(1)), lm.group(4).strip())
                e["elem_count"] = int(lm.group(2))
                e["status"] = lm.group(3)
            continue
        if cur_model is None:
            continue
        em = _MODEL_ELEM.match(s)
        if em:
            cur_elem = {"num": int(em.group(1)), "status": em.group(2), "detail": []}
            cur_model["elements"].append(cur_elem)
            continue
        if cur_elem is None:
            continue  # the 'Elements' summary table - redundant with per-element detail
        axes = _AXES_RE.findall(s)
        if len(axes) >= 2:  # "X: .. Y: .. Z: .." -> a structured, colourable row
            cur_elem["detail"].append({"axes": [[a, v] for a, v in axes]})
            continue
        kv = _DETAIL_KV.match(line)
        if kv:
            val = kv.group(2).strip()
            bm = _BRACKET.match(val)
            if bm:
                val = bm.group(1).strip()
            cur_elem["detail"].append({"key": kv.group(1).strip(), "value": val})
            continue
        cur_elem["detail"].append({"raw": s})

    out = []
    for idx in order:
        e = entries[idx]
        e["label"] = e["comment"] or f"No. {idx}"
        e["active"] = bool(e.get("elem_count"))
        e.setdefault("status", "")
        out.append(e)
    return {"entries": out, "preamble": []}


def _classify(title_lower: str) -> str:
    if "position check" in title_lower:
        return "list_detail"
    if "user model" in title_lower:
        return "user_model"
    if "tool frame" in title_lower or "user frame" in title_lower:
        return "frames"
    if "stop position prediction" in title_lower:
        return "grouped_list"
    if "safe i/o connect" in title_lower:
        return "logic"
    if "consistency" in title_lower:
        return "table"
    if "mastering parameter" in title_lower:
        return "table_grouped"
    return "raw"


def _parse_section(kind: str, title_lower: str, lines: list[str]) -> dict:
    if kind == "frames":
        return _parse_frames(lines, is_tool="tool frame" in title_lower)
    if kind == "user_model":
        return _parse_user_model(lines)
    return _PARSERS[kind](lines)


_PARSERS = {
    "list_detail": _parse_list_detail,
    "grouped_list": _parse_grouped_list,
    "logic": _parse_logic,
    "table": _parse_consistency,
    "table_grouped": _parse_mastering,
    "raw": _parse_raw,
}


# -- top level -------------------------------------------------------------


def _split_sections(text: str):
    header_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur: list[str] | None = None
    for raw in text.splitlines():
        m = _SECTION.match(raw)
        if m:
            cur = []
            sections.append((m.group(1).strip(), cur))
        elif cur is None:
            header_lines.append(raw)
        else:
            cur.append(raw)
    return header_lines, sections


def _parse_header(lines: list[str]) -> dict:
    header: dict = {}
    keymap = {"DATE": "date", "DCS Version": "dcs_version", "F Number": "f_number"}
    for raw in lines:
        m = _HEAD_KV.match(raw.strip())
        if m and m.group(1).strip() in keymap:
            header[keymap[m.group(1).strip()]] = m.group(2).strip()
    return header


def _parse_signatures(lines: list[str]) -> list[dict]:
    sigs: list[dict] = []
    for raw in lines:
        m = _SIG.match(raw)
        if m:
            cur, latch = int(m.group(2)), int(m.group(3))
            sigs.append({"name": m.group(1).strip(), "current": cur, "latch": latch,
                         "match": cur == latch})
            continue
        m = _SIG_TIME.match(raw)
        if m and sigs:
            sigs[-1]["current_time"] = m.group(1).strip()
            sigs[-1]["latch_time"] = m.group(2).strip()
    return sigs


def parse_dcs_report(text: str) -> dict:
    header_lines, raw_sections = _split_sections(text)
    header = _parse_header(header_lines)

    signatures: list[dict] = []
    sections: list[dict] = []
    counts = {"ok": 0, "chgd": 0, "ng": 0}

    for title, lines in raw_sections:
        lo = title.lower()
        if lo.startswith("signature"):
            signatures = _parse_signatures(lines)
            continue
        kind = _classify(lo)
        payload = _parse_section(kind, lo, lines)
        summ = _summary(lines)
        for k in counts:
            counts[k] += summ[k]
        payload.update({
            "id": _slug(title),
            "title": title,
            "kind": kind,
            "summary": summ,
        })
        sections.append(payload)

    return {
        "header": header,
        "sections": sections,
        "signatures": signatures,
        "all_signatures_match": bool(signatures) and all(s["match"] for s in signatures),
        "counts": counts,
    }
