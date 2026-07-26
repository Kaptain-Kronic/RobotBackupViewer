"""Loading cameras into the CV-X simulator's flat folder.

The simulator takes ONE base path and lists the workspace folders directly
inside it, so the library's plant/line/dated tree is unusable to it. These tests
pin the export that bridges the two: what is OFFERED (only cameras with a real
workspace on disk), what it is NAMED (the shop's station convention, never two
cameras claiming one folder), and that a backup is only ever read."""
from pathlib import Path

import pytest

from backupviewer import keyence_workspace as kw, library, settings
from backupviewer.api import Api


@pytest.fixture
def env(monkeypatch, tmp_path):
    """An isolated %APPDATA% (settings + library) and a sim folder."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)
    sim = tmp_path / "KEYENCE" / "BackupViewer"
    settings.set_value("sim_root", str(sim))
    return tmp_path, sim


def _mkws(folder, ip="192.0.2.44", body=b"env blob"):
    (folder / kw.SD_DIR / "cv-x" / "setting").mkdir(parents=True)
    (folder / kw.SD_DIR / "cv-x" / "setting" / "env.dat").write_bytes(body)
    kw.write_workspace_xml(folder, ip)
    return folder


def _add_camera(tmp_path, station, line, cams=("CAM1",), ip="192.0.2.44"):
    latest = tmp_path / "lib" / line / "Latest" / station
    for c in cams:
        _mkws(latest / c, ip)
    return library.add_robot({"robot": station, "line": line, "plant": "FakePlant",
                              "device_type": "camera-keyence",
                              "latest_path": str(latest), "last_backup": "2026-07-25T10:00:00"})


def test_candidates_offer_only_cameras_with_a_real_workspace(env):
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    # a camera whose latest backup predates the workspace layout: no manifest
    old = tmp_path / "lib" / "RBB01" / "Latest" / "RB173"
    (old / "CAM1" / "cv-x" / "setting").mkdir(parents=True)
    library.add_robot({"robot": "RB173", "line": "RBB01", "device_type": "camera-keyence",
                       "latest_path": str(old)})
    # …and a FANUC robot, which is not a camera at all
    library.add_robot({"robot": "RB010R01B01", "line": "RBB01", "latest_path": str(tmp_path)})

    cams = Api().sim_candidates()["data"]["cameras"]
    assert [c["station"] for c in cams] == ["RB172"]     # the other two are absent
    assert cams[0]["name"] == "RB172"
    assert cams[0]["ip"] == "192.0.2.44"
    assert cams[0]["files"] == 2 and cams[0]["bytes"] > 0    # env.dat + workspace.xml
    assert cams[0]["already"] is False


def test_export_copies_the_picked_cameras_into_the_flat_folder(env):
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    _add_camera(tmp_path, "RB180", "RBB01", cams=("CAM1", "CAM2"))

    api = Api()
    cams = api.sim_candidates()["data"]["cameras"]
    assert sorted(c["name"] for c in cams) == ["RB172", "RB180CAM1", "RB180CAM2"]

    picked = [c["key"] for c in cams if c["name"] in ("RB172", "RB180CAM2")]
    res = api.sim_export(picked)["data"]
    assert sorted(e["name"] for e in res["exported"]) == ["RB172", "RB180CAM2"]
    assert not res["failed"]

    # FLAT: every workspace is a direct child of the one base path (the only
    # non-directory alongside them is our own ledger, which the simulator,
    # listing workspace FOLDERS, never looks at)
    assert sorted(p.name for p in sim.iterdir() if p.is_dir()) == ["RB172", "RB180CAM2"]
    assert [p.name for p in sim.iterdir() if not p.is_dir()] == [kw.EXPORT_LEDGER]
    for name in ("RB172", "RB180CAM2"):
        assert kw.is_workspace(sim / name)
        assert (sim / name / kw.SD_DIR / "cv-x" / "setting" / "env.dat").is_file()

    # a second pass reports what it will replace
    again = [c for c in api.sim_candidates()["data"]["cameras"] if c["name"] == "RB172"][0]
    assert again["already"] is True


def test_export_refreshes_a_camera_without_disturbing_its_neighbours(env):
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    _add_camera(tmp_path, "RB180", "RBB01")

    api = Api()
    api.sim_export([c["key"] for c in api.sim_candidates()["data"]["cameras"]])
    assert (sim / "RB172" / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"env blob"

    # the camera is backed up again with new content, then re-loaded alone
    src = tmp_path / "lib" / "RBB01" / "Latest" / "RB172" / "CAM1"
    (src / kw.SD_DIR / "cv-x" / "setting" / "env.dat").write_bytes(b"NEWER blob")
    rb172 = [c for c in api.sim_candidates()["data"]["cameras"] if c["name"] == "RB172"][0]
    api.sim_export([rb172["key"]])

    assert (sim / "RB172" / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"NEWER blob"
    assert kw.is_workspace(sim / "RB180")            # the neighbour is untouched
    assert not list(sim.glob("*.__tmp"))


def test_export_never_writes_into_the_backup(env):
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    src = tmp_path / "lib" / "RBB01" / "Latest" / "RB172" / "CAM1"
    before = sorted(str(p.relative_to(src)) for p in src.rglob("*"))

    api = Api()
    api.sim_export([c["key"] for c in api.sim_candidates()["data"]["cameras"]])

    assert sorted(str(p.relative_to(src)) for p in src.rglob("*")) == before


def test_a_handmade_workspace_blocks_the_export_until_confirmed(env):
    """Pointing the simulator folder at one that already holds hand-made saves is
    the natural thing to do — and the export must not quietly eat them."""
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    _add_camera(tmp_path, "RB180", "RBB01")
    handmade = sim / "RB172"
    _mkws(handmade, body=b"a week of work")

    api = Api()
    cams = {c["name"]: c for c in api.sim_candidates()["data"]["cameras"]}
    assert cams["RB172"]["foreign"] is True and cams["RB172"]["already"] is True
    assert cams["RB180"]["foreign"] is False

    res = api.sim_export([c["key"] for c in cams.values()])["data"]
    assert [b["name"] for b in res["blocked"]] == ["RB172"]
    assert [e["name"] for e in res["exported"]] == ["RB180"]      # the safe one still lands
    assert (handmade / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"a week of work"

    # the blocked row carries the key back, so the confirm step can redo just it
    again = api.sim_export([res["blocked"][0]["key"]], True)["data"]
    assert [e["name"] for e in again["exported"]] == ["RB172"]
    assert not again["blocked"]
    assert (sim / "RB172" / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"env blob"


def test_blocking_everything_is_not_reported_as_a_failure(env):
    """All-blocked must come back as a question to the user, not an error toast."""
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    _mkws(sim / "RB172", body=b"handmade")

    api = Api()
    res = api.sim_export([c["key"] for c in api.sim_candidates()["data"]["cameras"]])
    assert res["ok"] is True
    assert res["data"]["exported"] == [] and len(res["data"]["blocked"]) == 1


def test_our_own_earlier_export_is_replaced_without_a_prompt(env):
    tmp_path, sim = env
    _add_camera(tmp_path, "RB172", "RBB01")
    api = Api()
    keys = [c["key"] for c in api.sim_candidates()["data"]["cameras"]]
    api.sim_export(keys)

    cams = api.sim_candidates()["data"]["cameras"]
    assert cams[0]["already"] is True and cams[0]["foreign"] is False
    res = api.sim_export(keys)["data"]
    assert not res["blocked"] and [e["name"] for e in res["exported"]] == ["RB172"]


def test_export_with_nothing_picked_is_an_error_not_a_no_op(env):
    res = Api().sim_export([])
    assert res["ok"] is False
    assert res["error"]["code"] == "NOTHING_PICKED"


def test_sim_root_setting_round_trips(env):
    _tmp, sim = env
    api = Api()
    assert api.get_sim_root()["data"]["path"] == str(sim)

    other = str(sim.parent / "TESTFOLDER")
    assert api.set_sim_root(other)["data"]["path"] == other
    assert api.get_sim_root()["data"]["path"] == other
    assert settings.sim_root() == other

    assert api.set_sim_root("  ")["error"]["code"] == "BAD_PATH"


def test_sim_root_defaults_under_documents_keyence(monkeypatch, tmp_path):
    """Unset, it points somewhere the simulator's file dialog will already be —
    and it is NOT the library root (the library keeps history, this does not)."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(settings, "app_dir", lambda: appdata)
    root = Path(settings.sim_root())
    assert root.name == "BackupViewer"
    assert root.parent.name == "KEYENCE"
    assert str(root) != settings.library_root()
