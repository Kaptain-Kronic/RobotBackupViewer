"""The [*SYSTEM*] merge that makes the system-var browser show the WHOLE dump.

SYSTEM.VA is only part of it - the controller scatters its $-vars across ~two
dozen SY*.VA files (and a few oddly-named ones: CELLIO, DCSIOC, TWLOGVAR). Every
system var, and only a system var, is tagged [*SYSTEM*]; the register headers
($NUMREG/...) and KAREL program vars carry their own sections and must NOT leak
in. Fully synthetic + identifier-clean (fake RB robot, FANUC-generic var names),
so this runs in CI without a real backup - the real-tree numbers live in the
untracked test_sysvars.py.
"""
import pytest

from backupviewer import settings
from backupviewer.api import Api
from backupviewer.parsers import sysvars
from backupviewer.session import BackupSession


@pytest.fixture(autouse=True)
def _own_settings_folder(tmp_path_factory, monkeypatch):
    """open_backup remembers the folder it opened (last_folder) - in a
    throwaway settings folder, never the developer's real %APPDATA%."""
    appdata = tmp_path_factory.mktemp("appdata")
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)

# -- synthetic .VA fixtures, mirroring real record shapes ---------------------

SYSTEM_VA = (
    "[*SYSTEM*]$FOO  Storage: DRAM  Access: RW  : BOOLEAN = TRUE\n"
    "[*SYSTEM*]$BAR  Storage: DRAM  Access: RW  : ARRAY[2] OF INTEGER\n"
    "  [1] = 10\n"
    "  [2] = 20\n"
)
SYSSPOT_VA = "[*SYSTEM*]$SPOT_CFG  Storage: CMOS  Access: RW  : INTEGER = 5\n"
# a real-world quirk: [*SYSTEM*] vars living in a non-SY* file, mixed in with a
# KAREL section - only the [*SYSTEM*] record is a system var
MIXED_VA = (
    "[MIXPROG]P_LOCAL  Storage: DRAM  Access: RW  : INTEGER = 1\n"
    "[*SYSTEM*]$MIX_LOGIC  Storage: DRAM  Access: RW  : BOOLEAN = FALSE\n"
)
# decoys that must never appear in the system-var list
NUMREG_VA = (
    "[*NUMREG*]$NUMREG  Storage: DRAM  Access: RW  : ARRAY[10] OF INTEGER\n"
    "[*NUMREG*]$MAXREGNUM  Storage: DRAM  Access: RO  : INTEGER = 200\n"
)
KAREL_VA = "[MYPROG]LOCALVAR  Storage: DRAM  Access: RW  : INTEGER = 7\n"
NONCARRIER_VA = "[OTHER]THING  Storage: DRAM  Access: RW  : INTEGER = 1\n"


def _all_sources():
    return [
        ("SYSTEM.VA", SYSTEM_VA),
        ("SYSSPOT.VA", SYSSPOT_VA),
        ("MIXED.VA", MIXED_VA),
        ("NUMREG.VA", NUMREG_VA),
        ("MYPROG.VA", KAREL_VA),
        ("NONCARRIER.VA", NONCARRIER_VA),
    ]


# -- the pure merge -----------------------------------------------------------

def test_merge_keeps_only_system_section_and_tags_source():
    recs = sysvars.merge_system_records(_all_sources())
    assert [r.name for r in recs] == ["$FOO", "$BAR", "$SPOT_CFG", "$MIX_LOGIC"]
    assert [r.source for r in recs] == [
        "SYSTEM.VA", "SYSTEM.VA", "SYSSPOT.VA", "MIXED.VA"]
    assert all(r.section == "*SYSTEM*" for r in recs)


def test_merge_excludes_register_headers_and_karel_vars():
    names = {r.name for r in sysvars.merge_system_records(_all_sources())}
    assert "$NUMREG" not in names          # [*NUMREG*] section, shown on registers tab
    assert "$MAXREGNUM" not in names
    assert "LOCALVAR" not in names          # KAREL program var
    assert "P_LOCAL" not in names           # KAREL var mixed into a carrier file
    assert "THING" not in names


