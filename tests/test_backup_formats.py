"""Backup-format detection and per-format session behavior, on synthetic trees.

These assertions used to live in a git-excluded file pointed at real
maintenance-data / all-of-the-above fixture backups. Those fixtures are
retired (the owner's ruling: the other backup forms are the same parsers over
less data, so keeping real corpora of them proves nothing) - but detection,
recursive indexing, duplicate-basename priority and per-format tab
availability are LOGIC, and logic needs a directory shape, not a real robot.
Synthetic shapes prove it and can be tracked, so a clean clone verifies this
for the first time. Same move as test_magnet / test_mhvalves / test_payloads.

Identifier-clean by construction: fake RB robots, F999999, no real paths.
"""
from backupviewer.parsers.io_dg import merge_io, parse_io_config, parse_io_state
from backupviewer.parsers.summary_dg import io_section_texts
from backupviewer.session import BackupSession

ROBOT = "RB010R01B01"

# A minimal SUMMARY.DG: the manifest reads "F Number:" from the head, and the
# two I/O sections carry the same table formats IOCONFIG/IOSTATE.DG would -
# including the '::' marker line the io_dg parsers key on. This is the
# fallback path real maintenance-data / all-of-the-above backups rely on.
SUMMARY = (
    'F Number: F999999\n'
    '<H2><A NAME="1">Version Information</A></H2>\n'
    '<PRE>\n'
    'VERSION INFORMATION::\n'
    '\n'
    ' F Number: F999999\n'
    '</PRE>\n'
    '<H2><A NAME="4">I/O status information</A></H2>\n'
    '<PRE>\n'
    'IO STATUS::\n'
    '\n'
    'DIN[   1] OFF  *IMSTP\n'
    'DIN[   2] ON   *Hold\n'
    'GOUT[  93]21986  TotalSignA\n'
    'FLG[   1]  ON  Capwear Done          FLG[ 513] OFF\n'
    '</PRE>\n'
    '<H2><A NAME="5">I/O Configuration Information</A></H2>\n'
    '<PRE>\n'
    'IO CONFIG::\n'
    '\n'
    'DIN[   1]  *IMSTP\n'
    'DIN[   2]  *Hold\n'
    'DIN     1  -  800   RACK:  89   SLOT:   1   PORT:   1\n'
    'GIN     1   RACK:  89   SLOT:   1   PORT:  25   #NUM:   8\n'
    '</PRE>\n'
)

PROG_LS = "/PROG  TESTPROG\n/ATTR\nOWNER\t\t= MNEDITOR;\n/MN\n   1:  ! ;\n/END\n"


def report_ls(name):
    return f"{name}   Robot Name {ROBOT}  07-JUL-26 13:56:35  page   1\n\n"


def make_md(root):
    """Flat MD shape: everything at the top."""
    (root / "summary.dg").write_text(SUMMARY, encoding="cp1252")
    (root / "testprog.ls").write_text(PROG_LS, encoding="cp1252")
    (root / "errall.ls").write_text(report_ls("ERRALL.LS"), encoding="cp1252")
    return root


def make_maint(root):
    """Hierarchical maintenance-data shape: mnt_data/mdb (controller dump) +
    mnt_data/md_ls (programs), with errall.ls duplicated across both."""
    mdb = root / "mnt_data" / "mdb"
    md_ls = root / "mnt_data" / "md_ls"
    mdb.mkdir(parents=True)
    md_ls.mkdir(parents=True)
    (mdb / "summary.dg").write_text(SUMMARY, encoding="cp1252")
    (mdb / "errall.ls").write_text(report_ls("ERRALL.LS"), encoding="cp1252")
    (md_ls / "errall.ls").write_text(report_ls("ERRALL.LS"), encoding="cp1252")
    (md_ls / "prog1.ls").write_text(PROG_LS, encoding="cp1252")
    return root


def make_allabove(root):
    """All-of-the-above shape: binary programs only, no .ls source."""
    (root / "summary.dg").write_text(SUMMARY, encoding="cp1252")
    (root / "errall.ls").write_text(report_ls("ERRALL.LS"), encoding="cp1252")
    (root / "prog1.tp").write_bytes(b"\x00\x01binary")
    (root / "prog2.tp").write_bytes(b"\x00\x02binary")
    return root


