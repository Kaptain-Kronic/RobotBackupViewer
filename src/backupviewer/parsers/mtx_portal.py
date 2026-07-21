"""Matrox DA portal page scraping: find the operator ("Design Assistant") pages
a camera's web portal launches, from the portal's own markup. Pure text -> dict;
the api layer owns fetching the HTML.

Two harvest paths, matching how real portals differ:
- older portals write literal /DesignAssistant/<project>/default.htm links
  (often into window.open calls) - harvest the hrefs.
- DA 9.x portals never write that link in HTML: each project row carries a
  prj-name attribute (unquoted in the wild) and projectsTableView.js does
    window.open("/DesignAssistant/" + project + "/default.htm?pgx=" + Math.random())
  - so harvest the project names and build the same URL the portal builds.
"""
from __future__ import annotations

import re
import urllib.parse

_DA_LINK_RE = re.compile(
    r"""["']((?:https?://[^"'\s]+?)?[^"'\s]*?DesignAssistant/[^"'\s]+?\.html?"""
    r"""(?:\?[^"'\s]*)?)["']""", re.IGNORECASE)
_PRJ_NAME_RE = re.compile(r"""prj-name\s*=\s*["']?([A-Za-z0-9_.\-]+)""", re.IGNORECASE)

MAX_PAGES = 8


def find_da_pages(ip: str, html: str) -> list[dict]:
    """DesignAssistant page links scraped from portal markup, absolutized and
    restricted to the camera itself (never embed a foreign host a page names).
    The portal's per-launch ?pgx= cache-buster is stripped; the viewer adds its
    own. Each: {label, url}."""
    pages, seen = [], set()
    for m in _DA_LINK_RE.finditer(html or ""):
        url = urllib.parse.urljoin(f"http://{ip}/", m.group(1))
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.hostname != ip:
            continue
        q = [kv for kv in urllib.parse.parse_qsl(parts.query) if kv[0] != "pgx"]
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), ""))
        if url in seen:
            continue
        seen.add(url)
        segs = [s for s in parts.path.split("/") if s]
        low = [s.lower() for s in segs]
        try:
            label = segs[low.index("designassistant") + 1]
            if label.lower().endswith((".htm", ".html")):   # no project folder in path
                label = "design assistant"
        except (ValueError, IndexError):
            label = "design assistant"
        pages.append({"label": label, "url": url})
        if len(pages) >= MAX_PAGES:
            break
    # DA 9.x: no literal links - build the URL from each project row's prj-name,
    # exactly as the portal's own projectsTableView.js does
    for m in _PRJ_NAME_RE.finditer(html or ""):
        if len(pages) >= MAX_PAGES:
            break
        name = m.group(1)
        url = f"http://{ip}/DesignAssistant/{name}/default.htm"
        if url in seen:
            continue
        seen.add(url)
        pages.append({"label": name, "url": url})
    if len(pages) == 1:
        pages[0]["label"] = "design assistant"
    return pages