def test_merge_skips_files_without_the_tag():
    # a file with no [*SYSTEM*] contributes nothing (and is cheap-skipped)
    assert sysvars.merge_system_records([("X.VA", NONCARRIER_VA)]) == []
    assert sysvars.merge_system_records([("EMPTY.VA", "")]) == []


def test_merge_first_source_wins_a_name_clash():
    dup = [("SYSTEM.VA", SYSTEM_VA),
           ("SYSOTHER.VA",
            "[*SYSTEM*]$FOO  Storage: DRAM  Access: RW  : BOOLEAN = FALSE\n")]
    by_name = {}
    for r in sysvars.merge_system_records(dup):
        by_name.setdefault(r.name.upper(), r)
    assert by_name["$FOO"].source == "SYSTEM.VA"   # the leading file wins


# -- summarize carries provenance --------------------------------------------

def test_summarize_leaf_and_body_with_source():
    recs = {r.name: r for r in sysvars.merge_system_records(_all_sources())}
    foo = sysvars.summarize(recs["$FOO"])
    assert foo["source"] == "SYSTEM.VA"
    assert foo["has_children"] is False
    assert foo["value"] == "TRUE" and foo["type"] == "BOOLEAN"

    bar = sysvars.summarize(recs["$BAR"])
    assert bar["source"] == "SYSTEM.VA"
    assert bar["has_children"] is True
    assert bar["value"] is None and bar["type"] == "ARRAY[2] OF INTEGER"

    spot = sysvars.summarize(recs["$SPOT_CFG"])
    assert spot["source"] == "SYSSPOT.VA" and spot["value"] == "5"


def test_record_tree_expands_array_body():
    recs = {r.name: r for r in sysvars.merge_system_records(_all_sources())}
    tree = sysvars.record_tree(recs["$BAR"])
    kids = tree["children"]
    assert [c["name"] for c in kids] == ["[1]", "[2]"]
    assert [c["value"] for c in kids] == ["10", "20"]


# -- session + endpoint end-to-end -------------------------------------------

def _mk_sysvar_backup(tmp):
    d = tmp / "RB010R01B01"
    d.mkdir()
    (d / "SUMMARY.DG").write_text("Robot: RB010R01B01\n", encoding="utf-8")
    for name, text in _all_sources():
        (d / name).write_text(text, encoding="cp1252")
    return d


def test_va_files_lists_system_first(tmp_path):
    d = _mk_sysvar_backup(tmp_path)
    names = [n for n, _ in BackupSession(d).va_files()]
    assert names[0] == "SYSTEM.VA"                 # familiar dump leads
    assert names[1:] == sorted(names[1:])          # the rest alphabetical
    assert set(names) >= {"SYSTEM.VA", "SYSSPOT.VA", "MIXED.VA", "NUMREG.VA"}


def _ok(res):
    assert res.get("ok"), res
    return res["data"]


def test_get_sysvar_records_merges_all_files(tmp_path):
    d = _mk_sysvar_backup(tmp_path)
    api = Api()
    _ok(api.open_backup(str(d)))
    rows = _ok(api.get_sysvar_records())
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"$FOO", "$BAR", "$SPOT_CFG", "$MIX_LOGIC"}
    assert by_name["$SPOT_CFG"]["source"] == "SYSSPOT.VA"
    assert by_name["$MIX_LOGIC"]["source"] == "MIXED.VA"
    # one alphabetical run like the pendant - NOT grouped by source. $MIX_LOGIC
    # (from MIXED.VA) sorts between the two SYSTEM.VA vars, proving the source
    # tag is provenance, not the sort key (source order would be F,B,SPOT,MIX).
    assert [r["name"] for r in rows] == ["$BAR", "$FOO", "$MIX_LOGIC", "$SPOT_CFG"]
    # a var pulled from a non-SYSTEM.VA chunk is openable by name
    tree = _ok(api.get_sysvar("$SPOT_CFG"))
    assert tree["value"] == "5" and tree["leaf"] is True
    # unknown name is a clean NOT_FOUND, not a crash
    assert api.get_sysvar("$NOPE")["error"]["code"] == "NOT_FOUND"


def test_sysvars_tab_lights_up_on_system_va(tmp_path):
    d = _mk_sysvar_backup(tmp_path)
    assert BackupSession(d).manifest()["tabs"]["sysvars"] is True
