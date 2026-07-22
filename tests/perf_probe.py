"""Plant-scale interaction budget for the library screen.

The behaviour probe (ui_batch_probe.py) runs on a 3-robot fixture, which can
never catch a performance cliff — and this screen has real ones. A plant
library is thousands of rows, and every one of them is laid out unless the
CSS says otherwise, so a single careless DOM read or write inside the tree
turns into hundreds of milliseconds. Measured on this 2400-row tree before
the fixes landed: 174ms per KEYSTROKE in a note, 814ms per favourite-star
toggle, and 42 SECONDS for one shift+click range (offsetParent forcing layout
of every content-visibility-skipped row).

No disk I/O and no real backups: lib_list is stubbed with a synthetic library,
so this runs anywhere in a few seconds. Identifier-clean (RB fakes, TEST-NET
IPs, FakePlant). Run: python tests/perf_probe.py [--rows 2400]
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = Path(tempfile.mkdtemp(prefix="bv_perf_"))
os.environ["APPDATA"] = str(_TMP / "appdata")     # never touch the real settings
os.environ["BV_NO_WATCHER"] = "1"

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

FAILURES = []

# what "still feels like an app" means here, in ms. Generous on purpose: these
# are cliff detectors, not micro-benchmarks — and a plant PC is slower than a
# dev box, so the numbers we accept are the ones that survive that.
BUDGET = {
    "keystroke": 25,      # typing in a note must fit inside a frame
    "editor_open": 80,    # double-click a note -> box is there
    "editor_close": 80,
    "star_toggle": 500,   # pins the row + rebuilds the strip
    "shift_range": 800,   # selecting ~900 rows at once
    "picker_open": 900,   # the link/compare picker builds its own tree
}


def check(name, cond, detail=""):
    print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


def budget(name, ms):
    check(f"{name} <= {BUDGET[name]}ms", ms is not None and ms <= BUDGET[name],
          f"({ms}ms)")


def js(window, expr):
    return window.evaluate_js(expr)


def poll(window, expr, tries=60, delay=0.25):
    val = None
    for _ in range(tries):
        val = js(window, expr)
        if val:
            return val
        time.sleep(delay)
    return val


def stub_js(rows):
    """A synthetic library of `rows` robots across 40 lines, served to the UI
    in place of a disk scan."""
    return """
