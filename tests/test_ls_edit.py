"""Byte-faithful round-trip for the .LS edit/export seam.

Fully synthetic and identifier-clean - no sample backup needed.
"""
import pytest

from backupviewer.parsers.ls_edit import LsEncodeError, decode_ls, encode_ls

# A .LS the way it sits on disk: CRLF terminators, a comment line, a blank
# body line, a motion line + its /POS block, and a masked value.  The high
# byte (0xB0 = degree sign in latin-1) in the comment is the round-trip
# tripwire - the lossy cp1252 reader would drop it.
LS_BYTES = (
    b"/PROG  EDITME\r\n"
    b"/ATTR\r\n"
    b'COMMENT\t\t= "TURN 90\xb0";\r\n'
    b"/MN\r\n"
    b"   1:  !setup ;\r\n"
    b"   2:   ;\r\n"
    b"   3:J P[1] 100% FINE ;\r\n"
    b"   4:  DO[1]=ON ;\r\n"
    b"/POS\r\n"
    b"P[1]{\r\n"
    b"   GP1:\r\n"
    b"\tUF : 0, UT : 1,\r\n"
    b"\tX =  ********  mm, Y =  ********  mm\r\n"
    b"};\r\n"
    b"/END\r\n"
)


def test_roundtrip_identity():
    """No edit -> byte-for-byte identical, high byte and CRLF preserved."""
    assert encode_ls(decode_ls(LS_BYTES)) == LS_BYTES


def test_high_byte_survives_decode():
    text = decode_ls(LS_BYTES)
    assert "\xb0" in text  # degree sign, not U+FFFD
    assert encode_ls(text) == LS_BYTES


def test_crlf_normalization_from_lf():
    """A textarea hands back \\n-only; export re-imposes CRLF."""
    assert encode_ls("A\nB\nC") == b"A\r\nB\r\nC"


def test_crlf_normalization_from_bare_cr():
    assert encode_ls("A\rB") == b"A\r\nB"


def test_crlf_not_doubled():
    """Already-CRLF text must not become \\r\\r\\n."""
    assert encode_ls("A\r\nB") == b"A\r\nB"


def test_empty():
    assert encode_ls("") == b""
    assert decode_ls(b"") == ""


def test_edit_reencodes():
    text = decode_ls(LS_BYTES).replace("DO[1]=ON", "DO[2]=ON")
    out = encode_ls(text)
    assert b"DO[2]=ON" in out and b"DO[1]=ON" not in out
    assert out.count(b"\r\n") == LS_BYTES.count(b"\r\n")  # line count unchanged


def test_non_latin1_raises_named_line():
    text = "   1:  !ok ;\n   2:  !euro € ;\n   3:  !end ;"
    with pytest.raises(LsEncodeError) as ei:
        encode_ls(text)
    msg = str(ei.value)
    assert "line 2" in msg and "U+20AC" in msg


# ---------------------------------------------------------------------------
# structured engine: split -> tokens -> emit

from pathlib import Path

from backupviewer.parsers.ls_edit import (LsEditError, apply_attrs,
                                          apply_positions, body_text,
                                          display_text, emit, split_sections)

# every measured body shape: comment, logic, motion (no separator), blank,
# and a wrapped statement (first line no ';', continuation carries it)
FULL_LS = (
    "/PROG  EDITME\r\n"
    "/ATTR\r\n"
    "OWNER\t\t= MNEDITOR;\r\n"
    'COMMENT\t\t= "EDIT ME";\r\n'
    "PROTECT\t\t= READ_WRITE;\r\n"
    "LINE_COUNT\t= 6;\r\n"
    "/MN\r\n"
    "   1:  !setup ;\r\n"
    "   2:   ;\r\n"
    "   3:J P[1] 100% FINE ;\r\n"
    "   4:  IF (R[1:one]>=R[2:two] AND\r\n"
    "    :  R[3:three]<=R[4:four]),JMP LBL[9] ;\r\n"
    "   5:  DO[1]=ON ;\r\n"
    "   6:  CALL OTHER ;\r\n"
    "/POS\r\n"
    "P[1]{\r\n"
    "   GP1:\r\n"
    "\tUF : 0, UT : 1,\t\tCONFIG : 'N U T, 0, 0, 0',\r\n"
    "\tX =  1115.514  mm,\tY =  -226.539  mm,\tZ =  1119.827  mm,\r\n"
    "\tW =     -.006 deg,\tP =   -89.978 deg,\tR =   179.994 deg\r\n"
    "};\r\n"
    "P[2]{\r\n"
    "   GP1:\r\n"
    "\tUF : F, UT : F,\t\tCONFIG : '',\r\n"
    "\tX = ********  mm,\tY = ********  mm,\tZ = ********  mm,\r\n"
    "\tW = ******** deg,\tP = ******** deg,\tR = ******** deg\r\n"
    "};\r\n"
    "/END\r\n"
)


