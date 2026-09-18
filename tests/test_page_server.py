"""pywebview's page server must take every script index.html asks for at once.

Its stdlib listen backlog is five, and Windows REFUSES a connect that finds the
backlog full. WebView2 runtime 153 opens the page's scripts fast enough to
overflow that on nearly every boot, so the app came up half-built (no library,
no saved settings). The hidden-window probes load too gently to see it - this
test is the only thing that does. No GUI: it drives pywebview's own server
adapter and a real listening socket, accepting nothing, so the kernel backlog
is the only thing on trial."""
import re
import selectors
import socket
from wsgiref.simple_server import make_server

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
    monkeypatch.setattr(wv_http, "WSGIServer", wv_http.WSGIServer)   # restored after
    assert app._deepen_page_server_backlog() is True
    cls = _server_class_pywebview_builds(monkeypatch)
    n = _script_count()
    assert n > 5, "index.html should load more scripts than the stdlib backlog"
    assert _refused_in_a_burst(cls, n) == 0


def test_deepen_is_idempotent(monkeypatch):
    monkeypatch.setattr(wv_http, "WSGIServer", wv_http.WSGIServer)
    assert app._deepen_page_server_backlog() is True
    first = wv_http.WSGIServer
    assert app._deepen_page_server_backlog() is True
    assert wv_http.WSGIServer is first


def test_deepen_reports_a_moved_seam(monkeypatch, caplog):
    monkeypatch.delattr(wv_http, "WSGIServer")
    with caplog.at_level("WARNING", logger="backupviewer.app"):
        assert app._deepen_page_server_backlog() is False
    assert "NOT deepened" in caplog.text
