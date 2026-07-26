"""CV-X backup images: the BMP decoder, the height packing, and the photos
payload built from a pair.

Fixtures are synthetic BMPs built here to the shape real cameras write
(BITMAPINFOHEADER, uncompressed, bottom-up; 8-bpp palettised for intensity,
24-bpp for height) - self-contained and identifier-clean, no sample backup.
"""
import struct

import pytest

from backupviewer.api import Api
from backupviewer.parsers import cvx_image
from backupviewer.session import BackupSession


# -- fixture builders -----------------------------------------------------------

def _bmp(width, height, bpp, rows, palette=b""):
    """rows: bottom row FIRST, already padded to the BMP stride."""
    stride = ((width * bpp + 31) // 32) * 4
    body = b"".join(r.ljust(stride, b"\x00") for r in rows)
    offset = 14 + 40 + len(palette)
    return (b"BM" + struct.pack("<IHHI", offset + len(body), 0, 0, offset)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, 0,
                          len(body), 2835, 2835, len(palette) // 4, 0)
            + palette + body)


def _gray_palette():
    return b"".join(bytes((i, i, i, 0)) for i in range(256))      # stored BGRA


def intensity_bmp(pixels):
    """pixels: rows of 0..255 grey, TOP row first (we flip for the file)."""
    h, w = len(pixels), len(pixels[0])
    return _bmp(w, h, 8, [bytes(r) for r in reversed(pixels)], _gray_palette())


def height_bmp(values):
    """values: rows of 15-bit heights (cvx_image.NO_DATA -> the all-zero pixel),
    TOP row first."""
    h, w = len(values), len(values[0])
    rows = []
    for row in reversed(values):
        out = bytearray()
        for v in row:
            if v == cvx_image.NO_DATA:
                out += b"\x00\x00\x00"
            else:
                out += bytes((v & 0x0F, (v >> 7) & 0xFF, (v >> 4) & 0x07))   # B, G, R
        rows.append(bytes(out))
    return _bmp(w, h, 24, rows)


# -- header ---------------------------------------------------------------------

def test_bit_depth_decides_the_kind():
    assert cvx_image.probe(intensity_bmp([[1, 2], [3, 4]]))["kind"] == cvx_image.INTENSITY
    assert cvx_image.probe(height_bmp([[100, 200], [300, 400]]))["kind"] == cvx_image.HEIGHT


def test_probe_reports_geometry():
    got = cvx_image.probe(height_bmp([[1, 2, 3]] * 5))
    assert (got["width"], got["height"], got["bpp"]) == (3, 5, 24)


@pytest.mark.parametrize("data", [
    b"", b"not a bmp at all, but long enough to be read as one maybe",
    b"BM" + b"\x00" * 60,                                    # BM magic, junk header
])
def test_header_kind_is_blank_for_non_images(data):
    assert cvx_image.header_kind(data) == ""


def test_compressed_and_odd_depths_are_refused():
    raw = bytearray(height_bmp([[1, 2], [3, 4]]))
    raw[30:34] = struct.pack("<I", 1)          # BI_RLE8
    with pytest.raises(cvx_image.BadImage):
        cvx_image.probe(bytes(raw))
    raw = bytearray(height_bmp([[1, 2], [3, 4]]))
    raw[28:30] = struct.pack("<H", 16)         # a depth no CV-X writes
    with pytest.raises(cvx_image.BadImage):
        cvx_image.probe(bytes(raw))


# -- the height packing ---------------------------------------------------------

def test_height_round_trips_through_the_packing():
    """The whole feature rests on H = (G<<7)|(R<<4)|B, so prove it end to end.
    Values chosen to exercise every field: the low nibble (B), the 3-bit middle
    (R), the carry into G, and the top of the 15-bit range."""
    want = [[1, 15, 16, 127], [128, 4095, 8192, 20000], [32767, 2, 3, 4]]
    w, h, vals = cvx_image.height_field(height_bmp(want))
    assert (w, h) == (4, 3)
    assert list(vals) == want[0] + want[1] + want[2]


def test_a_height_of_zero_is_indistinguishable_from_no_data():
    """A limit of the format, not of this decoder: height 0 and "no return" are
    both an all-zero pixel, so a true zero necessarily reads as NO_DATA. Real
    cameras put the measuring range well above zero (~4000-29000 counts on every
    file checked), so this costs nothing in practice - but it is not a decoding
    choice we get to make, and it must not be silently "fixed" later."""
    _w, _h, vals = cvx_image.height_field(height_bmp([[0, 1]]))
    assert list(vals) == [cvx_image.NO_DATA, 1]


def test_no_data_survives_as_a_sentinel_not_a_zero_height():
    want = [[cvx_image.NO_DATA, 5000], [5001, cvx_image.NO_DATA]]
    _w, _h, vals = cvx_image.height_field(height_bmp(want))
    assert list(vals) == [cvx_image.NO_DATA, 5000, 5001, cvx_image.NO_DATA]


def test_rows_come_back_top_first_from_a_bottom_up_file():
    w, h, vals = cvx_image.height_field(height_bmp([[900, 900], [100, 100]]))
    assert list(vals)[:2] == [900, 900]        # the top row of the picture


def test_odd_width_row_padding_is_handled():
    """3 px * 3 B = 9 B, padded to a 12-byte stride - get that wrong and every
    row after the first is shifted."""
    want = [[10, 20, 30], [40, 50, 60], [70, 80, 90]]
    _w, _h, vals = cvx_image.height_field(height_bmp(want))
    assert list(vals) == [10, 20, 30, 40, 50, 60, 70, 80, 90]


def test_max_dim_decimates_both_axes():
    big = [[i * 7 for i in range(40)] for _ in range(40)]
    w, h, vals = cvx_image.height_field(height_bmp(big), max_dim=10)
    assert (w, h) == (10, 10) and len(vals) == 100


# -- rendering ------------------------------------------------------------------

def test_intensity_renders_its_own_grey_values_opaque():
    w, h, rgba = cvx_image.to_rgba(intensity_bmp([[0, 128], [255, 64]]))
    assert (w, h) == (2, 2)
    assert rgba[0:4] == bytes((0, 0, 0, 255))
    assert rgba[4:8] == bytes((128, 128, 128, 255))
    assert rgba[8:12] == bytes((255, 255, 255, 255))


def test_height_renders_no_data_transparent_and_stretches_the_rest():
    w, h, rgba = cvx_image.to_rgba(height_bmp([[cvx_image.NO_DATA, 1000],
                                               [2000, 3000]]))
    assert (w, h) == (2, 2)
    assert rgba[3] == 0                        # no data -> fully transparent
    assert rgba[7] == 255 and rgba[11] == 255  # measured pixels are opaque
    low, high = rgba[4:7], rgba[12:15]
    assert low != high                         # the stretch actually separated them


def test_a_flat_surface_does_not_divide_by_zero():
    w, h, rgba = cvx_image.to_rgba(height_bmp([[5000, 5000], [5000, 5000]]))
    assert (w, h) == (2, 2) and len(rgba) == 16


# -- pairing + labels -----------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("s/001/ref1_000.bmp", "s/001/ref1_500.bmp"),
    ("s/001/SimImage/CAM1/250130_000839_0000000180_CAM1_HEIGHT_OK.bmp",
     "s/001/SimImage/CAM1/250130_000839_0000000180_CAM1_INTENSITY_OK.bmp"),
])
def test_the_two_halves_of_one_scene_share_a_key(a, b):
    assert cvx_image.pair_key(a) == cvx_image.pair_key(b)


