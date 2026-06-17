"""Program call graph extracted from .LS bodies.

Three ways a TP program invokes another:
    CALL HOMFRPON            (also mid-line: IF R[151]=15,CALL HOMFRPON)
    RUN SWAXTSK1             (separate task)
    REQUEST CONTINUE         (a macro invoked by its macro NAME - resolved
                              through the macro table to its program)
"""
from __future__ import annotations

import re

_BODY = re.compile(r"^\s*(\d+):\s{0,2}(.*?)\s*;?\s*$")
_CALL = re.compile(r"\b(CALL|RUN)\s+([A-Z][A-Z0-9_]*)")
_MACRO_ARGS = re.compile(r"\s*\(.*\)\s*$")


def _macro_name(body: str) -> str:
    """Bare macro name from a TP line, dropping a trailing '(args)'. Macro names
    can contain spaces ('SET SEGMENT'), and may be invoked with arguments
    ('SET SEGMENT(72)'), so match the table by stripping the argument list."""
    return _MACRO_ARGS.sub("", body.strip())


def extract_calls(text: str, macro_by_name: dict[str, str]) -> list[dict]:
    """[{target, kind: call|run|macro, count, first_line}] for one program."""
    found: dict[tuple[str, str], dict] = {}
    in_mn = False
    for line in text.splitlines():
        if line.startswith("/MN"):
            in_mn = True
            continue
        if line.startswith(("/POS", "/END")):
            break
        if not in_mn:
            continue
        m = _BODY.match(line)
        if not m:
            continue
        lineno, body = int(m.group(1)), m.group(2)
        if body.lstrip().startswith("!"):
            continue
        for kind, target in _CALL.findall(body):
            key = (target, kind.lower())
            e = found.setdefault(key, {"target": target, "kind": kind.lower(),
                                       "count": 0, "first_line": lineno})
            e["count"] += 1
        t = _macro_name(body)
        if t and t in macro_by_name:
            target = macro_by_name[t]
            key = (target, "macro")
            e = found.setdefault(key, {"target": target, "kind": "macro",
                                       "count": 0, "first_line": lineno, "macro_name": t})
            e["count"] += 1
    return sorted(found.values(), key=lambda e: e["first_line"])


def line_hops(text: str, macro_by_name: dict[str, str],
              program_stems: set[str]) -> dict[int, list[dict]]:
    """Per /MN line, the program/macro hops it contains, so the source viewer
    can make `CALL FOO` / `RUN BAR` names and bare macro-name lines clickable.

    {line_n: [{target, kind, name, exists}]} where `name` is the literal token
    to wrap (the called name) and `exists` is whether the target is a program
    in this backup.
    """
    hops: dict[int, list[dict]] = {}
    in_mn = False
    for line in text.splitlines():
        if line.startswith("/MN"):
            in_mn = True
            continue
        if line.startswith(("/POS", "/END")):
            break
        if not in_mn:
            continue
        m = _BODY.match(line)
        if not m:
            continue
        lineno, body = int(m.group(1)), m.group(2)
        if body.lstrip().startswith("!"):
            continue
        found: list[dict] = []
        for kind, target in _CALL.findall(body):
            found.append({"target": target, "kind": kind.lower(), "name": target,
                          "exists": target.upper() in program_stems})
        t = _macro_name(body)
        if t and t in macro_by_name:
            target = macro_by_name[t]
            found.append({"target": target, "kind": "macro", "name": t,
                          "exists": target.upper() in program_stems})
        if found:
            hops[lineno] = found
    return hops


def build_call_graph(program_texts: dict[str, str], macro_by_name: dict[str, str]) -> dict:
    """{"calls": {prog: [edges]}, "called_by": {prog: [{caller, count}]}}"""
    calls: dict[str, list[dict]] = {}
    called_by: dict[str, list[dict]] = {}
    for name, text in program_texts.items():
        edges = extract_calls(text, macro_by_name)
        for e in edges:
            e["exists"] = e["target"] in program_texts
        calls[name] = edges
        for e in edges:
            called_by.setdefault(e["target"], []).append({"caller": name, "count": e["count"]})
    for lst in called_by.values():
        lst.sort(key=lambda x: x["caller"])
    return {"calls": calls, "called_by": called_by}
