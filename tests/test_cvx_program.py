"""Keyence CV-X inspection programs: the "ST" container, the tool-name
language table, the calculation scripts, and the logic tab they feed.

Fixtures are synthetic and identifier-clean: an `inspect.dat` built here to the
shape the real files carry - the "ST" header with the program name and its
byte-for-byte echo, then zlib blocks holding length-prefixed name records and
plain-ASCII script lines. NUL padding is always built programmatically
(`bytes(n)` / `bytearray(n)`), never written as literal escapes.
"""
import struct
import zlib

import pytest

from backupviewer.api import Api
from backupviewer.parsers import cvx_inspect, cvx_program
from backupviewer.session import BackupSession


# -- fixture builders -----------------------------------------------------------

JP = "テスト".encode("cp932")            # a real record's japanese slot is cp932
# NUL run planted between regions: longer than the 64-byte gap that ends a
# script run, so two planted scripts are two scripts
PAD = 4096   # a real between-scripts gap measures 23k-1.3M; MERGE_GAP is 1k
# the header must reach past the program name's echo for cvx_inspect to read it
HEAD = cvx_inspect.NAME_ECHO_OFFSET + cvx_inspect.NAME_MAX
PROGRAM_NAME = "RB130R01B01CAM1"


def name_record(slots):
    """(u32 language_index, u32 byte_length, text)* in the order given.

    The real format ascends and carries empty languages at length 0; the
    refusal fixtures pass orders the format never writes, which is the point."""
    return b"".join(struct.pack("<II", lang, len(raw)) + raw for lang, raw in slots)


def full_slots(english=b"Bin Pick Height", japanese=JP):
    """Six ascending language slots, most of them empty - a tool named in
    English on a controller that shipped with a Japanese default."""
    return [(0, japanese), (1, english), (2, b""), (4, b""), (7, b""), (8, b"")]


def name_table(names):
    """Several records back to back - the format is the (lang, len, text) run
    "repeated", so a name table is one contiguous region, not records scattered
    across the block. Returns (bytes, [offset of each record within it])."""
    out = bytearray()
    offsets = []
    for english in names:
        offsets.append(len(out))
        out += name_record(full_slots(english))
    return bytes(out), offsets


# a calculation script in the CV-X's own grammar: a heading comment, locals
# fed from other tools' results, a conditional, and an output
SCRIPT_A = [
    "'Bin Pick Height Check",
    "@floor = T101.RSLT.CZ[0]:MS",
    "@part = T102.RSLT.CZ[0]:MS",
    "IF @part - @floor > 12.5 THEN",
    "ANS1 = 1",
    "ELSE",
    "ANS1 = 0",
    "ENDIF",
]
# exactly MIN_SCRIPT_LINES long, and opening on no comment - so it has no title
SCRIPT_B = [
    "@gap = T103.RSLT.DIST[0]:MS",
    "ANS2 = @gap",
    "T104.RSLT.NG[0]:MS",
]


def script_bytes(lines):
    """Script lines as the block stores them: plain ASCII, CRLF-separated."""
    return b"".join(line.encode("ascii") + b"\r\n" for line in lines)


def block(parts, pad=PAD):
    """One inflated block: `parts` planted at 4-aligned offsets with NUL
    padding around them. Returns (block, [offset of each part]) so a test can
    assert the parser found a region WHERE it was put, not at a guessed
    offset."""
    out = bytearray(pad)
    offsets = []
    for part in parts:
        out += bytes(-len(out) % 4)
        offsets.append(len(out))
        out += bytes(part)
        out += bytes(pad)
    return bytes(out), offsets


def container(*inflated, name=PROGRAM_NAME):
    """An `inspect.dat`: the "ST" header carrying the program name at 0x4C and
    its byte-for-byte echo at 0x398 (the agreement cvx_inspect requires before
    it believes a name), then each block zlib-compressed, back to back."""
    head = bytearray(HEAD)
    head[0:2] = b"ST"
    raw = name.encode("cp1252")
    head[cvx_inspect.NAME_OFFSET:cvx_inspect.NAME_OFFSET + len(raw)] = raw
    head[cvx_inspect.NAME_ECHO_OFFSET:cvx_inspect.NAME_ECHO_OFFSET + len(raw)] = raw
    return bytes(head) + b"".join(zlib.compress(b) for b in inflated)


def one_block(parts):
    """(container bytes, inflated block, planted offsets) for a single block."""
    raw, offsets = block(parts)
    return container(raw), raw, offsets


