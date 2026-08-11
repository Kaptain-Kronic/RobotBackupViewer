"""Shared preamble for the hidden-window probes.

Every probe in this folder opened with the same forty lines: put src/ on the
path, force utf-8 stdout, mint a temp APPDATA and silence the library watcher
BEFORE importing the app, then define its own check/js/poll and its own
FAILURES list. Nine copies, drifting only in cosmetics (f-string style) and in
how patient poll() was. This is that preamble, once.

The isolation call has to run before any backupviewer import: settings.json
and library.json resolve under APPDATA, so a probe that skips it writes into
the real one and a plant PC's settings become test fallout.

What deliberately did NOT move here: window creation. The probes differ in
size, in whether they pass a file path or a file:// URI, and in what they hang
off the window afterwards - unifying that would be a behaviour change wearing
a refactor's clothes.

Run any probe standalone the way you always did:
    python tests/ui_tabs_probe.py
or all of them through pytest:
    python -m pytest tests -m probe
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]

FAILURES = []


def isolate(prefix, appdata=True):
    """Sandbox this process and return its temp dir. Call BEFORE importing
    anything from backupviewer. `appdata=False` for probes that drive a
    different app (libraryimporter) and have no settings to protect."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):      # already wrapped / not a tty
        pass
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    if appdata:
        os.environ["APPDATA"] = str(tmp / "appdata")
    os.environ["BV_NO_WATCHER"] = "1"         # no auto-refresh mid-assertion
    return tmp


def check(name, cond, detail=""):
    print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=24, delay=0.25):
    """Poll until evaluate_js(expr) is truthy; returns the last value.

    Hidden windows throttle timers to roughly a second, so anything the app
    debounces has to be polled for rather than slept past."""
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def poller(tries=24, delay=0.25):
    """poll() with different patience, for the probes that need more of it."""
    def _poll(window, expr, tries=tries, delay=delay):
        return poll(window, expr, tries, delay)
    return _poll


def report():
    print()
    print("FAILURES:", FAILURES if FAILURES else "none")


def exit_code():
    return 1 if FAILURES else 0
