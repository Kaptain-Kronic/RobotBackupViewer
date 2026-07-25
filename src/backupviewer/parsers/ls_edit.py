"""Byte-faithful editing engine for TP program listings (.LS).

The viewer is read-only evidence; editing exports a modified copy to a NEW
folder and never touches the backup. Everything here is pure (no I/O) so the
whole round-trip tests in isolation.

Layout facts this module encodes, measured across 6478 real programs /
378k body lines (zero counter-examples):
- body line   = 4-wide right-justified number + ':' + separator + text + ' ;'
- separator   = 2 spaces, EXCEPT motion lines (J/L/C/A + space) which butt
  straight against the colon ('   3:J P[1] 100% FINE ;')
- blank line  = '   4:   ;' (empty text through the same rule)
- a long statement WRAPS: the first physical line carries the number and NO
  trailing ';', continuations look like '    :  ...' and the LAST physical
  line carries the ' ;'. Unedited wrapped statements pass through verbatim;
  an edited one re-emits as a single long line.
- everything is CRLF.

Editing model: split_sections() cuts the file into a verbatim prefix (through
the /MN line), body records, and a verbatim suffix (/POS.../END). Edits are
tokens over the records - {"ref": i} keeps record i byte-exact except the
renumbered 4-char field, {"text": s} / {"ref": i, "text": s} emit canonically.
apply_attrs()/apply_positions() splice single values inside the pristine
prefix/suffix, preserving every surrounding byte.

Why latin-1 and not parsers/common.read_text? That reader decodes cp1252 with
errors="replace", which is *lossy*: a byte >= 0x80 that isn't a clean cp1252
character becomes U+FFFD and can never be encoded back. Editing has to
round-trip, so decode/encode latin-1 - a lossless 1:1 byte<->codepoint map
(the same trick tools/restyle uses).
"""
from __future__ import annotations

import re


class LsEncodeError(ValueError):
    """A character in the edited text has no FANUC (latin-1) byte."""


class LsEditError(ValueError):
    """An edit cannot be applied to this file (bad target, bad value)."""


def decode_ls(data: bytes) -> str:
    """Raw file bytes -> editable text, byte-exact (latin-1 never fails)."""
    return data.decode("latin-1")


