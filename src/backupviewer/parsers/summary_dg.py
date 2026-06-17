"""Parser for SUMMARY.DG - the controller's self-describing snapshot.

SUMMARY.DG is pseudo-HTML: sections are delimited by
    <H2><A NAME="n">Title</A></H2>
with bodies wrapped in <PRE> tags. Bodies are plain fixed-width text.
Every structured sub-parser is tolerant: unknown lines are simply skipped,
and the raw (tag-stripped) section text is always preserved in "sections"
so nothing is ever hidden from the user by a parse gap.
"""
from __future__ import annotations

import re

_SECTION = re.compile(r'<H2><A NAME="(\d+)">([^<]+)</A></H2>')
_TAG = re.compile(r"<[^>]+>")
_KV = re.compile(r"^(.+?)\s*:\s*(.*)$")
_ORD_NO = re.compile(r"^[A-Z0-9]{4}$")
_MODEL = re.compile(r"^([A-Z]{1,3}-\d+i[A-Z]\S*)\s")
_POOL = re.compile(r"^(\w+)\s+([\d.]+)KB\s+([\d.]+)KB\s+([\d.]+)KB\s*$")
_HW = re.compile(r"^(\w+)\s+(\S+)\s*$")
_TASK = re.compile(r"^\s*(\d+)\s+(\S+)\s+(PAUSED|RUNNING|ABORTED|ABORTING|HELD)\s+@\s+(\d+)\s+in\s+(\S+)\s+of\s+(\S+)")
_SAFETY = re.compile(r"^(.+?)\s{2,}(TRUE|FALSE)\s*$")
_HOST = re.compile(r"^Host Table \[(\d+)\]:\s+(\S+)(?:\s+addr:\s*(\S+))?\s*$")
_SERVER = re.compile(r"^Server \[(\d+)\]:\s+(\S+)\s+state:\s*(\S+)\s*$")
_JOINT = re.compile(r"^Joint\s+(\d+):\s+(-?[\d.]+)\s*$")
_AXISVAL = re.compile(r"^([XYZWPR]):\s+(-?[\d.]+)\s*$")
_FRAME_TOOL = re.compile(r"^Frame #:\s*(\d+)\s+Tool #:\s*(\d+)")
_MACRO_NAME = re.compile(r"^\[\s*(\d+)\]NAME\s*:(.*)$")
_MOTOR = re.compile(r"^\s*(\d+)\s+(\d+)\s+(.+?)\s*$")


def parse_summary(text: str) -> dict:
    sections = _split_sections(text)
    by_title = {s["title"].lower(): s["text"] for s in sections}

    def sec(*needles: str) -> str:
        for title, body in by_title.items():
            if any(n in title for n in needles):
                return body
        return ""

    version_text = sec("version")
    identity, options, options_demo, motors, servo_params = _parse_version_section(version_text)
    ethernet = _parse_ethernet(sec("ethernet"))
    if not identity.get("robot_name"):
        identity["robot_name"] = ethernet.get("hostname", "")

    return {
        "sections": sections,
        "identity": identity,
        "options": options,
        "options_demo": options_demo,
        "motors": motors,
        "servo_params": servo_params,
        "memory": _parse_memory(sec("memory")),
        "tasks": _parse_tasks(sec("program status")),
        "safety": _parse_safety(sec("safety")),
        "positions": _parse_positions(sec("current position")),
        "ethernet": ethernet,
        "ports": _parse_ports(sec("port setup")),
        "macros": parse_macro_section(sec("macro setup")),
    }


def io_section_texts(text: str) -> tuple[str | None, str | None]:
    """(io_status_body, io_config_body) raw section texts from SUMMARY.DG.

    Backup formats without IOCONFIG.DG/IOSTATE.DG (all-of-the-above,
    maintenance data) still carry the full signal tables in these sections -
    same line formats, including the '::' marker the io_dg parsers key on.
    """
    status = config = None
    for s in _split_sections(text):
        title = s["title"].lower()
        if "i/o status" in title:
            status = s["text"]
        elif "i/o configuration" in title:
            config = s["text"]
    return status, config