def test_different_programs_never_pair():
    assert cvx_image.pair_key("s/001/ref1_000.bmp") != cvx_image.pair_key("s/002/ref1_000.bmp")


def test_an_unrecognised_name_stands_alone():
    assert cvx_image.pair_key("s/001/whatever.bmp") == "s/001/whatever.bmp"


def test_label_reads_a_capture_filename():
    got = cvx_image.label("x/250130_000839_0000000180_CAM1_HEIGHT_NG.bmp")
    assert got == {"stamp": "2025-01-30 00:08:39", "seq": "180",
                   "cam": "CAM1", "verdict": "NG"}


def test_label_is_blank_on_a_master():
    assert cvx_image.label("x/ref1_000.bmp") == {
        "stamp": "", "seq": "", "cam": "", "verdict": ""}


# -- through the session + api --------------------------------------------------

@pytest.fixture()
def cvx_backup(tmp_path):
    """A CV-X snapshot shaped like a real pull: <label>/SD1/cv-x/setting/<NNN>/."""
    d = tmp_path / "CAM1" / "SD1" / "cv-x" / "setting" / "001"
    d.mkdir(parents=True)
    (d / "ref1_000.bmp").write_bytes(height_bmp([[1000, 2000], [3000, 4000]]))
    (d / "ref1_500.bmp").write_bytes(intensity_bmp([[10, 20], [30, 40]]))
    (d / "inspect.dat").write_bytes(b"not an image")
    (d / "notreally.bmp").write_bytes(b"BM this is not a bitmap")
    return tmp_path


