"""Keyence CV-X live remote-desktop (screen mirror + mouse control).

A clean-room port of the proven C# reference client (`CvxRemote/mirror2.cs`, fully
reverse-engineered from packet captures - see CvxRemote/CVX_REMOTE_HANDOFF.md). NO
Keyence software involved; this is a completely separate code path from the CV-X
anon-FTP backup (keyencebackup.py) - keep both.

How it works (all detail in the handoff doc):
  - 3 TCP sockets to the controller: 8502 control/mouse, 8503 aux, 8504 video.
  - Every message = a 32-byte LE header [seq, ctx, type, opcode, method, 0, 0,
    bodyLen] + body.
  - The controller assigns a `ctx` per service `type` (0x18 login, 7 remote-
    desktop, 6 video); the client must LEARN it from replies and echo it back in
    every later message of that type. The captured client handshake
    (cvx_handshake/chan850x_tx.bin) is replayed in global-seq order, lockstep
    (wait for the op1 ack after an OPEN, the op6 response after a REQUEST), with
    the ctx fields patched in and the type7/method0x17 body pointed at the video
    ctx so frames get routed to 8504.
  - Video: full 1024x768 JPEGs pushed on change on 8504 (accumulate message
    bodies, scan FFD8..FFD9). Reply to the op5/method5 "please-ack" with the
    frame-ack "16" message to keep frames flowing.
  - Mouse: 8502 type7 op5 method0x34, body [7, h1, h2, eventId, X, Y, h3].

One session per controller (don't connect while the Keyence Terminal or an
operator is on it). `_connect` is injectable so the framing/handshake logic is
unit-testable offline against a fake socket.
"""
from __future__ import annotations

import itertools
import logging
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

CTRL_PORT, AUX_PORT, VIDEO_PORT = 8502, 8503, 8504
PORTS = (CTRL_PORT, AUX_PORT, VIDEO_PORT)
# controller service "type" ids (field[2]) whose ctx the client must learn+echo:
# 7 = remote-desktop (mouse), 6 = video stream. The video ctx is keyed by TYPE 6,
# NOT the port number - it's what routes frames to 8504 and stamps the frame-ack.
RD_TYPE, VIDEO_TYPE = 7, 6
SCREEN_W, SCREEN_H = 1024, 768
CONNECT_TIMEOUT = 6.0
_NONE_CTX = 0xFFFFFFFF

# mouse handles - client-side constants the controller just echoes (reuse as-is)
_H1, _H2, _H3 = 0x244DF81C, 0x117A79E7, 0xA54AC70B
# VapiMouseEventId
EV_MOVE, EV_LDOWN, EV_LUP, EV_RDOWN, EV_RUP = 0, 1, 2, 3, 4

# op5/meth5 frame-ack "16" message (header + 20-byte body); [0:4]=seq, [4:8]=ctx[6]
_FRAME_ACK = bytes.fromhex(
    "160000000a0000000600000006000000050000000000000000000000140000"
    "0048e1526988c403600000000038110000" "01000100")


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _put_u32(b, o, v):
    struct.pack_into("<I", b, o, v & 0xFFFFFFFF)


def _handshake_dir() -> Path:
    # PyInstaller re-roots this module's __file__ under _MEIPASS, so this resolves
    # in both dev and frozen builds (the spec bundles cvx_handshake/).
    return Path(__file__).resolve().parent / "cvx_handshake"


# The captured channel-open advertises the capture-time address in a NUL-padded
# ASCII "TCP:<ip>" field; rewrite it to the controller we're actually dialing.
# The replacement keeps the exact byte length (string + NUL padding), so the
# 32-byte-header framing around it is untouched.
_ADDR_RE = re.compile(rb"TCP:[0-9.]{7,15}\x00+")


def _patch_addr(blob: bytes, ip: str) -> bytes:
    def _sub(m):
        new = b"TCP:" + ip.encode("ascii")
        pad = len(m.group(0)) - len(new)
        return new + b"\x00" * pad if pad >= 0 else m.group(0)
    return _ADDR_RE.sub(_sub, blob)