def _refs(sections):
    return [{"ref": i} for i in range(len(sections["records"]))]


def test_split_shapes():
    s = split_sections(FULL_LS)
    assert s["prefix"].endswith("/MN\r\n")
    assert s["suffix"].startswith("/POS\r\n")
    assert len(s["records"]) == 6            # the wrapped statement is ONE record
    assert s["records"][3]["cont"] == ["    :  R[3:three]<=R[4:four]),JMP LBL[9] ;\r\n"]
    assert not s["mn_missing"]


def test_display_text_shapes():
    s = split_sections(FULL_LS)
    texts = [display_text(r) for r in s["records"]]
    assert texts[0] == "!setup"
    assert texts[1] == ""                    # blank body line
    assert texts[2] == "J P[1] 100% FINE"    # motion keeps its letter, loses nothing
    assert texts[3] == "IF (R[1:one]>=R[2:two] AND R[3:three]<=R[4:four]),JMP LBL[9]"
    assert texts[5] == "CALL OTHER"


def test_emit_roundtrip_identity():
    """No edit -> byte-for-byte identical, including the wrapped statement."""
    s = split_sections(FULL_LS)
    assert emit(s, _refs(s)) == FULL_LS


def test_emit_insert_renumbers():
    s = split_sections(FULL_LS)
    toks = _refs(s)
    toks.insert(2, {"text": "CALL INSERTED"})
    out = emit(s, toks)
    assert "   3:  CALL INSERTED ;\r\n" in out
    # old line 3 (motion) became 4, byte-exact after the number
    assert "   4:J P[1] 100% FINE ;\r\n" in out
    # wrapped statement renumbered but its continuation is untouched
    assert "   5:  IF (R[1:one]>=R[2:two] AND\r\n    :  R[3:three]" in out
    assert out.count("CALL INSERTED") == 1


def test_emit_delete_renumbers():
    s = split_sections(FULL_LS)
    toks = _refs(s)
    del toks[0]
    out = emit(s, toks)
    assert "   1:   ;\r\n" in out            # the blank moved up to 1
    assert "!setup" not in out
    assert "   5:  CALL OTHER ;\r\n" in out


def test_emit_edited_line_canonical():
    s = split_sections(FULL_LS)
    toks = _refs(s)
    toks[4] = {"ref": 4, "text": "DO[2]=OFF"}
    out = emit(s, toks)
    assert "   5:  DO[2]=OFF ;\r\n" in out
    assert "DO[1]=ON" not in out


def test_emit_motion_line_no_separator():
    s = split_sections(FULL_LS)
    out = emit(s, [{"text": "L P[9] 2000mm/sec CNT100"}])
    assert "   1:L P[9] 2000mm/sec CNT100 ;\r\n" in out


def test_emit_blank_line_canonical():
    s = split_sections(FULL_LS)
    out = emit(s, [{"text": ""}])
    assert "   1:   ;\r\n" in out


def test_emit_rejects_newline_in_line():
    s = split_sections(FULL_LS)
    with pytest.raises(LsEditError):
        emit(s, [{"text": "A\nB"}])


def test_emit_no_mn_synthesizes():
    src = "/PROG  X\r\n/ATTR\r\n/POS\r\n/END\r\n"
    s = split_sections(src)
    assert s["mn_missing"]
    out = emit(s, [{"text": "!new"}])
    assert "/MN\r\n   1:  !new ;\r\n/POS\r\n" in out


def test_body_text():
    s = split_sections(FULL_LS)
    assert body_text(s).split("\n")[2] == "J P[1] 100% FINE"


# ---------------------------------------------------------------------------
# renaming

def test_rename_patches_prog_header_only():
    from backupviewer.parsers.ls_edit import rename_program
    out = rename_program(FULL_LS, "NEWNAME")
    assert "/PROG  NEWNAME\r\n" in out
    assert "EDITME" not in out.split("/MN")[0]      # header no longer claims the old name
    # FILE_NAME is vestigial and left exactly as found
    assert out.count("\r\n") == FULL_LS.count("\r\n")


def test_rename_keeps_the_program_type():
    from backupviewer.parsers.ls_edit import rename_program
    src = "/PROG  OLDMACRO\t  Macro\r\n/ATTR\r\n/MN\r\n/POS\r\n/END\r\n"
    out = rename_program(src, "NEWMACRO")
    assert out.startswith("/PROG  NEWMACRO\t  Macro\r\n")


