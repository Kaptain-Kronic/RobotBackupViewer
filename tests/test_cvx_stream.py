"""The frame bridge's two routes: the overlay's stream, and a tile's still.

The MJPEG flush behavior: a settled frame must paint without input.

Chromium's multipart/x-mixed-replace parser is boundary-driven - it hands part N
to the image decoder only when part N's delimiter arrives - so a stream that
writes that delimiter lazily, as the start of part N+1, never paints its final
frame when it goes byte-silent (the mouse-wiggle bug). The bridge therefore
ships the closing boundary WITH its part, and repeats the newest frame about
once a second while idle, so the picture cannot sit stale behind client-side
buffering and the connection stays provably alive.
These tests drive a real CvxRemoteSession against the loopback sim and read the
actual HTTP stream a browser would."""
import socket
import threading
import time

from cvx_sim import CvxSim

from backupviewer import cvx_remote as cx


def _settle(sess, timeout=5.0):
    """Wait until the handshake replay finished and the video ctx is learned."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if sess.handshake_done and cx.VIDEO_TYPE in sess._ctx:
            return
        time.sleep(0.01)
    raise AssertionError(f"handshake never settled (error={sess.error!r})")


def _stream_jpegs(conn, acc, found, deadline):
    """Read the multipart stream until `deadline`, harvesting complete JPEGs.
    extract_jpegs consumes what it scans, so `acc` keeps only the unfinished
    tail; the ASCII part headers can't contain SOI/EOI and are consumed along
    with each frame."""
    conn.settimeout(0.1)
    while time.monotonic() < deadline:
        try:
            data = conn.recv(65536)
        except socket.timeout:
            continue
        if not data:
            break
        acc += data
        found.extend(cx.extract_jpegs(acc))


def test_settled_frame_paints_and_then_heartbeats():
    """ONE frame pushed -> a COMPLETE part on the wire straight away (closing
    boundary and all, so a browser paints it with no further input), and then
    the same frame again on the idle heartbeat. Pre-fix the delimiter waited
    for the next frame and the picture hung until the user wiggled the mouse."""
    with CvxSim() as sim:
        sess = cx.CvxRemoteSession("192.0.2.44", connect=sim.connect)
        assert sess.start(), sess.error
        try:
            _settle(sess)
            srv = cx.start_frame_server({"t1": sess})
            try:
                c = socket.create_connection(
                    ("127.0.0.1", srv.server_address[1]), timeout=5)
                c.sendall(b"GET /cvx/t1 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                time.sleep(0.1)          # let the handler enter its stream loop
                sim.push_frame()         # the ONLY frame this test ever pushes

                # well under the ~1s heartbeat: whatever arrives in this window
                # is the eager part, not a repeat
                acc, jpgs = bytearray(), []
                _stream_jpegs(c, acc, jpgs, time.monotonic() + 0.4)
                assert len(jpgs) >= 1, (
                    "the pushed frame never arrived as a complete part - a "
                    "browser would hold it un-painted until the next frame "
                    "(the mouse-wiggle bug)")

                # and the stream keeps itself alive: the same frame repeats
                before = len(jpgs)
                _stream_jpegs(c, acc, jpgs, time.monotonic() + 2.5)
                assert len(jpgs) > before, (
                    "idle stream never repeated the frame - no heartbeat, so a "
                    "dead connection looks exactly like a quiet controller")
                assert jpgs[0] == jpgs[-1], "the heartbeat must be the same frame"
                c.close()
            finally:
                srv.shutdown()
        finally:
            sess.stop()


def test_wait_frame_wakes_on_frame_and_times_out_idle():
    with CvxSim() as sim:
        sess = cx.CvxRemoteSession("192.0.2.44", connect=sim.connect)
        assert sess.start(), sess.error
        try:
            _settle(sess)
            base = sess.frames
            assert sess.wait_frame(base, 0.05) is False   # quiet stream: timeout
            threading.Timer(0.05, sim.push_frame).start()
            assert sess.wait_frame(base, 2.0) is True     # a frame wakes it
            assert sess.wait_frame(base, 0.0) is True     # already-newer: no wait
        finally:
            sess.stop()


def test_stop_wakes_blocked_stream_consumers():
    """stop() must notify waiters, or reaped sessions strand MJPEG handler
    threads until their full timeout."""
    with CvxSim() as sim:
        sess = cx.CvxRemoteSession("192.0.2.44", connect=sim.connect)
        assert sess.start(), sess.error
        try:
            _settle(sess)
            woke = {}

            def waiter():
                t0 = time.monotonic()
                sess.wait_frame(sess.frames, 5.0)
                woke["dt"] = time.monotonic() - t0

            t = threading.Thread(target=waiter, daemon=True)
            t.start()
            time.sleep(0.1)
            sess.stop()
            t.join(2.0)
            assert woke.get("dt") is not None and woke["dt"] < 2.0
        finally:
            sess.stop()


def test_mjpeg_handler_disables_nagle():
    # the literal is the point: with Nagle on, a part's tail bytes can sit out
    # a delayed-ACK window in the kernel - the stream must never nagle
    assert cx._MjpegHandler.disable_nagle_algorithm is True


# -- the still route: what a cam-lens tile asks for ---------------------------------

def _http_get(port, path, timeout=5.0):
    """One request, read to EOF - exactly what an <img> does with a finite
    response. Returns (status_line, headers_dict, body)."""
    c = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        c.sendall(("GET " + path + " HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").encode())
        buf = bytearray()
        c.settimeout(timeout)
        while True:
            try:
                data = c.recv(65536)
            except socket.timeout:
                break
            if not data:
                break            # the server closed: a FINITE response
            buf += data
    finally:
        c.close()
    head, _, body = bytes(buf).partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdrs[k.strip().lower()] = v.strip()
    return lines[0], hdrs, body


def test_shot_returns_one_finite_frame():
    """A tile's still must END. A wall cannot be built out of streams: a
    multipart response never completes, so each streaming tile holds one of the
    browser's six-per-origin connections open and every tile past the sixth
    never connects at all. This response closes, so the socket comes back."""
    with CvxSim() as sim:
        sess = cx.CvxRemoteSession("192.0.2.44", connect=sim.connect)
        assert sess.start(), sess.error
        try:
            _settle(sess)
            sim.push_frame()
            t0 = time.monotonic()
            while sess.frames == 0 and time.monotonic() - t0 < 5:
                time.sleep(0.01)
            assert sess.frames, "the sim's frame never reached the session"

            srv = cx.start_frame_server({"t1": sess})
            try:
                port = srv.server_address[1]
                status, hdrs, body = _http_get(port, cx.SHOT_PATH + "t1")
                assert "200" in status, status
                assert hdrs["content-type"] == "image/jpeg", hdrs
                assert int(hdrs["content-length"]) == len(body), (hdrs, len(body))
                assert body.startswith(b"\xff\xd8") and body.endswith(b"\xff\xd9")
                assert body == sess.latest_frame()

                # and the route is still addressed by session, like the stream
                status, _, _ = _http_get(port, cx.SHOT_PATH + "nosuch")
                assert "404" in status, status
            finally:
                srv.shutdown()
        finally:
            sess.stop()


def test_shot_404s_before_the_first_frame():
    """Alive but nothing pushed yet is NOT a dark camera. The 404 is what lets
    the tile say "connected - no picture yet" instead of "not answering"."""
    with CvxSim() as sim:
        sess = cx.CvxRemoteSession("192.0.2.44", connect=sim.connect)
        assert sess.start(), sess.error
        try:
            _settle(sess)
            assert sess.frames == 0, "this test needs a camera that stayed quiet"
            srv = cx.start_frame_server({"t1": sess})
            try:
                status, _, _ = _http_get(srv.server_address[1], cx.SHOT_PATH + "t1")
                assert "404" in status, status
            finally:
                srv.shutdown()
        finally:
            sess.stop()


def test_the_two_routes_do_not_collide():
    """/cvxshot/<sid> must not be swallowed by the /cvx/<sid> prefix - they
    differ only in the segment before the id."""
    assert cx.SHOT_PATH.startswith("/cvx")
    assert not cx.SHOT_PATH.startswith(cx.STREAM_PATH)
    assert (cx.SHOT_PATH + "abc").rsplit("/", 1)[-1] == "abc"
    assert (cx.STREAM_PATH + "abc").rsplit("/", 1)[-1] == "abc"
