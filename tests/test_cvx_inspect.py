"""A CV-X naming itself out of its inspection programs.

The format pinned here (name at 0x4c, echoed at 0x398) was established against
154 real inspect.dat files across 13 cameras; the fixtures below are synthetic
blobs in that shape, and the name-choosing cases reproduce the real drift those
cameras showed - dated saves, variants, typos - with identifier-clean tags."""
import json

import pytest

from backupviewer import keyencebackup, keyence_workspace as kw
from backupviewer.parsers import cvx_inspect as ci


def _blob(name, echo=None, size=0x1000):
    """A synthetic inspect.dat: the name at 0x4c and its echo at 0x398."""
    b = bytearray(b"\x00" * size)
    b[0x14:0x18] = b"0008"                       # the fixed header runs seen in real files
    enc = name.encode("cp1252")
    b[ci.NAME_OFFSET:ci.NAME_OFFSET + len(enc)] = enc
    e = (name if echo is None else echo).encode("cp1252")
    b[ci.NAME_ECHO_OFFSET:ci.NAME_ECHO_OFFSET + len(e)] = e
    return bytes(b)


def test_program_name_reads_both_copies():
    assert ci.program_name(_blob("RB130R01B01CAM1")) == "RB130R01B01CAM1"
    # trailing junk after the NUL is not part of the name
    assert ci.program_name(_blob("RB172")) == "RB172"


def test_program_name_requires_the_two_copies_to_agree():
    """The echo is the whole reason this read is trustworthy: a mis-seek or a
    file that isn't an inspect.dat must yield NOTHING, not a plausible name."""
    assert ci.program_name(_blob("RB172", echo="SOMETHINGELSE")) == ""
    assert ci.program_name(_blob("RB172", echo="")) == ""
    assert ci.program_name(b"") == ""
    assert ci.program_name(b"\x00" * 64) == ""               # too short to hold both
    assert ci.program_name(b"RB172" * 500) == ""             # text everywhere, no structure


def test_default_slot_names_are_not_camera_names():
    """`Neu115` / `Nouveau004` sit near the real name in every file — they are
    the controller's own localized defaults and must never win."""
    for d in ("Neu115", "Nouveau004", "New", "Program 3", "nuevo12"):
        assert ci.is_default_name(d), d
    for real in ("RB130R01B01CAM1", "RB172CAM01DOORHINGE", "RC90R1B2"):
        assert not ci.is_default_name(real), real
    assert ci.camera_name({"000": "Neu115", "001": "Nouveau004"}) == ""


def test_camera_name_takes_the_name_that_repeats():
    """Real cameras carry dated saves and variants alongside the steady tag;
    the one that repeats across programs is the camera's identity."""
    assert ci.camera_name({
        "000": "RB130R01 20250224", "001": "RB130R1B1CAM1",
        "002": "RB130R01B01 20250228", "005": "RB130R01B01CAM1",
        "011": "RB130R01B01CAM1", "019": "RB130R01B01CAM1",
    }) == "RB130R01B01CAM1"
    assert ci.camera_name({
        "000": "RB20R1B1CAM1_400prt", "001": "RB20R1B1CAM1", "011": "RB20R1B1CAM1",
    }) == "RB20R1B1CAM1"
    assert ci.camera_name({"018": "RB40R1B1CAM2"}) == "RB40R1B1CAM2"


def test_camera_name_breaks_ties_on_the_lowest_program():
    """All-distinct names (seen on a real camera) must still resolve, and the
    oldest slot is the plainest tag in practice."""
    assert ci.camera_name({
        "000": "RB200R01B01CAM2", "001": "RB200R01B01-20250910", "010": "RB200R01B01CAM1",
    }) == "RB200R01B01CAM2"
    assert ci.camera_name({}) == "" and ci.camera_name(None) == ""
    assert ci.camera_name({"000": "  ", "001": ""}) == ""


def _snapshot(tmp_path, progs, label="CAM1"):
    """A pulled CV-X snapshot: <dated>/<label>/SD1/cv-x/setting/<NNN>/inspect.dat"""
    setting = tmp_path / label / kw.SD_DIR / "cv-x" / "setting"
    for prog, name in progs.items():
        d = setting / prog
        d.mkdir(parents=True)
        (d / "inspect.dat").write_bytes(_blob(name))
    return tmp_path


