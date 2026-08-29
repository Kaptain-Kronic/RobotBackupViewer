"""Matrox remote (the camera's web UI) - probe, DesignAssistant page scraping,
and fallback-window endpoints, exercised offline: the HTTP probe is
monkeypatched, webview is stubbed."""
import sys
import types

import backupviewer.api as api_mod
from backupviewer.api import Api
from backupviewer.parsers.mtx_portal import find_da_pages


def _probe(status=200, headers=None, final=None, body=""):
    def fake(url, timeout=4.0):
        return status, dict(headers or {}), final or url, body
    return fake


# -- find_da_pages (the operator-page scraper, parsers/mtx_portal) -----------

def test_scrape_relative_href_with_pgx_stripped():
    html = '<a href="DesignAssistant/SAMPLEPROJ_9_1_V2_0/default.htm?pgx=0.858566">op</a>'
    pages = find_da_pages("198.51.100.50", html)
    assert pages == [{
        "label": "design assistant",
        "url": "http://198.51.100.50/DesignAssistant/SAMPLEPROJ_9_1_V2_0/default.htm",
    }]


def test_scrape_window_open_and_absolute_url():
    html = "<script>window.open('http://198.51.100.50/DesignAssistant/PROJ_A/default.htm');</script>"
    pages = find_da_pages("198.51.100.50", html)
    assert len(pages) == 1
    assert pages[0]["url"].endswith("/DesignAssistant/PROJ_A/default.htm")


def test_scrape_rejects_foreign_host():
    html = '<a href="http://evil.example/DesignAssistant/X/default.htm">x</a>'
    assert find_da_pages("198.51.100.50", html) == []


def test_scrape_dedupes_and_labels_multiple_projects():
    html = ('<a href="/DesignAssistant/PROJ_A/default.htm?pgx=0.1">a</a>'
            '<a href="/DesignAssistant/PROJ_A/default.htm?pgx=0.2">a again</a>'
            '<a href="/DesignAssistant/PROJ_B/default.htm">b</a>')
    pages = find_da_pages("10.0.0.1", html)
    assert [p["label"] for p in pages] == ["PROJ_A", "PROJ_B"]


def test_scrape_keeps_non_pgx_query():
    html = '<a href="/DesignAssistant/P/default.htm?pgx=0.5&view=op">x</a>'
    pages = find_da_pages("10.0.0.1", html)
    assert pages[0]["url"] == "http://10.0.0.1/DesignAssistant/P/default.htm?view=op"


def test_scrape_empty_html():
    assert find_da_pages("10.0.0.1", "") == []
    assert find_da_pages("10.0.0.1", None) == []


def test_scrape_da9_project_row_unquoted_attr():
    """DA 9.x portals carry no literal link - the project row's (unquoted)
    prj-name attribute is the source; the URL is built the way the portal's own
    projectsTableView.js builds it. Markup captured off a live camera."""
    html = ("<tr class='project-inf-cntr bottom-row-round' "
            "prj-name=SAMPLEPROJ_9_1_V2_0>\n<td>SAMPLEPROJ_9_1_V2_0</td>")
    pages = find_da_pages("198.51.100.50", html)
    assert pages == [{
        "label": "design assistant",
        "url": "http://198.51.100.50/DesignAssistant/SAMPLEPROJ_9_1_V2_0/default.htm",
    }]


def test_scrape_da9_project_rows_quoted_and_multiple():
    html = ('<tr prj-name="PROJ_A"></tr><tr prj-name=\'PROJ_B\'></tr>'
            "<tr prj-name=PROJ_A></tr>")   # dup ignored
    pages = find_da_pages("10.0.0.1", html)
    assert [p["label"] for p in pages] == ["PROJ_A", "PROJ_B"]
    assert pages[0]["url"] == "http://10.0.0.1/DesignAssistant/PROJ_A/default.htm"


def test_scrape_literal_link_and_prj_name_dedupe():
    """A firmware that writes both the literal link and the prj-name attr must
    not produce a duplicate tab."""
    html = ('<a href="/DesignAssistant/PROJ_A/default.htm?pgx=0.3">open</a>'
            "<tr prj-name=PROJ_A></tr>")
    pages = find_da_pages("10.0.0.1", html)
    assert len(pages) == 1
    assert pages[0]["url"] == "http://10.0.0.1/DesignAssistant/PROJ_A/default.htm"


# -- mtx_remote_start (probe + pages) ----------------------------------------

def test_start_embeddable_with_scraped_pages(monkeypatch):
    html = '<a href="DesignAssistant/LINE1/default.htm?pgx=0.9">operator</a>'
    monkeypatch.setattr(api_mod, "_probe_http",
                        _probe(200, {"Content-Type": "text/html"}, body=html))
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["ok"] is True
    assert r["data"]["embeddable"] is True
    assert r["data"]["url"] == "http://10.1.2.3/"
    assert r["data"]["pages"] == [{
        "label": "design assistant",
        "url": "http://10.1.2.3/DesignAssistant/LINE1/default.htm"}]


def test_start_falls_back_to_da_root_listing(monkeypatch):
    def fake(url, timeout=4.0):
        if "DesignAssistant" in url:
            return 200, {}, url, '<a href="/DesignAssistant/P2/default.htm">p2</a>'
        return 200, {}, url, "<html>no links here</html>"
    monkeypatch.setattr(api_mod, "_probe_http", fake)
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert [p["label"] for p in r["data"]["pages"]] == ["design assistant"]