def encode_ls(text: str) -> bytes:
    """Edited text -> file bytes: normalize line endings to CRLF (the .LS
    convention - browsers hand back \\n-only from a <textarea>) and encode
    latin-1. Raises LsEncodeError naming the offending line on any non-latin-1
    character, so a bad edit fails loudly instead of corrupting silently."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    try:
        return normalized.encode("latin-1")
    except UnicodeEncodeError as ex:
        line_no = normalized.count("\n", 0, ex.start) + 1
        ch = normalized[ex.start]
        raise LsEncodeError(
            f"line {line_no}: character {ch!r} (U+{ord(ch):04X}) is not a "
            "FANUC (latin-1) character"
        ) from ex


# ---------------------------------------------------------------------------
# body: split -> tokens -> emit

_NUMBERED = re.compile(r"^\s*(\d+):(.*)$")
_MOTION = re.compile(r"^[JLCA]\s")          # same letter set highlight_tp.js colors
_SEMI = re.compile(r"\s*;\s*$")
_CONT_LEAD = re.compile(r"^\s*:\s{0,2}")


def _split_eol(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line and line[-1] in "\r\n":
        return line[:-1], line[-1]
    return line, ""


def display_text(record: dict) -> str:
    """A record as the editor shows it: no number, no separator, no ';',
    continuations joined into one logical line."""
    parts = [_SEMI.sub("", _strip_sep(record["rest"]))]
    for c in record["cont"]:
        raw, _eol = _split_eol(c)
        parts.append(_SEMI.sub("", _CONT_LEAD.sub("", raw)))
    return " ".join(p for p in parts if p != "") if len(parts) > 1 else parts[0]


def _strip_sep(rest: str) -> str:
    """Drop the after-colon separator: up to 2 leading spaces (motion lines
    have none - the J/L butts against the colon)."""
    if rest.startswith("  "):
        return rest[2:]
    if rest.startswith(" "):
        return rest[1:]
    return rest


def split_sections(text: str) -> dict:
    """Cut a .LS into {prefix, records, suffix}. prefix runs through the /MN
    line verbatim; suffix runs from the section line that ends the body
    (/POS or /END) verbatim to EOF. Each record is one numbered statement:
    {"rest": after-colon text (verbatim, no EOL), "eol", "cont": [verbatim
    physical continuation lines]}. Files with no /MN get mn_missing=True."""
    lines = text.splitlines(keepends=True)
    prefix: list[str] = []
    records: list[dict] = []
    suffix: list[str] = []
    state = "prefix"
    saw_mn = False
    for ln in lines:
        raw, eol = _split_eol(ln)
        if state == "prefix":
            if raw.startswith(("/POS", "/END")):
                state = "suffix"        # no /MN in this file - body goes before here
                suffix.append(ln)
                continue
            prefix.append(ln)
            if raw.rstrip() == "/MN" or raw.split()[:1] == ["/MN"]:
                state = "mn"
                saw_mn = True
            continue
        if state == "mn":
            if raw.startswith("/"):
                state = "suffix"
                suffix.append(ln)
                continue
            m = _NUMBERED.match(raw)
            if m:
                records.append({"rest": m.group(2), "eol": eol or "\r\n", "cont": []})
            elif records:
                records[-1]["cont"].append(ln)   # continuation / stray - verbatim
            else:
                prefix.append(ln)                # blank between /MN and line 1
            continue
        suffix.append(ln)
    return {
        "prefix": "".join(prefix),
        "records": records,
        "suffix": "".join(suffix),
        "mn_missing": not saw_mn,
    }


def body_text(sections: dict) -> str:
    """The whole body as editor text - one display line per statement."""
    return "\n".join(display_text(r) for r in sections["records"])


def emit(sections: dict, tokens: list[dict]) -> str:
    """Reassemble the program with the body described by `tokens`, renumbering
    1..N. {"ref": i} passes record i through byte-exact (only the 4-char
    number field reflows); {"text": s} (with or without ref) emits the
    canonical form measured from real files."""
    width = max(4, len(str(len(tokens))))
    out: list[str] = []
    records = sections["records"]
    for n, tok in enumerate(tokens, 1):
        num = f"{n:>{width}}"
        if "ref" in tok and "text" not in tok:
            i = tok["ref"]
            if not (isinstance(i, int) and 0 <= i < len(records)):
                raise LsEditError(f"body ref {i!r} is out of range")
            r = records[i]
            out.append(f"{num}:{r['rest']}{r['eol']}")
            out.extend(r["cont"])
        else:
            t = str(tok.get("text", ""))
            if "\n" in t or "\r" in t:
                raise LsEditError(f"line {n}: a body line cannot contain a newline")
            sep = "" if _MOTION.match(t) else "  "
            out.append(f"{num}:{sep}{t} ;\r\n")
    mn = "/MN\r\n" if sections.get("mn_missing") and tokens else ""
    return sections["prefix"] + mn + "".join(out) + sections["suffix"]


# ---------------------------------------------------------------------------
# renaming a program

_PROG_LINE = re.compile(r"(?m)^(/PROG[ \t]+)(\S+)(.*)$")
_PROG_NAME_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def rename_program(text: str, new_name: str) -> str:
    """Point a program's /PROG header at `new_name`, preserving the spacing and
    anything after the name (the program type, e.g. '/PROG  FOO  Macro').

    Only /PROG is rewritten. FILE_NAME is deliberately left alone: it is
    vestigial and already disagrees with its own file in the wild (90 of 400
    sampled programs, e.g. a BODYHMIO.LS whose FILE_NAME says HOME_IO), so
    "fixing" it would invent a claim rather than record one. Callers rename the
    FILE itself; CALL/RUN references in OTHER programs are a separate,
    deliberate find/replace - tools/restyle.py works the same way and then
    verifies the header matches the new file name."""
    base = (new_name or "").strip()
    if base.lower().endswith(".ls"):
        base = base[:-3]
    if not _PROG_NAME_OK.match(base):
        raise LsEditError(
            f"{new_name!r} is not a valid program name (letters, digits and "
            "underscore, not starting with a digit)")
    out, n = _PROG_LINE.subn(lambda m: m.group(1) + base + m.group(3), text, count=1)
    if n != 1:
        raise LsEditError("no /PROG header to rename")
    return out


# ---------------------------------------------------------------------------
# /ATTR: value splices on the verbatim prefix

_ATTR_EDITABLE = {"owner", "comment", "protect"}
_PROTECT_VALUES = {"READ_WRITE", "READ"}


def apply_attrs(prefix: str, edits: dict) -> str:
    """Replace attribute VALUES inside the pristine header, byte-preserving
    everything around them ('OWNER\\t\\t= MNEDITOR;' keeps its tabs). Only
    owner/comment/protect are editable; the derived sizes and timestamps are
    the controller's numbers, not ours to invent."""
    for key, value in edits.items():
        k = str(key).lower()
        if k not in _ATTR_EDITABLE:
            raise LsEditError(f"attribute {key!r} is not editable")
        v = str(value)
        if k == "comment":
            if '"' in v:
                raise LsEditError('a comment cannot contain a quote (")')
            repl = f'"{v}"'
        elif k == "protect":
            if v not in _PROTECT_VALUES:
                raise LsEditError(f"protect must be one of {sorted(_PROTECT_VALUES)}")
            repl = v
        else:
            if not v or re.search(r"[;\s]", v):
                raise LsEditError("owner must be one word without ';'")
            repl = v
        pat = re.compile(r"(?m)^(%s[ \t]*=[ \t]*)([^\r\n]*?)(;[ \t]*\r?)$" % k.upper())
        new, n = pat.subn(lambda m: m.group(1) + repl + m.group(3), prefix, count=1)
        if n != 1:
            raise LsEditError(f"attribute {k.upper()} not found in this program")
        prefix = new
    return prefix


