"""The CV-X simulator workspace manifest - the one file that turns a pulled
settings tree into something the simulator can open.

The byte-exactness assertions here are the point of this module: the simulator
wrote these files, and it is the thing that has to read ours back. The fixture is
a verbatim copy of a real simulator-written workspace.xml with the plant's IP
swapped for TEST-NET, so a regression in BOM / CRLF / field order / spelling
(`Tumbnail`, sic) fails loudly instead of shipping a workspace that silently will
not open."""
import pytest

from backupviewer import keyence_workspace as kw

# A real simulator-written workspace.xml, byte-for-byte, with the camera IP
# replaced by TEST-NET. Note: BOM, CRLF, and NO trailing newline.
SAMPLE = (
    b"\xef\xbb\xbf"
    b'<?xml version="1.0" encoding="utf-8"?>\r\n'
    b'<Workspace xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    b'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n'
    b"  <ControllerIpAddress>192.0.2.44</ControllerIpAddress>\r\n"
    b"  <ControllerName />\r\n"
    b"  <ControllerType>12</ControllerType>\r\n"
    b"  <SoftwareGrade>1078219315</SoftwareGrade>\r\n"
    b"  <ControllerID>-1</ControllerID>\r\n"
    b"  <Version>1.0.0000</Version>\r\n"
    b"  <SimulatorSizeHeight>-1</SimulatorSizeHeight>\r\n"
    b"  <SimulatorSizeWidth>-1</SimulatorSizeWidth>\r\n"
    b"  <SimulatorPositionX>-1</SimulatorPositionX>\r\n"
    b"  <SimulatorPositionY>-1</SimulatorPositionY>\r\n"
    b"  <ImageBarSizeHeight>-1</ImageBarSizeHeight>\r\n"
    b"  <ImageBarSizeWidth>-1</ImageBarSizeWidth>\r\n"
    b"  <ImageBarPositionX>-1</ImageBarPositionX>\r\n"
    b"  <ImageBarPositionY>-1</ImageBarPositionY>\r\n"
    b"  <ImageBarSticking>false</ImageBarSticking>\r\n"
    b"  <ImageBarFormMode>0</ImageBarFormMode>\r\n"
    b"  <ImageBarViewMode>0</ImageBarViewMode>\r\n"
    b"  <TumbnailSizeType>2</TumbnailSizeType>\r\n"
    b"</Workspace>"
)

# The camera's own short dialect, from /SD1/cv-x/workspace/<NAME>/ - no BOM, LF
# line endings, 8 fields, and a real ControllerID. We never write this one, but a
# workspace copied off a camera has to still parse.
CAMERA_SAMPLE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<Workspace xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
    "  <ControllerIpAddress>192.0.2.121</ControllerIpAddress>\n"
    "  <ControllerName />\n"
    "  <ControllerType>12</ControllerType>\n"
    "  <SoftwareGrade>1078219315</SoftwareGrade>\n"
    "  <ControllerID>194003241</ControllerID>\n"
    "  <Version>1.0.0000</Version>\n"
    "</Workspace>"
)


def test_render_matches_a_real_simulator_file_byte_for_byte():
    assert kw.render_workspace_xml("192.0.2.44") == SAMPLE


def test_render_keeps_the_bytes_the_simulator_cares_about():
    out = kw.render_workspace_xml("192.0.2.44")
    assert out.startswith(b"\xef\xbb\xbf")          # BOM
    assert out.count(b"\r\n") == 20                 # CRLF throughout
    assert out.count(b"\n") == out.count(b"\r\n")   # ...and never a bare LF
    assert out.endswith(b"</Workspace>")            # no trailing newline


def test_software_grade_is_packed_ascii():
    """The grade is not a magic number - it is 4 ASCII chars packed big-endian."""
    assert kw.grade_text(kw.SOFTWARE_GRADE) == "@DR3"
    assert kw.grade_text(kw.NEW_WORKSPACE_GRADE) == "@ALL"
    assert kw.grade_value("@DR3") == kw.SOFTWARE_GRADE
    assert kw.grade_text("not a number") == ""
    assert kw.grade_text(0) == ""                   # NULs are not printable ASCII
    with pytest.raises(ValueError):
        kw.grade_value("toolong")


