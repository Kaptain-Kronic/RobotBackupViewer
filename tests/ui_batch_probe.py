"""Hidden-window probe for the 2026-07 UI batch: library favorites, the
generalized edit modal + libTree link picker, the photos-tab rework
(filtered/raw, report pane, moved filter row, zoom/pan fullscreen), the
camera-backup backspace trap, the -/= tab hotkeys, and the multi-cam lens
post-merge fix batch (manage reachable in cam mode, per-lens scroll memory,
setCount blanking, linked-robot filter match, hidden-aware empty state,
fold policy, keyboard tiles, home_view persistence across a remount).

Fully synthetic and identifier-clean: builds its own library tree in a temp
folder and redirects APPDATA there BEFORE importing the app, so the real
settings/library are never touched — camera entries use unroutable TEST-NET
(192.0.2.x) IPs only, so nothing real is ever probed.
Run: python tests/ui_batch_probe.py
"""
import json
import sys
import time
from pathlib import Path

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402


# --- synthetic library tree (identifier-clean: RB/CELL fakes, TEST-NET IPs) ---

SIDECAR_TXT = """Camera
Camera Name: CELL-01RB010-R01CAM01
Host Name: gtx000000
IP Address: 192.0.2.161
Camera Type: Matrox GTX2000
Software Version: 9.1.54
Project Name: SAMPLEPROJ_9_1

Inspection
Image Time Stamp: 2026/07/07 {ts}
Overall Pass or Fail: {result}

Vision Tool Settings
Recipe: Face A Sol 2
Recipe ID: 402
Exposure Time: 101

Vision Tool Results
Blob 1 Pass or Fail: {result}
"""

# a real 1x1 png so <img> decodes cleanly in the hidden window
import base64  # noqa: E402
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

# tiny TP pair for the program-view section: labels + forward jump + a broken
# jump + a commented-out jump + one CALL, so the navigator card, labels xref
# and click-to-definition all have something real to chew on
TESTMAIN_LS = """/PROG  TESTMAIN
/ATTR
OWNER\t\t= MNEDITOR;
COMMENT\t\t= "NAV PROBE";
PROG_SIZE\t= 400;
CREATE\t\t= DATE 26-07-01  TIME 08:00:00;
MODIFIED\t= DATE 26-07-20  TIME 09:30:00;
LINE_COUNT\t= 10;
PROTECT\t\t= READ_WRITE;
/MN
   1:  !setup ;
   2:  LBL[1:TOP] ;
   3:  IF DI[7]=ON,JMP LBL[2] ;
   4:  CALL TESTSUB ;
   5:  JMP LBL[1] ;
   6:  LBL[2:DONE] ;
   7:  ! JMP LBL[1] ;
   8:  JMP LBL[99] ;
   9:  LBL[3] ;
  10:  DO[1:PartOk]=ON ;
/POS
/END
"""

TESTSUB_LS = """/PROG  TESTSUB
/ATTR
OWNER\t\t= MNEDITOR;
COMMENT\t\t= "NAV PROBE SUB";
LINE_COUNT\t= 2;
/MN
   1:  DO[2:SubDone]=ON ;
   2:  R[1]=R[1]+1 ;
/POS
/END
"""

# one tool / one wired valve, mirroring real MHGRIPDT.VA record shapes - just
# enough for the valve card to render its full setup kv (incl. the compound
# "no / no" and "1500 ms" values whose wrapping the reflow checks pin down)
MHGRIPDT_VA = """[MHGRIP]MH_TOOL  Storage: CMOS  Access: RW  : ARRAY[4] OF TOOL_DATA
  Field: MH_TOOL[1].TOOL_NAME Access: RW: STRING[20] = 'EOAT1'
  Field: MH_TOOL[1].TOOL_VALVES Access: RW: INTEGER = 1
[MHGRIP]MH_GRIPPERS  Storage: CMOS  Access: RW  : ARRAY[4,16] OF GRIP_DATA
  Field: MH_GRIPPERS[1,1].GRIP_NAME Access: RW: STRING[20] = 'CLAMP1'
  Field: MH_GRIPPERS[1,1].GRIP_ID Access: RW: INTEGER = 1
  Field: MH_GRIPPERS[1,1].GRIP_CLAMPS Access: RW: INTEGER = 2
  Field: MH_GRIPPERS[1,1].GRIP_PARTPRS Access: RW: INTEGER = 1
  Field: MH_GRIPPERS[1,1].CHK_OPENED Access: RW: BOOLEAN = TRUE
  Field: MH_GRIPPERS[1,1].CHK_CLOSED Access: RW: BOOLEAN = TRUE
  Field: MH_GRIPPERS[1,1].CLAMP_DELAY Access: RW: INTEGER = 1500
  Field: MH_GRIPPERS[1,1].PARTPRS_CHK Access: RW: BOOLEAN = FALSE
  Field: MH_GRIPPERS[1,1].TGL_GRP Access: RW: BOOLEAN = FALSE
  Field: MH_GRIPPERS[1,1].TGL_REL Access: RW: BOOLEAN = FALSE
  Field: MH_GRIPPERS[1,1].VALVETOA_SN Access: RW: INTEGER = 1
  Field: MH_GRIPPERS[1,1].VALVETOB_SN Access: RW: INTEGER = 1
  Field: MH_GRIPPERS[1,1].PART_PRES_SN Access: RW: ARRAY[8] OF INTEGER
    [1] = 1
  Field: MH_GRIPPERS[1,1].CLAMPOPEN_SN Access: RW: ARRAY[8] OF INTEGER
    [1] = 1
  Field: MH_GRIPPERS[1,1].CLAMPCLOSESN Access: RW: ARRAY[8] OF INTEGER
    [1] = 1
[MHGRIP]MH_GRIPPERS2  Storage: CMOS  Access: RW  : ARRAY[4,16] OF GRP_TGL_DATA
  Field: MH_GRIPPERS2[1,1].OVRSTRKDELAY Access: RW: INTEGER = 200
  Field: MH_GRIPPERS2[1,1].CNCL_RCVRGRP Access: RW: BOOLEAN = FALSE
  Field: MH_GRIPPERS2[1,1].CNCL_RCVRREL Access: RW: BOOLEAN = FALSE
[MHGRIP]VALVE_TAB  Storage: CMOS  Access: RW  : ARRAY[16] OF SIGAB_TAB
  Field: VALVE_TAB[1].SIGTOA_N Access: RW: STRING[24] = 'CLAMP1 ADVANCE'
  Field: VALVE_TAB[1].SIGTOA_T Access: RW: INTEGER = 2
  Field: VALVE_TAB[1].SIGTOA_I Access: RW: INTEGER = 801
  Field: VALVE_TAB[1].SIGTOB_N Access: RW: STRING[24] = 'CLAMP1 RETURN'
  Field: VALVE_TAB[1].SIGTOB_T Access: RW: INTEGER = 2
  Field: VALVE_TAB[1].SIGTOB_I Access: RW: INTEGER = 802
[MHGRIP]PARTP_TAB  Storage: CMOS  Access: RW  : ARRAY[16] OF SIGNAL_TAB
  Field: PARTP_TAB[1].SIGNAL_N Access: RW: STRING[24] = 'PART PRESENT 1'
  Field: PARTP_TAB[1].SIGNAL_T Access: RW: INTEGER = 1
  Field: PARTP_TAB[1].SIGNAL_I Access: RW: INTEGER = 813
[MHGRIP]CLAMP_TAB  Storage: CMOS  Access: RW  : ARRAY[34] OF SIGOPEN_TAB
  Field: CLAMP_TAB[1].SIGOPEN_N Access: RW: STRING[24] = 'CLAMP1 OPENED'
  Field: CLAMP_TAB[1].SIGOPEN_T Access: RW: INTEGER = 1
  Field: CLAMP_TAB[1].SIGOPEN_I Access: RW: INTEGER = 821
  Field: CLAMP_TAB[1].SIGCLOSE_N Access: RW: STRING[24] = 'CLAMP1 CLOSED'
  Field: CLAMP_TAB[1].SIGCLOSE_T Access: RW: INTEGER = 1
  Field: CLAMP_TAB[1].SIGCLOSE_I Access: RW: INTEGER = 822
"""


