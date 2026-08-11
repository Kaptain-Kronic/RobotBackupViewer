"""CV-X remote-desktop protocol unit tests - the framing, ctx-echo, video
routing, JPEG harvest, frame-ack and mouse encoding, all exercised offline
against fake sockets (no controller, no network). The connect fn is injectable
so a session never touches a real socket here.

The critical regression these pin: the video-service ctx is keyed by service
TYPE 6, not the port number 8504 - get that wrong and live video never routes
and every frame-ack is stamped with the wrong context."""
import re
import struct

from backupviewer import cvx_remote as cx


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _msg(seq, ctx, type_, op, meth, body=b""):
    """Build one wire message: 32-byte LE header + body."""
    h = bytearray(32)
    struct.pack_into("<I", h, 0, seq)
    struct.pack_into("<I", h, 4, ctx)
    struct.pack_into("<I", h, 8, type_)
    struct.pack_into("<I", h, 12, op)
    struct.pack_into("<I", h, 16, meth)
    struct.pack_into("<I", h, 28, len(body))
    return bytes(h) + body


class FakeSock:
    """Captures everything sendall'd; recv/close are no-ops (readers aren't run)."""
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, b):
        self.sent += b

    def setsockopt(self, *a):
        pass

    def settimeout(self, *a):
        pass

    def close(self):
        pass


# -- framing -----------------------------------------------------------------

def test_parse_messages_roundtrip():
    blob = _msg(0, 0xAA, 0x18, 0, 0) + _msg(1, 0xAA, 7, 5, 0x34, b"body!") \
        + _msg(2, 0xAA, 6, 6, 5)
    msgs = cx.parse_messages(cx.CTRL_PORT, blob)
    assert [m["seq"] for m in msgs] == [0, 1, 2]
    assert [m["type"] for m in msgs] == [0x18, 7, 6]
    assert [m["opcode"] for m in msgs] == [0, 5, 6]
    assert msgs[1]["method"] == 0x34
    assert bytes(msgs[1]["bytes"][32:]) == b"body!"


def test_parse_messages_ignores_trailing_partial_header():
    blob = _msg(0, 0, 7, 0, 0) + b"\x01\x02\x03"   # 3 dangling bytes < 32
    msgs = cx.parse_messages(cx.CTRL_PORT, blob)
    assert len(msgs) == 1


def test_bundled_handshake_blobs_parse():
    """The blobs shipped in cvx_handshake/ must frame cleanly on every channel."""
    hs = cx._handshake_dir()
    total = 0
    for p in cx.PORTS:
        blob = (hs / f"chan{p}_tx.bin").read_bytes()
        msgs = cx.parse_messages(p, blob)
        assert msgs, f"no messages parsed from chan{p}_tx.bin"
        # every message's declared length matched (parse stops on truncation)
        assert sum(len(m["bytes"]) for m in msgs) == len(blob)
        total += len(msgs)
    assert total > 5


def test_patch_addr_rewrites_advertised_ip_same_length():
    """The channel-open's NUL-padded 'TCP:<ip>' field gets the ip we're dialing,
    at the exact same byte length - shorter and longer ips both pad/fit."""
    field = b"TCP:198.51.100.249" + b"\x00\x00"
    blob = b"\x01" * 8 + field + b"\x02" * 8
    for ip in ("10.0.0.5", "192.0.2.117", "100.100.100.100"):
        out = cx._patch_addr(blob, ip)
        assert len(out) == len(blob)
        assert b"TCP:" + ip.encode() + b"\x00" in out
        assert out.startswith(b"\x01" * 8) and out.endswith(b"\x02" * 8)
    # bundled blobs: the placeholder address must actually get replaced
    hs = cx._handshake_dir()
    for p in cx.PORTS:
        raw = (hs / f"chan{p}_tx.bin").read_bytes()
        patched = cx._patch_addr(raw, "192.0.2.9")
        assert len(patched) == len(raw)
        assert b"TCP:192.0.2.9\x00" in patched
        assert cx._ADDR_RE.search(raw), f"chan{p}_tx.bin lost its addr field"
    # a field too small for the dialed ip must fail loudly, never silently
    # replay the capture-time address
    tiny = b"TCP:1.2.3.4" + bytes([0])
    try:
        cx._patch_addr(tiny, "100.100.100.100")
        raise AssertionError("expected ValueError for a too-small addr field")
    except ValueError:
        pass


# RFC 5737 documentation ranges. Anything a capture legitimately carries has to
# come from one of these; a real plant address does not look like this.
_DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")
_DOTTED_QUAD = re.compile(rb"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")


