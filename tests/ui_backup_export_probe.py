"""ui_backup_export_probe - the manage-backups EXPORT tab, end to end on a
synthetic library (three robots + a camera) and a real destination folder.

Covered: the third tab's chrome and its option defaults (robot.json opt-in
OFF, zip OFF, age window OFF with a disabled knob, devices "both"); the
robots/cameras filter repainting the picker and dropping filtered-out picks;
the age window excluding everything at 1 day and nothing at 100000; a custom
typed-name folder added via + (Enter commits, ✕ removes) appearing in the
engine-planned preview; the collision refusal when the date level goes;
preview folds with the app-wide right-click subtree toggle; a REAL folder
export (robot.json present only because its box was ticked, sources
untouched, no .__part); a REAL zip export beside it (leaf archives, parents
real, already-there repaint) plus the zip-level picker rolling each robot's
snapshots into ONE archive; chip drag-reorder persisting the JSON layout.
The remembered tab survives a reopen."""
import json
import sys
import time
from pathlib import Path

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_export_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

LIB = _TMP / "lib"
DEST = _TMP / "stick"


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
    # RB010R01B01: two completed + a NEWER dead pull (export must step over it)
    snap("RB010R01B01", "2026_08_09", "12_00_00", complete=False, bytes_=111)
    snap("RB010R01B01", "2026_08_01", "10_00_00")
    snap("RB010R01B01", "2026_06_01", "10_00_00", bytes_=500)
    # RB020R01B01: one completed
    snap("RB020R01B01", "2026_08_01", "11_00_00", bytes_=300)
    # RB030R01B01: partial-only -> visible in the picker, never exportable
    snap("RB030R01B01", "2026_07_01", "09_00_00", complete=False)
    # CELL-01CAM01: a camera with one completed pull - the devices filter's food
    snap("CELL-01CAM01", "2026_08_02", "08_00_00", bytes_=200)
    cam = LIB / "FakePlant" / "RBB01" / "CELL-01CAM01"
    (cam / "robot.json").write_text(json.dumps({
        "schema": 3, "id": "cam-1", "device_type": "camera-mtx",
        "model": "", "f_number": "", "ips": [],
        "ftp": {"user": "", "passive": True}, "notes": ""}), encoding="utf-8")


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


def exp_state(window):
    """The export tab as JSON: chip/option/selection/preview/bar facts in one
    read. Chips: known segments as name or -name (unticked), customs as
    ~name (they carry ✕, not a checkbox)."""
    raw = poll(window, """(function(){
        var main=document.querySelector('.mb-exp-main');
        if(!main) return '';
        var chips=[].slice.call(document.querySelectorAll('.mb-seg-strip .mb-seg'));
        var bar=document.querySelector('.mb-stagebar');
        var dinp=document.querySelector('.mb-opt-days .mb-stale-days');
        return JSON.stringify({
            chips: chips.map(function(c){
                var name=(c.querySelector('.mb-seg-name')||{textContent:''}).textContent;
                if(c.querySelector('.mb-seg-x')) return '~'+name;
                var cb=c.querySelector('.lf-check');
                return (cb&&cb.checked?'':'-')+name; }),
            sidecar: !!document.querySelector('.mb-opt-export_sidecar .lf-check:checked'),
            zip: !!document.querySelector('.mb-opt-export_zip .lf-check:checked'),
            zipAtHidden: (function(){ var b=document.querySelector('.mb-zipat');
                return b ? b.style.display==='none' : null; })(),
            zipAtLabel: (document.querySelector('.mb-zipat')||{textContent:''}).textContent,
            daysOn: !!document.querySelector('.mb-opt-days .lf-check:checked'),
            daysDisabled: dinp ? dinp.disabled : null,
            dev: (document.querySelector('.mb-exp-devseg button.active')||{textContent:''}).textContent,
            pickRows: document.querySelectorAll('.mb-exp-pick .mb-cl-row').length,
            leaves: document.querySelectorAll('.mb-exp-tree .mb-exp-data').length,
            zipLeaves: document.querySelectorAll('.mb-exp-tree .mb-exp-zipleaf').length,
            exists: document.querySelectorAll('.mb-exp-tree .mb-exp-dir.exists').length,
            closed: document.querySelectorAll('.mb-exp-tree .mb-fold:not(.open)').length,
            warn: !!document.querySelector('.mb-exp-warn'),
            skip: (document.querySelector('.mb-exp-skip')||{textContent:''}).textContent,
            sum: (bar.querySelector('.mb-sum')||{textContent:''}).textContent,
            on: !bar.querySelector('button:last-child').disabled,
            hasTree: !!document.querySelector('.mb-exp-tree .mb-exp-root'),
        });
    })()""")
    return json.loads(raw or "{}")


