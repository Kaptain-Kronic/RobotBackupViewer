"""QR encoder proofs, fully offline. The encoder was ground-truthed against an
independent decoder (zxing-cpp, 144-code sweep) before landing; these tests
keep every piece pinned without that dependency: RS math by syndrome check,
format info by BCH + code-distance properties, geometry against the spec's
fixed patterns, and a from-scratch reader that unmasks and re-reads the
payload out of the finished matrix."""
import pytest

from backupviewer import qr

URL = "http://192.0.2.1:8756/v/AbCdEfGh"


# -- Reed-Solomon: syndromes of every emitted codeword block must be zero ----------

def _gf_tables():
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


def _poly_eval(codeword: bytes, alpha_pow: int, exp, log) -> int:
    """codeword as polynomial (first byte = highest degree) at x = a^alpha_pow."""
    acc = 0
    for b in codeword:
        acc = (exp[log[acc] + alpha_pow] if acc else 0) ^ b
    return acc


@pytest.mark.parametrize("version,payload_len", [(1, 5), (2, 20), (3, 40), (4, 60), (5, 90)])
def test_rs_syndromes_zero(version, payload_len):
    exp, log = _gf_tables()
    datacw, ecn = qr._VERSIONS[version]
    cw = qr._codewords(b"A" * payload_len, version)
    assert len(cw) == datacw + ecn
    for i in range(ecn):
        assert _poly_eval(cw, i, exp, log) == 0, f"syndrome {i} nonzero"


def test_codewords_pad_bytes_alternate():
    cw = qr._codewords(b"hi", 1)          # 2 bytes payload, 19 data codewords
    # mode+count+payload = 28 bits -> terminator + pad-to-byte -> 4 data bytes used
    assert list(cw[4:19]) == [0xEC, 0x11] * 7 + [0xEC]


# -- format info -------------------------------------------------------------------

def test_format_bch_remainder_zero_for_all_masks():
    for mask in range(8):
        un = qr._format_bits(mask) ^ qr._FORMAT_MASK
        v = un
        while v.bit_length() > 10:
            v ^= qr._FORMAT_G << (v.bit_length() - 11)
        assert v == 0
        assert un >> 13 == qr._EC_L        # top 2 bits: the EC level
        assert (un >> 10) & 7 == mask


def test_format_codes_pairwise_distance():
    """BCH(15,5) has minimum distance 7; the fixed XOR preserves pairwise
    distance. A wrong generator or a dropped bit collapses this instantly."""
    codes = [qr._format_bits(m) for m in range(8)]
    assert len(set(codes)) == 8
    for i in range(8):
        for j in range(i + 1, 8):
            assert bin(codes[i] ^ codes[j]).count("1") >= 7


def test_format_msb_lands_on_8_0():
    """Placement order (the classic trap, caught by the zxing ground-truth):
    module (8,0) carries bit 14 of the format value, (0,8) carries bit 0."""
    for mask in (0, 3, 7):
        mat = qr.encode("x", mask=mask)
        f = qr._format_bits(mask)
        assert mat[8][0] == (f >> 14) & 1
        assert mat[0][8] == f & 1
        n = len(mat)
        assert mat[n - 1][8] == (f >> 14) & 1   # second copy starts bottom-left


# -- geometry ----------------------------------------------------------------------

def _expect_finder(mat, r0, c0):
    for dr in range(7):
        for dc in range(7):
            ring = min(dr, dc, 6 - dr, 6 - dc)
            want = 0 if ring == 1 else 1
            assert mat[r0 + dr][c0 + dc] == want, f"finder module ({dr},{dc})"


@pytest.mark.parametrize("text,version", [
    ("short", 1), ("x" * 20, 2), ("x" * 40, 3), ("x" * 60, 4), ("x" * 90, 5)])
