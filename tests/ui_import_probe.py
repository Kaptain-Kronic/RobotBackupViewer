"""Hidden-window probe for the drop-import flow.

Covers what pytest can't: the + add robot menu entry, the import modal's real
DOM (drop zone, pre-ticked results, warning pills), the simulated NATIVE drop
(api.handle_drop called with the same dict pywebview's DOM subscription
delivers - a hidden probe cannot drag real folders), the plant/line step, the
progress poll, the summary toast, and the imported tree adopted into the
library list on screen. Also the stray-drop hint toast while the modal is
closed. Ends by checking the copied files on disk byte-for-byte.

Fully synthetic and identifier-clean: RB fakes under FakePlant in temp
folders, APPDATA redirected before any backupviewer import.

Run:  python tests/ui_import_probe.py
Or manually check a real native drag (visible window, watch the log):
      python tests/ui_import_probe.py --drop-probe
"""
import json
import sys
import time
from pathlib import Path

from probeutil import isolate, FAILURES, check, js, poll, report, exit_code

_TMP = isolate("bv_import_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path, _wire_drop  # noqa: E402

LIB = _TMP / "lib"
DROP = _TMP / "drop"
R_DATED = "RB010R01B01"
R_BARE = "RB040R04B04"
VA = "[*NUMREG*]\r\n[1] = 10\r\n"


JUNK = _TMP / "junknotes"


def build_trees() -> None:
    LIB.mkdir(parents=True)
    # write_bytes, not write_text: text mode would newline-translate the
    # fixture and the byte-for-byte disk checks below would chase ghosts
    snap = DROP / "LINE9" / R_DATED / "2026_08_01" / "07_00_00"
    snap.mkdir(parents=True)
    (snap / "NUMREG.VA").write_bytes(VA.encode("cp1252"))
    (snap / "SUMMARY.DG").write_bytes(b"SUMMARY.DG\r\n")
    bare = DROP / "LINE9" / R_BARE
    bare.mkdir(parents=True)
    (bare / "NUMREG.VA").write_bytes(VA.encode("cp1252"))
    (bare / "backup.json").write_bytes(
        json.dumps({"robot": R_BARE, "taken": "2026-08-02T08:00:00",
                    "complete": True}).encode("utf-8"))
    JUNK.mkdir()
    (JUNK / "readme.txt").write_bytes(b"hi")


def drop_event() -> dict:
    """The dict shape pywebview's DOM drop subscription delivers - a backup
    slice and a junk folder dragged together (the junk exercises the honest
    'no backups found inside' line)."""
    return {"dataTransfer": {"files": [
        {"name": DROP.name, "pywebviewFullPath": str(DROP)},
        {"name": JUNK.name, "pywebviewFullPath": str(JUNK)}]}}


def toast(window) -> str:
    return js(window, "(document.querySelector('#toast')||{}).textContent || ''") or ""


def click_ctx_item(window, label):
    return js(window, """(function(){
        var b=[...document.querySelectorAll('.ctx-item')].find(function(x){
            return x.textContent===%s;});
        if(!b) return '';
        b.click();
        return 'y';
    })()""" % json.dumps(label))


def probe(window):
    api = window._bv_api
    try:
        time.sleep(4)  # boot

        # ---- a drop with the modal CLOSED points at the flow, imports nothing ----
        api.handle_drop(drop_event())
        got = poll(window, "(document.querySelector('#toast')||{}).textContent"
                           ".indexOf('add robot')>=0 ? 'y' : ''")
        check("hint.toast_on_stray_drop", got == "y", f"(toast: {toast(window)!r})")
        check("hint.no_modal", not js(window, "BV.modalOpen()"))

        # ---- + add robot -> import backup folder… ----
        opened = poll(window, """(function(){
            var b=document.getElementById('lib-add-robot');
            if(!b) return '';
            b.click();
            return 'y';
        })()""")
        check("menu.opens", opened == "y")
        check("menu.import_entry", click_ctx_item(window, "import backup folder…") == "y")
        check("modal.opens", bool(poll(window, "BV.modalOpen() ? 'y' : ''")))
        check("modal.drop_zone",
              bool(js(window, "document.querySelector('#modal-root .imp-drop') ? 'y' : ''")))

        # ---- the simulated native drop scans into pre-ticked rows ----
        api.handle_drop(drop_event())
        rows = poll(window, "document.querySelectorAll('#modal-root .scan-row').length")
        check("scan.two_robots", rows == 2, f"(got {rows})")
        body = js(window, "document.querySelector('#modal-root .modal').textContent") or ""
        check("scan.names_both", R_DATED in body and R_BARE in body)
        check("scan.junk_listed", "no backups found inside" in body)
        check("scan.preticked",
              js(window, "document.querySelectorAll('#modal-root .scan-row input:checked').length") == 2)
        go = poll(window, """(function(){
            var b=[...document.querySelectorAll('#modal-root button')].find(function(x){
                return x.textContent==='import 2';});
            return b && !b.disabled ? 'y' : '';
        })()""")
        check("scan.go_button_counts", go == "y")

        # ---- plant/line step ----
        js(window, """[...document.querySelectorAll('#modal-root button')]
            .find(function(x){ return x.textContent==='import 2'; }).click()""")
        ok = poll(window, "document.querySelectorAll('#modal-root .lf-combo input').length")
        check("step2.combo_fields", ok == 2, f"(got {ok})")
        js(window, """(function(){
            var f=document.querySelectorAll('#modal-root .lf-combo input');
            f[0].value='FakePlant'; f[1].value='LINE9';
        })()""")
        js(window, """[...document.querySelectorAll('#modal-root button')]
            .find(function(x){ return x.textContent==='import 2'; }).click()""")

        # ---- progress runs to the summary toast; the library repaints ----
        done = poll(window, "(document.querySelector('#toast')||{}).textContent"
                            ".indexOf('imported 2 robots')>=0 ? 'y' : ''", tries=48)
        check("done.summary_toast", done == "y", f"(toast: {toast(window)!r})")
        listed = poll(window, """(function(){
            var t=[...document.querySelectorAll('.lib-robot')].map(function(r){
                return r.textContent; }).join('|');
            return t.indexOf(%s)>=0 && t.indexOf(%s)>=0 ? 'y' : '';
        })()""" % (json.dumps(R_DATED), json.dumps(R_BARE)), tries=48)
        check("done.library_lists_both", listed == "y")

        # ---- the copies are real, in the law-shaped tree, no droppings ----
        line = LIB / "FakePlant" / "LINE9"
        dated = line / R_DATED / "2026_08_01" / "07_00_00"
        check("disk.dated_va", (dated / "NUMREG.VA").read_bytes() == VA.encode("cp1252"))
        bare_days = list((line / R_BARE).glob("*/*/NUMREG.VA"))
        check("disk.bare_landed_dated", len(bare_days) == 1
              and bare_days[0].parent.parent.parent.name == R_BARE
              and bare_days[0].parent.parent.name == "2026_08_02",
              f"(got {bare_days})")
        check("disk.no_parts", not list(LIB.rglob("*.__part")))
        check("disk.source_untouched",
              (DROP / "LINE9" / R_DATED / "2026_08_01" / "07_00_00" / "NUMREG.VA").is_file())

        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass


def drop_probe(window):
    """--drop-probe: a VISIBLE window wired exactly like the real app; drag a
    real folder onto it and watch stdout report what python received."""
    print("drag a backup folder onto the window; ctrl+c here to quit")


def main():
    manual = "--drop-probe" in sys.argv
    build_trees()
    bv_settings.set_value("library_root", str(LIB))

    api = Api()
    window = webview.create_window(
        "import probe",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1280,
        height=860,
        hidden=not manual,
    )
    window._bv_api = api
    api.bind(window)
    if manual:
        _wire_drop(window, api)   # the real subscription - this is what's under manual test
        webview.start(drop_probe, window, gui="edgechromium", debug=True)
        return
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