def _split_sections(text: str) -> list[dict]:
    matches = list(_SECTION.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = _TAG.sub("", text[start:end])
        sections.append({
            "id": int(m.group(1)),
            "title": m.group(2).strip(),
            "text": body.strip("\n"),
        })
    return sections


def _parse_version_section(text: str):
    identity: dict = {}
    options: list[dict] = []
    options_demo: list[dict] = []
    motors: list[dict] = []
    servo_params: list[dict] = []
    kv_extra: list[dict] = []
    mode = "head"

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if s.endswith("::"):
            marker = s[:-2].strip().upper()
            mode = {
                "VERSION INFORMATION": "versions",
                "CONFIG": "options",
                "CONFIG2": "options_demo",
                "MOTOR": "motors",
                "SERVO": "servo",
            }.get(marker, "skip")
            continue
        if not s:
            continue

        if mode == "head":
            m = _KV.match(s)
            if m:
                key, val = m.group(1).strip(), m.group(2).strip()
                if key == "F Number":
                    identity["f_number"] = val
                elif key == "VERSION":
                    identity["application"] = val
                elif key == "$VERSION":
                    parts = val.split(None, 1)
                    identity["version"] = parts[0]
                    identity["version_date"] = parts[1].strip() if len(parts) > 1 else ""
                elif key == "DATE":
                    identity["backup_date"] = val
        elif mode == "versions":
            if s in ("SOFTWARE:", "ID:") or s.startswith("SOFTWARE:"):
                continue
            mdl = _MODEL.match(s)
            if mdl and "robot_model" not in identity:
                identity["robot_model"] = mdl.group(1)
                continue
            m = _KV.match(s)
            if m and len(m.group(1).strip()) > 1:
                key, val = m.group(1).strip(), m.group(2).strip()
                known = {
                    "S/W Serial No.": "serial_no",
                    "Controller ID": "controller_id",
                    "Robot No.": "robot_no",
                    "Servo Code": "servo_code",
                    "DCS": "dcs_version",
                    "Software Edition No.": "software_edition",
                    "Teach Pendant": "teach_pendant",
                    "Update Version": "update_version",
                    "Customization Ver.": "customization",
                    "Stop pattern": "stop_pattern",
                }
                if key in known:
                    identity[known[key]] = val
                else:
                    kv_extra.append({"key": key, "value": val})
        elif mode in ("options", "options_demo"):
            if s.startswith(("FEATURE:", "DEMO. FEATURE:")):
                continue
            parts = s.rsplit(None, 1)
            target = options if mode == "options" else options_demo
            if len(parts) == 2 and _ORD_NO.match(parts[1]):
                target.append({"feature": parts[0].strip(), "ord_no": parts[1]})
            else:
                target.append({"feature": s, "ord_no": ""})
        elif mode == "motors":
            if s.startswith("GR:"):
                continue
            m = _MOTOR.match(line)
            if m:
                motors.append({"group": int(m.group(1)), "axis": int(m.group(2)), "info": m.group(3)})
        elif mode == "servo":
            if s.startswith("GROUP:"):
                continue
            m = _MOTOR.match(line)
            if m:
                servo_params.append({"group": int(m.group(1)), "axis": int(m.group(2)), "param_id": m.group(3)})

    identity["extra"] = kv_extra
    return identity, options, options_demo, motors, servo_params


def _parse_memory(text: str) -> dict:
    out: dict = {}
    device = None
    in_hw = False
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^MEMORY DETAIL \((\w+)\):", s)
        if m:
            device = m.group(1)
            out[device] = {"pools": [], "hardware": {}}
            in_hw = False
            continue
        if device is None or not s or s.startswith("Pools") or s.endswith("::"):
            continue
        if s == "HARDWARE":
            in_hw = True
            continue
        if in_hw:
            m = _HW.match(s)
            if m:
                out[device]["hardware"][m.group(1)] = m.group(2)
            continue
        m = _POOL.match(s)
        if m:
            out[device]["pools"].append({
                "name": m.group(1),
                "total_kb": float(m.group(2)),
                "avail_kb": float(m.group(3)),
                "largest_kb": float(m.group(4)),
            })
    return out


def _parse_tasks(text: str) -> list[dict]:
    tasks = []
    for raw in text.splitlines():
        m = _TASK.match(raw)
        if m:
            tasks.append({
                "num": int(m.group(1)),
                "name": m.group(2),
                "status": m.group(3),
                "line": int(m.group(4)),
                "routine": m.group(5),
                "program": m.group(6),
            })
    return tasks


def _parse_safety(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.endswith("::"):
            continue
        m = _SAFETY.match(s)
        if m:
            out.append({"signal": m.group(1).strip(), "value": m.group(2) == "TRUE"})
    return out


def _parse_positions(text: str) -> list[dict]:
    groups: list[dict] = []
    cur: dict | None = None
    target: dict | None = None
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^Group #:\s*(\d+)", s)
        if m:
            cur = {"group": int(m.group(1)), "joints": [], "frame_no": None,
                   "tool_no": None, "userframe": None, "world": None}
            groups.append(cur)
            target = None
            continue
        if cur is None:
            continue
        m = _JOINT.match(s)
        if m:
            cur["joints"].append({"joint": int(m.group(1)), "deg": float(m.group(2))})
            continue
        m = _FRAME_TOOL.match(s)
        if m:
            cur["frame_no"], cur["tool_no"] = int(m.group(1)), int(m.group(2))
            continue
        if s.startswith("CURRENT USER FRAME"):
            target = cur["userframe"] = {}
            continue
        if s.startswith("CURRENT WORLD"):
            target = cur["world"] = {}
            continue
        if target is not None:
            if s.startswith("CFG:"):
                target["config"] = s[4:].strip()
                continue
            if "cannot be calculated" in s:
                target["unavailable"] = True
                continue
            m = _AXISVAL.match(s)
            if m:
                target[m.group(1).lower()] = float(m.group(2))
    return groups


def _parse_ethernet(text: str) -> dict:
    out: dict = {"hosts": [], "servers": [], "extra": []}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.endswith("::"):
            continue
        m = _HOST.match(s)
        if m:
            out["hosts"].append({"slot": int(m.group(1)), "name": m.group(2), "addr": m.group(3) or ""})
            continue
        m = _SERVER.match(s)
        if m:
            out["servers"].append({"slot": int(m.group(1)), "protocol": m.group(2), "state": m.group(3)})
            continue
        m = _KV.match(s)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            known = {
                "$HOSTNAME": "hostname",
                "TMI_ROUTER": "router",
                "TMI_ETHERAD": "mac",
                "Subnet mask": "subnet",
            }
            if key in known:
                out[known[key]] = val
            else:
                out["extra"].append({"key": key, "value": val})
    return out


def _parse_ports(text: str) -> list[dict]:
    ports: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^DEVICE:\s*(.+?):?\s*$", s)
        if m and raw.startswith("DEVICE"):
            cur = {"device": m.group(1), "settings": {}}
            ports.append(cur)
            continue
        if cur is None or not s or s.endswith("::"):
            continue
        m = _KV.match(s)
        if m:
            cur["settings"][m.group(1).strip()] = m.group(2).strip()
    return ports


def parse_macro_section(text: str) -> list[dict]:
    """Section 10 macro table: readable assign types (DI/MF/--), unlike SYSMACRO.VA."""
    macros: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        m = _MACRO_NAME.match(raw.strip())
        if m:
            cur = {"index": int(m.group(1)), "name": m.group(2).strip(),
                   "prog_name": "", "assign_type": "", "assign_id": None, "system": False}
            macros.append(cur)
            continue
        if cur is None:
            continue
        s = raw.strip()
        if s == "SYSTEM MACRO":
            cur["system"] = True
            continue
        m = _KV.match(s)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if key == "PROG NAME":
                cur["prog_name"] = val
            elif key == "Assign type":
                cur["assign_type"] = "" if val == "--" else val
            elif key == "Assign ID":
                try:
                    cur["assign_id"] = int(val)
                except ValueError:
                    cur["assign_id"] = None
    return macros
