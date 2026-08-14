"""A stand-in CV-X controller: speaks the 850x remote-desktop framing on loopback.

Enough of the controller side to drive a real CvxRemoteSession end to end with no
hardware: it accepts the three channels, assigns a ctx per service type, answers
OPENs with an op1 ack and REQUESTs with an op6 response (which is exactly what
the replay blocks on), pushes JPEG frames on the video channel, and records every
inbound message so a test can assert on what the client actually put on the wire.

It is NOT a controller emulator - it does not interpret input, and it cannot tell
you the real wire method id for a console key (only a live capture can). What it
does prove is framing, ctx learning/echo, sequencing and the video path.

Run it standalone to hand-drive a session:  python tests/cvx_sim.py
"""
from __future__ import annotations

import socket
import struct
import threading

CTRL_PORT, AUX_PORT, VIDEO_PORT = 8502, 8503, 8504
PORTS = (CTRL_PORT, AUX_PORT, VIDEO_PORT)
_NONE_CTX = 0xFFFFFFFF
_VIDEO_SUBHDR = 40

# ctx the fake controller hands out per service type, mirroring a real one:
# distinct per type, and nothing the client could have guessed.
_CTX_FOR_TYPE = {6: 0x0606_A1A1, 7: 0x0707_B2B2, 24: 0x1818_C3C3}

# the client handles every input message carries (cvx_remote._H1/_H2) - what
# tells a real input apart from a same-sized handshake body
_H1, _H2 = 0x244DF81C, 0x117A79E7

# a 1x1 JPEG - real bytes, so extract_jpegs()'s SOI..EOI scan has something valid
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc2"
    "000b080001000101011100ffc4001400010000000000000000000000000000000"
    "9ffda0008010100000000d2cfffd9")


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def msg(seq, ctx, type_, op, meth, body=b"") -> bytes:
    """One protocol message: 32-byte LE header [seq,ctx,type,op,meth,0,0,len] + body."""
    return struct.pack("<8I", seq, ctx, type_, op, meth, 0, 0, len(body)) + body


class CvxSim:
    """Fake controller. Start it, point a CvxRemoteSession at .connect, inspect .received."""

    def __init__(self, *, host="127.0.0.1"):
        self.host = host
        self._srv: dict[int, socket.socket] = {}
        self.ports: dict[int, int] = {}          # logical 850x -> actual bound port
        self.received: dict[int, list[dict]] = {p: [] for p in PORTS}
        self._conns: dict[int, socket.socket] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._seq = 0x900

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        for p in PORTS:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, 0))           # ephemeral: never collide with a real 850x
            s.listen(1)
            self._srv[p] = s
            self.ports[p] = s.getsockname()[1]
            t = threading.Thread(target=self._serve, args=(p,), daemon=True,
                                 name=f"cvxsim-{p}")
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        for s in list(self._srv.values()) + list(self._conns.values()):
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=1.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def connect(self, ip: str, port: int):
        """Drop-in for cvx_remote._default_connect - dials the sim's real port."""
        s = socket.create_connection((self.host, self.ports[port]), timeout=5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return s

    # -- the controller side -----------------------------------------------

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _serve(self, port: int):
        srv = self._srv[port]
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        self._conns[port] = conn
        buf = bytearray()
        try:
            while not self._stop.is_set():
                data = conn.recv(65536)
                if not data:
                    break
                buf += data
                self._consume(port, conn, buf)
        except OSError:
            pass

    def _consume(self, port: int, conn: socket.socket, buf: bytearray):
        while len(buf) >= 32:
            body_len = _u32(buf, 28)
            total = 32 + body_len
            if len(buf) < total:
                return
            seq, ctx, type_, op, meth = (_u32(buf, 0), _u32(buf, 4), _u32(buf, 8),
                                         _u32(buf, 12), _u32(buf, 16))
            rec = {"port": port, "seq": seq, "ctx": ctx, "type": type_, "opcode": op,
                   "method": meth, "body": bytes(buf[32:total])}
            with self._lock:
                self.received[port].append(rec)
            del buf[:total]
            self._respond(conn, rec)

    def _respond(self, conn: socket.socket, rec: dict):
        """Answer the two things the client's replay actually waits on: an op1 ack
        after an OPEN (opcode 0), an op6 response after a REQUEST (opcode 5). Both
        carry the ctx for that service type, which is how the client learns it."""
        type_, op = rec["type"], rec["opcode"]
        ctx = _CTX_FOR_TYPE.get(type_, _NONE_CTX)
        try:
            if op == 0:
                conn.sendall(msg(self._next_seq(), ctx, type_, 1, rec["method"]))
            elif op == 5:
                conn.sendall(msg(self._next_seq(), ctx, type_, 6, rec["method"]))
        except OSError:
            pass

    # -- video --------------------------------------------------------------

    def push_frame(self, jpg: bytes = _TINY_JPEG, *, chunks: int = 2):
        """Push one JPEG down the video channel the way a controller does: N
        op7/method4 chunks then an op5/method5 end-of-frame, each body prefixed
        with the 40-byte sub-header the client strips."""
        conn = self._conns.get(VIDEO_PORT)
        if conn is None:
            raise RuntimeError("video channel not connected yet")
        ctx = _CTX_FOR_TYPE[6]
        size = max(1, -(-len(jpg) // chunks))
        parts = [jpg[i:i + size] for i in range(0, len(jpg), size)] or [b""]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            sub = bytearray(_VIDEO_SUBHDR)
            struct.pack_into("<I", sub, 12, len(part))
            struct.pack_into("<HH", sub, 16, 0 if i == 0 else 1, 1 if last else 0)
            struct.pack_into("<HH", sub, 32, 1024, 768)
            op, meth = (5, 5) if last else (7, 4)
            conn.sendall(msg(self._next_seq(), ctx, 6, op, meth, bytes(sub) + part))

    # -- assertions helpers --------------------------------------------------

    def inputs(self, method: int | None = None) -> list[dict]:
        """Every mouse/console-key input message the client sent on the control
        channel, decoded into its three payload ints.

        Size alone does not identify one - the replayed handshake also carries a
        28-byte type-7 opcode-5 body - so match the input body's signature: the
        leading handle 7 followed by the two client handles the controller echoes."""
        out = []
        with self._lock:
            msgs = list(self.received[CTRL_PORT])
        for m in msgs:
            b = m["body"]
            if m["type"] != 7 or m["opcode"] != 5 or len(b) != 28:
                continue
            if (_u32(b, 0), _u32(b, 4), _u32(b, 8)) != (7, _H1, _H2):
                continue
            if method is not None and m["method"] != method:
                continue
            out.append({"method": m["method"], "ctx": m["ctx"], "seq": m["seq"],
                        "a": _u32(b, 12), "b": _u32(b, 16), "c": _u32(b, 20)})
        return out


if __name__ == "__main__":       # hand-drive: python tests/cvx_sim.py
    import time

    with CvxSim() as sim:
        print("fake CV-X listening:", {k: v for k, v in sim.ports.items()})
        print("point a session at sim.connect; ctrl-c to stop")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
