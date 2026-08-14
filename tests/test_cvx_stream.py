"""The MJPEG bridge's flush behavior: a settled frame must paint without input.

Chromium's multipart/x-mixed-replace parser is boundary-driven - it hands part N
to the image decoder only when part N+1's delimiter arrives - so a stream that
goes byte-silent after a burst never paints its final frame (the mouse-wiggle
bug). The bridge therefore re-sends the settled JPEG once after IDLE_RESEND_S.
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


def test_idle_resend_flushes_the_settled_frame():
    """ONE frame pushed -> TWO identical parts on the wire (the frame, then the
    idle flush that lets the browser paint it) - and no third: the re-send is
    once per burst, not a loop. Pre-fix, the second part never arrives."""
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

                acc, jpgs = bytearray(), []
                _stream_jpegs(c, acc, jpgs, time.monotonic() + 3.0)

                assert len(jpgs) >= 2, (
                    f"got {len(jpgs)} part(s) - the settled frame was never "
                    "flushed; a browser would hold it un-painted until the "
                    "next frame (the mouse-wiggle bug)")
                assert jpgs[0] == jpgs[1], "the flush part must be the same frame"

                # and it is a single flush, not a re-send loop
                before = len(jpgs)
                _stream_jpegs(c, acc, jpgs, time.monotonic() + 0.6)
                assert len(jpgs) == before, "idle stream kept re-sending"
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
