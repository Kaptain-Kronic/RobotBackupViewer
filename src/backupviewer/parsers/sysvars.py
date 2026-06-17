"""System-variable browser model over SYSTEM.VA (the controller's full $-var
dump - ~769 top-level records).

Each record is `[*SYSTEM*]$NAME  Storage: S  Access: A  : TYPEDECL` followed by
an indentation-structured body:
    Field: $NAME[idx].$FLD Access: RW: TYPE = val   (struct field, scalar)
    Field: $NAME[idx].$FLD  ARRAY[n] OF TYPE        (struct field = array)
      [1] = v                                        (its elements)
    [1] = v   /  [1,2] = v                           (plain array elements)

`records()` is cheap (just the VaRecord list); `summarize()` builds the
top-level list shown up front; `record_tree()` expands ONE record into a
nested collapsible tree on demand (so the 5 MB file isn't shipped at once).
Values are kept as their exact text (Uninitialized, 0.000000e+00, 'str  ')
rather than coerced - this is a browser, fidelity matters.
"""
from __future__ import annotations

import re

from .va import VaRecord, iter_records

# The field name is normally $-prefixed ($SYSTEM struct fields: .$FLD) but KAREL
# program structs dump plain field names (DEF_GRIP.GRIP_ID, MH_GRIPPERS[1,1].GRIP_ID)
# - the '$' must be optional or those lines fall through to the verbatim branch and
# the whole "Field: ..." line gets shown as an entry name.
_FIELD_LINE = re.compile(
    r"^\s*Field:\s+\$?\w+(?:\[([\d,\s]+)\])?\.\$?(\w+)(?:\s+Access:\s+\w+)?:?\s*(.*)$"
)
_INDEXED = re.compile(r"^\s*\[([\d,\s]+)\]\s*=\s*(.*)$")


def records(text: str) -> list[VaRecord]:
    return list(iter_records(text))


def _inline_value(rec: VaRecord) -> str | None:
    if "=" in rec.typedecl:
        return rec.typedecl.split("=", 1)[1].strip()
    return None


def summarize(rec: VaRecord) -> dict:
    """One row for the top-level list (no body parsed)."""
    has_body = any(l.strip() for l in rec.lines)
    item = {
        "name": rec.name,
        "section": rec.section,
        "storage": rec.storage,
        "access": rec.access,
        "has_children": has_body,
    }
    if not has_body:
        v = _inline_value(rec)
        item["value"] = v if v is not None else ""
        item["type"] = (rec.typedecl.split("=", 1)[0].strip()
                        if "=" in rec.typedecl else rec.typedecl)
    else:
        item["value"] = None
        item["type"] = rec.typedecl
    return item


def record_tree(rec: VaRecord) -> dict:
    """Expand one record into {name, type, value?, leaf?, children:[...]}."""
    node: dict = {
        "name": rec.name, "type": rec.typedecl,
        "storage": rec.storage, "access": rec.access,
    }
    if not any(l.strip() for l in rec.lines):
        node["value"] = _inline_value(rec) or ""
        node["leaf"] = True
        return node

    children: list[dict] = []
    elems: dict[str, dict] = {}   # struct-array element idx -> node
    field_array: dict | None = None  # a Field that's an array, collecting [i]=v

    for raw in rec.lines:
        if not raw.strip():
            continue
        fm = _FIELD_LINE.match(raw)
        if fm:
            field_array = None
            idx, fname, rest = fm.group(1), fm.group(2), fm.group(3).strip()
            if idx:
                if idx not in elems:
                    en = {"name": "[" + idx.strip() + "]", "children": []}
                    elems[idx] = en
                    children.append(en)
                bucket = elems[idx]["children"]
            else:
                bucket = children
            if "=" in rest:
                typ, _, val = rest.partition("=")
                bucket.append({"name": fname, "type": typ.strip(),
                               "value": val.strip(), "leaf": True})
            elif "ARRAY[" in rest:
                fa = {"name": fname, "type": rest, "children": []}
                bucket.append(fa)
                field_array = fa
            else:
                bucket.append({"name": fname, "type": rest, "value": "", "leaf": True})
            continue
        im = _INDEXED.match(raw)
        if im:
            bucket = field_array["children"] if field_array is not None else children
            bucket.append({"name": "[" + im.group(1).strip() + "]",
                           "value": im.group(2).strip(), "leaf": True})
            continue
        # group/position/other line - keep verbatim so nothing is hidden
        children.append({"name": raw.strip(), "raw": True, "leaf": True})

    node["children"] = children
    return node


def flatten(rec: VaRecord) -> dict[str, str]:
    """{full.path -> value} over all leaves of a record - for variable diffing.
    Array indices stick to their parent ([1]); fields join with '.'."""
    out: dict[str, str] = {}

    def walk(node: dict, prefix: str):
        if node.get("leaf"):
            out[prefix] = node.get("value", "")
            return
        for c in node.get("children", []):
            nm = c["name"]
            sep = "" if nm.startswith("[") else "."
            walk(c, (prefix + sep + nm) if prefix else nm)

    tree = record_tree(rec)
    if tree.get("leaf"):
        out[rec.name] = tree.get("value", "")
    else:
        for c in tree.get("children", []):
            nm = c["name"]
            sep = "" if nm.startswith("[") else "."
            walk(c, rec.name + sep + nm)
    return out