def parse_messages(port: int, blob: bytes) -> list[dict]:
    """Split a channel blob into logical messages via the 32-byte header +
    bodyLen. Each: {port, seq, type, opcode, method, bytes}."""
    out = []
    off = 0
    n = len(blob)
    while off + 32 <= n:
        body_len = _u32(blob, off + 28)
        total = 32 + body_len
        if off + total > n:
            log.warning("cvx parse %d: truncated at %d (need %d)", port, off, total)
            break
        out.append({
            "port": port, "seq": _u32(blob, off), "type": _u32(blob, off + 8),
            "opcode": _u32(blob, off + 12), "method": _u32(blob, off + 16),
            "bytes": bytearray(blob[off:off + total]),
        })
        off += total
    return out


def _port_priority(port: int) -> int:
    return {CTRL_PORT: 0, AUX_PORT: 1, VIDEO_PORT: 2}.get(port, 3)


def extract_jpegs(buf: bytearray) -> list[bytes]:
    """Pull complete JPEGs (SOI FFD8 .. EOI FFD9) out of an accumulating body
    buffer, removing consumed bytes. Standard decoders tolerate the few
    inter-chunk header bytes left between SOI/EOI."""
    frames = []
    while True:
        soi = buf.find(b"\xff\xd8")
        if soi < 0:
            break
        eoi = buf.find(b"\xff\xd9", soi + 2)
        if eoi < 0:
            break
        end = eoi + 2
        frames.append(bytes(buf[soi:end]))
        del buf[:end]
    return frames


def _default_connect(ip: str, port: int):
    s = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(None)
    return s


