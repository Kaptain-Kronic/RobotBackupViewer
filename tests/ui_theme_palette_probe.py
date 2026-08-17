"""Hidden-window probe for the theme editor's hex fields + saved palette
(theme.js editTheme).

What it pins down:
  * every color row carries a hex text field two-way synced with its color
    input: a pasted 6-digit code applies live (letter O forgiven as zero —
    the classic hand-copied-code typo), a 3-digit code waits for change so
    half-typed 6-digit codes never flash a wrong color, junk marks the field
    bad without painting anything, and blur snaps back to canonical #rrggbb;
  * while the field is FOCUSED (a real typist) the text is never rewritten
    under the caret — only change/blur canonicalizes;
  * the palette strip: ＋ saves the highlighted row's color (deduped),
    clicking a swatch paints the highlighted row, × forgets a swatch, and
    the set persists to settings.json (app-level, NOT part of any theme) —
    including hydrating from disk at boot, junk entries dropped;
  * the target follows both halves of row-touching (mousedown AND focusin),
    and collapsing "advanced" can never leave it on a hidden row;
  * the dirty contract, both directions: adding/removing swatches saves
    immediately and never arms the discard guard, while APPLYING a swatch
    is a theme edit and must arm it;
  * a draft restore repaints ALL color inputs — the old advanced-only sync
    left the four main pickers showing stale values after a restore.

Fully synthetic and identifier-clean: empty library in a temp folder,
APPDATA redirected there BEFORE importing the app.
Run: python tests/ui_theme_palette_probe.py
"""
import sys
import time
from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_theme_palette_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402


def _row(label):
    """JS expression fragment: the editor row whose label is `label`."""
    return ("[...document.querySelectorAll('#modal-root .editor-row')]"
            ".find(function(x){ var n = x.querySelector('.name');"
            f" return n && n.textContent === '{label}'; }})")


def _set_field(window, label, sel, value, event):
    js(window, f"""(function(){{
        var r = {_row(label)};
        var i = r.querySelector('{sel}');
        i.value = '{value}';
        i.dispatchEvent(new Event('{event}', {{bubbles: true}}));
    }})()""")


def _var(window, name):
    return js(window, f"document.documentElement.style.getPropertyValue('{name}')")


def _esc(window, times=1):
    for _ in range(times):
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown', "
                   "{key:'Escape', bubbles:true}))")
        time.sleep(0.4)


def _poll_setting(key, want, timeout=8):
    deadline = time.time() + timeout
    got = None
    while time.time() < deadline:
        got = bv_settings.load().get(key)
        if got == want:
            return got
        time.sleep(0.25)
    return got


