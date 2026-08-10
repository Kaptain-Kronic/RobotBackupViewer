"""CV-X model blobs: container headers, the zlib->STL facet gate, mesh
building, and the identity/calibration reads.

Fixtures are synthetic and identifier-clean: a tetrahedron whose facet
records are packed here with normals computed and normalised (never
hand-typed), wrapped in containers built to the real headers' shape. NUL
padding is always built programmatically (`bytearray(n)` / `bytes(n)`),
never written as literal escapes.
"""
import struct
import zlib
from pathlib import Path

import pytest

from backupviewer import keyence_workspace
from backupviewer.api import Api
from backupviewer.parsers import cvx_models
from backupviewer.session import BackupSession


# -- fixture builders -----------------------------------------------------------

_TETRA_VERTS = ((1.0, 1.0, 1.0), (1.0, -1.0, -1.0), (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0))
_TETRA_FACES = ((0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2))


def tetra_facets():
    """4 x 50-byte binary-STL facet records - 12 vertex corners that dedupe
    to 4 unique points, with correct unit normals."""
    out = bytearray()
    for a, b, c in _TETRA_FACES:
        va, vb, vc = _TETRA_VERTS[a], _TETRA_VERTS[b], _TETRA_VERTS[c]
        u = [vb[i] - va[i] for i in range(3)]
        v = [vc[i] - va[i] for i in range(3)]
        n = [u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        mag = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
        n = [x / mag for x in n]
        out += struct.pack("<12fH", *n, *va, *vb, *vc, 0)
    return bytes(out)


def bad_normal_facets(count=4):
    """The right record length, garbage normals - the length test alone
    would wave these through."""
    rec = struct.pack("<12fH", 5.0, 5.0, 5.0,
                      0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0)
    return rec * count


def container_1c(payload):
    head = bytearray(28)                   # zero-filled = NUL padding
    struct.pack_into("<II", head, 0, 28, 1001)
    struct.pack_into("<I", head, 0x10, len(payload))
    return bytes(head) + payload


def container_1001(payload, type_id=7, label="ハンドモデル0", label_en="Hand Model 0"):
    head = bytearray(1342)
    struct.pack_into("<III", head, 0, 1001, 1342, len(payload))
    struct.pack_into("<H", head, 0x0C, type_id)
    jp = label.encode("cp932")
    head[0x0E:0x0E + len(jp)] = jp
    en = label_en.encode("ascii")
    head[0x4A:0x4A + len(en)] = en
    return bytes(head) + payload


def rmd_bytes(maker="FANUC", model="M-20iD/35"):
    buf = bytearray(0x600)
    buf[0x53E:0x53E + len(maker)] = maker.encode("ascii")
    buf[0x57E:0x57E + len(model)] = model.encode("ascii")
    return bytes(buf)


def cal_record(k):
    pose = [400.0 + 100.0 * k, -250.0, 700.0 + 10.0 * k, 179.9, -90.0, 45.0]
    measured = [401.2 + 100.0 * k, -249.1, 699.5, 0.1, -0.2, 0.3]
    extras = [0.0, 0.0, 1.0, float(k)]
    return struct.pack("<16d", *(pose + measured + extras))


def cal_dat(n=3):
    """Records surrounded by 0xFF filler, whose every 8-byte window reads as
    NaN - implausible at any scan offset, like a real header region."""
    junk = bytes([255]) * 0x548
    return junk + b"".join(cal_record(k) for k in range(n)) + bytes([255]) * 64


# -- container headers ------------------------------------------------------------

def test_header_kind_names_both_container_styles():
    assert cvx_models.header_kind(container_1001(bytes(4))[:64]) == "container-1001"
    assert cvx_models.header_kind(container_1c(bytes(4))[:64]) == "container-1c"


@pytest.mark.parametrize("head", [
    b"",
    b"BM" + bytes(62),                                 # a BMP is not a container
    bytes(64),
    b"GS" + bytes(30),                                 # env.dat's magic
    struct.pack("<II", 1001, 28) + bytes(56),          # right words, wrong pairing
    struct.pack("<II", 28, 1342) + bytes(56),
    struct.pack("<I", 1001),                           # truncated before word two
])
def test_header_kind_is_blank_for_everything_else(head):
    assert cvx_models.header_kind(head) == ""


def test_container_info_round_trips_the_labels():
    data = container_1001(bytes(10), type_id=7,
                          label="ハンドモデル0", label_en="Hand Model 0")
    assert cvx_models.container_info(data) == {
        "style": "1001", "payload_size": 10, "type_id": 7,
        "label": "ハンドモデル0", "label_en": "Hand Model 0"}


def test_container_info_reads_a_1c_header():
    payload = zlib.compress(tetra_facets())
    assert cvx_models.container_info(container_1c(payload)) == {
        "style": "1c", "payload_size": len(payload),
        "type_id": None, "label": "", "label_en": ""}


def test_container_info_labels_may_be_empty():
    got = cvx_models.container_info(container_1001(b"", label="", label_en=""))
    assert (got["label"], got["label_en"]) == ("", "")


@pytest.mark.parametrize("data", [
    b"",
    b"BM" + bytes(62),
    container_1001(b"")[:64],              # right magic, shorter than its header
    struct.pack("<II", 28, 1001),          # 1c magic, but not even 28 bytes
])
def test_container_info_refuses_garbage_and_truncation(data):
    with pytest.raises(cvx_models.BadModel):
        cvx_models.container_info(data)


# -- the zlib -> STL gate ---------------------------------------------------------

def test_stl_streams_finds_the_geometry_in_a_container():
    facets = tetra_facets()
    got = cvx_models.stl_streams(container_1c(zlib.compress(facets)))
    assert len(got) == 1
    s = got[0]
    assert s["offset"] == 28
    assert s["tri_count"] == 4
    assert s["facets"] == facets
    assert s["bounds"] == {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]}


