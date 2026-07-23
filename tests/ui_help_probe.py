"""Hidden-window probe for the ? help window (help_ui.js + help_content.js).

The load-bearing check is the honesty guard: every registered tab must have
a guide topic (topic.tab) and every topic.tab must name a real registered
tab — so the docs can never silently drift from what the app actually is.
A new tab without words, or words about a renamed tab, fails right here.

Also exercises the window itself: ? opens it, topics render and select,
the filter narrows (and esc clears it before it closes the window), doc
links hop topics / switch tabs / route-and-close, the shortcuts tab shows
the full map, and the window remembers its place for the session.

Fully synthetic and identifier-clean: empty library in a temp folder,
APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_help_probe.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# isolate settings/library under a temp APPDATA before any backupviewer import
_TMP = Path(tempfile.mkdtemp(prefix="bv_help_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def esc_key(window):
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
    time.sleep(0.2)


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.help_present", js(window, "!!BV.help && !!BV.helpUI"))
        ntopics = js(window, "BV.help.topics.length")
        check("boot.topic_count", (ntopics or 0) >= 20, f"(got {ntopics})")
        check("boot.ids_unique",
              js(window, "new Set(BV.help.topics.map(function(t){return t.id;})).size"
                         " === BV.help.topics.length"))
        check("boot.topics_shaped",
              js(window, "BV.help.topics.every(function(t){"
                         "return t.id && t.title && t.body;})"))

        # ---- the honesty guard: docs <-> tabs, both directions ----
        missing = json.loads(js(window, """JSON.stringify(
            BV.tabs.map(function(t){return t.id;}).filter(function(id){
                return !BV.help.topics.some(function(tp){return tp.tab === id;});
            }))""") or "[]")
        check("guard.every_tab_documented", not missing, f"(undocumented tabs: {missing})")
        unknown = json.loads(js(window, """JSON.stringify(
            BV.help.topics.filter(function(tp){return tp.tab;})
                .map(function(tp){return tp.tab;})
                .filter(function(id){
                    return !BV.tabs.some(function(t){return t.id === id;});
                }))""") or "[]")
        check("guard.every_topic_tab_real", not unknown, f"(phantom tabs: {unknown})")

        # ---- ? opens the window on the guide ----
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key:'?', bubbles:true}))")
        time.sleep(0.3)
        check("open.by_key", js(window, "!!document.querySelector('.modal.help-win')"))
        nrows = js(window, "document.querySelectorAll('.help-nav .opt-row[data-topic-id]').length")
        check("open.nav_rows", nrows == ntopics, f"(got {nrows} of {ntopics})")
        check("open.first_topic",
              "getting started" in (js(window, "document.querySelector('.help-doc .help-title').textContent") or ""))

        # ---- selecting a topic renders its doc ----
        js(window, "document.querySelector('.help-nav .opt-row[data-topic-id=\"compare\"]').click()")
        check("select.compare_doc",
              "from library" in (js(window, "document.querySelector('.help-doc').textContent") or ""))
        check("select.row_marked",
              js(window, "document.querySelector('.help-nav .opt-row.sel').dataset.topicId") == "compare")

        # ---- the filter narrows; esc clears it BEFORE it closes the window ----
        js(window, """(function(){
            var f = document.querySelector('.help-filter');
            f.value = 'clock drift';
            f.dispatchEvent(new Event('input', {bubbles: true}));
        })()""")
        nvis = js(window, "document.querySelectorAll('.help-nav .opt-row[data-topic-id]:not(.hidden)').length")
        check("filter.narrows", 0 < (nvis or 0) < ntopics, f"(got {nvis})")
        check("filter.finds_scan",
              js(window, "!document.querySelector('.help-nav .opt-row[data-topic-id=\"scan\"]').classList.contains('hidden')"))
        esc_key(window)
        check("filter.esc_clears_first",
              js(window, "!!document.querySelector('.modal.help-win')")
              and js(window, "document.querySelector('.help-filter').value") == "")
        nvis = js(window, "document.querySelectorAll('.help-nav .opt-row[data-topic-id]:not(.hidden)').length")
        check("filter.cleared_restores", nvis == ntopics, f"(got {nvis})")

        # ---- doc links: topic hop and tab switch ----
        js(window, "document.querySelector('.help-nav .opt-row[data-topic-id=\"start\"]').click()")
        js(window, "document.querySelector('.help-doc a[data-topic=\"compare\"]').click()")
        check("links.topic_hop",
              js(window, "document.querySelector('.help-nav .opt-row.sel').dataset.topicId") == "compare")
        js(window, "document.querySelector('.help-nav .opt-row[data-topic-id=\"keyboard\"]').click()")
        js(window, "document.querySelector('.help-doc a[data-tab=\"shortcuts\"]').click()")
        nsc = js(window, "document.querySelectorAll('.help-shortcuts .static-row').length")
        want = js(window, "BV.help.shortcuts.length")
        check("links.tab_switch", nsc == want, f"(got {nsc} of {want})")

        # ---- the window remembers its place for the session ----
        esc_key(window)
        check("close.esc", not js(window, "!!document.querySelector('.modal.help-win')"))
        js(window, "document.getElementById('btn-help').click()")
        time.sleep(0.3)
        check("memory.last_tab_shortcuts",
              js(window, "!!document.querySelector('.help-shortcuts')"))
        js(window, "document.querySelector('.help-tabs button[data-tab=\"guide\"]').click()")
        check("memory.last_topic",
              js(window, "document.querySelector('.help-nav .opt-row.sel').dataset.topicId") == "keyboard")

        # ---- a goto link closes the window and routes ----
        js(window, "document.querySelector('.help-nav .opt-row[data-topic-id=\"library\"]').click()")
        js(window, "document.querySelector('.help-doc a[data-goto]').click()")
        time.sleep(0.2)
        check("goto.closes", not js(window, "!!document.querySelector('.modal.help-win')"))
        check("goto.routes", js(window, "location.hash") == "#home",
              f"(got {js(window, 'location.hash')})")

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
    bv_settings.set_value("library_root", str(lib))

    api = Api()
    window = webview.create_window(
        "probe",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1280,
        height=860,
        hidden=True,
    )
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
