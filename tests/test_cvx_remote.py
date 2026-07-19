"""CV-X remote-desktop protocol unit tests - the framing, ctx-echo, video
routing, JPEG harvest, frame-ack and mouse encoding, all exercised offline
against fake sockets (no controller, no network). The connect fn is injectable
so a session never touches a real socket here.

The critical regression these pin: the video-service ctx is keyed by service
TYPE 6, not the port number 8504 - get that wrong and live video never routes
and every frame-ack is stamped with the wrong context."""
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

def test_parse_video_decodes_frame_and_acks_with_type6_ctx():
    s = _session()
    s._alive = True
    fake = FakeSock()
    s._socks[cx.VIDEO_PORT] = fake
    s._ctx[cx.VIDEO_TYPE] = 0x5151     # learned video ctx

    jpg = b"\xff\xd8" + b"IMAGE-DATA" + b"\xff\xd9"
    # a video message carrying the JPEG, then the op5/meth5 "please ack" end-of-frame
    buf = bytearray(_msg(1, 0x5151, cx.VIDEO_TYPE, 0, 0, jpg)
                    + _msg(2, 0x5151, cx.VIDEO_TYPE, 5, 5))
    s._parse_video(buf)

    assert s.frames == 1
    assert s.latest_frame() == jpg
    # a frame-ack went out on 8504, stamped with the type-6 ctx and first ack seq
    assert len(fake.sent) == 52
    assert _u32(fake.sent, 4) == 0x5151
    assert _u32(fake.sent, 0) == 0x101   # _ack_seq starts at 0x101


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
    assert _u32(b, 48) == cx.SCREEN_W
    assert _u32(b, 52) == 0


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