def test_stl_streams_finds_two_streams_like_a_real_wsm():
    z = zlib.compress(tetra_facets())
    data = bytes(64) + z + bytes(32) + z
    offs = [s["offset"] for s in cvx_models.stl_streams(data)]
    assert offs == [64, 64 + len(z) + 32]


def test_a_zlib_stream_of_text_is_not_geometry():
    """b'hello'*100 inflates to 500 bytes - a perfectly whole number of
    50-byte records - so only the unit-normal gate tells it from a mesh."""
    assert cvx_models.stl_streams(zlib.compress(b"hello" * 100)) == []


def test_right_length_wrong_normals_is_rejected():
    assert cvx_models.stl_streams(zlib.compress(bad_normal_facets())) == []


@pytest.mark.parametrize("data", [b"", b"no zlib magic anywhere here", bytes(200)])
def test_absence_of_streams_is_an_empty_list_not_an_error(data):
    assert cvx_models.stl_streams(data) == []


# -- mesh building ----------------------------------------------------------------

def test_mesh_arrays_dedupes_the_shared_corners():
    m = cvx_models.mesh_arrays(tetra_facets())
    assert m["tri_count"] == 4 and m["shown_tris"] == 4
    assert m["decimated"] is False
    assert len(m["vertices"]) == 12        # 12 facet corners -> 4 unique points
    assert len(m["triangles"]) == 12
    assert sorted(set(m["triangles"])) == [0, 1, 2, 3]
    assert m["bounds"] == {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]}


def test_mesh_arrays_decimates_but_reports_the_true_count():
    facets = tetra_facets() * 25           # 100 records
    m = cvx_models.mesh_arrays(facets, max_tris=10)
    assert m["decimated"] is True
    assert m["tri_count"] == 100           # the honest count, not the shown one
    assert 0 < m["shown_tris"] <= 10
    assert len(m["triangles"]) == m["shown_tris"] * 3


def test_a_degenerate_facet_is_skipped_not_crashed():
    collapsed = struct.pack("<12fH", 0.0, 0.0, 0.0,
                            1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0)
    m = cvx_models.mesh_arrays(tetra_facets() + collapsed)
    assert m["tri_count"] == 5 and m["shown_tris"] == 4


