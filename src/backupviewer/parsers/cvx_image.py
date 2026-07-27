"""Keyence CV-X backup images: what the `.bmp` files under `cv-x/setting/`
actually hold, and how to turn one into pixels a browser can show.

There are two kinds, and the bit depth tells them apart. Checked against 157
real files off six cameras and two plants: every 8-bpp file carries an identity
grayscale palette, every 24-bpp file carries packed range data, and nothing else
appears (all `BITMAPINFOHEADER`, uncompressed, bottom-up).

  **8 bpp, palettised** - an INTENSITY image: a real grayscale photo of the
  scene, the taught master or a captured trigger. Shows as-is.

  **24 bpp, BGR** - a HEIGHT image, and *not* a photo. The three bytes pack one
  15-bit range value::

      H = (G << 7) | (R << 4) | B          all-zero pixel = no data

  (A true height of 0 is therefore unreadable - it encodes to the same all-zero
  pixel as "no return". That is the format's limit, not this decoder's; real
  cameras sit their measuring range at ~4000-29000 counts, well clear of it.)

  Open one in Paint and you get the washed-out grey mess techs know, because
  viewers read those bytes as colour. Green is the high byte, so the picture is
  *almost* legible, which is exactly what makes it misleading.

How the packing was established, since it is not documented anywhere: on a
smooth surface the green byte steps by one at the moment the low field rolls
over 127 -> 0, which pins G's weight at 128 rather than 256; and scored across
whole scanlines that arrangement is ~4x smoother than any other way of ordering
the same three bytes. Corroborating tells: R never exceeds 7 (3 bits) and B
never exceeds 15 (4 bits), so the low field is exactly 7 bits wide.

A height image is rendered here with a p1..p99 stretch (a fixed 0..32767 ramp
would leave every real part in a two-shade band) over a blue->red ramp, and
no-data pixels come back fully transparent - honest, and it lets the intensity
image show through where the sensor got no return when the two are overlaid.

Pure: bytes in, numbers out. The caller owns file I/O and PNG encoding.
"""
from __future__ import annotations

import struct
from array import array

HEIGHT = "height"
INTENSITY = "intensity"

NO_DATA = -1            # sampled-height sentinel; renders transparent
_HEIGHT_BPP = 24
_INTENSITY_BPP = 8

# the ramp a CV-X tech expects for range data: far/low -> near/high
_RAMP_STOPS = ((0, 0, 128), (0, 128, 255), (0, 200, 120), (235, 220, 40), (200, 30, 30))


class BadImage(ValueError):
    """Not a BMP we can read (wrong magic, compressed, or an unseen depth)."""


# -- header ---------------------------------------------------------------------

