"""Hidden-window probe for the background effects (bgfx.js) + the 🎨 theme
window (theme_ui.js).

Exercises the part the probe environment is uniquely good at: this window
has NO requestAnimationFrame, which is exactly the environment bgfx must
survive (build every effect, never throw, settle for a static frame). Also
checks the layer lifecycle (canvas/css created and torn down per effect),
the theme window's tabs/sliders/effect menu, the themes tab's
restore-on-close contract, that tuning and effect choices land in
settings.json, and that ⚙ settings kept only behavior rows.

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
        # paint-order regression guard: the layers sit at z-index -1, which is
        # above the ROOT background but below in-flow backgrounds — if body
        # ever paints opaque again, every effect draws invisibly behind it
        check("boot.body_transparent",
              js(window, "getComputedStyle(document.body).backgroundColor")
              in ("rgba(0, 0, 0, 0)", "transparent"))
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

        # ---- theme window: customize tab sliders, effect menu, credit ----
        check("theme.btn_present", js(window, "!!document.getElementById('btn-theme')"))
        js(window, "BV.bgfx.set('rain', false)")
        js(window, "BV.themeUI.open('customize')")
        nsliders = poll(window, "document.querySelectorAll('.modal.theme-win input[type=range]').length")
        check("theme.sliders", nsliders == 2, f"(got {nsliders})")
        check("theme.fx_button_names_current",
              (js(window, "document.querySelector('.modal .btn.fx-pick').textContent") or "").startswith("rain"))
        check("theme.credit_line",
              js(window, "[...document.querySelectorAll('.modal .acc-credit')].some(function(c){return c.textContent.indexOf('odysseus') >= 0;})"))

        # the intensity slider drives the live value and persists (debounced)
        js(window, """(function(){
            var r = document.querySelector('.modal.theme-win input[type=range]');
            r.value = '40';
            r.dispatchEvent(new Event('input', {bubbles: true}));
        })()""")
        got_i = js(window, "BV.bgfx.intensity")
        check("theme.intensity_live", abs((got_i or 0) - 0.4) < 1e-6, f"(got {got_i})")
        deadline = time.time() + 4
        saved_i = None
        while time.time() < deadline:
            saved_i = bv_settings.load().get("bgfx_intensity")
            if saved_i == 0.4:
                break
            time.sleep(0.25)
        check("theme.intensity_persists", saved_i == 0.4, f"(got {saved_i})")

        # the effect dropdown lists every effect incl. off
        js(window, "document.querySelector('.modal .btn.fx-pick').click()")
        nitems = poll(window, "document.querySelectorAll('.ctx-menu .ctx-item').length")
        check("theme.fx_menu_items", nitems == 13, f"(got {nitems})")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")
        time.sleep(0.3)

        # ---- themes tab: rows render; a hover preview un-does itself on close ----
        js(window, "BV.themeUI.open('themes')")
        nrows = poll(window, "document.querySelectorAll('.opt-row[data-theme-id]').length")
        check("theme.rows", (nrows or 0) >= 20, f"(got {nrows})")
        committed = js(window, "BV.theme.activeId")
        js(window, """(function(){
            var other = BV.theme.themes.find(function(t){ return t.id !== BV.theme.activeId; });
            BV.theme.applyById(other.id, false);   /* hover-preview equivalent */
        })()""")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")
        time.sleep(0.3)
        check("theme.esc_restores", js(window, "BV.theme.activeId") == committed,
              f"(got {js(window, 'BV.theme.activeId')})")

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

        # ---- ⚙ settings kept behavior-only; appearance moved to the window ----
        js(window, "BV.bgfx.set('none', false); BV.uiPrefs.modal()")
        got = poll(window, """(function(){
            var rows = [...document.querySelectorAll('.modal .set-row')]
                .map(function(x){ return x.querySelector('.name').textContent; });
            return rows.length ? JSON.stringify(rows) : '';
        })()""")
        rows = json.loads(got or "[]")
        check("settings.behavior_only",
              "library folder" in rows and "invert rotate x" in rows
              and not any(r in rows for r in ("theme", "background", "font", "text size", "chrome scale")),
              f"({rows})")
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