def test_a_trailing_partial_record_is_ignored():
    m = cvx_models.mesh_arrays(tetra_facets() + b"xx")
    assert m["tri_count"] == 4 and m["shown_tris"] == 4


# -- STL export -------------------------------------------------------------------

def test_stl_bytes_is_a_complete_binary_stl():
    facets = tetra_facets()
    stl = cvx_models.stl_bytes(facets)
    assert stl[:80] == b"BackupViewer export" + bytes(61)
    assert struct.unpack_from("<I", stl, 80)[0] == 4
    assert stl[84:] == facets              # records byte-exact


def test_stl_bytes_facets_round_trip_the_geometry_gate():
    stl = cvx_models.stl_bytes(tetra_facets())
    got = cvx_models.stl_streams(zlib.compress(stl[84:]))
    assert len(got) == 1 and got[0]["facets"] == tetra_facets()


# -- robot model identity ---------------------------------------------------------

def test_rmd_identity_reads_a_well_formed_slot_pair():
    assert cvx_models.rmd_identity(rmd_bytes()) == {
        "maker": "FANUC", "model": "M-20iD/35"}


def test_rmd_identity_slots_prove_themselves_independently():
    raw = bytearray(rmd_bytes())
    raw[0x53E + 50] = 0xFF                 # junk after the maker's terminator
    assert cvx_models.rmd_identity(bytes(raw)) == {
        "maker": "", "model": "M-20iD/35"}


@pytest.mark.parametrize("data", [
    b"",                                   # far too short for either slot
    bytes(0x600),                          # slots present but all NUL
    bytes(0x53E) + bytes([255]) * 0xC2,    # both slots solid non-ASCII bytes
])
def test_rmd_identity_returns_blank_rather_than_guess(data):
    assert cvx_models.rmd_identity(data) == {"maker": "", "model": ""}


def test_rmd_identity_rejects_overruns_and_control_bytes():
    raw = bytearray(0x600)
    raw[0x53E:0x53E + 41] = b"A" * 41      # one char past the 40 cap
    raw[0x57E:0x57E + 2] = b"F" + bytes([1])   # a control byte inside the name
    assert cvx_models.rmd_identity(bytes(raw)) == {"maker": "", "model": ""}


# -- hand-eye calibration ---------------------------------------------------------

def test_calibration_records_finds_the_pose_grid():
    got = cvx_models.calibration_records(cal_dat(3))
    assert len(got) == 3
    assert all(len(r) == 16 for r in got)
    assert got[0][:6] == [400.0, -250.0, 700.0, 179.9, -90.0, 45.0]
    assert got[1][0] == 500.0 and got[2][0] == 600.0
    assert got[0][15] == 0.0 and got[2][15] == 2.0


@pytest.mark.parametrize("data", [
    b"",
    bytes(0x800),                          # zero padding is not a pose grid
    bytes([255]) * 0x800,                  # NaN at every alignment
    bytes(0x200) + b"The quick brown fox jumps over the lazy dog. " * 40,
])
def test_calibration_noise_returns_empty_not_guesses(data):
    assert cvx_models.calibration_records(data) == []


# -- naming -----------------------------------------------------------------------

@pytest.mark.parametrize("rel,kind", [
    ("001/T101/OBJ_000/TDC_L.tbd", "part"),
    ("001/T101/OBJ_000/WSM_L.tbd", "model"),
    ("001/T101/HND/HND_L_000.tbd", "hand"),
    ("001/T101/OBJ_000/TDM_L_003.tbd", "template"),
    ("001/T101/RBT_G_RMD_002.dat", "robot"),
    ("001/T101/3D_RBT_G_CLB_002.dat", "calibration"),
    ("001/LYT_G_002.tbd", "layout"),
    ("001\\T101\\OBJ_000\\tdc_l.tbd", "part"),         # case + separator tolerant
    ("001/ref1_000.bmp", ""),
    ("001/inspect.dat", ""),
    ("env.dat", ""),
    ("001/T101/OBJ_000/notes.txt", ""),
])
def test_classify_names_the_families(rel, kind):
    assert cvx_models.classify(rel) == kind


