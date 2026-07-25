"""LibraryImporter end-to-end probe: boots the real page in a hidden pywebview
window against a synthetic robot list + temp destination, drives the checklist
(folds, ranges, tri-states), clicks import, and asserts the folders landed on
disk. Run: python tests/libraryimporter_probe.py

`--drop-probe` opens a VISIBLE window instead: drag any file onto it and the
window toasts what Python received - the one manual check of WebView2's
native-path capture (everything else about the drop path is asserted here
synthetically). Close the window to exit.

Synthetic data only (TEST-NET IPs, made-up names).
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

import webview  # noqa: E402

from libraryimporter import app as li_app  # noqa: E402
from libraryimporter.api import Api  # noqa: E402

FAILURES = []

LIST = {
    "RCB02": {"005R01": "192.0.2.20", "010R01": "192.0.2.21", "020R01": "192.0.2.22"},
    "RBB01": {"010R01": "192.0.2.10", "020R01": "192.0.2.11",
              "030R01": "192.0.2.12", "040R01": "192.0.2.13"},
    "RBB02": {"010R01": "192.0.2.30", "020R01": "192.0.2.31"},
}


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


def probe(window, api, work: Path):
    src = work / "robots.json"
    dest = work / "FakePlant"
    try:
        time.sleep(4)  # boot
        check("boot.page", poll(window, "!!window.LI && !!document.getElementById('lines')"))

        # empty state: nothing selectable, import disabled
        check("boot.empty", js(window, "document.getElementById('btn-go').disabled === true"))

        # ---- load list + dest through the real push path (a drop, minus the
        # native path capture: handle_drop is handed the path directly) ----
        api._dest = str(dest)          # the probe seam - the folder dialog is native-modal
        api.handle_drop({"dataTransfer": {"files": [
            {"name": "robots.json", "pywebviewFullPath": str(src)}]}})
        check("drop.renders", poll(window, "document.querySelectorAll('details.li-line').length === 3"))
        meta = js(window, "document.getElementById('src-meta').textContent")
        check("drop.source_line", meta == "9 robots / 3 lines", f"({meta!r})")
        check("drop.dest_shown", js(
            window, f"document.getElementById('dest-path').textContent === {json.dumps(str(dest))}"))
        head = js(window, "document.getElementById('head-count').textContent")
        check("drop.head_count", head == "7 selectable · 2 already in library", f"({head!r})")

        # ---- collapsed by default, and folded rows are invisible to ranges ----
        check("fold.collapsed", js(window, "document.querySelectorAll('details[open]').length === 0"))
        check("fold.rows_hidden", js(window, """(function(){
            return [...document.querySelectorAll('.li-row')]
                .every(function(r){ return r.offsetParent === null; });
        })()"""))

        # ---- present rows: disabled + checked + hinted, never selectable ----
        present = js(window, """(function(){
            var rows=[...document.querySelectorAll('.li-row.present')];
            return JSON.stringify({
                n: rows.length,
                all: rows.every(function(r){var c=r.querySelector('.lf-check');
                                            return c.disabled && c.checked;}),
                hints: rows.map(function(r){return r.querySelector('.li-hint').textContent;}).sort(),
            });
        })()""") or ""
        check("present.rows", '"n":2' in present and '"all":true' in present
              and "already in library (IP known)" in present and '"already in library"' in present,
              f"({present})")

        # ---- summary checkbox selects the line without unfolding ----
        r = js(window, """(function(){
            var det=document.querySelector('details[data-line="RBB01"]');
            var sel=det.querySelector('summary .lf-check');
            sel.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            return JSON.stringify({open:det.open,
                go:document.getElementById('btn-go').textContent,
                count:det.querySelector('.li-count').textContent});
        })()""") or ""
        check("line.select_all", '"open":false' in r and '"go":"import 3 robots"' in r
              and '"count":"3/3"' in r, f"({r})")

        # honest tri-state: ANY selection -> a click clears
        r = js(window, """(function(){
            var sel=document.querySelector('details[data-line="RBB01"] summary .lf-check');
            sel.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            return document.getElementById('btn-go').textContent;
        })()""")
        check("line.minus_clears", r == "import", f"({r!r})")

        # ---- shift+click range inside an open line ----
        r = js(window, """(function(){
            var det=document.querySelector('details[data-line="RBB01"]');
            det.open=true;
            var boxes=det.querySelectorAll('.li-row:not(.present) .lf-check');
            boxes[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            boxes[2].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,shiftKey:true}));
            return JSON.stringify({go:document.getElementById('btn-go').textContent,
                sum:document.getElementById('go-summary').textContent});
        })()""") or ""
        check("range.shift_click", '"go":"import 3 robots"' in r
              and '"sum":"3 robots across 1 line"' in r, f"({r})")

        # ---- a range never reaches into a COLLAPSED line ----
        # the lines render SORTED, so the folded one has to be the middle name
        # (RBB01 < RBB02 < RCB02) for the range to have to step over it
        r = js(window, """(function(){
            var first=document.querySelector('details[data-line="RBB01"]');
            var folded=document.querySelector('details[data-line="RBB02"]');
            var last=document.querySelector('details[data-line="RCB02"]');
            first.open=true; last.open=true; folded.open=false;
            document.getElementById('sel-all').dispatchEvent(new Event('change')); /* clear (had 3) */
            var fB=first.querySelectorAll('.li-row:not(.present) .lf-check');
            var lB=last.querySelectorAll('.li-row:not(.present) .lf-check');
            fB[fB.length-1].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            lB[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,shiftKey:true}));
            return JSON.stringify({go:document.getElementById('btn-go').textContent,
                sum:document.getElementById('go-summary').textContent,
                folded:folded.querySelector('.li-count').textContent});
        })()""") or ""
        check("range.skips_folded", '"go":"import 2 robots"' in r
              and '"sum":"2 robots across 2 lines"' in r
              and '"folded":"0/' in r, f"({r})")

        # ---- master select-all: clear (had selection), select all 7, partial -> minus ----
        r = js(window, """(function(){
            var m=document.getElementById('sel-all');
            m.dispatchEvent(new Event('change'));   /* any -> clear */
            var afterClear=document.getElementById('btn-go').textContent;
            m.dispatchEvent(new Event('change'));   /* none -> all */
            var afterAll=document.getElementById('btn-go').textContent;
            var ad=document.querySelector('details[data-line="RCB02"]');
            var box=ad.querySelectorAll('.li-row:not(.present) .lf-check')[0];
            box.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            return JSON.stringify({afterClear:afterClear, afterAll:afterAll,
                checked:m.checked, indet:m.indeterminate,
                adCount:ad.querySelector('.li-count').textContent});
        })()""") or ""
        check("master.honest_cycle", '"afterClear":"import"' in r
              and '"afterAll":"import 7 robots"' in r and '"indet":true' in r
              and '"adCount":"1/2"' in r, f"({r})")

        # ---- import for real: 6 selected robots land on disk ----
        js(window, "document.getElementById('btn-go').click()")
        check("seed.result_shown", poll(
            window, "!document.getElementById('result').classList.contains('hidden')", tries=40))
        big = js(window, "(document.querySelector('#result .res-big')||{}).textContent") or ""
        check("seed.created_6", big == "6 robots created", f"({big!r})")
        made = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("robot.json"))
        check("seed.on_disk", len(made) == 7, f"({len(made)} sidecars incl. the pre-seeded twin)")
        sc = json.loads((dest / "RBB02" / "RB010R01B02" / "robot.json").read_text(encoding="utf-8"))
        check("seed.sidecar_schema2", sc.get("schema") == 2 and sc.get("ips") == ["192.0.2.30"]
              and "plant" not in sc and "line" not in sc)
        check("seed.no_tmp_orphans", not list(dest.rglob("robot.json.tmp")))
        check("seed.skipped_stay_absent", not (dest / "RBB01" / "RB040R01B01" / "robot.json").exists()
              and not (dest / "RCB02" / "RC010R01B02").exists(),
              "(present rows were never in the selection)")

        # ---- import more: back to steps, everything now grayed ----
        js(window, """[...document.querySelectorAll('#result .btn')]
            .find(function(b){return b.textContent==='import more';}).click()""")
        check("again.steps_back", poll(
            window, "!document.getElementById('steps').classList.contains('hidden')"))
        head = js(window, "document.getElementById('head-count').textContent") or ""
        check("again.all_gray", head == "1 selectable · 8 already in library", f"({head!r})")
        check("again.go_disabled", js(window, "document.getElementById('btn-go').disabled === true"))

        # ---- the push leg of the drop path: handler -> evaluate_js -> page
        # toast. (A synthetic DOM DragEvent never reaches pywebview's native
        # serializer, so the OS-side path capture is the --drop-probe manual
        # check; click-to-pick is the guaranteed fallback either way.) ----
        api.handle_drop({"dataTransfer": {"files": []}})
        toast = poll(window, "(document.getElementById('toast')||{}).textContent || ''", tries=20)
        check("drop.push_roundtrip", toast == "drop a .json robot list", f"({toast!r})")

    finally:
        window.destroy()


def drop_probe():
    """Visible window; drag files onto it and watch what Python receives."""
    api = Api()

    def handle(event):
        files = ((event or {}).get("dataTransfer") or {}).get("files") or []
        got = [f.get("pywebviewFullPath") for f in files if isinstance(f, dict)]
        print("drop ->", got)
        api._push("BV.toast(%s, 6000)" % json.dumps("python got: " + (", ".join(map(str, got)) or "(no paths)")))

    api.handle_drop = handle
    window = webview.create_window(
        "drop probe - drag a file here", url=str(li_app.resource_path("web/index.html")),
        js_api=api, width=520, height=420)
    api.bind(window)
    li_app._wire_drop(window, api)
    webview.start(gui="edgechromium")


def main():
    if "--drop-probe" in sys.argv:
        drop_probe()
        return

    work = Path(tempfile.mkdtemp(prefix="li_probe_"))
    (work / "robots.json").write_text(json.dumps(LIST), encoding="utf-8")
    dest = work / "FakePlant"
    # two robots are already "theirs": one by folder name, one by a claimed IP
    (dest / "RBB01" / "RB040R01B01").mkdir(parents=True)
    other = dest / "RCB02" / "010R01"          # short-named twin claiming .21
    other.mkdir(parents=True)
    (other / "robot.json").write_text(json.dumps({"ips": ["192.0.2.21"]}), encoding="utf-8")

    api = Api()
    window = webview.create_window(
        "probe", url=str(li_app.resource_path("web/index.html")),
        js_api=api, width=900, height=760, hidden=True)
    api.bind(window)
    li_app._wire_drop(window, api)
    try:
        webview.start(probe, (window, api, work), gui="edgechromium")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("FAILURES:", FAILURES or "none")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
