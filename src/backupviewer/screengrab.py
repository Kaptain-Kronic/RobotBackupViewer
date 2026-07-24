"""Screen capture for the phone view: BitBlt a rectangle of the real Windows
desktop and hand back a PNG. ctypes + zlib only - the stack stays locked, and
stdlib has no JPEG encoder but writing a PNG is forty lines.

The user picks the rectangle snip-style (see phoneview.picker_page): WebView2
can do neither transparent windows nor layered-window capture exclusion (both
spiked dead on Win11), so a live "hollow frame" window is off the table - the
picker freezes the monitor into a screenshot instead, and the chosen physical
rect is then grabbed live per phone pull.

DPI: capture runs in whatever thread asked (an HTTP handler); each call flips
that thread to per-monitor DPI awareness (and restores it) so window rects
and BitBlt agree on PHYSICAL pixels even on scaled displays.
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
_PER_MONITOR_AWARE_V2 = -4


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def png_encode(width: int, height: int, rgba: bytes, level: int = 3) -> bytes:
    """RGBA bytes (row-major, no padding) -> a complete PNG file."""
    if len(rgba) != width * height * 4:
        raise ValueError(f"need {width * height * 4} bytes, got {len(rgba)}")

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level))
            + chunk(b"IEND", b""))


def _grab_rect_rgba(x: int, y: int, w: int, h: int) -> bytes:
    """BitBlt a physical-pixel screen rect -> RGBA bytes. Caller owns DPI
    context. Raises OSError when GDI says no (locked desktop, secure input)."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise OSError("no screen DC")
    hdc_mem = hbm = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hdc_mem or not hbm:
            raise OSError("could not build a capture bitmap")
        gdi32.SelectObject(hdc_mem, hbm)
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y,
                            SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt failed")
        bmi = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER),
                                biWidth=w, biHeight=-h,  # negative = top-down rows
                                biPlanes=1, biBitCount=32, biCompression=BI_RGB)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi),
                           DIB_RGB_COLORS) != h:
            raise OSError("GetDIBits failed")
        raw = bytearray(buf.raw)
        # GDI hands back BGRA with garbage alpha: swap B<->R, force alpha opaque
        raw[0::4], raw[2::4] = raw[2::4], raw[0::4]
        raw[3::4] = b"\xff" * (w * h)
        return bytes(raw)
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def _dpi_aware(fn, *args):
    """Run fn with THIS thread per-monitor-DPI-aware, restoring after - so
    window rects and BitBlt speak physical pixels on scaled displays."""
    user32 = ctypes.windll.user32
    prev = None
    try:
        prev = user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(_PER_MONITOR_AWARE_V2))
    except (AttributeError, OSError):  # pre-1703 Windows: already consistent
        pass
    try:
        return fn(*args)
    finally:
        if prev:
            try:
                user32.SetThreadDpiAwarenessContext(prev)
            except (AttributeError, OSError):
                pass


def grab_rect_png(x: int, y: int, w: int, h: int) -> bytes:
    """A physical screen rect as PNG bytes."""
    if w <= 0 or h <= 0:
        raise OSError("empty capture rect")
    return png_encode(w, h, _dpi_aware(_grab_rect_rgba, x, y, w, h))


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_MONITOR_DEFAULTTONEAREST = 2
_HWND_TOPMOST = -1
_SWP_SHOWWINDOW = 0x0040
_SWP_FRAMECHANGED = 0x0020
_SW_SHOW = 5

# Window prototypes on an ISOLATED user32 handle (never mutating the shared
# ctypes.windll cache app.py also uses). Without restype/argtypes, ctypes
# treats every handle as a 32-bit C int - and a top-level HWND on Win64 can
# exceed 32 bits, so it gets truncated and the call silently no-ops. That was
# the dc9d9b3 "picker never shows" bug: SetWindowPos got a truncated HWND,
# returned 0, and the hidden window was never revealed. Real handles fix it.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.FindWindowW.restype = wintypes.HWND
_u32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_u32.SetWindowPos.restype = wintypes.BOOL
_u32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
_u32.ShowWindow.restype = wintypes.BOOL
_u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.IsWindowVisible.argtypes = (wintypes.HWND,)
_u32.MonitorFromWindow.restype = wintypes.HANDLE
_u32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
_u32.GetMonitorInfoW.restype = wintypes.BOOL
_u32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
_u32.GetSystemMetrics.restype = ctypes.c_int
_u32.GetSystemMetrics.argtypes = (ctypes.c_int,)


def monitor_rect_for_window(title: str) -> tuple[int, int, int, int]:
    """The physical rect (x, y, w, h) of the monitor holding the window with
    this title - or the primary monitor when the window isn't found. This is
    the screen the area picker should cover."""
    def query():
        hwnd = _u32.FindWindowW(None, title)
        if hwnd:
            hmon = _u32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
            mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
            if hmon and _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcMonitor
                return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        return (0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))
    return _dpi_aware(query)


def cover_window_on_monitor(title: str, rect: tuple[int, int, int, int],
                            tries: int = 25, delay: float = 0.1) -> bool:
    """Reveal the window with this title and force it to exactly the physical
    rect, topmost - the area picker, made fullscreen on its monitor. pywebview
    materializes windows asynchronously and speaks logical pixels; a DPI-aware
    ShowWindow + SetWindowPos sidesteps both. ShowWindow is what actually
    un-hides a window born hidden; SetWindowPos alone won't. Polls up to
    tries*delay seconds; False when the window never appeared or the move
    was rejected."""
    import time as _time
    for _ in range(tries):
        hwnd = _u32.FindWindowW(None, title)
        if hwnd:
            def place():
                _u32.ShowWindow(hwnd, _SW_SHOW)
                return _u32.SetWindowPos(
                    hwnd, _HWND_TOPMOST, rect[0], rect[1], rect[2], rect[3],
                    _SWP_SHOWWINDOW | _SWP_FRAMECHANGED)
            return bool(_dpi_aware(place))
        _time.sleep(delay)
    return False


