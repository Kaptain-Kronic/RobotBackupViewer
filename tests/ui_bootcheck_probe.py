"""Hidden-window probe for the boot self-check (index.html recorder +
router.js BV.bootCheck).

What it pins down:
- a clean boot records nothing and judges "ok";
- the recorder catches a REAL failed load: a script the file server answers
  404 lands in window.BV_LOST under its "js/name" spelling (the same shape a
  connection-refused script produces - the v1.7 field failure);
- the judge's whole table: nothing lost = ok; lost on a first look = reload
  once; lost again = halt (never a reload loop);
- the halt names the files and gives the one action, in the app's own
  empty-state chrome, and hides the toolbar - a half-built app must say so
  instead of failing somewhere unrelated later.

The reload itself is not exercised (this window is the probe): bootCheck.run
is judged through its pure parts. Fully synthetic and identifier-clean.
Run: python tests/ui_bootcheck_probe.py
"""
import sys
import time
from probeutil import check, exit_code, isolate, js, poller, report

_TMP = isolate("bv_bootcheck_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

poll = poller(tries=20, delay=0.4)


def probe(window):
    try:
        time.sleep(3)
        check("boot.module", js(window, "!!(BV.bootCheck && BV.bootCheck.decide && BV.bootCheck.halt && BV.bootCheck.run)"))
        check("boot.recorder_present", js(window, "Array.isArray(window.BV_LOST)"))
        check("boot.clean_boot_lost_nothing", js(window, "window.BV_LOST.length") == 0,
              f"(got {js(window, 'JSON.stringify(window.BV_LOST)')})")
        check("boot.clean_boot_routed", js(window, "document.getElementById('view').children.length") >= 1)

        # the judge's table - the actual invariant, not a rendering
        check("judge.nothing_lost_is_ok", js(window, "BV.bootCheck.decide([], false)") == "ok")
        check("judge.first_loss_reloads", js(window, "BV.bootCheck.decide(['js/jobs.js'], false)") == "reload")
        check("judge.second_loss_halts", js(window, "BV.bootCheck.decide(['js/jobs.js'], true)") == "halt")

        # a real failed load: the file server 404s it, the element fires
        # error, the capture listener records the js/name spelling
        js(window, """(function () {
            var s = document.createElement('script');
            s.src = 'js/no-such-file-for-the-probe.js';
            document.head.appendChild(s);
        })()""")
        got = poll(window, "window.BV_LOST.indexOf('js/no-such-file-for-the-probe.js') >= 0")
        check("recorder.catches_a_failed_script", got is True,
              f"(BV_LOST={js(window, 'JSON.stringify(window.BV_LOST)')})")

        # the halt: names the files, gives the one action, hides the toolbar
        js(window, "BV.bootCheck.halt(['js/jobs.js', 'js/theme.js'])")
        text = js(window, "document.getElementById('view').textContent") or ""
        check("halt.says_incomplete", "did not load completely" in text, f"(got {text[:80]!r})")
        check("halt.names_the_files", "js/jobs.js, js/theme.js" in text)
        check("halt.gives_the_action", "start it again" in text)
        check("halt.uses_the_empty_state_chrome", js(window, "!!document.querySelector('#view .empty-state .big')"))
        check("halt.toolbar_hidden", js(window, "document.getElementById('toolbar').classList.contains('hidden')"))
        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        from probeutil import FAILURES
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
        width=1100,
        height=760,
        hidden=True,
    )
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