class CvxRemoteSession:
    """One live remote-desktop session to a CV-X controller. start() connects +
    replays the handshake on background threads; poll latest_frame()/frames and
    drive send_mouse(); stop() tears down. `connect` is injectable for tests."""

    def __init__(self, ip: str, *, connect=_default_connect, handshake_dir=None):
        self.ip = ip
        self._connect = connect
        self._hs_dir = Path(handshake_dir) if handshake_dir else _handshake_dir()
        self._socks: dict[int, object] = {}
        self._ctx: dict[int, int] = {}          # service type -> controller ctx
        self._ctx_lock = threading.Lock()
        self._acks = {p: 0 for p in PORTS}       # op1 count per channel
        self._resp6 = {p: 0 for p in PORTS}      # op6 count per channel
        self._imgbuf = bytearray()
        self._img_lock = threading.Lock()
        self._latest: bytes | None = None
        self.frames = 0
        self._ctrl_seq = itertools.count(0x51)   # mouse seq
        self._ack_seq = itertools.count(0x101)   # frame-ack seq
        self._stop = threading.Event()
        self._alive = False
        self.error = ""
        self.handshake_done = False
        self._threads: list[threading.Thread] = []

    # -- lifecycle -----------------------------------------------------------

    def _load_handshake(self) -> list[dict]:
        msgs = []
        for p in PORTS:
            blob = _patch_addr((self._hs_dir / f"chan{p}_tx.bin").read_bytes(), self.ip)
            msgs.extend(parse_messages(p, blob))
        msgs.sort(key=lambda m: (m["seq"], _port_priority(m["port"])))
        return msgs

    def start(self) -> bool:
        """Connect the 3 sockets + start reader/replay threads. Returns False (and
        sets .error) if the controller can't be reached / is busy."""
        try:
            messages = self._load_handshake()
        except OSError as e:
            self.error = f"handshake blobs missing: {e}"
            return False
        try:
            for p in PORTS:
                self._socks[p] = self._connect(self.ip, p)
        except OSError as e:
            self.error = f"connect failed (camera off, or Terminal/operator already on it?): {e}"
            self._teardown()
            return False
        self._alive = True
        for p in PORTS:
            t = threading.Thread(target=self._reader, args=(p,), name=f"cvx-rx-{p}", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._replay, args=(messages,), name="cvx-replay", daemon=True)
        t.start()
        self._threads.append(t)
        return True

    @property
    def alive(self) -> bool:
        return self._alive and not self._stop.is_set()

    def latest_frame(self) -> bytes | None:
        return self._latest

    def stop(self):
        self._stop.set()
        self._alive = False
        self._teardown()

    def _teardown(self):
        for s in self._socks.values():
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._socks.clear()

    # -- readers -------------------------------------------------------------

    def _reader(self, port: int):
        s = self._socks[port]
        buf = bytearray()
        try:
            while not self._stop.is_set():
                data = s.recv(65536)
                if not data:
                    break
                buf += data
                if port == VIDEO_PORT:
                    self._parse_video(buf)
                else:
                    self._parse_ctrl(port, buf)
        except OSError:
            pass
        finally:
            if port == CTRL_PORT:      # control channel dropped -> session is done
                self._alive = False

    def _learn_ctx(self, type_, ctx):
        if ctx != _NONE_CTX and type_ != _NONE_CTX and type_ < 1000:
            with self._ctx_lock:
                self._ctx[type_] = ctx

    def _parse_ctrl(self, port: int, buf: bytearray):
        while len(buf) >= 32:
            body_len = _u32(buf, 28)
            total = 32 + body_len
            if len(buf) < total:
                return
            ctx = _u32(buf, 4); type_ = _u32(buf, 8); op = _u32(buf, 12)
            self._learn_ctx(type_, ctx)
            if op == 1:
                self._acks[port] += 1
            elif op == 6:
                self._resp6[port] += 1
            del buf[:total]

    def _parse_video(self, buf: bytearray):
        need_ack = False
        while len(buf) >= 32:
            body_len = _u32(buf, 28)
            if body_len > 10_000_000:            # desync -> resync one byte at a time
                del buf[:1]
                continue
            total = 32 + body_len
            if len(buf) < total:
                break
            ctx = _u32(buf, 4); type_ = _u32(buf, 8); op = _u32(buf, 12); meth = _u32(buf, 16)
            self._learn_ctx(type_, ctx)
            if op == 1:
                self._acks[VIDEO_PORT] += 1
            elif op == 6:
                self._resp6[VIDEO_PORT] += 1
            if op == 5 and meth == 5:            # end-of-frame: controller wants an ack
                need_ack = True
            if body_len > 0:
                with self._img_lock:
                    self._imgbuf += buf[32:total]
            del buf[:total]
        with self._img_lock:
            for jpg in extract_jpegs(self._imgbuf):
                self._latest = jpg
                self.frames += 1
        if need_ack:
            self._send_frame_ack()

    # -- replay (the handshake) ---------------------------------------------

    def _prepare(self, m: dict) -> bytearray:
        """Patch a replayed handshake message before sending: echo the
        controller-assigned ctx for this service type (except OPEN/channel-open,
        opcodes 0/2, which carry no ctx yet), and point the video-route message
        (type7 / method0x17) at the learned video-service (type 6) ctx so frames
        get pushed to 8504."""
        b = bytearray(m["bytes"])
        if m["opcode"] not in (0, 2):
            with self._ctx_lock:
                ctx = self._ctx.get(m["type"])
            if ctx is not None:
                _put_u32(b, 4, ctx)
        if m["type"] == RD_TYPE and m["method"] == 0x17 and len(b) >= 36:
            with self._ctx_lock:
                vctx = self._ctx.get(VIDEO_TYPE)
            if vctx is not None:
                _put_u32(b, 32, vctx)
        return b

    def _replay(self, messages: list[dict]):
        try:
            for m in messages:
                if self._stop.is_set() or not self._alive:
                    return
                port = m["port"]
                # the 8504 op6/meth5 frame-ack "prime" is sent reactively, not replayed
                if port == VIDEO_PORT and m["opcode"] == 6 and m["method"] == 5:
                    continue
                b = self._prepare(m)
                a0, r0 = self._acks[port], self._resp6[port]
                try:
                    self._socks[port].sendall(b)
                except OSError as e:
                    self.error = f"handshake send failed: {e}"
                    self._alive = False
                    return
                if m["opcode"] == 0:
                    self._wait(lambda: self._acks[port] > a0, 1.5)
                elif m["opcode"] == 5:
                    self._wait(lambda: self._resp6[port] > r0, 1.5)
                else:
                    time.sleep(0.25 if m["seq"] == 0 else 0.06)
            self.handshake_done = True
            log.info("cvx %s handshake replayed", self.ip)
        except Exception as e:  # noqa: BLE001 - never let the worker crash silently
            log.exception("cvx replay failed")
            self.error = f"{type(e).__name__}: {e}"
            self._alive = False

    def _wait(self, pred, timeout: float):
        end = time.time() + timeout
        while time.time() < end:
            if self._stop.is_set() or pred():
                return
            time.sleep(0.005)

    # -- outbound: mouse + frame-ack ----------------------------------------

    def _send_frame_ack(self):
        s = self._socks.get(VIDEO_PORT)
        if s is None:
            return
        with self._ctx_lock:
            ctx = self._ctx.get(VIDEO_TYPE, _NONE_CTX)
        b = bytearray(_FRAME_ACK)
        _put_u32(b, 0, next(self._ack_seq))
        _put_u32(b, 4, ctx)
        try:
            s.sendall(b)
        except OSError:
            pass

    def send_mouse(self, event_id: int, x: int, y: int):
        """Send one mouse event (0=move,1=down,2=up,3=rdown,4=rup) at controller
        pixel (x,y) in the 1024x768 screen space."""
        s = self._socks.get(CTRL_PORT)
        if s is None or not self.alive:
            return
        x = max(0, min(SCREEN_W, int(x)))
        y = max(0, min(SCREEN_H, int(y)))
        with self._ctx_lock:
            ctx = self._ctx.get(RD_TYPE, _NONE_CTX)
        b = bytearray(60)
        _put_u32(b, 0, next(self._ctrl_seq)); _put_u32(b, 4, ctx)
        _put_u32(b, 8, RD_TYPE); _put_u32(b, 12, 5); _put_u32(b, 16, 0x34); _put_u32(b, 28, 28)
        _put_u32(b, 32, 7); _put_u32(b, 36, _H1); _put_u32(b, 40, _H2)
        _put_u32(b, 44, event_id); _put_u32(b, 48, x); _put_u32(b, 52, y); _put_u32(b, 56, _H3)
        try:
            s.sendall(b)
        except OSError:
            pass

    def click(self, x: int, y: int):
        """A left click = move, down, up at the same point (spaced like the real client)."""
        self.send_mouse(EV_MOVE, x, y); time.sleep(0.13)
        self.send_mouse(EV_LDOWN, x, y); time.sleep(0.12)
        self.send_mouse(EV_LUP, x, y)


# -- MJPEG frame server ----------------------------------------------------------
# A tiny localhost HTTP server that streams a session's JPEG frames as
# multipart/x-mixed-replace, so a plain <img src="http://127.0.0.1:PORT/cvx/<id>">
# in the (file://, no-CSP) frontend renders the live screen with zero JS decoding.

class _MjpegHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        sid = self.path.rsplit("/", 1)[-1].split("?")[0]
        sess = self.server.registry.get(sid)  # type: ignore[attr-defined]
        if sess is None:
            self.send_error(404, "no such session")
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        last = -1
        try:
            while sess.alive:
                if sess.frames != last:
                    last = sess.frames
                    jpg = sess.latest_frame()
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                time.sleep(0.04)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *a):  # silence per-request logging
        pass


def start_frame_server(registry: dict) -> ThreadingHTTPServer:
    """Start the MJPEG server on a free localhost port; returns the server (its
    .server_address[1] is the port). `registry` maps session_id -> CvxRemoteSession."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MjpegHandler)
    srv.registry = registry  # type: ignore[attr-defined]
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="cvx-mjpeg", daemon=True).start()
    return srv
