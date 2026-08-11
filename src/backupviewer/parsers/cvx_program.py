"""Keyence CV-X inspection programs: the tool names and the calculation logic
a technician actually wrote, read out of `inspect.dat`.

Evidence basis, 2026-08-11. `inspect.dat` is an "ST" container whose payload is
a handful of zlib blocks (98.7% of the file), stored twice - a working copy
and a recovery copy (see `blocks`, which keeps one of each pair). The blocks are the program's
parameter memory: sparse, mostly zero, with two regions that are plain,
self-describing text.

**Tool names** are a length-prefixed language table:

    (u32 language_index, u32 byte_length, <byte_length bytes>) repeated,
    language_index ascending, empty languages present with length 0.

Read off two independently renamed tools in a controlled experiment (a known
string was typed into one tool's English name in the vendor's own simulator and
found at both records, with every other language slot intact around it). Slot 1
is English - the slot a technician types and reads - which is the same rule
`cvx_inspect` uses for the program name. Japanese and Chinese slots are cp932.

**Calculation scripts** are the CV-X's own expression language, stored as plain
ASCII lines: `@local` variables, `Tnnn.RSLT.<MNEMONIC>[i]:MS` references to
another tool's result, `ANSn` outputs, IF/ELSEIF/ENDIF, and `'` comments. The
same controlled experiment put a known string into two script comments and found
them verbatim. Real cameras carry 68-102 lines of this per program.

What could NOT be proved, and is therefore not claimed here:
- **Which tool a name record belongs to.** The records appear in a plausible
  order but no tool number or type code has been tied to a name record, so this
  module returns names as a list and never labels one "tool 5".
- **Which tool owns a script.** Scripts sit in the same block as the names;
  nothing observed links a script to a specific unit. A script's own leading
  comment often says (a technician's habit), and that is left as text for a
  human to read - never parsed into a claim.
- The numeric parameter slots (float64 at an offset 6 mod 8, with a repeated
  ~1e12 sentinel for "unset"). One slot was identified by experiment; the map
  from slot to setting is unknown, so no values are surfaced.
"""

import re
import struct
import zlib

# a language slot's text is capped well above any real tool name; a longer
# "length" means we are not looking at a name record at all
MAX_NAME_BYTES = 512
# a name record must carry at least this many language slots to be believed -
# a stray pair of plausible u32s cannot fake a run this long
MIN_LANG_SLOTS = 4
# the vendor's language order, indexed by the record's own language_index.
# Only the slots proved by the experiment are named; the rest stay honest.
LANGUAGES = {
    0: "japanese",
    1: "english",
    2: "german",
    4: "chinese (simplified)",
    7: "french",
    8: "italian",
}
ENGLISH = 1

# a script line is believed only if it matches the language's own grammar
_SCRIPT_LINE = re.compile(
    r"""(?x)
    ^\s*(?:
        '.*                                   # a comment
      | @[A-Za-z_]\w*\s*=.*                   # assignment to a local
      | ANS\d+\s*=.*                          # an output assignment
      | (?:IF|ELSEIF)\b.*\bTHEN\b.*           # a conditional
      | (?:ELSE|ENDIF|END)\s*
      | T\d+\.RSLT\..*                        # a bare cross-tool reference
    )\s*$"""
)
# a run shorter than this is noise that happens to look like a line
MIN_SCRIPT_LINES = 3
# One script is stored in PIECES with small binary records interleaved between
# them, so a naive "any gap ends the script" rule chops a technician's script up
# and - worse - hides its ending. Measured on a real 3D-pick camera, the gap
# distribution is cleanly bimodal: 41, 99, 131, 149, 150 bytes BETWEEN pieces of
# one script, against 23,278 / 37,667 / 37,668 / 1,359,322 between different
# scripts. Any threshold in that valley works; 1 KB sits well inside it. The
# gap bytes themselves are not padding (they are nonzero records), so "merge
# only across NULs" would not have worked.
MERGE_GAP = 1024
# A run of lines that all match the grammar can still be noise: stretches of
# `'5` and `'K` decode out of the binary and are, technically, comments. A real
# script either does something (an assignment, an output, a control keyword) or
# says something a person wrote - a technician's notes-only tool is real and
# must survive, so the test is substance, not statements. 12 characters keeps
# "'Use this tool to leave notes" and drops "'5".
MIN_COMMENT_CHARS = 12


class BadProgram(ValueError):
    """The bytes are not a CV-X program, or not the part we can read."""


def is_program(head: bytes) -> bool:
    """Cheap gate: does this look like an `inspect.dat`? (magic only - a name
    is never evidence, so callers still have to parse before believing.)"""
    return len(head) >= 4 and head[:2] == b"ST"


def blocks(data: bytes) -> list[bytes]:
    """Every complete zlib block in the container, inflated, in file order.

    A program is stored TWICE - a working copy and a recovery copy - so the
    blocks arrive in pairs and everything inside would otherwise be listed
    twice. Measured on a real camera: 8 blocks, 4 lengths, each appearing
    exactly twice; three of the four pairs are byte-identical and the fourth
    (the program itself) differs in a handful of bytes, which is why an
    equality test does not catch it and the LENGTH does. The four logical
    blocks have four distinct lengths, so length is a safe key here. The first
    of each pair is kept: it is the working copy, and the recovery copy is by
    definition the older spare."""
    out: list[bytes] = []
    seen: set[int] = set()
    off = 0
    n = len(data)
    while off < n:
        cands = [data.find(sig, off) for sig in (b"\x78\x01", b"\x78\x9c", b"\x78\xda")]
        cands = [c for c in cands if c >= 0]
        if not cands:
            break
        pos = min(cands)
        try:
            obj = zlib.decompressobj()
            raw = obj.decompress(data[pos:])
            if obj.eof and len(raw) > 64:
                consumed = n - pos - len(obj.unused_data)
                if len(raw) not in seen:
                    seen.add(len(raw))
                    out.append(raw)
                off = pos + max(consumed, 2)
                continue
        except zlib.error:
            pass
        off = pos + 2
    return out


