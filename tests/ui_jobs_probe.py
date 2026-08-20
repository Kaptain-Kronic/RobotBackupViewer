"""ui_jobs_probe - scans as first-class background jobs, end to end.

The contract under test: closing a scan window DETACHES the scan (the job
keeps running server-side), the global #jobstrip carries a row per live scan
(label, progress, open, ✕), "open" re-attaches the window to the job, a ✕ on
the strip is the ONLY way a close-shaped gesture kills a scan, and a scan
that finishes while detached still lands in the saved last-scan report.

Three phases: (A) a REAL fleet scan over a synthetic library - started from
the picker, Esc'd mid-run, asserted never-cancelled and persisted; (B) a fake
never-finishing health job injected into api._scans - deterministic attach,
live update, Esc-detach, strip persistence, per-row cancel (no timing races:
the fake moves only when this probe moves it); (C) fake network sweeps - the
running one survives the discover dialog's own weak spot (a stray BACKDROP
click - the dialog is non-sticky by design), the finished one re-lists its
results on reopen.

Toasts are deliberately not asserted (transient, throttled timers); every
assertion targets an EFFECT: job status via list_scan_jobs, the saved report
via load_last_scan, real DOM in the strip and the reopened windows."""
import json
import sys
import time

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_jobs_probe_")

import webview  # noqa: E402

from backupviewer import discover  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

LIB = _TMP / "lib"
API = None            # the Api instance, reachable from probe() for phase B/C fakes
N_ROBOTS = 12


def snap(robot, date="2026_08_01", time_="10_00_00"):
    d = LIB / "FakePlant" / "RBB01" / robot / date / time_
    d.mkdir(parents=True, exist_ok=True)
    (d / "SUMMARY.DG").write_text("Robot: " + robot + "\n", encoding="utf-8")
    meta = {"robot": robot, "line": "RBB01", "plant": "FakePlant",
            "taken": date.replace("_", "-") + "T" + time_.replace("_", ":"),
            "type": "all of above", "files": 1, "bytes": 100, "source": "ftp"}
    (d / "backup.json").write_text(json.dumps(meta), encoding="utf-8")


def build_tree():
    for i in range(1, N_ROBOTS + 1):
        snap(f"RB{i:03d}R01B01")


class _FakeScan(discover._ScanJob):
    """A scan job that moves only when the probe moves it - cancel() flips the
    status the way a real run() loop would notice the event and do."""

    def cancel(self):
        super().cancel()
        self._set(status="cancelled")


class FakeHealth(_FakeScan):
    kind = "health"


class FakeNet(_FakeScan):
    kind = "network"


def scan_status(window, job_id):
    """One list_scan_jobs round-trip -> that job's status ('' until it lands)."""
    raw = poll(window, """(function(){
        BV.api.call('list_scan_jobs').then(function(r){
            var j=(r.jobs||[]).filter(function(x){return x.id==='%s';})[0];
            window.__st = j ? j.status : '?';
        });
        return window.__st||'';
    })()""" % job_id)
    js(window, "window.__st='';")
    return raw


def esc(window):
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")


def modal_hidden(window):
    return poll(window,
                "document.getElementById('modal-root').classList.contains('hidden')?'y':''")