def test_the_photos_tab_lights_up_for_a_cvx_backup(cvx_backup):
    s = BackupSession(cvx_backup)
    assert s.has_photos() is True
    assert [p.name for p in s.cvx_image_files()] == ["ref1_000.bmp", "ref1_500.bmp"]


def test_a_bmp_that_is_not_an_image_is_not_counted(cvx_backup):
    """Presence of a name proves nothing; the header is what we trust."""
    s = BackupSession(cvx_backup)
    assert "notreally.bmp" not in [p.name for p in s.cvx_image_files()]


def test_photos_pairs_the_two_halves_into_one_record(cvx_backup):
    api = Api()
    sid = api.open_backup(str(cvx_backup))["data"]["sid"]
    data = api.get_photos(sid)["data"]
    assert data["count"] == 1
    rec = data["photos"][0]
    assert rec["overlay"]["base"].endswith("ref1_500.bmp")    # the photo underneath
    assert rec["overlay"]["top"].endswith("ref1_000.bmp")     # the height map over it
    assert rec["thumb"].endswith("ref1_500.bmp")             # grid shows the photo
    assert rec["date"] == "program 001"


def test_a_cvx_image_comes_back_as_a_png_not_the_raw_bmp(cvx_backup):
    api = Api()
    sid = api.open_backup(str(cvx_backup))["data"]["sid"]
    rec = api.get_photos(sid)["data"]["photos"][0]
    got = api.get_image(rec["overlay"]["top"], sid)["data"]
    assert got["mime"] == "image/png"
    assert got["kind"] == cvx_image.HEIGHT
    assert got["data_uri"].startswith("data:image/png;base64,iVBORw0KGgo")


def test_the_intensity_half_renders_too(cvx_backup):
    api = Api()
    sid = api.open_backup(str(cvx_backup))["data"]["sid"]
    rec = api.get_photos(sid)["data"]["photos"][0]
    got = api.get_image(rec["overlay"]["base"], sid)["data"]
    assert got["mime"] == "image/png" and got["kind"] == cvx_image.INTENSITY


def test_a_lone_height_image_still_shows_without_an_overlay(tmp_path):
    d = tmp_path / "CAM1" / "SD1" / "cv-x" / "setting" / "002"
    d.mkdir(parents=True)
    (d / "ref1_000.bmp").write_bytes(height_bmp([[1000, 2000], [3000, 4000]]))
    api = Api()
    sid = api.open_backup(str(tmp_path))["data"]["sid"]
    rec = api.get_photos(sid)["data"]["photos"][0]
    assert "overlay" not in rec
    assert rec["thumb"].endswith("ref1_000.bmp")


def test_a_captured_trigger_carries_its_verdict(tmp_path):
    d = tmp_path / "CAM1" / "SD1" / "cv-x" / "setting" / "001" / "SimImage" / "CAM1"
    d.mkdir(parents=True)
    stem = "250130_000839_0000000180_CAM1"
    (d / f"{stem}_HEIGHT_NG.bmp").write_bytes(height_bmp([[1, 2], [3, 4]]))
    (d / f"{stem}_INTENSITY_NG.bmp").write_bytes(intensity_bmp([[1, 2], [3, 4]]))
    api = Api()
    sid = api.open_backup(str(tmp_path))["data"]["sid"]
    rec = api.get_photos(sid)["data"]["photos"][0]
    assert rec["result"] == "Fail"             # NG, in the grid's own vocabulary
    assert rec["timestamp"] == "2025-01-30 00:08:39"
    assert "overlay" in rec