def test_geometry(text, version):
    mat = qr.encode(text)
    n = len(mat)
    assert n == 17 + 4 * version
    assert all(len(row) == n for row in mat)
    _expect_finder(mat, 0, 0)
    _expect_finder(mat, 0, n - 7)
    _expect_finder(mat, n - 7, 0)
    # separators are light
    assert all(mat[7][c] == 0 for c in range(8))
    assert all(mat[r][7] == 0 for r in range(8))
    assert all(mat[7][c] == 0 for c in range(n - 8, n))
    assert all(mat[n - 8][c] == 0 for c in range(8))
    # timing alternates, dark on even
    for i in range(8, n - 8):
        assert mat[6][i] == 1 - i % 2
        assert mat[i][6] == 1 - i % 2
    # the dark module
    assert mat[4 * version + 9][8] == 1
    # alignment pattern (v2+): dark border, light ring, dark center
    if version >= 2:
        p = 4 * version + 10
        assert mat[p][p] == 1
        assert mat[p - 1][p] == 0
        assert mat[p - 2][p] == 1


def test_version_selection_and_limits():
    assert len(qr.encode("x" * 17)) == 21
    assert len(qr.encode("x" * 18)) == 25
    assert len(qr.encode("x" * 53)) == 29
    assert len(qr.encode("x" * 54)) == 33
    assert len(qr.encode("x" * 106)) == 37
    with pytest.raises(ValueError):
        qr.encode("x" * 107)
    assert len(qr.encode("é" * 53)) == 37   # utf-8 counts bytes, not chars


# -- the full read-back: unmask and re-read the payload out of the matrix ----------

def _function_map(n: int) -> set:
    """Every non-data module position, rebuilt here from the spec constants
    (not from the encoder's map)."""
    version = (n - 17) // 4
    fun = set()
    for r0, c0 in ((0, 0), (0, n - 8), (n - 8, 0)):      # finders + separators
        for r in range(8):
            for c in range(8):
                fun.add((r0 + r, c0 + c))
    for i in range(n):                                    # timing
        fun.add((6, i))
        fun.add((i, 6))
    if version >= 2:
        p = 4 * version + 10
        for r in range(p - 2, p + 3):
            for c in range(p - 2, p + 3):
                fun.add((r, c))
    fun.add((4 * version + 9, 8))                         # dark module
    for r in range(9):                                    # format areas
        fun.add((r, 8))
    for c in range(9):
        fun.add((8, c))
    for r in range(n - 8, n):
        fun.add((r, 8))
    for c in range(n - 8, n):
        fun.add((8, c))
    return fun


def _read_back(mat) -> bytes:
    n = len(mat)
    # format: MSB first along row 8 from the left
    f = 0
    for r, c in [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                 (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]:
        f = (f << 1) | mat[r][c]
    un = f ^ 0b101010000010010
    mask = (un >> 10) & 7
    fun = _function_map(n)
    mask_fn = qr._MASKS[mask]
    bits = []
    col, upward = n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for r in rows:
            for c in (col, col - 1):
                if (r, c) in fun:
                    continue
                bits.append(mat[r][c] ^ (1 if mask_fn(r, c) else 0))
        upward = not upward
        col -= 2
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        out.append(v)
    return bytes(out)


@pytest.mark.parametrize("text", [URL, "a", "x" * 17, "y" * 53, "z" * 106,
                                  "http://198.51.100.7:8760/v/t0k3n"])
@pytest.mark.parametrize("mask", [None, 0, 5])
def test_read_back_payload(text, mask):
    stream = _read_back(qr.encode(text, mask=mask))
    assert stream[0] >> 4 == 0b0100                        # byte mode
    count = ((stream[0] & 0xF) << 4) | (stream[1] >> 4)
    assert count == len(text.encode())
    payload = bytes(((stream[i] & 0xF) << 4) | (stream[i + 1] >> 4)
                    for i in range(1, 1 + count))
    assert payload.decode() == text


def test_matrix_is_binary():
    mat = qr.encode(URL)
    assert {v for row in mat for v in row} <= {0, 1}