def probe(window):
    try:
        time.sleep(4)                                    # boot + cold library scan
        poll(window, "document.querySelectorAll('.lib-robot').length >= %d ? 'y' : ''"
             % N_ROBOTS, tries=48)

        # ---- phase A: a real fleet scan, Esc'd mid-run ----
        js(window, "window.__sopen=0;"
                   "BV.api.call('lib_list').then(function(r){"
                   "  BV.scanUI.open(r.robots||[]); window.__sopen=1; });")
        poll(window, "window.__sopen===1 && document.querySelector('.hs-checks') ? 'y' : ''")
        js(window, "document.querySelector('.hs-pickhead .scan-selall .lf-check').click()")
        js(window, "document.querySelector('.hs-footbar .btn.primary').click()")
        started = poll(window,
                       "document.querySelector('.hs-host .membar') ? 'y' : ''", tries=48)
        check("real.scan_started", started == "y")
        esc(window)                                      # the gesture that used to cancel
        check("real.modal_closed", bool(modal_hidden(window)))
        hid = poll(window, """(function(){
            BV.api.call('list_scan_jobs').then(function(r){
                var j=(r.jobs||[]).filter(function(x){return x.kind==='health';})[0];
                window.__hid = j ? j.id : '';
            });
            return window.__hid||'';
        })()""")
        check("real.job_visible_after_close", bool(hid))
        final = ""
        for _ in range(60):                              # a real 12-robot scan: seconds
            final = scan_status(window, hid)
            if final in ("done", "error", "cancelled"):
                break
            time.sleep(0.5)
        check("real.never_cancelled", final == "done", f"(final {final!r})")

        # the detached finish landed in the saved last-scan report
        got = poll(window, """(function(){
            BV.api.call('load_last_scan').then(function(r){
                window.__rep = (r && r.results) ? r.results.length : 0;
            });
            return window.__rep===%d ? 'y' : '';
        })()""" % N_ROBOTS, tries=48)
        check("real.detached_finish_saved", got == "y")

        # reopening goes straight to that report (nothing selected, scan over)
        js(window, "BV.scanUI.open([])")
        check("real.reopen_shows_report", bool(poll(window,
              "document.querySelector('.hs-results') ? 'y' : ''", tries=48)))
        esc(window)
        modal_hidden(window)

        # ---- phase B: fake health job - attach, live update, detach, cancel ----
        fh = FakeHealth(label="fleet scan")
        fh._set(status="scanning", total=10, scanned=3)
        API._scans[fh.id] = fh
        js(window, "BV.jobs.trackScan('%s','health')" % fh.id)
        # the row is born as a "starting…" stub and settles to the server's
        # numbers on the strip's first poll - wait for the settled form
        row = poll(window, """(function(){
            var r=document.querySelector('#jobstrip .jobstrip-scan');
            if(!r) return '';
            var l=r.querySelector('.js-slab-l').textContent;
            var n=r.querySelector('.js-slab-r').textContent;
            return (l==='fleet scan' && n.indexOf('3 / 10')===0) ? 'y' : '';
        })()""", tries=48)
        check("strip.row_appears", row == "y")

        js(window, "BV.scanUI.open([])")                 # attach via the strip's cache
        check("attach.runview_shows_job", bool(poll(window, """(function(){
            var b=document.querySelector('.hs-host .membar');
            return b && b.textContent.indexOf('3 / 10')>=0 ? 'y' : '';
        })()""", tries=48)))
        fh._set(scanned=7)                               # the job moves while attached
        check("attach.live_update", bool(poll(window, """(function(){
            var b=document.querySelector('.hs-host .membar');
            return b && b.textContent.indexOf('7 / 10')>=0 ? 'y' : '';
        })()""", tries=48)))

        esc(window)                                      # detach - NOT cancel
        modal_hidden(window)
        time.sleep(1.2)                                  # give a would-be cancel time to land
        check("detach.job_still_running", scan_status(window, fh.id) == "scanning")
        check("detach.strip_row_persists", bool(js(window,
              "document.querySelector('#jobstrip .jobstrip-scan') ? 'y' : ''")))

        js(window, "document.querySelector("
                   "'#jobstrip .jobstrip-scan [data-cancel-scan]').click()")
        st = ""
        for _ in range(20):
            st = scan_status(window, fh.id)
            if st == "cancelled":
                break
            time.sleep(0.3)
        check("strip.x_cancels", st == "cancelled")
        check("strip.row_leaves_when_terminal", bool(poll(window,
              "document.querySelector('#jobstrip .jobstrip-scan') ? '' : 'y'", tries=48)))

        # ---- phase C: network sweeps - backdrop-safe, results survive reopen ----
        na = FakeNet(label="network sweep 10.9.9.0/24")
        na._set(status="scanning", total=254, scanned=60, started="2026-01-01T00:00:01")
        API._scans[na.id] = na
        js(window, "BV.jobs.trackScan('%s','network')" % na.id)
        poll(window, "document.querySelector('#jobstrip .jobstrip-scan') ? 'y' : ''",
             tries=48)
        js(window, "BV.discover()")
        check("disc.attaches_to_running", bool(poll(window, """(function(){
            var b=document.querySelector('.disc-body .scan-bar');
            return b && b.textContent.indexOf('60 / 254')>=0 ? 'y' : '';
        })()""", tries=48)))
        # the original sin: a stray click OUTSIDE the (non-sticky) dialog
        js(window, "document.getElementById('modal-root').dispatchEvent("
                   "new MouseEvent('mousedown',{bubbles:true}))")
        check("disc.backdrop_closes", bool(modal_hidden(window)))
        time.sleep(1.2)
        check("disc.backdrop_never_cancels", scan_status(window, na.id) == "scanning")
        check("disc.strip_still_has_it", bool(js(window,
              "document.querySelector('#jobstrip .jobstrip-scan') ? 'y' : ''")))
        js(window, "document.querySelector("
                   "'#jobstrip .jobstrip-scan [data-cancel-scan]').click()")
        for _ in range(20):
            if scan_status(window, na.id) == "cancelled":
                break
            time.sleep(0.3)

        nb = FakeNet(label="network sweep 10.9.9.0/24")
        nb._set(status="done", total=254, scanned=254, found=2,
                started="2026-01-01T00:00:02")
        nb._set_results([
            {"host": "10.9.9.21", "device_type": "robot", "name": "RB901R01B01",
             "has_md": True, "has_fr": False},
            {"host": "10.9.9.22", "device_type": "robot", "name": "RB902R01B01",
             "has_md": True, "has_fr": False},
        ])
        API._scans[nb.id] = nb
        js(window, "BV.jobs.trackScan('%s','network')" % nb.id)
        poll(window, """(function(){
            var s=BV.jobs.scanLatest()['%s'];
            return s && s.label ? 'y' : '';
        })()""" % nb.id)                                 # cache carries the real snapshot
        js(window, "BV.discover()")
        n = poll(window, """(function(){
            var rows=document.querySelectorAll('.disc-body .scan-results .scan-row');
            return rows.length===2 ? 'y' : '';
        })()""", tries=48)
        check("disc.finished_sweep_relists", n == "y")
        esc(window)
        modal_hidden(window)

        report()
    except Exception as e:  # noqa: BLE001 - a crashed probe must still report
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    global API
    build_tree()
    bv_settings.set_value("library_root", str(LIB))
    API = Api()
    window = webview.create_window(
        "probe", url=str(resource_path("web/index.html")), js_api=API,
        width=1280, height=860, hidden=True,
    )
    API.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