def test_overrides_reach_the_document():
    out = kw.render_workspace_xml("192.0.2.9", controller_type=1,
                                  software_grade=kw.NEW_WORKSPACE_GRADE,
                                  controller_id=194003241).decode("utf-8-sig")
    assert "<ControllerType>1</ControllerType>" in out
    assert f"<SoftwareGrade>{kw.NEW_WORKSPACE_GRADE}</SoftwareGrade>" in out
    assert "<ControllerID>194003241</ControllerID>" in out


def test_controller_name_self_closes_when_blank_and_escapes_when_not():
    assert b"  <ControllerName />\r\n" in kw.render_workspace_xml("192.0.2.44")
    named = kw.render_workspace_xml("192.0.2.44", controller_name="RB172 & CAM1")
    assert b"<ControllerName>RB172 &amp; CAM1</ControllerName>" in named


def test_parse_reads_both_dialects():
    sim = kw.parse_workspace_xml(SAMPLE.decode("utf-8-sig"))
    assert sim["ControllerIpAddress"] == "192.0.2.44"
    assert sim["ControllerName"] == ""               # self-closed tag
    assert sim["software_grade_text"] == "@DR3"
    assert sim["TumbnailSizeType"] == "2"

    cam = kw.parse_workspace_xml(CAMERA_SAMPLE)
    assert cam["ControllerIpAddress"] == "192.0.2.121"
    assert cam["ControllerID"] == "194003241"        # the camera knows its own id
    assert cam["software_grade_text"] == "@DR3"
    assert "TumbnailSizeType" not in cam             # short dialect, not padded out


def test_render_then_parse_round_trips():
    out = kw.render_workspace_xml("192.0.2.77", controller_name="RB172-CVX")
    got = kw.parse_workspace_xml(out.decode("utf-8-sig"))
    assert got["ControllerIpAddress"] == "192.0.2.77"
    assert got["ControllerName"] == "RB172-CVX"


def _mkws(folder, ip="192.0.2.44"):
    """A minimal but REAL workspace on disk: SD1/ tree + the manifest."""
    (folder / kw.SD_DIR / "cv-x" / "setting" / "001").mkdir(parents=True)
    (folder / kw.SD_DIR / "cv-x" / "setting" / "env.dat").write_bytes(b"env blob")
    kw.write_workspace_xml(folder, ip)
    return folder


def test_workspaces_in_finds_the_cameras_under_a_backup(tmp_path):
    dated = tmp_path / "2026_07_25" / "16_47_32"
    _mkws(dated / "CAM1")
    _mkws(dated / "CAM2")
    (dated / "backup.json").write_text("{}", encoding="utf-8")     # not a workspace
    (dated / "notes").mkdir()                                      # nor this

    assert [p.name for p in kw.workspaces_in(dated)] == ["CAM1", "CAM2"]
    # pointed AT a workspace, it is the answer - not searched inside
    assert kw.workspaces_in(dated / "CAM1") == [dated / "CAM1"]
    assert kw.workspaces_in(tmp_path / "gone") == []


def test_plan_exports_names_by_station_and_adds_the_label_only_when_needed():
    """The shop's own convention: DD183 alone, but 040R01B01CAM1/CAM2 when the
    station really does have two cameras."""
    plan = kw.plan_exports([
        {"plant": "P", "line": "L1", "station": "DD183", "label": "CAM1"},
        {"plant": "P", "line": "L1", "station": "040R01B01", "label": "CAM1"},
        {"plant": "P", "line": "L1", "station": "040R01B01", "label": "CAM2"},
    ])
    assert {p["station"] + "/" + p["label"]: p["name"] for p in plan} == {
        "DD183/CAM1": "DD183",
        "040R01B01/CAM1": "040R01B01CAM1",
        "040R01B01/CAM2": "040R01B01CAM2",
    }
    assert all(not p["why"] for p in plan)         # nothing needed qualifying