# -- the container gate ---------------------------------------------------------

def test_is_program_accepts_the_st_magic():
    assert cvx_program.is_program(b"ST" + bytes(6)) is True
    assert cvx_program.is_program(container(bytes(200))[:8]) is True


@pytest.mark.parametrize("head", [
    b"",
    b"ST",                                  # the magic, but nothing behind it
    b"ST" + bytes(1),                       # still short of the four bytes read
    b"st" + bytes(6),                       # case matters
    b"BM" + bytes(62),                      # a CV-X image
    b"GS" + bytes(30),                      # env.dat's magic
    bytes(64),
    b"TS" + bytes(6),                       # the magic, byte-swapped
])
def test_is_program_refuses_everything_else(head):
    assert cvx_program.is_program(head) is False


# -- blocks ---------------------------------------------------------------------

def test_blocks_inflates_the_payload_byte_for_byte():
    raw, _offs = block([script_bytes(SCRIPT_A)])
    assert cvx_program.blocks(container(raw)) == [raw]


def test_the_working_and_recovery_copies_are_one_program():
    """A real file stores the same blocks twice. They inflate identically, and
    reporting both would be a lie about how much is in the program."""
    raw, _offs = block([script_bytes(SCRIPT_A)])
    data = container(raw, raw)
    assert data.count(zlib.compress(raw)) == 2      # the pair really is on disk
    assert cvx_program.blocks(data) == [raw]


def test_two_different_blocks_are_both_kept():
    a, _ = block([script_bytes(SCRIPT_A)])
    b, _ = block([script_bytes(SCRIPT_B), name_record(full_slots())])
    assert cvx_program.blocks(container(a, b)) == [a, b]


@pytest.mark.parametrize("data", [
    b"",
    b"ST" + bytes(4096),                    # a container with nothing packed in
    b"The quick brown fox jumps over the lazy dog. " * 20,
    zlib.compress(bytes(64)),               # too little inflates to be a block
], ids=["empty", "all NUL", "text", "64-byte stream"])
def test_blocks_returns_nothing_rather_than_guessing(data):
    assert cvx_program.blocks(data) == []


# -- tool names -----------------------------------------------------------------

def test_a_name_record_round_trips_every_language_slot():
    raw, (off,) = block([name_record(full_slots())])
    got = cvx_program.tool_names(raw)
    assert len(got) == 1
    rec = got[0]
    assert rec["offset"] == off             # found where it was planted
    assert rec["langs"] == {0: "テスト", 1: "Bin Pick Height",
                            2: "", 4: "", 7: "", 8: ""}
    # the english slot IS the name - the rule, not a coincidence of this fixture
    assert rec["name"] == rec["langs"][cvx_program.ENGLISH] == "Bin Pick Height"


def test_a_tool_nobody_named_in_english_is_still_a_record():
    """The empty English slot is itself evidence (nobody typed a name), so the
    record is kept with an empty name rather than dropped."""
    raw, _offs = block([name_record(full_slots(english=b""))])
    got = cvx_program.tool_names(raw)
    assert len(got) == 1
    assert got[0]["name"] == ""
    assert got[0]["langs"][0] == "テスト"    # the japanese slot still reads


def test_every_record_in_a_table_is_found_in_order():
    table, inner = name_table([b"Bin Pick Height", b"Color Detection",
                               b"Edge Pitch"])
    raw, (base,) = block([table])
    got = cvx_program.tool_names(raw)
    # each record ends exactly where the next begins - no slot is swallowed by
    # its neighbour, and none of the table is read twice
    assert [r["offset"] for r in got] == [base + o for o in inner]
    assert [r["name"] for r in got] == ["Bin Pick Height", "Color Detection",
                                        "Edge Pitch"]


@pytest.mark.parametrize("slots", [
    # language indices out of order: neither the head nor the tail of the run
    # reaches MIN_LANG_SLOTS on its own, so nothing is believed
    [(0, JP), (2, b"DE"), (1, b"EN"), (4, b"")],
    # the same language twice - a record names a tool once per language
    [(0, JP), (1, b"EN"), (1, b"E2"), (4, b"")],
    # a length no tool name reaches; a "length" that big means these are not
    # the u32s we think they are
    [(0, JP), (1, b"A" * (cvx_program.MAX_NAME_BYTES + 1)), (2, b""), (4, b"")],
    # a control byte inside the text - built programmatically, never as an
    # escape - stops the run where it sits, leaving too little to believe
    [(0, JP), (1, b"EN"), (2, b"D" + bytes([1]) + b"E"), (4, b"")],
    # three slots: short enough that a stray pair of plausible u32s could fake it
    [(0, JP), (1, b"EN"), (2, b"DE")],
    # long enough, but every slot empty - a run that says nothing
    [(0, b""), (1, b""), (2, b""), (4, b"")],
], ids=["descending languages", "repeated language", "length past the cap",
        "control byte in the text", "shorter than MIN_LANG_SLOTS",
        "every slot empty"])
