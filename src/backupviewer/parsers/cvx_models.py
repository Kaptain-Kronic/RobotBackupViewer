"""Keyence CV-X 3D model blobs: the two container styles under `cv-x/setting/`,
the zlib->STL geometry decode, and the identity/calibration reads.

Decoded 2026-08-07/10 against one real 3D-pick backup - eleven tool folders'
worth of part CAD, workspace scans, gripper models, robot-model and hand-eye
calibration files - and nothing else. The sample is one camera's worth, and
this module claims only what that evidence supports.

Two container styles appear, all little-endian:

  **"1001-style"** (`.dat`, and the first section of HND/TDM `.tbd`) -
  u32 magic 1001 @0x00, u32 header size 1342 @0x04, u32 payload size @0x08,
  u16 type id @0x0c, then NUL-terminated name slots: Shift-JIS @0x0e
  (e.g. ハンドモデル0) and its English twin @0x4a ("Hand Model 0"). More
  languages sit at higher fixed slots this module does not need.

  **"0x1c-style"** (TDC_L / WSM_L / LYT_G `.tbd`) - u32 header size 28 @0x00,
  u32 1001 @0x04, u32 payload size @0x10; the payload holds zlib streams.

The key decode: a zlib stream inside a model `.tbd` inflates to bare
binary-STL facet records - 50 bytes each, 12 float32 (unit normal + three
vertices, in mm) + a u16 attribute - with no 80-byte header or count in
front. A stream is accepted as geometry only when its length is a whole
number of records AND at least 90% of sampled records open with a ~unit (or
exactly zero) normal; that statistical gate is what keeps a layout table or
a compressed text blob from ever being shown as a part. Verified data
points: a TDC part CAD of 5,144 facets spanning 36x100x71 mm, and WSM
workspace scans of 48,066 + 31,686 facets in ~1.3-1.5 m robot-world
coordinates.

The HND gripper assembly and the RMD robot arm joined that decode on
2026-08-10 (see raw_facets): same facet triples, 48-byte records,
uncompressed, at an offset 2 mod 4 - the misalignment that made an earlier
pass read both as noise.

What could NOT be proved, and is therefore not parsed here: the trailing
~2 MB after an HND mesh block (a smooth byte ramp - a texture or preview,
not geometry), the TDM template record structure, the RMD
kinematics doubles (its mesh now decodes; the joint table does not), and
the calibration file's record stride (0x88 on the one file seen,
so records are found by scanning + self-validation, never by hardcoding a
stride).

Pure: bytes in, dicts out. The caller owns file I/O and caching.
"""
from __future__ import annotations

import math
import struct
import zlib

MAGIC = 1001                     # both styles carry it, in different words
HEAD_1001 = 1342                 # the 1001-style header size (also its second word)
HEAD_1C = 28
KIND_1001 = "container-1001"
KIND_1C = "container-1c"

FACET = 50                       # one binary-STL record: 12 float32 + u16 attribute
HND_FACET = 48                   # the HND mesh's own record: the same 12 float32,
                                 # no attribute word (see raw_facets)
_HND_RUN = 24                    # consecutive self-proving records that mark it
_HND_MIN = 64                    # a block smaller than this is not a model
_HND_MAX_MM = 1e5                # a coordinate past this is not a tool dimension

_JP_SLOT = 0x0E                  # Shift-JIS name, NUL-terminated
_EN_SLOT = 0x4A                  # English name; further languages sit higher
_EN_SLOT_END = 0x86              # same 60-byte width as the JP slot

_NUL = bytes([0])                # built programmatically, never a literal escape

_CMF = b"x"                      # 0x78 - the zlib CMF byte all three magics share
_FLG = (0x01, 0x9C, 0xDA)
_NORM_SAMPLE = 200               # records sampled for the unit-normal gate
_UNIT_LO2 = 0.97 * 0.97          # squared, to skip the sqrt per record
_UNIT_HI2 = 1.03 * 1.03
_GOOD = 0.9

_RMD_MAKER = 0x53E               # "FANUC" on the file that proved the offset
_RMD_MODEL = 0x57E               # the model string, e.g. "M-20iD/35"
_RMD_SLOT = 64
_RMD_NAME_MAX = 40

