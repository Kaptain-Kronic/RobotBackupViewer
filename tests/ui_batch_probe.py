"""Hidden-window probe for the 2026-07 UI batch: library favorites, the
generalized edit modal + libTree link picker, the photos-tab rework
(filtered/raw, report pane, moved filter row, zoom/pan fullscreen), the
camera-backup backspace trap, and the -/= tab hotkeys.

Fully synthetic and identifier-clean: builds its own library tree in a temp
folder and redirects APPDATA there BEFORE importing the app, so the real
settings/library are never touched. Run: python tests/ui_batch_probe.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

# the strip head prints a ★ — don't let a cp1252 console kill the probe
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# isolate EVERYTHING before any backupviewer import: settings.json and
# library.json resolve under APPDATA at call time
_TMP = Path(tempfile.mkdtemp(prefix="bv_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []

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


def build_tree(lib: Path) -> None:
    line = lib / "FakePlant" / "LINE01"
    for rb in ("RB010R01B01", "RB020R01B01"):
        snap = line / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / "SUMMARY.DG").write_text("x", encoding="utf-8")

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

        check("boot.tabs_registered", js(window, "BV.tabs.length") == 15,
              f"(got {js(window, 'BV.tabs.length')})")

        # ---- home library renders the synthetic tree ----
        nrows = poll(window, "document.querySelectorAll('.lib-robot').length")
        check("home.rows", nrows == 3, f"(got {nrows})")
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
              json.loads(items or "[]") == ["edit", "add note", "hide", "open folder"],
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
              "location.hash==='#home' && document.querySelectorAll('.lib-robot').length===3")))
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

        # ---- link cameras: moved off the library head into manage backups ----
        check("manage.link_cams_not_in_head",
              not js(window, "!!document.querySelector('.lib-link-cams')"))
        js(window, "document.querySelector('.lib-act-manage').click()")
        mb = poll(window, """(function(){
            var b=document.querySelector('.mb-actbar .mb-link-cams');
            return b ? b.textContent : '';
        })()""")
        check("manage.link_cams_in_modal", mb == "link cameras", f"(got {mb!r})")
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        check("manage.modal_closes", bool(poll(window,
              "document.getElementById('modal-root').classList.contains('hidden')")))

        # ---- open the camera backup: photos tab layout ----
        js(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('CELL-01CAM01')>=0;});
            row.click();
        })()""")
        check("photos.route_replaced_to_photos",
              poll(window, "location.hash==='#photos' ? 'y' : ''") == "y",
              f"(hash={js(window, 'location.hash')!r})")
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
        js(window, "document.querySelector('#photo-hero > div').click()")
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
        js(window, "document.querySelector('#photo-hero > div').click()")
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

        # ---- tab hotkeys: badges and keys continue past 9 on the number row ----
        check("keys.badge_fn",
              js(window, "BV.tabKeyBadge(9)==='-' && BV.tabKeyBadge(10)==='=' && BV.tabKeyBadge(11)===''"))
        js(window, """(function(){
            BV.state.manifest={name:'x', path:'x', file_count:1, robot_name:'RBX',
              tabs:{overview:true,frames:true,io:true,registers:true,programs:true,
                    dcs:true,sysvars:true,mhvalves:true,photos:true,files:true,view3d:true}};
            BV.route();
        })()""")
        badges = poll(window, """(function(){
            var f=document.querySelector('#tab-files .tab-num');
            if(!f) return null;
            var v3=document.getElementById('tab-view3d');
            return JSON.stringify({
              files: f.textContent,
              photos: document.querySelector('#tab-photos .tab-num').textContent,
              view3d: v3.querySelector('.tab-num').textContent,
              kbOrder: v3.previousElementSibling.id==='tab-photos'
                    && v3.nextElementSibling.id==='tab-files',
              pos: BV.positionalTabs().map(function(t){return t.id;}),
            });
        })()""")
        badges = json.loads(badges or "{}")
        check("keys.tenth_tab_badge_dash", badges.get("files") == "-", f"({badges})")
        check("keys.ninth_badge_9", badges.get("photos") == "9")
        check("keys.view3d_badge_0", badges.get("view3d") == "0")
        check("keys.zero_sits_between_9_and_dash", badges.get("kbOrder") is True,
              f"({badges})")
        check("keys.positional_list",
              badges.get("pos", [])[-1:] == ["files"] and len(badges.get("pos", [])) == 10,
              f"({badges.get('pos')})")
        js(window, """document.activeElement && document.activeElement.blur();
            document.dispatchEvent(new KeyboardEvent('keydown', {key:'-', bubbles:true}))""")
        check("keys.dash_opens_tenth",
              poll(window, "location.hash==='#files' ? 'y' : ''") == "y",
              f"(hash={js(window, 'location.hash')!r})")

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
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
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
