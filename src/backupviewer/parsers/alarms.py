"""Alarm history reports (ERRALL.LS, ERRACT.LS, ERRHIST.LS, ...).

Header:  ERRALL.LS      Robot Name DEMOBOT01 09-JUN-26 16:20:54
Rows are quote-delimited:

  32607" 09-JUN-26 16:17:38 " SRVO-037 IMSTP input (Group:2)  " " SERVO   00110110" act"
  seq    datetime             code + message                   cause severity+flags  active

Any line that doesn't decode is collected under "unparsed" - never raises.
"""
from __future__ import annotations

import re

_HEADER = re.compile(r"^(\S+)\s+Robot Name\s+(\S+)\s*(.*?)\s*$")
_CODE = re.compile(r"^([A-Z][A-Z0-9]{1,5})-(\d{3})\s+(.*)$")


def parse_alarm_file(text: str) -> dict:
    lines = text.splitlines()
    out: dict = {"robot_name": "", "exported": "", "rows": [], "unparsed": []}
    start = 0
    if lines:
        m = _HEADER.match(lines[0])
        if m:
            out["robot_name"] = m.group(2)
            out["exported"] = m.group(3)
            start = 1

    for lineno in range(start, len(lines)):
        line = lines[lineno]
        if not line.strip():
            continue
        parts = line.split('"')
        if len(parts) < 5:
            out["unparsed"].append({"line": lineno + 1, "text": line})
            continue
        try:
            seq = int(parts[0].strip())
        except ValueError:
            out["unparsed"].append({"line": lineno + 1, "text": line})
            continue

        datetime_s = parts[1].strip()
        code_msg = parts[2].strip()
        cause = parts[3].strip()
        sev_flags = parts[4].strip() if len(parts) > 4 else ""
        active = len(parts) > 5 and parts[5].strip() == "act"

        m = _CODE.match(code_msg)
        if m:
            facility, number, message = m.group(1), int(m.group(2)), m.group(3).strip()
            code = f"{facility}-{m.group(2)}"
        else:
            facility, number, message, code = "", None, code_msg, ""

        sev_parts = sev_flags.rsplit(None, 1)
        if len(sev_parts) == 2 and re.fullmatch(r"[01]{8}", sev_parts[1]):
            severity, flags = sev_parts[0], sev_parts[1]
        else:
            severity, flags = sev_flags, ""

        out["rows"].append({
            "seq": seq,
            "datetime": datetime_s,
            "code": code,
            "facility": facility,
            "number": number,
            "message": message,
            "cause": cause,
            "severity": severity,
            "flags": flags,
            "active": active,
        })
    return out