def _header(data) -> dict:
    if len(data) < 30 or data[:2] != b"BM":
        raise BadImage("not a BMP")
    try:
        offset, dib = struct.unpack_from("<II", data, 10)
        width, height, planes, bpp = struct.unpack_from("<iiHH", data, 18)
        compression = struct.unpack_from("<I", data, 30)[0] if len(data) >= 34 else 0
    except struct.error as e:                 # truncated header - our problem, not the caller's
        raise BadImage(f"truncated BMP header: {e}") from e
    if dib < 40:
        raise BadImage(f"unsupported DIB header ({dib} bytes)")
    if compression != 0:
        raise BadImage(f"compressed BMP (compression {compression})")
    if bpp not in (_HEIGHT_BPP, _INTENSITY_BPP):
        raise BadImage(f"unsupported depth ({bpp} bpp)")
    if width <= 0 or height == 0 or planes != 1:
        raise BadImage("bad BMP geometry")
    return {
        "kind": HEIGHT if bpp == _HEIGHT_BPP else INTENSITY,
        "width": width,
        "height": abs(height),
        "bpp": bpp,
        "offset": offset,
        "dib": dib,
        "top_down": height < 0,          # a negative biHeight means row 0 is first
        "stride": ((width * bpp + 31) // 32) * 4,
    }


def header_kind(data) -> str:
    """`"height"` / `"intensity"` for a readable CV-X BMP, `""` for anything
    else. Cheap enough to run over a whole backup off the first 64 bytes -
    that is what decides whether the photos tab lights up, and a name alone is
    not evidence that a file is an image."""
    try:
        return _header(data)["kind"]
    except BadImage:
        return ""


def probe(data) -> dict:
    """`{kind, width, height, bpp}` - the header, without touching pixels."""
    h = _header(data)
    return {k: h[k] for k in ("kind", "width", "height", "bpp")}


# -- pixels ---------------------------------------------------------------------

def _sample_step(width: int, height: int, max_dim: int) -> int:
    """Nearest-neighbour decimation factor. Sampling *during* the decode is the
    whole performance story: a 2048x2048 master is 4.2M pixels, and pure-Python
    per-pixel work on all of them takes seconds, while the viewer never shows
    more than ~1200 across."""
    if max_dim <= 0:
        return 1
    return max(1, -(-max(width, height) // max_dim))


def _palette(data, h: dict) -> list:
    """The BMP colour table as RGBA byte-strings. Read rather than assumed: on
    every real file it is an identity grey ramp, but a palette is what the
    format says the indices mean."""
    start = 14 + h["dib"]
    used = struct.unpack_from("<I", data, 46)[0] if len(data) >= 50 else 0
    n = used or (1 << h["bpp"])
    out = []
    for i in range(n):
        o = start + i * 4
        if o + 3 > len(data):
            break
        out.append(bytes((data[o + 2], data[o + 1], data[o], 255)))   # stored BGRA
    while len(out) < 256:
        g = len(out) & 0xFF
        out.append(bytes((g, g, g, 255)))          # a short table: fall back to grey
    return out


def height_field(data, max_dim: int = 0):
    """Decode a 24-bpp height BMP to `(width, height, array("i") of values)`,
    row 0 at the top, `NO_DATA` where the sensor returned nothing.

    This is the honest numeric view - the caller can render it, diff it, or read
    a value out of it. `max_dim` decimates while decoding (0 = full resolution).
    """
    h = _header(data)
    if h["kind"] != HEIGHT:
        raise BadImage("not a height image")
    w, hh, stride, off = h["width"], h["height"], h["stride"], h["offset"]
    if off + stride * hh > len(data):
        raise BadImage("truncated BMP pixel data")

    step = _sample_step(w, hh, max_dim)
    ow = -(-w // step)
    oh = -(-hh // step)
    end = w * 3
    vals = array("i")
    for oy in range(oh):
        sy = oy * step
        row = sy if h["top_down"] else hh - 1 - sy      # bottom-up is the norm here
        o = off + row * stride
        line = data[o:o + stride]
        bs = line[0:end:3][::step]
        gs = line[1:end:3][::step]
        rs = line[2:end:3][::step]
        vals.extend([
            ((g << 7) | (r << 4) | b) if (b or g or r) else NO_DATA
            for b, g, r in zip(bs, gs, rs)
        ])
    return ow, oh, vals


def _stretch(vals, lo_pct: float = 1.0, hi_pct: float = 99.0):
    """Percentile window over the valid samples -> `(lo, hi)`. Histogram rather
    than a sort: the range is only 15 bits, and a sort of a megapixel of Python
    ints costs more than the whole decode."""
    hist = [0] * 32768
    n = 0
    for v in vals:
        if v != NO_DATA and 0 <= v < 32768:
            hist[v] += 1
            n += 1
    if not n:
        return 0, 1
    want_lo, want_hi = n * lo_pct / 100.0, n * hi_pct / 100.0
    lo = hi = 0
    seen = 0
    for v, c in enumerate(hist):
        if not c:
            continue
        prev, seen = seen, seen + c
        if prev <= want_lo:
            lo = v
        if prev < want_hi:
            hi = v
    return lo, (hi if hi > lo else lo + 1)


def _ramp_lut() -> list:
    """256 RGBA entries interpolated across the ramp stops."""
    out = []
    segs = len(_RAMP_STOPS) - 1
    for i in range(256):
        x = i * segs / 255.0
        s = min(int(x), segs - 1)
        f = x - s
        a, b = _RAMP_STOPS[s], _RAMP_STOPS[s + 1]
        out.append(bytes((int(a[0] + (b[0] - a[0]) * f),
                          int(a[1] + (b[1] - a[1]) * f),
                          int(a[2] + (b[2] - a[2]) * f), 255)))
    return out


_RAMP = _ramp_lut()
_CLEAR = b"\x00\x00\x00\x00"


def to_rgba(data, max_dim: int = 1200):
    """Decode any CV-X BMP to `(width, height, rgba bytes)`, top row first -
    ready for `screengrab.png_encode`. Intensity comes through as its own grey
    values; height is stretched and ramped, with no-data transparent."""
    h = _header(data)
    if h["kind"] == INTENSITY:
        return _intensity_rgba(data, h, max_dim)

    ow, oh, vals = height_field(data, max_dim)
    lo, hi = _stretch(vals)
    span = hi - lo
    ramp = _RAMP
    px = []
    for v in vals:
        if v == NO_DATA:
            px.append(_CLEAR)
            continue
        i = (v - lo) * 255 // span
        px.append(ramp[0 if i < 0 else (255 if i > 255 else i)])
    return ow, oh, b"".join(px)


def _intensity_rgba(data, h: dict, max_dim: int):
    w, hh, stride, off = h["width"], h["height"], h["stride"], h["offset"]
    if off + stride * hh > len(data):
        raise BadImage("truncated BMP pixel data")
    lut = _palette(data, h)
    step = _sample_step(w, hh, max_dim)
    ow = -(-w // step)
    oh = -(-hh // step)
    rows = []
    for oy in range(oh):
        sy = oy * step
        row = sy if h["top_down"] else hh - 1 - sy
        o = off + row * stride
        rows.append(b"".join([lut[i] for i in data[o:o + w:step]]))
    return ow, oh, b"".join(rows)


# -- naming ---------------------------------------------------------------------
#
# Filenames are used for *labels and pairing only* - never to decide what a file
# holds. Two shapes appear on real cameras:
#
#   ref1_000.bmp / ref1_500.bmp          the taught master for a program
#   250130_000839_0000000180_CAM1_HEIGHT_OK.bmp    a captured trigger
#
# plus PickSim / PickSimNG variants. The `_500` suffix happens to be the
# intensity half of a master pair, but the bit depth is what proves it.

def pair_key(rel: str) -> str:
    """The identity a height/intensity pair shares, so the two halves of one
    scene group together. Anything unrecognised keys to itself and stands
    alone - a lone image is still worth showing."""
    folder, _, name = rel.rpartition("/")
    stem = name[:-4] if name.lower().endswith(".bmp") else name
    parts = stem.split("_")
    for i, p in enumerate(parts):
        if p.upper() in ("HEIGHT", "INTENSITY"):
            del parts[i]
            return folder + "/" + "_".join(parts)
    if len(parts) == 2 and parts[0].lower().startswith("ref") and parts[1].isdigit():
        return folder + "/" + parts[0]          # ref1_000 + ref1_500 -> ref1
    return rel


def label(rel: str) -> dict:
    """`{stamp, seq, cam, verdict}` read off a capture filename; blanks when the
    name carries none of it (a master, say). `verdict` is the camera's own OK/NG
    token, kept verbatim."""
    name = rel.rpartition("/")[2]
    stem = name[:-4] if name.lower().endswith(".bmp") else name
    parts = stem.split("_")
    out = {"stamp": "", "seq": "", "cam": "", "verdict": ""}
    if parts and parts[-1].upper() in ("OK", "NG"):
        out["verdict"] = parts[-1].upper()
    for p in parts:
        u = p.upper()
        if u.startswith("CAM") and u[3:].isdigit():
            out["cam"] = u
    if len(parts) >= 3 and len(parts[0]) == 6 and parts[0].isdigit() \
            and len(parts[1]) == 6 and parts[1].isdigit():
        d, t = parts[0], parts[1]
        out["stamp"] = (f"20{d[0:2]}-{d[2:4]}-{d[4:6]} {t[0:2]}:{t[2:4]}:{t[4:6]}")
        if parts[2].isdigit():
            out["seq"] = str(int(parts[2]))
    return out