def test_name_from_backup_reads_a_pulled_snapshot(tmp_path):
    root = _snapshot(tmp_path, {"000": "RB172CAM1_400prt",
                                "001": "RB172CAM1", "011": "RB172CAM1"})
    assert keyencebackup.name_from_backup(root) == {"name": "RB172CAM1", "model": ""}


def test_name_from_backup_ignores_files_that_are_not_program_slots(tmp_path):
    """Only `setting/<NNN>/inspect.dat` counts — a stray inspect.dat elsewhere in
    the tree (a recovery copy, a saved workspace) must not name the camera."""
    root = _snapshot(tmp_path, {"000": "RB172CAM1"})
    stray = root / "CAM1" / kw.SD_DIR / "cv-x" / "workspace" / "OLD"
    stray.mkdir(parents=True)
    (stray / "inspect.dat").write_bytes(_blob("WRONGNAME"))
    assert keyencebackup.name_from_backup(root)["name"] == "RB172CAM1"


def test_name_from_backup_never_raises(tmp_path):
    """Identity work must not sink a finished backup."""
    assert keyencebackup.name_from_backup(tmp_path / "nope") == {"name": "", "model": ""}
    assert keyencebackup.name_from_backup("") == {"name": "", "model": ""}
    assert keyencebackup.name_from_backup(None) == {"name": "", "model": ""}

    bad = tmp_path / "CAM1" / kw.SD_DIR / "cv-x" / "setting" / "000"
    bad.mkdir(parents=True)
    (bad / "inspect.dat").write_bytes(b"\x01\x02\x03")        # unreadable shape
    assert keyencebackup.name_from_backup(tmp_path)["name"] == ""


@pytest.fixture
def lib(monkeypatch, tmp_path):
    """Isolated %APPDATA% AND a library root - teach_camera_name moves the
    camera's folder, and relocate_robot rightly refuses to move anything that
    isn't inside the configured library."""
    from backupviewer import settings
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)
    root = tmp_path / "lib"
    root.mkdir()
    settings.set_value("library_root", str(root))
    return root


def test_a_camera_discovered_as_an_ip_renames_itself(lib):
    """The end of the story the user actually sees: a CV-X added by discovery is
    named `192.0.2.44`, and the backup it takes teaches it its real tag — folder
    and all, so a rescan doesn't revert it."""
    from backupviewer import library

    root = lib / "RBB01" / "192.0.2.44" / "2026_07_25" / "10_00_00"
    _snapshot(root, {"000": "RB172CAM1", "001": "RB172CAM1"})
    root.mkdir(parents=True, exist_ok=True)
    (root / "backup.json").write_text(json.dumps({"complete": True}), encoding="utf-8")

    e = library.add_robot({"robot": "192.0.2.44", "ips": ["192.0.2.44"], "line": "RBB01",
                           "device_type": "camera-keyence", "latest_path": str(root)})
    assert library._is_placeholder_name(e) is True

    ident = keyencebackup.name_from_backup(root)
    assert ident["name"] == "RB172CAM1"
    taught = library.teach_camera_name(e["id"], ident["name"], ident["model"])
    assert taught["robot"] == "RB172CAM1"
    # the placeholder is remembered so anything filed under it still re-merges
    assert any(a.get("robot") == "192.0.2.44" for a in (taught.get("aliases") or []))
    # files are law: the FOLDER moved too, so the next rescan won't revert it
    assert (lib / "RBB01" / "RB172CAM1").is_dir()
    assert not (lib / "RBB01" / "192.0.2.44").exists()


def test_a_name_someone_typed_is_never_overwritten(lib):
    from backupviewer import library

    _snapshot(lib / "snap", {"000": "RB172CAM1"})
    e = library.add_robot({"robot": "the good camera", "ips": ["192.0.2.44"],
                           "device_type": "camera-keyence"})
    kept = library.teach_camera_name(e["id"], "RB172CAM1")
    assert kept["robot"] == "the good camera"