def test_rename_accepts_a_dot_ls_name():
    from backupviewer.parsers.ls_edit import rename_program
    assert "/PROG  FOO\r\n" in rename_program(FULL_LS, "foo.LS".upper())


def test_rename_rejects_bad_names():
    from backupviewer.parsers.ls_edit import rename_program
    for bad in ("", "  ", "9LIVES", "has space", "has-dash", "semi;colon"):
        with pytest.raises(LsEditError):
            rename_program(FULL_LS, bad)


def test_rename_needs_a_prog_header():
    from backupviewer.parsers.ls_edit import rename_program
    with pytest.raises(LsEditError):
        rename_program("/ATTR\r\n/MN\r\n/END\r\n", "FOO")


# ---------------------------------------------------------------------------
# /ATTR splices

def test_attr_edits_preserve_shape():
    s = split_sections(FULL_LS)
    p = apply_attrs(s["prefix"], {"owner": "YOURPLNT", "comment": "NEW NAME",
                                  "protect": "READ"})
    assert "OWNER\t\t= YOURPLNT;\r\n" in p          # tabs survive
    assert 'COMMENT\t\t= "NEW NAME";\r\n' in p
    assert "PROTECT\t\t= READ;\r\n" in p
    assert "LINE_COUNT\t= 6;" in p                   # untouched neighbors


def test_attr_rejects():
    s = split_sections(FULL_LS)
    with pytest.raises(LsEditError):
        apply_attrs(s["prefix"], {"line_count": 99})          # not ours to invent
    with pytest.raises(LsEditError):
        apply_attrs(s["prefix"], {"comment": 'has "quote"'})
    with pytest.raises(LsEditError):
        apply_attrs(s["prefix"], {"protect": "SOMETHING"})
    with pytest.raises(LsEditError):
        apply_attrs(s["prefix"], {"owner": "two words"})


# ---------------------------------------------------------------------------
# /POS splices

def test_pos_edit_cartesian():
    s = split_sections(FULL_LS)
    out = apply_positions(s["suffix"], [{"id": 1, "gp": 1, "field": "x", "value": 1200.5}])
    assert "X =  1200.500  mm," in out       # surrounding spacing byte-preserved
    assert "Y =  -226.539  mm," in out               # neighbor untouched


def test_pos_edit_masked_initializes():
    """A masked point is uninitialized, not secret - typing a value initializes it."""
    s = split_sections(FULL_LS)
    out = apply_positions(s["suffix"], [
        {"id": 2, "gp": 1, "field": "x", "value": 100},
        {"id": 2, "gp": 1, "field": "w", "value": -0.006},
    ])
    assert "X = 100.000  mm," in out
    assert "W = -0.006 deg," in out
    assert "Y = ********  mm," in out                # only edited fields change


def test_pos_edit_ufut_config_comment():
    s = split_sections(FULL_LS)
    out = apply_positions(s["suffix"], [
        {"id": 2, "gp": 1, "field": "uf", "value": "0"},
        {"id": 2, "gp": 1, "field": "config", "value": "N U T, 0, 0, 0"},
        {"id": 2, "gp": 1, "field": "comment", "value": "pounce"},
    ])
    assert "UF : 0, UT : F," in out
    assert "CONFIG : 'N U T, 0, 0, 0'," in out
    assert 'P[2:"pounce"]{' in out


def test_pos_edit_rejects():
    s = split_sections(FULL_LS)
    with pytest.raises(LsEditError):
        apply_positions(s["suffix"], [{"id": 9, "gp": 1, "field": "x", "value": 1}])
    with pytest.raises(LsEditError):
        apply_positions(s["suffix"], [{"id": 1, "gp": 1, "field": "x", "value": "abc"}])
    with pytest.raises(LsEditError):
        apply_positions(s["suffix"], [{"id": 1, "gp": 1, "field": "uf", "value": "Q"}])


# ---------------------------------------------------------------------------
# the acid test: byte-identity round-trip over every real program present

def test_roundtrip_fuzz_over_sample_tree():
    """For EVERY program .ls in SampleBackup: split -> all-ref tokens -> emit
    must be byte-identical. This is what makes the editor safe to trust on
    files we didn't anticipate. Skips when no sample tree is present."""
    root = Path(__file__).parents[1] / "SampleBackup"
    if not root.is_dir():
        pytest.skip("no SampleBackup tree")
    checked = 0
    for p in root.rglob("*.[lL][sS]"):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if not data.startswith(b"/PROG"):
            continue
        text = decode_ls(data)
        s = split_sections(text)
        out = emit(s, [{"ref": i} for i in range(len(s["records"]))])
        assert out == text, f"round-trip drift in {p.name}"
        assert encode_ls(out) == data, f"byte drift in {p.name}"
        checked += 1
    assert checked, "sample tree present but no programs found"
