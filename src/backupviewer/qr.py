"""QR encoder: text -> module matrix. Hand-rolled because the stack is locked
(stdlib only) and a QR is the phone-view handoff: the code IS the URL.

Scope on purpose: byte mode, EC level L, versions 1-5 - a single Reed-Solomon
block in every case, no interleaving. That caps payloads at 106 bytes, triple
what a LAN URL needs; anything longer raises. Output is a plain list of rows
(1 = dark) - rendering (SVG, quiet zone, scale) is the caller's job.

Verified end-to-end against an independent decoder (zxing-cpp) across every
version and mask before landing; the tests re-prove the pieces offline
(RS syndromes, BCH distance, finder/timing geometry, full unmask-and-read).
"""
from __future__ import annotations

# -- GF(256), the QR field (poly 0x11D, generator 2) ------------------------------

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(ecn: int) -> list[int]:
    """Monic generator polynomial prod (x - a^i), i=0..ecn-1; descending powers."""
    g = [1]
    for i in range(ecn):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c                          # c * x
            ng[j + 1] ^= _gf_mul(c, _EXP[i])    # c * a^i
        g = ng
    return g


def _rs_ecc(data: bytes, ecn: int) -> bytes:
    """The ecn Reed-Solomon codewords for data (polynomial long division)."""
    gen = _rs_generator(ecn)
    rem = bytearray(ecn)
    for b in data:
        lead = b ^ rem[0]
        del rem[0]
        rem.append(0)
        if lead:
            for i in range(ecn):
                rem[i] ^= _gf_mul(gen[i + 1], lead)
    return bytes(rem)


# -- versions (EC level L: one RS block each) --------------------------------------

# version: (data codewords, ec codewords). Byte-mode capacity = data - 2
# (4-bit mode + 8-bit count). v5-L tops out at 106 payload bytes.
_VERSIONS = {1: (19, 7), 2: (34, 10), 3: (55, 15), 4: (80, 20), 5: (108, 26)}
MAX_BYTES = _VERSIONS[5][0] - 2

_EC_L = 0b01                    # format-info EC indicator for level L
_FORMAT_G = 0b10100110111      # BCH(15,5) generator
_FORMAT_MASK = 0b101010000010010


def _format_bits(mask: int) -> int:
    """15-bit format info for EC L + mask: 5 data bits, BCH remainder, fixed XOR."""
    data = (_EC_L << 3) | mask
    val = data << 10
    while val.bit_length() > 10:
        val ^= _FORMAT_G << (val.bit_length() - 11)
    return ((data << 10) | val) ^ _FORMAT_MASK


# -- bit assembly ------------------------------------------------------------------