def test_plan_exports_does_not_double_a_cam_tag_the_camera_named_itself():
    """A self-named CV-X takes its name from a program the tech called
    `…CAM1`, so a two-camera station arrives already carrying a CAM tag.
    Appending the label would read `RB130R01B01CAM1CAM2` — wrong AND misleading
    about which camera it is. The trailing tag is replaced instead."""
    plan = {p["label"]: p["name"] for p in kw.plan_exports([
        {"plant": "P", "line": "L", "station": "RB130R01B01CAM1", "label": "CAM1"},
        {"plant": "P", "line": "L", "station": "RB130R01B01CAM1", "label": "CAM2"},
    ])}
    assert plan == {"CAM1": "RB130R01B01CAM1", "CAM2": "RB130R01B01CAM2"}

    # separators before the tag go with it, and a station with no tag is unchanged
    assert {p["label"]: p["name"] for p in kw.plan_exports([
        {"plant": "P", "line": "L", "station": "RB172-Cam 1", "label": "CAM1"},
        {"plant": "P", "line": "L", "station": "RB172-Cam 1", "label": "CAM2"},
    ])} == {"CAM1": "RB172CAM1", "CAM2": "RB172CAM2"}
    assert {p["label"]: p["name"] for p in kw.plan_exports([
        {"plant": "P", "line": "L", "station": "RB180", "label": "CAM1"},
        {"plant": "P", "line": "L", "station": "RB180", "label": "CAM2"},
    ])} == {"CAM1": "RB180CAM1", "CAM2": "RB180CAM2"}

    # a SINGLE-camera station keeps its self-given name untouched
    assert kw.plan_exports([
        {"plant": "P", "line": "L", "station": "RB172CAM1", "label": "CAM1"}
    ])[0]["name"] == "RB172CAM1"


def test_plan_exports_never_lets_two_stations_claim_one_folder():
    """The flat folder mixes every line together, and the library really does
    hold the same station name on two lines - the second must not overwrite the
    first, and must SAY why it was renamed."""
    plan = {p["line"]: p for p in kw.plan_exports([
        {"plant": "P", "line": "LINE-A", "station": "RB172", "label": "CAM1"},
        {"plant": "P", "line": "LINE-B", "station": "RB172", "label": "CAM1"},
    ])}
    assert plan["LINE-A"]["name"] == "RB172"               # first in sort order keeps it
    assert plan["LINE-B"]["name"] == "RB172-LINE-B"
    assert "RB172" in plan["LINE-B"]["why"]
    assert not plan["LINE-A"]["why"]

    # same station name on the same line in two PLANTS falls through to the plant
    plan2 = {p["plant"]: p for p in kw.plan_exports([
        {"plant": "P1", "line": "L", "station": "RB172", "label": "CAM1"},
        {"plant": "P2", "line": "L", "station": "RB172", "label": "CAM1"},
    ])}
    assert plan2["P1"]["name"] == "RB172"
    assert plan2["P2"]["name"] not in ("RB172", "")
    assert plan2["P2"]["name"] != plan2["P1"]["name"]


def test_plan_exports_is_stable_and_scrubs_unsafe_names():
    items = [{"plant": "P", "line": "L", "station": "RB/172:CAM", "label": "CAM1"}]
    assert kw.plan_exports(items)[0]["name"] == "RB_172_CAM"
    # order in does not change the answer out
    a = [{"plant": "P", "line": "L2", "station": "X", "label": "CAM1"},
         {"plant": "P", "line": "L1", "station": "X", "label": "CAM1"}]
    assert ([p["name"] for p in kw.plan_exports(a)]
            == [p["name"] for p in kw.plan_exports(list(reversed(a)))])


def test_export_refuses_to_destroy_a_folder_it_did_not_create(tmp_path):
    """The one destructive path in the feature. A hand-made simulator save looks
    EXACTLY like ours (same format), so the only thing separating "replace our
    own last export" from "delete somebody's work" is the ledger."""
    src = _mkws(tmp_path / "backup" / "CAM1")
    sim = tmp_path / "simfolder"

    # a workspace somebody made by hand, sitting where our camera wants to land
    handmade = _mkws(sim / "RB172")
    (handmade / kw.SD_DIR / "precious.dat").write_bytes(b"a week of work")

    assert kw.would_replace_foreign(sim, "RB172") is True
    assert kw.is_ours(sim, "RB172") is False
    with pytest.raises(kw.ForeignWorkspace):
        kw.export_workspace(src, sim, "RB172")
    assert (handmade / kw.SD_DIR / "precious.dat").read_bytes() == b"a week of work"

    # only a deliberate confirmation goes through
    assert kw.export_workspace(src, sim, "RB172", replace_foreign=True) == sim / "RB172"
    assert not (sim / "RB172" / kw.SD_DIR / "precious.dat").exists()

    # and now it IS ours, so the next export needs no confirmation
    assert kw.is_ours(sim, "RB172") is True
    assert kw.would_replace_foreign(sim, "RB172") is False
    assert kw.export_workspace(src, sim, "RB172") == sim / "RB172"


