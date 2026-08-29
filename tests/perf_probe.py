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
import sys
import time
from probeutil import FAILURES, check, exit_code, isolate, js, poller, report

_TMP = isolate("bv_perf_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

# this probe waits longer than the shared default
poll = poller(tries=60, delay=0.25)

# what "still feels like an app" means here, in ms. Generous on purpose: these
# are cliff detectors, not micro-benchmarks — and a plant PC is slower than a
# dev box, so the numbers we accept are the ones that survive that.
BUDGET = {
    "keystroke": 25,      # typing in a note must fit inside a frame
    # rebaselined 80 -> 110 with the details-view density (2026-08): rows went
    # 46px -> 31px, so a viewport holds ~50% more of them and the editor-open
    # reflow shifts them all. The cliff this guards (the 120ms-at-old-density
    # open, ~180 at this density) still trips it; measured 82 on the day the
    # density landed.
    "editor_open": 110,   # double-click a note -> box is there
    "editor_close": 80,
    # rebaselined 500 -> 150 with the surgical star repaint (2026-08): a
    # toggle now redraws the star + favorites strip only, measured 49 on the
    # day it landed. The cliff this guards is a full tree rebuild sneaking
    # back onto the click path — ~490ms here at 2400 rows, which the old 500
    # budget sat 3% under: it asserted this machine's speed, not the cliff.
    "star_toggle": 150,   # pins the row + rebuilds the strip (never the tree)
    # star_off - the unstar - was measured all along and asserted never;
    # it gets the same budget rather than staying a number nobody checks.
    "star_off": 150,      # and unpinning it repaints the same two places
    "shift_range": 800,   # selecting ~900 rows at once
    "picker_open": 900,   # the link/compare picker builds its own tree
}


# Starring is sampled STAR_REPS times and budgeted on the BEST pair, not on
# one sample. Two sessions fixed this check at once and their fixes disagreed;
# this is what survived the merge, measured 2026-08-26 on the merged tree:
#
#   * the flake that started it is GONE at the source. A star toggle used to
#     rebuild all 2400 rows (~490ms, against a 500 budget it sat 3% under), so
#     any run that landed behind something heavy went red with nothing wrong.
#     The surgical repaint fixed that - a toggle now redraws this robot's star
#     and the favourites strip and nothing else. Measured here over four runs
#     of five pairs: star_on 33.9-67.0ms, star_off 32.7-69.2ms.
#   * so the OTHER session's numbers do not apply. It measured 390-532ms and
#     raised the budget to 700, which is honest arithmetic on a branch that
#     did not have the repaint - and on this tree would be 10x the real cost,
#     wide enough for a full rebuild to sneak back onto the click path
#     unnoticed. That rebuild is the whole cliff this guards. The budget
#     stays 150.
#   * the sampling is kept anyway, because it is nearly free (five pairs cost
#     ~250ms) and the noise here is ONE-SIDED - a scheduler slice or a GC
#     pause only ever ADDS time - so the fastest of a few is the honest floor,
#     and a real regression lifts the floor along with everything else. What
#     it buys now is drift resolution, not flake protection.
#   * each pair is its own evaluate_js: pairs batched inside one js call ramp
#     (320 -> 924ms was measured on the rebuild path), because the browser
#     never gets back to its event loop between them. There is no warm-up, and
#     sample 0 is not special here - it was the most expensive of the five in
#     three of the four runs, the reverse of what the rebuild path did.
#   * load does not move it any more. The run taken straight after
#     ui_camwall_probe and its sixteen local HTTP servers - the exact
#     neighbour that used to push this red - best-of-five 40.9 / 37.0ms, in
#     line with the three idle runs. 150 is ~2.2x the worst single sample
#     seen across all four.
STAR_REPS = 5

# One on/off pair. Re-queried each time on purpose: the favourites strip
# renders its own copy of the row, so while a robot is starred every index
# below it shifts by one - and the pair puts that back, which is what
# star.pair_is_its_own_undo below checks rather than assumes.
STAR_PAIR = """(function(){
  var row = document.querySelectorAll('.lib-robot')[1200];
  var on = window.__time(function () { row.querySelector('.lib-fav').click(); });
  var off = window.__time(function () {
    document.querySelector('.lib-favs .lib-fav').click();
  });
  return {id: row.getAttribute('data-robot-id'), on: on, off: off,
          rows: document.querySelectorAll('.lib-robot').length};
})()"""


def best(samples, key):
    """The fastest `key` across the samples, or None if none of them reported
    one (a budget() with None fails loudly rather than skipping)."""
    vals = [s.get(key) for s in samples if isinstance(s.get(key), (int, float))]
    return min(vals) if vals else None


def budget(name, ms):
    check(f"{name} <= {BUDGET[name]}ms", ms is not None and ms <= BUDGET[name],
          f"({ms}ms)")


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
          return out;
        })()""")
        print("  " + json.dumps(t))
        if t.get("err"):
            check("editor.opens", False, t["err"])
            return
        budget("editor_open", t.get("editor_open"))
        budget("keystroke", t.get("keystroke"))
        budget("editor_close", t.get("editor_close"))
        check("editor.newline_cheap", t.get("newline", 999) <= BUDGET["keystroke"] * 2,
              f"({t.get('newline')}ms)")

        star = [js(window, STAR_PAIR) or {} for _ in range(STAR_REPS)]
        print("  " + json.dumps({"star_on": [s.get("on") for s in star],
                                 "star_off": [s.get("off") for s in star]}))
        # the samples are only comparable if each pair left the tree where it
        # found it - same robot every time, same row count afterwards
        check("star.pair_is_its_own_undo",
              len({s.get("id") for s in star}) == 1 and
              all(s.get("rows") == rows for s in star),
              f"({sorted({s.get('id') for s in star})}, "
              f"{sorted({s.get('rows') for s in star})} rows)")
        budget("star_toggle", best(star, "on"))
        budget("star_off", best(star, "off"))

        # your place in the list survives the two shapes of repaint. Rows off
        # screen have ESTIMATED heights, so restoring a raw pixel offset
        # drifts (measured ~40 robots) - home.js anchors on the top robot
        # instead. A star toggle is the SURGICAL shape (the strip gains a row
        # above you, no rebuild); a sort-header click is a full reordered
        # rebuild — starring used to be the rebuild trigger here, so when it
        # went surgical the rebuild check had to move to a trigger that still
        # rebuilds.
        #
        # The assert is same ROW (exact) held to within one css pixel — not a
        # rounded-offset string compare. Measured 2026-08-29 on this check's
        # own geometry: scroll offsets are DEVICE-pixel quantized (this hidden
        # window runs dpr 1.25, a 0.8 css-px grid — writing 33333.4 stored
        # 33333.6), so a restore can land at most half a device pixel from the
        # captured offset; the real residue here was 0.075px (star) / 0.2px
        # (rebuild) against a ~69px strip swap. The old rounded compare turned
        # a captured 29.525 — 0.025px from the round boundary — into a "1px
        # drift" verdict, and passed or failed with the fixture's geometry.
        # The cliff this guards is a ROW slip: >= 16px, never a fraction.
        r = json.loads(js(window, """(function(){
          var view = document.getElementById('view');
          function topRobot() {
            var vt = view.getBoundingClientRect().top;
            var rows = document.querySelectorAll('.lib-robot');
            for (var i = 0; i < rows.length; i++) {
              var b = rows[i].getBoundingClientRect();
              if (b.bottom > vt + 2) {
                return { id: rows[i].getAttribute('data-robot-id') || '',
                         into: vt - b.top };
              }
            }
            return {};
          }
          view.scrollTop = 0; void view.offsetHeight;
          view.scrollTop = 25000; void view.offsetHeight;
          var before = topRobot();
          document.querySelectorAll('.lib-robot')[1500].querySelector('.lib-fav').click();
          var after = topRobot();
          document.querySelector('.lib-favs .lib-fav').click();      /* undo */
          var before2 = topRobot();
          /* the active header re-renders on click — re-query for the undo */
          document.querySelector('.hlc-cell.hlc-sort.on').click();   /* flip dir */
          var after2 = topRobot();
          document.querySelector('.hlc-cell.hlc-sort.on').click();   /* restore */
          return JSON.stringify({before: before, after: after,
                                 before2: before2, after2: after2});
        })()""") or "{}")

        def anchored(b, a):
            b, a = b or {}, a or {}
            if not b.get("id") or b.get("into") is None or a.get("into") is None:
                return False, f"(no anchor row resolved: {b} -> {a})"
            drift = abs(a["into"] - b["into"])
            return (b["id"] == a.get("id") and drift < 1.0,
                    f"(top row {b['id']}@{b['into']:.3f} -> "
                    f"{a.get('id')}@{a['into']:.3f}, drift {drift:.3f}px)")

        ok, why = anchored(r.get("before"), r.get("after"))
        check("scroll.anchored_across_star", ok, why)
        ok, why = anchored(r.get("before2"), r.get("after2"))
        check("scroll.anchored_across_rebuild", ok, why)

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

        report()
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
    # NO second positional here: pywebview passes `args` INTO the callback, and
    # this one takes none. It used to be `..., window, ...`, which raised
    # TypeError inside the GUI thread - the probe body never ran, the window was
    # never destroyed, and the process hung until it was killed by hand.
    webview.start(lambda: probe(window, args.rows), gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
