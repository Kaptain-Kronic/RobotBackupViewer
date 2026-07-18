"""modeldb: registry import + normalized matching + builtin/import layering
(tmp appdata; BUILTIN emptied per test so only the layering test sees it)."""
from backupviewer import modeldb
from backupviewer.parsers import roboguidedef

from test_kinematics import _DEF


def _iso(monkeypatch, tmp_path, builtin=None):
    monkeypatch.setattr(modeldb.settings, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(modeldb, "BUILTIN", builtin or {})


def test_import_and_match(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "FakeBot_10-3d_NEW.def").write_text(_DEF, encoding="utf-8")
    # an EOAT def (no joint chain) is counted, not fatal
    (lib / "gripper.def").write_text(
        "<RobotCadFolder><General/></RobotCadFolder>", encoding="utf-8")
    (lib / "junk.def").write_text("not xml <", encoding="utf-8")

    out = modeldb.import_folder(str(lib))
    assert out["imported"] == 1 and out["skipped"] == 2
    # the FILENAME is the identity (envelope names are shared reach shells);
    # underscores read as spaces
    assert out["names"] == ["FakeBot 10"]
    assert modeldb.counts() == {"builtin": 0, "imported": 1}

    assert modeldb.match("Fake Bot 10")["name"] == "FakeBot 10"
    # the -IF dress suffix falls away (same arm, plate measured separately)
    assert modeldb.match("Fake Bot 10-IF")["name"] == "FakeBot 10"
    # a near-miss variant must NOT borrow a different arm
    assert modeldb.match("Fake Bot 10S") is None
    assert modeldb.match("Fake Bot 10/35") is None
    assert modeldb.match("Other Bot") is None
    assert modeldb.match("") is None

    # re-import updates in place (no duplicates)
    out2 = modeldb.import_folder(str(lib))
    assert out2["imported"] == 1 and modeldb.counts()["imported"] == 1

    kin = modeldb.match("Fake Bot 10")["kin"]
    assert [j["n"] for j in kin["joints"]] == [1, 2, 3, 4, 5, 6]


def test_builtin_layer_and_override(monkeypatch, tmp_path):
    builtin = {"FAKEBOT10": {"name": "FakeBot 10", "kin": {"zero": [0] * 6},
                             "validated": {"robots": 3}}}
    _iso(monkeypatch, tmp_path, builtin)
    # built-in answers with zero imports, tagged as such
    m = modeldb.match("Fake Bot 10-IF")
    assert m["source_kind"] == "builtin" and m["validated"]["robots"] == 3
    assert modeldb.counts() == {"builtin": 1, "imported": 0}
    # a user import of the same type wins over the built-in
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "FakeBot_10-3d_NEW.def").write_text(_DEF, encoding="utf-8")
    modeldb.import_folder(str(lib))
    m2 = modeldb.match("Fake Bot 10")
    assert m2["source_kind"] == "imported"
    assert [j["n"] for j in m2["kin"]["joints"]] == [1, 2, 3, 4, 5, 6]


def test_shipping_builtins_are_wellformed():
    """The generated table: the full FANUC type set, normalized keys, runnable
    chains; the validated tier carries real provenance."""
    from backupviewer.kinematics_builtin import BUILTIN
    from backupviewer.parsers import kinematics
    assert len(BUILTIN) >= 200
    validated = 0
    for key, e in BUILTIN.items():
        assert key == roboguidedef.normalize_type(e["name"])
        kin = e["kin"]
        assert len(kin["zero"]) == 6 and 1 <= len(kin["joints"]) <= 9
        assert [j["n"] for j in kin["joints"]] == list(range(1, len(kin["joints"]) + 1))
        assert kin["faceplate"]["p"] and len(kin["faceplate"]["wpr"]) == 3
        # every shipped chain must actually run
        f = kinematics.chain_frames(kin, [0.0] * len(kin["joints"]))
        assert f["faceplate"][0][3] != 0 or f["faceplate"][2][3] != 0
        v = e.get("validated")
        if v:
            validated += 1
            assert v["robots"] >= 1 and v["max_xy_mm"] < 1.0 and v["max_ori_deg"] < 0.1
    assert validated >= 5


def test_default_library_detection(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    assert modeldb.default_library() == ""  # nothing there
    lib = tmp_path / "FANUC" / "ROBOGUIDECore" / "Robot Library"
    lib.mkdir(parents=True)
    assert modeldb.default_library() == ""  # folder but no defs
    (lib / "FakeBot_10-3d_NEW.def").write_text(_DEF, encoding="utf-8")
    assert modeldb.default_library() == str(lib)