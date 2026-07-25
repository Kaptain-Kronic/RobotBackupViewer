"""Hidden-window probe for the multi-robot EDIT WORKSPACE (#edit).

Covers what pytest cannot: that a shell screen renders with ZERO backups open,
the rail's working set (grouped per robot, collapsible), per-pane tab strips
(open by double-click, close by x, ctrl+tab cycling), the single-layer
BV.lsEditor mounting per pane, live dirty state reaching the rail and the
topbar badge, find/replace scoped to the working set (identity-aware matching,
per-robot scope, collapsible grouped results with select-all, click-to-reveal),
and an export that lands ONE FOLDER PER ROBOT while both backups stay
byte-for-byte untouched.

Two robots deliberately share a program name (MAIN.LS) - the collision the
per-robot export layout exists to prevent.

Fully synthetic and identifier-clean: RB* fakes under FakePlant in a temp
library, APPDATA redirected before any backupviewer import.
Run: python tests/ui_edit_probe.py
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

_TMP = Path(tempfile.mkdtemp(prefix="bv_ws_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []
ROBOTS = ["RB010R01B01", "RB020R01B01"]
PROG = "MAIN.LS"
DEST = _TMP / "export"

PROG_BYTES = (
    b"/PROG  MAIN\r\n"
    b"/ATTR\r\n"
    b"OWNER\t\t= MNEDITOR;\r\n"
    b'COMMENT\t\t= "EDIT ME";\r\n'
    b"PROTECT\t\t= READ_WRITE;\r\n"
    b"/MN\r\n"
    b"   1:  !setup ;\r\n"
    b"   2:  R[21:SERVO GUN WORK]=1 ;\r\n"
    b"   3:  R[21]=2 ;\r\n"
    b"   4:  !R[21] remarked on purpose ;\r\n"
    b"   5:J P[1] 100% FINE ;\r\n"
    b"/POS\r\n"
    b"P[1]{\r\n"
    b"   GP1:\r\n"
    b"\tUF : F, UT : F,\t\tCONFIG : '',\r\n"
    b"\tX = ********  mm,\tY = ********  mm,\tZ = ********  mm,\r\n"
    b"\tW = ******** deg,\tP = ******** deg,\tR = ******** deg\r\n"
    b"};\r\n"
    b"/END\r\n"
)
SNAPS = {}


def check(name, cond, detail=""):
    print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=30, delay=0.25):
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

        # ---- the shell contract: #edit renders with NO backup open ----
        check("boot.no_backup_open", not js(window, "!!BV.state.manifest"))
        js(window, "location.hash = '#edit'")
        time.sleep(0.6)
        check("shell.renders_with_zero_backups", bool(js(window, "!!document.querySelector('.ws')")),
              "(a non-shell tab would have been bounced to #home)")
        check("shell.empty_state", bool(js(window, "!!document.querySelector('.ws-empty')")))
        check("shell.topbar_button", bool(js(window, "!!document.getElementById('btn-edit')")))
        check("shell.chrome_hidden",
              bool(js(window, "document.getElementById('btn-compare').classList.contains('hidden')")))

        # ---- populate the working set from BOTH robots (same program name) ----
        # robot ids come from a library SCAN, not from folder names - go through
        # lib_list first, exactly as the library screen does
        js(window, """window._ids='';
            BV.api.call('lib_list').then(function(d){
                var rs = (d.robots||[]).filter(function(r){ return !!r.id; });
                window._ids = JSON.stringify(rs.map(function(r){ return r.id; }));
            }, function(e){ window._ids = 'err:' + (e.code||e.message); });""")
        ids_raw = poll(window, "window._ids")
        ids = []
        if isinstance(ids_raw, str) and ids_raw.startswith("["):
            ids = json.loads(ids_raw)
        check("library.scanned_two_robots", len(ids) == 2, f"(got {ids_raw!r})")

        js(window, """window._add='';
            Promise.all(%s.map(function(id){ return BV.api.call('ws_robot_programs', id); }))
              .then(function(rs){
                var n = 0;
                rs.forEach(function(r){
                  n += BV.workspace.addMany(r.programs.map(function(p){
                    return {root:r.root, label:r.label, file:p.file, name:p.name};
                  }));
                });
                window._add = 'added:' + n;
              }, function(e){ window._add = 'err:' + (e.code||e.message); });""" % json.dumps(ids))
        added = poll(window, "window._add")
        check("add.via_library_endpoint", added == "added:2", f"(got {added!r})")
        check("add.no_session_opened", js(window, "1") == 1 and not window._bv_api._sessions,
              "(a workspace must not consume MAX_OPEN_SESSIONS)")

        js(window, "BV.route()")
        time.sleep(0.5)
        check("rail.two_robot_groups",
              js(window, "document.querySelectorAll('.ws-robot-h').length") == 2)
        check("rail.two_programs",
              js(window, "document.querySelectorAll('.ws-prog').length") == 2)
        check("topbar.badge_counts",
              (js(window, "(document.querySelector('#btn-edit .wsbadge')||{}).textContent") or "") == "2")

        # ---- the sandbox bug: the working-set caret must COLLAPSE ----
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.25)
        after = js(window, "document.querySelectorAll('.ws-prog').length")
        check("rail.caret_collapses", after == 1, f"(2 -> {after})")
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.25)
        check("rail.caret_expands",
              js(window, "document.querySelectorAll('.ws-prog').length") == 2)

        # ---- open both programs, one per pane, by double-click ----
        check("panes.two", js(window, "document.querySelectorAll('.ws-pane').length") == 2)
        check("panes.resizer_between",
              js(window, "document.querySelectorAll('.ws-panes .ws-resizer').length") == 1)
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog');
            r[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));
            r[0].dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        seeded = poll(window, "(document.querySelector('.lsed-code')||{}).textContent || ''")
        check("pane.editor_mounted", bool(seeded))
        check("pane.body_only",
              isinstance(seeded, str) and seeded.split("\n")[0] == "!setup"
              and ";" not in seeded, f"(got {seeded!r})")
        check("pane.single_layer",
              not js(window, "!!document.querySelector('.lsed-ta')"))
        check("pane.tab_created",
              js(window, "document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab').length") == 1)

        # second program into the RIGHT pane
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog')[1];
            var p1=document.querySelectorAll('.ws-pane')[1];
            p1.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        time.sleep(0.7)
        check("panes.second_pane_has_tab",
              js(window, "document.querySelectorAll('.ws-pane')[1].querySelectorAll('.ws-tab').length") == 1)
        check("panes.two_editors",
              js(window, "document.querySelectorAll('.lsed-code').length") == 2,
              "(side-by-side)")

        # ---- edit one program: dirty must reach the rail + badge ----
        js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            code.textContent = code.textContent + '\\nCALL ADDED';
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.4)
        check("edit.workspace_dirty", bool(js(window, "BV.workspace.anyDirty()")))
        check("edit.rail_dot", bool(js(window, "!!document.querySelector('.ws-prog .dot.dirty')")))
        check("edit.export_btn_enabled",
              not js(window, "document.querySelector('.toolbar-slot .btn.primary').disabled"))

        # ---- find/replace in the rail: identity-aware, scoped, grouped ----
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('find')>=0;}).click()""")
        got = poll(window, "!!document.querySelector('.fp-inputs input')")
        check("find.panel_in_rail", bool(got))
        check("find.code_still_visible",
              js(window, "document.querySelectorAll('.lsed-code').length") >= 1,
              "(the panel takes the rail, not the editors)")
        js(window, """(function(){
            var i=document.querySelector('.fp-inputs input');
            i.value='R[21]';
            i.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.6)
        snips = js(window, """[...document.querySelectorAll('.fp-ln .snip')]
            .map(function(s){return s.textContent;})""") or []
        check("find.identity_matches_both_forms",
              any("R[21:SERVO GUN WORK]" in s for s in snips) and any(s.strip().startswith("R[21]=") for s in snips),
              f"({len(snips)} hits)")
        check("find.remarked_line_excluded",
              not any("remarked on purpose" in s for s in snips))
        check("find.grouped_by_robot",
              js(window, "document.querySelectorAll('.fp-rb').length") == 2)
        check("find.per_program_selectall",
              js(window, "document.querySelectorAll('.fp-pg input[type=checkbox]').length") == 2)
        # a robot caret folds its results without losing the selection
        before = js(window, "document.querySelectorAll('.fp-ln').length")
        js(window, "document.querySelector('.fp-rb .caret').click()")
        time.sleep(0.3)
        folded = js(window, "document.querySelectorAll('.fp-ln').length")
        check("find.results_collapse", folded < before, f"({before} -> {folded})")
        js(window, "document.querySelector('.fp-rb .caret').click()")
        time.sleep(0.3)
        # excluding a robot drops it from scope but still SHOWS it
        js(window, """(function(){
            var cbs=document.querySelectorAll('.fp-scope input[type=checkbox]');
            cbs[1].checked=false; cbs[1].dispatchEvent(new Event('change',{bubbles:true}));
        })()""")
        time.sleep(0.4)
        check("find.robot_excluded_but_listed",
              bool(js(window, "!!document.querySelector('.fp-rb.excluded')")))
        js(window, """(function(){
            var cbs=document.querySelectorAll('.fp-scope input[type=checkbox]');
            cbs[1].checked=true; cbs[1].dispatchEvent(new Event('change',{bubbles:true}));
        })()""")
        time.sleep(0.4)
        # clicking a result opens it and flashes the line, WITHOUT closing find
        js(window, "document.querySelectorAll('.fp-ln')[0].click()")
        time.sleep(0.5)
        check("find.click_flashes_line", bool(js(window, "!!document.querySelector('.flashbar')")))
        check("find.panel_survives_click", bool(js(window, "!!document.querySelector('.fp')")),
              "(opening a tab must not rebuild the rail)")

        # ---- replace across BOTH robots ----
        js(window, """(function(){
            var i=document.querySelectorAll('.fp-inputs input')[1];
            i.value='R[30]'; i.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.4)
        js(window, """(function(){
            var b=[...document.querySelectorAll('.fp-foot .btn')].pop();
            b.click();
        })()""")
        time.sleep(0.6)
        check("replace.applied",
              js(window, "BV.workspace.dirtyCount()") == 2, "(both robots edited)")

        # ---- export: one folder per robot ----
        js(window, """window._exp='';
            BV.api.call('ws_export', BV.workspace.edits(), %s).then(function(r){
                BV.workspace.markSaved(); window._exp = JSON.stringify(r);
            }, function(e){ window._exp = 'err:' + (e.code||e.message); });""" % json.dumps(str(DEST)))
        exp = poll(window, "window._exp")
        check("export.ok", isinstance(exp, str) and exp.startswith("{"), f"(got {exp!r})")
        for robot in ROBOTS:
            out = DEST / robot / PROG
            check(f"export.{robot}_folder", out.is_file())
            if out.is_file():
                data = out.read_bytes()
                check(f"export.{robot}_replaced", b"R[30]" in data and b"R[21]=2" not in data)
                check(f"export.{robot}_kept_untouched_line", b"   1:  !setup ;\r\n" in data)
        check("export.no_part", not list(DEST.rglob("*.part")))
        check("export.clean_after_save", not js(window, "BV.workspace.anyDirty()"))

        # ---- THE trust contract ----
        for robot in ROBOTS:
            check(f"backup.{robot}_untouched",
                  (SNAPS[robot] / PROG).read_bytes() == PROG_BYTES)

        # ---- tabs: x closes, ctrl+tab cycles ----
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('working')>=0;}).click()""")
        time.sleep(0.3)
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog');
            var p0=document.querySelectorAll('.ws-pane')[0];
            p0.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            r[1].dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        time.sleep(0.6)
        n0 = js(window, "document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab').length")
        check("tabs.two_in_pane", n0 == 2, f"(got {n0})")
        act0 = js(window, """[...document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab')]
            .findIndex(function(t){return t.classList.contains('active');})""")
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown',
            {key:'Tab',ctrlKey:true,bubbles:true,cancelable:true}))""")
        time.sleep(0.4)
        act1 = js(window, """[...document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab')]
            .findIndex(function(t){return t.classList.contains('active');})""")
        check("tabs.ctrl_tab_cycles", act0 != act1, f"({act0} -> {act1})")
        js(window, "document.querySelectorAll('.ws-pane')[0].querySelector('.ws-tab .x').click()")
        time.sleep(0.4)
        check("tabs.x_closes",
              js(window, "document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab').length") == 1)

        # ---- the workspace survives a route away and back ----
        js(window, "location.hash = '#home'")
        time.sleep(0.6)
        js(window, "location.hash = '#edit'")
        time.sleep(0.8)
        check("persist.set_survives_navigation",
              js(window, "document.querySelectorAll('.ws-prog').length") == 2)

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        import traceback
        traceback.print_exc()
        FAILURES.append("crash")
    finally:
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass


def main():
    lib = _TMP / "lib"
    for rb in ROBOTS:
        snap = lib / "FakePlant" / "LINE01" / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / PROG).write_bytes(PROG_BYTES)
        (snap / "SUMMARY.DG").write_text(f"Robot Name     {rb}\n", encoding="utf-8")
        (snap / "backup.json").write_text(
            json.dumps({"robot": rb, "line": "LINE01", "plant": "FakePlant",
                        "taken": "2026-01-01T12:00:00", "complete": True}),
            encoding="utf-8")
        SNAPS[rb] = snap
    bv_settings.set_value("library_root", str(lib))

    api = Api()
    window = webview.create_window(
        "probe", url=str(resource_path("web/index.html")),
        js_api=api, width=1400, height=900, hidden=True)
    window._bv_api = api
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
