"""Backup-wide search.

Queries come in two shapes:
- signal/register form 'TYPE[N]' (DI[279], R[151], PR[5]...) - matched
  structurally against IO/registers and as 'TYPE[N]' / 'TYPE[N:' prefixes in
  program text (TP notation). Long .DG names are aliased to pendant names
  first: DIN[279] -> DI[279].
- free text - case-insensitive substring across program lines, comments,
  names.
"""
from __future__ import annotations

import re

ALIAS = {"DIN": "DI", "DOUT": "DO", "GIN": "GI", "GOUT": "GO",
         "AIN": "AI", "AOUT": "AO", "FLG": "F"}

_SIG_QUERY = re.compile(r"^([A-Za-z]+)\s*\[\s*(\d+)\s*\]?$")

MAX_PROGRAMS = 40
MAX_HITS_PER_PROGRAM = 8
MAX_ROWS = 100


def normalize_query(query: str):
    """-> (canonical_text, sig) where sig = (TYPE, N) for TYPE[N] queries."""
    q = query.strip()
    m = _SIG_QUERY.match(q)
    if not m:
        return q, None
    t = ALIAS.get(m.group(1).upper(), m.group(1).upper())
    n = int(m.group(2))
    return f"{t}[{n}]", (t, n)


def _search_program_text(name: str, text: str, canonical: str, sig) -> dict | None:
    hits = []
    count = 0
    if sig:
        needles = (canonical, canonical[:-1] + ":")  # DO[135] and DO[135:
    in_mn = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.startswith("/MN"):
            in_mn = True
        if sig:
            matched = any(n in line for n in needles)
        else:
            matched = canonical.lower() in line.lower()
        if matched:
            count += 1
            if len(hits) < MAX_HITS_PER_PROGRAM:
                hits.append({"line": lineno, "text": line.strip()[:160], "in_body": in_mn})
    if not count:
        return None
    return {"program": name, "count": count, "hits": hits}


def search_backup(query: str, *, program_texts: dict[str, str],
                  io_signals: list[dict], registers: dict[str, list[dict]],
                  frames_model: dict | None, macros: list[dict],
                  file_names: list[str]) -> dict:
    canonical, sig = normalize_query(query)
    if not canonical:
        return {"query": query, "canonical": "", "total": 0}
    low = canonical.lower()

    programs = []
    for name in sorted(program_texts):
        r = _search_program_text(name, program_texts[name], canonical, sig)
        if r:
            programs.append(r)
    programs.sort(key=lambda r: -r["count"])
    programs = programs[:MAX_PROGRAMS]

    io_hits = []
    for s in io_signals:
        if sig:
            ok = s["type"] == sig[0] and s["index"] == sig[1]
        else:
            ok = low in s["comment"].lower() or low in f"{s['type'].lower()}[{s['index']}]"
        if ok:
            io_hits.append(s)
            if len(io_hits) >= MAX_ROWS:
                break

    reg_hits = []
    reg_kind_names = {"num": "R", "pos": "PR", "str": "SR"}
    for kind, regs in registers.items():
        kname = reg_kind_names.get(kind, kind)
        for r in regs:
            if sig:
                ok = kname == sig[0] and r["index"] == sig[1]
            else:
                ok = (low in (r.get("comment") or "").lower()
                      or low in str(r.get("value", "")).lower())
            if ok:
                reg_hits.append({"kind": kname, "index": r["index"],
                                 "group": r.get("group"),
                                 "comment": r.get("comment", ""),
                                 "value": r.get("value")})
                if len(reg_hits) >= MAX_ROWS:
                    break

    frame_hits = []
    if frames_model and not sig:
        for kind, label in (("tools", "tool"), ("frames", "uframe"), ("jogs", "jog")):
            for group, entries in (frames_model.get(kind) or {}).items():
                for e in entries:
                    if e.get("comment") and low in e["comment"].lower():
                        frame_hits.append({"kind": label, "group": group,
                                           "index": e["index"], "comment": e["comment"]})

    macro_hits = []
    if not sig:
        for m in macros:
            if low in m["name"].lower() or low in m["prog_name"].lower():
                macro_hits.append(m)

    file_hits = [] if sig else [n for n in file_names if low in n.lower()][:MAX_ROWS]

    total = (sum(p["count"] for p in programs) + len(io_hits) + len(reg_hits)
             + len(frame_hits) + len(macro_hits) + len(file_hits))
    return {
        "query": query,
        "canonical": canonical,
        "is_signal": bool(sig),
        "programs": programs,
        "io": io_hits,
        "registers": reg_hits,
        "frames": frame_hits,
        "macros": macro_hits,
        "files": file_hits,
        "total": total,
    }
