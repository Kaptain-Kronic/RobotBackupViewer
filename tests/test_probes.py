"""Collect the hidden-window probes into pytest, one subprocess each.

The probes are the only thing that checks the app actually WORKS - real DOM in
a real WebView2 - and until now pytest never ran any of them, because none of
them is named test_*.py. This file is the missing wiring.

Why a subprocess per probe rather than ten in-process pytest modules:

  * each probe pins APPDATA and BV_NO_WATCHER at import time and then imports
    backupviewer; two of them in one interpreter and the second one's sandbox
    silently wins for both;
  * ui_cvxremote_probe replaces cvx_remote.CvxRemoteSession with a fake and
    never puts it back, and ui_updatecheck_probe fakes the version - neither
    has teardown, because neither ever needed one;
  * webview.start() holds the main thread until every window is destroyed, so
    one probe that fails to clean up hangs the WHOLE run. perf_probe did
    exactly that for weeks. One process each plus a timeout turns that from
    "the suite never finishes" into "one test failed".

(pywebview itself is fine with being restarted in-process - that was checked,
not assumed. It is the three reasons above that decide this.)

Each probe stays runnable on its own, which is how they are debugged:

    python tests/ui_edit_probe.py

They are marked `probe` and excluded from the default run by addopts in
pyproject.toml, because ~2.5 minutes is long enough that a fast suite stops
being run at all:

    python -m pytest tests                          # unit only, ~40s
    python -m pytest tests -m probe                 # the ten probes, ~2.5 min
    python -m pytest tests -m "probe or not probe"  # EVERYTHING, ~3.5 min

(the everything form is spelled out rather than -m "" because PowerShell drops
an empty argument before pytest ever sees it.)
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

# Explicit, not a glob: a glob would sweep up whatever probe happens to be
# sitting untracked in a working copy (ui_probe.py is one) and make the result
# depend on the machine.
PROBES = [
    "libraryimporter_probe.py",
    "perf_probe.py",
    "ui_batch_probe.py",
    "ui_bgfx_probe.py",
    "ui_cvxremote_probe.py",
    "ui_edit_probe.py",
    "ui_fk_probe.py",
    "ui_sim_export_probe.py",
    "ui_tabs_probe.py",
    "ui_updatecheck_probe.py",
]

# The slowest today is ui_edit at ~57s. Generous, because this is a hang
# detector, not a benchmark - a plant PC is slower than a dev box.
TIMEOUT = 300


def _tail(text, n=40):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


@pytest.mark.probe
@pytest.mark.parametrize("name", PROBES)
def test_probe(name):
    script = ROOT / "tests" / name
    if not script.is_file():
        pytest.skip(f"{name} not present")
    pytest.importorskip("webview", reason="pywebview not installed")
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        pytest.fail(f"{name} did not finish in {TIMEOUT}s - it is hung, not slow.\n"
                    f"A probe that never destroys its window blocks webview.start() "
                    f"forever.\n\nlast output:\n{_tail(out)}")
    if r.returncode == 0:
        return
    # the probe already prints a [FAIL] line per failed check - surface those,
    # and fall back to the tail when it died some other way (a crash, an import
    # error) and never got that far.
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("[FAIL]")]
    detail = "\n".join(fails) if fails else _tail(r.stdout)
    if r.stderr.strip():
        detail += "\n--- stderr ---\n" + _tail(r.stderr, 20)
    pytest.fail(f"{name} exited {r.returncode}\n{detail}")