def test_bundled_handshakes_carry_no_real_address():
    """FIREWALL: the handshake blobs are opaque bytes captured from a live
    camera, so nothing in review reads them. _patch_addr rewrites the TCP:
    field at connect time, which means a capture-time plant address would be
    functionally invisible AND still shipped in a public repo. Assert that
    every address in every bundled blob is a documentation-range placeholder -
    if a re-capture ever fails this, scrub the blob, do not relax the test."""
    hs = cx._handshake_dir()
    for p in cx.PORTS:
        raw = (hs / f"chan{p}_tx.bin").read_bytes()
        m = cx._ADDR_RE.search(raw)
        assert m, f"chan{p}_tx.bin lost its addr field"
        advertised = m.group(0)[len(b"TCP:"):].rstrip(b"\x00").decode("ascii")
        assert advertised.startswith(_DOC_NETS), \
            f"chan{p}_tx.bin advertises {advertised!r} - not a documentation range"
        # and nowhere else in the blob either (a hostname string, a second
        # service record - the TCP: field is not the only place bytes can hide)
        for other in _DOTTED_QUAD.findall(raw):
            got = other.decode("ascii")
            assert got.startswith(_DOC_NETS), \
                f"chan{p}_tx.bin contains the address {got!r}"


# -- JPEG harvest ------------------------------------------------------------

def test_extract_jpegs_pulls_frame_and_keeps_remainder():
    jpg = b"\xff\xd8" + b"payload" + b"\xff\xd9"
    buf = bytearray(b"\x00\x01" + jpg + b"\xff\xd8partial")   # leading junk + next SOI
    frames = cx.extract_jpegs(buf)
    assert frames == [jpg]
    assert bytes(buf) == b"\xff\xd8partial"   # consumed through EOI, rest kept


def test_extract_jpegs_waits_for_complete_frame():
    buf = bytearray(b"\xff\xd8 no end yet")
    assert cx.extract_jpegs(buf) == []
    assert bytes(buf) == b"\xff\xd8 no end yet"   # nothing consumed


# -- frame-ack constant ------------------------------------------------------

def test_frame_ack_shape():
    fa = cx._FRAME_ACK
    assert len(fa) == 52
    assert _u32(fa, 28) == 20   # bodyLen field -> 20-byte body (52 = 32 + 20)


# -- ctx echo + video routing (the regression) -------------------------------

def _session():
    return cx.CvxRemoteSession("10.0.0.9", connect=lambda ip, port: FakeSock())


def test_prepare_echoes_learned_ctx_for_service_type():
    s = _session()
    s._ctx[7] = 0x00C0FFEE
    out = s._prepare({"type": 7, "opcode": 5, "method": 0,
                      "bytes": bytearray(_msg(9, cx._NONE_CTX, 7, 5, 0))})
    assert _u32(out, 4) == 0x00C0FFEE


def test_prepare_does_not_touch_open_messages():
    s = _session()
    s._ctx[7] = 0x11223344
    raw = _msg(0, 0xFFFFFFFF, 7, 0, 0)   # opcode 0 = OPEN, no ctx yet
    out = s._prepare({"type": 7, "opcode": 0, "method": 0, "bytes": bytearray(raw)})
    assert _u32(out, 4) == 0xFFFFFFFF     # left untouched


def test_prepare_routes_video_using_type6_ctx_not_port():
    """type7/method0x17 body[32:36] must carry the ctx learned for TYPE 6."""
    s = _session()
    s._ctx[6] = 0xABCD1234          # video service ctx (type 6)
    s._ctx[8504] = 0xDEADBEEF       # a red herring keyed by the PORT number
    body = bytes(36 - 32)           # 4 body bytes at offset 32
    raw = _msg(5, 0, cx.RD_TYPE, 5, 0x17, body)
    out = s._prepare({"type": cx.RD_TYPE, "opcode": 5, "method": 0x17,
                      "bytes": bytearray(raw)})
    assert _u32(out, 32) == 0xABCD1234    # NOT 0xDEADBEEF


# -- video parse -> decode + reactive frame-ack ------------------------------

def _subhdr(data_len, first=False, last=False):
    """The 40-byte per-chunk sub-header exactly as the controller sends it
    (layout documented at cx._VIDEO_SUBHDR): handles, payload len, not-first/
    last flags, stream id, const 3, 1024x768, chunk data len (0 on the final
    chunk - the real controller doesn't fill it there)."""
    h = bytearray(cx._VIDEO_SUBHDR)
    h[0:8] = bytes.fromhex("48e1526988c40360")
    struct.pack_into("<I", h, 12, 24 + data_len)     # payload len = bodyLen-16
    struct.pack_into("<H", h, 16, 0 if first else 1)
    struct.pack_into("<H", h, 18, 1 if last else 0)
    struct.pack_into("<I", h, 20, 0x73)
    struct.pack_into("<I", h, 24, 3)
    struct.pack_into("<H", h, 32, 1024)
    struct.pack_into("<H", h, 34, 768)
    struct.pack_into("<I", h, 36, 0 if last else data_len)
    return bytes(h)


