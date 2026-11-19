"""Window capture: the PNG encoder proven by decoding it back by hand, and the
window-renders-itself path tested against real windows of this process
(Windows only - which is the only place the app runs)."""
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


@pytest.fixture
def covered_window():
    """A window of THIS process (red) with another window (blue, topmost) lying
    exactly over it - both at 2% opacity, so nothing shows on the screen of
    whoever runs the suite. Yields the red one's title."""
    if sys.platform != "win32":
        pytest.skip("window capture is Windows-only")
    tk = pytest.importorskip("tkinter")
    import time
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for a test window")
    root.withdraw()
    title = "bv-grab-target-7f3q"
    target = tk.Toplevel(root, bg="#d01010")
    target.title(title)
    target.geometry("160x120+30+30")
    target.attributes("-alpha", 0.02)
    cover = tk.Toplevel(root, bg="#1010d0")
    cover.title("bv-grab-cover")
    cover.geometry("160x120+30+30")
    cover.attributes("-topmost", True)
    cover.attributes("-alpha", 0.02)
    for _ in range(25):
        root.update()
        time.sleep(0.02)
    try:
        yield title
    finally:
        root.destroy()


def test_the_window_renders_itself_not_what_covers_it(covered_window):
    """The mirror used to copy the SCREEN at the window's position, so whatever
    lay over the app (an email, a chat) went to the phone. The window now
    renders itself: its own red, and not one pixel of the blue lying on top."""
    try:
        png = screengrab.grab_window_png(covered_window)
    except OSError as e:
        pytest.skip(f"desktop not capturable here ({e})")
    w, h, rgba = _decode_png(png)
    px = [rgba[i:i + 3] for i in range(0, len(rgba), 4 * 7)]
    red = sum(1 for p in px if p[0] > 180 and p[1] < 60 and p[2] < 60)
    blue = sum(1 for p in px if p[2] > 180 and p[0] < 60)
    assert red > len(px) // 5, "the window's own content is in the capture"
    assert blue == 0, "nothing covering the window may reach the phone"
    assert all(rgba[i] == 255 for i in range(3, len(rgba), 4)), "alpha forced opaque"


@pytest.mark.skipif(sys.platform != "win32", reason="window query is Windows-only")
def test_another_programs_window_is_never_mirrored():
    """Only this process's own windows are ever looked up: a stranger's window
    that happens to share a title can never be captured."""
    import ctypes
    import os
    from ctypes import wintypes
    titles = []
    buf = ctypes.create_unicode_buffer(512)

    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        screengrab._u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        screengrab._u32.GetWindowTextW(hwnd, buf, 512)
        if pid.value != os.getpid() and buf.value:
            titles.append(buf.value)
            return False
        return True

    screengrab._u32.EnumWindows(screengrab._ENUM_PROC(visit), 0)
    if not titles:
        pytest.skip("no titled window of another process to look for")
    assert screengrab.window_is_open(titles[0]) is False
    with pytest.raises(OSError):
        screengrab.grab_window_png(titles[0])


@pytest.mark.skipif(sys.platform != "win32", reason="window query is Windows-only")
def test_window_is_open_false_for_missing():
    assert screengrab.window_is_open("no window has this title 8f2k") is False


@pytest.mark.skipif(sys.platform != "win32", reason="window capture is Windows-only")
def test_grab_window_png_missing_is_clean_error():
    with pytest.raises(OSError):
        screengrab.grab_window_png("no window has this title 8f2k")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 prototypes are Windows-only")
def test_window_handles_are_pointer_sized():
    """Regression guard: without explicit prototypes, ctypes truncates a Win64
    HWND to 32 bits and the call silently no-ops (that broke the picker reveal
    once). Every user32 call here MUST carry pointer-sized handle types."""
    import ctypes
    from ctypes import wintypes
    u = screengrab._u32
    assert ctypes.sizeof(wintypes.HWND) == ctypes.sizeof(ctypes.c_void_p)
    assert u.GetClientRect.argtypes[0] is wintypes.HWND
    assert u.PrintWindow.argtypes[:2] == (wintypes.HWND, wintypes.HDC)
    assert u.GetWindowThreadProcessId.argtypes[0] is wintypes.HWND
    assert screengrab._g32.CreateCompatibleDC.restype is wintypes.HDC