def test_start_not_embeddable_on_x_frame_options(monkeypatch):
    monkeypatch.setattr(api_mod, "_probe_http", _probe(200, {"X-Frame-Options": "DENY"}))
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["data"]["embeddable"] is False


def test_start_not_embeddable_on_csp_frame_ancestors(monkeypatch):
    monkeypatch.setattr(api_mod, "_probe_http",
                        _probe(200, {"Content-Security-Policy": "frame-ancestors 'none'"}))
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["data"]["embeddable"] is False


def test_start_follows_redirect_url(monkeypatch):
    monkeypatch.setattr(api_mod, "_probe_http",
                        _probe(200, {}, final="http://10.1.2.3/portal/"))
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["data"]["url"] == "http://10.1.2.3/portal/"


def test_start_login_page_still_counts_as_up(monkeypatch):
    monkeypatch.setattr(api_mod, "_probe_http", _probe(401, {"WWW-Authenticate": "Basic"}))
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["ok"] is True
    assert r["data"]["embeddable"] is True


def test_start_dead_socket_is_a_clean_error(monkeypatch):
    def boom(url, timeout=4.0):
        raise OSError("timed out")
    monkeypatch.setattr(api_mod, "_probe_http", boom)
    r = Api().mtx_remote_start({"ip": "10.1.2.3"})
    assert r["ok"] is False
    assert r["error"]["code"] == "MTX_CONNECT"


def test_start_rejects_bad_ip():
    r = Api().mtx_remote_start({"ip": "not-an-ip"})
    assert r["ok"] is False
    assert r["error"]["code"] == "BAD_SPEC"
    r2 = Api().mtx_remote_start({})
    assert r2["error"]["code"] == "BAD_SPEC"


# -- mtx_remote_window (the separate-window fallback) ------------------------

def _stub_webview(monkeypatch, made):
    stub = types.SimpleNamespace(
        create_window=lambda title, url, **kw: made.append((title, url)))
    monkeypatch.setitem(sys.modules, "webview", stub)


def test_window_creates_a_webview_window(monkeypatch):
    made = []
    _stub_webview(monkeypatch, made)
    r = Api().mtx_remote_window({"ip": "10.1.2.3", "label": "RB187 CAM"})
    assert r["ok"] is True
    assert made == [("MTX remote · RB187 CAM", "http://10.1.2.3/")]


def test_window_accepts_same_camera_page_url(monkeypatch):
    made = []
    _stub_webview(monkeypatch, made)
    Api().mtx_remote_window({"ip": "10.1.2.3",
                             "url": "http://10.1.2.3/DesignAssistant/P/default.htm?pgx=0.4"})
    assert made[0][1] == "http://10.1.2.3/DesignAssistant/P/default.htm?pgx=0.4"


def test_window_ignores_foreign_url(monkeypatch):
    made = []
    _stub_webview(monkeypatch, made)
    Api().mtx_remote_window({"ip": "10.1.2.3", "url": "http://evil.example/steal"})
    assert made[0][1] == "http://10.1.2.3/"


def test_window_rejects_bad_ip(monkeypatch):
    made = []
    _stub_webview(monkeypatch, made)
    r = Api().mtx_remote_window({"ip": "999.1.1.1"})
    assert r["ok"] is False
    assert made == []


# -- why is a tile dark: a 404 is not a dead camera --------------------------------

def _probe_returning(monkeypatch, *, status=None, ctype="", boom=False):
    """Stand in for _probe_http: an HTTP status, or a socket-level failure."""
    seen = []

    def fake(url, timeout=4.0, read=262144):
        seen.append((url, read))
        if boom:
            raise OSError("no route to host")
        return status, {"content-type": ctype}, url, ""
    monkeypatch.setattr(api_mod, "_probe_http", fake)
    return seen


def test_tile_probe_reads_only_the_status(monkeypatch):
    """The body is a 300 kB jpeg on a healthy camera - the probe wants the
    status, so it must not pull (and utf-8 decode) the picture."""
    seen = _probe_returning(monkeypatch, status=200, ctype="image/jpeg")
    r = Api().mtx_tile_probe({"ip": "10.1.2.3"})
    assert r["data"]["state"] == "ok"
    assert seen == [("http://10.1.2.3/SavedImages/HMIImage.jpg", 0)]


def test_tile_probe_calls_a_404_a_missing_frame_not_a_dead_camera(monkeypatch):
    """The camera answered, so it is UP. Its Design Assistant project simply
    never writes SavedImages/HMIImage.jpg - measured on a real line, 3 of 12."""
    _probe_returning(monkeypatch, status=404)
    r = Api().mtx_tile_probe({"ip": "10.1.2.3"})
    assert r["data"] == {"state": "no_image", "status": 404}


def test_tile_probe_calls_a_dead_socket_down(monkeypatch):
    _probe_returning(monkeypatch, boom=True)
    assert Api().mtx_tile_probe({"ip": "10.1.2.3"})["data"] == {"state": "down"}


def test_tile_probe_wants_an_image_not_an_error_page(monkeypatch):
    """A 200 that is HTML is a portal error page, not a frame - the <img> would
    fail to decode it, so the tile must not be told the picture is fine."""
    _probe_returning(monkeypatch, status=200, ctype="text/html; charset=utf-8")
    assert Api().mtx_tile_probe({"ip": "10.1.2.3"})["data"]["state"] == "no_image"


def test_tile_probe_rejects_bad_ip():
    assert Api().mtx_tile_probe({"ip": "999.1.1.1"})["ok"] is False