def _video_frame_messages(jpg, chunk=4096, seq0=1, ctx=0x5151):
    """Wrap a JPEG the way the controller ships one frame: op7/meth4 chunks,
    then the tail bytes on the op5/meth5 end-of-frame message, every body led
    by the 40-byte sub-header, the final one padded with the 8-byte trailer."""
    chunks = [jpg[i:i + chunk] for i in range(0, len(jpg), chunk)]
    out = b""
    for i, part in enumerate(chunks):
        seq = seq0 + i
        if i == len(chunks) - 1:
            body = _subhdr(len(part), first=(i == 0), last=True) + part + bytes(8)
            out += _msg(seq, ctx, cx.VIDEO_TYPE, 5, 5, body)
        else:
            body = _subhdr(len(part), first=(i == 0)) + part
            out += _msg(seq, ctx, cx.VIDEO_TYPE, 7, 4, body)
    return out


def test_parse_video_decodes_frame_and_acks_with_type6_ctx():
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.VIDEO_PORT] = fake
    s._ctx[cx.VIDEO_TYPE] = 0x5151     # learned video ctx

    jpg = b"\xff\xd8" + b"IMAGE-DATA" + b"\xff\xd9"
    # one chunk message carrying the whole JPEG (op5/meth5 = end-of-frame + ack ask)
    buf = bytearray(_video_frame_messages(jpg))
    s._parse_video(buf)

    assert s.frames == 1
    assert s.latest_frame() == jpg
    # a frame-ack went out on 8504, stamped with the type-6 ctx and first ack seq
    assert len(fake.sent) == 52
    assert _u32(fake.sent, 4) == 0x5151
    assert _u32(fake.sent, 0) == 0x101   # _ack_seq starts at 0x101


def test_parse_video_strips_subheaders_frame_is_byte_identical():
    """The artifacting regression: a frame split across chunk messages must
    come out byte-identical - no sub-header bytes left inside the scan data,
    where every stray byte decodes as garbage until the next restart marker."""
    s = _session()
    s._socks[cx.VIDEO_PORT] = FakeSock()
    # ~30 KB of scan-like payload with no accidental FFD8/FFD9 inside
    payload = bytes(range(255)) * 120
    jpg = b"\xff\xd8" + payload + b"\xff\xd9"
    wire = _video_frame_messages(jpg, chunk=4408)   # the chunk size seen live
    # arrives as arbitrary recv()-sized pieces, like the real reader sees it
    buf = bytearray()
    for i in range(0, len(wire), 1500):
        buf += wire[i:i + 1500]
        s._parse_video(buf)
    assert s.frames == 1
    assert s.latest_frame() == jpg


def test_parse_video_ignores_non_video_bodies():
    """Control traffic on 8504 (op1 acks, op6 responses) must never join the
    frame - an op6 body carrying JPEG markers would previously have been
    emitted as a phantom frame."""
    s = _session()
    s._socks[cx.VIDEO_PORT] = FakeSock()
    buf = bytearray(_msg(1, 0x5151, cx.VIDEO_TYPE, 1, 0)
                    + _msg(2, 0x5151, cx.VIDEO_TYPE, 6, 0,
                           b"\xff\xd8not-really-a-frame\xff\xd9"))
    s._parse_video(buf)
    assert s.frames == 0
    assert s.latest_frame() is None
    assert s._acks[cx.VIDEO_PORT] == 1 and s._resp6[cx.VIDEO_PORT] == 1


def test_parse_ctrl_learns_ctx_and_counts_acks():
    s = _session()
    buf = bytearray(_msg(1, 0x7777, 7, 1, 0) + _msg(2, 0x8888, 6, 6, 0))
    s._parse_ctrl(cx.CTRL_PORT, buf)
    assert s._ctx[7] == 0x7777
    assert s._ctx[6] == 0x8888
    assert s._acks[cx.CTRL_PORT] == 1
    assert s._resp6[cx.CTRL_PORT] == 1


def test_learn_ctx_rejects_sentinel_and_out_of_range_types():
    s = _session()
    s._learn_ctx(cx._NONE_CTX, 0x10)      # sentinel type
    s._learn_ctx(7, cx._NONE_CTX)         # sentinel ctx
    s._learn_ctx(9999, 0x10)              # type >= 1000 (e.g. a port number)
    assert s._ctx == {}


# -- mouse encoding ----------------------------------------------------------