# -- through the session + api (a synthetic camera pull) --------------------------

def big_facets(offset=1300.0, span=200.0):
    """Facet records shaped like a real workspace scan: a SMALL span sitting
    far from the origin (the measured fixture scan spanned ~110-230 mm but sat
    1.26-1.5 m out in robot-world coords) - the WSM part-vs-scan rule keys on
    origin distance, so the fixture must sit beyond _CVX_PART_REACH_MM."""
    out = bytearray()
    verts = [tuple(c * span / 2.0 + offset for c in v) for v in _TETRA_VERTS]
    for a, b, c in _TETRA_FACES:
        va, vb, vc = verts[a], verts[b], verts[c]
        u = [vb[i] - va[i] for i in range(3)]
        v = [vc[i] - va[i] for i in range(3)]
        n = [u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        mag = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
        n = [x / mag for x in n]
        out += struct.pack("<12fH", *n, *va, *vb, *vc, 0)
    return bytes(out)


def rmd_file(maker="FANUC", model="M-20iD/35"):
    """A robot-model .dat shaped like the real one: a 1001 container whose
    payload opens with the two 64-byte name slots (0x53E from file start IS
    the 1342-byte header size, so the slots sit at payload offset 0/0x40)."""
    payload = bytearray(0x100)
    payload[0:len(maker)] = maker.encode("ascii")
    payload[0x40:0x40 + len(model)] = model.encode("ascii")
    return container_1001(bytes(payload), type_id=9,
                          label="ロボットモデル", label_en="Robot Model")


def clb_file(n=3):
    """A hand-eye calibration .dat: a 1001 container whose payload holds the
    16-double records past a run of 0xFF filler (every window over the filler
    reads as NaN, like a real header region - see cal_dat)."""
    payload = bytes([255]) * 16 + b"".join(cal_record(k) for k in range(n))
    return container_1001(payload, type_id=5,
                          label="キャリブレーションデータ021",
                          label_en="Calibration Data 021")


def inspect_dat(name):
    """An inspect.dat carrying the program name at 0x4C with the byte-exact
    echo at 0x398 that cvx_inspect requires before it believes the read."""
    buf = bytearray(0x398 + 64)
    raw = name.encode("cp1252")
    buf[0x4C:0x4C + len(raw)] = raw
    buf[0x398:0x398 + len(raw)] = raw
    return bytes(buf)


def build_model_backup(root):
    """A CV-X pull shaped like a real one - <label>/SD1/cv-x/setting/<NNN>/
    T<xxx>/ - carrying one of every model family plus a decoy that claims a
    family name without being a container. Identifier-clean: RB* fixtures,
    TEST-NET ip. A plain function so ui_cvx3d_probe.py can seed the same
    tree without pytest."""
    cam = root / "RB130R01B01CAM1"
    t101 = cam / "SD1" / "cv-x" / "setting" / "001" / "T101"
    obj = t101 / "OBJ_000"
    obj.mkdir(parents=True)
    facets = tetra_facets()
    (obj / "TDC_L.tbd").write_bytes(container_1c(zlib.compress(facets)))
    # WSM in a pick tool: a far-out workspace scan, then a byte-identical
    # copy of the part CAD (the pick/verify duplicate the dedupe must flag)
    (obj / "WSM_L.tbd").write_bytes(
        container_1c(zlib.compress(big_facets()) + bytes(16) + zlib.compress(facets)))
    (obj / "TDM_L_000.tbd").write_bytes(
        container_1001(bytes(32), type_id=4, label="", label_en="Template 0"))
    # claims the TDM family, but no container header - must never be vouched
    (obj / "TDM_L_005.tbd").write_bytes(b"junk, not a container header")
    hnd = t101 / "HND"
    hnd.mkdir()
    (hnd / "HND_L_000.tbd").write_bytes(
        container_1001(bytes(64), type_id=7,
                       label="ハンドモデル0", label_en="Hand Model 0"))
    (t101 / "RBT_G_RMD_002.dat").write_bytes(rmd_file())
    (t101 / "3D_RBT_G_CLB_002.dat").write_bytes(clb_file(3))
    (t101.parent / "inspect.dat").write_bytes(inspect_dat("RB130R01B01CAM1"))
    (cam / "workspace.xml").write_bytes(
        keyence_workspace.render_workspace_xml("192.0.2.50"))
    return root


@pytest.fixture()
def model_backup(tmp_path):
    root = tmp_path / "pull"
    return build_model_backup(root)


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("BV_NO_WATCHER", "1")
    monkeypatch.setattr("backupviewer.settings.set_value", lambda *a, **k: None)
    return Api()


def _sid(api, root):
    return api.open_backup(str(root))["data"]["sid"]


def test_cvx_model_files_vouches_only_real_containers(model_backup):
    s = BackupSession(model_backup)
    names = sorted(Path(rel).name for rel, _p in s.cvx_model_files())
    assert names == sorted([
        "TDC_L.tbd", "WSM_L.tbd", "HND_L_000.tbd", "TDM_L_000.tbd",
        "RBT_G_RMD_002.dat", "3D_RBT_G_CLB_002.dat"])
    # TDM_L_005.tbd (family name, junk bytes) and inspect.dat/workspace.xml
    # (no family claim) are all absent - the header is what we trust


def test_manifest_lights_the_camera_tabs(model_backup):
    m = BackupSession(model_backup).manifest()
    assert m["backup_type"] == "keyence camera"
    assert m["tabs"]["overview"] is True       # "*camera"
    assert m["tabs"]["view3d"] is True         # "*cvx3d": vouched models exist


def test_a_robot_backup_does_not_light_the_model_view(tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    (d / "SUMMARY.DG").write_text("F Number: F999999\n", encoding="cp1252")
    m = BackupSession(d).manifest()
    assert m["tabs"]["overview"] is True       # SUMMARY.DG, as ever
    assert m["tabs"]["view3d"] is False        # no DCS files, no CV-X models


def test_a_robot_without_summary_gets_no_overview(tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    (d / "NUMREG.VA").write_text("[*NUMREG*]\n", encoding="cp1252")
    m = BackupSession(d).manifest()
    assert m["tabs"]["overview"] is False
    assert m["tabs"]["view3d"] is False


def test_a_camera_with_no_models_keeps_view3d_dark(tmp_path):
    d = tmp_path / "CAM1" / "SD1" / "cv-x" / "setting" / "001"
    d.mkdir(parents=True)
    (d / "inspect.dat").write_bytes(inspect_dat("RB130R01B01CAM1"))
    m = BackupSession(tmp_path).manifest()
    assert m["backup_type"] == "keyence camera"
    assert m["tabs"]["overview"] is True
    assert m["tabs"]["view3d"] is False


def _by_name(data):
    return {(e["rel"].rsplit("/", 1)[-1], e["stream"]): e for e in data["models"]}


def test_cvx_models_lists_every_stream_and_flags_the_copies(api, model_backup):
    data = api.cvx_models(_sid(api, model_backup))["data"]
    assert data["count"] == len(data["models"]) == 7
    ent = _by_name(data)
    tdc = ent[("TDC_L.tbd", 0)]
    assert tdc["kind"] == "part" and tdc["viewable"] is True
    assert tdc["tris"] == 4 and tdc["dup_of"] is None
    assert (tdc["program"], tdc["tool"]) == ("001", "T101")
    scan = ent[("WSM_L.tbd", 0)]
    assert scan["kind"] == "scan"              # sits ~1.3 m out: robot-world
    part_copy = ent[("WSM_L.tbd", 1)]
    assert part_copy["kind"] == "part"         # part-local mm: a CAD copy
    assert part_copy["dup_of"] == {"rel": tdc["rel"], "stream": 0}
    hand = ent[("HND_L_000.tbd", 0)]
    assert hand["kind"] == "hand" and hand["viewable"] is False
    assert hand["tris"] is None and hand["label"] == "Hand Model 0"
    robot = ent[("RBT_G_RMD_002.dat", 0)]
    assert (robot["maker"], robot["model"]) == ("FANUC", "M-20iD/35")
    assert data["robot"] == {"maker": "FANUC", "model": "M-20iD/35"}
    assert ent[("3D_RBT_G_CLB_002.dat", 0)]["points"] == 3
    assert data["calibration"] == {"points": 3}


def test_cvx_model_serves_a_mesh_and_refuses_the_unvouched(api, model_backup):
    sid = _sid(api, model_backup)
    data = api.cvx_models(sid)["data"]
    tdc = next(e for e in data["models"] if e["rel"].endswith("TDC_L.tbd"))
    got = api.cvx_model(tdc["rel"], 0, sid)["data"]
    assert got["kind"] == "part" and got["stream"] == 0
    assert got["tri_count"] == 4 and got["shown_tris"] == 4
    assert got["decimated"] is False
    assert len(got["vertices"]) == 12 and len(got["triangles"]) == 12
    assert got["bounds"]["max"] == [1.0, 1.0, 1.0]
    # in the backup but not a vouched model container -> refused, not parsed
    res = api.cvx_model("RB130R01B01CAM1/SD1/cv-x/setting/001/inspect.dat", 0, sid)
    assert not res["ok"] and res["error"]["code"] == "NOT_FOUND"
    res = api.cvx_model("nothere.tbd", 0, sid)
    assert not res["ok"] and res["error"]["code"] == "NOT_FOUND"


def test_cvx_model_export_writes_real_stls(api, model_backup, tmp_path):
    sid = _sid(api, model_backup)
    data = api.cvx_models(sid)["data"]
    tdc = next(e for e in data["models"] if e["rel"].endswith("TDC_L.tbd"))
    scan = next(e for e in data["models"]
                if e["rel"].endswith("WSM_L.tbd") and e["stream"] == 0)
    dest = tmp_path / "out"
    dest.mkdir()
    got = api.cvx_model_export(
        [{"rel": tdc["rel"], "stream": 0}, {"rel": scan["rel"], "stream": 0},
         # the WSM part-copy is the same tool + kind as the TDC; its stream
         # suffix keeps the name unique - and it must export, never error
         {"rel": scan["rel"], "stream": 1},
         # a double-picked item exports once, not twice
         {"rel": tdc["rel"], "stream": 0}],
        str(dest), "RB130R01B01CAM1", sid)["data"]
    assert got["root"] == "RB130R01B01CAM1"
    assert got["count"] == 3
    assert sorted(got["files"]) == ["RB130R01B01CAM1/001_T101_part.stl",
                                    "RB130R01B01CAM1/001_T101_part_1.stl",
                                    "RB130R01B01CAM1/001_T101_scan.stl"]
    assert len(set(got["files"])) == 3        # no two outputs share a name
    part = (dest / "RB130R01B01CAM1" / "001_T101_part.stl").read_bytes()
    assert struct.unpack_from("<I", part, 80)[0] == 4     # a complete binary STL
    assert part[84:] == tetra_facets()                    # records byte-exact
    scan_blob = (dest / "RB130R01B01CAM1" / "001_T101_scan.stl").read_bytes()
    assert scan_blob[84:] == big_facets()
    copy_blob = (dest / "RB130R01B01CAM1" / "001_T101_part_1.stl").read_bytes()
    assert copy_blob[84:] == tetra_facets()   # the WSM part-copy, byte-exact
    assert got["bytes"] == len(part) + len(scan_blob) + len(copy_blob)
    assert not list((dest / "RB130R01B01CAM1").glob("*.part"))   # no residue


def test_cvx_model_export_never_writes_into_a_backup(api, model_backup):
    sid = _sid(api, model_backup)
    data = api.cvx_models(sid)["data"]
    items = [{"rel": next(e["rel"] for e in data["models"] if e["viewable"]),
              "stream": 0}]
    for bad in (model_backup, model_backup / "RB130R01B01CAM1"):
        res = api.cvx_model_export(items, str(bad), "x", sid)
        assert not res["ok"] and res["error"]["code"] == "BAD_DEST"
    assert not list(model_backup.rglob("*.stl"))


def test_cvx_overview_reads_programs_controller_and_counts(api, model_backup):
    got = api.cvx_overview(_sid(api, model_backup))["data"]
    assert got["programs"] == [{"n": "001", "name": "RB130R01B01CAM1"}]
    assert got["controller"] == {"type_text": "12", "grade_text": "@DR3"}
    assert got["robot"] == {"maker": "FANUC", "model": "M-20iD/35"}
    assert got["calibration"] == {"points": 3}
    assert got["models"] == {"parts": 2, "scans": 1, "hands": 1, "templates": 1}


def test_cvx_overview_refuses_a_robot_backup(api, tmp_path):
    d = tmp_path / "rb"
    d.mkdir()
    (d / "SUMMARY.DG").write_text("F Number: F999999\n", encoding="cp1252")
    res = api.cvx_overview(_sid(api, d))
    assert not res["ok"] and res["error"]["code"] == "NOT_CVX"


# -- the HND gripper mesh (48-byte records, uncompressed, offset 2 mod 4) ---------

def hnd_records(count=64, scale=1.0):
    """`count` HND facet records: 48 bytes each - twelve float32, no u16
    attribute word. Built by re-packing tetra_facets()'s own records, each
    repeat of the four faces scaled further out from the origin so the block
    spans a real box; uniform scaling about the origin leaves a face normal
    untouched, so the normals stay the computed ones (never hand-typed).
    64 records span +/-16 mm, and 64 is exactly cvx_models._HND_MIN."""
    src = tetra_facets()
    out = bytearray()
    for i in range(count):
        rec = list(struct.unpack_from("<12f", src, (i % 4) * cvx_models.FACET))
        k = scale * (1 + i // 4)
        for j in range(3, 12):             # the three vertices; [0:3] is the normal
            rec[j] *= k
        out += struct.pack("<12f", *rec)
    return bytes(out)


def stl_records(records):
    """The same facets in the 50-byte binary-STL form the rest of the module
    speaks - each 48-byte record with the u16 attribute word (built
    programmatically) put back. What a faithful 48->50 repack must produce."""
    step = cvx_models.HND_FACET
    return b"".join(records[i:i + step] + bytes(2)
                    for i in range(0, len(records), step))


def hand_file(records, pre=4, tail=256):
    """A synthetic HND_L_*.tbd: the 1001-style header, `pre` NUL bytes, the
    raw mesh block, then NUL padding after it. The default `pre` lands the
    block at HEAD_1001 + 4 = 2 mod 4 - the misalignment that is the whole
    point of this format, and what made an earlier pass read the file as
    noise."""
    return container_1001(bytes(pre) + records + bytes(tail))


def noise_bytes(n, seed=12345):
    """Deterministic pseudo-random filler - an LCG, so the fixture is the
    same bytes on every machine and no test depends on randomness."""
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out += struct.pack("<I", x)
    return bytes(out[:n])


def test_raw_facets_round_trips_a_planted_block():
    """The block is found where it was planted (not at a hardcoded offset),
    every planted record comes back, the bounds are the planted geometry's,
    and the 48->50 repack is byte-faithful - so faithful that the module's
    own facet gate accepts the result, which is what lets stl_bytes /
    mesh_arrays / _facet_bounds take these records unchanged."""
    recs = hnd_records(64)
    got = cvx_models.raw_facets(hand_file(recs))
    assert got["tri_count"] == 64
    assert got["blocks"] == 1
    assert got["offset"] == cvx_models.HEAD_1001 + 4
    assert got["bounds"] == {"min": [-16.0, -16.0, -16.0],
                             "max": [16.0, 16.0, 16.0]}
    assert got["facets"] == stl_records(recs)   # every record, byte-exact
    assert cvx_models._looks_like_facets(got["facets"])
    assert cvx_models.mesh_arrays(got["facets"])["shown_tris"] == 64


def test_raw_facets_normals_match_their_own_winding():
    """The geometric proof of the layout, rather than an assumption about it:
    for every returned record the stored first vector IS the unit normal of
    that same record's vertex winding (dot ~ +1.0). A field read one float
    early or late - exactly what a 2-mod-4 offset invites - could not survive
    this check."""
    got = cvx_models.raw_facets(hand_file(hnd_records(64)))
    worst = 1.0
    for i in range(got["tri_count"]):
        rec = struct.unpack_from("<12f", got["facets"], i * cvx_models.FACET)
        va, vb, vc = rec[3:6], rec[6:9], rec[9:12]
        u = [vb[j] - va[j] for j in range(3)]
        v = [vc[j] - va[j] for j in range(3)]
        n = [u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        mag = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
        worst = min(worst, sum(a * b / mag for a, b in zip(rec[0:3], n)))
    assert worst > 1.0 - 1e-6               # float32 storage is the only error


@pytest.mark.parametrize("data", [
    # the same geometry, zlib-packed in a TDC-style container: a real model
    # file, but not this one - stl_streams is what reads it
    container_1c(zlib.compress(stl_records(hnd_records(600)))),
    hand_file(hnd_records(63)),            # one record short of _HND_MIN
    hand_file(hnd_records(24)),            # a bare _HND_RUN proves nothing
    bytes(8192),                           # all-NUL: padding is not a model
    b"The quick brown fox jumps over the lazy dog. " * 200,
    noise_bytes(8192),
    container_1001(noise_bytes(8192)),     # a real hand header over junk: the
], ids=[                                   # header alone never vouches
    # ids are spelled out because pytest builds them from the parameter, and
    # a multi-KB blob's id overflows the PYTEST_CURRENT_TEST environment
    # variable (32767 chars) on Windows
    "tdc-style zlib container", "63 records", "24 records", "all NUL",
    "text", "noise", "noise under a hand header"])
def test_raw_facets_refuses_what_is_not_a_hand_mesh(data):
    """Absence is an empty dict, never a guess: a zlib container, a block too
    short to be a model, NUL padding, text, and noise - inside a genuine
    1001 hand header or not - all come back {}."""
    assert cvx_models.raw_facets(data) == {}


def test_raw_facets_does_not_eat_the_padding_between_blocks():
    """Two planted blocks come back as two blocks whose records are exactly
    the ones planted - the NUL gap between them is neither drawn as geometry
    nor allowed to fuse the blocks into one."""
    # the gap is deliberately shorter than one 48-byte record: the real files
    # carry a sub-header there, and a run may legally open on zero normals,
    # so a whole-record gap of NULs is a case this fixture does not claim.
    a, b = hnd_records(64), hnd_records(80, scale=2.0)
    got = cvx_models.raw_facets(hand_file(a + bytes(8) + b))
    assert got["blocks"] == 2
    assert got["tri_count"] == 64 + 80
    assert got["offset"] == cvx_models.HEAD_1001 + 4      # the FIRST block
    assert got["facets"] == stl_records(a) + stl_records(b)
    assert bytes(cvx_models.HND_FACET) not in got["facets"]   # no NUL record
    assert got["bounds"] == {"min": [-40.0, -40.0, -40.0],
                             "max": [40.0, 40.0, 40.0]}


@pytest.mark.parametrize("pre,mod4", [(2, 0), (4, 2)])
def test_raw_facets_tolerates_the_offset_without_depending_on_it(pre, mod4):
    """The scan finds the same mesh whether the block starts 4-aligned or at
    the real files' 2-mod-4 offset. The misalignment is tolerated, not
    required - a decoder that keyed on it would be as wrong as the 4-aligned
    pass that first read these files as noise."""
    recs = hnd_records(64)
    got = cvx_models.raw_facets(hand_file(recs, pre=pre))
    assert got["offset"] == cvx_models.HEAD_1001 + pre
    assert got["offset"] % 4 == mod4
    assert got["tri_count"] == 64 and got["blocks"] == 1
    assert got["facets"] == stl_records(recs)
