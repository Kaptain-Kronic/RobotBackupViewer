"""The pure photo-triple layer of the Matrox SavedImages parser: how a folder of
files becomes photos, and which of them is the newest.

Both answers are load-bearing twice over - the photos tab orders its grid with
them, and the backup uses them to decide WHICH photos a pull carries back
(mtxbackup._photo_files). Synthetic filenames only, in the exact shapes a real
Design Assistant camera writes.
"""
from backupviewer.parsers import mtx_saved_image as msi

CAM = "CELL-01RB172-R01CAM02"


def _name(stamp: str, result: str = "Pass") -> str:
    return CAM + "-401-0-" + result + "-" + stamp


def test_photo_sort_key_pads_a_one_digit_hour():
    """The camera writes hours before 10:00 with no leading zero, so a plain text
    sort calls a 9am shot newer than a 1pm one. Padding is what makes the key
    order photos the way the clock does."""
    morning = _name("2026_07_07-9.14.30.020") + ".jpg"
    afternoon = _name("2026_07_07-13.05.02.100", "Fail") + ".jpg"
    assert morning > afternoon                                  # as plain text
    assert msi.photo_sort_key(morning, 0) < msi.photo_sort_key(afternoon, 0)


def test_photo_sort_key_orders_by_stamp_then_falls_back_to_mtime():
    a = _name("2026_06_25-16.02.11.003") + ".jpg"
    b = _name("2026_07_07-9.14.30.020") + ".jpg"
    assert msi.photo_sort_key(a, 99) < msi.photo_sort_key(b, 0)  # date beats mtime
    # a name the camera did not stamp sorts on mtime alone, and never above one
    # it did - an unknown time is not a claim to be the newest
    assert msi.photo_sort_key("HMIImage.jpg", 10**9) < msi.photo_sort_key(a, 0)
    assert msi.photo_sort_key("x.jpg", 5) < msi.photo_sort_key("y.jpg", 6)


def test_group_photo_files_pairs_a_triple_by_stem():
    stem = _name("2026_07_07-13.05.02.100", "Fail")
    groups = msi.group_photo_files([stem + ".jpg", stem + ".png", stem + ".txt",
                                    "notes.md", "HMIImage.bmp"])
    assert groups[stem] == {"jpg": stem + ".jpg", "png": stem + ".png",
                            "txt": stem + ".txt"}
    assert "notes" not in groups                     # .md is not a photo extension
    assert groups["HMIImage"] == {"jpg": "HMIImage.bmp"}   # bmp classes as a preview


def test_photo_record_drops_a_sidecar_with_no_image():
    """A .txt whose images have been rotated away is not a photo - the grid would
    have nothing to show for it."""
    stem = _name("2026_07_07-13.05.02.100", "Fail")
    assert msi.photo_record({"txt": stem + ".txt"}, {}, 0) is None
    rec = msi.photo_record({"png": "d/2026-07-07/" + stem + ".png"}, {}, 0)
    assert rec["thumb"] == rec["full"]               # png-only: it is both
    assert rec["result"] == "Fail"                   # read off the filename
    assert rec["date"] == "2026-07-07"