window.__realCall = window.__realCall || BV.api.call;
BV.api.call = function (name) {
  if (name === 'lib_list') {
    var robots = [], n = %d;
    for (var i = 0; i < n; i++) {
      var ln = 'LINE' + String(i %% 40).padStart(2, '0');
      robots.push({
        id: 'id' + i, robot: 'RB' + String(1000 + i) + 'R01B01',
        plant: 'FakePlant', line: ln, model: 'M-710iC/50',
        device_type: 'robot', linked_robot_id: '',
        ips: ['192.0.2.' + (i %% 250 + 1)], ftp: {user: '', passive: true},
        notes: (i %% 7 === 0) ? 'a note line' : '',
        latest_path: 'X:/lib/' + ln + '/rb' + i,
        history_root: 'X:/lib/' + ln + '/rb' + i,
        last_backup: '2026-07-0' + (i %% 9 + 1) + 'T12:00:00',
        backups: [{path: 'x', taken: '2026-07-01T12:00:00'}],
        hidden: false, favorite: false, stale: false,
      });
    }
    return Promise.resolve({robots: robots, empty_folders: {plants: [], lines: []}});
  }
  if (name === 'lib_update' || name === 'lib_set_favorite') return Promise.resolve({});
  return window.__realCall.apply(this, arguments);
};
window.__time = function (fn) {
  var t0 = performance.now();
  fn();
  void document.documentElement.offsetHeight;   /* include the reflow */
  return +(performance.now() - t0).toFixed(1);
};
window.__bench = function (ta, n) {
  var times = [];
  for (var i = 0; i < n; i++) {
    var t0 = performance.now();
    ta.value += 'x';
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    void document.documentElement.offsetHeight;
    times.push(performance.now() - t0);
  }
  times.sort(function (a, b) { return a - b; });
  return +times[Math.floor(n / 2)].toFixed(1);
};
""" % rows


def probe(window, rows):
    try:
        time.sleep(5)
        js(window, stub_js(rows))
        js(window, "BV.goHome()")
        n = poll(window, "document.querySelectorAll('.lib-robot').length")
        check("tree.rendered", n == rows, f"({n} rows)")
        if n != rows:
            return

        # the rule the whole budget rests on: rows off screen are not laid out
        check("css.rows_skip_offscreen_layout",
              js(window, "getComputedStyle(document.querySelector('.lib-robot'))"
                         ".contentVisibility") == "auto")

        t = js(window, """(function(){
          var out = {};
          var row = document.querySelectorAll('.lib-robot')[3];
          out.editor_open = window.__time(function () {
            row.querySelector('.lib-robot-more').click();
            [...document.querySelectorAll('.ctx-menu .ctx-item')]
              .find(function (b) { return b.textContent.indexOf('note') >= 0; }).click();
          });
          var ta = row.querySelector('.lib-note-edit');
          if (!ta) return {err: 'no editor'};
          out.keystroke = window.__bench(ta, 25);
          out.newline = window.__time(function () {
            ta.value += '\\nsecond line';
            ta.dispatchEvent(new Event('input', {bubbles: true}));
          });
          out.editor_close = window.__time(function () {
            ta.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
          });
          out.star_toggle = window.__time(function () {
            document.querySelectorAll('.lib-robot')[1200].querySelector('.lib-fav').click();
          });
          out.star_off = window.__time(function () {
            document.querySelector('.lib-favs .lib-fav').click();
          });
          return out;
        })()""")
        print("  " + json.dumps(t))
        if t.get("err"):
            check("editor.opens", False, t["err"])
            return
        budget("editor_open", t.get("editor_open"))
        budget("keystroke", t.get("keystroke"))
        budget("editor_close", t.get("editor_close"))
        budget("star_toggle", t.get("star_toggle"))
        check("editor.newline_cheap", t.get("newline", 999) <= BUDGET["keystroke"] * 2,
              f"({t.get('newline')}ms)")

        # your place in the list survives a rebuild. Rows off screen have
        # ESTIMATED heights, so restoring a raw pixel offset drifts (measured
        # ~40 robots) - home.js anchors on the top robot instead.
        r = json.loads(js(window, """(function(){
          var view = document.getElementById('view');
          function topRobot() {
            var vt = view.getBoundingClientRect().top;
            var rows = document.querySelectorAll('.lib-robot');
            for (var i = 0; i < rows.length; i++) {
              var b = rows[i].getBoundingClientRect();
              if (b.bottom > vt + 2) {
                return (rows[i].getAttribute('data-robot-id') || '') +
                       '@' + Math.round(vt - b.top);
              }
            }
            return '';
          }
          view.scrollTop = 0; void view.offsetHeight;
          view.scrollTop = 25000; void view.offsetHeight;
          var before = topRobot();
          document.querySelectorAll('.lib-robot')[1500].querySelector('.lib-fav').click();
          var after = topRobot();
          document.querySelector('.lib-favs .lib-fav').click();      /* undo */
          return JSON.stringify({before: before, after: after});
        })()""") or "{}")
        check("scroll.anchored_across_rebuild", r.get("before") == r.get("after"),
              f"(top row {r.get('before')} -> {r.get('after')})")

        # shift+click across rows that were never on screen. This is the one
        # that cost 42 SECONDS: BV.checklist asked offsetParent per row, and
        # that forces layout of skipped content.
        rng = json.loads(js(window, """(function(){
          var view = document.getElementById('view');
          view.scrollTop = 0; void view.offsetHeight;
          var boxes = document.querySelectorAll(
            '.lib-plant:not(.lib-favs) .lib-robot .lib-check');
          boxes[2].click();
          var anchor = document.querySelector('.lib-sel-count').textContent;
          /* never pre-set .checked: dispatching a click runs the checkbox's
             own activation behaviour, which toggles it first */
          var t0 = performance.now();
          boxes[900].dispatchEvent(new MouseEvent(
            'click', {shiftKey: true, bubbles: true, cancelable: true}));
          var ms = +(performance.now() - t0).toFixed(1);
          var after = document.querySelector('.lib-sel-count').textContent;
          document.querySelectorAll('.lib-check').forEach(function (c) {
            if (c.checked) c.click();
          });
          return JSON.stringify({anchor: anchor, after: after, ms: ms});
        })()""") or "{}")
        print("  " + json.dumps(rng))
        check("range.anchor_selects_one", rng.get("anchor") == "1 selected")
        check("range.covers_offscreen_rows", rng.get("after") == "899 selected",
              f"(got {rng.get('after')!r})")
        budget("shift_range", rng.get("ms"))

        # the link/compare picker builds a second tree of the same library
        pk = json.loads(js(window, """(function(){
          var row = [...document.querySelectorAll('.lib-robot')][5];
          var ms = window.__time(function () {
            row.querySelector('.lib-robot-more').click();
            [...document.querySelectorAll('.ctx-menu .ctx-item')]
              .find(function (b) { return b.textContent === 'edit'; }).click();
          });
          var rows = document.querySelectorAll('.modal .lf-row').length;
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
          return JSON.stringify({ms: ms, rows: rows});
        })()""") or "{}")
        print("  " + json.dumps(pk))
        budget("picker_open", pk.get("ms"))

        print()
        print("FAILURES:", FAILURES if FAILURES else "none")
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=2400,
                    help="synthetic library size (a real plant tree is ~2400)")
    args = ap.parse_args()

    lib = _TMP / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    bv_settings.set_value("library_root", str(lib))
    print(f"plant-scale probe: {args.rows} robots\n")

    api = Api()
    window = webview.create_window("perf probe", url=str(resource_path("web/index.html")),
                                   js_api=api, width=1400, height=900, hidden=True)
    api.bind(window)
    webview.start(lambda: probe(window, args.rows), window, gui="edgechromium")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
