"""Hidden-window probe for the CV-X simulator export UI: the "simulator folder"
row in the ⚙ dialog (settings_ui.js) and the "load cameras…" picker
(sim_export.js).

Asserts on the real DOM in a real WebView2, because the parts most likely to
break are the parts pytest cannot see: the row landing under `library folder`,
the checklist wiring (`bind`/`selected` — easy to mis-name against the shared
component), the counter/enable logic, and the copy button that exists purely so
the path can be pasted into the simulator.

Fully synthetic and identifier-clean: a temp library holding one FakePlant
camera whose latest backup is a real workspace, APPDATA redirected to a temp
folder BEFORE any backupviewer import.
Run: python tests/ui_sim_export_probe.py
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

_TMP = Path(tempfile.mkdtemp(prefix="bv_sim_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import keyence_workspace as kw  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []
SIM = _TMP / "KEYENCE" / "SIMFOLDER"


def check(name, cond, detail=""):
    print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")
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


OPEN_SETTINGS = """
document.dispatchEvent(new KeyboardEvent('keydown', {key:'t', bubbles:true}));
"""
GOTO_PREFS = """(function(){
  var m = document.querySelector('#modal-root .modal.settings-win');
  var b = [...m.querySelectorAll('.set-tabs button')].find(function(x){
    return x.textContent.trim() === 'preferences'; });
  if (b) b.click();
  return !!b;
})()"""


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.sim_export_loaded", js(window, "typeof BV.simExport === 'function'"))

        js(window, OPEN_SETTINGS)
        time.sleep(0.4)
        check("settings.prefs_tab", js(window, GOTO_PREFS) is True)
        time.sleep(0.4)

        rows = js(window, """(function(){
          var m = document.querySelector('#modal-root .modal.settings-win');
          return [...m.querySelectorAll('.set-row .name')].map(function(n){
            return n.textContent.trim(); });
        })()""") or []
        check("settings.has_sim_row", "simulator folder" in rows, f"({rows})")
        # the arrow on the mock pointed here: directly under the library folder
        if "library folder" in rows and "simulator folder" in rows:
            check("settings.sim_row_follows_library",
                  rows.index("simulator folder") == rows.index("library folder") + 1,
                  f"({rows})")

        shown = js(window, """(function(){
          var r = [...document.querySelectorAll('#modal-root .set-row')].find(function(x){
            return (x.querySelector('.name')||{}).textContent === 'simulator folder'; });
          if (!r) return null;
          return {path: (r.querySelector('.set-path-val')||{}).textContent || '',
                  btns: [...r.querySelectorAll('button')].map(function(b){
                    return b.textContent.trim(); })};
        })()""") or {}
        check("settings.shows_configured_path", shown.get("path") == str(SIM),
              f"({shown.get('path')!r})")
        check("settings.has_copy_change_load",
              shown.get("btns") == ["copy", "change…", "load cameras…"],
              f"({shown.get('btns')})")

        # the copy button is the whole point of the row - it must reach the
        # clipboard helper, not throw (WebView2 has no async clipboard by default)
        copied = js(window, """(function(){
          var seen = null, real = BV.copyText;
          BV.copyText = function(t, m){ seen = t; };
          var r = [...document.querySelectorAll('#modal-root .set-row')].find(function(x){
            return (x.querySelector('.name')||{}).textContent === 'simulator folder'; });
          [...r.querySelectorAll('button')].find(function(b){
            return b.textContent.trim() === 'copy'; }).click();
          BV.copyText = real;
          return seen;
        })()""")
        check("settings.copy_sends_the_path", copied == str(SIM), f"({copied!r})")

        # ---- the picker ----
        js(window, """[...document.querySelectorAll('#modal-root .set-row button')]
             .find(function(b){ return b.textContent.trim() === 'load cameras…'; }).click();""")
        names = poll(window, """(function(){
          var rows = document.querySelectorAll('#modal-root .sim-row .sim-name');
          return rows.length ? [...rows].map(function(n){ return n.textContent; }) : null;
        })()""")
        check("picker.lists_the_cameras", names == ["RB172", "RB180CAM1", "RB180CAM2"],
              f"({names})")

        check("picker.load_disabled_until_picked", js(window, """(function(){
          var b = document.querySelector('#modal-root .sim-foot button');
          return !!b && b.disabled;
        })()"""))

        # tick the first camera: the counter and the button must both wake up
        js(window, """document.querySelector('#modal-root .sim-row input').click();""")
        time.sleep(0.3)
        state = js(window, """(function(){
          var f = document.querySelector('#modal-root .sim-foot');
          return {count: f.querySelector('span').textContent,
                  disabled: f.querySelector('button').disabled};
        })()""") or {}
        check("picker.counts_the_pick", "1 selected" in (state.get("count") or ""),
              f"({state.get('count')!r})")
        check("picker.load_enabled_after_pick", state.get("disabled") is False)

        # the select-all box is an honest tri-state: with something already
        # picked a click CLEARS ("click to reset"), and only then selects all
        count_expr = """document.querySelector('#modal-root .sim-foot span').textContent"""
        js(window, """document.querySelector('#modal-root .sim-selall input').click();""")
        time.sleep(0.3)
        check("picker.selall_clears_first", "nothing" in (js(window, count_expr) or ""),
              f"({js(window, count_expr)!r})")
        js(window, """document.querySelector('#modal-root .sim-selall input').click();""")
        time.sleep(0.3)
        check("picker.selall_then_selects_all", "3 selected" in (js(window, count_expr) or ""),
              f"({js(window, count_expr)!r})")

        js(window, """document.querySelector('#modal-root .sim-foot button').click();""")
        # the picker goes away AND hands the one modal root back to the settings
        # dialog it was launched from, on the tab that launched it
        back = poll(window, """(function(){
          if (document.querySelector('#modal-root .sim-rows')) return '';
          var m = document.querySelector('#modal-root .modal.settings-win');
          if (!m) return '';
          return (m.querySelector('.set-tabs button.active') || {}).textContent || '';
        })()""")
        check("picker.returns_to_settings", back == "preferences", f"(got {back!r})")

        got = sorted(p.name for p in SIM.iterdir() if p.is_dir()) if SIM.is_dir() else []
        check("export.flat_folder_on_disk", got == ["RB172", "RB180CAM1", "RB180CAM2"],
              f"({got})")
        check("export.each_is_a_workspace",
              all(kw.is_workspace(SIM / n) for n in got), f"({got})")

        # ---- the guard: a hand-made workspace must not be eaten ----
        handmade = SIM / "RB999"
        (handmade / kw.SD_DIR / "cv-x" / "setting").mkdir(parents=True)
        (handmade / kw.SD_DIR / "cv-x" / "setting" / "env.dat").write_bytes(b"a week of work")
        kw.write_workspace_xml(handmade, "192.0.2.99")
        _rename_station(_TMP / "lib", "RB172", "RB999")     # now the camera wants that name

        js(window, """[...document.querySelectorAll('#modal-root .set-row button')]
             .find(function(b){ return b.textContent.trim() === 'load cameras…'; }).click();""")
        rows = poll(window, """(function(){
          var r = document.querySelectorAll('#modal-root .sim-row');
          if (!r.length) return null;
          return [...r].map(function(x){
            return (x.querySelector('.sim-name')||{}).textContent + '|' +
                   [...x.querySelectorAll('.pill')].map(function(p){
                     return p.textContent; }).join(',');
          });
        })()""") or []
        check("guard.marks_the_foreign_one",
              any(r.startswith("RB999|") and "not ours" in r for r in rows), f"({rows})")
        check("guard.marks_our_own_as_replaces",
              any(r.startswith("RB180CAM1|") and "replaces" in r for r in rows), f"({rows})")

        # select everything and load: the safe ones land, the foreign one stops us
        js(window, """document.querySelector('#modal-root .sim-selall input').click();""")
        time.sleep(0.3)
        js(window, """document.querySelector('#modal-root .sim-foot button').click();""")
        title = poll(window, """(function(){
          var h = document.querySelector('#modal-root .modal h2');
          return h && /replace folders/.test(h.textContent) ? h.textContent : '';
        })()""")
        check("guard.asks_before_replacing", bool(title), f"(got {title!r})")
        check("guard.untouched_while_asking",
              (handmade / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes()
              == b"a week of work")

        # "keep what's there" must leave it alone
        js(window, """[...document.querySelectorAll('#modal-root .sim-foot button')]
             .find(function(b){ return /keep/.test(b.textContent); }).click();""")
        time.sleep(0.8)
        check("guard.keep_leaves_it_alone",
              (handmade / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes()
              == b"a week of work")

        # and an explicit "replace them" goes through
        js(window, """[...document.querySelectorAll('#modal-root .set-row button')]
             .find(function(b){ return b.textContent.trim() === 'load cameras…'; }).click();""")
        poll(window, """document.querySelector('#modal-root .sim-row') ? 'y' : ''""")
        js(window, """document.querySelector('#modal-root .sim-selall input').click();""")
        time.sleep(0.3)
        js(window, """document.querySelector('#modal-root .sim-foot button').click();""")
        poll(window, """(function(){
          var h = document.querySelector('#modal-root .modal h2');
          return h && /replace folders/.test(h.textContent) ? 'y' : '';
        })()""")
        js(window, """[...document.querySelectorAll('#modal-root .sim-foot button')]
             .find(function(b){ return /replace them/.test(b.textContent); }).click();""")
        replaced = poll(window, """document.querySelector('#modal-root .sim-rows') ? '' : 'y'""")
        time.sleep(1.0)
        check("guard.confirm_replaces_it", replaced == "y" and
              (handmade / kw.SD_DIR / "cv-x" / "setting" / "env.dat").read_bytes() == b"env blob")

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def _rename_station(lib, old, new):
    """Point a seeded station at a new name, folders and all, so the probe can
    arrange a collision with a hand-made workspace."""
    from backupviewer import library
    for line_dir in lib.iterdir():
        for d in list(line_dir.rglob(old)):
            d.rename(d.with_name(new))
    data = library.list_robots()
    for e in data["robots"]:
        if e.get("robot") == old:
            library.update_robot(e["id"], {"robot": new,
                                           "latest_path": (e.get("latest_path") or "")
                                           .replace(old, new)})


def _seed():
    """One single-camera station and one two-camera station, laid down in the
    shape a real CV-X pull leaves behind: a dated snapshot carrying backup.json
    plus the Latest mirror. The library is then built by the app's own SCAN, not
    hand-registered — a hand-added entry with no dated snapshot is (correctly)
    reconciled away by the boot rescan, which is what makes this fixture honest.
    Identifier-clean: FakePlant / RB… / TEST-NET addresses."""
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
    bv_settings.set_value("library_root", str(lib))
    bv_settings.set_value("sim_root", str(SIM))

    def mkws(folder, ip):
        (folder / kw.SD_DIR / "cv-x" / "setting").mkdir(parents=True)
        (folder / kw.SD_DIR / "cv-x" / "setting" / "env.dat").write_bytes(b"env blob")
        kw.write_workspace_xml(folder, ip)

    for station, cams, ip in (("RB172", ("CAM1",), "192.0.2.44"),
                              ("RB180", ("CAM1", "CAM2"), "192.0.2.45")):
        dated = lib / "RBB01" / station / "2026_07_25" / "10_00_00"
        for c in cams:
            mkws(dated / c, ip)
        (dated / "backup.json").write_text(json.dumps({
            "type": "keyence cv-x setting", "device_type": "camera-keyence",
            "robot": station, "line": "RBB01", "plant": "FakePlant",
            "taken": "2026-07-25T10:00:00", "complete": True, "host": ip,
            "cameras": [{"label": c, "host": ip} for c in cams],
        }), encoding="utf-8")
        for c in cams:
            mkws(lib / "RBB01" / "Latest" / station / c, ip)


def main():
    _seed()
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