def _codewords(payload: bytes, version: int) -> bytes:
    datacw, ecn = _VERSIONS[version]
    bits: list[int] = []

    def put(val: int, n: int):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)              # byte mode
    put(len(payload), 8)        # count (8 bits through v9)
    for b in payload:
        put(b, 8)
    cap = datacw * 8
    put(0, min(4, cap - len(bits)))         # terminator
    if len(bits) % 8:
        put(0, 8 - len(bits) % 8)
    data = bytearray()
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        data.append(v)
    pad = (0xEC, 0x11)
    while len(data) < datacw:
        data.append(pad[(len(data) - (len(bits) // 8)) % 2])
    return bytes(data) + _rs_ecc(bytes(data), ecn)


# -- matrix ------------------------------------------------------------------------

def _blank(version: int):
    """Module grid + function-module map with every fixed pattern stamped:
    finders, separators, timing, alignment, dark module - and the format
    areas reserved (filled in after masking)."""
    n = 17 + 4 * version
    mat = [[0] * n for _ in range(n)]
    fun = [[False] * n for _ in range(n)]

    def stamp(r0, c0, size, rings):
        for dr in range(size):
            for dc in range(size):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < n and 0 <= c < n:
                    ring = min(dr, dc, size - 1 - dr, size - 1 - dc)
                    mat[r][c] = rings[min(ring, len(rings) - 1)]
                    fun[r][c] = True

    # finders with their light separators (stamp 9x9 centered off-grid clips)
    for r0, c0 in ((-1, -1), (-1, n - 8), (n - 8, -1)):
        stamp(r0, c0, 9, (0, 1, 0, 1, 1))   # separator, dark border, light ring, dark 3x3 core
    # timing
    for i in range(8, n - 8):
        mat[6][i] = mat[i][6] = 1 - (i % 2)
        fun[6][i] = fun[i][6] = True
    # alignment (v2+: the single non-finder-adjacent one at (p, p))
    if version >= 2:
        p = 4 * version + 10
        stamp(p - 2, p - 2, 5, (1, 0, 1))
    # dark module
    mat[4 * version + 9][8] = 1
    fun[4 * version + 9][8] = True
    # reserve the format areas
    for r, c in _format_coords(n)[0] + _format_coords(n)[1]:
        fun[r][c] = True
    return mat, fun


def _format_coords(n: int):
    """The two format-info copies as [(r, c)] * 15, bit 14 (MSB) first —
    ground-truthed against zxing's writer: (8,0) carries the MSB."""
    a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
         (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    b = [(n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8),
         (n - 6, 8), (n - 7, 8),
         (8, n - 8), (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4),
         (8, n - 3), (8, n - 2), (8, n - 1)]
    return a, b


def _place(mat, fun, codewords: bytes):
    """The zigzag: column pairs right to left (skipping the timing column),
    alternating up/down, right cell before left, function modules skipped."""
    n = len(mat)
    bits = ((byte >> (7 - i)) & 1 for byte in codewords for i in range(8))
    col, upward = n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for r in rows:
            for c in (col, col - 1):
                if not fun[r][c]:
                    mat[r][c] = next(bits, 0)
        upward = not upward
        col -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _apply_mask(mat, fun, mask: int):
    f = _MASKS[mask]
    n = len(mat)
    for r in range(n):
        for c in range(n):
            if not fun[r][c] and f(r, c):
                mat[r][c] ^= 1


def _write_format(mat, mask: int):
    bits = _format_bits(mask)
    a, b = _format_coords(len(mat))
    for i in range(15):
        v = (bits >> (14 - i)) & 1
        mat[a[i][0]][a[i][1]] = v
        mat[b[i][0]][b[i][1]] = v


def _penalty(mat) -> int:
    """The four mask-evaluation rules of the spec."""
    n = len(mat)
    score = 0
    # N1: runs of 5+ same-color in a row/column
    for line in list(mat) + [[mat[r][c] for r in range(n)] for c in range(n)]:
        run, prev = 0, None
        for v in line + [None]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, v
    # N2: 2x2 blocks of one color
    for r in range(n - 1):
        for c in range(n - 1):
            if mat[r][c] == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                score += 3
    # N3: finder-lookalike 1:1:3:1:1 with 4 light on either side
    pats = ((1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0), (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1))
    for line in list(mat) + [[mat[r][c] for r in range(n)] for c in range(n)]:
        for i in range(n - 10):
            t = tuple(line[i:i + 11])
            if t == pats[0] or t == pats[1]:
                score += 40
    # N4: dark-ratio deviation from 50%
    dark = sum(map(sum, mat))
    score += 10 * (abs(dark * 100 // (n * n) - 50) // 5)
    return score


def encode(text: str | bytes, mask: int | None = None) -> list[list[int]]:
    """QR matrix (rows of 0/1, 1 = dark) for text. Byte mode, EC L, version
    auto-picked 1-5. mask forces a mask 0-7 (tests); default picks by penalty.
    Raises ValueError when the payload cannot fit (> 106 bytes)."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    version = next((v for v, (d, _) in sorted(_VERSIONS.items())
                    if len(payload) <= d - 2), None)
    if version is None:
        raise ValueError(f"payload too long for a v5 QR ({len(payload)} > {MAX_BYTES} bytes)")
    codewords = _codewords(payload, version)

    def build(m: int):
        mat, fun = _blank(version)
        _place(mat, fun, codewords)
        _apply_mask(mat, fun, m)
        _write_format(mat, m)
        return mat

    if mask is not None:
        return build(mask)
    return min((build(m) for m in range(8)), key=_penalty)