def test_tool_names_refuses_a_run_that_does_not_prove_itself(slots):
    raw, _offs = block([name_record(slots)])
    assert cvx_program.tool_names(raw) == []


def test_min_lang_slots_is_the_line_the_refusals_sit_under():
    """The four-slot fixture above is refused for its CONTENT; four honest
    slots are accepted - so the refusals are not just "too short"."""
    raw, _offs = block([name_record([(0, JP), (1, b"EN"), (2, b""), (4, b"")])])
    assert len(cvx_program.tool_names(raw)) == 1
    assert cvx_program.MIN_LANG_SLOTS == 4


@pytest.mark.parametrize("data", [
    b"",
    bytes(4096),                            # NUL padding is not a name table
    script_bytes(SCRIPT_A),                 # script text is not a name table
], ids=["empty", "all NUL", "script text"])
def test_tool_names_finds_nothing_in_what_is_not_a_name_table(data):
    assert cvx_program.tool_names(data) == []


# -- calculation scripts --------------------------------------------------------

def test_a_script_comes_back_line_for_line_in_order():
    raw, (off,) = block([script_bytes(SCRIPT_A)])
    got = cvx_program.scripts(raw)
    assert len(got) == 1
    run = got[0]
    assert run["offset"] == off
    assert run["lines"] == SCRIPT_A
    assert run["text"] == "\n".join(SCRIPT_A)


def test_two_scripts_across_a_gap_are_never_fused_into_one():
    """The gap between the runs is unrelated bytes, not the next line of the
    same script - joining them would invent logic that is not in the file."""
    raw, offs = block([script_bytes(SCRIPT_A), script_bytes(SCRIPT_B)], pad=PAD)
    assert PAD > cvx_program.MERGE_GAP       # the gap the parser splits on
    got = cvx_program.scripts(raw)
    assert [r["offset"] for r in got] == offs
    assert [r["lines"] for r in got] == [SCRIPT_A, SCRIPT_B]


def test_a_gap_too_small_to_split_leaves_one_script():
    """The companion to the test above: the split is the GAP rule, not a rule
    that separated runs never join. Eight bytes between the halves and it stays
    one script - which is what makes the 4 KB gap above mean something."""
    raw, _offs = block([script_bytes(SCRIPT_A) + bytes(8) + script_bytes(SCRIPT_B)])
    got = cvx_program.scripts(raw)
    assert [r["lines"] for r in got] == [SCRIPT_A + SCRIPT_B]


def test_text_that_is_not_the_language_does_not_end_the_run():
    """A real script is stored in pieces with binary records between them, and
    some of those records decode to printable junk. Junk must be SKIPPED, not
    treated as the end of the script - cutting there is what hid a technician's
    closing ENDIF. Only distance ends a script (the gap tests above)."""
    mixed = SCRIPT_A[:4] + ["Camera Setting Table"] + SCRIPT_B
    raw, _offs = block([script_bytes(mixed)])
    got = cvx_program.scripts(raw)
    assert len(got) == 1
    assert got[0]["lines"] == SCRIPT_A[:4] + SCRIPT_B
    assert "Camera Setting Table" not in got[0]["text"]


def test_a_run_of_trivial_comments_is_not_a_script():
    """`'5` repeated decodes out of the binary and is, technically, a run of
    comments. A script has to DO something or SAY something - otherwise the
    tab fills with rubbish that looks like a technician wrote it."""
    raw, _offs = block([script_bytes(["'5", "'5", "'5", "'5", "'5"])])
    assert cvx_program.scripts(raw) == []


def test_a_notes_only_tool_survives_the_substance_test():
    """The other side of the rule above: technicians use a calculation tool as
    a logbook, and those notes are real content that must not be filtered."""
    notes = ["'Dual Bin + Redundancy 04/07/25",
             "'Use this tool to leave notes",
             "'Templates Loaded 8/26/25"]
    raw, _offs = block([script_bytes(notes)])
    got = cvx_program.scripts(raw)
    assert len(got) == 1 and got[0]["lines"] == notes


