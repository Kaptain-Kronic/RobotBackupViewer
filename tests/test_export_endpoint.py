"""export_edited_programs writes edited .LS to a NEW folder, never the backup.

Fully synthetic and identifier-clean (fake RB* robot, no sample backup): the
trust contract - the viewer never writes into a backup - is asserted directly.
Structured schema: body tokens + attr/position splices (engine in ls_edit.py;
this file covers the ENDPOINT: guards, atomicity, wiring).
"""
import pytest

from backupviewer.api import Api

PROG = "EDITME.LS"
PROG_BYTES = (
    b"/PROG  EDITME\r\n"
    b"/ATTR\r\n"
    b"OWNER\t\t= MNEDITOR;\r\n"
    b'COMMENT\t\t= "EDIT ME";\r\n'
    b"PROTECT\t\t= READ_WRITE;\r\n"
    b"/MN\r\n"
    b"   1:  !setup ;\r\n"
    b"   2:  DO[1]=ON ;\r\n"
    b"   3:J P[1] 100% FINE ;\r\n"
    b"/POS\r\n"
    b"P[1]{\r\n"
    b"   GP1:\r\n"
    b"\tUF : F, UT : F,\t\tCONFIG : '',\r\n"
    b"\tX = ********  mm,\tY = ********  mm,\tZ = ********  mm,\r\n"
    b"\tW = ******** deg,\tP = ******** deg,\tR = ******** deg\r\n"
    b"};\r\n"
    b"/END\r\n"
)


@pytest.fixture
def backup(tmp_path):
    bak = tmp_path / "RB010R01B01"
    bak.mkdir()
    (bak / PROG).write_bytes(PROG_BYTES)
    return bak


@pytest.fixture
def api(backup, monkeypatch):
    monkeypatch.setenv("BV_NO_WATCHER", "1")           # no library-watcher thread
    monkeypatch.setattr("backupviewer.settings.set_value", lambda *a, **k: None)  # don't touch %APPDATA%
    a = Api()
    a.bind(None, initial_backup=str(backup))
    return a


def _ok(res):
    assert res["ok"], res
    return res["data"]


def test_editable_seed(api):
    d = _ok(api.get_program_editable(PROG))
    assert d["name"] == PROG
    assert d["body"].split("\n") == ["!setup", "DO[1]=ON", "J P[1] 100% FINE"]
    assert d["attrs"] == {"owner": "MNEDITOR", "comment": "EDIT ME",
                          "protect": "READ_WRITE"}


def test_editable_unknown_program(api):
    res = api.get_program_editable("DOESNOTEXIST.LS")
    assert not res["ok"] and res["error"]["code"] == "NOT_FOUND"


def test_export_passthrough_is_byte_identical(api, tmp_path):
    """No body/attrs/positions -> the export is an exact copy."""
    dest = tmp_path / "export"
    data = _ok(api.export_edited_programs([{"file": PROG}], str(dest)))
    assert data["count"] == 1 and data["files"] == [PROG]
    assert (dest / PROG).read_bytes() == PROG_BYTES
    assert not list(dest.glob("*.part"))               # no staging debris


def test_export_body_edit_renumbers(api, tmp_path):
    dest = tmp_path / "export"
    body = [{"ref": 0}, {"text": "CALL OTHER"}, {"ref": 1}, {"ref": 2}]
    _ok(api.export_edited_programs([{"file": PROG, "body": body}], str(dest)))
    out = (dest / PROG).read_bytes()
    assert b"   2:  CALL OTHER ;\r\n" in out
    assert b"   3:  DO[1]=ON ;\r\n" in out             # renumbered, byte-exact rest
    assert b"   4:J P[1] 100% FINE ;\r\n" in out
    assert out.count(b"/POS") == 1                     # suffix untouched


def test_export_attr_and_position_edits(api, tmp_path):
    dest = tmp_path / "export"
    _ok(api.export_edited_programs([{
        "file": PROG,
        "attrs": {"comment": "RENAMED", "protect": "READ"},
        "positions": [{"id": 1, "gp": 1, "field": "x", "value": 100.5}],
    }], str(dest)))
    out = (dest / PROG).read_bytes()
    assert b'COMMENT\t\t= "RENAMED";\r\n' in out
    assert b"PROTECT\t\t= READ;\r\n" in out
    assert b"X = 100.500  mm," in out                  # masked point initialized
    assert b"Y = ********  mm," in out                 # neighbors stay masked
    assert b"   1:  !setup ;\r\n" in out               # body untouched


def test_export_bad_edit_aborts_all(api, tmp_path):
    dest = tmp_path / "export"
    res = api.export_edited_programs([{
        "file": PROG, "attrs": {"protect": "NONSENSE"},
    }], str(dest))
    assert not res["ok"] and res["error"]["code"] == "BAD_EDIT"
    assert not dest.exists()


def test_refuses_dest_equal_to_backup(api, backup):
    res = api.export_edited_programs([{"file": PROG}], str(backup))
    assert not res["ok"] and res["error"]["code"] == "BAD_DEST"


def test_refuses_dest_inside_backup(api, backup):
    res = api.export_edited_programs([{"file": PROG}], str(backup / "sub"))
    assert not res["ok"] and res["error"]["code"] == "BAD_DEST"


def test_refuses_backup_looking_dest(api, tmp_path):
    dest = tmp_path / "looks_like_a_backup"
    dest.mkdir()
    (dest / "SOME.LS").write_bytes(b"/PROG X\r\n/END\r\n")
    res = api.export_edited_programs([{"file": PROG}], str(dest))
    assert not res["ok"] and res["error"]["code"] == "BAD_DEST"


def test_backup_source_untouched(api, backup, tmp_path):
    before = (backup / PROG).read_bytes()
    _ok(api.export_edited_programs(
        [{"file": PROG, "body": [{"text": "REPLACED"}]}], str(tmp_path / "export")))
    assert (backup / PROG).read_bytes() == before


def test_all_or_nothing_on_missing_program(api, tmp_path):
    dest = tmp_path / "export"
    edits = [{"file": PROG}, {"file": "NOPE.LS"}]
    res = api.export_edited_programs(edits, str(dest))
    assert not res["ok"] and res["error"]["code"] == "NOT_FOUND"
    assert not dest.exists()                           # aborted before any write


def test_bad_char_aborts(api, tmp_path):
    dest = tmp_path / "export"
    res = api.export_edited_programs(
        [{"file": PROG, "body": [{"text": "euro €"}]}], str(dest))
    assert not res["ok"] and res["error"]["code"] == "BAD_CHAR"
    assert not dest.exists()


def test_empty_edits_rejected(api, tmp_path):
    res = api.export_edited_programs([], str(tmp_path / "export"))
    assert not res["ok"] and res["error"]["code"] == "NO_EDITS"