def _decode(raw: bytes) -> str:
    """Tool names are cp932 for the Japanese/Chinese slots and plain ASCII
    elsewhere; cp932 is a superset of ASCII so one decode serves both."""
    try:
        return raw.decode("cp932")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_name_record(buf: bytes, pos: int) -> tuple[dict, int] | None:
    """Read one (lang, length, text)* run starting at `pos`.

    Self-validating: language indices must ascend, every length must be sane,
    the text must not contain control bytes, and the run must be long enough
    that a coincidence cannot produce it. Returns (record, end) or None."""
    langs: dict[int, str] = {}
    off = pos
    prev = -1
    n = len(buf)
    while off + 8 <= n:
        lang, ln = struct.unpack_from("<II", buf, off)
        if lang <= prev or lang > 64 or ln > MAX_NAME_BYTES:
            break
        if off + 8 + ln > n:
            break
        text = buf[off + 8:off + 8 + ln]
        if any(b < 0x20 and b not in (0x09,) for b in text):
            break
        langs[lang] = _decode(text) if ln else ""
        prev = lang
        off += 8 + ln
    if len(langs) < MIN_LANG_SLOTS:
        return None
    if not any(v for v in langs.values()):
        return None                      # an all-empty run says nothing
    return {"offset": pos, "langs": langs, "name": langs.get(ENGLISH, "")}, off


def tool_names(buf: bytes) -> list[dict]:
    """Every believable tool-name record in one inflated block.

    Each record: {"offset", "langs": {index: text}, "name": english}. Records
    whose English slot is empty are kept - the absence is itself evidence that
    nobody named that tool in English - and callers decide how to show them."""
    out: list[dict] = []
    n = len(buf)
    off = 0
    while off + 8 <= n:
        # a record begins at a zero-length-or-text slot for language 0..2; scan
        # cheaply for a plausible (small lang index, sane length) pair
        lang, ln = struct.unpack_from("<II", buf, off)
        if lang <= 2 and ln <= MAX_NAME_BYTES and off + 8 + ln <= n:
            got = _read_name_record(buf, off)
            if got is not None:
                rec, end = got
                out.append(rec)
                off = end
                continue
        off += 4
    return out


def _has_substance(lines: list) -> bool:
    """True when a run is a script somebody wrote rather than bytes that happen
    to read as comments: it either performs an action, or carries a comment long
    enough to be prose (see MIN_COMMENT_CHARS). A notes-only tool is real and
    must survive this test - technicians use one as a logbook."""
    for ln in lines:
        s = ln.strip()
        if not s.startswith("'"):
            return True                       # an assignment/output/control line
        if len(s.lstrip("'").strip()) >= MIN_COMMENT_CHARS:
            return True                       # a sentence a person wrote
    return False


def scripts(buf: bytes) -> list[dict]:
    """Contiguous runs of calculation-script lines in one inflated block.

    Returns [{"offset", "lines": [str], "text": str}]. A run must reach
    MIN_SCRIPT_LINES to be reported, so a lone string that happens to match the
    grammar never becomes a "script"."""
    runs: list[dict] = []
    cur: list[str] = []
    start = 0
    last_end = -1
    for m in re.finditer(rb"[\x09\x20-\x7e]{2,400}", buf):
        s = m.group().decode("ascii", "replace").rstrip("\r\n")
        if not _SCRIPT_LINE.match(s):
            # Text that is not a script line does NOT end the script: the
            # pieces of one script have binary records between them, and some
            # of those decode to printable junk. Only DISTANCE ends a script
            # (below) - flushing here is what used to cut a technician's
            # script off before its ENDIF.
            continue
        # a big gap means a different script; a small one is the same script
        # continuing past an interleaved binary record (see MERGE_GAP)
        if cur and m.start() - last_end > MERGE_GAP:
            if len(cur) >= MIN_SCRIPT_LINES and _has_substance(cur):
                runs.append({"offset": start, "lines": cur, "text": "\n".join(cur)})
            cur = []
        if not cur:
            start = m.start()
        cur.append(s.strip())
        last_end = m.end()
    if len(cur) >= MIN_SCRIPT_LINES and _has_substance(cur):
        runs.append({"offset": start, "lines": cur, "text": "\n".join(cur)})
    return runs


def read_program(data: bytes) -> dict:
    """Everything readable in one `inspect.dat`.

    {"tools": [name record...], "scripts": [script run...], "blocks": n}
    Raises BadProgram when the container is not one we can open at all."""
    if not is_program(data[:4]):
        raise BadProgram("not an inspect.dat (magic is not 'ST')")
    bl = blocks(data)
    if not bl:
        raise BadProgram("no readable blocks in this program")
    tools: list[dict] = []
    runs: list[dict] = []
    for b in bl:
        # No de-duplication here on purpose: blocks() already folded out the
        # recovery copy, so anything still repeated is a genuine repeat - four
        # tools really can share the type name "Color Detection", and the count
        # is information, not noise.
        tools.extend(tool_names(b))
        runs.extend(scripts(b))
    return {"tools": tools, "scripts": runs, "blocks": len(bl)}
