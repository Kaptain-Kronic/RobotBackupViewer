"""ui_cleanup_probe - the manage-backups modal, end to end on a synthetic
library. report tab: stale/never robots in plant/line folds, selectable, the
backup button lighting up. cleanup tab: verdict groups from the Python engine
rendered as collapsible sections of plant/line folds, the pinned category
(favorites-strip style, never duplicated into plants), a pin/unpin round-trip,
tri-state select-alls that don't toggle their fold, and a REAL stage move -
folders land in <lib>/_staged, sources vanish, the list and staging status
repaint, and the remembered tab survives a reopen.

Candidate counts are time-stable by construction (candidates are strictly old,
their shields are strictly order-based); the protected fold is asserted by
PRESENCE and by exact membership (pinned + old-but-latest), never by calendar-
dependent counts. Folded-away rows are still DOM - counts query the tree,
clicks dispatch fine on display:none nodes.

Every snapshot that has to be FRESH is dated relative to today (ago(n)):
the report's 30-day stale window and cleanup's 90-day retention window are
rolling, and a hardcoded date crosses them on a calendar day - the stale
count went 1 -> 3 on 2026-08-31 and the partial-candidate count would have
gone 1 -> 2 on 2026-09-29. Strictly-old snapshots keep their 2024 dates."""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_cleanup_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

LIB = _TMP / "lib"


def ago(days):
    """A snapshot folder date `days` before today - inside or outside the
    rolling windows by construction, never by the calendar."""
    return (date.today() - timedelta(days=days)).strftime("%Y_%m_%d")


FRESH = ago(10)             # inside the 30-day stale window: a robot with this is not stale
SECOND = ago(70)            # older than FRESH, still inside the 90-day retention window
RECENT_PARTIAL = ago(30)    # a dead pull young enough to be protected(recent) in cleanup
NEWEST_BROKEN = ago(2)      # phase 2: newer than FRESH, so it is the newest snapshot