def poll_state(window, ok, tries=32):
    st = {}
    for _ in range(tries):
        st = exp_state(window)
        if ok(st):
            return st
        time.sleep(0.25)
    return st


def click_chip_box(window, name):
    js(window, """(function(){
        var c=[].slice.call(document.querySelectorAll('.mb-seg-strip .mb-seg'))
            .filter(function(x){return (x.querySelector('.mb-seg-name')||{textContent:''})
                .textContent===%s;})[0];
        c.querySelector('.lf-check').click();
    })()""" % json.dumps(name))


def probe(window):
    try:
        time.sleep(4)                                    # boot + cold library scan
        poll(window, "document.querySelectorAll('.lib-robot').length >= 4 ? 'y' : ''",
             tries=48)

        # ---- the third tab, its chrome, and every option's default ----
        open_manage(window)
        check("open.three_tabs", poll(window,
              "document.querySelectorAll('.mb-tabs .mb-tab').length===3 ? 'y' : ''") == "y")
        js(window, "document.querySelectorAll('.mb-tabs .mb-tab')[2].click()")
        st = poll_state(window, lambda s: bool(s.get("chips")))
        check("export.default_chips",
              st.get("chips") == ["plant", "line", "robot", "date", "-time"], f"({st})")
        check("export.options_default_off",
              st.get("sidecar") is False and st.get("zip") is False
              and st.get("daysOn") is False and st.get("daysDisabled") is True
              and st.get("zipAtHidden") is True,        # the level picker hides
              f"({st})")                                # until zip is on
        check("export.devices_default_both",
              st.get("dev") == "both" and st.get("pickRows") == 4, f"({st})")

        # ---- devices: cameras only, robots only; picks never survive a
        # filter that hides them ----
        js(window, "document.querySelectorAll('.mb-exp-devseg button')[2].click()")
        st = poll_state(window, lambda s: s.get("pickRows") == 1)
        check("devices.cameras_only", st.get("pickRows") == 1, f"({st})")
        js(window, "document.querySelectorAll('.mb-exp-devseg button')[1].click()")
        st = poll_state(window, lambda s: s.get("pickRows") == 3)
        check("devices.robots_only", st.get("pickRows") == 3, f"({st})")

        # ---- select everything selectable (robots filter holds: no camera) ----
        js(window, """(function(){
            var h=document.querySelector('.mb-exp-pick .mb-tree > .mb-fold > .hs-cat-head');
            h.querySelector('.lf-check').click();
        })()""")
        st = poll_state(window, lambda s: s.get("leaves") == 2)
        check("plan.two_leaves_partial_stepped_over_camera_filtered",
              st.get("leaves") == 2, f"({st})")
        check("plan.export_lights_up", st.get("on") is True, f"({st})")

        # ---- the age window: 1 day starves it honestly, a huge one feeds it ----
        js(window, "document.querySelector('.mb-opt-days .lf-check').click()")
        js(window, "var i=document.querySelector('.mb-opt-days .mb-stale-days');"
                   "i.value='1'; i.dispatchEvent(new Event('input'))")
        st = poll_state(window, lambda s: s.get("leaves") == 0 and "last 1 days" in s.get("skip", ""))
        check("days.one_day_excludes_all",
              st.get("leaves") == 0 and "nothing completed in the last 1 days" in st.get("skip", ""),
              f"({st.get('skip')!r})")
        js(window, "var i=document.querySelector('.mb-opt-days .mb-stale-days');"
                   "i.value='100000'; i.dispatchEvent(new Event('input'))")
        st = poll_state(window, lambda s: s.get("leaves") == 2)
        check("days.huge_window_includes_all", st.get("leaves") == 2, f"({st})")
        js(window, "document.querySelector('.mb-opt-days .lf-check').click()")
        poll_state(window, lambda s: s.get("daysOn") is False)

        # ---- a custom typed-name folder: + adds it, ✕ removes it ----
        js(window, "document.querySelector('.mb-seg-add').click()")
        check("custom.input_appears", bool(poll(window,
              "document.querySelector('.mb-seg-new') ? 'y' : ''")))
        js(window, "var i=document.querySelector('.mb-seg-new');"
                   "i.value='STICK01';"
                   "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}))")
        st = poll_state(window, lambda s: "~STICK01" in (s.get("chips") or []))
        check("custom.chip_with_x",
              st.get("chips") == ["plant", "line", "robot", "date", "-time", "~STICK01"],
              f"({st.get('chips')})")
        check("custom.persisted_as_json", bool(poll(window,
              "(String((BV.state.settings||{}).export_segs||'').indexOf('\"name\":\"STICK01\"')>=0)"
              " ? 'y' : ''")))
        ok = poll(window, """(function(){
            var t=document.querySelector('.mb-exp-tree');
            return t && t.textContent.indexOf('STICK01/')>=0 ? 'y' : '';
        })()""")
        check("custom.previewed", ok == "y")
        js(window, "document.querySelector('.mb-seg-strip .mb-seg-x').click()")
        st = poll_state(window, lambda s: "~STICK01" not in (s.get("chips") or []))
        check("custom.x_removes", "~STICK01" not in (st.get("chips") or []), f"({st.get('chips')})")

        # ---- omit date at count 2 -> honest collision, export refused ----
        js(window, "var i=document.querySelector('.mb-exptop .mb-stale-days');"
                   "i.value='2'; i.dispatchEvent(new Event('input'))")
        click_chip_box(window, "date")
        st = poll_state(window, lambda s: s.get("warn") is True)
        check("collide.warned_and_disabled",
              st.get("warn") is True and st.get("on") is False, f"({st})")
        click_chip_box(window, "date")
        st = poll_state(window, lambda s: s.get("warn") is False and s.get("leaves") == 3)
        check("collide.reticking_date_resolves",
              st.get("warn") is False and st.get("leaves") == 3 and st.get("on") is True,
              f"({st})")

        # ---- preview folds: right-click = the app-wide subtree toggle ----
        check("folds.start_open", st.get("closed") == 0, f"({st})")
        js(window, """(function(){
            var f=document.querySelector('.mb-exp-tree .mb-fold > .mb-exp-dir');
            f.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true}));
        })()""")
        st = poll_state(window, lambda s: s.get("closed") == 3)
        check("folds.rightclick_folds_children",
              st.get("closed") == 3, f"({st})")     # RBB01 + two robot folds; the
                                                    # clicked plant stays open
        js(window, """(function(){
            var f=document.querySelector('.mb-exp-tree .mb-fold > .mb-exp-dir');
            f.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true}));
        })()""")
        st = poll_state(window, lambda s: s.get("closed") == 0)
        check("folds.rightclick_again_expands", st.get("closed") == 0, f"({st})")

        # ---- folder export, robot.json ONLY because its box got ticked ----
        js(window, "document.querySelector('.mb-opt-export_sidecar .lf-check').click()")
        poll_state(window, lambda s: s.get("sidecar") is True)
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        armed = poll(window, """(function(){
            var b=document.querySelector('.mb-stagebar button:last-child');
            return b.textContent.indexOf('confirm export (3)')===0 ? 'y' : '';
        })()""")
        check("run.two_click_arm", armed == "y")
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        st = poll_state(window, lambda s: s.get("exists") == 3, tries=48)
        check("run.preview_marks_already_there", st.get("exists") == 3, f"({st})")

        out = DEST / "FakePlant" / "RBB01"
        check("disk.templated_copies",
              (out / "RB010R01B01" / "2026_08_01" / "SUMMARY.DG").is_file()
              and (out / "RB010R01B01" / "2026_06_01" / "backup.json").is_file()
              and (out / "RB020R01B01" / "2026_08_01" / "SUMMARY.DG").is_file())
        check("disk.partial_and_camera_never_copied",
              not (out / "RB010R01B01" / "2026_08_09").exists()
              and not (out / "RB030R01B01").exists()
              and not (out / "CELL-01CAM01").exists())
        check("disk.sidecar_rides_when_ticked",
              (out / "RB010R01B01" / "robot.json").is_file()
              and (out / "RB020R01B01" / "robot.json").is_file())
        check("disk.no_part_leftovers",
              not [p for p in DEST.rglob("*") if p.name.endswith(".__part")])
        src = LIB / "FakePlant" / "RBB01" / "RB010R01B01"
        check("disk.sources_untouched",
              (src / "2026_08_01" / "10_00_00" / "SUMMARY.DG").is_file()
              and (src / "2026_06_01" / "10_00_00").is_dir()
              and (src / "2026_08_09" / "12_00_00").is_dir())

        # ---- zip: leaf archives beside the folder copies, parents real ----
        js(window, "document.querySelector('.mb-opt-export_zip .lf-check').click()")
        st = poll_state(window, lambda s: s.get("zipLeaves") == 3 and s.get("exists") == 0)
        check("zip.preview_names_archives",
              st.get("zipLeaves") == 3 and st.get("exists") == 0 and st.get("on") is True,
              f"({st})")
        check("zip.level_picker_shows_data",
              st.get("zipAtHidden") is False
              and "backup data" in st.get("zipAtLabel", ""), f"({st.get('zipAtLabel')!r})")
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        poll(window, """(function(){
            var b=document.querySelector('.mb-stagebar button:last-child');
            return b.textContent.indexOf('confirm export (3)')===0 ? 'y' : '';
        })()""")
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        st = poll_state(window, lambda s: s.get("exists") == 3, tries=48)
        check("zip.already_there_after_run", st.get("exists") == 3, f"({st})")
        check("zip.disk_archives",
              (out / "RB010R01B01" / "2026_08_01.zip").is_file()
              and (out / "RB010R01B01" / "2026_06_01.zip").is_file()
              and (out / "RB020R01B01" / "2026_08_01.zip").is_file()
              and not [p for p in DEST.rglob("*") if p.name.endswith(".__part")])

        # ---- zip at a chosen level: the robot folder becomes ONE archive
        # holding its date folders ----
        js(window, "document.querySelector('.mb-zipat').click()")
        picked = poll(window, """(function(){
            var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
            var it=items.filter(function(b){return b.textContent.trim()==='robot';})[0];
            if(!it) return '';
            it.click();
            return 'y';
        })()""")
        check("zipat.menu_picks_robot", picked == "y")
        st = poll_state(window, lambda s: s.get("zipLeaves") == 2 and s.get("exists") == 0)
        check("zipat.preview_groups_members",
              st.get("zipLeaves") == 2 and st.get("leaves") == 3
              and "robot" in st.get("zipAtLabel", ""), f"({st})")
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        poll(window, """(function(){
            var b=document.querySelector('.mb-stagebar button:last-child');
            return b.textContent.indexOf('confirm export (3)')===0 ? 'y' : '';
        })()""")
        js(window, "document.querySelector('.mb-stagebar button:last-child').click()")
        # three member rows mark TWO archive lines (R1.zip holds two members);
        # the bar still counts snapshots
        st = poll_state(window, lambda s: s.get("exists") == 2, tries=48)
        check("zipat.already_there_after_run",
              st.get("exists") == 2 and "3 already there" in st.get("sum", ""),
              f"({st})")
        import zipfile
        r1zip = out / "RB010R01B01.zip"
        names = []
        if r1zip.is_file():
            with zipfile.ZipFile(r1zip) as zf:
                names = sorted(zf.namelist())
        check("zipat.disk_one_archive_per_robot",
              names == ["2026_06_01/SUMMARY.DG", "2026_06_01/backup.json",
                        "2026_08_01/SUMMARY.DG", "2026_08_01/backup.json"]
              and (out / "RB020R01B01.zip").is_file(), f"({names})")
        js(window, "document.querySelector('.mb-zipat').click()")
        poll(window, """(function(){
            var items=[].slice.call(document.querySelectorAll('.ctx-menu .ctx-item'));
            var it=items.filter(function(b){return b.textContent.indexOf('backup data')===0;})[0];
            if(!it) return '';
            it.click();
            return 'y';
        })()""")
        js(window, "document.querySelector('.mb-opt-export_zip .lf-check').click()")
        poll_state(window, lambda s: s.get("zipLeaves") == 0)

        # ---- chips drag-reorder; the JSON layout persists in order ----
        js(window, """(function(){
            var strip=document.querySelector('.mb-seg-strip');
            var robot=[].slice.call(strip.querySelectorAll('.mb-seg'))
                .filter(function(x){return (x.querySelector('.mb-seg-name')||{textContent:''})
                    .textContent==='robot';})[0];
            var x=strip.getBoundingClientRect().left+1;
            robot.dispatchEvent(new DragEvent('dragstart',{bubbles:true}));
            strip.dispatchEvent(new DragEvent('dragover',{bubbles:true,clientX:x,clientY:1}));
            strip.dispatchEvent(new DragEvent('drop',{bubbles:true,clientX:x,clientY:1}));
            robot.dispatchEvent(new DragEvent('dragend',{bubbles:true}));
        })()""")
        segs = poll(window, """(function(){
            var s=String((BV.state.settings||{}).export_segs||'');
            if(s.charAt(0)!=='[') return '';
            var ids=JSON.parse(s).map(function(x){
                return x.id==='custom' ? '~'+x.name : (x.on===false?'-':'')+x.id;
            }).join(',');
            return ids.indexOf('robot')===0 ? ids : '';
        })()""")
        check("chips.reorder_persists_json", segs == "robot,plant,line,date,-time",
              f"({segs!r})")

        # ---- the remembered tab survives a reopen ----
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        poll(window, "document.getElementById('modal-root').classList.contains('hidden')")
        open_manage(window)
        check("reopen.lands_on_export", bool(poll(window,
              "document.querySelector('.mb-modal .mb-exp-main') ? 'y' : ''")))
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")

        report()
    except Exception as e:  # noqa: BLE001 - a crashed probe must still report
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    build_tree()
    DEST.mkdir(parents=True, exist_ok=True)
    bv_settings.set_value("library_root", str(LIB))
    bv_settings.set_value("export_dir", str(DEST))
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
