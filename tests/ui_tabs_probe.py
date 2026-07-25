"""Hidden-window probe for the browser-style backup tabs + the solo pop-out.

Covers what pytest can't: the real strip DOM (open two robots, switch,
dedupe, per-backup UI memory across switches, ✕ close, sessions-released),
library fold persistence landing in settings.json, and the WebView2 facts
the pop-out design leans on - a SECOND pywebview window created after
start() with js_api on the same Api, booting index.html?sid=... into solo
mode where every content call routes to the pinned session.

Fully synthetic and identifier-clean: RB fakes under FakePlant in a temp
library, APPDATA redirected before any backupviewer import.
Run: python tests/ui_tabs_probe.py
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

_TMP = Path(tempfile.mkdtemp(prefix="bv_tabs_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []
ROBOTS = ["RB010R01B01", "RB020R01B01", "RB030R01B01"]


def build_tree(lib: Path) -> None:
    line = lib / "FakePlant" / "LINE01"
    for rb in ROBOTS:
        snap = line / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / "SUMMARY.DG").write_text(f"Robot: {rb}\n", encoding="utf-8")
        (snap / "backup.json").write_text(
            json.dumps({"robot": rb, "line": "LINE01", "plant": "FakePlant",
                        "taken": "2026-01-01T12:00:00", "complete": True}),
            encoding="utf-8")


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=24, delay=0.25):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def open_robot(window, name, prev_sid=""):
    """Click the library row like a user (runs the dedupe + session funnel).
    Waits for the row to exist first (goHome re-renders), then for the
    manifest to actually CHANGE - polling for any truthy sid would race and
    happily return the still-open previous backup's."""
    clicked = poll(window, """(function(){
        var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
            return r.textContent.indexOf('%s')>=0;});
        if (!row) return '';
        row.click();
        return 'y';
    })()""" % name)
    if clicked != "y":
        return ""
    return poll(window,
                "BV.state.manifest && BV.state.manifest.sid && BV.state.manifest.sid !== %s"
                " ? BV.state.manifest.sid : ''" % json.dumps(prev_sid))