def probe(window):
    try:
        time.sleep(4)  # boot

        js(window, "BV.theme.editTheme(null)")
        opened = poll(window, "!!document.querySelector('#modal-root .editor-row')")
        check("editor.opens", bool(opened))

        # ---- structure: 9 color rows (4 main + 5 advanced), each hex + picker.
        #      A literal on purpose: the did-we-lose-a-row anchor — update it
        #      deliberately when a color joins or leaves the editor. ----
        nhex = js(window, "document.querySelectorAll('#modal-root .editor-row .editor-hex').length")
        npick = js(window, "document.querySelectorAll("
                           "'#modal-root .editor-row input[type=color]').length")
        check("editor.hex_per_row", nhex == 9, f"(got {nhex})")
        check("editor.picker_per_row", npick == 9, f"(got {npick})")
        check("palette.strip_present", js(window, "!!document.querySelector('.modal .pal-strip')"))

        # ---- the seeded palette came up from DISK (boot hydration), and the
        #      junk entry seeded beside it was dropped by the sanitize filter ----
        check("palette.boot_hydrates",
              js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 1
              and (js(window, "document.querySelector('.modal .pal-swatch').title") or "")
              .startswith("#123456"))
        js(window, "document.querySelector('.modal .pal-swatch .pal-x').click()")
        saved = _poll_setting("palette", [])
        check("palette.boot_remove_persists", saved == [], f"(got {saved})")
        check("palette.empty_hint", js(window, "!!document.querySelector('.modal .pal-hint')"))
        check("target.defaults_to_bg",
              js(window, f"({_row('background')}).classList.contains('pal-target')"))

        # ---- a pasted code applies live, letter O read as zero ----
        _set_field(window, "accent", ".editor-hex", "#OD3B3E", "input")
        check("hex.paste_applies", _var(window, "--accent") == "#0d3b3e",
              f"(got {_var(window, '--accent')})")
        # pins the unfocused/programmatic path only — a FOCUSED field is
        # deliberately not rewritten on input (see hex.typing_not_rewritten)
        check("hex.field_canonical",
              js(window, f"({_row('accent')}).querySelector('.editor-hex').value") == "#0d3b3e")
        check("hex.picker_follows",
              js(window, f"({_row('accent')}).querySelector('input[type=color]').value") == "#0d3b3e")

        # ---- junk marks the field bad and paints nothing; blur snaps back ----
        _set_field(window, "accent", ".editor-hex", "nope", "input")
        check("hex.junk_marks_bad",
              js(window, f"({_row('accent')}).querySelector('.editor-hex')"
                         ".classList.contains('bad')"))
        check("hex.junk_paints_nothing", _var(window, "--accent") == "#0d3b3e")
        _set_field(window, "accent", ".editor-hex", "nope", "change")
        check("hex.blur_snaps_back",
              js(window, f"({_row('accent')}).querySelector('.editor-hex').value") == "#0d3b3e"
              and not js(window, f"({_row('accent')}).querySelector('.editor-hex')"
                                 ".classList.contains('bad')"))

        # ---- a 3-digit code waits for change (it could be half of a 6-digit) ----
        _set_field(window, "accent", ".editor-hex", "#fa0", "input")
        check("hex.short_waits", _var(window, "--accent") == "#0d3b3e",
              f"(got {_var(window, '--accent')})")
        _set_field(window, "accent", ".editor-hex", "#fa0", "change")
        check("hex.short_applies_on_change", _var(window, "--accent") == "#ffaa00",
              f"(got {_var(window, '--accent')})")

        # ---- mid-typing, the FOCUSED field is never rewritten under the caret
        #      (syncInputs skips the active hex field; change canonicalizes) ----
        js(window, f"""(function(){{
            var i = ({_row('accent')}).querySelector('.editor-hex');
            i.focus();
            i.value = 'OD3B3E';
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
        }})()""")
        check("hex.typing_applies", _var(window, "--accent") == "#0d3b3e",
              f"(got {_var(window, '--accent')})")
        check("hex.typing_not_rewritten",
              js(window, f"({_row('accent')}).querySelector('.editor-hex').value") == "OD3B3E")
        js(window, f"""(function(){{
            var i = ({_row('accent')}).querySelector('.editor-hex');
            i.dispatchEvent(new Event('change', {{bubbles: true}}));
            i.blur();
        }})()""")
        check("hex.change_canonicalizes",
              js(window, f"({_row('accent')}).querySelector('.editor-hex').value") == "#0d3b3e")

        # ---- the picker drives the hex field too ----
        _set_field(window, "background", "input[type=color]", "#123456", "input")
        check("hex.follows_picker", _var(window, "--bg") == "#123456"
              and js(window, f"({_row('background')}).querySelector('.editor-hex').value") == "#123456")

        # ---- touching a row moves the palette target ----
        js(window, f"({_row('text')}).dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}))")
        check("target.follows_touch",
              js(window, f"({_row('text')}).classList.contains('pal-target')")
              and not js(window, f"({_row('background')}).classList.contains('pal-target')"))

        # ---- collapsing 'advanced' can never leave the target on a hidden row ----
        js(window, "document.querySelector('.modal .editor-adv-head').click()")
        js(window, f"({_row('warn')}).dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}))")
        check("target.advanced_row_targets",
              js(window, f"({_row('warn')}).classList.contains('pal-target')"))
        js(window, "document.querySelector('.modal .editor-adv-head').click()")
        check("target.collapse_retargets_bg",
              js(window, f"({_row('background')}).classList.contains('pal-target')")
              and not js(window, f"({_row('warn')}).classList.contains('pal-target')"))
        js(window, f"({_row('text')}).dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}))")

        # ---- ＋ saves the highlighted row's color, deduped, persisted.
        #      `expected` is read live off the row: the invariant is identity
        #      with the target row, not any particular value. The one literal
        #      check is the deliberate did-the-default-theme-move anchor — it
        #      alone fails if the app stops shipping serika_dark as default. ----
        expected = js(window, f"({_row('text')}).querySelector('.editor-hex').value")
        check("palette.default_theme_anchor", expected == "#d1d0c5", f"(got {expected})")
        js(window, "document.querySelector('.modal .pal-add').click()")
        check("palette.add_makes_swatch",
              js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 1)
        check("palette.swatch_is_target_color",
              (js(window, "document.querySelector('.modal .pal-swatch').title") or "")
              .startswith(expected))
        saved = _poll_setting("palette", [expected])
        check("palette.persists", saved == [expected], f"(got {saved})")
        js(window, "document.querySelector('.modal .pal-add').click()")
        check("palette.dedupes",
              js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 1)

        # ---- tabbing into a row retargets too (focusin — the keyboard path) ----
        js(window, f"({_row('sub')}).querySelector('.editor-hex')"
                   ".dispatchEvent(new FocusEvent('focusin', {bubbles:true}))")
        check("target.follows_focus",
              js(window, f"({_row('sub')}).classList.contains('pal-target')")
              and not js(window, f"({_row('text')}).classList.contains('pal-target')"))

        # ---- a swatch paints the highlighted row ----
        js(window, f"({_row('accent')}).dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}))")
        js(window, "document.querySelector('.modal .pal-swatch').click()")
        check("palette.swatch_paints_target", _var(window, "--accent") == expected,
              f"(got {_var(window, '--accent')})")

        # ---- × forgets a swatch, and the empty hint returns ----
        js(window, "document.querySelector('.modal .pal-swatch .pal-x').click()")
        check("palette.remove", js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 0
              and js(window, "!!document.querySelector('.modal .pal-hint')"))
        saved = _poll_setting("palette", [])
        check("palette.remove_persists", saved == [], f"(got {saved})")

        # one back in for the reopen check (accent holds the swatch color
        # after the apply, and accent is the target)
        js(window, "document.querySelector('.modal .pal-add').click()")
        saved = _poll_setting("palette", [expected])
        check("palette.readd_persists", saved == [expected], f"(got {saved})")

        # ---- the edits above wrote a draft (debounced; hidden windows throttle
        #      timers to ~1s, so poll) ----
        deadline = time.time() + 8
        draft = None
        while time.time() < deadline:
            draft = bv_settings.load().get("theme_draft")
            if draft and (draft.get("colors") or {}).get("bg") == "#123456":
                break
            time.sleep(0.25)
        check("draft.written", bool(draft) and draft.get("isNew") is True
              and (draft.get("colors") or {}).get("bg") == "#123456", f"(got {draft})")

        # ---- close: the editor is dirty, so Esc arms the guard, Esc discards ----
        _esc(window)
        check("guard.first_esc_holds", js(window, "BV.modalOpen()"))
        _esc(window)
        check("guard.second_esc_closes", not js(window, "BV.modalOpen()"))

        # ---- reopen: the palette survived, the draft is offered ----
        js(window, "BV.theme.editTheme(null)")
        reopened = poll(window, "!!document.querySelector('#modal-root .editor-row')")
        check("editor.reopens", bool(reopened))
        check("palette.survives_reopen",
              js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 1)
        check("draft.offered", js(window, "!!document.querySelector('.modal .draft-bar')"))

        # ---- palette add AND remove alone never dirty: Esc closes FIRST time.
        #      Both ops run in this clean editor so a dirty-flag sneaking into
        #      either handler cannot hide behind an already-dirty session. ----
        js(window, "document.querySelector('.modal .pal-add').click()")
        check("palette.clean_add_makes_swatch",
              js(window, "document.querySelectorAll('.modal .pal-swatch').length") == 2)
        js(window, "document.querySelector('.modal .pal-swatch .pal-x').click()")
        js(window, "document.querySelector('.modal .pal-swatch .pal-x').click()")
        saved = _poll_setting("palette", [])
        check("palette.clean_ops_persist", saved == [], f"(got {saved})")
        check("palette.editor_open_before_esc", js(window, "BV.modalOpen()"))
        _esc(window)
        check("palette.change_not_dirty", not js(window, "BV.modalOpen()"))

        # ---- restore repaints EVERY input (the stale-main-pickers fix) ----
        js(window, "BV.theme.editTheme(null)")
        offered = poll(window, "!!document.querySelector('.modal .draft-bar')")
        check("draft.bar_reoffered", bool(offered))
        js(window, """[...document.querySelectorAll('.modal .draft-bar .btn')]
            .find(function(b){ return b.textContent === 'restore'; }).click()""")
        time.sleep(0.3)
        check("draft.restore_applies", _var(window, "--bg") == "#123456",
              f"(got {_var(window, '--bg')})")
        check("draft.restore_repaints_picker",
              js(window, f"({_row('background')}).querySelector('input[type=color]').value")
              == "#123456")
        check("draft.restore_repaints_hex",
              js(window, f"({_row('background')}).querySelector('.editor-hex').value")
              == "#123456")
        _esc(window, times=2)

        # ---- the inverse contract: APPLYING a swatch is a theme edit and must
        #      arm the guard — a stray Esc can never eat an applied color ----
        js(window, "BV.theme.editTheme(null)")
        opened = poll(window, "!!document.querySelector('#modal-root .editor-row')")
        check("editor.opens_for_apply", bool(opened))
        js(window, "document.querySelector('.modal .pal-add').click()")
        js(window, f"({_row('accent')}).dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}))")
        js(window, "document.querySelector('.modal .pal-swatch').click()")
        _esc(window)
        check("palette.apply_dirties", js(window, "BV.modalOpen()"))
        _esc(window)
        check("palette.apply_guard_second_esc", not js(window, "BV.modalOpen()"))

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
    # seeded BEFORE boot so the first open exercises the real read path
    # (disk -> get_settings -> BV.state.settings -> editor), junk included —
    # the in-session mirror can't stand in for it
    bv_settings.set_value("palette", ["#123456", "zzz"])

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
