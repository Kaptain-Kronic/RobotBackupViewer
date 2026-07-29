"""Hidden-window probe for the background effects (bgfx.js) + the ⚙ settings
dialog (settings_ui.js) and its theme picker (theme_ui.js).

Exercises the part the probe environment is uniquely good at: this window
has NO requestAnimationFrame, which is exactly the environment bgfx must
survive (build every effect, never throw, settle for a static frame). Also
checks the layer lifecycle (canvas/css created and torn down per effect),
the ONE dialog's two tabs and their row sets, the theme picker panel
(flat list, Custom first, hover-off ends the preview, Esc closes the panel
and not the dialog), that the two scale sliders commit on RELEASE, and that
tuning and effect choices land in settings.json.

NOT covered yet (noted, not built - see docs/proposals/ if this gets picked up):
  * the six `simulations` effects get the generic build/no-throw/layer checks
    for free (the fx.* loop reads EFFECTS live), but nothing asserts their
    emergent behaviour - a boids flock that never flocks still passes.
  * the per-effect param racks: nothing switches to an effect that HAS a
    `params` rack and asserts the .fx-own section appears, that its rows match
    that effect's spec, that a slider writes bgfx_params.<id>.<k> to settings,
    that `live:false` params re-seed while `live:true` ones apply in flight,
    or that switching effects swaps the rack instead of stacking it.
  * real `dt`: the frame-count->wall-clock fix is what stopped 144Hz running
    everything 2.4x fast, and it is untestable here - this window has no
    requestAnimationFrame at all, so no frames ever advance. Covering it needs
    a step() the effects can be driven through with an injected dt, which is a
    bgfx.js change, not a probe change.

Fully synthetic and identifier-clean: empty library in a temp folder,
APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_bgfx_probe.py
"""
import json
import sys
import time
from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_bgfx_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.bgfx_present", js(window, "!!BV.bgfx"))
        # 19 = off + 5 house + 7 odysseus + 6 simulations. A literal on purpose:
        # this is the "did the table quietly lose an effect" anchor, so it has
        # to be updated deliberately when one is added or retired.
        check("boot.effect_count", js(window, "BV.bgfx.EFFECTS.length") == 19,
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

        # ---- ONE dialog, two tabs; 🎨 is gone ----
        check("settings.one_button",
              js(window, "!!document.getElementById('btn-cog') && !document.getElementById('btn-theme')"))
        js(window, "BV.bgfx.set('rain', false)")
        js(window, "BV.uiPrefs.modal()")
        got = poll(window, """(function(){
            var m = document.querySelector('#modal-root .modal');
            if (!m) return '';
            return JSON.stringify({
                win: m.classList.contains('settings-win'),
                tabs: [...m.querySelectorAll('.set-tabs button')].map(function(b){return b.textContent;}),
                heads: [...m.querySelectorAll('.set-head')].map(function(h){return h.textContent;}),
                rows: [...m.querySelectorAll('.set-row .name')].map(function(n){return n.textContent;}),
            });
        })()""")
        disp = json.loads(got or "{}")
        check("settings.win_class", disp.get("win") is True)
        check("settings.tabs", disp.get("tabs") == ["display", "preferences"], f"({disp.get('tabs')})")
        check("settings.display_sections",
              disp.get("heads") == ["theme", "interface", "background"], f"({disp.get('heads')})")
        # opacity + frost belong with the interface knobs, not the effect sliders.
        # The background block ends with the six GLOBAL dials; an effect's own
        # dials render into the separate .fx-own host under their own heading,
        # and `rain` (set above) has no rack - so this list is the globals only.
        check("settings.display_rows",
              disp.get("rows") == ["theme", "font", "borders", "text size", "toolbar size",
                                   "panel opacity", "frost", "effect", "intensity", "size",
                                   "speed", "density", "variance", "hue drift"],
              f"({disp.get('rows')})")
        # the theme row: ＋ FIRST, then the picker, tight and right-aligned with
        # the control column above it
        got = js(window, """(function(){
            var m = document.querySelector('#modal-root .modal');
            var g = m.querySelector('.set-theme');
            var k = [...g.children];
            var seg = [...m.querySelectorAll('.set-row')].find(function(r){
                return r.querySelector('.name').textContent === 'font'; }).querySelector('.seg');
            return JSON.stringify({
                order: k.map(function(e){ return e.classList.contains('theme-new') ? '+' : 'pick'; }),
                gap: Math.round(k[1].getBoundingClientRect().left - k[0].getBoundingClientRect().right),
                flush: Math.abs(k[1].getBoundingClientRect().right - seg.getBoundingClientRect().right) <= 1,
            });
        })()""")
        trow = json.loads(got or "{}")
        check("settings.theme_row_order", trow.get("order") == ["+", "pick"], f"({trow.get('order')})")
        check("settings.theme_row_tight", 0 <= (trow.get("gap") or 99) <= 8, f"({trow.get('gap')})")
        check("settings.theme_row_flush", trow.get("flush") is True)

        check("settings.fx_button_names_current",
              (js(window, "document.querySelector('.modal .btn.fx-pick').textContent") or "").startswith("rain"))
        check("settings.credit_line",
              js(window, "[...document.querySelectorAll('.modal .acc-credit')].some(function(c){return c.textContent.indexOf('odysseus') >= 0;})"))

        # find a slider by its ROW LABEL, not its index — the display tab has ten
        # and a reordering must not silently retarget these checks
        def slider(label, value, event="input"):
            js(window, f"""(function(){{
                var r = [...document.querySelectorAll('#modal-root .set-row')].find(function(x){{
                    return x.querySelector('.name').textContent === '{label}'; }});
                var i = r.querySelector('input[type=range]');
                i.value = '{value}';
                i.dispatchEvent(new Event('{event}', {{bubbles: true}}));
            }})()""")

        # 4 interface (text size, toolbar size, panel opacity, frost) + the 6
        # global background dials. `rain` contributes none of its own.
        nsliders = js(window, "document.querySelectorAll('.modal.settings-win input[type=range]').length")
        check("settings.display_sliders", nsliders == 10, f"(got {nsliders})")

        # the intensity slider drives the live value and persists (debounced)
        slider("intensity", 40)
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

        # the opacity slider drives the --panel fill and persists
        slider("panel opacity", 40)
        check("theme.opacity_live",
              "0.760" in (js(window, "document.documentElement.style.getPropertyValue('--panel')") or ""))
        deadline = time.time() + 4
        saved_o = None
        while time.time() < deadline:
            saved_o = bv_settings.load().get("glass_op")
            if saved_o == 0.4:
                break
            time.sleep(0.25)
        check("theme.opacity_persists", saved_o == 0.4, f"(got {saved_o})")

        # the frost slider drives --frost (blur) and persists
        slider("frost", 40)
        check("theme.frost_live",
              js(window, "document.documentElement.style.getPropertyValue('--frost')") == "0.4")
        deadline = time.time() + 4
        saved_f = None
        while time.time() < deadline:
            saved_f = bv_settings.load().get("frost")
            if saved_f == 0.4:
                break
            time.sleep(0.25)
        check("theme.frost_persists", saved_f == 0.4, f"(got {saved_f})")

        # REGRESSION (Jake, live): a glass slider must never change the
        # background - the effect picked via the menu has to survive any
        # pref re-apply (the settings mirror in bgfx.set/tune)
        js(window, "BV.bgfx.set('petals', true)")
        slider("panel opacity", 60)
        time.sleep(0.3)
        check("theme.glass_slider_keeps_effect",
              js(window, "BV.bgfx.activeId") == "petals",
              f"(got {js(window, 'BV.bgfx.activeId')})")

        # the effect dropdown lists every effect incl. off. Compared against the
        # live table rather than a literal: "the menu shows all of them" is the
        # actual invariant, and boot.effect_count above already pins the count.
        js(window, "document.querySelector('.modal .btn.fx-pick').click()")
        nitems = poll(window, "document.querySelectorAll('.ctx-menu .ctx-item').length")
        nfx = js(window, "BV.bgfx.EFFECTS.length")
        check("theme.fx_menu_items", nitems == nfx, f"(got {nitems}, of {nfx})")
        # Esc dismisses the MENU and leaves the dialog standing. It used to take
        # the dialog with it: BV.menu's Esc was a document-capture listener and
        # the dialog's, registered first, won.
        js(window, """document.querySelector('.ctx-menu .ctx-item')
            .dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}))""")
        time.sleep(0.3)
        check("theme.fx_menu_esc_keeps_dialog",
              not js(window, "!!document.querySelector('.ctx-menu')") and js(window, "BV.modalOpen()"))

        # ---- the two SCALE sliders commit on RELEASE ----
        # They are the only controls whose own geometry is a function of the value
        # they set (rem dialog, centred): applying per tick grew the track ~170px
        # and walked it ~120px left mid-drag, so the value fought the pointer.
        # Dragging must move the READOUT only; `change` (let-go) commits.
        before = js(window, "document.documentElement.style.fontSize")
        slider("text size", 18)                      # input only == a drag in progress
        time.sleep(0.2)
        check("scale.drag_does_not_apply",
              js(window, "document.documentElement.style.fontSize") == before,
              f"(got {js(window, 'document.documentElement.style.fontSize')}, was {before})")
        check("scale.readout_follows_drag",
              (js(window, """(function(){
                  var r = [...document.querySelectorAll('#modal-root .set-row')].find(function(x){
                      return x.querySelector('.name').textContent === 'text size'; });
                  return r.querySelector('.range-val').textContent;
              })()""") or "") == "18px")
        slider("text size", 18, event="change")      # let go
        time.sleep(0.2)
        check("scale.release_applies",
              js(window, "document.documentElement.style.fontSize") == "18px",
              f"(got {js(window, 'document.documentElement.style.fontSize')})")
        slider("text size", 15, event="change")      # back to the default
        time.sleep(0.2)

        # ---- the theme picker panel ----
        js(window, "document.querySelector('#modal-root .theme-pick').click()")
        got = poll(window, """(function(){
            var p = document.querySelector('.bv-drop');
            if (!p) return '';
            var pick = document.querySelector('#modal-root .theme-pick');
            return JSON.stringify({
                rows: p.querySelectorAll('.opt-row[data-theme-id]').length,
                folds: p.querySelectorAll('.bv-collapsible').length,
                cats: [...p.querySelectorAll('.acc-name')].map(function(n){return n.textContent;}),
                filter: !!p.querySelector('.search-box input'),
                rightAligned: Math.abs(p.getBoundingClientRect().right -
                                       pick.getBoundingClientRect().right) <= 2,
            });
        })()""")
        pick = json.loads(got or "{}")
        check("picker.rows", (pick.get("rows") or 0) >= 20, f"(got {pick.get('rows')})")
        # nothing in the picker folds: a category is a divider, not a control
        check("picker.no_folds", pick.get("folds") == 0, f"(got {pick.get('folds')})")
        check("picker.custom_first", (pick.get("cats") or [""])[0] == "Custom", f"({pick.get('cats')})")
        check("picker.has_filter", pick.get("filter") is True)
        check("picker.right_aligned", pick.get("rightAligned") is True)

        # the filter narrows across packs and every hit stays visible.
        # BV.searchBox debounces 150ms, and THIS window throttles timers to ~1s
        # because it is hidden — poll, never sleep a fixed 400ms.
        def filter_to(text):
            js(window, f"""(function(){{
                var i = document.querySelector('.bv-drop .search-box input');
                i.value = '{text}';
                i.dispatchEvent(new Event('input', {{bubbles: true}}));
            }})()""")

        total = js(window, "document.querySelectorAll('.bv-drop .opt-row[data-theme-id]').length")
        filter_to("cyber")
        nhits = poll(window, f"""(function(){{
            var n = document.querySelectorAll('.bv-drop .opt-row[data-theme-id]').length;
            return n < {total} ? n : 0;
        }})()""")
        check("picker.filter_narrows", 0 < (nhits or 0) < 6, f"(got {nhits}, of {total})")
        filter_to("")
        back = poll(window, f"""(function(){{
            var n = document.querySelectorAll('.bv-drop .opt-row[data-theme-id]').length;
            return n === {total} ? n : 0;
        }})()""")
        check("picker.filter_clears", back == total, f"(got {back}, wanted {total})")

        # hover previews live, and LEAVING the list ends the preview
        committed = js(window, "BV.theme.activeId")
        js(window, """(function(){
            var r = [...document.querySelectorAll('.bv-drop .opt-row[data-theme-id]')].find(function(x){
                return x.dataset.themeId !== BV.theme.activeId; });
            r.dispatchEvent(new MouseEvent('mouseenter'));
        })()""")
        time.sleep(0.2)
        check("picker.hover_previews", js(window, "BV.theme.activeId") != committed)
        js(window, """document.querySelector('.bv-drop .theme-pick-list')
            .dispatchEvent(new MouseEvent('mouseleave'))""")
        time.sleep(0.2)
        check("picker.hover_off_ends_preview", js(window, "BV.theme.activeId") == committed,
              f"(got {js(window, 'BV.theme.activeId')})")

        # Esc closes the PANEL, not the dialog under it (window-capture keydown:
        # the dialog's own Esc handler is a document-capture listener registered
        # first, so it would otherwise win and take the whole dialog down)
        js(window, """document.querySelector('.bv-drop .search-box input')
            .dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}))""")
        time.sleep(0.3)
        check("picker.esc_closes_panel_only",
              not js(window, "!!document.querySelector('.bv-drop')") and js(window, "BV.modalOpen()"))

        # a click commits, and the picker button repaints to the new theme
        js(window, "document.querySelector('#modal-root .theme-pick').click()")
        time.sleep(0.4)
        picked = js(window, """(function(){
            var r = [...document.querySelectorAll('.bv-drop .opt-row[data-theme-id]')].find(function(x){
                return x.dataset.themeId !== BV.theme.activeId; });
            var id = r.dataset.themeId;
            r.click();
            return id;
        })()""")
        time.sleep(0.3)
        check("picker.click_commits", js(window, "BV.theme.activeId") == picked,
              f"(got {js(window, 'BV.theme.activeId')}, wanted {picked})")
        check("picker.closes_on_pick", not js(window, "!!document.querySelector('.bv-drop')"))
        want = js(window, f"""(function(){{
            var t = BV.theme.themes.find(function(x){{ return x.id === '{picked}'; }});
            return t ? (t.name || t.id) : '';
        }})()""")
        got_nm = js(window, "document.querySelector('#modal-root .theme-pick .nm').textContent")
        check("picker.button_repaints", got_nm == want, f"(got {got_nm!r}, wanted {want!r})")

        # The panel lives on <html> so nothing can clip it — which also means it
        # OUTLIVES the dialog unless every teardown path takes it down. Two paths
        # reach it without a click landing outside the panel:

        # 1. switching tabs tears down the row that owns it
        js(window, "document.querySelector('#modal-root .theme-pick').click()")
        time.sleep(0.4)
        check("picker.panel_open_before_switch", js(window, "!!document.querySelector('.bv-drop')"))
        js(window, """document.querySelector('#modal-root .set-tabs button[data-tab=preferences]').click()""")
        time.sleep(0.3)
        check("picker.tab_switch_closes_panel",
              not js(window, "!!document.querySelector('.bv-drop')") and js(window, "BV.modalOpen()"))

        # 2. the ＋ hops to the color editor: BV.modal does not stack, so the
        #    dialog steps aside — and the panel must not be left floating
        js(window, """document.querySelector('#modal-root .set-tabs button[data-tab=display]').click()""")
        time.sleep(0.3)
        js(window, "document.querySelector('#modal-root .theme-pick').click()")
        time.sleep(0.4)
        js(window, "document.querySelector('#modal-root .theme-new').click()")
        time.sleep(0.4)
        check("picker.editor_hop_closes_panel",
              not js(window, "!!document.querySelector('.bv-drop')"))
        check("picker.editor_hop_opens_editor",
              js(window, "BV.modalOpen()")
              and not js(window, "!!document.querySelector('#modal-root .modal.settings-win')")
              and js(window, "!!document.querySelector('#modal-root .editor-row')"))
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")
        time.sleep(0.4)

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

        # ---- the preferences tab holds app behavior, and only that ----
        js(window, "BV.bgfx.set('none', false); BV.uiPrefs.modal('preferences')")
        got = poll(window, """(function(){
            var m = document.querySelector('#modal-root .modal');
            if (!m) return '';
            return JSON.stringify({
                heads: [...m.querySelectorAll('.set-head')].map(function(h){return h.textContent;}),
                rows: [...m.querySelectorAll('.set-row .name')].map(function(n){return n.textContent;}),
            });
        })()""")
        prefs = json.loads(got or "{}")
        check("prefs.sections",
              prefs.get("heads") == ["3d view", "library", "cv-x simulator", "updates"],
              f"({prefs.get('heads')})")
        check("prefs.rows",
              prefs.get("rows") == ["invert rotate x", "invert rotate y",
                                    "library folder", "simulator folder",
                                    "check on startup"],
              f"({prefs.get('rows')})")
        # every one of the 14 controls has exactly one home
        check("prefs.no_display_rows",
              not any(r in (prefs.get("rows") or [])
                      for r in ("theme", "font", "text size", "frost", "effect")),
              f"({prefs.get('rows')})")
        # `t` opens the dialog now that the 🎨 window is gone
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")
        time.sleep(0.3)
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'t', bubbles:true}))""")
        opened = poll(window, """(function(){
            var m = document.querySelector('#modal-root .modal.settings-win');
            return m ? (m.querySelector('.set-tabs button.active') || {}).textContent || '' : '';
        })()""")
        check("keys.t_opens_display", opened == "display", f"(got {opened!r})")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))""")

        report()
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
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
