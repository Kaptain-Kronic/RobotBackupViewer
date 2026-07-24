"""Screen capture: the PNG encoder proven by decoding it back by hand, and
the GDI path smoke-tested against the real desktop (Windows only - which is
the only place the app runs)."""
import struct
import sys
import zlib

import pytest

from backupviewer import screengrab


def _decode_png(data: bytes):
    """Minimal PNG reader for our own encoder's output: returns (w, h, rgba)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, w = 8, None
    idat = b""
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        (crc,) = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])
        assert crc == zlib.crc32(tag + body), f"bad crc on {tag}"
        if tag == b"IHDR":
            w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", body)
            assert (depth, ctype, comp, filt, inter) == (8, 6, 0, 0, 0)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    rows = []
    for y in range(h):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert line[0] == 0, "only filter 0 is emitted"
        rows.append(line[1:])
    return w, h, b"".join(rows)


def test_png_roundtrip():
    rgba = bytes(range(2 * 3 * 4))          # 2x3, every byte distinct
    png = screengrab.png_encode(2, 3, rgba)
    assert _decode_png(png) == (2, 3, rgba)


def test_png_rejects_wrong_length():
    with pytest.raises(ValueError):
        screengrab.png_encode(2, 2, b"\x00" * 15)


def _desktop_capturable() -> bool:
    """GDI can't capture a locked/disconnected session - that's the machine's
    state, not a code bug, so the live-capture smoke test skips honestly."""
    if sys.platform != "win32":
        return False
    try:
        screengrab.grab_rect_png(0, 0, 2, 2)
        return True
    except OSError:
        return False


needs_desktop = pytest.mark.skipif(
    not _desktop_capturable(), reason="desktop not capturable (locked session?)")


@needs_desktop
def test_grab_rect_png_speaks_png():
    png = screengrab.grab_rect_png(0, 0, 6, 4)
    w, h, rgba = _decode_png(png)
    assert (w, h) == (6, 4)
    assert all(rgba[i] == 255 for i in range(3, len(rgba), 4)), "alpha forced opaque"


@pytest.mark.skipif(sys.platform != "win32", reason="GDI capture is Windows-only")
def test_grab_rejects_empty_rect():
    with pytest.raises(OSError):
        screengrab.grab_rect_png(0, 0, 0, 10)


@pytest.mark.skipif(sys.platform != "win32", reason="monitor query is Windows-only")
def test_monitor_rect_falls_back_to_primary():
    x, y, w, h = screengrab.monitor_rect_for_window("no window has this title 8f2k")
    assert (x, y) == (0, 0)
    assert w > 0 and h > 0


@pytest.mark.skipif(sys.platform != "win32", reason="window move is Windows-only")
def test_cover_window_gives_up_honestly():
    assert screengrab.cover_window_on_monitor(
        "no window has this title 8f2k", (0, 0, 10, 10), tries=2, delay=0.01) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 prototypes are Windows-only")
def test_window_handles_are_pointer_sized():
    """The dc9d9b3 regression guard: without explicit prototypes, ctypes
    truncates a Win64 HWND to 32 bits and SetWindowPos silently no-ops, so a
    hidden picker window is never revealed. These calls MUST carry
    pointer-sized handle types, not the default C int."""
    import ctypes
    from ctypes import wintypes
    u = screengrab._u32
    assert u.FindWindowW.restype is wintypes.HWND
    assert u.SetWindowPos.restype is wintypes.BOOL
    # handle params (window + insert-after) must be pointer-sized, not c_int
    assert u.SetWindowPos.argtypes[0] is wintypes.HWND
    assert u.SetWindowPos.argtypes[1] is wintypes.HWND
    assert ctypes.sizeof(u.SetWindowPos.argtypes[0]) == ctypes.sizeof(ctypes.c_void_p)
    assert u.ShowWindow.argtypes[0] is wintypes.HWND
