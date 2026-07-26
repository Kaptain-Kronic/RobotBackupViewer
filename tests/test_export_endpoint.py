"""The path-addressed edit-workspace endpoints: ws_list_programs / ws_get_program
/ ws_export.

Fully synthetic and identifier-clean (fake RB* robots, no sample backup). These
endpoints deliberately take NO session: a workspace spans more robots than
MAX_OPEN_SESSIONS allows. The trust contract - the viewer never writes into a
backup - and the per-robot export layout are asserted directly.
"""
import json

import pytest

from backupviewer.api import Api

PROG = "MAIN.LS"          # the SAME name in both robots - the collision case
PROG_BYTES = (
    b"/PROG  MAIN\r\n"
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
# a .LS that is NOT a program: an alarm report dump. Must never be listed.
REPORT_BYTES = b"ERRALL.LS       Robot Name  RB010R01B01\r\n\r\nnothing here\r\n"


def _mk(root, robot):
    root.mkdir(parents=True)
    (root / PROG).write_bytes(PROG_BYTES)
    (root / "ERRALL.LS").write_bytes(REPORT_BYTES)
    (root / "backup.json").write_text(
        json.dumps({"robot": robot, "complete": True}), encoding="utf-8")
    return root


@pytest.fixture
def two_robots(tmp_path):
    a = _mk(tmp_path / "libA" / "RB010R01B01" / "2026_01_01" / "12_00_00", "RB010R01B01")
    b = _mk(tmp_path / "libB" / "RB020R01B01" / "2026_01_01" / "12_00_00", "RB020R01B01")
    return a, b


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("BV_NO_WATCHER", "1")
    monkeypatch.setattr("backupviewer.settings.set_value", lambda *a, **k: None)
    return Api()


def _ok(res):
    assert res["ok"], res
    return res["data"]


def _err(res):
    assert not res["ok"], res
    return res["error"]["code"]


# ---------------------------------------------------------------- listing

def test_list_finds_programs_and_skips_reports(api, two_robots):
    a, _ = two_robots
    got = _ok(api.ws_list_programs(str(a)))
    assert [p["name"] for p in got] == [PROG]        # ERRALL.LS excluded
    assert got[0]["file"] == PROG                    # relative to the root
    assert got[0]["comment"] == "EDIT ME"


def test_list_dedupes_basenames_shallowest_wins(api, tmp_path):
    root = _mk(tmp_path / "RB010R01B01", "RB010R01B01")
    deep = root / "mdb"
    deep.mkdir()
    (deep / PROG).write_bytes(PROG_BYTES.replace(b"!setup", b"!deep "))
    got = _ok(api.ws_list_programs(str(root)))
    assert [p["file"] for p in got] == [PROG]        # the root copy, not mdb/


def test_list_needs_a_real_backup_folder(api, tmp_path):
    plain = tmp_path / "just_a_folder"
    plain.mkdir()
    assert _err(api.ws_list_programs(str(plain))) == "BAD_ROOT"
    assert _err(api.ws_list_programs(str(tmp_path / "nope"))) == "BAD_ROOT"


# ---------------------------------------------------------------- reading

def test_get_program_seed(api, two_robots):
    a, _ = two_robots
    d = _ok(api.ws_get_program(str(a), PROG))
    assert d["name"] == PROG and d["file"] == PROG
    assert d["body"].split("\n") == ["!setup", "DO[1]=ON", "J P[1] 100% FINE"]
    assert d["attrs"] == {"owner": "MNEDITOR", "comment": "EDIT ME",
                          "protect": "READ_WRITE"}


def test_get_program_refuses_traversal(api, two_robots, tmp_path):
    a, _ = two_robots
    outside = tmp_path / "secret.ls"
    outside.write_bytes(PROG_BYTES)
    assert _err(api.ws_get_program(str(a), "../../../secret.ls")) == "BAD_FILE"
    assert _err(api.ws_get_program(str(a), "..")) == "BAD_FILE"


def test_get_program_refuses_non_program(api, two_robots):
    a, _ = two_robots
    assert _err(api.ws_get_program(str(a), "ERRALL.LS")) == "BAD_FILE"
    assert _err(api.ws_get_program(str(a), "backup.json")) == "BAD_FILE"
    assert _err(api.ws_get_program(str(a), "NOSUCH.LS")) == "NOT_FOUND"


# ---------------------------------------------------------------- diffing

def test_ws_diff_texts_modes(api):
    """The workspace diff endpoint: raw for original-vs-edited, normalized
    (identity) for pane-vs-pane, io_status flags per side."""
    a = "R[21]=1\nDO[1:Clamp]=ON"
    b = "R[21:SERVO GUN WORK]=1\nDO[1:OFF:Clamp]=ON"
    d = _ok(api.ws_diff_texts(a, b, True))
    assert [r["kind"] for r in d["rows"]] == ["equiv", "equiv"]
    assert d["io_status"] == {"a": False, "b": True}
    # raw mode: the same pair is two changed lines (same-source diffs are
    # byte-honest - there, a comment edit IS an edit)
    raw = _ok(api.ws_diff_texts(a, b))
    assert [r["kind"] for r in raw["rows"]] == ["change", "change"]
    # empty side: all rows one-sided, never a crash
    e = _ok(api.ws_diff_texts("", "DO[1]=ON"))
    assert [r["kind"] for r in e["rows"]] == ["b_only"]
    assert _ok(api.ws_diff_texts("", ""))["rows"] == []


# ---------------------------------------------------------------- export

def test_export_puts_each_robot_in_its_own_folder(api, two_robots, tmp_path):
    """The collision case: same program name in two robots must not overwrite."""
    a, b = two_robots
    dest = tmp_path / "export"
    data = _ok(api.ws_export([
        {"root": str(a), "file": PROG},
        {"root": str(b), "file": PROG},
    ], str(dest)))
    assert data["count"] == 2
    assert (dest / "RB010R01B01" / PROG).read_bytes() == PROG_BYTES
    assert (dest / "RB020R01B01" / PROG).read_bytes() == PROG_BYTES
    assert sorted(data["files"]) == ["RB010R01B01/MAIN.LS", "RB020R01B01/MAIN.LS"]
    assert not list(dest.rglob("*.part"))


def test_export_label_falls_back_to_hint_then_folder(api, tmp_path):
    root = tmp_path / "RB030R01B01"      # no backup.json -> no robot field
    root.mkdir()
    (root / PROG).write_bytes(PROG_BYTES)
    dest = tmp_path / "export"
    _ok(api.ws_export([{"root": str(root), "file": PROG, "label": "RB030R01B01"}], str(dest)))
    assert (dest / "RB030R01B01" / PROG).is_file()
    dest2 = tmp_path / "export2"
    _ok(api.ws_export([{"root": str(root), "file": PROG}], str(dest2)))
    assert (dest2 / "RB030R01B01" / PROG).is_file()      # from the folder name


def test_export_applies_edits(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "export"
    _ok(api.ws_export([{
        "root": str(a), "file": PROG,
        "body": [{"ref": 0}, {"text": "CALL OTHER"}, {"ref": 1}, {"ref": 2}],
        "attrs": {"comment": "RENAMED"},
        "positions": [{"id": 1, "gp": 1, "field": "x", "value": 100.5}],
    }], str(dest)))
    out = (dest / "RB010R01B01" / PROG).read_bytes()
    assert b"   2:  CALL OTHER ;\r\n" in out
    assert b"   3:  DO[1]=ON ;\r\n" in out              # renumbered, byte-exact
    assert b'COMMENT\t\t= "RENAMED";\r\n' in out
    assert b"X = 100.500  mm," in out                   # masked point initialized
    assert b"Y = ********  mm," in out                  # neighbours untouched


def test_export_rename_writes_new_name_and_patches_prog(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "export"
    _ok(api.ws_export([{"root": str(a), "file": PROG, "save_as": "RENAMED"}], str(dest)))
    out = dest / "RB010R01B01" / "RENAMED.LS"
    assert out.is_file()
    assert not (dest / "RB010R01B01" / PROG).exists()
    data = out.read_bytes()
    assert b"/PROG  RENAMED\r\n" in data       # header follows the file name
    assert b"   1:  !setup ;\r\n" in data      # body still byte-exact


def test_export_duplicate_same_source_two_names(api, two_robots, tmp_path):
    """A duplicate is two edits on one source with different save_as values."""
    a, _ = two_robots
    dest = tmp_path / "export"
    data = _ok(api.ws_export([
        {"root": str(a), "file": PROG},
        {"root": str(a), "file": PROG, "save_as": "MAIN_COPY",
         "body": [{"text": "!a copy"}]},
    ], str(dest)))
    assert data["count"] == 2
    assert (dest / "RB010R01B01" / PROG).read_bytes() == PROG_BYTES
    copy = (dest / "RB010R01B01" / "MAIN_COPY.LS").read_bytes()
    assert b"/PROG  MAIN_COPY\r\n" in copy and b"!a copy" in copy


def test_export_refuses_two_programs_landing_on_one_name(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "export"
    code = _err(api.ws_export([
        {"root": str(a), "file": PROG},
        {"root": str(a), "file": PROG, "save_as": "MAIN"},   # collides with itself
    ], str(dest)))
    assert code == "NAME_CLASH"
    assert not dest.exists()


def test_export_rejects_a_bad_rename(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "export"
    assert _err(api.ws_export(
        [{"root": str(a), "file": PROG, "save_as": "9bad name"}], str(dest))) == "BAD_EDIT"
    assert not dest.exists()


def test_export_refuses_dest_at_or_inside_any_backup(api, two_robots, tmp_path):
    a, b = two_robots
    edits = [{"root": str(a), "file": PROG}, {"root": str(b), "file": PROG}]
    assert _err(api.ws_export(edits, str(a))) == "BAD_DEST"
    assert _err(api.ws_export(edits, str(a / "sub"))) == "BAD_DEST"
    # guards the SECOND backup too, not just the first
    assert _err(api.ws_export(edits, str(b / "sub"))) == "BAD_DEST"


def test_export_refuses_backup_looking_dest(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "looks_like_a_backup"
    dest.mkdir()
    (dest / "SOME.LS").write_bytes(b"/PROG X\r\n/END\r\n")
    assert _err(api.ws_export([{"root": str(a), "file": PROG}], str(dest))) == "BAD_DEST"


def test_export_leaves_the_backups_untouched(api, two_robots, tmp_path):
    a, b = two_robots
    before = ((a / PROG).read_bytes(), (b / PROG).read_bytes())
    _ok(api.ws_export([
        {"root": str(a), "file": PROG, "body": [{"text": "REPLACED"}]},
        {"root": str(b), "file": PROG, "body": [{"text": "REPLACED"}]},
    ], str(tmp_path / "export")))
    assert ((a / PROG).read_bytes(), (b / PROG).read_bytes()) == before


def test_export_is_all_or_nothing(api, two_robots, tmp_path):
    a, b = two_robots
    dest = tmp_path / "export"
    # second edit is bad -> nothing at all is written, not even robot one
    code = _err(api.ws_export([
        {"root": str(a), "file": PROG},
        {"root": str(b), "file": "NOSUCH.LS"},
    ], str(dest)))
    assert code == "NOT_FOUND"
    assert not dest.exists()
    code = _err(api.ws_export([
        {"root": str(a), "file": PROG},
        {"root": str(b), "file": PROG, "attrs": {"protect": "NONSENSE"}},
    ], str(dest)))
    assert code == "BAD_EDIT"
    assert not dest.exists()


def test_export_bad_char_aborts(api, two_robots, tmp_path):
    a, _ = two_robots
    dest = tmp_path / "export"
    assert _err(api.ws_export(
        [{"root": str(a), "file": PROG, "body": [{"text": "euro €"}]}], str(dest))) == "BAD_CHAR"
    assert not dest.exists()


def test_export_empty_rejected(api, tmp_path):
    assert _err(api.ws_export([], str(tmp_path / "export"))) == "NO_EDITS"


def test_export_needs_no_open_session(api, two_robots, tmp_path):
    """The whole point: a workspace spans more robots than MAX_OPEN_SESSIONS."""
    a, b = two_robots
    assert not api._sessions
    _ok(api.ws_export([{"root": str(a), "file": PROG},
                       {"root": str(b), "file": PROG}], str(tmp_path / "export")))
    assert not api._sessions
