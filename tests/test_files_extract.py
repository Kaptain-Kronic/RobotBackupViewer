"""files_extract: the files tab's tick-and-copy-to-USB endpoint.

The editor's ws_export contract, restated for raw files: byte-for-byte copies,
never into a backup, .part -> os.replace landing, one robot-named subfolder
with relative paths kept beneath it, source mtimes preserved. Fully synthetic
and identifier-clean (fake RB* robots, TEST-NET-free).
"""
import json
import os

import pytest

from backupviewer.api import Api
from backupviewer.ftpbackup import long_path

LS_BYTES = b"/PROG  MAIN\r\n/ATTR\r\n/MN\r\n   1:  !setup ;\r\n/END\r\n"
DG_BYTES = b"Robot: RB010R01B01\r\nmixed line endings\nmasked ********\r\n"
VR_BYTES = bytes(range(256)) * 3          # binary, NULs and cp1252 traps included
MTIME = 1_700_000_000                     # a fixed, assertable stamp


def _mk(root, robot=None):
    root.mkdir(parents=True)
    (root / "MAIN.LS").write_bytes(LS_BYTES)
    (root / "SUMMARY.DG").write_bytes(DG_BYTES)
    sub = root / "md_ls"
    sub.mkdir()
    (sub / "MAIN.LS").write_bytes(LS_BYTES.replace(b"!setup", b"!deep "))
    (sub / "SAVED.VR").write_bytes(VR_BYTES)
    os.utime(root / "SUMMARY.DG", (MTIME, MTIME))
    if robot:
        (root / "backup.json").write_text(
            json.dumps({"robot": robot, "complete": True}), encoding="utf-8")
    return root


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("BV_NO_WATCHER", "1")
    monkeypatch.setattr("backupviewer.settings.set_value", lambda *a, **k: None)
    return Api()


@pytest.fixture
def backup(api, tmp_path):
    root = _mk(tmp_path / "RB010R01B01" / "2026_01_01" / "12_00_00", "RB010R01B01")
    assert api.open_backup(str(root))["ok"]
    return root


def _ok(res):
    assert res["ok"], res
    return res["data"]


def _err(res):
    assert not res["ok"], res
    return res["error"]["code"]


# ---------------------------------------------------------------- copying

def test_extract_copies_bytes_structure_and_mtime(api, backup, tmp_path):
    dest = tmp_path / "usb"
    data = _ok(api.files_extract(
        ["MAIN.LS", "md_ls/MAIN.LS", "md_ls/SAVED.VR", "SUMMARY.DG"], str(dest)))
    lab = dest / "RB010R01B01"            # backup.json's word for the robot
    assert (lab / "MAIN.LS").read_bytes() == LS_BYTES
    assert (lab / "md_ls" / "MAIN.LS").read_bytes() == LS_BYTES.replace(b"!setup", b"!deep ")
    assert (lab / "md_ls" / "SAVED.VR").read_bytes() == VR_BYTES
    assert (lab / "SUMMARY.DG").read_bytes() == DG_BYTES
    # the copy keeps the source's modified time - extracted evidence dates itself
    assert int((lab / "SUMMARY.DG").stat().st_mtime) == MTIME
    assert data["count"] == 4
    assert data["root"] == "RB010R01B01"
    assert data["bytes"] == sum(len(b) for b in (
        LS_BYTES, LS_BYTES.replace(b"!setup", b"!deep "), VR_BYTES, DG_BYTES))
    assert sorted(data["files"]) == [
        "RB010R01B01/MAIN.LS", "RB010R01B01/SUMMARY.DG",
        "RB010R01B01/md_ls/MAIN.LS", "RB010R01B01/md_ls/SAVED.VR"]
    assert not list(dest.rglob("*.part"))


def test_extract_resolves_case_and_backslash(api, backup, tmp_path):
    dest = tmp_path / "usb"
    data = _ok(api.files_extract(["main.ls", "md_ls\\saved.vr"], str(dest)))
    # written under the INDEX casing, not the caller's
    assert sorted(data["files"]) == [
        "RB010R01B01/MAIN.LS", "RB010R01B01/md_ls/SAVED.VR"]


def test_extract_dedupes_a_double_tick(api, backup, tmp_path):
    dest = tmp_path / "usb"
    assert _ok(api.files_extract(["MAIN.LS", "main.ls"], str(dest)))["count"] == 1


def test_extract_label_falls_back_to_hint_then_folder(api, tmp_path):
    root = _mk(tmp_path / "RB020R01B01")          # no backup.json
    assert api.open_backup(str(root))["ok"]
    d1 = tmp_path / "u1"
    assert _ok(api.files_extract(["MAIN.LS"], str(d1), "RB030R01B01"))["root"] == "RB030R01B01"
    assert (d1 / "RB030R01B01" / "MAIN.LS").is_file()
    d2 = tmp_path / "u2"
    assert _ok(api.files_extract(["MAIN.LS"], str(d2)))["root"] == "RB020R01B01"


# ---------------------------------------------------------------- guards

def test_extract_refuses_dest_at_or_inside_the_backup(api, backup, tmp_path):
    assert _err(api.files_extract(["MAIN.LS"], str(backup))) == "BAD_DEST"
    assert _err(api.files_extract(["MAIN.LS"], str(backup / "sub"))) == "BAD_DEST"


def test_extract_refuses_backup_looking_dest(api, backup, tmp_path):
    dest = tmp_path / "looks_like_a_backup"
    dest.mkdir()
    (dest / "SOME.LS").write_bytes(b"/PROG X\r\n/END\r\n")
    assert _err(api.files_extract(["MAIN.LS"], str(dest))) == "BAD_DEST"


def test_extract_unknown_rel_is_all_or_nothing(api, backup, tmp_path):
    dest = tmp_path / "usb"
    assert _err(api.files_extract(["MAIN.LS", "NOSUCH.XX"], str(dest))) == "NOT_FOUND"
    assert _err(api.files_extract(["../outside.txt"], str(dest))) == "NOT_FOUND"
    assert not dest.exists()


def test_extract_empty_rejected(api, backup, tmp_path):
    assert _err(api.files_extract([], str(tmp_path / "usb"))) == "NO_FILES"
    assert _err(api.files_extract(["", "/"], str(tmp_path / "usb"))) == "NO_FILES"
    assert _err(api.files_extract(["MAIN.LS"], "")) == "BAD_DEST"


def test_extract_leaves_the_backup_untouched(api, backup, tmp_path):
    before = sorted(p.relative_to(backup).as_posix() for p in backup.rglob("*"))
    bytes_before = (backup / "MAIN.LS").read_bytes()
    _ok(api.files_extract(["MAIN.LS", "md_ls/SAVED.VR"], str(tmp_path / "usb")))
    assert sorted(p.relative_to(backup).as_posix() for p in backup.rglob("*")) == before
    assert (backup / "MAIN.LS").read_bytes() == bytes_before


# ---------------------------------------------------------------- long paths

def test_extract_lands_past_max_path(api, backup, tmp_path):
    """Plant PCs run LongPathsEnabled=0; a deep USB folder must still work."""
    deep = tmp_path
    while len(str(deep)) < 250:
        deep = deep / "a_deep_extract_destination_segment"
    data = _ok(api.files_extract(["md_ls/MAIN.LS"], str(deep)))
    target = data["dest"] + os.sep + "RB010R01B01" + os.sep + "md_ls" + os.sep + "MAIN.LS"
    assert len(target) > 260
    with open(long_path(target), "rb") as f:
        assert f.read() == LS_BYTES.replace(b"!setup", b"!deep ")