def test_the_recovery_copy_is_folded_out():
    """A program is stored twice and the pair is not byte-identical, so blocks()
    keys on LENGTH. Without this every script and every name would be listed
    twice - which is exactly what a reader would read as "the camera has two of
    these"."""
    body = block([script_bytes(SCRIPT_A)])[0]
    twin = bytearray(body)
    twin[len(twin) - 1] ^= 0xFF          # same length, different bytes
    data = container(body, bytes(twin))
    got = cvx_program.blocks(data)
    assert len(got) == 1
    assert len(cvx_program.read_program(data)["scripts"]) == 1


@pytest.mark.parametrize("lines", [
    SCRIPT_B[:2],                           # one line short of MIN_SCRIPT_LINES
    SCRIPT_B[:1],
], ids=["two lines", "one line"])
def test_a_run_shorter_than_min_script_lines_is_not_a_script(lines):
    raw, _offs = block([script_bytes(lines)])
    assert cvx_program.scripts(raw) == []
    assert cvx_program.MIN_SCRIPT_LINES == 3


@pytest.mark.parametrize("data", [
    b"",
    bytes(4096),
    b"The quick brown fox jumps over the lazy dog.\r\n" * 20,
    name_record(full_slots()),              # a name table is not a script
], ids=["empty", "all NUL", "prose", "name record"])
def test_scripts_finds_nothing_in_what_is_not_script_text(data):
    assert cvx_program.scripts(data) == []


# -- the whole program ----------------------------------------------------------

def test_read_program_reports_both_regions_and_the_block_count():
    table, _inner = name_table([b"Bin Pick Height", b"Edge Pitch"])
    data, _raw, _offs = one_block(
        [table, script_bytes(SCRIPT_A), script_bytes(SCRIPT_B)])
    got = cvx_program.read_program(data)
    assert got["blocks"] == 1
    assert [t["name"] for t in got["tools"]] == ["Bin Pick Height", "Edge Pitch"]
    assert [s["lines"] for s in got["scripts"]] == [SCRIPT_A, SCRIPT_B]


def test_read_program_does_not_double_the_recovery_copy():
    raw, _offs = block([name_record(full_slots()), script_bytes(SCRIPT_A)])
    got = cvx_program.read_program(container(raw, raw))
    assert got["blocks"] == 1
    assert len(got["tools"]) == 1 and len(got["scripts"]) == 1


@pytest.mark.parametrize("data", [
    b"",
    b"not an ST container",
    bytes(4096),                            # no magic at all
    zlib.compress(bytes(4096)),             # blocks, but not in our container
], ids=["empty", "text", "all NUL", "bare zlib"])
def test_read_program_refuses_what_is_not_an_inspect_dat(data):
    with pytest.raises(cvx_program.BadProgram):
        cvx_program.read_program(data)


def test_a_container_with_nothing_inflatable_is_refused():
    """The magic claims; the blocks prove. An "ST" header over bytes we cannot
    open is not a program we can read, and saying so beats an empty report."""
    with pytest.raises(cvx_program.BadProgram):
        cvx_program.read_program(b"ST" + bytes(8192))


# -- through the session + api (a synthetic camera pull) ------------------------

def logic_block(names, scripts):
    table, _inner = name_table(names)
    raw, _offs = block([table] + [script_bytes(s) for s in scripts])
    return raw


@pytest.fixture()
def logic_backup(tmp_path):
    """A CV-X pull shaped like a real one - <label>/SD1/cv-x/setting/<NNN>/ -
    with two readable programs, a decoy that claims the name without the magic,
    and an inspect.dat outside the camera tree. Identifier-clean: an RB* fixture
    label, no plant or line anywhere in it."""
    root = tmp_path / "pull"
    cam = root / PROGRAM_NAME / "SD1" / "cv-x" / "setting"
    for n in ("001", "002", "003"):
        (cam / n).mkdir(parents=True)
    (cam / "001" / "inspect.dat").write_bytes(container(logic_block(
        # a technician's own name, the vendor's built-in tool-type vocabulary
        # twice, another built-in, and a tool nobody named in english
        [b"Bin Pick Height", b"Color Detection", b"Color Detection",
         b"Edge Pitch", b""],
        [SCRIPT_A, SCRIPT_B])))
    (cam / "002" / "inspect.dat").write_bytes(container(logic_block(
        [b"Edge Pitch"], [SCRIPT_B])))
    # the name claims a program; the magic does not vouch for it
    (cam / "003" / "inspect.dat").write_bytes(b"not an ST container" + bytes(64))
    stray = root / PROGRAM_NAME / "notes"
    stray.mkdir(parents=True)
    (stray / "inspect.dat").write_bytes(container(logic_block([b"Edge Pitch"],
                                                              [SCRIPT_A])))
    return root


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("BV_NO_WATCHER", "1")
    monkeypatch.setattr("backupviewer.settings.set_value", lambda *a, **k: None)
    return Api()