def test_a_folder_we_created_is_replaced_freely(tmp_path):
    src = _mkws(tmp_path / "backup" / "CAM1")
    sim = tmp_path / "simfolder"

    assert kw.would_replace_foreign(sim, "RB172") is False   # nothing there at all
    kw.export_workspace(src, sim, "RB172")
    assert (sim / kw.EXPORT_LEDGER).is_file()
    assert "RB172" in kw.read_ledger(sim)
    # the ledger sits at the BASE PATH, never inside a workspace the simulator reads
    assert sorted(p.name for p in (sim / "RB172").iterdir()) == [kw.SD_DIR, kw.WORKSPACE_XML]


def test_an_unreadable_or_missing_ledger_fails_toward_caution(tmp_path):
    """A wiped or corrupt ledger must make us ASK, never assume the folder is
    ours - the cost of asking is a click, the cost of assuming is somebody's
    workspace."""
    src = _mkws(tmp_path / "backup" / "CAM1")
    sim = tmp_path / "simfolder"
    kw.export_workspace(src, sim, "RB172")
    assert kw.is_ours(sim, "RB172") is True

    (sim / kw.EXPORT_LEDGER).write_text("{ not json", encoding="utf-8")
    assert kw.read_ledger(sim) == {}
    assert kw.would_replace_foreign(sim, "RB172") is True
    with pytest.raises(kw.ForeignWorkspace):
        kw.export_workspace(src, sim, "RB172")

    (sim / kw.EXPORT_LEDGER).unlink()
    assert kw.would_replace_foreign(sim, "RB172") is True


def test_export_copies_and_replaces_without_touching_the_source(tmp_path):
    src = _mkws(tmp_path / "backup" / "CAM1", "192.0.2.44")
    sim = tmp_path / "simfolder"

    dest = kw.export_workspace(src, sim, "DD183")
    assert dest == sim / "DD183"
    assert kw.is_workspace(dest)
    assert (dest / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"env blob"

    # re-exporting REPLACES: the stale file from the old copy is gone
    (dest / kw.SD_DIR / "cv-x" / "setting" / "stale.dat").write_bytes(b"old")
    (src / kw.SD_DIR / "cv-x" / "setting" / "new.dat").write_bytes(b"new")
    kw.export_workspace(src, sim, "DD183")
    assert not (dest / kw.SD_DIR / "cv-x" / "setting" / "stale.dat").exists()
    assert (dest / kw.SD_DIR / "cv-x" / "setting" / "new.dat").is_file()
    assert not list(sim.glob("*.__tmp"))          # no half-copy left visible

    # the backup is read-only evidence - the export must not have changed it
    assert kw.is_workspace(src)
    assert not (src / kw.SD_DIR / "cv-x" / "setting" / "stale.dat").exists()

    # and a folder that is not a workspace is refused rather than half-copied
    plain = tmp_path / "plain"
    plain.mkdir()
    assert kw.export_workspace(plain, sim, "NOPE") is None
    assert not (sim / "NOPE").exists()


def test_write_and_detect(tmp_path):
    ws = tmp_path / "RB172-CVX"
    (ws / kw.SD_DIR / "cv-x" / "setting").mkdir(parents=True)

    assert not kw.is_workspace(ws)                   # SD1/ alone is not a workspace
    dest = kw.write_workspace_xml(ws, "192.0.2.44")
    assert dest.read_bytes() == SAMPLE
    assert kw.is_workspace(ws)
    assert not list(ws.glob("*.tmp"))                # no half-manifest left behind

    assert kw.read_workspace_xml(dest)["ControllerIpAddress"] == "192.0.2.44"
    assert kw.read_workspace_xml(tmp_path / "nope.xml") == {}   # unreadable -> {}

    bare = tmp_path / "bare"                         # workspace.xml alone is not one
    bare.mkdir()
    (bare / kw.WORKSPACE_XML).write_bytes(SAMPLE)
    assert not kw.is_workspace(bare)
