"""Hidden-window probe for the release check (updatecheck.py + update.js).

What it pins down:
- the boot auto-check is a policy no-op from a source run (this probe IS a
  source run): the real bridge answers {"status": "skipped"} and no pill
  appears — probes never touch the network;
- with the bridge endpoint stubbed, the whole UI path works: statusbar pill,
  about-box updates row, get/skip buttons, skip persisting to settings.json
  and silencing the pill, the manual check's honest states (up to date /
  offline), and the ⚙ "check on startup" toggle round-trip.

Fully synthetic and identifier-clean: empty library in a temp folder, APPDATA
redirected there BEFORE importing the app; fake versions (9.9) and
example.invalid urls; zero network.
Run: python tests/ui_updatecheck_probe.py
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

# isolate settings/library under a temp APPDATA before any backupviewer import
_TMP = Path(tempfile.mkdtemp(prefix="bv_upd_probe_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ["BV_NO_WATCHER"] = "1"
os.environ["BV_UPDATE_CHECK"] = "0"   # belt; the not-frozen gate already skips

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=24, delay=0.5):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def poll_setting(key, want, timeout=5.0):
    deadline = time.time() + timeout
    got = None
    while time.time() < deadline:
        got = bv_settings.load().get(key)
        if got == want:
            return got
        time.sleep(0.25)
    return got


def stub_check_update(window, payload):
    """Replace the bridge endpoint with a canned envelope (the api.js wrapper
    calls window.pywebview.api.check_update directly, so this intercepts both
    the auto and the manual path)."""
    js(window, """window.pywebview.api.check_update = function () {
        return Promise.resolve({ ok: true, data: %s });
    };""" % json.dumps(payload))


def probe(window):
    try:
        time.sleep(4)  # boot (update.js schedules its auto-check at T+3s)

        check("boot.update_present", js(window, "!!BV.update"))
        # the real bridge ran the boot auto-check by now; from a source run the
        # policy answers "skipped", nothing is remembered, no pill shows
        check("boot.no_info", js(window, "BV.state.updateInfo === null || BV.state.updateInfo === undefined"))
        check("boot.no_pill", not js(window, "!!document.querySelector('#statusbar .update-pill')"))
        js(window, """window.__auto = null;
            BV.api.call("check_update", true).then(function (d) {
                window.__auto = JSON.stringify(d);
            });""")
        auto = poll(window, "window.__auto")
        auto = json.loads(auto or "{}")
        check("boot.policy_skips_source_runs", auto.get("status") == "skipped", f"(got {auto})")

        # ---- stubbed newer release -> toast path + statusbar pill ----
        stub_check_update(window, {"status": "update", "current": "1.3",
                                   "latest": "9.9", "url": "https://example.invalid/rel"})
        js(window, "BV.update.autocheck()")
        pill = poll(window, "(document.querySelector('#statusbar .update-pill') || {}).textContent || ''")
        check("pill.shows", "9.9" in (pill or ""), f"(got {pill!r})")

        # ---- pill click -> about modal with the updates row ----
        js(window, "document.querySelector('#statusbar .update-pill').click()")
        got = poll(window, """(function () {
            var m = document.querySelector('.modal .about-upd');
            if (!m) return '';
            return JSON.stringify({
                line: m.querySelector('.about-line').textContent,
                btns: [...m.querySelectorAll('button')]
                    .filter(function (b) { return b.style.display !== 'none'; })
                    .map(function (b) { return b.textContent; }),
            });
        })()""")
        got = json.loads(got or "{}")
        check("about.line_honest", "9.9" in got.get("line", "") and "is on github" in got.get("line", ""),
              f"({got})")
        check("about.get_button", "get 9.9" in got.get("btns", []), f"({got})")
        check("about.skip_button", "skip this version" in got.get("btns", []), f"({got})")

        # ---- skip: persists, drops the pill, stays honest in the line ----
        js(window, """[...document.querySelectorAll('.modal .about-upd button')]
            .find(function (b) { return b.textContent === 'skip this version'; }).click()""")
        check("skip.persists", poll_setting("update_skip", "9.9") == "9.9")
        check("skip.pill_gone", not js(window, "!!document.querySelector('#statusbar .update-pill')"))
        check("skip.line_says_so",
              "skipped" in (js(window, "document.querySelector('.modal .about-upd .about-line').textContent") or ""))

        # ---- manual check: up-to-date and offline states render honestly ----
        stub_check_update(window, {"status": "current", "current": "1.3"})
        js(window, """[...document.querySelectorAll('.modal .about-upd button')]
            .find(function (b) { return b.textContent === 'check for updates'; }).click()""")
        line = poll(window, """(function () {
            var t = document.querySelector('.modal .about-upd .about-line').textContent;
            return t.indexOf('up to date') >= 0 ? t : '';
        })()""")
        check("manual.up_to_date", "up to date" in (line or ""), f"(got {line!r})")

        stub_check_update(window, {"status": "offline", "current": "1.3"})
        js(window, """[...document.querySelectorAll('.modal .about-upd button')]
            .find(function (b) { return b.textContent === 'check for updates'; }).click()""")
        line = poll(window, """(function () {
            var t = document.querySelector('.modal .about-upd .about-line').textContent;
            return t.indexOf('github') >= 0 && t.indexOf('offline') >= 0 ? t : '';
        })()""")
        check("manual.offline_honest", "couldn't reach github" in (line or ""), f"(got {line!r})")

        js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        time.sleep(0.3)

        # ---- ⚙ settings: the check-on-startup switch round-trips ----
        js(window, "BV.uiPrefs.modal()")
        rows = poll(window, """(function () {
            var rows = [...document.querySelectorAll('.modal .set-row')]
                .map(function (x) { return x.querySelector('.name').textContent; });
            return rows.indexOf('check on startup') >= 0 ? JSON.stringify(rows) : '';
        })()""")
        check("settings.row_present", bool(rows), f"(got {rows})")
        js(window, """(function () {
            var row = [...document.querySelectorAll('.modal .set-row')]
                .find(function (r) { return r.querySelector('.name').textContent === 'check on startup'; });
            [...row.querySelectorAll('.seg button')]
                .find(function (b) { return b.textContent === 'off'; }).click();
        })()""")
        check("settings.toggle_persists", poll_setting("update_check", False) is False)
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
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
