"""Window capture for the phone view: have one of THIS app's windows render its
client area into a memory bitmap and hand back a PNG. ctypes + zlib only -
the stack stays locked, and stdlib has no JPEG encoder but writing a PNG is
forty lines.

grab_window_png re-finds the window every call, so the phone mirror follows it
if the tech moves or resizes it - no rectangle to pick, no extra window to
manage: whatever the window shows is what the phone shows.

The window renders ITSELF (PrintWindow with PW_RENDERFULLCONTENT, which covers
WebView2's GPU-composited content). This used to be a copy of the SCREEN at the
window's position, so anything lying over the app - an email, a chat, another
program - went to the phone too (audit 2026-11-18). Only windows owned by this
process are ever looked up, so a stranger's window that happens to share a
title can never be mirrored either.

DPI: capture runs in whatever thread asked (an HTTP handler); each call flips
that thread to per-monitor DPI awareness (and restores it) so the client size
is measured in PHYSICAL pixels even on scaled displays.
"""
from __future__ import annotations

import ctypes
import os
import struct
import zlib
from ctypes import wintypes

DIB_RGB_COLORS = 0
BI_RGB = 0
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2      # Windows 8.1+: DirectComposition content too
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


# Prototypes on ISOLATED user32/gdi32 handles (never mutating the shared
# ctypes.windll cache app.py also uses). Without restype/argtypes, ctypes
# treats every handle as a 32-bit C int - and a top-level HWND on Win64 can
# exceed 32 bits, so it gets truncated and the call silently no-ops. Real
# pointer-sized handle types are mandatory (paid for by the picker-never-showed
# bug: a truncated HWND made SetWindowPos a no-op). Any new call added here
# needs its restype/argtypes too.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_g32 = ctypes.WinDLL("gdi32", use_last_error=True)
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = (_ENUM_PROC, wintypes.LPARAM)
_u32.EnumWindows.restype = wintypes.BOOL
_u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.GetClientRect.restype = wintypes.BOOL
_u32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_u32.PrintWindow.restype = wintypes.BOOL
_u32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
_u32.GetDC.restype = wintypes.HDC
_u32.GetDC.argtypes = (wintypes.HWND,)
_u32.ReleaseDC.restype = ctypes.c_int
_u32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
_g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
_g32.SelectObject.restype = wintypes.HGDIOBJ
_g32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
_g32.DeleteObject.restype = wintypes.BOOL
_g32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
_g32.DeleteDC.restype = wintypes.BOOL
_g32.DeleteDC.argtypes = (wintypes.HDC,)
_g32.GetDIBits.restype = ctypes.c_int
_g32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.POINTER(_BITMAPINFOHEADER), wintypes.UINT)


def _grab_window_rgba(hwnd, w: int, h: int) -> bytes:
    """The window's OWN client-area pixels as RGBA, rendered by the window into
    a memory bitmap - never the screen at its position, so nothing covering it
    is ever captured. Caller owns DPI context. Raises OSError when Windows will
    not render it (a locked desktop, a window being torn down)."""
    hdc_screen = _u32.GetDC(None)
    if not hdc_screen:
        raise OSError("no screen DC")
    hdc_mem = hbm = None
    try:
        hdc_mem = _g32.CreateCompatibleDC(hdc_screen)
        hbm = _g32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hdc_mem or not hbm:
            raise OSError("could not build a capture bitmap")
        _g32.SelectObject(hdc_mem, hbm)
        if not _u32.PrintWindow(hwnd, hdc_mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise OSError("the window would not render itself (PrintWindow failed)")
        bmi = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER),
                                biWidth=w, biHeight=-h,  # negative = top-down rows
                                biPlanes=1, biBitCount=32, biCompression=BI_RGB)
        buf = ctypes.create_string_buffer(w * h * 4)
        if _g32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi),
                          DIB_RGB_COLORS) != h:
            raise OSError("GetDIBits failed")
        raw = bytearray(buf.raw)
        # GDI hands back BGRA with garbage alpha: swap B<->R, force alpha opaque
        raw[0::4], raw[2::4] = raw[2::4], raw[0::4]
        raw[3::4] = b"\xff" * (w * h)
        return bytes(raw)
    finally:
        if hbm:
            _g32.DeleteObject(hbm)
        if hdc_mem:
            _g32.DeleteDC(hdc_mem)
        _u32.ReleaseDC(None, hdc_screen)


def _dpi_aware(fn, *args):
    """Run fn with THIS thread per-monitor-DPI-aware, restoring after - so
    window sizes are physical pixels on scaled displays."""
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


def _find_own_window(title: str):
    """The top-level window with exactly this title that THIS process owns, or
    None. Another program's window with the same title is never a match."""
    me = os.getpid()
    found = []
    buf = ctypes.create_unicode_buffer(512)

    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            _u32.GetWindowTextW(hwnd, buf, len(buf))
            if buf.value == title:
                found.append(hwnd)
                return False                      # stop: first match wins
        return True

    _u32.EnumWindows(_ENUM_PROC(visit), 0)
    return found[0] if found else None


def _client_size(hwnd) -> tuple[int, int] | None:
    """Physical (w, h) of a window's client area - its content, minus the OS
    title bar and borders. None when minimized (zero-size client)."""
    rc = wintypes.RECT()
    if not _u32.GetClientRect(hwnd, ctypes.byref(rc)) or rc.right <= 0 or rc.bottom <= 0:
        return None
    return (rc.right, rc.bottom)


def window_is_open(title: str) -> bool:
    return _dpi_aware(_find_own_window, title) is not None


def grab_window_png(title: str) -> bytes:
    """The client area of this app's window with this exact title, rendered by
    the window and returned as PNG - re-found every call, so the capture
    follows the window if it moved or resized. Raises OSError when the window
    is closed or minimized."""
    def grab():
        hwnd = _find_own_window(title)
        if not hwnd:
            raise OSError(f"the '{title}' window is not open")
        size = _client_size(hwnd)
        if size is None:
            raise OSError(f"the '{title}' window is minimized")
        w, h = size
        return png_encode(w, h, _grab_window_rgba(hwnd, w, h))
    return _dpi_aware(grab)
