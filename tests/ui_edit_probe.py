"""Hidden-window probe for the structured .ls editor (view<->edit + export).

Covers what pytest can't: the real DOM chain - view mode's highlighted source,
flipping to edit mode mounts the single-layer BV.lsEditor (auto-number gutter, live
highlight layer, transparent textarea), the details panel exposes editable
attributes + point data (incl. initializing a masked point), and export writes
a renumbered, attribute-patched, position-patched .ls to a NEW folder while the
backup stays byte-for-byte untouched. The native folder picker can't be
automated, so the probe calls export_edited_programs directly with a temp dest -
exactly what doSave() does after the picker returns.

Fully synthetic and identifier-clean: an RB* fake under FakePlant in a temp
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

_TMP = Path(tempfile.mkdtemp(prefix="bv_edit_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []
ROBOT = "RB010R01B01"
PROG = "EDITME.LS"
PROG_BYTES = (
    b"/PROG  EDITME\r\n"
    b"/ATTR\r\n"
    b"OWNER\t\t= MNEDITOR;\r\n"
    b'COMMENT\t\t= "EDIT ME";\r\n'
    b"PROTECT\t\t= READ_WRITE;\r\n"
    b"/MN\r\n"
    b"   1:  !setup ;\r\n"
    b"   2:  DO[1]=ON ;\r\n"
    b"   3:J P[1] 100% FINE ;\r\n"
    b"/POS\r\n"
    b"P[1]{\r\n"
    b"   GP1:\r\n"
    b"\tUF : F, UT : F,\t\tCONFIG : '',\r\n"
    b"\tX = ********  mm,\tY = ********  mm,\tZ = ********  mm,\r\n"
    b"\tW = ******** deg,\tP = ******** deg,\tR = ******** deg\r\n"
    b"};\r\n"
    b"/END\r\n"
)

SNAP = None   # the backup folder (set in main)
DEST = _TMP / "export"


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

        # ---- open the backup through the real frontend path ----
        js(window, """window._open='';
            BV.api.call('open_backup', %s).then(function(m){
                BV.session.open(m); BV.state.setManifest(m);
                window._open = m.sid || 'ok';
            }, function(e){ window._open = 'err:' + e.code; })""" % json.dumps(str(SNAP)))
        opened = poll(window, "window._open")
        check("open.backup", bool(opened) and not str(opened).startswith("err"), f"(got {opened!r})")

        # ---- view mode: highlighted source + the mode toggle ----
        js(window, "location.hash = '#programs/%s'" % PROG)
        nlines = poll(window, "document.querySelectorAll('.code-line').length")
        check("view.source_lines", bool(nlines) and nlines >= 3, f"(got {nlines})")
        check("view.toggle_present",
              bool(js(window, "[...document.querySelectorAll('.seg button')]"
                              ".some(function(b){return b.textContent.trim()==='edit';})")))

        # ---- flip to edit: the single-layer contenteditable editor mounts ----
        js(window, """[...document.querySelectorAll('.seg button')]
            .find(function(b){return b.textContent.trim()==='edit';}).click()""")
        seeded = poll(window, "(document.querySelector('.lsed-code')||{}).textContent || ''")
        check("edit.editor_mounted", bool(seeded))
        check("edit.body_only",
              isinstance(seeded, str)
              and seeded.split("\n") == ["!setup", "DO[1]=ON", "J P[1] 100% FINE"],
              f"(got {seeded!r})")          # no numbers, no ';', no header
        check("edit.single_layer",           # the two-layer overlay is gone for good
              not js(window, "!!document.querySelector('.lsed-ta')")
              and not js(window, "!!document.querySelector('.lsed-hl')"))
        check("edit.contenteditable",
              bool(js(window, "(document.querySelector('.lsed-code')||{}).isContentEditable")))
        check("edit.gutter_numbers",
              (js(window, "(document.querySelector('.lsed-nums')||{}).textContent||''") or "")
              .startswith("1\n2\n3"))
        check("edit.live_highlight",
              bool(js(window, "document.querySelector('.lsed-code .tp-comment')"
                              " && document.querySelector('.lsed-code .tp-motion')")))
        # bold/italic tokens INSIDE the editable layer - exactly what the old
        # overlay could never render without ghosting - plus the editor font
        # on every token (the UA `code{font-family:monospace}` lesson).
        check("edit.bold_italic_inline", bool(js(window, """(function(){
            var m=document.querySelector('.lsed-code .tp-motion');
            var c=document.querySelector('.lsed-code .tp-comment');
            var code=document.querySelector('.lsed-code');
            return !!m && !!c && parseInt(getComputedStyle(m).fontWeight,10) >= 600
                && getComputedStyle(c).fontStyle === 'italic'
                && getComputedStyle(m).fontFamily === getComputedStyle(code).fontFamily;
        })()""")))
        # the selection color must be OPAQUE: Chromium paints token-span
        # fragments twice, and a translucent ::selection stacks into darker
        # doubled bands (the overlap-highlight bug)
        check("edit.selection_opaque", bool(js(window, """(function(){
            var bg=getComputedStyle(document.querySelector('.lsed-code'),
                                    '::selection').backgroundColor;
            return !/\\/\\s*0?\\.\\d+\\)/.test(bg) && bg.indexOf('rgba') !== 0;
        })()""")))
        # a selection must survive the re-highlight repaint (text-offset
        # save/restore) - asserted here in the real WebView2 shell
        check("edit.selection_survives_repaint",
              js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            try { code.focus(); } catch (e) {}
            var txt=code.textContent, s=txt.indexOf('DO[1]=ON');
            var walker=document.createTreeWalker(code, NodeFilter.SHOW_TEXT, null);
            var pos=0, node, r=document.createRange(), placed=0;
            while ((node=walker.nextNode())) {
              var len=node.nodeValue.length;
              if (!placed && s <= pos+len) { r.setStart(node, s-pos); placed=1; }
              if (placed && s+8 <= pos+len) { r.setEnd(node, s+8-pos); placed=2; break; }
              pos+=len;
            }
            if (placed!==2) return 'place:'+placed;
            var sel=window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
            code.dispatchEvent(new Event('input',{bubbles:true}));
            return window.getSelection().toString();
        })()""") == "DO[1]=ON")
        check("edit.details_hidden_by_default",
              not js(window, "!!document.querySelector('.edattr-row')"))
        # the editable must be sized to its CONTENT, not to the scroller's
        # visible box: when it was stretched, every line you had to scroll to
        # sat outside the element and only its glyphs were clickable (blank
        # space past the text hit the scroller instead). Fills the editor with
        # enough lines to scroll, then clicks blank space on a scrolled-to row.
        check("edit.click_target_covers_scrolled_lines", bool(js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            var scroller=document.querySelector('.lsed-scroll');
            var gut=document.querySelector('.lsed-gutter');
            if(!code||!scroller||!gut) return false;
            var saved=code.textContent;
            var many=[]; for(var i=1;i<=80;i++) many.push('CALL PROG'+i);
            code.textContent=many.join('\\n');
            code.dispatchEvent(new Event('input',{bubbles:true}));
            scroller.scrollTop=600;
            var s=scroller.getBoundingClientRect(), g=gut.getBoundingClientRect();
            var el=document.elementFromPoint(g.right+200, s.top+45);
            var ok=!!(el && (el===code||code.contains(el)));
            scroller.scrollTop=0;
            code.textContent=saved;                 /* restore the real program */
            code.dispatchEvent(new Event('input',{bubbles:true}));
            return ok;
        })()""")))
        # the restore above must leave the buffer exactly as it was, or every
        # export assertion below would be testing the probe's own scratch text
        check("edit.probe_restore_clean", not js(window, "BV.edit.anyDirty()"))

        # ---- type a body edit through the real editable element ----
        js(window, """(function(){
            var code=document.querySelector('.lsed-code');
            var lines=code.textContent.split('\\n');
            lines.splice(2, 0, 'CALL OTHER');           /* insert between 2 and 3 */
            code.textContent=lines.join('\\n');
            code.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        check("edit.dirty", bool(js(window, "BV.edit.anyDirty()")))
        check("edit.gutter_grew",
              (js(window, "(document.querySelector('.lsed-nums')||{}).textContent||''") or "")
              .startswith("1\n2\n3\n4"))
        check("edit.dirty_pill",
              bool(js(window, "[...document.querySelectorAll('.toolbar-slot .pill')]"
                              ".some(function(p){return p.textContent.indexOf('unsaved')>=0;})")))

        # ---- details panel: attr edit + masked position init ----
        js(window, """[...document.querySelectorAll('.toolbar-slot .btn')]
            .find(function(b){return b.textContent.trim()==='details';}).click()""")
        got_attr = poll(window, "!!document.querySelector('.edattr-row input')")
        check("details.attrs_shown", bool(got_attr))
        check("details.positions_shown", bool(js(window, "!!document.querySelector('.edpos')")))
        js(window, """(function(){
            var inp=document.querySelector('.edattr-row input');   /* comment field */
            inp.value='RENAMED';
            inp.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        js(window, """(function(){
            var cells=[...document.querySelectorAll('.edpos-grid .cell')];
            var x=cells.find(function(c){return c.querySelector('span').textContent==='x';});
            var inp=x.querySelector('input');
            inp.value='100.5';
            inp.dispatchEvent(new Event('change',{bubbles:true}));
        })()""")
        check("details.masked_placeholder",
              js(window, """(function(){
                  var cells=[...document.querySelectorAll('.edpos-grid .cell')];
                  var y=cells.find(function(c){return c.querySelector('span').textContent==='y';});
                  return y.querySelector('input').placeholder;
              })()""") == "********")

        # ---- export (bypass the native picker, as doSave does post-dialog) ----
        js(window, """window._exp='';
            BV.api.call('export_edited_programs', BV.edit.edits(), %s).then(function(r){
                BV.edit.markSaved(); window._exp = JSON.stringify(r);
            }, function(e){ window._exp = 'err:' + (e.code||e.message); })""" % json.dumps(str(DEST)))
        exp = poll(window, "window._exp")
        ok = isinstance(exp, str) and exp.startswith("{")
        check("export.endpoint_ok", ok, f"(got {exp!r})")

        out = DEST / PROG
        check("export.file_written", out.is_file())
        if out.is_file():
            data = out.read_bytes()
            check("export.insert_renumbered",
                  b"   3:  CALL OTHER ;\r\n" in data and b"   4:J P[1] 100% FINE ;\r\n" in data)
            check("export.kept_lines_byte_exact",
                  b"   1:  !setup ;\r\n" in data and b"   2:  DO[1]=ON ;\r\n" in data)
            check("export.attr_patched", b'COMMENT\t\t= "RENAMED";\r\n' in data)
            check("export.masked_initialized", b"X = 100.500  mm," in data)
            check("export.other_axes_stay_masked", b"Y = ********  mm," in data)
        check("export.no_part", not list(DEST.glob("*.part")))

        # ---- the trust contract ----
        check("backup.untouched", (SNAP / PROG).read_bytes() == PROG_BYTES)
        check("edit.clean_after_save", not js(window, "BV.edit.anyDirty()"))

        # ---- back to view mode: highlighted source again ----
        js(window, """[...document.querySelectorAll('.seg button')]
            .find(function(b){return b.textContent.trim()==='view';}).click()""")
        back = poll(window, "document.querySelectorAll('.code-line').length")
        check("toggle.back_to_view", bool(back) and back >= 3, f"(got {back})")

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
    global SNAP
    lib = _TMP / "lib"
    SNAP = lib / "FakePlant" / "LINE01" / ROBOT / "2026_01_01" / "12_00_00"
    SNAP.mkdir(parents=True)
    (SNAP / "SUMMARY.DG").write_text(f"Robot Name     {ROBOT}\n", encoding="utf-8")
    (SNAP / PROG).write_bytes(PROG_BYTES)
    (SNAP / "backup.json").write_text(
        json.dumps({"robot": ROBOT, "line": "LINE01", "plant": "FakePlant",
                    "taken": "2026-01-01T12:00:00", "complete": True}),
        encoding="utf-8")
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
