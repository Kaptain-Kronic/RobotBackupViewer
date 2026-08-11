"""Keyence CV-X inspection programs: the tool names and the calculation logic
a technician actually wrote, read out of `inspect.dat`.

Evidence basis, 2026-08-11. `inspect.dat` is an "ST" container whose payload is
a handful of zlib blocks (98.7% of the file), stored twice - a working copy and
a recovery copy, which inflate byte-identically. The blocks are the program's
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


class BadProgram(ValueError):
    """The bytes are not a CV-X program, or not the part we can read."""


def is_program(head: bytes) -> bool:
    """Cheap gate: does this look like an `inspect.dat`? (magic only - a name
    is never evidence, so callers still have to parse before believing.)"""
    return len(head) >= 4 and head[:2] == b"ST"


def blocks(data: bytes) -> list[bytes]:
    """Every complete zlib block in the container, inflated, in file order.

    The working and recovery copies inflate identically, so exact duplicates
    are folded out - a program has one set of contents, and showing it twice
    would be a lie about how much is in there."""
    out: list[bytes] = []
    seen: set[bytes] = set()
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
                key = raw[:4096] + raw[-4096:] + bytes(str(len(raw)), "ascii")
                if key not in seen:
                    seen.add(key)
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
            if len(cur) >= MIN_SCRIPT_LINES:
                runs.append({"offset": start, "lines": cur, "text": "\n".join(cur)})
            cur = []
            continue
        # a gap of more than a few bytes means a different region, not the
        # next line of the same script
        if cur and m.start() - last_end > 64:
            if len(cur) >= MIN_SCRIPT_LINES:
                runs.append({"offset": start, "lines": cur, "text": "\n".join(cur)})
            cur = []
        if not cur:
            start = m.start()
        cur.append(s.strip())
        last_end = m.end()
    if len(cur) >= MIN_SCRIPT_LINES:
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
        tools.extend(tool_names(b))
        runs.extend(scripts(b))
    return {"tools": tools, "scripts": runs, "blocks": len(bl)}