# ---------------------------------------------------------------------------
# /POS: value splices on the verbatim suffix

_P_HEAD = re.compile(r'^P\[(\d+)(:"([^"]*)")?\]\s*\{')
_GP_LINE = re.compile(r"^\s*GP(\d+):")
_NUM_OK = re.compile(r"^-?\d+(\.\d+)?$")


def _fmt_pos(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise LsEditError(f"position value {value!r} is not a number")
    if f != f or f in (float("inf"), float("-inf")):
        raise LsEditError("position value must be finite")
    return f"{f:.3f}"       # 3 decimals: unanimous across 30k measured values


def apply_positions(suffix: str, edits: list[dict]) -> str:
    """Splice edited position fields into the pristine /POS block. Each edit:
    {"id": P-number, "gp": group, "field": x|y|z|w|p|r|j1..j9|uf|ut|config|
    comment, "value": ...}. Masked '********' fields are editable like any
    other - they are points placed logically but never initialized with data,
    and typing a value initializes them. Every byte outside the replaced
    value is preserved."""
    lines = suffix.splitlines(keepends=True)
    for e in edits:
        pid, gp = int(e.get("id", -1)), int(e.get("gp", 1))
        field = str(e.get("field", "")).lower()
        value = e.get("value")
        done = False
        cur_p, cur_gp = None, 1
        for idx, ln in enumerate(lines):
            raw, eol = _split_eol(ln)
            hm = _P_HEAD.match(raw.strip())
            if hm:
                cur_p, cur_gp = int(hm.group(1)), 1
                if cur_p == pid and field == "comment":
                    c = str(value)
                    if '"' in c:
                        raise LsEditError('a position comment cannot contain "')
                    head = f'P[{pid}:"{c}"]{{' if c else f"P[{pid}]{{"
                    lines[idx] = raw.replace(raw.strip(), head, 1) + eol
                    done = True
                    break
                continue
            gm = _GP_LINE.match(raw)
            if gm:
                cur_gp = int(gm.group(1))
                continue
            if cur_p != pid or cur_gp != gp:
                continue
            new = _splice_pos_field(raw, field, value)
            if new is not None:
                lines[idx] = new + eol
                done = True
                break
        if not done:
            raise LsEditError(
                f"P[{pid}] GP{gp} {field} not found in this program's /POS data")
    return "".join(lines)


def _splice_pos_field(raw: str, field: str, value) -> str | None:
    """Replace one field's value inside one physical /POS line; None when the
    field is not on this line. Only the value token changes."""
    if field in ("x", "y", "z", "w", "p", "r"):
        pat = re.compile(r"\b%s(\s*=\s*)(\S+)(\s+(?:mm|deg))" % field.upper())
        if pat.search(raw):
            return pat.sub(lambda m: field.upper() + m.group(1) + _fmt_pos(value) + m.group(3),
                           raw, count=1)
        return None
    if re.fullmatch(r"j\d", field):
        pat = re.compile(r"\bJ%s(\s*=\s*)(\S+)(\s+deg)" % field[1])
        if pat.search(raw):
            return pat.sub(lambda m: "J" + field[1] + m.group(1) + _fmt_pos(value) + m.group(3),
                           raw, count=1)
        return None
    if field in ("uf", "ut"):
        v = str(value).strip().upper()
        if not re.fullmatch(r"\d+|F", v):
            raise LsEditError(f"{field} must be a number or F, got {value!r}")
        pat = re.compile(r"\b%s(\s*:\s*)(\S+?)(,)" % field.upper())
        if pat.search(raw):
            return pat.sub(lambda m: field.upper() + m.group(1) + v + m.group(3),
                           raw, count=1)
        return None
    if field == "config":
        v = str(value)
        if "'" in v:
            raise LsEditError("config cannot contain a quote (')")
        pat = re.compile(r"(CONFIG\s*:\s*')([^']*)(')")
        if pat.search(raw):
            return pat.sub(lambda m: m.group(1) + v + m.group(3), raw, count=1)
        return None
    raise LsEditError(f"unknown position field {field!r}")