_CAL_FLOOR = 0x200               # calibration payload proper; headers live below
_CAL_STEP = 2                    # scan alignment - records self-validate instead
_REC_DOUBLES = 16
_REC_BYTES = _REC_DOUBLES * 8
_VAL_MAX = 1e7                   # a plausible calibration double sits under this
_VAL_MIN = 1e-6                  # denormal floor: text bytes re-read as float64
_POS_MAX = 5000.0                # mm - a robot position within physical reach
_ANG_MAX = 360.0                 # degrees


class BadModel(ValueError):
    """Not a CV-X model container we can read (wrong magic, or shorter than
    the header it claims)."""


# -- containers -------------------------------------------------------------------

def header_kind(head: bytes) -> str:
    """`"container-1001"` / `"container-1c"` for a CV-X model container,
    `""` for anything else. Cheap enough to run over a whole backup off the
    first 64 bytes - a filename alone is not evidence of what a file holds."""
    if len(head) < 8:
        return ""
    a, b = struct.unpack_from("<II", head, 0)
    if a == MAGIC and b == HEAD_1001:
        return KIND_1001
    if a == HEAD_1C and b == MAGIC:
        return KIND_1C
    return ""


def _cstr(data: bytes, start: int, end: int, codec: str) -> str:
    return bytes(data[start:end]).partition(_NUL)[0].decode(codec, errors="replace")


def container_info(data: bytes) -> dict:
    """`{style, payload_size, type_id, label, label_en}` off a container
    header. 1001-style carries the name slots and a type id; 1c-style carries
    only a payload size (`type_id` is None and the labels are empty - the
    header genuinely has none, and inventing them would be a guess)."""
    kind = header_kind(data)
    if kind == KIND_1001:
        if len(data) < HEAD_1001:
            raise BadModel(f"shorter than its {HEAD_1001}-byte header ({len(data)} bytes)")
        return {
            "style": "1001",
            "payload_size": struct.unpack_from("<I", data, 0x08)[0],
            "type_id": struct.unpack_from("<H", data, 0x0C)[0],
            "label": _cstr(data, _JP_SLOT, _EN_SLOT, "cp932"),
            "label_en": _cstr(data, _EN_SLOT, _EN_SLOT_END, "ascii"),
        }
    if kind == KIND_1C:
        if len(data) < HEAD_1C:
            raise BadModel(f"shorter than its {HEAD_1C}-byte header ({len(data)} bytes)")
        return {
            "style": "1c",
            "payload_size": struct.unpack_from("<I", data, 0x10)[0],
            "type_id": None,
            "label": "",
            "label_en": "",
        }
    raise BadModel("not a CV-X model container")


# -- zlib -> STL geometry ---------------------------------------------------------