def test_md_flat_detection(tmp_path):
    s = BackupSession(make_md(tmp_path))
    assert s.backup_type == "MD"
    m = s.manifest()
    assert m["backup_type"] == "MD"
    assert m["robot_name"] == ROBOT
    assert m["f_number"] == "F999999"
    # flat backup: relpath == basename
    assert s.rel(s.find("SUMMARY.DG")) == "summary.dg"
    tabs = m["tabs"]
    assert tabs["overview"] and tabs["io"] and tabs["programs"] and tabs["alarms"]
    assert tabs["frames"] is False and tabs["registers"] is False


def test_maint_recursive_index(tmp_path):
    s = BackupSession(make_maint(tmp_path))
    assert s.backup_type == "maintenance data"

    # summary.dg lives under a subfolder but resolves by basename
    p = s.find("summary.dg")
    assert p is not None
    assert "/" in s.rel(p)

    # exact relpath lookup also works (forward or back slashes)
    assert s.find(s.rel(p)) == p
    assert s.find(s.rel(p).replace("/", "\\")) == p


def test_maint_duplicate_basenames_resolved(tmp_path):
    s = BackupSession(make_maint(tmp_path))
    dupes = [n for n, paths in s.by_name.items() if len(paths) > 1]
    assert dupes, "expected duplicated basenames across subfolders"
    # errall.ls exists in mdb/ and md_ls/; the mdb controller dump wins
    errall = s.by_name.get("ERRALL.LS")
    assert errall and len(errall) >= 2
    assert s.find("ERRALL.LS").parent.name.lower() == "mdb"
    # alarm list dedupes by basename
    names = [p.name.upper() for p in s.alarm_files()]
    assert len(names) == len(set(names))


def test_maint_tabs(tmp_path):
    s = BackupSession(make_maint(tmp_path))
    m = s.manifest()
    tabs = m["tabs"]
    assert tabs["overview"] is True       # summary.dg in mdb/
    assert tabs["io"] is True             # via SUMMARY.DG fallback
    assert tabs["macros"] is True         # summary macro section
    assert tabs["programs"] is True       # md_ls/*.ls
    assert tabs["alarms"] is True
    assert tabs["frames"] is False        # no .VA sources
    assert tabs["registers"] is False
    assert s.program_files, "md_ls programs should classify"
    assert m["robot_name"] == ROBOT
    assert m["f_number"] == "F999999"


def test_allabove_tabs(tmp_path):
    s = BackupSession(make_allabove(tmp_path))
    assert s.backup_type == "all of the above"
    m = s.manifest()
    tabs = m["tabs"]
    assert tabs["overview"] is True
    assert tabs["io"] is True
    assert tabs["programs"] is True       # binary-only .tp list
    assert tabs["frames"] is False
    assert tabs["registers"] is False
    assert not s.program_files
    assert s.has_binary_programs()


def test_empty_folder_unknown(tmp_path):
    s = BackupSession(tmp_path)
    assert s.backup_type == "unknown"
    assert s.manifest()["tabs"]["files"] is True


def test_io_fallback_from_summary():
    """Backups without IOCONFIG/IOSTATE.DG carry the same signal tables in
    SUMMARY.DG's I/O sections - io_section_texts must hand the io_dg parsers
    bodies they can read, column quirks included."""
    state_text, cfg_text = io_section_texts(SUMMARY)
    assert state_text and cfg_text
    merged = merge_io(parse_io_config(cfg_text), parse_io_state(state_text))

    di1 = next(x for x in merged["signals"] if x["type"] == "DI" and x["index"] == 1)
    assert di1["comment"] == "*IMSTP"
    assert di1["state"] == "OFF"
    assert (di1["rack"], di1["slot"], di1["port"]) == (89, 1, 1)

    # FLG's two-per-line packing and the flush-against-bracket group state
    # survive the summary path too
    assert "F" in merged["types"]
    f = {x["index"]: x for x in merged["signals"] if x["type"] == "F"}
    assert f[1]["state"] == "ON" and f[513]["state"] == "OFF"
    go93 = next(x for x in merged["signals"] if x["type"] == "GO" and x["index"] == 93)
    assert go93["state"] == "21986" and go93["comment"] == "TotalSignA"

    # range + group assignments came through section 5
    assert any(a["type"] == "DI" and a["start"] == 1 and a["end"] == 800
               for a in merged["assignments"])
    assert any(a["type"] == "GI" and a["num_bits"] == 8
               for a in merged["assignments"])