def probe(window):
    try:
        time.sleep(4)  # boot

        # ---- home: strip exists, empty + hidden ----
        nrows = poll(window, "document.querySelectorAll('.lib-robot').length")
        check("home.rows", nrows == len(ROBOTS), f"(got {nrows})")
        check("strip.hidden_when_empty",
              js(window, "document.getElementById('sessionbar').classList.contains('hidden')"))

        # ---- open two robots -> two tabs, second active ----
        sid1 = open_robot(window, ROBOTS[0])
        check("open.first", bool(sid1), f"(sid={sid1!r})")
        js(window, "BV.goHome()")
        time.sleep(0.4)
        sid2 = open_robot(window, ROBOTS[1], prev_sid=sid1)
        check("open.second", bool(sid2) and sid2 != sid1)
        check("strip.two_tabs",
              js(window, "document.querySelectorAll('#sessionbar .stab').length") == 2)
        check("strip.active_is_second",
              js(window, "(document.querySelector('#sessionbar .stab.active')||{}).dataset ? document.querySelector('#sessionbar .stab.active').dataset.sid : ''") == sid2)

        # ---- per-backup UI memory survives a tab switch ----
        js(window, "BV.tabState('probe').mark = 'first-was-here'")  # lives in sid2's bucket
        js(window, """(function(){
            var t=[...document.querySelectorAll('#sessionbar .stab')].find(function(x){
                return x.dataset.sid===%s;});
            t.click();
        })()""" % json.dumps(sid1))
        got = poll(window, "BV.state.manifest && BV.state.manifest.sid === %s ? 'y' : ''" % json.dumps(sid1))
        check("switch.manifest_follows", got == "y")
        check("switch.fresh_bucket", not js(window, "!!BV.tabState('probe').mark"))
        js(window, """(function(){
            var t=[...document.querySelectorAll('#sessionbar .stab')].find(function(x){
                return x.dataset.sid===%s;});
            t.click();
        })()""" % json.dumps(sid2))
        poll(window, "BV.state.manifest && BV.state.manifest.sid === %s ? 'y' : ''" % json.dumps(sid2))
        check("switch.bucket_restored",
              js(window, "BV.tabState('probe').mark") == "first-was-here")

        # ---- dedupe: re-opening an open robot focuses, never duplicates ----
        js(window, "BV.goHome()")
        time.sleep(0.4)
        open_robot(window, ROBOTS[0])
        check("dedupe.no_third_tab",
              js(window, "document.querySelectorAll('#sessionbar .stab').length") == 2)
        check("dedupe.focused_existing",
              js(window, "BV.state.manifest.sid") == sid1)

        # ---- library folds persist to settings.json ----
        js(window, "BV.goHome()")
        time.sleep(0.4)
        js(window, """(function(){
            var h=document.querySelector('.lib-plant-h');  /* fold FakePlant */
            if (h) h.click();
        })()""")
        deadline = time.time() + 4
        folds = None
        while time.time() < deadline:
            folds = bv_settings.load().get("lib_folds")
            if folds and any(v is False for v in folds.values()):
                break
            time.sleep(0.25)
        check("folds.persisted", bool(folds) and any(v is False for v in folds.values()),
              f"(got {folds})")

        # ---- sessions-released closes tabs quietly ----
        js(window, "BV.state.emit('sessions-released', [%s])" % json.dumps(sid2))
        time.sleep(0.4)
        check("released.tab_gone",
              js(window, "document.querySelectorAll('#sessionbar .stab').length") == 1)

        # ---- pop-out: the second-window spike, for real. Through the
        # FRONTEND path (BV.session.popOut) so the tab-transfer runs too. ----
        api = window._bv_api
        js(window, "BV.session.popOut(%s)" % json.dumps(sid1))
        deadline = time.time() + 6
        owner = None
        while time.time() < deadline:
            listed = api.list_open_sessions()["data"]
            owner = next((x["owner"] for x in listed if x["sid"] == sid1), None)
            if owner == "popout":
                break
            time.sleep(0.3)
        check("popout.endpoint_ok", owner == "popout", f"(owner={owner})")
        deadline = time.time() + 10
        while time.time() < deadline and len(webview.windows) < 2:
            time.sleep(0.3)
        check("popout.window_exists", len(webview.windows) >= 2,
              f"(windows={len(webview.windows)})")
        if len(webview.windows) >= 2:
            w2 = webview.windows[1]
            booted = None
            for _ in range(40):  # the new window boots its own JS world
                try:
                    booted = w2.evaluate_js("window.BV && BV.solo ? BV.soloSid : ''")
                except Exception:
                    booted = None
                if booted:
                    break
                time.sleep(0.4)
            check("popout.solo_boot", booted == sid1, f"(got {booted!r})")
            check("popout.solo_flagged", bool(w2.evaluate_js("BV.solo === true")))
            got = poll(w2, "BV.state.manifest && BV.state.manifest.sid ? BV.state.manifest.sid : ''")
            check("popout.pinned_manifest", got == sid1, f"(got {got!r})")
            check("popout.chrome_hidden",
                  w2.evaluate_js("""getComputedStyle(document.getElementById('sessionbar')).display === 'none'
                    && getComputedStyle(document.getElementById('btn-compare')).display === 'none'"""))
            # a pop-out shows the WORDMARK where the main window shows cubes -
            # the swap is the deliberate "which window is the main one?" tell
            check("popout.wordmark_instead_of_cubes",
                  w2.evaluate_js("""getComputedStyle(document.getElementById('topbar-cubes')).display === 'none'
                    && getComputedStyle(document.getElementById('logo')).display !== 'none'"""))
            # the SID_POS shim end-to-end: content calls resolve the pinned
            # session even though the MAIN window's active sid is different.
            # (evaluate_js can't await a promise - park the result on window)
            w2.evaluate_js("""window._probeOv = '';
                BV.api.call('get_overview').then(function(){ window._probeOv = 'ok'; },
                    function(e){ window._probeOv = 'err:' + e.code; })""")
            ov = poll(w2, "window._probeOv")
            check("popout.sid_injection", ov in ("ok", "err:MISSING_FILE"), f"(got {ov!r})")
            # main window: the popped tab left the strip; session still listed as popout
            check("popout.main_tab_left",
                  js(window, "document.querySelectorAll('#sessionbar .stab').length") == 0)
            listed = api.list_open_sessions()["data"]
            check("popout.owner_marked",
                  any(x["sid"] == sid1 and x["owner"] == "popout" for x in listed),
                  f"({listed})")
            # closing the pop-out DROPS the session
            w2.destroy()
            deadline = time.time() + 6
            gone = False
            while time.time() < deadline:
                if not any(x["sid"] == sid1 for x in api.list_open_sessions()["data"]):
                    gone = True
                    break
                time.sleep(0.3)
            check("popout.close_drops_session", gone)

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass


def main():
    lib = _TMP / "lib"
    build_tree(lib)
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
    window._bv_api = api
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