def snap(robot, date, time_, *, complete=None, bytes_=1000):
    d = LIB / "FakePlant" / "RBB01" / robot / date / time_
    d.mkdir(parents=True, exist_ok=True)
    (d / "SUMMARY.DG").write_text("Robot: " + robot + "\n", encoding="utf-8")
    meta = {"robot": robot, "line": "RBB01", "plant": "FakePlant",
            "taken": date.replace("_", "-") + "T" + time_.replace("_", ":"),
            "type": "all of above", "files": 2, "bytes": bytes_, "source": "ftp"}
    if complete is not None:
        meta["complete"] = complete
    (d / "backup.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def build_tree():
    # RB010R01B01: two newer completed + two old -> exactly 2 superseded candidates
    snap("RB010R01B01", FRESH, "10_00_00")
    snap("RB010R01B01", SECOND, "10_00_00")
    snap("RB010R01B01", "2024_05_01", "10_00_00", bytes_=500)
    snap("RB010R01B01", "2024_01_01", "10_00_00", bytes_=250)
    # RB020R01B01: fresh completed + an old dead pull -> exactly 1 partial candidate
    snap("RB020R01B01", FRESH, "11_00_00")
    snap("RB020R01B01", "2024_02_02", "09_00_00", complete=False, bytes_=300)
    # RB030R01B01: one old completed -> protected "old but latest" (warn); stale
    snap("RB030R01B01", "2024_03_03", "08_00_00")
    # RB040R01B01: partial-only -> the report's "none at all" bucket; its fresh
    # partial is protected(recent) in cleanup, so candidate counts stay 3
    snap("RB040R01B01", RECENT_PARTIAL, "09_00_00", complete=False)


def open_manage(window):
    js(window, "document.querySelector('.lib-act-functions').click()")
    ok = poll(window, """(function(){
        var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
        var it=items.filter(function(b){return b.textContent.indexOf('manage backups')===0;})[0];
        if(!it) return '';
        it.click();
        return 'y';
    })()""")
    check("open.menu_item_found", ok == "y")


def cleanup_state(window):
    """The cleanup tab as JSON: per-section row counts + open state (rows are
    counted through folds - display:none is still DOM), totals/staging/bar."""
    raw = poll(window, """(function(){
        var list=document.querySelector('.mb-cleanlist');
        if(!list) return '';
        function sec(q){ var s=list.querySelector(q); if(!s) return null;
            return { n: s.querySelectorAll('.mb-cl-row').length,
                     open: s.classList.contains('open') }; }
        var any = list.querySelector('.mb-fold') || list.querySelector('.mb-none');
        if(!any) return '';
        return JSON.stringify({
            partial: sec('.mb-sec-partial'),
            superseded: sec('.mb-sec-super'),
            prot: sec('.mb-sec-prot'),
            pincat: sec('.mb-pin-cat'),
            none: !!list.querySelector('.mb-none'),
            noQmark: list.textContent.indexOf('?') === -1,
            totals: (document.querySelector('.mb-cleantop .mb-sum')||{textContent:''}).textContent,
            stg: (document.querySelector('.mb-stg')||{textContent:''}).textContent,
            openBtn: !!document.querySelector('.mb-cleantop button'),
            bar: (document.querySelector('.mb-stagebar .mb-sum')||{textContent:''}).textContent,
        });
    })()""")
    return json.loads(raw or "{}")


def pinned_outside_category(window):
    """How many pinned rows sit in the protected fold's plant tree instead of
    the pinned category - the duplication `mb-pin-cat` exists to prevent. Only
    `reason == "pinned"` rows carry a pin button inside `.mb-sec-prot`, so
    counting the ones outside the category asserts that invariant directly.

    The protected fold's TOTAL row count cannot stand in for it: the fold also
    holds every `kept`/`latest` row, and whether a fixture's dated snapshot is
    still inside the rolling 90-day window is a fact about the calendar, not
    about the code. This check read `prot.n == 3` until 2026-08-30, when
    RB010R01B01's 2026_06_01 backup aged past the cutoff, turned from `recent`
    (which the fold omits) into `kept` (which it renders), and made it 4 -
    permanently red, with nothing wrong in the app."""
    n = js(window, """(function(){
        var sec=document.querySelector('.mb-sec-prot');
        if(!sec) return '-1';
        var cat=sec.querySelector('.mb-pin-cat');
        return String([].slice.call(sec.querySelectorAll('.mb-cl-row'))
            .filter(function(r){
                return r.querySelector('.mb-pin') && !(cat && cat.contains(r));
            }).length);
    })()""")
    return int(n) if n else -1


def poll_until(window, ok, tries=24):
    """cleanup_state() until `ok(state)` - repaints race the throttled
    hidden-window timers, so states are re-read, never sampled once."""
    st = {}
    for _ in range(tries):
        st = cleanup_state(window)
        if ok(st):
            return st
        time.sleep(0.25)
    return st


def probe(window):
    try:
        time.sleep(4)                                    # boot + cold library scan
        poll(window, "document.querySelectorAll('.lib-robot').length >= 4 ? 'y' : ''",
             tries=48)

        # ---- opens tabbed, report first ----
        open_manage(window)
        check("open.report_default", bool(poll(window,
              "document.querySelector('.mb-modal .mb-runpane') ? 'y' : ''")))
        check("open.three_tabs_no_title", bool(js(window, """(function(){
            var m=document.querySelector('.mb-modal');
            return m.querySelectorAll('.mb-tabs .mb-tab').length===3 && !m.querySelector('h2')
                ? 'y' : '';
        })()""")))

        # ---- report: one scroller of plant/line folds, selectable rows ----
        check("report.pane_is_the_scroller", js(window,
              "getComputedStyle(document.querySelector('.mb-colfill')).overflowY") == "auto")
        rep = json.loads(js(window, """(function(){
            return JSON.stringify({
                sum: (document.querySelector('.mb-colfill .mb-sum')||{textContent:''}).textContent,
                nv: document.querySelectorAll('.mb-sec-never .mb-cl-row').length,
                st: document.querySelectorAll('.mb-sec-stale .mb-cl-row').length,
                nvOpen: (document.querySelector('.mb-sec-never')||{classList:{contains:function(){}}}).classList.contains('open'),
            });
        })()""") or "{}")
        check("report.stale_and_never_grouped",
              rep.get("nv") == 1 and rep.get("st") == 1 and rep.get("nvOpen") is False,
              f"({rep})")
        check("report.sum_counts_stale", rep.get("sum", "").startswith("1 robot"),
              f"({rep.get('sum')!r})")

        # rows wear their flex spacing (the NAMEnever-taken smush is dead);
        # open the folds down to a row first - computed style needs a visible node
        js(window, "document.querySelector('.mb-sec-never > .hs-cat-head').click()")
        js(window, """(function(){
            var line=document.querySelector('.mb-sec-never .mb-tree .mb-fold .mb-fold');
            if(line && !line.classList.contains('open'))
                line.querySelector('.hs-cat-head').click();
        })()""")
        disp = poll(window, """(function(){
            var r=document.querySelector('.mb-sec-never .mb-cl-row');
            if(!r || !r.checkVisibility()) return '';
            return getComputedStyle(r).display;
        })()""")
        check("report.rows_are_flex", disp == "flex", f"({disp!r})")

        # select-alls ride the fold heads without toggling them; backup lights up
        js(window, """(function(){
            var h=document.querySelector('.mb-sec-never .mb-tree > .mb-fold > .hs-cat-head');
            h.querySelector('.lf-check').click();
        })()""")
        bar1 = poll(window, """(function(){
            var t=(document.querySelector('.mb-stagebar .mb-sum')||{textContent:''}).textContent;
            return t.indexOf('1 selected')===0 ? t : '';
        })()""")
        check("report.select_all_counts", bool(bar1), f"({bar1!r})")
        check("report.selall_leaves_fold_alone", bool(js(window,
              "document.querySelector('.mb-sec-never .mb-tree > .mb-fold').classList.contains('open')")))
        js(window, """(function(){
            var h=document.querySelector('.mb-sec-stale .mb-tree > .mb-fold > .hs-cat-head');
            h.querySelector('.lf-check').click();
        })()""")
        bk = json.loads(poll(window, """(function(){
            var t=(document.querySelector('.mb-stagebar .mb-sum')||{textContent:''}).textContent;
            if(t.indexOf('2 selected')!==0) return '';
            var b=document.querySelector('.mb-stagebar .btn');
            return JSON.stringify({ label: b.textContent, on: !b.disabled });
        })()""") or "{}")
        check("report.backup_lights_up",
              bk.get("on") is True and bk.get("label") == "backup (2)", f"({bk})")

        # ---- cleanup tab: the engine's groups, rendered not re-derived ----
        js(window, "document.querySelectorAll('.mb-tabs .mb-tab')[1].click()")
        st = cleanup_state(window)
        check("cleanup.partial_open_super_closed",
              (st.get("partial") or {}).get("n") == 1 and (st.get("partial") or {}).get("open") is True
              and (st.get("superseded") or {}).get("n") == 2
              and (st.get("superseded") or {}).get("open") is False, f"({st})")
        check("cleanup.protected_closed",
              (st.get("prot") or {}).get("open") is False and st.get("pincat") is None,
              f"({st})")
        check("cleanup.totals_line", "8 backups" in st.get("totals", ""),
              f"({st.get('totals')!r})")
        check("cleanup.no_staging_yet",
              st.get("stg") == "" and st.get("openBtn") is False, f"({st.get('stg')!r})")
        check("cleanup.unknown_sizes_stay_blank", st.get("noQmark") is True)
        check("cleanup.partials_say_their_age", bool(js(window, """(function(){
            var r=document.querySelector('.mb-sec-partial .mb-cl-row');
            return r && /\\d+d old/.test(r.textContent) ? 'y' : '';
        })()""")))

        # ---- protected: the robot FOLD carries the name, its bare row the
        # terse descriptor + hover tip ----
        warn = js(window, """(function(){
            var folds=[].slice.call(document.querySelectorAll('.mb-sec-prot .mb-fold'));
            var rf=folds.filter(function(f){
                var h=f.querySelector(':scope > .hs-cat-head');
                return h && h.textContent.indexOf('RB030R01B01')>=0; })[0];
            if(!rf) return '';
            var why=rf.querySelector('.mb-cl-row.warn .mb-cl-reason');
            return why && why.textContent==='latest' &&
                   why.title.indexOf('fresh backup')>=0 ? 'y' : '';
        })()""")
        check("cleanup.old_but_latest_warns", warn == "y")

        # ---- pin round-trip: candidate -> pinned category -> candidate ----
        js(window, "document.querySelector('.mb-sec-partial .mb-pin').click()")
        st = poll_until(window, lambda s: s.get("partial") is None)
        check("pin.partial_section_empties", st.get("partial") is None, f"({st})")
        strays = pinned_outside_category(window)
        check("pin.lands_in_pinned_category_only",
              (st.get("pincat") or {}).get("n") == 1 and strays == 0,
              f"(pincat={st.get('pincat')}, copies in the plant tree={strays}, {st})")
        pincat_row = js(window, """(function(){
            var r=document.querySelector('.mb-pin-cat .mb-cl-row');
            return r && r.textContent.indexOf('RB020R01B01')>=0 ? 'y' : '';
        })()""")
        check("pin.category_row_named", pincat_row == "y")
        js(window, "document.querySelector('.mb-pin-cat .mb-pin').click()")
        st = poll_until(window, lambda s: s.get("partial") is not None)
        check("pin.unpin_restores_candidate",
              (st.get("partial") or {}).get("n") == 1 and st.get("pincat") is None,
              f"({st})")

        # ---- tri-state alls on section heads (fold stays put) + stage ----
        js(window, """(function(){
            ['.mb-sec-partial','.mb-sec-super'].forEach(function(q){
                document.querySelector(q + ' > .hs-cat-head .lf-check').click();
            });
        })()""")
        bar = poll(window, """(function(){
            var t=(document.querySelector('.mb-stagebar .mb-sum')||{textContent:''}).textContent;
            return t.indexOf('3 selected')===0 ? t : '';
        })()""")
        check("stage.bar_counts_selection", "3 selected" in bar and "KB" in bar,
              f"({bar!r})")                     # 500+250+300 = 1050 B -> some KB text
        check("stage.selall_leaves_section_closed", bool(js(window,
              "!document.querySelector('.mb-sec-super').classList.contains('open')")))

        js(window, "document.querySelector('.mb-stagebar .btn').click()")
        armed = poll(window, """(function(){
            var b=document.querySelector('.mb-stagebar .btn');
            return b.textContent.indexOf('confirm move (3)')===0 ? 'y' : '';
        })()""")
        check("stage.two_click_arm", armed == "y")
        js(window, "document.querySelector('.mb-stagebar .btn').click()")
        st = poll_until(window, lambda s: s.get("none") is True, tries=48)
        check("stage.list_repaints_empty", st.get("none") is True, f"({st})")
        check("stage.status_counts_parked", "staged: 3" in st.get("stg", ""),
              f"({st.get('stg')!r})")
        check("stage.open_folder_button", st.get("openBtn") is True)

        staged = LIB / "_staged" / "FakePlant" / "RBB01"
        check("disk.parked_mirrored",
              (staged / "RB010R01B01" / "2024_05_01" / "10_00_00" / "SUMMARY.DG").is_file()
              and (staged / "RB010R01B01" / "2024_01_01" / "10_00_00").is_dir()
              and (staged / "RB020R01B01" / "2024_02_02" / "09_00_00").is_dir())
        r1 = LIB / "FakePlant" / "RBB01" / "RB010R01B01"
        check("disk.sources_gone",
              not (r1 / "2024_05_01").exists() and not (r1 / "2024_01_01").exists())
        check("disk.survivors_intact",
              (r1 / FRESH / "10_00_00").is_dir()
              and (LIB / "FakePlant" / "RBB01" / "RB030R01B01" / "2024_03_03").is_dir())
        logf = LIB / "_staged" / "staged.log"
        lines = [json.loads(x) for x in
                 logf.read_text(encoding="utf-8").splitlines()] if logf.is_file() else []
        check("disk.log_ledger", len(lines) == 3 and
              all(ln.get("rel", "").startswith("FakePlant/RBB01/") for ln in lines),
              f"({len(lines)} lines)")

        # ---- the remembered tab survives a reopen ----
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        poll(window, "document.getElementById('modal-root').classList.contains('hidden')")
        open_manage(window)
        check("reopen.lands_on_cleanup", bool(poll(window,
              "document.querySelector('.mb-modal .mb-cleanlist') ? 'y' : ''")))
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        poll(window, "document.getElementById('modal-root').classList.contains('hidden')")

        # ---- phase 2: auto-stage sweeps dead pulls but SKIPS the newest one
        # (it is the out-of-date evidence), and backup-broken targets it ----
        snap("RB010R01B01", NEWEST_BROKEN, "12_00_00", complete=False, bytes_=111)  # newest: broken
        snap("RB010R01B01", "2024_06_01", "12_00_00", complete=False, bytes_=222)  # auto-stage food
        js(window, "window.__rs=0; BV.api.call('lib_rescan').then(function(){window.__rs=1;})")
        poll(window, "window.__rs===1 ? 'y' : ''", tries=48)
        js(window, "BV.state.settings.auto_stage_partials=true;"
                   "BV.api.call('set_setting','auto_stage_partials',true)")
        open_manage(window)                                # remembered tab = cleanup
        st = poll_until(window, lambda s: "staged: 4" in s.get("stg", ""), tries=48)
        check("auto.stages_old_partial", "staged: 4" in st.get("stg", ""),
              f"({st.get('stg')!r})")
        check("auto.newest_partial_stays", (st.get("partial") or {}).get("n") == 1,
              f"({st.get('partial')})")
        check("auto.broken_row_wears_latest_pill", bool(js(window, """(function(){
            var r=document.querySelector('.mb-sec-partial .mb-cl-row');
            return r && r.textContent.indexOf('latest')>=0 ? 'y' : '';
        })()""")))
        bb = json.loads(js(window, """(function(){
            var b=[].slice.call(document.querySelectorAll('.mb-sec-partial > .hs-cat-head button'))
                .filter(function(x){return x.textContent.indexOf('backup broken')===0;})[0];
            return b ? JSON.stringify({label:b.textContent, on:!b.disabled}) : '';
        })()""") or "{}")
        check("auto.backup_broken_button",
              bb.get("on") is True and bb.get("label") == "backup broken (1)", f"({bb})")
        assert_dir = LIB / "_staged" / "FakePlant" / "RBB01" / "RB010R01B01"
        check("auto.disk_swept_old_not_newest",
              (assert_dir / "2024_06_01" / "12_00_00").is_dir()
              and (LIB / "FakePlant" / "RBB01" / "RB010R01B01" / NEWEST_BROKEN / "12_00_00").is_dir())
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")

        report()
    except Exception as e:  # noqa: BLE001 - a crashed probe must still report
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    build_tree()
    bv_settings.set_value("library_root", str(LIB))
    api = Api()
    window = webview.create_window(
        "probe", url=str(resource_path("web/index.html")), js_api=api,
        width=1280, height=860, hidden=True,
    )
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
