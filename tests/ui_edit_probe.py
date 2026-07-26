"""Hidden-window probe for the multi-robot EDIT WORKSPACE (#edit).

Covers what pytest cannot: that a shell screen renders with ZERO backups open,
the rail's working set (grouped per robot, collapsible), per-pane tab strips
(open by double-click, close by x, ctrl+tab cycling), the single-layer
BV.lsEditor mounting per pane, live dirty state reaching the rail and the
topbar badge, find/replace scoped to the working set (identity-aware matching,
per-robot scope, collapsible grouped results with select-all, click-to-reveal),
the pane-vs-pane diff strip (offered only while split, live while typing,
ref-comment differences honestly classified display-only), the
review-your-edits modal (original vs edited; clean programs say so plainly;
a rename alone counts as an exportable change), and an export that lands ONE
FOLDER PER ROBOT while both backups stay byte-for-byte untouched.

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
    b"   6:  CALL HOMEPOS ;\r\n"        # not in this backup -> flagged missing
    b"   7:  LBL[1:TOP] ;\r\n"
    b"   8:  IF DI[3]=ON,JMP LBL[1] ;\r\n"
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


def location_hash(window):
    return js(window, "location.hash.split('/')[0]")


def poll(window, expr, tries=30, delay=0.25):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


# Splits are opened by DRAGGING onto a pane's edges, so the probe has to drive
# real drag events AT a pane. Synthetic MouseEvents carry no dataTransfer,
# which edit.js's dragstart already tolerates (its setData sits in a
# try/catch); dragover/drop read only clientX/clientY.
_DRAG_JS = """(function(){
    var pane=document.querySelectorAll('.ws-pane')[%(pane)d];
    var r=pane.getBoundingClientRect();
    var x=r.left+r.width*%(fx)f, y=r.top+r.height*%(fy)f;
    var src=%(src)s;
    src.dispatchEvent(new MouseEvent('dragstart',{bubbles:true}));
    pane.dispatchEvent(new MouseEvent('dragover',
        {bubbles:true,cancelable:true,clientX:x,clientY:y}));
    var z=document.querySelector('.dropzone.show');
    /* read the zone BEFORE the drop - dropping hides it again */
    var seen=JSON.stringify(z ? {show:true,split:z.classList.contains('split'),
                                 left:z.style.left,top:z.style.top,
                                 width:z.style.width,height:z.style.height}
                              : {show:false});
    pane.dispatchEvent(new MouseEvent('drop',
        {bubbles:true,cancelable:true,clientX:x,clientY:y}));
    src.dispatchEvent(new MouseEvent('dragend',{bubbles:true}));
    return seen;
})()"""


def close_menu(window):
    """Dismiss an open BV.menu. Must be a SEPARATE evaluate_js call: wireDismiss
    attaches its Escape/outside-click listeners on a deferred tick, so a key
    dispatched in the same call as the open is simply not heard - and the stale
    menu then shadows the next one under document.querySelector('.ctx-menu')."""
    js(window, """window.dispatchEvent(new KeyboardEvent('keydown',
        {key:'Escape',bubbles:true,cancelable:true}))""")
    time.sleep(0.25)


def menu_text(window):
    return js(window, """(function(){
        var m=document.querySelector('.ctx-menu');
        return m ? [...m.querySelectorAll('.ctx-item')]
            .map(function(b){return b.textContent;}).join(' | ') : '';
    })()""") or ""


def drag_prog(window, row_idx, fx, fy=0.5, pane=0):
    """Drag working-set row `row_idx` onto pane `pane` at (fx, fy) of its box.
    The outer 22%% band of a pane is an edge (split there); the rest is
    "into this pane's tabs"."""
    return js(window, _DRAG_JS % {
        "fx": fx, "fy": fy, "pane": pane,
        "src": "document.querySelectorAll('.ws-prog')[%d]" % row_idx})


def drag_tab(window, from_pane, tab_idx, fx, fy=0.5, pane=0):
    """Drag an open program TAB out of pane `from_pane` onto pane `pane`."""
    return js(window, _DRAG_JS % {
        "fx": fx, "fy": fy, "pane": pane,
        "src": "document.querySelectorAll('.ws-pane')[%d].querySelectorAll('.ws-tab')[%d]"
               % (from_pane, tab_idx)})


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
        check("shell.topbar_button", bool(js(window, "!!document.getElementById('cube-edit')")))
        # ctrl+e reaches the workspace from anywhere in the main window
        js(window, "location.hash = '#home'")
        time.sleep(0.6)
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown',
            {key:'e',ctrlKey:true,bubbles:true,cancelable:true}))""")
        time.sleep(0.6)
        check("shell.ctrl_e_opens_workspace", location_hash(window) == "#edit",
              f"(got {js(window, 'location.hash')!r})")
        check("shell.cube_active_on_edit",
              bool(js(window, "document.getElementById('cube-edit').classList.contains('active')")),
              "(a cube reads as selected when you are on its screen)")
        check("shell.logo_hidden_in_main_window",
              js(window, "getComputedStyle(document.getElementById('logo')).display") == "none",
              "(the wordmark is the pop-out's tell)")
        # getBBox(), not getBoundingClientRect(): the element box is whatever CSS
        # says regardless of what is drawn, so only the DRAWN geometry's bounds
        # can catch a path that silently collapsed to nothing
        cubes_box = js(window, """JSON.stringify(['cube-lib','cube-cam','cube-edit'].map(function(id){
            var b=document.getElementById(id), s=b && b.querySelector('svg');
            if(!s) return 0;
            try { var g=s.getBBox(); return Math.round(Math.min(g.width, g.height)); }
            catch (e) { return -1; }
        }))""")
        check("shell.cubes_render",
              all(v >= 8 for v in json.loads(cubes_box or "[0]")),
              f"(drawn bounds {cubes_box} of 24 user units — a collapsed path reads 0)")
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
              (js(window, "(document.querySelector('#cube-edit .cube-badge')||{}).textContent") or "") == "2")
        # the badge used to be written as the button's whole innerHTML, which
        # with an icon inside erases it on the first paint
        check("topbar.cube_icon_survives_badge",
              bool(js(window, "!!document.querySelector('#cube-edit svg')")))

        # ---- the sandbox bug: the working-set caret must COLLAPSE ----
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.25)
        after = js(window, "document.querySelectorAll('.ws-prog').length")
        check("rail.caret_collapses", after == 1, f"(2 -> {after})")
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.25)
        check("rail.caret_expands",
              js(window, "document.querySelectorAll('.ws-prog').length") == 2)

        # ---- the working set: highlight-selection, no checkboxes ----
        # removeEntries() asks native confirm for dirty entries, which would
        # hang a hidden window
        js(window, "window.confirm = function(){ return true; };")
        js(window, """document.querySelectorAll('.ws-prog')[0]
            .dispatchEvent(new MouseEvent('click',{bubbles:true}))""")
        time.sleep(0.3)
        check("rail.no_checkboxes",
              js(window, "document.querySelectorAll('.ws-prog input').length") == 0,
              "(selection is highlight, never a checkbox column)")
        check("rail.click_selects_one",
              js(window, "document.querySelectorAll('.ws-prog.sel').length") == 1
              and js(window, "document.querySelectorAll('.ws-prog.anchor').length") == 1)
        js(window, """document.querySelectorAll('.ws-prog')[1].dispatchEvent(
            new MouseEvent('click',{bubbles:true,ctrlKey:true}))""")
        time.sleep(0.3)
        check("rail.ctrl_click_adds",
              js(window, "document.querySelectorAll('.ws-prog.sel').length") == 2)
        head_txt = js(window, "document.querySelector('.ws-railhead').textContent") or ""
        check("rail.head_reports_selection", "2 of 2 selected" in head_txt, f"({head_txt!r})")
        js(window, """document.querySelectorAll('.ws-prog')[1].dispatchEvent(
            new MouseEvent('click',{bubbles:true,ctrlKey:true}))""")
        time.sleep(0.3)
        check("rail.ctrl_click_toggles_off",
              js(window, "document.querySelectorAll('.ws-prog.sel').length") == 1)
        # shift extends from the anchor, over the VISIBLE rows only. A plain
        # click first: ctrl+click moves the anchor too, so a range measured
        # after one would be a range of length 1.
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog');
            r[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));
            r[1].dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}));
        })()""")
        time.sleep(0.3)
        check("rail.shift_range",
              js(window, "document.querySelectorAll('.ws-prog.sel').length") == 2)
        # a folded group contributes no rows to a range, but keeps its selection
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.3)
        check("rail.folded_group_keeps_selection",
              "2 of 2 selected" in (js(window, "document.querySelector('.ws-railhead').textContent") or ""),
              "(hidden rows stay lit, and the head says so)")
        js(window, "document.querySelectorAll('.ws-robot-h')[0].click()")
        time.sleep(0.3)
        # clicking a row must NOT rebuild the rail, or the dblclick that opens
        # the program is eaten with the node it fired on
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog')[0];
            r.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            window._sameNode = (document.querySelectorAll('.ws-prog')[0] === r);
        })()""")
        check("rail.click_keeps_the_row_node", bool(js(window, "window._sameNode")),
              "(a rebuilt row eats the dblclick that opens the program)")

        # ---- the split is DERIVED: one pane until a program is dropped right ----
        check("panes.single_by_default",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1)
        check("panes.no_resizer_when_unsplit",
              js(window, "document.querySelectorAll('.ws-panes .ws-resizer').length") == 0)
        check("panes.no_split_toggle",
              not js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
                  .some(function(b){return b.textContent.trim()==='split';})"""),
              "(the split is opened by dragging, never armed by a button)")

        # ---- the navigator panel exists and can be hidden/shown ----
        check("nav.panel_present", bool(js(window, "!!document.querySelector('.ws-nav')")))
        js(window, "document.querySelector('.ws-navhide').click()")
        time.sleep(0.35)
        check("nav.hidden_leaves_stub",
              not js(window, "!!document.querySelector('.ws-nav')")
              and js(window, "document.querySelectorAll('.ws-stub').length") == 1)
        js(window, "document.querySelector('.ws-stub').click()")
        time.sleep(0.35)
        check("nav.reopens", bool(js(window, "!!document.querySelector('.ws-nav')")))
        js(window, "document.querySelector('.ws-hidetab').click()")
        time.sleep(0.35)
        check("rail.hides_too", not js(window, "!!document.querySelector('.ws-rail')"))
        js(window, "document.querySelector('.ws-stub').click()")
        time.sleep(0.4)
        check("rail.reopens", bool(js(window, "!!document.querySelector('.ws-rail')")))
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

        # ---- a LONG program scrolls inside the editor, never the window ----
        # regression: .ws-work went column for the diff dock and .ws-panes lost
        # its height clamp - a 400-line program then inflated the pane stack
        # and #view scrolled the whole workspace (rail and all) out of frame
        js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            var lines=[];
            for (var i=0;i<400;i++) lines.push('DO['+(20+i%9)+':Clamp '+(i%9)+']=ON');
            code.textContent=lines.join('\\n');
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        chain = js(window, """JSON.stringify((function(){
            var v=document.getElementById('view'), s=document.querySelector('.lsed-scroll');
            return {view:[v.scrollHeight,v.clientHeight],
                    scroller:[s.scrollHeight,s.clientHeight]};
        })())""")
        chain_d = json.loads(chain or "{}")
        check("scroll.window_contained",
              chain_d and chain_d["view"][0] - chain_d["view"][1] <= 2,
              f"({chain})")
        check("scroll.editor_scrolls_instead",
              chain_d and chain_d["scroller"][0] > chain_d["scroller"][1] + 100,
              "(the 400 lines must live in the editor's own scroller)")
        # one undo restores the seeded text exactly, so the buffer reads clean
        # for every later dirty/export check
        js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            code.dispatchEvent(new KeyboardEvent('keydown',
                {key:'z',ctrlKey:true,bubbles:true,cancelable:true}));
        })()""")
        time.sleep(0.4)
        check("scroll.undo_restores_clean",
              js(window, "BV.workspace.dirtyCount()") == 0,
              "(the growth was probe scaffolding, not an edit)")

        # ---- the drag decides the pane; an EDGE of a pane opens the split ----
        # order matters: there is nothing to split AGAINST until one program is
        # open, so the center drop runs first
        left_seen = drag_prog(window, 1, 0.30)
        time.sleep(0.6)
        check("panes.center_zone_no_split",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1
              and '"split":false' in (left_seen or ""), f"({left_seen})")
        check("panes.center_zone_lands_in_pane0",
              js(window, "document.querySelectorAll('.ws-pane')[0]"
                         ".querySelectorAll('.ws-tab').length") == 2)
        right_seen = drag_prog(window, 1, 0.94)
        time.sleep(0.6)
        check("panes.right_edge_paints_split_zone",
              '"split":true' in (right_seen or "") and '"left":"50%"' in (right_seen or "")
              and '"height":"100%"' in (right_seen or ""),
              f"({right_seen})")
        check("panes.two_after_drag_split",
              js(window, "document.querySelectorAll('.ws-pane').length") == 2)
        check("panes.resizer_between",
              js(window, "document.querySelectorAll('.ws-splitrz').length") == 1)
        check("panes.second_pane_has_tab",
              js(window, "document.querySelectorAll('.ws-pane')[1].querySelectorAll('.ws-tab').length") == 1)
        check("panes.two_editors",
              bool(poll(window, "document.querySelectorAll('.lsed-code').length === 2")),
              "(side-by-side)")

        # ---- closing the last tab of a pane folds the split away ----
        right_label = js(window, """(function(){
            var t=document.querySelectorAll('.ws-pane')[1].querySelector('.ws-tab');
            return t ? t.textContent.replace('✕','') : '';
        })()""")
        js(window, "document.querySelectorAll('.ws-pane')[0].querySelector('.ws-tab .x').click()")
        time.sleep(0.6)
        check("split.autocollapses_when_pane_empties",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1)
        now_label = js(window, """(function(){
            var t=document.querySelector('.ws-pane .ws-tab');
            return t ? t.textContent.replace('✕','') : '';
        })()""")
        # emptying the LEFT pane must slide the right one over, not leave a hole
        check("split.left_empty_slides_right_over", bool(now_label) and now_label == right_label,
              f"({right_label!r} survived as {now_label!r})")
        # activePane must come home, or the next double-click silently re-splits
        js(window, """document.querySelectorAll('.ws-prog')[0]
            .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))""")
        time.sleep(0.7)
        check("panes.activepane_comes_home",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1
              and js(window, "document.querySelectorAll('.ws-pane')[0]"
                             ".querySelectorAll('.ws-tab').length") == 2)

        # ---- one menu from the ⋯ button AND from right-click on the row ----
        js(window, """document.querySelectorAll('.ws-prog')[0].dispatchEvent(
            new MouseEvent('contextmenu',
                {bubbles:true,cancelable:true,clientX:60,clientY:120}))""")
        time.sleep(0.3)
        menu_txt = menu_text(window)
        check("menu.right_click_opens_row_menu", "rename" in menu_txt, f"({menu_txt[:90]!r})")
        check("menu.has_open_in_split", "open in split view" in menu_txt, f"({menu_txt[:90]!r})")
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('open in split')>=0;}).click()""")
        time.sleep(0.7)
        check("menu.open_in_split_splits",
              js(window, "document.querySelectorAll('.ws-pane').length") == 2,
              "(an already-open program MOVES to the other side)")
        # put it back so the undo test below starts from one pane, two tabs
        drag_tab(window, 1, 0, 0.30)
        time.sleep(0.7)

        # ---- undo must SURVIVE closing and reopening a tab ----
        # (the editor instance is cached and its DOM re-attached, so its history
        # is still there; a rebuilt editor would start with an empty stack)
        undo = js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            var before=code.textContent;
            code.textContent = before + '\\nUNDO ME';
            code.dispatchEvent(new Event('input',{bubbles:true}));
            var typed=document.querySelectorAll('.lsed-code')[0].textContent;
            /* close the tab we just edited, then reopen the same program */
            var pane=document.querySelectorAll('.ws-pane')[0];
            pane.querySelector('.ws-tab.active .x').click();
            return JSON.stringify({before:before, typed:typed});
        })()""")
        time.sleep(0.5)
        js(window, """(function(){
            var p0=document.querySelectorAll('.ws-pane')[0];
            p0.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            document.querySelectorAll('.ws-prog')[0]
                .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        time.sleep(0.7)
        reopened = js(window, "(document.querySelector('.lsed-code')||{}).textContent||''")
        check("undo.text_survived_close", "UNDO ME" in (reopened or ""),
              "(the edit itself must persist)")
        js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            code.focus();
            code.dispatchEvent(new KeyboardEvent('keydown',
                {key:'z',ctrlKey:true,bubbles:true,cancelable:true}));
        })()""")
        time.sleep(0.4)
        after_undo = js(window, "(document.querySelector('.lsed-code')||{}).textContent||''")
        check("undo.survives_tab_close", "UNDO ME" not in (after_undo or ""),
              f"({undo} -> after ctrl+z: {(after_undo or '')[-24:]!r})")

        # ---- the navigator reads the ACTIVE program, live from its text ----
        calls_txt = js(window, """(function(){
            return [...document.querySelectorAll('.ws-navrow')]
                .map(function(r){return r.textContent;}).join(' | ');
        })()""") or ""
        check("nav.lists_calls", "HOMEPOS" in calls_txt, f"({calls_txt[:70]!r})")
        check("nav.flags_missing_call",
              bool(js(window, "!!document.querySelector('.ws-navrow .nm.miss')")),
              "(HOMEPOS is not a program in this backup)")
        labels_txt = js(window, """(function(){
            var lab=[...document.querySelectorAll('.ws-navhead .seg button')]
                .filter(function(b){return b.textContent.trim()==='labels';})[0];
            if(lab) lab.click();
            return [...document.querySelectorAll('.ws-navrow')]
                .map(function(r){return r.textContent;}).join(' | ');
        })()""") or ""
        check("nav.lists_labels", "LBL[1]" in labels_txt, f"({labels_txt[:70]!r})")

        # ---- no duplicate tabs across panes ----
        # re-open the split (the auto-collapse test above folded it away)
        drag_prog(window, 1, 0.90)
        time.sleep(0.7)
        check("panes.resplit_by_drag",
              js(window, "document.querySelectorAll('.ws-pane').length") == 2)
        js(window, """(function(){
            var p1=document.querySelectorAll('.ws-pane')[1];
            p1.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            /* ask for a program that is ALREADY open in the left pane */
            document.querySelectorAll('.ws-prog')[0]
                .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        tabs_l = js(window, "document.querySelectorAll('.ws-pane')[0].querySelectorAll('.ws-tab').length")
        tabs_r = js(window, "document.querySelectorAll('.ws-pane')[1].querySelectorAll('.ws-tab').length")
        check("tabs.no_duplicate_across_panes", tabs_l == 1 and tabs_r == 1,
              f"(left {tabs_l}, right {tabs_r} — asking pane 1 for pane 0's program must focus, not copy)")

        # ---- the layout TREE: stacking, the pane cap, ctrl+w / ctrl+shift+t ----
        # scratch entries via duplicate() so the fixture stays untouched; they
        # are removed again at the end of this section (confirm stubbed - a
        # duplicate's buffer is unsaved by definition)
        js(window, "window.confirm = function(){ return true; }")
        js(window, """(function(){
            var e0=BV.workspace.entries()[0];
            window._t1=BV.workspace.duplicate(e0.id,'TREETMP1',false).id;
            window._t2=BV.workspace.duplicate(e0.id,'TREETMP2',false).id;
            window._t3=BV.workspace.duplicate(e0.id,'TREETMP3',false).id;
        })()""")
        js(window, "BV.route()")           # the rail rebuilds on render
        time.sleep(0.8)
        row_of = ("(function(nm){ return [...document.querySelectorAll('.ws-prog')]"
                  ".find(function(r){ return r.textContent.indexOf(nm)>=0; }); })")
        bottom_seen = js(window, _DRAG_JS % {
            "fx": 0.5, "fy": 0.94, "pane": 0, "src": row_of + "('TREETMP1')"})
        time.sleep(0.7)
        check("tree.bottom_edge_stacks",
              js(window, "document.querySelectorAll('.ws-pane').length") == 3
              and js(window, "document.querySelectorAll('.ws-split.col').length") >= 1
              and '"split":true' in (bottom_seen or "")
              and '"top":"50%"' in (bottom_seen or ""),
              f"({bottom_seen})")
        js(window, _DRAG_JS % {
            "fx": 0.94, "fy": 0.5, "pane": 2, "src": row_of + "('TREETMP2')"})
        time.sleep(0.7)
        check("tree.four_panes",
              js(window, "document.querySelectorAll('.ws-pane').length") == 4)
        # the FIFTH pane is refused: the drop still opens the program, but as
        # a tab of the target pane, never a fifth split
        js(window, _DRAG_JS % {
            "fx": 0.94, "fy": 0.5, "pane": 0, "src": row_of + "('TREETMP3')"})
        time.sleep(0.7)
        check("tree.pane_cap_holds",
              js(window, "document.querySelectorAll('.ws-pane').length") == 4)
        check("tree.capped_drop_still_opens",
              bool(js(window, """[...document.querySelectorAll('.ws-tab')]
                  .some(function(t){ return t.textContent.indexOf('TREETMP3')>=0; })""")))
        check("tree.details_btn_pinned_outside_scroll",
              js(window, "document.querySelectorAll('.ws-strip > .ws-detbtn').length") == 4,
              "(one per pane, outside the scrolling tab box)")
        # ctrl+w closes the ACTIVE pane's tab; ctrl+shift+t brings it back
        js(window, """(function(){
            var tmp=[...document.querySelectorAll('.ws-tab')]
                .find(function(t){ return t.textContent.indexOf('TREETMP3')>=0; });
            tmp.click();
        })()""")
        time.sleep(0.5)
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown',
            {key:'w',ctrlKey:true,bubbles:true,cancelable:true}))""")
        time.sleep(0.5)
        check("tree.ctrl_w_closes_active_tab",
              not js(window, """[...document.querySelectorAll('.ws-tab')]
                  .some(function(t){ return t.textContent.indexOf('TREETMP3')>=0; })"""))
        js(window, """document.dispatchEvent(new KeyboardEvent('keydown',
            {key:'T',ctrlKey:true,shiftKey:true,bubbles:true,cancelable:true}))""")
        time.sleep(0.5)
        check("tree.ctrl_shift_t_reopens",
              bool(js(window, """[...document.querySelectorAll('.ws-tab')]
                  .some(function(t){ return t.textContent.indexOf('TREETMP3')>=0; })""")))
        # cleanup: scratch entries out, back to the two MAINs side by side
        js(window, """(function(){
            [window._t1, window._t2, window._t3].forEach(function(id){
                var tab=[...document.querySelectorAll('.ws-tab')].find(function(t){
                    return t.textContent.indexOf('TREETMP')>=0; });
                if (tab) tab.querySelector('.x').click();
                BV.workspace.remove(id);
            });
        })()""")
        js(window, "BV.route()")
        time.sleep(0.8)
        panes_left = js(window, "document.querySelectorAll('.ws-pane').length")
        check("tree.cleanup_back_to_two_panes",
              panes_left == 2 and js(window, "BV.workspace.count()") == 2,
              f"({panes_left} panes, {js(window, 'BV.workspace.count()')} entries)")

        # ---- the inline pane diff: pick two, the EDITORS carry the marks ----
        # collapse to ONE pane first, so pick-two has to build the pair view
        # itself - park + restore is the point
        drag_tab(window, 1, 0, 0.5, pane=0)
        time.sleep(0.6)
        check("pdiff.collapsed_to_one_pane",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1)
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(x){return x.textContent==='diff…';}).click()""")
        time.sleep(0.3)
        hint = js(window, "(document.querySelector('.ws-armhint')||{}).textContent||''")
        check("pdiff.arming_hint", "pick two" in (hint or ""), f"({hint!r})")
        check("pdiff.tabs_marked_pickable",
              js(window, "document.querySelectorAll('.ws-tab.pickable').length") == 2)
        # ONE click per pick - activating the pane first must not eat it
        js(window, "document.querySelectorAll('.ws-tab')[0].click()")
        time.sleep(0.3)
        js(window, "document.querySelectorAll('.ws-tab')[1].click()")
        got = poll(window, "document.querySelectorAll('.ws-pane').length === 2")
        check("pdiff.pair_view_presents_side_by_side", bool(got))
        badges = js(window, """[...document.querySelectorAll('.ws-side')]
            .map(function(b){return b.textContent;}).join('')""")
        check("pdiff.side_badges_match_pills", badges == "ab", f"({badges!r})")
        stats_txt = poll(window, """(function(){
            var s=document.querySelector('.ws-diffstats');
            return s && s.textContent.indexOf('identical')>=0 ? s.textContent : '';
        })()""")
        check("pdiff.stats_ride_the_toolbar", "8 identical" in (stats_txt or ""),
              f"({stats_txt!r} — two robots' pristine MAIN.LS are byte-identical)")
        check("pdiff.identical_no_marks",
              js(window, "document.querySelectorAll('.wsd-bar').length") == 0
              and js(window, "document.querySelectorAll('.lsed-gap').length") == 0)
        # diff ✕ restores the PARKED single-pane layout exactly
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(x){return x.textContent==='diff ✕';}).click()""")
        time.sleep(0.5)
        check("pdiff.close_restores_parked_layout",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1
              and js(window, "document.querySelectorAll('.ws-pane .ws-tab').length") == 2
              and js(window, "document.querySelectorAll('.ws-side').length") == 0)
        # re-split and diff the two panes in place (already the pair: no park)
        drag_tab(window, 0, 1, 0.94)
        time.sleep(0.6)
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(x){return x.textContent==='diff…';}).click()""")
        time.sleep(0.3)
        js(window, "document.querySelectorAll('.ws-pane')[0].querySelector('.ws-tab').click()")
        time.sleep(0.3)
        js(window, "document.querySelectorAll('.ws-pane')[1].querySelector('.ws-tab').click()")
        poll(window, """(function(){
            var s=document.querySelector('.ws-diffstats');
            return s && s.textContent.indexOf('identical')>=0;
        })()""")

        # ---- edit one program: dirty must reach the rail + badge ----
        js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            code.textContent = code.textContent + '\\nCALL ADDED';
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.4)
        check("edit.workspace_dirty", bool(js(window, "BV.workspace.anyDirty()")))
        check("edit.rail_dot", bool(js(window, "!!document.querySelector('.ws-prog .dot.dirty')")))
        check("topbar.badge_dirty_tint",
              bool(js(window, "document.querySelector('#cube-edit .cube-badge').classList.contains('dirty')")),
              "(unsaved work is visible from every screen)")
        check("edit.export_btn_enabled",
              not js(window, "document.querySelector('.toolbar-slot .btn.primary').disabled"))

        # ---- the diff recomputes as you type; marks live IN the editors ----
        a_only_bars = poll(window, "document.querySelectorAll('.wsd-bar.only').length")
        check("pdiff.typed_line_marked_in_editor", a_only_bars == 1,
              f"({a_only_bars} one-side bars)")
        check("pdiff.other_side_gets_real_gap",
              js(window, "document.querySelectorAll('.lsed-code .lsed-gap').length") == 1,
              "(the counterpart shows a blank row, not a squeezed-up line)")
        heights = js(window, """JSON.stringify([...document.querySelectorAll('.lsed-scroll')]
            .map(function(s){return s.scrollHeight;}))""")
        hh = json.loads(heights or "[0,1]")
        check("pdiff.gap_equalizes_heights", len(hh) == 2 and hh[0] == hh[1],
              f"({heights} — row-for-row alignment is the whole point)")
        # identity honesty: a ref COMMENT difference must read display-only -
        # the same instruction saved under different comment-table state
        js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            code.textContent = code.textContent.replace('R[21]=2','R[21:WORKCMT]=2');
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        equiv = poll(window, "document.querySelectorAll('.wsd-bar.equiv').length")
        check("pdiff.ref_comment_is_display_only", equiv == 2,
              f"({equiv} — one faint bar per side; a naive text diff would call it changed)")
        check("pdiff.no_false_change",
              js(window, "document.querySelectorAll('.wsd-bar.change').length") == 0)
        stats_txt = js(window, "(document.querySelector('.ws-diffstats')||{}).textContent||''")
        check("pdiff.stats_report_display_only", "display-only" in (stats_txt or ""),
              f"({stats_txt[:60]!r})")
        # the scroll LOCK is a plain mirror - but the fixture programs are 8
        # lines, so grow side A first until both sides actually overflow (the
        # gap mirrors the growth onto side B); one undo puts it all back
        js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            var lines=[];
            for (var i=0;i<200;i++) lines.push('DO['+(20+i%9)+':Clamp]=ON');
            code.textContent = code.textContent + '\\n' + lines.join('\\n');
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        grown = poll(window, """(function(){
            var s=document.querySelectorAll('.lsed-scroll');
            return s.length === 2 && s[1].scrollHeight > s[1].clientHeight + 100;
        })()""")
        check("pdiff.growth_mirrored_by_gap", bool(grown),
              "(200 typed lines must swell BOTH sides via the alignment gap)")
        mirror = js(window, """(function(){
            var s=[...document.querySelectorAll('.lsed-scroll')];
            if (s.length < 2) return 'scrollers:'+s.length+' panes:'+
                document.querySelectorAll('.ws-pane').length+' codes:'+
                document.querySelectorAll('.lsed-code').length;
            s[0].scrollTop=60;
            s[0].dispatchEvent(new Event('scroll'));
            return JSON.stringify([s[0].scrollTop, s[1].scrollTop]);
        })()""")
        try:
            mm = json.loads(mirror or "[0,1]")
        except ValueError:
            mm = [None, None]
        check("pdiff.scroll_locked", mm[0] == 60 and mm[1] == 60, f"({mirror})")
        back = js(window, """(function(){
            var s=[...document.querySelectorAll('.lsed-scroll')];
            if (s.length < 2) return 'scrollers:'+s.length;
            s[1].scrollTop=0;
            s[1].dispatchEvent(new Event('scroll'));
            return 'ok';
        })()""")
        time.sleep(0.2)
        check("pdiff.lock_works_both_ways",
              back == "ok"
              and js(window, "document.querySelectorAll('.lsed-scroll')[0].scrollTop") == 0,
              f"({back})")
        # one undo takes the growth back out; the diff re-settles
        js(window, """(function(){
            var code=document.querySelectorAll('.lsed-code')[0];
            code.dispatchEvent(new KeyboardEvent('keydown',
                {key:'z',ctrlKey:true,bubbles:true,cancelable:true}));
        })()""")
        poll(window, """(function(){
            var g=document.querySelectorAll('.lsed-code .lsed-gap');
            return g.length === 1 && g[0].style.height === '1lh';
        })()""")
        # ↓ next jumps to a difference and flashes it
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(x){return x.title==='next difference';}).click()""")
        time.sleep(0.3)
        check("pdiff.jump_flashes",
              js(window, "document.querySelectorAll('.lsed-scroll .flashbar').length") >= 1)

        # ---- review-your-edits: original vs edited, every kind of change ----
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(x){return x.textContent==='review…';}).click()""")
        got = poll(window, "!!document.querySelector('.rv-modal')")
        check("review.modal_opens", bool(got))
        cards = poll(window, "document.querySelectorAll('.rv-card').length")
        sum_txt = js(window, "(document.querySelector('.rv-sum')||{}).textContent||''")
        check("review.only_changed_programs_get_cards", cards == 1, f"({cards} cards)")
        check("review.summary_counts", "1 of 2 programs changed" in (sum_txt or ""),
              f"({sum_txt!r})")
        check("review.added_line_is_b_only",
              js(window, "document.querySelectorAll('.rv-card .pd-b_only').length") == 1,
              "(the typed line exists only on the edited side)")
        # raw mode: same source, same save-time state - here the comment edit
        # IS an edit, and hiding it would be the lie
        check("review.comment_edit_visible_raw",
              js(window, "document.querySelectorAll('.rv-card .pd-change').length") == 1)
        heads = js(window, "(document.querySelector('.rv-card .pd-head')||{}).textContent||''")
        check("review.heads_original_vs_edited",
              "original (backup)" in (heads or "") and "edited" in (heads or ""), f"({heads!r})")
        check("review.unchanged_listed",
              "unchanged:" in (js(window, "document.querySelector('.rv-body').textContent") or ""),
              "(clean programs are listed, not silently dropped)")
        js(window, """document.getElementById('modal-root').dispatchEvent(
            new MouseEvent('mousedown',{bubbles:true}))""")
        time.sleep(0.4)
        check("review.backdrop_closes", not js(window, "BV.modalOpen()"))

        # a clean single program answers plainly (the rail's ⋯ menu path)
        js(window, """document.querySelectorAll('.ws-prog')[1].dispatchEvent(
            new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:60,clientY:140}))""")
        time.sleep(0.35)
        check("review.menu_item_present", "review changes" in menu_text(window))
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('review changes')>=0;}).click()""")
        poll(window, "!!document.querySelector('.rv-modal')")
        clean_txt = poll(window, "(document.querySelector('.rv-card')||{}).textContent||''")
        check("review.clean_program_says_so",
              "no changes — matches the backup" in (clean_txt or ""),
              f"({(clean_txt or '')[-60:]!r})")
        js(window, """document.getElementById('modal-root').dispatchEvent(
            new MouseEvent('mousedown',{bubbles:true}))""")
        time.sleep(0.4)

        # ---- diff mode LOCKS the layout; closing a diffed tab ends it ----
        # an unopened program refuses to open while the diff is on
        js(window, """(function(){
            var e0=BV.workspace.entries()[0];
            window._t4=BV.workspace.duplicate(e0.id,'TREETMP4',false).id;
        })()""")
        js(window, "BV.route()")
        time.sleep(0.8)
        check("pdiff.survives_reroute",
              js(window, "document.querySelectorAll('.ws-side').length") == 2
              and "display-only" in (js(window,
                  "(document.querySelector('.ws-diffstats')||{}).textContent||''") or ""),
              "(module state + repainted stats, not DOM state)")
        js(window, """(function(){
            var r=[...document.querySelectorAll('.ws-prog')].find(function(x){
                return x.textContent.indexOf('TREETMP4')>=0; });
            r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        check("pdiff.layout_locked_while_on",
              js(window, "document.querySelectorAll('.ws-tab').length") == 2,
              "(opening anything says 'close the diff first' instead of reshaping)")
        js(window, "BV.workspace.remove(window._t4)")
        # closing one of the diff's OWN tabs clears the marks with the diff
        js(window, "document.querySelectorAll('.ws-pane')[1].querySelector('.ws-tab .x').click()")
        time.sleep(0.5)
        check("pdiff.closing_diffed_tab_clears",
              js(window, "document.querySelectorAll('.ws-side').length") == 0
              and js(window, "document.querySelectorAll('.wsd-bar').length") == 0
              and js(window, "document.querySelectorAll('.lsed-gap').length") == 0
              and bool(js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
                  .some(function(x){return x.textContent==='diff…';})""")))
        # back to two panes for the find/replace flow below: the closed side
        # reopens from the rail (allowed again - the diff is off), then splits
        js(window, """document.querySelectorAll('.ws-prog')[1]
            .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))""")
        time.sleep(0.6)
        drag_tab(window, 0, 1, 0.94)
        time.sleep(0.6)
        check("pdiff.state_restored_for_find",
              js(window, "document.querySelectorAll('.ws-pane').length") == 2)

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
        check("find.no_duplicate_scope_row",
              not js(window, "!!document.querySelector('.fp-scope')"),
              "(the robot row's own box IS the scope now)")
        # options a text editor is expected to have
        check("find.has_case_and_word_options", bool(js(window, """(function(){
            var t=[...document.querySelectorAll('.fp-opt')].map(function(o){return o.textContent;}).join('|');
            return t.indexOf('match case')>=0 && t.indexOf('whole word')>=0;
        })()""")))
        # a program NAME match is found and marked (navigational, not replaceable)
        js(window, """(function(){
            var i=document.querySelector('.fp-inputs input');
            i.value='MAIN'; i.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        check("find.matches_program_names",
              bool(js(window, "!!document.querySelector('.fp-pg .pill')")),
              "(MAIN matches the file name, badged 'name')")
        js(window, """(function(){
            var i=document.querySelector('.fp-inputs input');
            i.value='R[21]'; i.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.5)

        # ---- BUG: collapsing must not tick boxes, and one result = one box ----
        sel_before = js(window, "document.querySelector('.fp-foot .dim').textContent")
        js(window, """(function(){
            var cbs=document.querySelectorAll('.fp-ln input[type=checkbox]');
            cbs[0].checked=false; cbs[0].click();   /* untick one line by hand */
        })()""")
        time.sleep(0.3)
        after_untick = js(window, "document.querySelector('.fp-foot .dim').textContent")
        js(window, "document.querySelector('.fp-rb .caret').click()")
        time.sleep(0.3)
        folded_txt = js(window, "document.querySelector('.fp-foot .dim').textContent")
        check("find.collapse_keeps_selection", folded_txt == after_untick,
              f"(before {sel_before!r} -> untick {after_untick!r} -> folded {folded_txt!r})")
        js(window, "document.querySelector('.fp-rb .caret').click()")
        time.sleep(0.3)
        # a robot with a single hit must not stack robot+program+line boxes
        check("find.no_redundant_boxes", bool(js(window, """(function(){
            var rows=[...document.querySelectorAll('.fp-rb,.fp-pg,.fp-ln')];
            var bad=0;
            rows.forEach(function(r){
              if(!r.classList.contains('fp-ln')){
                var n=+((r.querySelector('.dim')||{}).textContent||'0');
                var hasBox=!!r.querySelector('input[type=checkbox]');
                if(n===1 && hasBox) bad++;   /* a single-hit group needs no box */
              }
            });
            return bad===0;
        })()""")), "(single-hit groups render no group checkbox)")
        # clicking a result opens it and flashes the line, WITHOUT closing find
        js(window, "document.querySelectorAll('.fp-ln')[0].click()")
        time.sleep(0.5)
        flash_dx = js(window, """JSON.stringify({
            bar: !!document.querySelector('.flashbar'),
            scrollers: document.querySelectorAll('.lsed-scroll').length,
            editors: document.querySelectorAll('.lsed-code').length,
            tabs: document.querySelectorAll('.ws-tab').length })""")
        check("find.click_flashes_line",
              bool(js(window, "!!document.querySelector('.flashbar')")), str(flash_dx))
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

        # ---- a rename alone is an exportable change (pending, not dirty) ----
        js(window, "window._rn = BV.workspace.rename(BV.workspace.entries()[0].id, 'PENDINGCHK')")
        time.sleep(0.5)
        check("rename.pending_enables_export",
              bool(js(window, "window._rn"))
              and js(window, "BV.workspace.pendingCount()") == 1
              and not js(window, "BV.workspace.anyDirty()")
              and not js(window, "document.querySelector('.toolbar-slot .btn.primary').disabled"),
              "(a rename-only workspace used to be silently unexportable)")
        check("rename.badge_tints_for_pending",
              bool(js(window, "document.querySelector('#cube-edit .cube-badge').classList.contains('dirty')")))
        js(window, "BV.workspace.rename(BV.workspace.entries()[0].id, '')")
        time.sleep(0.5)
        check("rename.clear_disables_export",
              bool(js(window, "document.querySelector('.toolbar-slot .btn.primary').disabled"))
              and js(window, "BV.workspace.pendingCount()") == 0)

        # ---- THE trust contract ----
        for robot in ROBOTS:
            check(f"backup.{robot}_untouched",
                  (SNAPS[robot] / PROG).read_bytes() == PROG_BYTES)

        # ---- tabs: x closes, ctrl+tab cycles ----
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('working')>=0;}).click()""")
        time.sleep(0.3)
        # dragging the right pane's only tab back to the LEFT zone moves it and
        # folds the split away - which also gives us two tabs in one pane
        drag_tab(window, 1, 0, 0.30)
        time.sleep(0.7)
        check("split.folds_when_tab_dragged_back",
              js(window, "document.querySelectorAll('.ws-pane').length") == 1)
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
        # close the last one too: with nothing open there is nothing to split
        # AGAINST, so the item is omitted rather than shown dead (BV.menu has no
        # disabled state)
        js(window, "document.querySelectorAll('.ws-pane')[0].querySelector('.ws-tab .x').click()")
        time.sleep(0.4)
        js(window, """document.querySelectorAll('.ws-prog')[0].dispatchEvent(
            new MouseEvent('contextmenu',
                {bubbles:true,cancelable:true,clientX:60,clientY:120}))""")
        time.sleep(0.3)
        lone_menu = menu_text(window)
        check("menu.no_split_item_with_nothing_open",
              "rename" in lone_menu and "open in split view" not in lone_menu,
              f"({lone_menu[:90]!r})")
        close_menu(window)

        # ---- the workspace survives a route away and back ----
        js(window, "location.hash = '#home'")
        time.sleep(0.6)
        js(window, "location.hash = '#edit'")
        time.sleep(0.8)
        check("persist.set_survives_navigation",
              js(window, "document.querySelectorAll('.ws-prog').length") == 2)

        # ---- rename / duplicate / new-empty, and rename-by-replace ----
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('working')>=0;}).click()""")
        time.sleep(0.35)
        n_entries = js(window, "BV.workspace.count()")
        # duplicate the first entry under a new name (via the state API - the
        # row menu path is exercised by the rename below)
        made = js(window, """(function(){
            var e=BV.workspace.entries()[0];
            var d=BV.workspace.duplicate(e.id,'MAIN_COPY',false);
            return d ? d.id !== e.id && d.saveAs==='MAIN_COPY' : false;
        })()""")
        check("dup.creates_independent_entry", bool(made))
        check("dup.count_grew", js(window, "BV.workspace.count()") == n_entries + 1)
        check("dup.same_source_allowed", bool(js(window, """(function(){
            var es=BV.workspace.entries();
            return es[0].file === es[es.length-1].file &&
                   es[0].root === es[es.length-1].root;
        })()""")), "(a duplicate shares the source, differing only by name)")
        check("dup.refuses_taken_name", not js(window, """(function(){
            var e=BV.workspace.entries()[0];
            return !!BV.workspace.duplicate(e.id,'MAIN_COPY',false);
        })()""")),
        check("dup.refuses_bad_name", not js(window, """(function(){
            var e=BV.workspace.entries()[0];
            return !!BV.workspace.duplicate(e.id,'9bad',false);
        })()"""))
        # a new EMPTY program keeps a real header but starts blank
        js(window, """window._new='';
            (function(){
              var e=BV.workspace.entries()[0];
              var d=BV.workspace.duplicate(e.id,'BRANDNEW',true);
              window._new = d ? d.id : 'no';
            })()""")
        newid = poll(window, "window._new")
        check("new.empty_entry_made", isinstance(newid, str) and newid != "no")

        # rename via find/replace: a ticked name match renames on export
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('find')>=0;}).click()""")
        time.sleep(0.4)
        js(window, """(function(){
            var ins=document.querySelectorAll('.fp-inputs input');
            ins[0].value='BRANDNEW'; ins[0].dispatchEvent(new Event('input',{bubbles:true}));
            ins[1].value='RENAMEDBYFR'; ins[1].dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.6)
        check("rename_fr.name_hit_has_box", bool(js(window, """(function(){
            var rows=[...document.querySelectorAll('.fp-pg')];
            return rows.some(function(r){
              return r.querySelector('.pill') && r.querySelector('input[type=checkbox]');
            });
        })()""")), "(a name match is actionable, so it must be tickable)")
        js(window, """(function(){
            var b=[...document.querySelectorAll('.fp-foot .btn')].pop();
            if (b && !b.disabled) b.click();
        })()""")
        time.sleep(0.6)
        check("rename_fr.applied", bool(js(window, """(function(){
            return BV.workspace.entries().some(function(e){ return e.saveAs==='RENAMEDBYFR'; });
        })()""")), "(replacing a name match sets the export rename)")

        # exporting a renamed program writes the new file with a patched /PROG
        js(window, """window._exp2='';
            BV.api.call('ws_export', BV.workspace.edits(), %s).then(function(r){
                window._exp2 = JSON.stringify(r);
            }, function(e){ window._exp2 = 'err:' + (e.code||e.message); });""" %
           json.dumps(str(_TMP / "export2")))
        exp2 = poll(window, "window._exp2")
        check("rename_fr.export_ok", isinstance(exp2, str) and exp2.startswith("{"), f"({exp2!r})")
        renamed_out = list((_TMP / "export2").rglob("RENAMEDBYFR.LS"))
        check("rename_fr.file_written", bool(renamed_out))
        if renamed_out:
            check("rename_fr.prog_header_patched",
                  b"/PROG  RENAMEDBYFR" in renamed_out[0].read_bytes())
        # and the duplicate landed under its own name alongside the original
        check("dup.exported_beside_original",
              bool(list((_TMP / "export2").rglob("MAIN_COPY.LS"))))

        # ---- acting on a SELECTION: the multi menu and the Delete key ----
        js(window, """[...document.querySelectorAll('.ws-railtab')]
            .find(function(t){return t.textContent.indexOf('working')>=0;}).click()""")
        time.sleep(0.4)
        n_rows = js(window, "document.querySelectorAll('.ws-prog').length")
        check("sel.rows_to_work_with", n_rows >= 3, f"(got {n_rows})")
        # right-clicking OUTSIDE the selection means "I mean this one"
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog');
            r[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));
            r[1].dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}));
            r[2].dispatchEvent(new MouseEvent('contextmenu',
                {bubbles:true,cancelable:true,clientX:60,clientY:200}));
        })()""")
        time.sleep(0.3)
        out_menu = menu_text(window)
        out_sel = js(window, "document.querySelectorAll('.ws-prog.sel').length")
        check("sel.right_click_outside_reselects",
              out_sel == 1 and "rename" in out_menu,
              f"({out_sel} lit, {out_menu[:70]!r})")
        close_menu(window)
        # right-clicking INSIDE a multi-selection offers the set's own menu
        js(window, """(function(){
            var r=document.querySelectorAll('.ws-prog');
            r[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));
            r[1].dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}));
            r[0].dispatchEvent(new MouseEvent('contextmenu',
                {bubbles:true,cancelable:true,clientX:60,clientY:120}));
        })()""")
        time.sleep(0.3)
        multi_menu = menu_text(window)
        check("sel.multi_menu_counts", "remove 2 programs" in multi_menu, f"({multi_menu[:90]!r})")
        check("sel.multi_menu_has_no_single_items", "rename" not in multi_menu,
              "(rename and duplicate have no sane bulk form)")
        close_menu(window)
        # Delete belongs to whatever is being typed in first
        js(window, """document.querySelectorAll('.ws-prog')[0]
            .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))""")
        poll(window, "!!document.querySelector('.lsed-code')")
        n_before = js(window, "BV.workspace.count()")
        js(window, """(function(){
            document.querySelectorAll('.ws-prog')[2]
                .dispatchEvent(new MouseEvent('click',{bubbles:true}));
            document.querySelector('.lsed-code').focus();
            document.dispatchEvent(new KeyboardEvent('keydown',
                {key:'Delete',bubbles:true,cancelable:true}));
        })()""")
        time.sleep(0.4)
        check("sel.delete_ignored_while_typing",
              js(window, "BV.workspace.count()") == n_before,
              "(Delete in the editor must never reach the rail)")
        js(window, """(function(){
            document.activeElement.blur();
            document.dispatchEvent(new KeyboardEvent('keydown',
                {key:'Delete',bubbles:true,cancelable:true}));
        })()""")
        time.sleep(0.5)
        check("sel.delete_removes_selection",
              js(window, "BV.workspace.count()") == n_before - 1,
              f"({n_before} -> {js(window, 'BV.workspace.count()')})")
        check("sel.head_back_to_plain_count",
              "programs" in (js(window, "document.querySelector('.ws-railhead').textContent") or ""),
              "(nothing selected: the head stops reporting a selection)")

        # ---- the programs list: pick programs one at a time or in chunks ----
        # needs a backup OPEN (the list is session-backed), so this runs last
        js(window, """window._ob='';
            BV.api.call('open_backup', %s).then(function(m){
                BV.session.open(m); BV.state.setManifest(m); window._ob='ok';
            }, function(e){ window._ob='err:'+(e.code||e.message); });""" %
           json.dumps(str(SNAPS[ROBOTS[0]])))
        check("list.backup_opened", poll(window, "window._ob") == "ok")
        js(window, "location.hash = '#programs'")
        poll(window, "!!document.querySelector('.btn-split .bs-main')")
        # selecting is OFF by default: no checkbox column at all
        check("list.pick_column_hidden_by_default",
              js(window, "document.querySelectorAll('.vt-pick').length") == 0,
              "(the list stays a list until you ask to select)")
        check("list.add_disabled_at_zero",
              bool(js(window, "document.querySelector('.btn-split .bs-main').disabled")))
        js(window, "document.querySelector('.btn-split .bs-toggle').click()")
        boxes = poll(window, "document.querySelectorAll('.vt-pick').length")
        check("list.pick_toggle_shows_column", bool(boxes) and boxes >= 1, f"({boxes} checkboxes)")
        check("list.pick_col_not_sortable",
              not js(window, """document.querySelectorAll('.vt-head .vt-cell')[0]
                  .classList.contains('sortable')"""),
              "(sorting by a non-field scrambled the list)")
        # a real click toggles the box itself - pre-setting .checked then
        # clicking would toggle it straight back off
        count_before_add = js(window, "BV.workspace.count()")
        js(window, "document.querySelectorAll('.vt-pick')[0].click()")
        time.sleep(0.35)
        label = js(window, "document.querySelector('.btn-split .bs-main').textContent")
        check("list.tick_updates_button", "(1)" in (label or ""), f"({label!r})")
        check("list.tick_did_not_navigate", location_hash(window) == "#programs",
              "(a ticked box must not open the program)")
        # one tick means ONE: a stacked capture listener would toggle it twice
        check("list.single_capture_listener",
              js(window, "document.querySelectorAll('.vt-pick:checked').length") == 1,
              "(a doubled handler ticks and unticks in the same click)")
        # the header box tracks "is every shown row picked?" and toggles them
        shown = js(window, "BV.currentVTable.view.filter(function(r){return r.rel && !r.binary;}).length")
        js(window, "document.querySelector('.vt-pickall').click()")
        time.sleep(0.4)
        cleared = js(window, "document.querySelector('.btn-split .bs-main').textContent")
        js(window, "document.querySelector('.vt-pickall').click()")
        time.sleep(0.4)
        all_label = js(window, "document.querySelector('.btn-split .bs-main').textContent")
        check("list.select_all_header",
              ("(" + str(shown) + ")") in (all_label or "") and cleared == "+ workspace",
              f"(off {cleared!r} -> on {all_label!r}, {shown} pickable rows)")
        # adding consumes the ticks (the button goes back to its idle label)
        js(window, "document.querySelector('.btn-split .bs-main').click()")
        time.sleep(0.5)
        after = js(window, "document.querySelector('.btn-split .bs-main').textContent")
        check("list.add_clears_ticks", after == "+ workspace", f"({after!r})")
        check("list.add_disables_again",
              bool(js(window, "document.querySelector('.btn-split .bs-main').disabled")))
        check("list.no_duplicate_entry",
              js(window, "BV.workspace.count()") == count_before_add,
              "(that source was already in the working set - a second add is a no-op)")
        # turning selecting back off takes the column and the ticks with it
        js(window, "document.querySelectorAll('.vt-pick')[0].click()")
        time.sleep(0.3)
        js(window, "document.querySelector('.btn-split .bs-toggle').click()")
        time.sleep(0.4)
        check("list.toggle_off_clears",
              js(window, "document.querySelectorAll('.vt-pick').length") == 0
              and bool(js(window, "document.querySelector('.btn-split .bs-main').disabled")))

        # ---- right-click a program row: add WITHOUT being thrown to #edit ----
        js(window, """(function(){
            var r=document.querySelector('.vt-row');
            var b=r.getBoundingClientRect();
            r.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,
                clientX:b.left+20, clientY:b.top+5}));
        })()""")
        time.sleep(0.35)
        ctx = menu_text(window)
        check("list.row_context_menu", "add to workspace" in ctx, f"({ctx[:70]!r})")
        check("list.context_selects_row",
              bool(js(window, "!!document.querySelector('.vt-row.selected')")),
              "(the menu and the highlight must point at the same row)")
        n_ws = js(window, "BV.workspace.count()")
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('add to workspace')>=0;}).click()""")
        time.sleep(0.5)
        check("list.context_add_does_not_jump", location_hash(window) == "#programs",
              "(add only - the topbar is how you go to the workspace)")
        check("list.context_add_worked",
              js(window, "BV.workspace.count()") >= n_ws)

        # ---- lsEditor alignment gaps: the component contract, in isolation ----
        # A scratch instance in its own host: gaps are pixels, never text.
        gap_out = js(window, """(function(){
            var host=document.createElement('div');
            host.style.cssText='position:fixed;left:0;top:0;width:400px;height:220px;visibility:hidden';
            document.body.appendChild(host);
            var ed=BV.lsEditor(host,{text:'DO[1:A]=ON\\nDO[2:B]=ON\\nDO[3:C]=ON\\nDO[4:D]=ON'});
            var out={};
            var lh=parseFloat(getComputedStyle(ed.code).lineHeight)||16;
            var t0=ed.lineTop(3);
            ed.setGaps([{after:2,n:3}]);
            out.lineTopGrew=Math.round(ed.lineTop(3)-t0)===Math.round(3*lh);
            out.codeGap=(function(g){return g?g.style.height:''})(ed.code.querySelector('.lsed-gap'));
            out.gutterGap=!!ed.el.querySelector('.lsed-nums .lsed-gap');
            out.textPure=ed.getText()==='DO[1:A]=ON\\nDO[2:B]=ON\\nDO[3:C]=ON\\nDO[4:D]=ON';
            /* backspace AT the gap boundary (start of line 3) must eat the
               newline in text space, not the gap block */
            ed.focusLine(3);
            ed.code.dispatchEvent(new KeyboardEvent('keydown',
                {key:'Backspace',bubbles:true,cancelable:true}));
            var t=ed.getText().split('\\n');
            out.boundaryMerged=t.length===3 && t[1]==='DO[2:B]=ONDO[3:C]=ON';
            /* undo restores, gaps still drawn */
            ed.code.dispatchEvent(new KeyboardEvent('keydown',
                {key:'z',ctrlKey:true,bubbles:true,cancelable:true}));
            out.undoRestored=ed.getText().split('\\n').length===4;
            out.gapSurvivedUndo=!!ed.code.querySelector('.lsed-gap');
            ed.setGaps([]);
            out.cleared=!ed.el.querySelector('.lsed-gap');
            host.remove();
            return JSON.stringify(out);
        })()""")
        g = json.loads(gap_out or "{}")
        check("gaps.line_top_gap_aware", g.get("lineTopGrew") is True)
        check("gaps.code_spacer_in_lh", g.get("codeGap") == "3lh", f"({g.get('codeGap')!r})")
        check("gaps.gutter_mirrors", g.get("gutterGap") is True)
        check("gaps.never_in_text", g.get("textPure") is True)
        check("gaps.boundary_backspace_eats_newline", g.get("boundaryMerged") is True,
              "(Chromium's atomic island delete would read as a dead key)")
        check("gaps.undo_restores_text", g.get("undoRestored") is True)
        check("gaps.survive_undo_repaint", g.get("gapSurvivedUndo") is True)
        check("gaps.clear_removes_all", g.get("cleared") is True)

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