def _looks_like_facets(raw: bytes) -> bool:
    """The statistical gate: a whole number of 50-byte records, and >=90% of
    sampled records opening with a ~unit (or exactly zero) first vector. Both
    halves matter - `b"hello"*100` inflates to 500 bytes, a perfectly whole
    number of records, and only the normal test tells it from a mesh."""
    n = len(raw) // FACET
    if not n or len(raw) % FACET:
        return False
    step = -(-n // _NORM_SAMPLE)           # ceil -> at most _NORM_SAMPLE, evenly
    good = total = 0
    for i in range(0, n, step):
        nx, ny, nz = struct.unpack_from("<3f", raw, i * FACET)
        m2 = nx * nx + ny * ny + nz * nz   # NaN/inf fail both comparisons below
        total += 1
        if m2 == 0.0 or _UNIT_LO2 <= m2 <= _UNIT_HI2:
            good += 1
    return good >= total * _GOOD


def _facet_bounds(facets: bytes) -> dict:
    """Axis-aligned bounds over every vertex, in the mm the records carry.
    Non-finite components are skipped rather than poisoning the box."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for i in range(len(facets) // FACET):
        rec = struct.unpack_from("<12f", facets, i * FACET)
        for j in (3, 6, 9):                # the three vertices; [0:3] is the normal
            for a in range(3):
                v = rec[j + a]
                if not math.isfinite(v):
                    continue
                if v < lo[a]:
                    lo[a] = v
                if v > hi[a]:
                    hi[a] = v
    if any(l > h for l, h in zip(lo, hi)):
        return {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
    return {"min": lo, "max": hi}


def _facet_like(data: bytes, off: int) -> bool:
    """One 48-byte record reads as a facet: finite floats, tool-scale
    coordinates, a normal that is ~unit or exactly zero, and at least one
    non-zero vertex component. That last clause is what stops a block's edge
    from eating the zero padding around it - a padding record is all zeros,
    while a genuinely degenerate facet still carries its vertices."""
    if off + HND_FACET > len(data):
        return False
    rec = struct.unpack_from("<12f", data, off)
    if not all(math.isfinite(v) and abs(v) < _HND_MAX_MM for v in rec):
        return False
    if not any(rec[i] for i in range(3, 12)):
        return False
    m2 = rec[0] * rec[0] + rec[1] * rec[1] + rec[2] * rec[2]
    return m2 == 0.0 or _UNIT_LO2 <= m2 <= _UNIT_HI2


def _unit_run(data: bytes, off: int, want: int) -> bool:
    """`want` consecutive HND records from `off` all open with a ~unit or
    exactly-zero normal. A zero normal is legal (STL's "you compute it"), so
    a run must also contain at least one real unit normal to count."""
    seen_unit = False
    for i in range(want):
        o = off + i * HND_FACET
        if o + 12 > len(data):
            return False
        nx, ny, nz = struct.unpack_from("<3f", data, o)
        m2 = nx * nx + ny * ny + nz * nz
        if _UNIT_LO2 <= m2 <= _UNIT_HI2:
            seen_unit = True
        elif m2 != 0.0:
            return False
    return seen_unit


def raw_facets(data: bytes) -> dict:
    """An uncompressed facet block, or `{}` - the mesh inside an
    `HND_L_*.tbd` gripper AND the arm inside a `RBT_G_RMD_*.dat`.

    Same facet triples as the zlib path, three differences paid for by a
    2026-08-10 re-read of real files (two hand models and a robot model):
    records here are **48 bytes** - twelve float32, no u16 attribute - they
    are **not compressed**, and the block sits at an offset that is *2 mod
    4*, which is why an earlier pass reading 4-aligned floats saw noise and
    wrote the file off as an unreversed encoding.

    Nothing here is taken on faith. Blocks are *found*, never assumed: a
    candidate must open with a run of records whose normals prove themselves
    unit (or exactly zero, which STL allows), and it is then grown in both
    directions only over records that still read as facets. A sub-header
    ahead of a hand's block does carry the stride and a count - 46,946 and
    481,016 on the two hand files - and the decode lands within a handful of
    that without being told it, which is corroboration rather than
    instruction. The layout itself was proved geometrically: the stored
    normal equals the cross product of the record's own vertex winding to
    1.0000 on every file tried, which no accidental alignment survives.

    The robot arm in `RBT_G_RMD_*.dat` turned out to use the same encoding
    (32,250 facets, one contiguous block, an M-20iD/35 at its saved pose) -
    the file's ~1.8 MB "proprietary mesh" was the same 48-byte records all
    along. It is ONE fused mesh, not per-link geometry, so it can be drawn
    but not posed by joint angles.

    Returns `{"facets": bytes (repacked to the 50-byte form the rest of this
    module speaks), "tri_count": int, "offset": int, "blocks": int,
    "bounds": {...}}`; `{}` when no self-validating block is found.
    """
    limit = len(data) - HND_FACET
    blocks: list[tuple[int, int]] = []           # (start, record count)
    off = HEAD_1001
    while off < limit:
        if not _unit_run(data, off, _HND_RUN):
            off += 2
            continue
        # walk back over records that still read as facets (a block can open
        # with degenerate ones), then forward to its end
        start = off
        while start - HND_FACET >= HEAD_1001 and _facet_like(data, start - HND_FACET):
            start -= HND_FACET
        end = off + _HND_RUN * HND_FACET
        while end <= limit and _facet_like(data, end):
            end += HND_FACET
        blocks.append((start, (end - start) // HND_FACET))
        off = end
    blocks = [(s, n) for s, n in blocks if n >= _HND_MIN]
    if not blocks:
        return {}

    out = bytearray()
    kept = 0
    for start, n in blocks:
        for i in range(n):
            rec = struct.unpack_from("<12f", data, start + i * HND_FACET)
            if not all(math.isfinite(v) for v in rec):
                continue                  # a torn record is dropped, not drawn
            out += struct.pack("<12f", *rec) + b"\x00\x00"
            kept += 1
    if not kept:
        return {}
    facets = bytes(out)
    bounds = _facet_bounds(facets)
    if bounds["min"] == bounds["max"]:     # a point is not a model
        return {}
    return {"facets": facets, "tri_count": kept, "offset": blocks[0][0],
            "blocks": len(blocks), "bounds": bounds}


def stl_streams(data: bytes) -> list[dict]:
    """Every zlib stream in `data` that inflates to STL facet records, as
    `{offset, facets, tri_count, bounds}` in file order. Candidates are the
    three zlib magics (78 01 / 78 9c / 78 da); failed or incomplete inflates
    are skipped, and a stream that inflates but fails the facet gate is
    passed over. Absence is an empty list, never an error - a file with no
    geometry is a finding, not a failure."""
    out: list[dict] = []
    mv = memoryview(data)
    n = len(data)
    i = data.find(_CMF)
    while 0 <= i < n - 1:
        if data[i + 1] not in _FLG:
            i = data.find(_CMF, i + 1)
            continue
        d = zlib.decompressobj()
        try:
            raw = d.decompress(mv[i:])
        except zlib.error:
            i = data.find(_CMF, i + 1)
            continue
        if not d.eof:                      # truncated stream: half a mesh is a lie
            i = data.find(_CMF, i + 1)
            continue
        consumed = (n - i) - len(d.unused_data)
        if _looks_like_facets(raw):
            out.append({
                "offset": i,
                "facets": raw,
                "tri_count": len(raw) // FACET,
                "bounds": _facet_bounds(raw),
            })
        # a complete stream is opaque bytes - never rescan inside it
        i = data.find(_CMF, i + max(consumed, 1))
    return out


def mesh_arrays(facets: bytes, max_tris: int = 0) -> dict:
    """Facet records -> the flat arrays a viewer draws: `{vertices,
    triangles, tri_count, shown_tris, decimated, bounds}`.

    Shared corners dedupe through a dict keyed on the 3-decimal-rounded
    triple (a micron at part scale), which typically collapses the payload
    ~6x. `max_tris` > 0 takes an even every-k-th sample of the facets so a
    48k-facet workspace scan does not have to cross the JS bridge whole;
    `tri_count` always reports the ORIGINAL count and `decimated` says the
    mesh was thinned - the UI must be able to say so, never silently show
    less. Degenerate records (non-finite, or corners that collapse to fewer
    than three points) are skipped, never a crash."""
    n = len(facets) // FACET
    step = 1
    decimated = False
    if max_tris > 0 and n > max_tris:
        step = -(-n // max_tris)           # ceil -> ceil(n/step) <= max_tris shown
        decimated = True
    verts: list[float] = []
    tris: list[int] = []
    seen: dict = {}
    for i in range(0, n, step):
        rec = struct.unpack_from("<12f", facets, i * FACET)
        keys = []
        for j in (3, 6, 9):
            x, y, z = rec[j], rec[j + 1], rec[j + 2]
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                break
            keys.append((round(x, 3), round(y, 3), round(z, 3)))
        if len(keys) < 3 or len(set(keys)) < 3:
            continue
        for key in keys:
            idx = seen.get(key)
            if idx is None:
                idx = len(seen)
                seen[key] = idx
                verts.extend(key)
            tris.append(idx)
    if verts:
        bounds = {"min": [min(verts[0::3]), min(verts[1::3]), min(verts[2::3])],
                  "max": [max(verts[0::3]), max(verts[1::3]), max(verts[2::3])]}
    else:
        bounds = {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
    return {"vertices": verts, "triangles": tris, "tri_count": n,
            "shown_tris": len(tris) // 3, "decimated": decimated, "bounds": bounds}


_STL_HEADER = b"BackupViewer export".ljust(80, bytes([0]))


def stl_bytes(facets: bytes) -> bytes:
    """A complete binary STL from facet records: the 80-byte header and u32
    count the camera stripped, then the records byte-exact - what came off
    the backup is what lands in the export."""
    return _STL_HEADER + struct.pack("<I", len(facets) // FACET) + facets


# -- robot model (RMD) ------------------------------------------------------------

def _name_slot(data: bytes, off: int) -> str:
    slot = bytes(data[off:off + _RMD_SLOT])
    if len(slot) < _RMD_SLOT:
        return ""
    name, nul, rest = slot.partition(_NUL)
    if not nul or rest.count(0) != len(rest):
        return ""                          # junk after the terminator: not a name
    if not 1 <= len(name) <= _RMD_NAME_MAX:
        return ""
    if any(c < 0x20 or c > 0x7E for c in name):
        return ""
    return name.decode("ascii")


def rmd_identity(data: bytes) -> dict:
    """`{maker, model}` off the two NUL-padded 64-byte name slots a robot
    model `.dat` carries at fixed offsets. A name must prove itself - 1-40
    printable-ASCII chars with the rest of the slot all NUL - or it comes
    back `""`; the slots validate independently. Never raises: the rest of
    the file (kinematics, the proprietary mesh) is unreversed, and a blank
    is honest where a guess is not."""
    return {"maker": _name_slot(data, _RMD_MAKER),
            "model": _name_slot(data, _RMD_MODEL)}


# -- hand-eye calibration (CLB) ---------------------------------------------------

def _plausible(v: float) -> bool:
    """A double a calibration file would actually hold: finite, and either
    exactly zero or of sane magnitude. The floor matters as much as the
    ceiling - ASCII text re-read as float64 lands in denormal territory,
    and without it half a header scans as `plausible`."""
    return math.isfinite(v) and (v == 0.0 or _VAL_MIN <= abs(v) < _VAL_MAX)


def _pose_like(vals) -> bool:
    if not any(vals[:6]):
        return False                       # indistinguishable from zero padding
    return (all(abs(v) < _POS_MAX for v in vals[:3])
            and all(abs(v) <= _ANG_MAX for v in vals[3:6]))


def calibration_records(data: bytes) -> list[list[float]]:
    """The 16-double hand-eye records in a calibration `.dat`:
    `[robot X,Y,Z,W,P,R | measured X,Y,Z,Rx,Ry,Rz | 4 extra]`. The one real
    file put them on a clean 300/200 mm grid at two Z heights, repeating
    every 0x88 bytes - but that stride is observed, not law, so the payload
    is scanned (from 0x200, 2-byte steps) for runs of 16 consecutive
    plausible doubles whose first six read as a robot pose (|X,Y,Z| under
    5 m, |W,P,R| within 360°). A record that matches consumes its 128 bytes;
    anything that does not self-validate returns `[]` - never a guess."""
    out: list[list[float]] = []
    i = _CAL_FLOOR
    end = len(data) - _REC_BYTES
    while i <= end:
        v0 = struct.unpack_from("<d", data, i)[0]
        if not (_plausible(v0) and abs(v0) < _POS_MAX):
            i += _CAL_STEP                 # cheap reject before the 16-wide read
            continue
        vals = struct.unpack_from("<16d", data, i)
        if _pose_like(vals) and all(map(_plausible, vals)):
            out.append(list(vals))
            i += _REC_BYTES
        else:
            i += _CAL_STEP
    return out


# -- naming -----------------------------------------------------------------------
#
# Filenames are used for *grouping and labels only* - never to decide what a
# file holds; the container header and the facet gate do that. WSM files in
# particular hold a workspace scan in pick tools but a copy of the part CAD
# in check tools, so the name can only say "model" and content decides the
# rest later.

_NAME_KINDS = (
    ("TDC_L", "part"),        # registered part CAD
    ("WSM_L", "model"),       # scan or part copy - content decides which
    ("HND_L", "hand"),        # gripper assembly (layout unreversed)
    ("TDM_L", "template"),    # 3D-matching template data
    ("LYT_G", "layout"),      # screen layout, zlib -> sparse table
)


def classify(rel_path: str) -> str:
    """The model family a path's filename claims, `""` when it claims none.
    A claim, not proof - callers verify against the bytes."""
    name = rel_path.replace("\\", "/").rpartition("/")[2].upper()
    for prefix, kind in _NAME_KINDS:
        if name.startswith(prefix):
            return kind
    if "RBT_G_RMD" in name:
        return "robot"
    _head, sep, tail = name.partition("3D_RBT")
    if sep and "CLB" in tail:
        return "calibration"
    return ""