def _sid(api, root):
    return api.open_backup(str(root))["data"]["sid"]


def _rel(n):
    return f"{PROGRAM_NAME}/SD1/cv-x/setting/{n}/inspect.dat"


def test_cvx_program_files_vouches_only_the_real_containers(logic_backup):
    s = BackupSession(logic_backup)
    assert [rel for rel, _p in s.cvx_program_files()] == [_rel("001"), _rel("002")]
    # 003 (the name without the magic) and notes/inspect.dat (a real container
    # outside the camera's cv-x tree) are both absent


def test_manifest_lights_the_logic_tab(logic_backup):
    m = BackupSession(logic_backup).manifest()
    assert m["backup_type"] == "keyence camera"
    assert m["tabs"]["logic"] is True


def test_a_robot_backup_never_lights_the_logic_tab(tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    (d / "SUMMARY.DG").write_text("F Number: F999999\n", encoding="cp1252")
    m = BackupSession(d).manifest()
    assert m["tabs"]["overview"] is True        # SUMMARY.DG, as ever
    assert m["tabs"]["logic"] is False


def test_a_camera_with_no_readable_program_keeps_logic_dark(tmp_path):
    d = tmp_path / "CAM1" / "SD1" / "cv-x" / "setting" / "001"
    d.mkdir(parents=True)
    (d / "inspect.dat").write_bytes(b"not an ST container" + bytes(64))
    m = BackupSession(tmp_path).manifest()
    assert m["backup_type"] == "keyence camera"
    assert m["tabs"]["logic"] is False


def test_cvx_logic_reads_the_programs_a_technician_wrote(api, logic_backup):
    data = api.cvx_logic(_sid(api, logic_backup))["data"]
    assert data["count"] == len(data["programs"]) == 2
    one, two = data["programs"]
    assert (one["program"], one["rel"]) == ("001", _rel("001"))
    assert one["name"] == PROGRAM_NAME
    assert (two["program"], two["rel"]) == ("002", _rel("002"))
    assert [s["lines"] for s in one["scripts"]] == [8, 3]      # longest first
    assert one["script_lines"] == 11
    assert one["scripts"][0]["text"].split("\n") == SCRIPT_A
    assert one["scripts"][0]["title"] == "Bin Pick Height Check"
    assert one["scripts"][1]["title"] == ""    # no leading comment, none invented


def test_cvx_logic_lists_the_names_without_claiming_a_tool(api, logic_backup):
    """Two honesty rules ride on this shape. Nothing in the file ties a name
    record to a tool NUMBER, so an entry carries a name and a count and nothing
    that could be read as an ordering. And the list mixes the technician's own
    name with the vendor's built-in tool-type vocabulary ("Color Detection",
    "Edge Pitch" ship inside every program) because no discriminator has been
    proved - so both are shown, plainly, rather than one being filtered away."""
    data = api.cvx_logic(_sid(api, logic_backup))["data"]
    names = data["programs"][0]["names"]
    assert names == [{"name": "Bin Pick Height", "count": 1},
                     {"name": "Color Detection", "count": 2},
                     {"name": "Edge Pitch", "count": 1}]
    assert [n["name"] for n in names] == sorted(n["name"] for n in names)
    assert all(set(n) == {"name", "count"} for n in names)
    # five records were planted; the fifth has no english name and is not
    # invented into one
    assert sum(n["count"] for n in names) == 4


def test_cvx_logic_refuses_a_robot_backup(api, tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    (d / "SUMMARY.DG").write_text("F Number: F999999\n", encoding="cp1252")
    res = api.cvx_logic(_sid(api, d))
    assert not res["ok"] and res["error"]["code"] == "NOT_CVX"
