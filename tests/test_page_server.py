"""pywebview's page server must take every script index.html asks for at once.

Its stdlib listen backlog is five, and Windows REFUSES a connect that finds the
backlog full. WebView2 opens the page's scripts fast enough to overflow that on
most boots (runtime 153 on Jake's machine, 09-17; five boots in six on Cody's a
week later), so the app came up half-built: no library, no saved settings, a
settings dialog dead at its first row. The hidden-window probes load too gently
to see it - this test is the only thing that does. No GUI: it drives pywebview's
own server adapter and a real listening socket, accepting nothing, so the
kernel backlog is the only thing on trial. The five-deep control is the
did-we-lose-one anchor: without it a green burst would prove nothing."""
import re
import selectors
import socket
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from webview import http as wv_http

from backupviewer import app


def _script_count():
    html = app.resource_path("web/index.html").read_text(encoding="utf-8")
    return len(re.findall(r"<script\b", html))


def _server_class_pywebview_builds(monkeypatch):
    """Run pywebview's real ThreadedAdapter.run() with make_server stubbed, and
    return the server class it would have listened with."""
    built = {}

    class _Idle:
        def serve_forever(self):
            pass

    def fake_make_server(host, port, handler, server_class, **_kw):
        built["cls"] = server_class
        return _Idle()

    monkeypatch.setattr(wv_http, "make_server", fake_make_server)
    adapter = wv_http.ThreadedAdapter(host="127.0.0.1", port=0)
    adapter.quiet = True
    adapter.run(lambda environ, start_response: [])
    return built["cls"]


def _refused_in_a_burst(server_class, n):
    """Listen with server_class, accept NOTHING, fire n non-blocking connects
    at once; return how many never completed."""
    srv = make_server("127.0.0.1", 0, lambda e, s: [], server_class=server_class)
    port = srv.server_address[1]
    sel = selectors.DefaultSelector()
    socks = []
    try:
        for _ in range(n):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            s.connect_ex(("127.0.0.1", port))
            sel.register(s, selectors.EVENT_WRITE)
            socks.append(s)
        done, pending = set(), set(socks)
        while pending:
            events = sel.select(timeout=3)
            if not events:
                break
            for key, _ in events:
                done.add(key.fileobj)
                pending.discard(key.fileobj)
                sel.unregister(key.fileobj)
        ok = sum(1 for s in done if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
        return n - ok
    finally:
        sel.close()
        for s in socks:
            s.close()
        srv.server_close()


def test_page_server_takes_every_script_at_once(monkeypatch):
    """The class pywebview really builds, after app.py's widening, swallows the
    whole burst - the widening lands on the stdlib base it inherits from."""
    app._widen_server_backlog()
    cls = _server_class_pywebview_builds(monkeypatch)
    n = _script_count()
    assert n > 5, "index.html should load more scripts than the stdlib backlog"
    assert _refused_in_a_burst(cls, n) == 0


def test_the_stdlib_default_refuses_the_burst():
    """The control: a five-deep backlog refuses most of the same burst. This is
    the kernel behaviour the widening exists for; it must stay red so the
    green above is worth something."""
    class _FiveDeep(ThreadingMixIn, WSGIServer):
        request_queue_size = 5    # the stdlib default, pinned so the widening cannot leak in

    n = _script_count()
    assert _refused_in_a_burst(_FiveDeep, n) > 0