def build_tree(lib: Path) -> None:
    line = lib / "FakePlant" / "LINE01"
    # 42 robots total: enough rows that the library view genuinely overflows,
    # so the per-lens scroll assertions exercise real scrollTop clamping
    names = ["RB010R01B01", "RB020R01B01"]
    names += [f"RB{i}R01B01" for i in range(100, 140)]
    for rb in names:
        snap = line / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / "SUMMARY.DG").write_text("x", encoding="utf-8")

    # RB020 gets the TP pair so the program-view section has a real detail
    # screen to probe (the other robots stay SUMMARY-only), plus the MH valve
    # dump so the reflow section has a real valve card
    snap = line / "RB020R01B01" / "2026_01_01" / "12_00_00"
    (snap / "TESTMAIN.LS").write_text(TESTMAIN_LS, encoding="utf-8")
    (snap / "TESTSUB.LS").write_text(TESTSUB_LS, encoding="utf-8")
    (snap / "MHGRIPDT.VA").write_text(MHGRIPDT_VA, encoding="utf-8")

    snap = line / "CELL-01CAM01" / "2026_07_07" / "11_20_00"
    saved = snap / "Documents" / "Matrox Design Assistant" / "SavedImages" / "2026-07-07"
    saved.mkdir(parents=True)
    triples = [
        ("CELL-01CAM01-Fail-2026_07_07-11.10.10.136", "Fail", "11:10:10:136"),
        ("CELL-01CAM01-Pass-2026_07_07-10.05.00.001", "Pass", "10:05:00:001"),
    ]
    for stem, result, ts in triples:
        (saved / (stem + ".jpg")).write_bytes(PNG_1PX)
        (saved / (stem + ".png")).write_bytes(PNG_1PX)
        (saved / (stem + ".txt")).write_text(
            SIDECAR_TXT.format(result=result, ts=ts), encoding="utf-8")
    (snap / "backup.json").write_text(json.dumps({
        "robot": "CELL-01CAM01", "line": "LINE01", "plant": "FakePlant",
        "taken": "2026-07-07T11:20:00", "type": "matrox da backup",
        "device_type": "camera-mtx", "files": 7, "bytes": 200,
        "source": "smb", "complete": True,
    }), encoding="utf-8")


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.tabs_registered", js(window, "BV.tabs.length") == 16,
              f"(got {js(window, 'BV.tabs.length')})")

        # ---- home library renders the synthetic tree ----
        nrows = poll(window, "document.querySelectorAll('.lib-robot').length")
        check("home.rows", nrows == 43, f"(got {nrows})")
        check("home.no_fav_strip_initially",
              not js(window, "!!document.querySelector('.lib-favs')"))

        # ---- row menu: edit folded in, ⋯ toggles, right-click at the mouse ----
        check("menu.no_standalone_edit_button",
              not js(window, "!!document.querySelector('.lib-robot-acts .btn[title=\"edit\"]')"))
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            row.querySelector('.lib-robot-more').click();
        })()""")
        items = js(window, """JSON.stringify([...document.querySelectorAll('.ctx-menu .ctx-item')]
            .map(function(b){return b.textContent;}))""")
        check("menu.items",
              json.loads(items or "[]") == ["edit", "add note", "hide", "open folder",
                                            "add all programs to edit workspace"],
              f"({items})")
        time.sleep(0.4)   # the menu's outside-click listeners attach deferred
        tog = js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            var btn=row.querySelector('.lib-robot-more');
            var open1=!!document.querySelector('.ctx-menu');
            btn.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            var closed=!document.querySelector('.ctx-menu');
            btn.click();
            var open2=!!document.querySelector('.ctx-menu');
            return JSON.stringify({open1:open1, closed:closed, open2:open2});
        })()""")
        tog = json.loads(tog or "{}")
        check("menu.click_toggles_closed",
              tog.get("open1") is True and tog.get("closed") is True and tog.get("open2") is False,
              f"({tog})")
        rc = js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            row.dispatchEvent(new MouseEvent('contextmenu',
                {clientX:333, clientY:222, bubbles:true, cancelable:true}));
            var m=document.querySelector('.ctx-menu');
            return JSON.stringify({open: !!m, left: m ? m.style.left : '',
              hasEdit: m ? [...m.querySelectorAll('.ctx-item')].some(function(b){
                  return b.textContent==='edit';}) : false});
        })()""")
        rc = json.loads(rc or "{}")
        check("menu.rightclick_at_mouse",
              rc.get("open") is True and rc.get("left") == "333px" and rc.get("hasEdit") is True,
              f"({rc})")
        time.sleep(0.4)
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        check("menu.esc_closes", bool(poll(window, "!document.querySelector('.ctx-menu')")))

        # ---- notes: inline multi-line editing right on the row ----
        added = js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            row.dispatchEvent(new MouseEvent('contextmenu',
                {clientX:400, clientY:260, bubbles:true, cancelable:true}));
            var it=[...document.querySelectorAll('.ctx-menu .ctx-item')]
                .find(function(b){return b.textContent==='add note';});
            if(!it) return 'no-item';
            it.click();
            return row.querySelector('.lib-note-edit') ? 'ta' : 'no-ta';
        })()""")
        check("notes.add_note_opens_editor", added == "ta", f"(got {added!r})")
        # mark an UNRELATED row: a note save must repaint ONLY the edited
        # row's note area, never rebuild the tree (plant-scale lag)
        js(window, """window.__rowMark=[...document.querySelectorAll('.lib-robot')]
            .find(function(r){return r.textContent.indexOf('RB010R01B01')>=0;});""")
        js(window, """(function(){
            var ta=document.querySelector('.lib-note-edit');
            ta.value='first line\\n\\tsecond indented\\nthird';
            ta.dispatchEvent(new Event('input',{bubbles:true}));
            /* hidden windows never really focus, so blur() alone may not fire
               the event (same family as the no-native-scroll probe quirk) —
               dispatch it; a doubled fire is a no-op (text already saved) */
            ta.blur();
            ta.dispatchEvent(new FocusEvent('blur'));
        })()""")
        disp = poll(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            var head=row.querySelector('.lib-robot-note-head');
            if(!head) return null;
            var node=row.querySelector('.lib-robot-note');
            return JSON.stringify({
              first: head.textContent.indexOf('first line')>=0,
              onlyFirst: head.textContent.indexOf('second')<0,
              caret: !!head.querySelector('.bv-caret'),
              folded: node ? !node.classList.contains('open') : null,
            });
        })()""")
        disp = json.loads(disp or "{}")
        check("notes.first_line_plus_caret",
              disp.get("first") is True and disp.get("onlyFirst") is True and
              disp.get("caret") is True and disp.get("folded") is True, f"({disp})")
        check("notes.save_repaints_only_the_row", bool(js(window,
              "document.contains(window.__rowMark)")))
        expand = js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            var head=row && row.querySelector('.lib-robot-note-head');
            var node=row && row.querySelector('.lib-robot-note');
            if(!head || !node) return JSON.stringify({});
            head.click();
            return JSON.stringify({
              open: node.classList.contains('open'),
              body: node.querySelector('.lib-robot-note-body').textContent.indexOf('second indented')>=0,
              stillHome: !location.hash || location.hash==='#home',
            });
        })()""")
        expand = json.loads(expand or "{}")
        check("notes.caret_expands_in_place",
              expand.get("open") is True and expand.get("body") is True and
              expand.get("stillHome") is True, f"({expand})")
        js(window, """window.__notes=null;
            BV.api.call('lib_list').then(function(d){
              var r=d.robots.find(function(x){return x.robot==='RB020R01B01';});
              window.__notes = r ? r.notes : 'missing';
            });""")
        saved = poll(window, "window.__notes")
        check("notes.saved_with_newlines_and_tab",
              saved == "first line\n\tsecond indented\nthird", f"(got {saved!r})")
        redit = js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            var note=row && row.querySelector('.lib-robot-note');
            if(!note) return 'no-note';
            note.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
            var ta=row.querySelector('.lib-note-edit');
            return ta ? (ta.value.indexOf('second indented')>=0 ? 'full' : 'partial') : 'none';
        })()""")
        check("notes.dblclick_reopens_full_note", redit == "full", f"(got {redit!r})")
        # typing must do zero layout work: height moves only when the line
        # COUNT changes (per-key scrollHeight reads reflowed the whole page)
        grow = js(window, """(function(){
            var ta=document.querySelector('.lib-note-edit');
            if(!ta) return null;
            var h0=ta.style.height;
            ta.value += ' x';
            ta.dispatchEvent(new Event('input',{bubbles:true}));
            var h1=ta.style.height;
            ta.value += '\\nmore';
            ta.dispatchEvent(new Event('input',{bubbles:true}));
            var h2=ta.style.height;
            return JSON.stringify({same: h1===h0, grew: parseFloat(h2)>parseFloat(h1)});
        })()""")
        grow = json.loads(grow or "{}")
        check("notes.height_only_on_new_lines",
              grow.get("same") is True and grow.get("grew") is True, f"({grow})")
        js(window, """(function(){
            var ta=document.querySelector('.lib-note-edit');
            ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
        })()""")
        check("notes.esc_cancels", bool(poll(window,
              "!document.querySelector('.lib-note-edit')")))
        check("notes.esc_restores_display", bool(poll(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            var head=row && row.querySelector('.lib-robot-note-head');
            return !!(head && head.textContent.indexOf('first line')>=0);
        })()""")))

        # Highlighting inside the editor and releasing over the row must NOT
        # open the backup: the browser dispatches that click on the row (the
        # common ancestor of mousedown+mouseup), past the editor's own
        # handlers. Counted at the lib_open CALL — opening navigates only
        # after that promise resolves, so watching location.hash in-tick would
        # pass whether or not the bug is there.
        drag = js(window, """(function(){
            window.__opens=0; window.__realCall=BV.api.call;
            BV.api.call=function(){ if(arguments[0]==='lib_open') window.__opens++;
                return window.__realCall.apply(this, arguments); };
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            row.querySelector('.lib-robot-note')
               .dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
            var ta=row.querySelector('.lib-note-edit');
            if(!ta) return JSON.stringify({err:'no editor'});
            /* press in the editor, release over the row */
            ta.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            row.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
            row.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            var afterDrag=window.__opens;
            var editingAfterDrag=!!row.querySelector('.lib-note-edit');
            /* a press on the row itself while editing only commits the edit */
            row.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            row.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            var afterPress=window.__opens;
            /* selecting the row's OWN text (name) and releasing on the row */
            var t=row.querySelector('.lib-note-edit');
            if(t) t.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
            var name=row.querySelector('.lib-robot-name');
            var sel=window.getSelection(), rg=document.createRange();
            rg.selectNodeContents(name); sel.removeAllRanges(); sel.addRange(rg);
            name.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            row.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            var afterSel=window.__opens;
            sel.removeAllRanges();
            /* control: a plain click (no drag, no selection) still opens */
            var other=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB010R01B01')>=0;});
            other.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            other.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            var afterPlain=window.__opens;
            BV.api.call=window.__realCall;
            return JSON.stringify({afterDrag:afterDrag, afterPress:afterPress,
              afterSel:afterSel, afterPlain:afterPlain,
              editingAfterDrag:editingAfterDrag});
        })()""")
        drag = json.loads(drag or "{}")
        check("notes.drag_out_of_editor_does_not_open",
              drag.get("afterDrag") == 0 and drag.get("editingAfterDrag") is True, f"({drag})")
        check("notes.click_while_editing_does_not_open",
              drag.get("afterPress") == 0, f"({drag})")
        check("notes.text_selection_does_not_open", drag.get("afterSel") == 0, f"({drag})")
        check("home.plain_click_still_opens", drag.get("afterPlain") == 1, f"({drag})")
        check("home.plain_click_opened_backup",
              poll(window, "location.hash==='#overview' ? 'y' : ''") == "y",
              f"(hash={js(window, 'location.hash')!r})")
        js(window, "BV.goHome()")
        check("home.returns_after_open", bool(poll(window,
              "location.hash==='#home' && document.querySelectorAll('.lib-robot').length===43")))
        # the Escape above must have reached the EDITOR, not a leaked menu
        # listener — a same-tick open+close used to strand a document-capture
        # Escape handler that ate the key for everything underneath it
        check("menu.no_leaked_escape_eater", bool(js(window, """(function(){
            var d=document.createElement('div'); document.body.appendChild(d);
            var hit=0;
            d.addEventListener('keydown', function(){ hit++; });
            d.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
            d.remove();
            return hit===1;
        })()""")))

        # ---- edit modal: camera title, device-name label, link picker ----
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('CELL-01CAM01')>=0;});
            row.querySelector('.lib-robot-more').click();
            [...document.querySelectorAll('.ctx-menu .ctx-item')]
                .find(function(b){return b.textContent==='edit';}).click();
        })()""")
        modal = poll(window, """(function(){
            var h=document.querySelector('#modal-root .modal h2');
            return h ? h.textContent : '';
        })()""")
        check("edit.title_says_camera", modal == "edit camera", f"(got {modal!r})")
        labels = js(window, """JSON.stringify([...document.querySelectorAll('.modal .lf-row label')]
            .map(function(l){return l.textContent;}))""")
        check("edit.device_name_label", "device name" in json.loads(labels or "[]"),
              f"({labels})")
        check("edit.no_robot_label", "robot" not in json.loads(labels or "[]"))
        check("edit.notes_is_textarea", bool(js(window, """(function(){
            var r=[...document.querySelectorAll('.modal .lf-row')].find(function(x){
                var l=x.querySelector('label'); return l && l.textContent==='notes';});
            return !!(r && r.querySelector('textarea'));
        })()""")))
        link0 = js(window, """(function(){
            var r=[...document.querySelectorAll('.modal .lf-row')].find(function(x){
                var l=x.querySelector('label'); return l && l.textContent==='linked robot';});
            return r ? r.querySelector('button').textContent : null;
        })()""")
        check("edit.link_button_none", link0 == "(none)", f"(got {link0!r})")

        # open the picker: libTree modal, robots only, filterable
        js(window, """(function(){
            var r=[...document.querySelectorAll('.modal .lf-row')].find(function(x){
                var l=x.querySelector('label'); return l && l.textContent==='linked robot';});
            r.querySelector('button').click();
        })()""")
        pick = poll(window, """(function(){
            var m=document.querySelector('#modal-root .modal');
            if(!m || !m.querySelector('.cmp-pick')) return null;
            return JSON.stringify({
              title: m.querySelector('h2').textContent,
              plants: m.querySelectorAll('.lib-plant').length,
              rows: [...m.querySelectorAll('.opt-row .name')].map(function(n){
                  return n.textContent;}),
              search: !!m.querySelector('.cmp-pick-search input'),
            });
        })()""")
        pick = json.loads(pick or "{}")
        rows = pick.get("rows", [])
        check("pick.modal_opens", pick.get("title") == "link to robot", f"({pick})")
        check("pick.tree_groups", pick.get("plants", 0) >= 1)
        check("pick.robots_only",
              any(r.startswith("RB010R01B01") for r in rows) and
              any(r.startswith("RB020R01B01") for r in rows) and
              not any("CELL-01CAM01" in r for r in rows), f"({rows})")
        check("pick.has_filter", pick.get("search") is True)

        # pick RB010 -> edit modal returns with the link shown
        js(window, """(function(){
            var m=document.querySelector('#modal-root .modal');
            var row=[...m.querySelectorAll('.opt-row')].find(function(r){
                var n=r.querySelector('.name');
                return n && n.textContent.indexOf('RB010R01B01')===0;});
            row.click();
        })()""")
        back = poll(window, """(function(){
            var h=document.querySelector('#modal-root .modal h2');
            if(!h || h.textContent!=='edit camera') return null;
            var r=[...document.querySelectorAll('.modal .lf-row')].find(function(x){
                var l=x.querySelector('label'); return l && l.textContent==='linked robot';});
            return r ? r.querySelector('button').textContent : null;
        })()""")
        check("pick.edit_returns_with_link",
              back is not None and back.startswith("RB010R01B01"), f"(got {back!r})")

        # save; the camera should now be linked (and nest under RB010 in the tree)
        js(window, """(function(){
            var b=[...document.querySelectorAll('.modal .lf-actions .btn.primary')]
                .find(function(x){return x.textContent==='save';});
            b.click();
        })()""")
        js(window, """window.__link=null;
            var iv=setInterval(function(){
              BV.api.call('lib_list').then(function(d){
                var cam=d.robots.find(function(r){return r.robot==='CELL-01CAM01';});
                var rb=d.robots.find(function(r){return r.robot==='RB010R01B01';});
                if(cam && rb && cam.linked_robot_id===rb.id){
                  window.__link='ok'; clearInterval(iv); }
              }).catch(function(){});
            }, 400);""")
        check("pick.saved_link", poll(window, "window.__link") == "ok")
        check("pick.camera_nests_under_robot", bool(poll(window,
              "!!document.querySelector('.lib-robot-nested')")))

        # ---- favorites: instant pin, full rows, linked cams ride along ----
        fav = js(window, """(function(){
            window.__lc=0; window.__realCall=BV.api.call;
            BV.api.call=function(){ if(arguments[0]==='lib_list') window.__lc++;
                return window.__realCall.apply(this, arguments); };
            var row=[...document.querySelectorAll(
                '.lib-plant:not(.lib-favs) .lib-robot:not(.lib-robot-nested)')]
                .find(function(r){return r.textContent.indexOf('RB010R01B01')>=0;});
            row.querySelector('.lib-fav').click();
            var s=document.querySelector('.lib-favs');   /* synchronous repaint */
            return JSON.stringify({instant: !!s, lc: window.__lc});
        })()""")
        fav = json.loads(fav or "{}")
        check("fav.pin_is_instant", fav.get("instant") is True, f"({fav})")
        check("fav.no_library_refetch", fav.get("lc") == 0, f"({fav})")

        strip = js(window, """(function(){
            var s=document.querySelector('.lib-favs');
            if(!s) return null;
            var top=s.querySelector('.lib-robot:not(.lib-robot-nested)');
            var cam=s.querySelector('.lib-robot-nested');
            return JSON.stringify({
              head: s.querySelector('.lib-plant-h').textContent,
              robot: top ? top.textContent.indexOf('RB010R01B01')>=0 : false,
              where: top ? top.textContent.indexOf('FakePlant / LINE01')>=0 : false,
              camAlong: cam ? cam.textContent.indexOf('CELL-01CAM01')>=0 : false,
              starByCheck: top ? (top.children[0].classList.contains('lib-check')
                              && top.children[1].classList.contains('lib-fav')) : false,
              hasCheckbox: !!(top && top.querySelector('.lib-check')),
              first: document.querySelector('.home-lib-body').firstElementChild===s,
              starOn: !![...document.querySelectorAll(
                  '.lib-plant:not(.lib-favs) .lib-fav.on')].length,
            });
        })()""")
        strip = json.loads(strip or "{}")
        check("fav.head", "favorites" in (strip.get("head") or ""), f"({strip})")
        check("fav.row_is_pinned_robot", strip.get("robot") is True)
        check("fav.row_shows_plant_line", strip.get("where") is True)
        check("fav.linked_cam_rides_along", strip.get("camAlong") is True)
        check("fav.star_next_to_checkbox", strip.get("starByCheck") is True)
        check("fav.row_selectable", strip.get("hasCheckbox") is True)
        check("fav.strip_is_first", strip.get("first") is True)
        check("fav.tree_star_lit", strip.get("starOn") is True)

        # selecting IN the strip drives the same selection as the tree
        selres = js(window, """(function(){
            document.querySelector(
                '.lib-favs .lib-robot:not(.lib-robot-nested) .lib-check').click();
            var count=document.querySelector('.lib-sel-count').textContent;
            var treeRow=[...document.querySelectorAll(
                '.lib-plant:not(.lib-favs) .lib-robot:not(.lib-robot-nested)')]
                .find(function(r){return r.textContent.indexOf('RB010R01B01')>=0;});
            var treeChecked=treeRow.querySelector('.lib-check').checked;
            document.querySelector(
                '.lib-favs .lib-robot:not(.lib-robot-nested) .lib-check').click();
            var cleared=document.querySelector('.lib-sel-count').textContent;
            return JSON.stringify({count:count, treeChecked:treeChecked, cleared:cleared});
        })()""")
        selres = json.loads(selres or "{}")
        check("fav.strip_select_counts", selres.get("count") == "1 selected", f"({selres})")
        check("fav.strip_select_syncs_tree_copy", selres.get("treeChecked") is True)
        check("fav.strip_deselect_clears", selres.get("cleared") == "")

        # the flag persisted server-side (behind the instant repaint)
        js(window, """BV.api.call=window.__realCall;
            window.__favok=null;
            BV.api.call('lib_list').then(function(d){
              var r=d.robots.find(function(x){return x.robot==='RB010R01B01';});
              window.__favok = r && r.favorite === true ? 'y' : 'n';
            });""")
        check("fav.persisted", poll(window, "window.__favok") == "y")

        # unpin from the STRIP copy; strip disappears instantly
        js(window, "document.querySelector('.lib-favs .lib-fav').click()")
        check("fav.unpin_removes_strip",
              bool(poll(window, "!document.querySelector('.lib-favs')")))

        # ---- multi-cam lens: the post-merge fix batch ----

        # The cam refresher's only idle gate is document.hidden — record what a
        # HIDDEN pywebview window actually reports (the assumption was never
        # verified in this codebase; the [info] line below is the ground truth).
        # ANSWER (2026-07-22, WebView2 hidden window, measured by this probe):
        # visibilityState reports "hidden" / document.hidden === true — so the
        # refresher's idle gate IS active in a hidden probe window, and
        # interval-driven tile refreshes can never be exercised from a probe
        # like this one (only the initial img wiring at render time can).
        vis = js(window, "document.visibilityState + ' / hidden=' + document.hidden")
        print(f"[info] hidden-window visibility: {vis}")

        # the setCount primitive: an undefined n blanks the counter even when a
        # total rides along (used to render the literal "undefined/0")
        sc = js(window, """(function(){
            var sb=BV.searchBox({onChange:function(){}});
            sb.setCount(undefined, 0);
            return sb.el.querySelector('.match-count').textContent;
        })()""")
        check("cam.setcount_undefined_blanks", sc == "", f"(got {sc!r})")

        # a CV-X camera in the library: it lists in the backup lens but is
        # deliberately NOT tiled in the cam lens (matrox-only by design)
        js(window, """window.__cvx=null;
            BV.api.call('lib_add', {robot:'CELL-01CVX01', plant:'FakePlant',
              line:'LINE01', device_type:'camera-keyence', ips:['192.0.2.162'],
              model:'', notes:'', latest_path:'', ftp:{user:'', passive:true}})
              .then(function(){ window.__cvx='ok'; })
              .catch(function(e){ window.__cvx='err:'+e.message; });""")
        check("cam.cvx_added", poll(window, "window.__cvx") == "ok",
              f"(got {js(window, 'window.__cvx')!r})")
        js(window, "BV.state.emit('library-dirty')")
        check("cam.cvx_lists_in_backup_lens", bool(poll(window,
              "document.querySelectorAll('.lib-robot').length===44 ? 'y' : ''")))

        # seed the backup lens: select a robot and scroll well into the tree
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB010R01B01')>=0;});
            row.querySelector('.lib-check').click();
        })()""")
        check("cam.selection_seeded", js(window,
              "document.querySelector('.lib-sel-count').textContent") == "1 selected")
        st0 = js(window, """(function(){
            var view=document.getElementById('view');
            view.scrollTop=600;
            return view.scrollTop;
        })()""")
        check("cam.scroll_env_scrollable", st0 == 600, f"(got {st0})")
        anchor0 = js(window, """(function(){
            var view=document.getElementById('view');
            var vt=view.getBoundingClientRect().top;
            var top=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.getBoundingClientRect().bottom>vt+2;});
            return top ? top.getAttribute('data-robot-id') : '';
        })()""")
        check("cam.scroll_anchor_found", bool(anchor0), f"(got {anchor0!r})")

        # flip to multi-cam (first flip this session)
        js(window, """(function(){
            document.getElementById('cube-cam').click();
        })()""")
        check("cam.lens_flips", bool(poll(window,
              "!!document.querySelector('.home-library.cam-mode')")))
        check("cam.filter_placeholder", js(window,
              "document.querySelector('#topbar-search .screen-search input').placeholder")
              == "filter cameras…")
        # the lens toggle lives in the TOPBAR now, not inside the screen it changes
        check("cam.no_head_seg",
              not js(window, "!!document.querySelector('.home-view-seg')"),
              "(the cubes own the flip)")
        check("cam.cube_active_tracks_lens", bool(js(window, """(function(){
            return document.getElementById('cube-cam').classList.contains('active')
                && !document.getElementById('cube-lib').classList.contains('active');
        })()""")))

        # fix 1: the selection count hides, but the functions… dropdown (the
        # selection actions + tidy-ups + last-backup report) stays reachable
        head = js(window, """JSON.stringify((function(){
            var sel=document.querySelector('.home-lib-selacts');
            var fb=document.querySelector('.home-lib-actions .lib-act-functions');
            return { selacts: getComputedStyle(sel).display,
                     fns: fb ? getComputedStyle(fb).display : 'missing' };
        })())""")
        head = json.loads(head or "{}")
        check("cam.selacts_hidden", head.get("selacts") == "none", f"({head})")
        check("cam.functions_reachable", head.get("fns") not in ("none", "missing", None),
              f"({head})")

        # fix 7: deliberate fold policy — plants open, lines folded, so the
        # first flip shows count badges instead of fetching every line at once
        fold = js(window, """JSON.stringify((function(){
            var body=document.querySelector('.home-lib-body');
            var plants=[...body.querySelectorAll('.lib-plant')];
            var lines=[...body.querySelectorAll('.lib-line')];
            return { plantsOpen: plants.length>0 && plants.every(function(p){
                       return p.classList.contains('open');}),
                     linesFolded: lines.length>0 && lines.every(function(l){
                       return !l.classList.contains('open');}),
                     counts: !!body.querySelector('.lib-line-h .lib-count') };
        })())""")
        fold = json.loads(fold or "{}")
        check("cam.plants_start_open", fold.get("plantsOpen") is True, f"({fold})")
        check("cam.lines_start_folded", fold.get("linesFolded") is True, f"({fold})")
        check("cam.folded_lines_show_counts", fold.get("counts") is True, f"({fold})")

        # only camera-mtx entries tile (robots and the CV-X stay out by design)
        tiles = js(window, """JSON.stringify((function(){
            var t=[...document.querySelectorAll('.cam-tile')];
            return { n: t.length,
                     name: t[0] ? t[0].textContent.indexOf('CELL-01CAM01')>=0 : false,
                     noip: t[0] ? t[0].classList.contains('no-ip') : false,
                     cvxTiled: t.some(function(x){
                       return x.textContent.indexOf('CVX')>=0;}) };
        })())""")
        tiles = json.loads(tiles or "{}")
        check("cam.only_mtx_tiles", tiles.get("n") == 1 and tiles.get("name") is True,
              f"({tiles})")
        check("cam.cvx_not_tiled", tiles.get("cvxTiled") is False, f"({tiles})")
        check("cam.tile_flags_no_ip", tiles.get("noip") is True, f"({tiles})")

        # fix 8: tiles are keyboard-reachable — focusable button role, and
        # Enter/Space take the click path (here: the honest no-IP refusal toast)
        kbd = js(window, """JSON.stringify((function(){
            var t=document.querySelector('.cam-tile');
            var out={ tab: t.getAttribute('tabindex')==='0',
                      role: t.getAttribute('role')==='button' };
            t.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
            var tst=document.getElementById('toast');
            out.enterToast = !!(tst && tst.classList.contains('show') &&
                tst.textContent.indexOf('no IP on record')>=0);
            tst.classList.remove('show');
            t.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true}));
            out.spaceToast = !!(tst.classList.contains('show') &&
                tst.textContent.indexOf('no IP on record')>=0);
            return out;
        })())""")
        kbd = json.loads(kbd or "{}")
        check("cam.tile_focusable_button",
              kbd.get("tab") is True and kbd.get("role") is True, f"({kbd})")
        check("cam.enter_refuses_no_ip_with_toast", kbd.get("enterToast") is True,
              f"({kbd})")
        check("cam.space_acts_too", kbd.get("spaceToast") is True, f"({kbd})")

        # fix 2: the cam lens starts at ITS OWN top instead of inheriting the
        # tree's clamped offset…
        check("cam.lens_has_own_scroll", js(window,
              "document.getElementById('view').scrollTop") == 0)

        # …and flipping back restores the tree exactly: same first-visible row
        js(window, """(function(){
            document.getElementById('cube-lib').click();
        })()""")
        back = poll(window, """(function(){
            if(document.querySelector('.home-library.cam-mode')) return null;
            var view=document.getElementById('view');
            if(!view.scrollTop) return null;   /* restore lands synchronously, but be kind */
            var vt=view.getBoundingClientRect().top;
            var top=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.getBoundingClientRect().bottom>vt+2;});
            return JSON.stringify({ st: view.scrollTop,
                id: top ? top.getAttribute('data-robot-id') : '' });
        })()""")
        back = json.loads(back or "{}")
        check("cam.flip_back_restores_scroll",
              back.get("id") == anchor0 and back.get("st", 0) > 300, f"({back})")
        check("cam.flip_back_keeps_selection", js(window,
              "document.querySelector('.lib-sel-count').textContent") == "1 selected")
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB010R01B01')>=0;});
            row.querySelector('.lib-check').click();   /* clear the seed */
            document.getElementById('view').scrollTop=0;
        })()""")

        # fix 4: the cam filter matches the "↳ robot" name printed on the tile
        # (CELL-01CAM01 is linked to RB010R01B01 from the picker test above)
        js(window, """(function(){
            document.getElementById('cube-cam').click();
            var inp=document.querySelector('#topbar-search .screen-search input');
            inp.value='RB010R01B01';
            inp.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        # the filter is debounced (150 ms) and hidden windows throttle timers
        # to ~1 s — poll for the applied state instead of sleeping
        match = poll(window, """(function(){
            var c=document.querySelector('#topbar-search .match-count').textContent;
            if(c!=='1') return null;   /* debounce hasn't fired yet */
            return JSON.stringify({ tiles: document.querySelectorAll('.cam-tile').length,
                                    count: c });
        })()""")
        match = json.loads(match or "{}")
        check("cam.filter_matches_linked_robot_name",
              match.get("tiles") == 1 and match.get("count") == "1", f"({match})")
        js(window, """(function(){
            var inp=document.querySelector('#topbar-search .screen-search input');
            inp.value='zzz-no-such';
            inp.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        nomatch = poll(window, """(function(){
            var e=document.querySelector('.home-lib-body .empty-lib');
            if(!e) return null;        /* throttled debounce hasn't fired yet */
            return JSON.stringify({ note: e.textContent,
                count: document.querySelector('#topbar-search .match-count').textContent });
        })()""")
        nomatch = json.loads(nomatch or "{}")
        check("cam.no_match_says_cameras",
              "no cameras match" in (nomatch.get("note") or ""), f"({nomatch})")
        check("cam.no_match_counter", nomatch.get("count") == "0/1", f"({nomatch})")
        js(window, """(function(){
            var inp=document.querySelector('#topbar-search .screen-search input');
            inp.value='';
            inp.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        check("cam.filter_clears", bool(poll(window,
              "document.querySelectorAll('.cam-tile').length===1 ? 'y' : ''")))

        # fix 5: hiding the only matrox cam (plus a robot, to prove the count
        # is lens-scoped) — the empty grid says HIDDEN, never "none yet"
        js(window, """window.__hid=null;
            BV.api.call('lib_list').then(function(d){
              var cam=d.robots.find(function(r){return r.robot==='CELL-01CAM01';});
              var rb=d.robots.find(function(r){return r.robot==='RB139R01B01';});
              return Promise.all([
                BV.api.call('lib_set_hidden', cam.id, true),
                BV.api.call('lib_set_hidden', rb.id, true),
              ]);
            }).then(function(){ window.__hid='ok'; })
              .catch(function(e){ window.__hid='err:'+e.message; });""")
        check("cam.hide_setup", poll(window, "window.__hid") == "ok",
              f"(got {js(window, 'window.__hid')!r})")
        js(window, "BV.state.emit('library-dirty')")
        empty = poll(window, """(function(){
            var e=document.querySelector('.home-lib-body .empty-lib');
            if(!e || e.textContent.indexOf('hidden')<0) return null;
            return JSON.stringify({ note: e.textContent,
                count: document.querySelector('#topbar-search .match-count').textContent,
                toggle: document.querySelector('.lib-show-hidden').textContent });
        })()""")
        empty = json.loads(empty or "{}")
        check("cam.empty_state_says_hidden",
              "1 matrox camera is hidden" in (empty.get("note") or ""), f"({empty})")
        check("cam.empty_state_never_undefined", empty.get("count") == "", f"({empty})")
        check("cam.hidden_count_is_lens_scoped",
              empty.get("toggle") == "show hidden (1)", f"({empty})")

        # the backup lens counts BOTH hidden entries (1 robot + 1 camera)
        js(window, """(function(){
            document.getElementById('cube-lib').click();
        })()""")
        tog = poll(window, """(function(){
            var t=document.querySelector('.lib-show-hidden');
            return t && t.textContent==='show hidden (2)' ? t.textContent : null;
        })()""")
        check("cam.backup_lens_counts_both", tog == "show hidden (2)", f"(got {tog!r})")

        # show hidden in the cam lens reveals the tile again
        js(window, """(function(){
            document.getElementById('cube-cam').click();
        })()""")
        poll(window, "!!document.querySelector('.home-library.cam-mode')")
        js(window, "document.querySelector('.lib-show-hidden').click()")
        check("cam.show_hidden_reveals_tile", bool(poll(window,
              "document.querySelectorAll('.cam-tile').length===1 ? 'y' : ''")))
        js(window, "document.querySelector('.lib-show-hidden').click()")   # back off
        js(window, """window.__unhid=null;
            BV.api.call('lib_list').then(function(d){
              return Promise.all(d.robots.filter(function(r){return r.hidden;})
                .map(function(r){return BV.api.call('lib_set_hidden', r.id, false);}));
            }).then(function(){ window.__unhid='ok'; })
              .catch(function(e){ window.__unhid='err:'+e.message; });""")
        check("cam.unhide_cleanup", poll(window, "window.__unhid") == "ok")
        js(window, "BV.state.emit('library-dirty')")
        check("cam.tile_back_after_unhide", bool(poll(window,
              "document.querySelectorAll('.cam-tile').length===1 ? 'y' : ''")))

        # a TEST-NET IP on the tile: the live img wires up and a dead camera
        # is tolerated (nothing real is ever probed — 192.0.2.x is unroutable)
        js(window, """window.__ip=null;
            BV.api.call('lib_list').then(function(d){
              var cam=d.robots.find(function(r){return r.robot==='CELL-01CAM01';});
              return BV.api.call('lib_update', cam.id, {ips:['192.0.2.161']});
            }).then(function(){ window.__ip='ok'; })
              .catch(function(e){ window.__ip='err:'+e.message; });""")
        check("cam.ip_set", poll(window, "window.__ip") == "ok")
        js(window, "BV.state.emit('library-dirty')")
        check("cam.live_img_wired", bool(poll(window, """(function(){
            var img=document.querySelector('.cam-tile img.cam-live');
            return img && img.dataset.ip==='192.0.2.161' ? 'y' : '';
        })()""")))

        # the lens choice persisted, and a REMOUNT lands straight in it
        # (home_view via set_setting; selacts hidden from the first paint)
        js(window, """window.__hv=null;
            BV.api.call('get_settings').then(function(s){
              window.__hv = s ? s.home_view : 'none'; });""")
        check("cam.home_view_persisted", poll(window, "window.__hv") == "multicam",
              f"(got {js(window, 'window.__hv')!r})")
        js(window, "BV.route()")
        remount = poll(window, """(function(){
            var lib=document.querySelector('.home-library.cam-mode');
            if(!lib || !document.querySelector('.cam-tile')) return null;
            return JSON.stringify({
              selacts: getComputedStyle(document.querySelector('.home-lib-selacts')).display,
              fns: getComputedStyle(document.querySelector('.lib-act-functions')).display });
        })()""")
        remount = json.loads(remount or "{}")
        check("cam.remount_lands_in_lens", remount.get("selacts") == "none", f"({remount})")
        check("cam.remount_functions_reachable",
              remount.get("fns") not in ("none", "missing", None), f"({remount})")

        # ---- a cube reaches its lens from ANOTHER screen in one click ----
        # the lens has to be set BEFORE the route: buildLibraryHead() and
        # renderTree() read viewMode() live as they paint, so the other order
        # gives a backup-lens paint followed by a multicam repaint
        js(window, "location.hash = '#edit'")
        time.sleep(0.8)
        check("cam.cube_leaves_for_workspace",
              js(window, "location.hash") == "#edit"
              and bool(js(window, "document.getElementById('cube-edit').classList.contains('active')")))
        js(window, "document.getElementById('cube-cam').click()")
        time.sleep(1.0)
        check("cam.cube_from_another_screen",
              (js(window, "location.hash") in ("#home", "", "#"))
              and bool(poll(window, "!!document.querySelector('.home-library.cam-mode')")),
              f"(hash={js(window, 'location.hash')!r})")

        # leave the library in the backup lens for the sections below
        js(window, """(function(){
            document.getElementById('cube-lib').click();
        })()""")
        check("cam.back_to_backup_lens", bool(poll(window,
              "document.querySelectorAll('.lib-robot').length===44 ? 'y' : ''")))

        # ---- health-scan picker: new checks listed, the clock tolerance input ----
        js(window, """(function(){
            window.__hs=null; window.__realCall2=BV.api.call;
            BV.api.call=function(){
              if(arguments[0]==='health_scan_start'){
                window.__hs=JSON.stringify([].slice.call(arguments,1));
                return Promise.reject(new Error('probe-intercept'));
              }
              return window.__realCall2.apply(this, arguments);
            };
            BV.scanUI.open([{id:'probe-r1', robot:'RB010R01B01'}]);
        })()""")
        picker = poll(window, """(function(){
            var cats=[...document.querySelectorAll('.hs-cat-title')]
                .map(function(t){return t.textContent;});
            if(cats.length<5) return null;
            var pin=document.querySelector('.hs-param');
            return JSON.stringify({ cats: cats,
              labels: [...document.querySelectorAll('.hs-lbl')].map(function(l){
                  return l.textContent;}),
              pin: !!pin, pinVal: pin ? pin.value : '' });
        })()""")
        picker = json.loads(picker or "{}")
        check("scan.categories_include_positions",
              picker.get("cats", [])[:5] == ["safety", "mastering", "programs",
                                             "positions", "config"],
              f"({picker.get('cats')})")
        for lbl in ("remarked positions", "remarked logic", "untaught positions",
                    "uninitialized PRs in use", "general override < 100%",
                    "controller clock drift"):
            check("scan.lists_" + lbl.split()[0] + "_" + lbl.split()[1][:5],
                  lbl in picker.get("labels", []), f"({lbl})")
        check("scan.tolerance_input_default",
              picker.get("pin") is True and picker.get("pinVal") == "2m", f"({picker})")

        # pick the clock check, type a tolerance, scan: the param rides along
        sent = js(window, """(function(){
            var rows=[...document.querySelectorAll('.hs-check')];
            var clock=rows.find(function(r){
                return r.querySelector('.hs-lbl').textContent==='controller clock drift';});
            clock.querySelector('input[type=checkbox]').click();
            var pin=clock.querySelector('.hs-param');
            pin.value='45s';
            var go=[...document.querySelectorAll('.hs-foot .btn.primary')]
                .find(function(b){return b.textContent==='scan';});
            go.click();
            return window.__hs;
        })()""")
        sent = json.loads(sent or "[]")
        # args after the method name: robot_ids, picked checks, queries, params
        check("scan.param_rides_along",
              len(sent) == 4 and sent[0] == ["probe-r1"] and
              sent[1] == ["clock_drift"] and sent[3].get("clock_drift") == "45s",
              f"({sent})")
        js(window, """BV.api.call=window.__realCall2;
            document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));""")
        check("scan.modal_closes", bool(poll(window,
              "document.getElementById('modal-root').classList.contains('hidden')")))

        # ---- the scan REPORT: a tree with report-scoped filters ----
        # canned results through stubbed endpoints, so the real pick -> run ->
        # report flow drives the real report code with known findings
        js(window, """(function(){
            window.__realCall3=BV.api.call;
            var RESULTS=[
              {robot_id:'probe-r1', robot:'RB010R01B01', line:'LINE01', plant:'FakePlant',
               checks:[
                 {id:'remarked_positions', status:'flag',
                  summary:'3 remarked motion lines — positions are being skipped',
                  detail:'capped text',
                  items:[{prog:'S04FLIP', line:71, text:'//J P[5] 100% CNT100'},
                         {prog:'S04FLIP', line:72, text:'//J P[6] 100% CNT15'},
                         {prog:'S042DMTX1', line:42, text:'//J P[2] 100% FINE'}]},
                 {id:'pause_used', status:'flag', summary:'1 PAUSE in 1 program',
                  items:[{prog:'S04PROC1', line:24, text:'PAUSE'}]},
                 {id:'broken_calls', status:'info',
                  summary:'2 called programs not in the backup (2 call sites)',
                  detail:'GONE <- S04FLIP, MISSING <- S04PROC1',
                  items:[{prog:'S04FLIP', text:'CALL GONE'},
                         {prog:'S04PROC1', text:'CALL MISSING'}]},
                 {id:'clock_drift', status:'info', summary:'drift +4m',
                  detail:'controller 11-JUN-26 08:56 vs backup written 08:52'},
                 {id:'override_low', status:'ok', summary:'100%'}]},
              {robot_id:'probe-r2', robot:'RB020R01B01', line:'LINE01', plant:'FakePlant',
               checks:[
                 {id:'remarked_positions', status:'flag',
                  summary:'1 remarked motion line — positions are being skipped',
                  items:[{prog:'S04FLIP', line:9, text:'//L P[1] 500mm/sec FINE'}]},
                 {id:'pause_used', status:'ok', summary:'no PAUSE instructions'},
                 {id:'override_low', status:'na', summary:'no $MCR.$GENOVERRIDE'}]}];
            BV.api.call=function(){
              var a=arguments;
              if(a[0]==='health_scan_start')
                return Promise.resolve({job_id:'hs-probe', total:2});
              if(a[0]==='scan_progress' && a[1]==='hs-probe')
                return Promise.resolve({status:'done', scanned:2, total:2, results:RESULTS});
              if(a[0]==='ws_robot_programs')
                return Promise.resolve({root:'PROBE-ROOT-'+a[1], label:'RB0X0R01B01',
                  programs:[{file:'S04FLIP.LS', name:'S04FLIP.LS', comment:''}]});
              return window.__realCall3.apply(this, a);
            };
            BV.scanUI.open([{id:'probe-r1'},{id:'probe-r2'}]);
        })()""")
        poll(window, "!!document.querySelector('.hs-check')")
        js(window, """(function(){
            var rows=[...document.querySelectorAll('.hs-check')];
            var pick=rows.find(function(r){
                return r.querySelector('.hs-lbl').textContent==='remarked positions';});
            pick.querySelector('input[type=checkbox]').click();
            [...document.querySelectorAll('.hs-foot .btn.primary')]
                .find(function(b){return b.textContent==='scan';}).click();
        })()""")
        got = poll(window, "document.querySelectorAll('.hs-sec').length")
        check("report.sections_render", got and got >= 2, f"({got} sections)")
        head_txt = js(window, "(document.querySelector('.hs-report-head .hs-info')||{}).textContent||''")
        check("report.head_counts_and_stamp",
              "2 robots scanned" in head_txt and "3 flags" in head_txt and ":" in head_txt,
              f"({head_txt!r})")
        # unfiltered: the head states findings plainly, no "of"
        check("report.head_counts_findings",
              "7 findings" in head_txt and " of " not in head_txt, f"({head_txt!r})")
        check("report.flag_sections_open_themselves",
              js(window, "document.querySelectorAll('.hs-sec.open').length") >= 2,
              "(the flags are the report — quiet sections stay folded)")
        # STICKY: a stray click outside must NOT eat the report
        js(window, """document.getElementById('modal-root').dispatchEvent(
            new MouseEvent('mousedown',{bubbles:true}))""")
        time.sleep(0.3)
        check("report.backdrop_click_does_not_close",
              not js(window, "document.getElementById('modal-root').classList.contains('hidden')"))
        check("report.has_x_close", bool(js(window, "!!document.querySelector('.modal-x')")))
        # the TREE: left-click expands into per-program groups, nothing capped
        js(window, "document.querySelector('.hs-sec.open .hs-rowhead.expandable').click()")
        time.sleep(0.3)
        # scope to the OPENED row: collapsed rowbodies exist in the DOM too
        tree = js(window, """JSON.stringify({
            progs:[...document.querySelectorAll('.hs-row.open .hs-prog-h .nm')]
                .map(function(n){return n.textContent;}),
            items:document.querySelectorAll('.hs-row.open .hs-item').length})""")
        tree_d = json.loads(tree or "{}")
        check("report.row_expands_to_program_groups",
              tree_d.get("progs") == ["S04FLIP", "S042DMTX1"] and tree_d.get("items") == 3,
              f"({tree})")
        # right-click a LINE ITEM -> ignore this finding (view filter only).
        # scoped to the row just opened: folded sections keep their rows in
        # the DOM, so an unscoped .hs-item lands in whichever section is first
        js(window, """document.querySelector('.hs-row.open .hs-item').dispatchEvent(
            new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:200,clientY:200}))""")
        time.sleep(0.3)
        menu1 = js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .map(function(b){return b.textContent;}).join('|')""") or ""
        check("report.item_menu_shape",
              "ignore this finding" in menu1 and "exclude program S04FLIP from scan" in menu1
              and "add S04FLIP to editor" in menu1 and "open this backup" in menu1,
              f"({menu1!r})")
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent==='ignore this finding';}).click()""")
        time.sleep(0.4)
        check("report.ignore_finding_filters_item", bool(js(window, """(function(){
            var sec=[...document.querySelectorAll('.hs-sec')].find(function(s){
                return s.querySelector('.hs-sec-title').textContent==='remarked positions';});
            var row=[...sec.querySelectorAll('.hs-rowhead')].find(function(h){
                return h.textContent.indexOf('RB010')>=0;});
            return row && row.querySelector('.hs-shown') &&
                   row.querySelector('.hs-shown').textContent==='2 of 3 shown';
        })()""")), "(the row must say honestly that a finding is filtered)")
        reset_txt = js(window, "(document.querySelector('.hs-reset')||{}).textContent") or ""
        check("report.reset_chip_appears", reset_txt == "reset filters (1)",
              f"({reset_txt!r})")
        # a filter repaint must NOT lose your place: the row you were working
        # in is still open (no re-scroll, no re-expand crawl)
        check("report.filters_keep_your_place", bool(js(window, """(function(){
            var sec=[...document.querySelectorAll('.hs-sec')].find(function(s){
                return s.querySelector('.hs-sec-title').textContent==='remarked positions';});
            var row=[...sec.querySelectorAll('.hs-row')].find(function(x){
                return x.querySelector('.hs-rowhead').textContent.indexOf('RB010')>=0;});
            return row && row.classList.contains('open');
        })()""")), "(every right-click used to collapse the whole report)")
        # the program header offers BOTH scopes: its own row-local ignore and
        # the every-robot exclude
        js(window, """document.querySelector('.hs-row.open .hs-prog-h').dispatchEvent(
            new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:220,clientY:220}))""")
        time.sleep(0.3)
        menu2 = js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .map(function(b){return b.textContent;}).join('|')""") or ""
        check("report.prog_menu_has_both_scopes",
              menu2.split("|")[0].startswith("ignore th")
              and "exclude program S04FLIP from scan" in menu2,
              f"({menu2!r})")
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('ignore th')===0;}).click()""")
        time.sleep(0.4)
        prog_local = js(window, """JSON.stringify((function(){
            var sec=[...document.querySelectorAll('.hs-sec')].find(function(s){
                return s.querySelector('.hs-sec-title').textContent==='remarked positions';});
            var r10=[...sec.querySelectorAll('.hs-row')].find(function(x){
                return x.querySelector('.hs-rowhead').textContent.indexOf('RB010')>=0;});
            return {progs:[...r10.querySelectorAll('.hs-prog-h .nm')].map(function(n){return n.textContent;}),
                    rb20: [...sec.querySelectorAll('.hs-rowhead')].some(function(h){
                        return h.textContent.indexOf('RB020')>=0;})};
        })())""")
        pl = json.loads(prog_local or "{}")
        check("report.prog_ignore_is_row_local",
              pl.get("progs") == ["S042DMTX1"] and pl.get("rb20") is True,
              f"({prog_local} — RB020's S04FLIP must survive a row-local ignore)")
        # EXCLUDE from RB020's side: S04FLIP findings vanish on EVERY robot
        js(window, """(function(){
            var sec=[...document.querySelectorAll('.hs-sec')].find(function(s){
                return s.querySelector('.hs-sec-title').textContent==='remarked positions';});
            var row=[...sec.querySelectorAll('.hs-row')].find(function(x){
                return x.querySelector('.hs-rowhead').textContent.indexOf('RB020')>=0;});
            row.querySelector('.hs-rowhead').click();
            row.querySelector('.hs-prog-h').dispatchEvent(
                new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:220,clientY:220}));
        })()""")
        time.sleep(0.3)
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('exclude program S04FLIP')>=0;}).click()""")
        time.sleep(0.4)
        # filtered ROWS leave the DOM entirely; RB020's ok/n·a rows in OTHER
        # sections rightly stay (CSS-hidden), so ask the remarked section
        after_ex = js(window, """JSON.stringify((function(){
            var txt=document.querySelector('.hs-results').textContent;
            var sec=[...document.querySelectorAll('.hs-sec')].find(function(s){
                return s.querySelector('.hs-sec-title').textContent==='remarked positions';});
            var rb20=[...sec.querySelectorAll('.hs-rowhead')].filter(function(h){
                return h.textContent.indexOf('RB020')>=0;});
            return {flip: txt.indexOf('S04FLIP')>=0, rb20rows: rb20.length};
        })())""")
        ex_d = json.loads(after_ex or "{}")
        check("report.exclude_program_sweeps_all_robots",
              ex_d.get("flip") is False and ex_d.get("rb20rows") == 0,
              f"({after_ex} — RB020's only finding was S04FLIP, so its row goes too)")
        # copy follows the filters and keeps the tree shape
        js(window, """window.__copied=null; window.__realCopy=BV.copyText;
            BV.copyText=function(t){ window.__copied=t; };
            [...document.querySelectorAll('.hs-report-head .btn')]
                .find(function(b){return b.textContent==='copy full';}).click();""")
        time.sleep(0.3)
        copied = js(window, "window.__copied") or ""
        js(window, "BV.copyText=window.__realCopy")
        check("report.copy_is_tree_text",
              "[remarked positions]" in copied and "S042DMTX1 (1)" in copied
              and "line 42: //J P[2] 100% FINE" in copied,
              f"({copied[:120]!r})")
        check("report.copy_honors_filters",
              "S04FLIP" not in copied and "filter" in copied and "not shown" in copied,
              "(what you send matches what you see)")
        # an exclude reaches EVERY section, not just the one it was used in -
        # broken CALLs names S04FLIP too, and prose-only details would have
        # smuggled it back into the paste
        check("report.exclude_reaches_other_sections",
              "[broken CALLs]" in copied and "CALL GONE" not in copied
              and "CALL MISSING" in copied,
              "(S04FLIP's broken call must go with it; S04PROC1's stays)")
        check("report.copy_states_filtered_counts",
              "[1 of 2 shown]" in copied and "of 7 findings" in copied,
              f"(a filtered row must never quote its pre-filter summary alone)")
        # a check with NO findings (per-robot facts) keeps its detail
        check("report.copy_keeps_findingless_detail",
              "controller 11-JUN-26 08:56" in copied,
              "(clock drift has nothing to filter — dropping it would lose real info)")
        head2 = js(window, "(document.querySelector('.hs-report-head .hs-info')||{}).textContent||''")
        check("report.head_recounts_when_filtered",
              " of 7 findings shown" in head2, f"({head2!r})")
        sec_txt = js(window, """(function(){
            var s=[...document.querySelectorAll('.hs-sec')].find(function(x){
                return x.querySelector('.hs-sec-title').textContent==='remarked positions';});
            return s.querySelector('.hs-sec-counts').textContent;
        })()""") or ""
        check("report.section_head_recounts", " of " in sec_txt and "findings" in sec_txt,
              f"({sec_txt!r})")
        # the QUICK list: who, how many, which programs - no lines. Same rows,
        # same filters, blank line between robots so it reads as a list.
        js(window, """window.__q=null; window.__realCopy2=BV.copyText;
            BV.copyText=function(t){ window.__q=t; };
            [...document.querySelectorAll('.hs-report-head .btn')]
                .find(function(b){return b.textContent==='copy list';}).click();
            BV.copyText=window.__realCopy2;""")
        time.sleep(0.3)
        quick = js(window, "window.__q") or ""
        check("quick.has_banner_rules",
              "[remarked positions]" in quick and quick.count("---") >= 2,
              f"({quick[:80]!r})")
        check("quick.robot_line_carries_count",
              any(l.strip().startswith("x RB010") and l.rstrip().endswith("— 1")
                  for l in quick.split("\n")),
              "(robot — <findings> for the shown findings only)")
        check("quick.lists_programs_with_counts", "      S042DMTX1 (1)" in quick)
        check("quick.no_line_detail",
              "line 42" not in quick and "//J P[2]" not in quick,
              "(the short form is the point — lines live in copy full)")
        check("quick.blank_line_between_robots",
              "\n\n  x RB0" in quick or "\n\n  * RB0" in quick,
              "(robots must breathe)")
        check("quick.honors_filters", "S04FLIP" not in quick)

        # LAST SCAN: close with the ✕, reopen the report from the picker —
        # the report AND its filters survive
        js(window, "document.querySelector('.modal-x').click()")
        check("report.x_closes", bool(poll(window,
              "document.getElementById('modal-root').classList.contains('hidden')")))

        # the finished report is on DISK, not just in memory: closing the app
        # must not throw away minutes of scanning. Closing FLUSHES the
        # debounced save, so a filter applied a second before quitting keeps.
        # the close FIRES the write, it does not await it - so re-read until
        # the flush lands rather than racing it (an atomic replace means a
        # read that is early sees the previous save, never a torn one)
        js(window, "window.__disk=''")
        disk = poll(window, """(function(){
            BV.api.call('load_last_scan').then(function(r){
                window.__disk = JSON.stringify({
                    robots: r && r.results ? r.results.length : -1,
                    progs: r && r.flt ? Object.keys(r.flt.progs).length : -1,
                    view: r && r.view ? Object.keys(r.view.rows).length : -1});
            }, function(e){ window.__disk = 'err:' + (e.code||e.message); });
            var d = null;
            try { d = window.__disk ? JSON.parse(window.__disk) : null; } catch (e) {}
            return (d && d.progs === 1) ? window.__disk : "";
        })()""") or js(window, "window.__disk")
        disk_d = json.loads(disk) if isinstance(disk, str) and disk.startswith("{") else {}
        check("persist.report_written_to_disk", disk_d.get("robots") == 2, f"({disk!r})")
        check("persist.filters_ride_along", disk_d.get("progs") == 1,
              "(the excluded program is part of the saved view)")
        check("persist.expansion_rides_along", (disk_d.get("view") or 0) >= 1)
        js(window, "BV.scanUI.open([{id:'probe-r1'},{id:'probe-r2'}])")
        last_btn = poll(window, """(function(){
            var b=[...document.querySelectorAll('.hs-foot .btn')]
                .find(function(x){return x.textContent.indexOf('last scan ·')===0;});
            return b ? b.textContent : '';
        })()""")
        check("report.last_scan_button_in_picker", bool(last_btn), f"({last_btn!r})")
        js(window, """[...document.querySelectorAll('.hs-foot .btn')]
            .find(function(x){return x.textContent.indexOf('last scan ·')===0;}).click()""")
        time.sleep(0.4)
        res_txt = js(window, "(document.querySelector('.hs-results')||{}).textContent") or ""
        check("report.last_scan_reopens_with_filters",
              bool(js(window, "!!document.querySelector('.hs-reset')"))
              and "S04FLIP" not in res_txt,
              "(the kept report carries its view filters)")
        # reset filters brings every finding back
        js(window, "document.querySelector('.hs-reset').click()")
        time.sleep(0.4)
        check("report.reset_restores_everything",
              "S04FLIP" in (js(window, "(document.querySelector('.hs-results')||{}).textContent") or "")
              and not js(window, """(function(){
                  var b=document.querySelector('.hs-reset');
                  return b && b.style.display!=='none';
              })()"""))
        # add-to-editor from a program header (stubbed ws_robot_programs);
        # the RB010 row is STILL open — expansion survives the reset repaint
        ws_before = js(window, "BV.workspace.count()")
        check("report.expansion_survives_reset",
              bool(js(window, "!!document.querySelector('.hs-row.open .hs-prog-h')")))
        js(window, """document.querySelector('.hs-row.open .hs-prog-h').dispatchEvent(
            new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:220,clientY:220}))""")
        time.sleep(0.3)
        js(window, """[...document.querySelectorAll('.ctx-menu .ctx-item')]
            .find(function(b){return b.textContent.indexOf('add S04FLIP to editor')>=0;}).click()""")
        added_ws = poll(window, "BV.workspace.count() > %d ? 'y' : ''" % (ws_before or 0))
        check("report.add_to_editor_adds_entry", added_ws == "y",
              f"({ws_before} -> {js(window, 'BV.workspace.count()')})")
        js(window, """(function(){
            BV.workspace.entries().filter(function(e){
                return String(e.root).indexOf('PROBE-ROOT-')===0; })
              .forEach(function(e){ BV.workspace.remove(e.id); });
        })()""")
        js(window, """BV.api.call=window.__realCall3;
            document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));""")
        poll(window, "document.getElementById('modal-root').classList.contains('hidden')")

        # ---- the scan window with NOTHING selected: a report viewer ----
        # it used to be unreachable (the menu item greyed out), so re-reading
        # a finished report cost you a robot selection you did not want
        js(window, "BV.scanUI.open([])")
        got = poll(window, "!!document.querySelector('.hs-results') ? 'report' : ''")
        check("noselect.opens_straight_to_last_report", got == "report",
              "(no robots + a kept report = the report, not an empty picker)")
        js(window, """[...document.querySelectorAll('.hs-report-head .btn')]
            .find(function(b){return b.textContent==='scan again';}).click()""")
        poll(window, "!!document.querySelector('.hs-check')")
        gate = js(window, """JSON.stringify((function(){
            var go=[...document.querySelectorAll('.hs-foot .btn')]
                .find(function(b){return b.textContent==='scan';});
            var last=[...document.querySelectorAll('.hs-foot .btn')]
                .some(function(b){return b.textContent.indexOf('last scan ·')===0;});
            return {disabled: !!(go && go.disabled), last: last,
                    note: (document.querySelector('.hs-info')||{}).textContent||''};
        })())""")
        gate_d = json.loads(gate or "{}")
        check("noselect.scan_disabled_and_says_why",
              gate_d.get("disabled") is True and "no robots selected" in gate_d.get("note", ""),
              f"({gate})")
        check("noselect.last_scan_still_offered", gate_d.get("last") is True)

        # ---- the picker's own layout: stapled foot, one scroller, packed ----
        layout = js(window, """JSON.stringify((function(){
            var m=document.querySelector('.hs-modal');
            var mb=m.querySelector('.modal-body');
            var sc=document.querySelector('.hs-scroll');
            var ft=document.querySelector('.hs-foot');
            var q=document.querySelector('.hs-findinput');
            var cats=document.querySelector('.hs-cats');
            var box=m.getBoundingClientRect();
            var rows=[...document.querySelectorAll('.hs-check')];
            return {
              bodyOverflow: getComputedStyle(mb).overflowY,
              scroller: !!sc && getComputedStyle(sc).overflowY === 'auto',
              footBelowScroll: !!(sc && ft) &&
                  ft.getBoundingClientRect().top >= sc.getBoundingClientRect().bottom - 1,
              footInsideModal: !!ft &&
                  ft.getBoundingClientRect().bottom <= box.bottom + 1,
              findStapled: !!(q && sc) &&
                  q.getBoundingClientRect().top >= sc.getBoundingClientRect().bottom - 1,
              columns: cats ? getComputedStyle(cats).columnCount : '0',
              wide: Math.round(box.width / window.innerWidth * 100),
              topGap: Math.round(box.top),
              bottomGap: Math.round(window.innerHeight - box.bottom),
              rowH: rows.length ? Math.round(rows[0].getBoundingClientRect().height) : 0,
              descEls: document.querySelectorAll('.hs-check .hs-desc').length,
              tipped: rows.filter(function(r){ return (r.title||'').length > 10; }).length,
              nRows: rows.length};
        })())""")
        lay = json.loads(layout or "{}")
        check("picker.body_does_not_scroll", lay.get("bodyOverflow") == "hidden",
              f"({layout} — the host owns [head][scroll][foot])")
        check("picker.one_scroller", lay.get("scroller") is True)
        check("picker.foot_is_stapled",
              lay.get("footBelowScroll") is True and lay.get("footInsideModal") is True,
              "(the scan / last-scan buttons must never scroll away)")
        check("picker.find_bar_stapled_too", lay.get("findStapled") is True,
              "(find is an action you reach for, not an option you browse)")
        check("picker.categories_flow_in_columns", lay.get("columns") == "3",
              "(a short category must not reserve the tallest one's height)")
        check("picker.uses_the_screen", (lay.get("wide") or 0) >= 70,
              f"({lay.get('wide')}% of the window — it carries a lot)")
        # #modal-root pins dialogs 10vh from the top, so the height must leave
        # the SAME gap underneath or the window reads as dropped, not centred
        check("picker.window_is_centred",
              abs((lay.get("topGap") or 0) - (lay.get("bottomGap") or 0)) <= 6,
              f"(top {lay.get('topGap')} vs bottom {lay.get('bottomGap')})")
        # a list, not a stack of paragraphs: one line per check, description
        # in the tooltip
        check("picker.rows_are_one_line", 0 < (lay.get("rowH") or 0) <= 34,
              f"({lay.get('rowH')}px per check)")
        check("picker.descriptions_are_tooltips",
              lay.get("descEls") == 0 and lay.get("tipped") == lay.get("nRows"),
              f"({lay.get('tipped')} of {lay.get('nRows')} rows carry their description as a title)")
        js(window, "document.querySelector('.modal-x').click()")
        poll(window, "document.getElementById('modal-root').classList.contains('hidden')")

        # ---- link cameras: lives in the functions… dropdown now ----
        check("manage.link_cams_not_in_head",
              not js(window, "!!document.querySelector('.lib-link-cams')"))
        js(window, "document.querySelector('.lib-act-functions').click()")
        mi = poll(window, """(function(){
            var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
            return items.some(function(b){return b.textContent.trim()==='link cameras';})
                ? 'y' : '';
        })()""")
        check("fns.link_cams_in_menu", mi == "y")
        # the "last backup…" entry opens the REPORT alone — no actions bar
        js(window, """(function(){
            var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
            items.filter(function(b){return b.textContent.indexOf('last backup')===0;})[0].click();
        })()""")
        mb = poll(window, """(function(){
            var m=document.querySelector('.mb-modal');
            if(!m || !m.querySelector('.mb-partial')) return '';
            return JSON.stringify({ actbar: !!m.querySelector('.mb-actbar'),
                                    partial: true });
        })()""")
        mb = json.loads(mb or "{}")
        check("fns.report_opens_without_actbar",
              mb.get("actbar") is False and mb.get("partial") is True, f"({mb})")
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        check("manage.modal_closes", bool(poll(window,
              "document.getElementById('modal-root').classList.contains('hidden')")))

        # ---- open the camera backup: photos tab layout ----
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('CELL-01CAM01')>=0;});
            row.click();
        })()""")
        # a camera-only backup has no overview, so route() falls back - and the
        # fallback must land on a tab you could have CLICKED, never on one of
        # the hidden always-on ones (edit/search/compare/pdiff)
        check("photos.route_replaced_to_photos",
              poll(window, "location.hash==='#photos' ? 'y' : ''") == "y",
              f"(hash={js(window, 'location.hash')!r})")
        check("photos.fallback_skips_hidden_tabs",
              not js(window, """BV.tabs.filter(function(t){return t.hidden;})
                  .some(function(t){return location.hash === '#' + t.id;})"""),
              "(a hidden always-on tab must never be a route fallback)")
        check("photos.hero", bool(poll(window, "!!document.querySelector('#photo-hero img')")))

        layout = js(window, """(function(){
            var hero=document.getElementById('photo-hero');
            var wrap=hero.parentElement;
            var filterRow=hero.nextElementSibling;
            var seg=filterRow ? filterRow.querySelector('.seg') : null;
            var segLabels=seg ? [...seg.querySelectorAll('button')].map(function(b){
                return b.textContent.trim();}) : [];
            var panelSeg=hero.querySelector('.seg');
            var viewLabels=panelSeg ? [...panelSeg.querySelectorAll('button')].map(function(b){
                return b.textContent.trim();}) : [];
            var rep=[...hero.children].find(function(c){
                return c.textContent.indexOf('full report')>=0;});
            return JSON.stringify({
              toolbarSeg: !!document.querySelector('#toolbar .seg'),
              gridAfterFilter: filterRow && filterRow.nextElementSibling===document.getElementById('photo-grid'),
              segLabels: segLabels,
              viewLabels: viewLabels,
              heroKids: hero.children.length,
              report: !!rep,
              reportScrolls: rep ? getComputedStyle(rep.lastElementChild).overflowY : '',
              reportSections: rep ? rep.textContent.indexOf('Vision Tool Settings')>=0 : false,
              fsButton: [...document.querySelectorAll('button')].some(function(b){
                  return b.textContent==='fullscreen';}),
            });
        })()""")
        layout = json.loads(layout or "{}")
        check("photos.filter_not_in_toolbar", layout.get("toolbarSeg") is False)
        check("photos.filter_above_grid", layout.get("gridAfterFilter") is True)
        check("photos.filter_counts",
              layout.get("segLabels") == ["all 2", "pass 1", "fail 1"],
              f"({layout.get('segLabels')})")
        check("photos.filtered_raw_labels",
              layout.get("viewLabels") == ["filtered", "raw"],
              f"({layout.get('viewLabels')})")
        check("photos.report_beside_attrs",
              layout.get("heroKids") == 3 and layout.get("report") is True,
              f"(kids={layout.get('heroKids')})")
        check("photos.report_scrollable", layout.get("reportScrolls") == "auto")
        check("photos.report_has_sections", layout.get("reportSections") is True)
        check("photos.no_fullscreen_button", layout.get("fsButton") is False)

        # raw persists across photo changes
        js(window, """(function(){
            var seg=document.getElementById('photo-hero').querySelector('.seg');
            [...seg.querySelectorAll('button')].find(function(b){
                return b.textContent.trim()==='raw';}).click();
        })()""")
        time.sleep(0.4)
        js(window, "document.querySelectorAll('#photo-grid > div')[1].click()")
        mode = poll(window, """(function(){
            var seg=document.getElementById('photo-hero').querySelector('.seg');
            if(!seg) return '';
            var a=seg.querySelector('button.active');
            return a ? a.textContent.trim() : '';
        })()""")
        check("photos.raw_sticks_across_photos", mode == "raw", f"(got {mode!r})")

        # ---- fullscreen: click image, wheel zoom, drag pan, guarded close ----
        # the zoom-in click lives on the FIGURE, which the CV-X crossfade work
        # (c8b3584) wrapped in a flex column - so the hero's direct child is the
        # column now and the figure is one level deeper. Clicking the column did
        # nothing and nothing noticed, because nothing runs this file.
        js(window, "document.querySelector('#photo-hero > div > div').click()")
        fs0 = poll(window, """(function(){
            var o=document.querySelector('.photo-fsov');
            return (o && o.querySelector('img') && o.querySelector('button')) ? 'y' : '';
        })()""")
        check("fs.overlay_with_close", fs0 == "y")
        fs = js(window, """(function(){
            var o=document.querySelector('.photo-fsov');
            var img=o.querySelector('img');
            o.dispatchEvent(new WheelEvent('wheel',
                {deltaY:-100, clientX:640, clientY:400, bubbles:true, cancelable:true}));
            var zoomed=img.style.transform.indexOf('scale')>=0;
            var t0=img.style.transform;
            img.dispatchEvent(new MouseEvent('mousedown',
                {button:0, clientX:600, clientY:400, bubbles:true, cancelable:true}));
            o.dispatchEvent(new MouseEvent('mousemove',
                {clientX:650, clientY:440, bubbles:true}));
            o.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
            var panned=img.style.transform!==t0;
            o.dispatchEvent(new MouseEvent('click', {bubbles:true}));
            var survived=document.body.contains(o);      /* click-after-drag guarded */
            o.dispatchEvent(new MouseEvent('click', {bubbles:true}));
            var closed=!document.body.contains(o);       /* clean backdrop click closes */
            return JSON.stringify({zoomed:zoomed, panned:panned,
                survived:survived, closed:closed});
        })()""")
        fs = json.loads(fs or "{}")
        check("fs.wheel_zooms", fs.get("zoomed") is True)
        check("fs.drag_pans", fs.get("panned") is True)
        check("fs.click_after_drag_guarded", fs.get("survived") is True)
        check("fs.backdrop_click_closes", fs.get("closed") is True)

        # X button closes too
        js(window, "document.querySelector('#photo-hero > div > div').click()")
        poll(window, "document.querySelector('.photo-fsov') ? 'y' : ''")
        js(window, "document.querySelector('.photo-fsov button').click()")
        check("fs.x_closes",
              poll(window, "!document.querySelector('.photo-fsov') ? 'y' : ''") == "y")

        # ---- backspace: history walks OUT of a camera-only backup ----
        js(window, "history.back()")
        athome = poll(window, "location.hash==='#home' ? 'y' : ''")
        check("back.leaves_camera_backup", athome == "y",
              f"(hash={js(window, 'location.hash')!r})")
        time.sleep(1.2)   # the old bug re-forwarded to #photos right about now
        check("back.stays_home",
              js(window, "location.hash") == "#home",
              f"(hash={js(window, 'location.hash')!r})")

        # ---- program view: navigator card, labels xref, editor-grade selection ----
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('RB020R01B01')>=0;});
            row.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            row.dispatchEvent(new MouseEvent('click',{bubbles:true}));
        })()""")
        opened = poll(window, "(function(){var m=BV.state.manifest;"
                              "return m && ((m.robot_name||m.name||'')+(m.path||''))"
                              ".indexOf('RB020R01B01')>=0 ? 'y':'';})()")
        check("prog.backup_opened", opened == "y",
              f"(hash={js(window, 'location.hash')!r})")
        js(window, "location.hash='#programs/TESTMAIN.LS'")
        nlines = poll(window, "document.querySelectorAll('.viewer .code-line').length")
        check("prog.viewer_lines", nlines == 10, f"(got {nlines})")

        # selection mechanics: the whole box is selectable, text fills the row,
        # line numbers stay out of copies
        mech = json.loads(js(window, """(function(){
            var v=document.querySelector('.viewer');
            return JSON.stringify({
              vsel:getComputedStyle(v).userSelect,
              lnsel:getComputedStyle(v.querySelector('.code-line .ln')).userSelect,
              grow:getComputedStyle(v.querySelector('.code-line .lc')).flexGrow,
            });
        })()""") or "{}")
        check("prog.viewer_selectable", mech.get("vsel") == "text", f"({mech})")
        check("prog.linenums_not_selectable", mech.get("lnsel") == "none", f"({mech})")
        check("prog.text_fills_row", mech.get("grow") == "1", f"({mech})")

        # releasing a text selection on a CALL token must NOT navigate
        guard = json.loads(js(window, """(function(){
            var before=location.hash;
            var call=document.querySelector('#srcline-4 .tp-call');
            if(!call) return JSON.stringify({err:'no tp-call on line 4'});
            var sel=window.getSelection(), rg=document.createRange();
            rg.selectNodeContents(document.querySelector('#srcline-4 .lc'));
            sel.removeAllRanges(); sel.addRange(rg);
            call.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            var withSel=location.hash;
            sel.removeAllRanges();
            return JSON.stringify({before:before, withSel:withSel});
        })()""") or "{}")
        check("prog.selection_suppresses_hop",
              "err" not in guard and guard.get("withSel") == guard.get("before"), f"({guard})")
        # ...and a plain click still hops
        js(window, "document.querySelector('#srcline-4 .tp-call')"
                   ".dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        check("prog.call_token_hops",
              poll(window, "location.hash==='#programs/TESTSUB.LS' ? 'y':''") == "y",
              f"(hash={js(window, 'location.hash')!r})")
        js(window, "history.back()")
        poll(window, "location.hash==='#programs/TESTMAIN.LS' ? 'y':''")
        poll(window, "document.querySelectorAll('.viewer .code-line').length")

        # navigator card: three segments, call tree default, root + child drawn
        poll(window, "document.querySelector('.prognav .ct-root') ? 'y':''")
        nav = json.loads(js(window, """(function(){
            var seg=[...document.querySelectorAll('.prognav .seg button')];
            var act=seg.find(function(b){return b.classList.contains('active');});
            return JSON.stringify({
              n:seg.length,
              active:act?act.textContent:'',
              root:(document.querySelector('.prognav .ct-root > .ct-row .ct-name')||{}).textContent||'',
              child:!!document.querySelector('.prognav .ct-node:not(.ct-root) .ct-name'),
            });
        })()""") or "{}")
        check("nav.three_segments", nav.get("n") == 3, f"({nav})")
        check("nav.tree_default", nav.get("active") == "call tree", f"({nav})")
        check("nav.tree_root_and_child",
              nav.get("root") == "TESTMAIN" and nav.get("child") is True, f"({nav})")

        # labels segment: defs in program order, broken jump flagged, honest zeros
        js(window, "[...document.querySelectorAll('.prognav .seg button')]"
                   ".find(function(b){return b.textContent.indexOf('labels')>=0;}).click()")
        lx = json.loads(js(window, """(function(){
            var defs=[...document.querySelectorAll('.lblx-def')];
            return JSON.stringify({
              ids:defs.map(function(d){return d.querySelector('.lblx-id').textContent;}),
              broken:document.querySelectorAll('.lblx-def.lblx-broken').length,
              jumps:document.querySelectorAll('.lblx-jmp').length,
              none:document.querySelectorAll('.lblx-none').length,
            });
        })()""") or "{}")
        check("lbl.defs_listed",
              lx.get("ids") == ["LBL[1]", "LBL[2]", "LBL[3]", "LBL[99]"], f"({lx})")
        check("lbl.broken_flagged", lx.get("broken") == 1, f"({lx})")
        check("lbl.jump_rows", lx.get("jumps") == 3, f"({lx})")
        check("lbl.unjumped_honest", lx.get("none") == 1, f"({lx})")

        # clicks land: jump row -> its line, def row -> the LBL line, and a
        # LBL token in the source -> its definition
        js(window, "document.querySelector('.lblx-jmp').click()")
        check("lbl.jump_click_flashes",
              bool(poll(window, "document.querySelector('#srcline-5.flash') ? 'y':''")))
        js(window, "document.querySelector('.lblx-def').click()")
        check("lbl.def_click_flashes",
              bool(poll(window, "document.querySelector('#srcline-2.flash') ? 'y':''")))
        js(window, "document.querySelector('#srcline-3 .tp-label')"
                   ".dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        check("lbl.token_click_goes_to_def",
              bool(poll(window, "document.querySelector('#srcline-6.flash') ? 'y':''")))

        # the chosen segment survives leaving and reopening the program
        js(window, "location.hash='#programs'")
        poll(window, "location.hash==='#programs' ? 'y':''")
        js(window, "location.hash='#programs/TESTMAIN.LS'")
        poll(window, "document.querySelectorAll('.lblx-def').length")
        seg2 = js(window, "(function(){var b=[...document.querySelectorAll("
                          "'.prognav .seg button')].find(function(x){return "
                          "x.classList.contains('active');});return b?b.textContent:'';})()")
        check("nav.segment_remembered", (seg2 or "").startswith("labels"), f"(got {seg2!r})")

        # attributes card folds (and the fold hides the kv body)
        att = json.loads(js(window, """(function(){
            var h=[...document.querySelectorAll('.split .card h3')].find(function(x){
                return x.textContent.indexOf('attributes')>=0;});
            var card=h.closest('.card');
            var openBefore=card.classList.contains('open');
            h.click();
            var openAfter=card.classList.contains('open');
            var bodyHidden=getComputedStyle(card.querySelector('.bv-collapse-body')).display==='none';
            h.click();
            return JSON.stringify({openBefore:openBefore,openAfter:openAfter,bodyHidden:bodyHidden});
        })()""") or "{}")
        check("attrs.collapsible",
              att.get("openBefore") is True and att.get("openAfter") is False
              and att.get("bodyHidden") is True, f"({att})")

        # expand grows the card and uncaps the scroll body
        ex = json.loads(js(window, """(function(){
            var btn=document.querySelector('.prognav .icon-btn');
            btn.click();
            var card=document.querySelector('.prognav');
            var big=card.classList.contains('expanded');
            var mh=getComputedStyle(card.querySelector('.prognav-body')).maxHeight;
            btn.click();
            return JSON.stringify({big:big, mh:mh,
              back:!card.classList.contains('expanded')});
        })()""") or "{}")
        check("nav.expand_toggles", ex.get("big") is True and ex.get("back") is True, f"({ex})")
        check("nav.expanded_uncaps_body", ex.get("mh") == "none", f"({ex})")

        # ---- mh valves: large-text reflow (the kv crush regression) ----
        # the historic failure: at the grid's 19rem card minimum the nowrap
        # max-content label starved the 1fr value track and word-break then
        # shattered "no / no" one character per line. The fix floors the value
        # (overflow-wrap + NBSP-joined pairs) and lets the LABEL wrap instead.
        js(window, "location.hash='#mhvalves'")
        nkv = poll(window, "document.querySelectorAll('.mhv-valve .kv dt').length")
        check("mhv.card_renders", bool(nkv), f"(kv rows: {nkv})")

        MEASURE = """(function(){
            function lines(el){
                var rg=document.createRange(); rg.selectNodeContents(el);
                var rs=rg.getClientRects(), tops=[], i, t;
                for(i=0;i<rs.length;i++){ if(!rs[i].width) continue;
                    t=Math.round(rs[i].top); if(tops.indexOf(t)<0) tops.push(t); }
                return tops.length||1;
            }
            var dts=[...document.querySelectorAll('.mhv-valve .kv dt')];
            var tg=dts.find(function(d){return d.textContent.indexOf('toggle retry')===0;});
            var ms=dts.find(function(d){return d.textContent.indexOf('operation timeout')===0;});
            if(!tg||!ms) return JSON.stringify({err:'rows missing'});
            var v=tg.nextElementSibling, mv=ms.nextElementSibling;
            return JSON.stringify({
                vLines:lines(v), vW:Math.round(v.getBoundingClientRect().width),
                lLines:lines(tg), msLines:lines(mv),
                nbsp:v.textContent.indexOf('\\u00A0/\\u00A0')>=0});
        })()"""

        m0 = json.loads(js(window, MEASURE) or "{}")
        check("mhv.value_one_line_default",
              m0.get("vLines") == 1 and m0.get("msLines") == 1, f"({m0})")
        check("mhv.pair_nbsp_joined", m0.get("nbsp") is True, f"({m0})")

        # 32px text in a card clamped to the 19rem grid minimum - the exact
        # geometry that used to char-split. Values must hold one line; the
        # long label is the one allowed to wrap.
        js(window, """(function(){
            document.documentElement.style.fontSize='32px';
            document.body.style.fontSize='32px';
            document.querySelector('.mhv-valve').style.maxWidth='19rem';
        })()""")
        m1 = json.loads(js(window, MEASURE) or "{}")
        check("mhv.value_one_line_32px_min_card", m1.get("vLines") == 1, f"({m1})")
        check("mhv.value_not_starved_32px", (m1.get("vW") or 0) >= 60, f"({m1})")
        check("mhv.label_wraps_instead", 1 <= (m1.get("lLines") or 0) <= 3, f"({m1})")
        check("mhv.ms_value_one_line_32px", m1.get("msLines") == 1, f"({m1})")

        # dialogs grow with text size (the px-frozen modal fix): still at 32px
        # root, .modal's max-width must resolve far beyond the old 640px cap
        md = json.loads(js(window, """(function(){
            var m=BV.modal('probe', BV.el('div', null, 'x'));
            var mw=parseFloat(getComputedStyle(document.querySelector('.modal')).maxWidth);
            m.close(true);
            return JSON.stringify({mw:mw});
        })()""") or "{}")
        check("mhv.modal_grows_with_text", (md.get("mw") or 0) > 900, f"({md})")

        js(window, """(function(){
            document.documentElement.style.fontSize='';
            document.body.style.fontSize='';
            var c=document.querySelector('.mhv-valve'); if(c) c.style.maxWidth='';
        })()""")

        js(window, "BV.goHome()")
        check("prog.returns_home", bool(poll(window, "location.hash==='#home' ? 'y':''")))

        # ---- tab hotkeys: badges and keys continue past 9 on the number row ----
        check("keys.badge_fn",
              js(window, "BV.tabKeyBadge(9)==='-' && BV.tabKeyBadge(10)==='=' && BV.tabKeyBadge(11)===''"))
        js(window, """(function(){
            BV.state.manifest={name:'x', path:'x', file_count:1, robot_name:'RBX',
              tabs:{overview:true,frames:true,io:true,registers:true,programs:true,
                    dcs:true,sysvars:true,mhvalves:true,photos:true,files:true,view3d:true}};
            BV.route();
        })()""")
        # the strip became the screens dropdown - same number-row contract,
        # asserted on the menu's rows ("<badge> · <label>", keyboard order).
        # BV.screensMenu is the shared builder behind both hosts (the active
        # session tab in the main window, the standalone button in solo);
        # this synthetic manifest has no session tab, so call it directly.
        js(window, "BV.screensMenu(document.getElementById('topbar-cubes'))")
        badges = poll(window, """(function(){
            var rows=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
            if(!rows.length) return null;
            return JSON.stringify({
              labels: rows.map(function(b){return b.textContent.trim();}),
              pos: BV.positionalTabs().map(function(t){return t.id;}),
            });
        })()""")
        badges = json.loads(badges or "{}")
        lbl = badges.get("labels") or []

        def at(prefix):
            hits = [i for i, s in enumerate(lbl) if s.startswith(prefix)]
            return hits[0] if hits else -1
        i9, i0, idash = at("9 · photos"), at("0 · "), at("- · files")
        check("keys.ninth_badge_9", i9 >= 0, f"({lbl})")
        check("keys.view3d_badge_0", i0 >= 0, f"({lbl})")
        check("keys.tenth_tab_badge_dash", idash >= 0, f"({lbl})")
        check("keys.zero_sits_between_9_and_dash",
              0 <= i9 < i0 < idash, f"({lbl})")
        check("keys.positional_list",
              badges.get("pos", [])[-1:] == ["files"] and len(badges.get("pos", [])) == 10,
              f"({badges.get('pos')})")
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        time.sleep(0.3)
        js(window, """document.activeElement && document.activeElement.blur();
            document.dispatchEvent(new KeyboardEvent('keydown', {key:'-', bubbles:true}))""")
        check("keys.dash_opens_tenth",
              poll(window, "location.hash==='#files' ? 'y' : ''") == "y",
              f"(hash={js(window, 'location.hash')!r})")

        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


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
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
