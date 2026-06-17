"""GM Global wizard log (GMWIZLOG.DT) - the setup wizard's Q&A transcript.

    ******  GM Global 4 Wizard Log File   *****
    Executed On:      22-MAY-26 14:08
    Custo Version:    GM Global 4 Rev00
    ...
    Q- Is gun#1 Eq#1 a Servo Gun?
      Ans:YES
    **- [GMSPINIO]Spin_IO_Done FAILED,Status =16011 -**

Output keeps entry order: qa / event / failure entries plus a header dict.
The Custo Version here is the UNTRUNCATED customization string (SUMMARY.DG
drops its first character - controller-side bug).
"""
from __future__ import annotations

import re

_BANNER = re.compile(r"^\*{4,}.*$")
_FAILURE = re.compile(r"^\*\*-\s*\[(\w+)\]\s*(\S+)\s+FAILED\s*,\s*Status\s*=\s*(\d+)\s*-\*\*\s*$")
_HEADER_KEYS = {
    "Executed On": "executed_on",
    "Robot Type": "robot_type",
    "OS Name": "os_name",
    "OS Version": "os_version",
    "Custo Version": "custo_version",
    "Wizard Version": "wizard_version",
    "Robot FNumber": "f_number",
}
_KV = re.compile(r"^([A-Za-z ]+?):\s*(.+?)\s*$")


def parse_gmwizlog(text: str) -> dict:
    header: dict = {}
    entries: list[dict] = []
    failures = 0
    pending_q: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or _BANNER.match(s):
            continue

        m = _FAILURE.match(s)
        if m:
            pending_q = None
            failures += 1
            entries.append({
                "kind": "failure",
                "prog": m.group(1),
                "label": m.group(2),
                "status": int(m.group(3)),
            })
            continue

        if s.startswith("Q-"):
            pending_q = s[2:].strip()
            continue

        if pending_q is not None and s.startswith("Ans:"):
            entries.append({"kind": "qa", "q": pending_q, "a": s[4:].strip()})
            pending_q = None
            continue

        m = _KV.match(s)
        if m and m.group(1).strip() in _HEADER_KEYS:
            header[_HEADER_KEYS[m.group(1).strip()]] = m.group(2)
            continue

        # a question without an Ans line directly after still records the event
        if pending_q is not None:
            entries.append({"kind": "qa", "q": pending_q, "a": ""})
            pending_q = None
        entries.append({"kind": "event", "text": s, "indent": len(raw) - len(raw.lstrip())})

    if pending_q is not None:
        entries.append({"kind": "qa", "q": pending_q, "a": ""})

    return {"header": header, "entries": entries, "failures": failures}
