"""Hidden-window probe for the files tab's tick-and-extract flow.

Covers what pytest can't: the real checkbox column in the VTable (tick, shift
is exercised in programs' twin of this code, select-all header), the extract
button's count/enable dance, the confirm modal, the real files_extract call
through the JS bridge (pick_export_folder stubbed to a temp folder - a hidden
probe cannot answer a native dialog), the done-modal, and the ticks surviving
tab navigation. Ends by checking the copies on disk byte-for-byte.

Fully synthetic and identifier-clean: RB fakes under FakePlant in a temp
library, APPDATA redirected before any backupviewer import.
Run: python tests/ui_files_extract_probe.py
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

_TMP = Path(tempfile.mkdtemp(prefix="bv_extract_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []
ROBOT = "RB010R01B01"
EXPORT = _TMP / "usb"
LS_BYTES = b"/PROG  MAIN\r\n/ATTR\r\n/MN\r\n   1:  !setup ;\r\n/END\r\n"
VR_BYTES = bytes(range(256))


def build_tree(lib: Path) -> Path:
    snap = lib / "FakePlant" / "LINE01" / ROBOT / "2026_01_01" / "12_00_00"
    (snap / "md_ls").mkdir(parents=True)
    (snap / "SUMMARY.DG").write_text(f"Robot: {ROBOT}\n", encoding="utf-8")
    # a report .LS, like every real pull has - it is where the session learns
    # the robot's name, which the extract confirm-modal shows as the folder
    (snap / "ERRALL.LS").write_bytes(
        f"ERRALL.LS       Robot Name  {ROBOT}\r\n\r\nnothing here\r\n".encode())
    (snap / "MAIN.LS").write_bytes(LS_BYTES)
    (snap / "md_ls" / "MAIN.LS").write_bytes(LS_BYTES.replace(b"!setup", b"!deep "))
    (snap / "md_ls" / "SAVED.VR").write_bytes(VR_BYTES)
    (snap / "backup.json").write_text(
        json.dumps({"robot": ROBOT, "line": "LINE01", "plant": "FakePlant",
                    "taken": "2026-01-01T12:00:00", "complete": True}),
        encoding="utf-8")
    return snap


class ProbeApi(Api):
    """The folder dialog cannot be answered in a hidden probe - the pick
    always lands on the temp 'usb' folder, everything else is the real Api."""

    def pick_export_folder(self):
        return {"ok": True, "data": str(EXPORT)}


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


def click_button(window, label):
    return js(window, """(function(){
        var b=[...document.querySelectorAll('button')].find(function(x){
            return x.textContent===%s;});
        if(!b) return '';
        b.click();
        return 'y';
    })()""" % json.dumps(label))


def probe(window):
    try:
        time.sleep(4)  # boot

        # ---- open the robot from the library, land on #files ----
        opened = poll(window, """(function(){
            var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
                return r.textContent.indexOf('%s')>=0;});
            if (!row) return '';
            row.click();
            return 'y';
        })()""" % ROBOT)
        check("open.robot_clicked", opened == "y")
        poll(window, "BV.state.manifest && BV.state.manifest.sid ? 'y' : ''")
        js(window, "location.hash='#files'")
        nbox = poll(window, "document.querySelectorAll('.vt-pick').length")
        check("files.checkbox_per_row", nbox == 6, f"(got {nbox})")   # 6 files incl backup.json + report
        check("files.selectall_in_header",
              js(window, "document.querySelectorAll('.vt-pickall').length") == 1)

        # ---- the extract button idles disabled, no count ----
        btn = js(window, """(function(){
            var b=[...document.querySelectorAll('.toolbar button, button')].find(function(x){
                return x.textContent.indexOf('extract')>=0;});
            return b ? JSON.stringify({dis:b.disabled, txt:b.textContent,
                                       prim:b.classList.contains('primary')}) : '';
        })()""") or ""
        check("btn.disabled_at_zero", '"dis":true' in btn and '"prim":false' in btn, f"({btn})")

        # ---- tick one file; the button wakes, the row does NOT open ----
        js(window, """(function(){
            var cb=[...document.querySelectorAll('.vt-pick')].find(function(x){
                return x.getAttribute('data-k')==='MAIN.LS';});
            if (cb) cb.click();
        })()""")
        check("tick.stays_on_list", js(window, "location.hash") == "#files")
        btn = poll(window, """(function(){
            var b=[...document.querySelectorAll('button')].find(function(x){
                return x.textContent.indexOf('extract (1)')>=0;});
            return b && !b.disabled && b.classList.contains('primary') ? 'y' : '';
        })()""")
        check("tick.button_counts_one", btn == "y")

        # ---- ticks survive leaving and re-entering the tab ----
        js(window, "location.hash='#overview'")
        time.sleep(0.6)
        js(window, "location.hash='#files'")
        poll(window, "document.querySelectorAll('.vt-pick').length")
        check("tick.survives_tab_nav",
              js(window, "document.querySelectorAll('.vt-pick:checked').length") == 1)

        # ---- select-all header box ticks every filtered row ----
        js(window, "document.querySelector('.vt-pickall').click()")
        check("selectall.all_ticked",
              js(window, "document.querySelectorAll('.vt-pick:checked').length") == 6)
        n6 = js(window, """(function(){
            var b=[...document.querySelectorAll('button')].find(function(x){
                return x.textContent.indexOf('extract (6)')>=0;});
            return b ? 'y' : '';
        })()""")
        check("selectall.button_counts_six", n6 == "y")

        # ---- extract: confirm modal shows the robot folder + the files ----
        check("modal.opens", click_button(window, "⇪ extract (6)") == "y" and
              bool(poll(window, "BV.modalOpen() ? 'y' : ''")))
        body = js(window, "(document.querySelector('#modal-root .modal')||{}).textContent || ''") or ""
        check("modal.names_robot_folder", (ROBOT + "/") in body, f"(body head: {body[:120]!r})")
        check("modal.lists_files", "md_ls/SAVED.VR" in body and "6 files" in body)

        # ---- choose folder (stubbed) -> the real copy -> done-modal ----
        check("modal.choose_clicked", click_button(window, "choose folder…") == "y")
        done = poll(window, """(function(){
            var m=document.querySelector('#modal-root .modal');
            return m && m.textContent.indexOf('extracted')>=0 ? m.textContent : '';
        })()""", tries=32) or ""
        check("done.modal_reports_six", "extracted 6 files" in done and str(EXPORT) in done,
              f"({done[:120]!r})")

        # ---- the copies are real, byte-for-byte, no .part droppings ----
        lab = EXPORT / ROBOT
        check("disk.root_ls", (lab / "MAIN.LS").read_bytes() == LS_BYTES)
        check("disk.nested_ls",
              (lab / "md_ls" / "MAIN.LS").read_bytes() == LS_BYTES.replace(b"!setup", b"!deep "))
        check("disk.binary_vr", (lab / "md_ls" / "SAVED.VR").read_bytes() == VR_BYTES)
        check("disk.no_parts", not list(EXPORT.rglob("*.part")))

        # ---- done closes; the ticks are spent ----
        click_button(window, "done")
        time.sleep(0.3)
        check("after.modal_closed", not js(window, "BV.modalOpen()"))
        check("after.ticks_cleared",
              js(window, "document.querySelectorAll('.vt-pick:checked').length") == 0)
        check("after.button_idle", js(window, """(function(){
            var b=[...document.querySelectorAll('button')].find(function(x){
                return x.textContent.indexOf('extract')>=0;});
            return b && b.disabled && b.textContent==='⇪ extract' ? 'y' : '';
        })()""") == "y")

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
    lib = _TMP / "lib"
    build_tree(lib)
    bv_settings.set_value("library_root", str(lib))

    api = ProbeApi()
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


