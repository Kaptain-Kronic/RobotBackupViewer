"""Screen capture for the phone view: BitBlt the client area of a window (the
Matrox remote, i.e. the app window) and hand back a PNG. ctypes + zlib only -
the stack stays locked, and stdlib has no JPEG encoder but writing a PNG is
forty lines.

grab_window_png re-finds the window every call, so the phone mirror follows it
if the tech moves or resizes it - no rectangle to pick, no extra window to
manage: whatever the Matrox window shows is what the phone shows.

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


# Window prototypes on an ISOLATED user32 handle (never mutating the shared
# ctypes.windll cache app.py also uses). Without restype/argtypes, ctypes
# treats every handle as a 32-bit C int - and a top-level HWND on Win64 can
# exceed 32 bits, so it gets truncated and the call silently no-ops. Real
# pointer-sized handle types are mandatory (paid for by the picker-never-showed
# bug: a truncated HWND made SetWindowPos a no-op). Any new user32 call added
# here needs its restype/argtypes too.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.FindWindowW.restype = wintypes.HWND
_u32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
_u32.GetClientRect.restype = wintypes.BOOL
_u32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_u32.ClientToScreen.restype = wintypes.BOOL
_u32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))


def _client_rect(hwnd) -> tuple[int, int, int, int] | None:
    """Physical (x, y, w, h) of a window's client area - its content, minus
    the OS title bar and borders. None when minimized (zero-size client)."""
    rc = wintypes.RECT()
    if not _u32.GetClientRect(hwnd, ctypes.byref(rc)) or rc.right <= 0 or rc.bottom <= 0:
        return None
    pt = wintypes.POINT(0, 0)
    if not _u32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, rc.right, rc.bottom)


def window_is_open(title: str) -> bool:
    return bool(_dpi_aware(lambda: _u32.FindWindowW(None, title)))


def grab_window_png(title: str) -> bytes:
    """The client area of the window with this exact title, captured live as
    PNG - re-found every call, so the capture follows the window if it moved
    or resized. Raises OSError when the window is closed or minimized."""
    def grab():
        hwnd = _u32.FindWindowW(None, title)
        if not hwnd:
            raise OSError(f"the '{title}' window is not open")
        rect = _client_rect(hwnd)
        if rect is None:
            raise OSError(f"the '{title}' window is minimized")
        x, y, w, h = rect
        return png_encode(w, h, _grab_rect_rgba(x, y, w, h))
    return _dpi_aware(grab)


