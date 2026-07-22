"""Hidden-window probe for the background-effects picker (bgfx.js).

Exercises the part the probe environment is uniquely good at: this window
has NO requestAnimationFrame, which is exactly the environment bgfx must
survive (build every effect, never throw, settle for a static frame). Also
checks the layer lifecycle (canvas/css created and torn down per effect),
the picker modal, the restore-on-cancel contract, and that a committed
choice lands in settings.json.

Fully synthetic and identifier-clean: empty library in a temp folder,
APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_bgfx_probe.py
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
_TMP = Path(tempfile.mkdtemp(prefix="bv_bgfx_probe_"))
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


def poll(window, expr, tries=24, delay=0.25):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.bgfx_present", js(window, "!!BV.bgfx"))
        check("boot.effect_count", js(window, "BV.bgfx.EFFECTS.length") == 13,
              f"(got {js(window, 'BV.bgfx.EFFECTS.length')})")
        check("boot.defaults_off", js(window, "BV.bgfx.activeId") == "none")
        check("boot.no_layers",
              not js(window, "!!document.getElementById('bgfx-canvas') || !!document.getElementById('bgfx-css')"))

        # ---- every effect builds without rAF and stands up the right layers ----
        ids = json.loads(js(
            window, "JSON.stringify(BV.bgfx.EFFECTS.map(function(e){return e.id;}))") or "[]")
        for fx in ids:
            if fx == "none":
                continue
            res = js(window, f"""(function(){{
                try {{
                    BV.bgfx.set('{fx}', false);
                    var canvas = document.getElementById('bgfx-canvas');
                    var css = document.getElementById('bgfx-css');
                    var t = BV.bgfx.EFFECTS.find(function(e){{return e.id==='{fx}';}});
                    return JSON.stringify({{
                        canvas: !!canvas, cls: css ? css.className : null,
                        sized: !canvas || (canvas.width > 0 && canvas.height > 0),
                        wantCanvas: !!t.make, wantCls: t.cls || "",
                    }});
                }} catch (e) {{ return JSON.stringify({{err: String(e)}}); }}
            }})()""")
            res = json.loads(res or "{}")
            check(f"fx.{fx}.no_throw", "err" not in res, f"({res.get('err')})")
            if "err" in res:
                continue
            check(f"fx.{fx}.canvas_matches", res["canvas"] == res["wantCanvas"], f"({res})")
            check(f"fx.{fx}.css_class", (res["cls"] or "") == res["wantCls"], f"({res})")
            check(f"fx.{fx}.canvas_sized", res["sized"])

        # ---- back to none: both layers torn down ----
        js(window, "BV.bgfx.set('none', false)")
        check("teardown.layers_gone",
              not js(window, "!!document.getElementById('bgfx-canvas') || !!document.getElementById('bgfx-css')"))

        # ---- picker: rows, credits, esc restores the un-committed state ----
        js(window, "BV.bgfx.set('rain', false)")
        js(window, "BV.bgfx.picker()")
        nrows = poll(window, "document.querySelectorAll('.opt-row[data-fx-id]').length")
        check("picker.rows", nrows == 13, f"(got {nrows})")
        check("picker.credit_line",
              js(window, "[...document.querySelectorAll('.acc-credit')].some(function(c){return c.textContent.indexOf('odysseus') >= 0;})"))
        js(window, "BV.bgfx.set('embers', false)")   # hover-preview equivalent
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")
        time.sleep(0.3)
        check("picker.esc_restores", js(window, "BV.bgfx.activeId") == "rain",
              f"(got {js(window, 'BV.bgfx.activeId')})")

        # ---- a committed choice persists through the settings round-trip ----
        js(window, "BV.bgfx.set('constellations', true)")
        deadline = time.time() + 4
        saved = None
        while time.time() < deadline:
            saved = bv_settings.load().get("bgfx")
            if saved == "constellations":
                break
            time.sleep(0.25)
        check("persist.settings_json", saved == "constellations", f"(got {saved})")

        # ---- settings modal shows the row ----
        js(window, "BV.bgfx.set('none', false); BV.uiPrefs.modal()")
        row = poll(window, """(function(){
            var rows=[...document.querySelectorAll('.modal .set-row')];
            var r=rows.find(function(x){return x.textContent.indexOf('background')>=0;});
            return r ? 'y' : '';
        })()""")
        check("settings.background_row", row == "y")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")

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