def test_send_mouse_encodes_header_and_body():
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    s._ctx[cx.RD_TYPE] = 0x0202

    s.send_mouse(cx.EV_LDOWN, 300, 200)
    b = fake.sent
    assert len(b) == 60
    assert _u32(b, 4) == 0x0202          # ctx (type 7)
    assert _u32(b, 8) == cx.RD_TYPE      # service type 7
    assert _u32(b, 12) == 5              # opcode
    assert _u32(b, 16) == 0x34           # method
    assert _u32(b, 28) == 28             # bodyLen
    assert _u32(b, 32) == 7              # body handle
    assert _u32(b, 44) == cx.EV_LDOWN
    assert _u32(b, 48) == 300 and _u32(b, 52) == 200


def test_send_mouse_clamps_to_screen():
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    s.send_mouse(cx.EV_MOVE, 99999, -40)
    b = fake.sent
    assert _u32(b, 48) == cx.SCREEN_W - 1   # clamped to the last pixel
    assert _u32(b, 52) == 0


def test_mouse_event_ids_match_vendor_enum():
    """The eventId values are Keyence's own VapiMouseEventId (reflected from
    Vapi.Net.dll) - pin them so nobody 'tidies' the deliberate gap between
    EV_MUP (6) and EV_WHEEL_UP (10; 7-9 are long-press variants we don't send)."""
    assert (cx.EV_MOVE, cx.EV_LDOWN, cx.EV_LUP, cx.EV_RDOWN, cx.EV_RUP) == (0, 1, 2, 3, 4)
    assert (cx.EV_MDOWN, cx.EV_MUP) == (5, 6)
    assert (cx.EV_WHEEL_UP, cx.EV_WHEEL_DOWN) == (10, 11)
    # drags are their own events - a held-button move sent as plain MOVE is
    # ignored by the controller (the field's snap-at-release bug)
    assert (cx.EV_DRAGGED, cx.EV_WHEEL_DRAGGED) == (14, 15)
    # the wheel/middle ids ride the proven 60-byte message unchanged
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    s.send_mouse(cx.EV_WHEEL_DOWN, 512, 384)
    assert len(fake.sent) == 60
    assert _u32(fake.sent, 44) == 11
    assert _u32(fake.sent, 48) == 512 and _u32(fake.sent, 52) == 384


def _sent_events(fake):
    """Decode the eventId + x of every 60-byte mouse message sendall'd."""
    out = []
    for off in range(0, len(fake.sent), 60):
        out.append((_u32(fake.sent, off + 44), _u32(fake.sent, off + 48)))
    return out


def test_queue_mouse_reorders_bridge_arrivals():
    """The frontend fires events without awaiting, so bridge calls can arrive
    out of client order - queue_mouse must restore it before the socket."""
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    # arrival order 1, 0, 2 (x doubles as a label) -> wire order 0, 1, 2
    s.queue_mouse(1, cx.EV_LDOWN, 101, 0)
    assert _sent_events(fake) == []          # holds: seq 0 hasn't arrived
    s.queue_mouse(0, cx.EV_MOVE, 100, 0)
    s.queue_mouse(2, cx.EV_LUP, 102, 0)
    assert _sent_events(fake) == [(cx.EV_MOVE, 100), (cx.EV_LDOWN, 101), (cx.EV_LUP, 102)]


def test_queue_mouse_skips_a_dead_hole():
    """A seq that never arrives (its bridge call died) must stall the stream
    for at most ~150ms, not forever - later events skip past the hole."""
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    s.queue_mouse(0, cx.EV_MOVE, 100, 0)
    s.queue_mouse(2, cx.EV_MOVE, 102, 0)     # 1 is missing -> stalls
    assert _sent_events(fake) == [(cx.EV_MOVE, 100)]
    s._mouse_gap_t0 -= 1.0                   # age the gap past the 150ms window
    s.queue_mouse(3, cx.EV_MOVE, 103, 0)     # next arrival skips the hole
    assert _sent_events(fake) == [(cx.EV_MOVE, 100), (cx.EV_MOVE, 102), (cx.EV_MOVE, 103)]
    s.queue_mouse(4, cx.EV_MOVE, 104, 0)     # and the stream keeps flowing
    assert _sent_events(fake)[-1] == (cx.EV_MOVE, 104)


def test_send_mouse_noop_when_not_alive():
    s = _session()
    fake = FakeSock()
    s._socks[cx.CTRL_PORT] = fake
    s._alive = False
    s.send_mouse(cx.EV_MOVE, 10, 10)
    assert len(fake.sent) == 0


# -- lifecycle ---------------------------------------------------------------

def test_start_reports_connect_failure():
    def boom(ip, port):
        raise OSError("refused")
    s = cx.CvxRemoteSession("10.0.0.9", connect=boom)
    assert s.start() is False
    assert "connect failed" in s.error
    assert s.alive is False
